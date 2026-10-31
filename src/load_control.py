"""Keeping a busy server responsive: answer cache, per-client rate limit, search queue and load metrics.

All of it is in-process state (one API process; see README "Capacity and load"). Limits and sizes are read from
`settings` on every call, so the ones exposed in runtime_config take effect without a restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Hashable, List, Optional, Tuple, TypeVar

from config import settings

T = TypeVar("T")

METRICS_WINDOW_SECONDS = 300
STAGES = ("load", "retrieve", "rerank", "diversify", "qa")


class Busy(Exception):
    """The search queue is full or a waiting search timed out; the client should retry later."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


# --- answer cache -------------------------------------------------------------------------------------------------


def settings_fingerprint() -> str:
    """Hash of every setting that changes search results, so a settings change never serves stale answers."""
    import runtime_config  # pylint: disable=import-outside-toplevel

    values = [(name, getattr(settings, name)) for name, field_ in runtime_config.FIELDS.items() if field_.group == "search"]
    values.append(("model_precision", settings.model_precision))
    return hashlib.sha256(repr(values).encode()).hexdigest()[:16]


class SearchCache:
    """LRU of recent search results with a time limit; `invalidate` drops everything (books changed)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items: "OrderedDict[Hashable, Tuple[float, Any]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(query: str, top_k: int) -> Hashable:
        from ranking import normalize_query  # pylint: disable=import-outside-toplevel

        return (" ".join(normalize_query(query).lower().split()), top_k, settings_fingerprint(), shared_corpus_version())

    def get(self, key: Hashable) -> Optional[Any]:
        if settings.search_cache_size <= 0 or settings.search_cache_ttl_seconds <= 0:
            return None
        with self._lock:
            entry = self._items.get(key)
            if entry is None or self._clock() - entry[0] > settings.search_cache_ttl_seconds:
                if entry is not None:
                    del self._items[key]
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return entry[1]

    def put(self, key: Hashable, value: Any) -> None:
        size = settings.search_cache_size
        if size <= 0 or settings.search_cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._items[key] = (self._clock(), value)
            self._items.move_to_end(key)
            while len(self._items) > size:
                self._items.popitem(last=False)

    def invalidate(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


# --- rate limit ---------------------------------------------------------------------------------------------------


class RateLimiter:
    """Sliding one-minute window of request times per client (an IP address or an API key)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}
        self._last_sweep = clock()

    def check(self, client: str, limit_per_minute: int) -> float:
        """Record a request; returns 0 if allowed, else the seconds until the client may try again."""
        if limit_per_minute <= 0:
            return 0.0
        now = self._clock()
        with self._lock:
            if now - self._last_sweep > 60:
                self._sweep(now)
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= limit_per_minute:
                return max(0.001, hits[0] + 60 - now)
            hits.append(now)
            return 0.0

    def _sweep(self, now: float) -> None:
        """Forget clients idle for a minute, so the table does not grow without bound."""
        self._hits = {client: hits for client, hits in self._hits.items() if hits and hits[-1] > now - 60}
        self._last_sweep = now


# --- search queue -------------------------------------------------------------------------------------------------


class SearchGate:
    """At most SEARCH_CONCURRENCY searches run the models at once; up to SEARCH_QUEUE_LIMIT more wait (FIFO).

    Further searches, or ones that wait longer than SEARCH_QUEUE_TIMEOUT_SECONDS, raise Busy (HTTP 503), so an
    overloaded server answers "try again" quickly instead of letting every request time out.
    """

    def __init__(self, concurrency: Optional[int] = None):
        self.concurrency = max(1, concurrency if concurrency is not None else settings.search_concurrency)
        self.running = 0
        self.waiting = 0
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _sem(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore, self._loop = asyncio.Semaphore(self.concurrency), loop
        return self._semaphore

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        semaphore = self._sem()
        if semaphore.locked() or self.waiting:
            if self.waiting >= settings.search_queue_limit:
                raise Busy("The server is busy; please try again in a few seconds.", self._retry_after())
            self.waiting += 1
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=settings.search_queue_timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise Busy("The server is busy; please try again in a few seconds.", self._retry_after()) from exc
            finally:
                self.waiting -= 1
        else:
            await semaphore.acquire()
        self.running += 1
        try:
            return await fn()
        finally:
            self.running -= 1
            semaphore.release()

    def _retry_after(self) -> int:
        typical = metrics.typical_latency() or 2.0
        return max(1, math.ceil(typical * (self.waiting + 1) / self.concurrency))


# --- metrics ------------------------------------------------------------------------------------------------------


@dataclass
class _Sample:
    time: float
    seconds: float
    cached: bool
    stages: Dict[str, float] = field(default_factory=dict)


class Metrics:
    """Searches of the last five minutes, plus refusals, for the admin Dashboard."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: Deque[_Sample] = deque()
        self._refused: Deque[Tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        while self._samples and self._samples[0].time < now - METRICS_WINDOW_SECONDS:
            self._samples.popleft()
        while self._refused and self._refused[0][0] < now - METRICS_WINDOW_SECONDS:
            self._refused.popleft()

    def record(self, seconds: float, cached: bool, stages: Optional[Dict[str, float]] = None) -> None:
        now = self._clock()
        with self._lock:
            self._samples.append(_Sample(now, seconds, cached, dict(stages or {})))
            self._prune(now)

    def refused(self, status: int) -> None:
        now = self._clock()
        with self._lock:
            self._refused.append((now, status))
            self._prune(now)

    def typical_latency(self) -> Optional[float]:
        with self._lock:
            computed = [sample.seconds for sample in self._samples if not sample.cached]
        return _percentile(computed, 50) if computed else None

    def snapshot(self, gate: Optional[SearchGate] = None, cache: Optional[SearchCache] = None) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            self._prune(now)
            samples = list(self._samples)
            refused = list(self._refused)
        computed = [sample for sample in samples if not sample.cached]
        latencies = [sample.seconds for sample in computed]
        stages = {
            stage: sum(sample.stages.get(stage, 0.0) for sample in computed) / len(computed) if computed else None
            for stage in STAGES
        }
        window_minutes = METRICS_WINDOW_SECONDS / 60
        return {
            "window_seconds": METRICS_WINDOW_SECONDS,
            "searches": len(samples),
            "searches_per_minute": len(samples) / window_minutes,
            "cache_hits": len(samples) - len(computed),
            "cache_hit_rate": (len(samples) - len(computed)) / len(samples) if samples else None,
            "latency_p50": _percentile(latencies, 50),
            "latency_p95": _percentile(latencies, 95),
            "stage_seconds": stages,
            "refused_rate_limit": sum(1 for _, status in refused if status == 429),
            "refused_busy": sum(1 for _, status in refused if status == 503),
            "running": gate.running if gate else 0,
            "waiting": gate.waiting if gate else 0,
            "concurrency": gate.concurrency if gate else settings.search_concurrency,
            "cache_entries": len(cache) if cache is not None else 0,
            "limits": {
                "search_rate_limit_per_minute": settings.search_rate_limit_per_minute,
                "api_key_rate_limit_per_minute": settings.api_key_rate_limit_per_minute,
                "search_queue_limit": settings.search_queue_limit,
                "search_cache_size": settings.search_cache_size,
                "search_cache_ttl_seconds": settings.search_cache_ttl_seconds,
                "model_precision": settings.model_precision,
            },
        }


def _percentile(values: List[float], percent: float) -> Optional[float]:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


# Process-wide instances used by the API.
cache = SearchCache()
limiter = RateLimiter()
metrics = Metrics()
_gate: Optional[SearchGate] = None


def gate() -> SearchGate:
    global _gate  # pylint: disable=global-statement
    if _gate is None:
        _gate = SearchGate()
    return _gate


# --- several API copies -------------------------------------------------------------------------------------------
#
# With STATE_BACKEND=postgres a book change also bumps server_state.corpus_version; every copy folds the current
# version into its cache keys (re-read at most every CORPUS_VERSION_TTL seconds), so answers cached before the change
# stop being used everywhere. With REDIS_URL the answer cache and the rate limits are shared and exact.

CORPUS_VERSION_TTL = 2.0
_corpus_version = (0.0, 0)  # (read at, version)
_corpus_lock = threading.Lock()


def shared_corpus_version() -> int:
    global _corpus_version  # pylint: disable=global-statement
    if settings.state_backend != "postgres":
        return 0
    now = time.monotonic()
    with _corpus_lock:
        read_at, version = _corpus_version
        if now - read_at < CORPUS_VERSION_TTL:
            return version
    try:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT corpus_version FROM server_state WHERE id = 1")
                row = cursor.fetchone()
        version = row[0] if row else 0
    except Exception:  # pylint: disable=broad-except
        version = _corpus_version[1]  # database briefly unreachable: keep the last known version
    with _corpus_lock:
        _corpus_version = (now, version)
    return version


def corpus_changed() -> None:
    """Books were added, edited, deleted or re-ingested: drop cached answers here and on every other copy."""
    global _corpus_version  # pylint: disable=global-statement
    cache.invalidate()
    if settings.state_backend != "postgres":
        return
    try:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO server_state (id, corpus_version) VALUES (1, 1) ON CONFLICT (id) DO UPDATE "
                        "SET corpus_version = server_state.corpus_version + 1 RETURNING corpus_version"
                    )
                    version = cursor.fetchone()[0]
        with _corpus_lock:
            _corpus_version = (time.monotonic(), version)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Could not tell other copies that books changed: {exc}")


def _jsonable(value: Any) -> Any:
    """Search results may hold numpy scalars; make them plain JSON values."""
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class _Throttled:
    """Print a backend error at most once a minute."""

    def __init__(self) -> None:
        self._last = 0.0

    def warn(self, message: str) -> None:
        if time.monotonic() - self._last > 60:
            self._last = time.monotonic()
            print(f"[OSISS] {message}")


class RedisSearchCache:
    """Answer cache shared by all copies (REDIS_URL). Falls back to computing when Redis is unreachable."""

    PREFIX = "osiss:answer:"

    def __init__(self, client) -> None:
        self._client = client
        self._local = SearchCache()  # same key function and size/TTL settings
        self._warn = _Throttled()
        self.hits = 0
        self.misses = 0

    key = staticmethod(SearchCache.key)

    def _redis_key(self, key: Hashable) -> str:
        return self.PREFIX + hashlib.sha256(repr(key).encode()).hexdigest()

    def get(self, key: Hashable) -> Optional[Any]:
        if settings.search_cache_size <= 0 or settings.search_cache_ttl_seconds <= 0:
            return None
        try:
            raw = self._client.get(self._redis_key(key))
        except Exception as exc:  # pylint: disable=broad-except
            self._warn.warn(f"Redis answer cache unavailable: {exc}")
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(raw)

    def put(self, key: Hashable, value: Any) -> None:
        if settings.search_cache_size <= 0 or settings.search_cache_ttl_seconds <= 0:
            return
        try:
            self._client.set(self._redis_key(key), json.dumps(value, default=_jsonable),
                             ex=max(1, int(settings.search_cache_ttl_seconds)))
        except Exception as exc:  # pylint: disable=broad-except
            self._warn.warn(f"Redis answer cache unavailable: {exc}")

    def invalidate(self) -> None:
        """Shared entries are keyed by the corpus version, so a bump already retires them; nothing to delete."""

    def __len__(self) -> int:
        try:
            return sum(1 for _ in self._client.scan_iter(match=self.PREFIX + "*", count=500))
        except Exception:  # pylint: disable=broad-except
            return 0


_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - 60)
if redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return tostring(tonumber(oldest[2]) + 60 - now)
end
redis.call('ZADD', key, now, ARGV[3])
redis.call('EXPIRE', key, 61)
return '0'
"""


class RedisRateLimiter:
    """Exact one-minute sliding window shared by all copies (an atomic Lua script per request).

    If Redis is unreachable requests are let through (a limiter must not take search down) and a warning is printed.
    """

    PREFIX = "osiss:rate:"

    def __init__(self, client, clock: Callable[[], float] = time.time) -> None:
        self._client = client
        self._clock = clock
        self._script = client.register_script(_SLIDING_WINDOW)
        self._warn = _Throttled()

    def check(self, client: str, limit_per_minute: int) -> float:
        if limit_per_minute <= 0:
            return 0.0
        now = self._clock()
        try:
            wait = float(self._script(keys=[self.PREFIX + client], args=[now, limit_per_minute, f"{now}:{os.urandom(4).hex()}"]))
        except Exception as exc:  # pylint: disable=broad-except
            self._warn.warn(f"Redis rate limiter unavailable, not limiting: {exc}")
            return 0.0
        return max(0.001, wait) if wait > 0 else 0.0


def configure() -> List[str]:
    """Pick shared implementations for this process (API startup). Returns notes for the log."""
    global cache, limiter  # pylint: disable=global-statement
    notes = []
    if settings.redis_url:
        import redis  # pylint: disable=import-outside-toplevel

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
        cache = RedisSearchCache(client)
        limiter = RedisRateLimiter(client)
        notes.append("Answer cache and rate limits are shared through Redis.")
    elif settings.state_backend == "postgres":
        notes.append(
            "Rate limits count per API copy (no REDIS_URL): set them to the total you want divided by the number of copies."
        )
    return notes

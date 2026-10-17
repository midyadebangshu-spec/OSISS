"""This API process among several copies: identity, heartbeat with its load metrics, readiness and warm-up."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

from config import settings

READY_CACHE_SECONDS = 2.0
STALE_AFTER_HEARTBEATS = 3
FORGET_AFTER_SECONDS = 24 * 3600

_started_at = time.time()
_warm = threading.Event()
_ready_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_ready_lock = threading.Lock()


@lru_cache(maxsize=1)
def replica_id() -> str:
    return settings.replica_id or f"{socket.gethostname()}-{os.getpid()}"


# --- readiness ----------------------------------------------------------------------------------------------------


def models_loaded() -> bool:
    try:
        import search  # pylint: disable=import-outside-toplevel

        return search.get_models.cache_info().currsize > 0
    except Exception:  # pylint: disable=broad-except
        return False


@lru_cache(maxsize=1)
def _probe_client():
    from elasticsearch import Elasticsearch  # pylint: disable=import-outside-toplevel

    from clients import elasticsearch_kwargs  # pylint: disable=import-outside-toplevel

    return Elasticsearch(settings.elasticsearch_url, request_timeout=2, **elasticsearch_kwargs())


def _check_elasticsearch() -> bool:
    try:
        return bool(_probe_client().ping())
    except Exception:  # pylint: disable=broad-except
        return False


def _check_database() -> bool:
    try:
        from db_pool import db  # pylint: disable=import-outside-toplevel

        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def readiness() -> Dict[str, Any]:
    """Whether this copy should get traffic (load balancer health check): Elasticsearch reachable, the database
    reachable when state is shared, and the models loaded when WARMUP_ON_START is on. Cached for a few seconds."""
    now = time.monotonic()
    with _ready_lock:
        if _ready_cache["value"] is not None and now - _ready_cache["at"] < READY_CACHE_SECONDS:
            return _ready_cache["value"]
    checks = {"elasticsearch": _check_elasticsearch()}
    if settings.state_backend == "postgres":
        checks["database"] = _check_database()
    if settings.warmup_on_start:
        checks["models"] = _warm.is_set() and models_loaded()
    value = {"ready": all(checks.values()), "replica": replica_id(), "checks": checks}
    with _ready_lock:
        _ready_cache.update(at=now, value=value)
    return value


def warm_up() -> None:
    """Load the models (and the reranker / transliterator when enabled) before the first visitor needs them."""
    started = time.time()
    try:
        import search  # pylint: disable=import-outside-toplevel
        import translit  # pylint: disable=import-outside-toplevel

        search.get_models()
        search.get_reranker()
        translit.get_translator()
        _warm.set()
        print(f"[OSISS] Models loaded in {time.time() - started:.1f} s; this copy is ready for searches.")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Warm-up failed (the copy stays not ready): {exc}")


def start_warm_up() -> Optional[threading.Thread]:
    if not settings.warmup_on_start:
        return None
    thread = threading.Thread(target=warm_up, name="osiss-warmup", daemon=True)
    thread.start()
    return thread


# --- heartbeat ----------------------------------------------------------------------------------------------------


def _info() -> Dict[str, Any]:
    import admin_api  # pylint: disable=import-outside-toplevel
    import load_control  # pylint: disable=import-outside-toplevel

    return {
        "hostname": socket.gethostname(),
        "version": admin_api.VERSION,
        "ingest_worker": settings.ingest_worker,
        "storage_backend": settings.storage_backend,
        "ready": readiness()["ready"],
        "metrics": load_control.metrics.snapshot(load_control.gate(), load_control.cache),
    }


def beat(info: Optional[Dict[str, Any]] = None) -> None:
    """Record that this copy is alive, with its current load (STATE_BACKEND=postgres)."""
    from db_pool import db  # pylint: disable=import-outside-toplevel

    now = time.time()
    with db() as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO replicas (id, started_at, heartbeat_at, info) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET heartbeat_at = EXCLUDED.heartbeat_at, info = EXCLUDED.info",
                    (replica_id(), _started_at, now, json.dumps(info if info is not None else _info())),
                )
                cursor.execute("DELETE FROM replicas WHERE heartbeat_at < %s", (now - FORGET_AFTER_SECONDS,))


def start_heartbeat(stop: threading.Event) -> Optional[threading.Thread]:
    if settings.state_backend != "postgres":
        return None

    def loop() -> None:
        while True:
            try:
                beat()
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[OSISS] Heartbeat failed: {exc}")
            if stop.wait(settings.replica_heartbeat_seconds):
                return

    thread = threading.Thread(target=loop, name="osiss-heartbeat", daemon=True)
    thread.start()
    return thread


def list_replicas() -> List[Dict[str, Any]]:
    """All copies seen in the last day, newest heartbeat first, each marked alive or stale."""
    from db_pool import db  # pylint: disable=import-outside-toplevel

    with db() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, started_at, heartbeat_at, info FROM replicas ORDER BY heartbeat_at DESC")
            rows = cursor.fetchall()
    now = time.time()
    stale_after = STALE_AFTER_HEARTBEATS * settings.replica_heartbeat_seconds
    return [
        {
            "id": replica, "started_at": started, "heartbeat_at": heartbeat, "alive": now - heartbeat <= stale_after,
            "this": replica == replica_id(), **(info or {}),
        }
        for replica, started, heartbeat, info in rows
    ]


def aggregate(replicas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals over the live copies, in the same shape as one copy's metrics snapshot."""
    live = [r["metrics"] for r in replicas if r.get("alive") and r.get("metrics")]
    if not live:
        return {}
    searches = sum(m["searches"] for m in live)
    computed = [(m["searches"] - m["cache_hits"], m["latency_p50"]) for m in live if m["latency_p50"] is not None]
    weight = sum(count for count, _ in computed)
    stages: Dict[str, Optional[float]] = {}
    for stage in live[0]["stage_seconds"]:
        values = [(m["searches"] - m["cache_hits"], m["stage_seconds"].get(stage)) for m in live]
        values = [(count, value) for count, value in values if value is not None and count]
        total = sum(count for count, _ in values)
        stages[stage] = sum(count * value for count, value in values) / total if total else None
    p95s = [m["latency_p95"] for m in live if m["latency_p95"] is not None]
    return {
        "searches": searches,
        "searches_per_minute": sum(m["searches_per_minute"] for m in live),
        "cache_hits": sum(m["cache_hits"] for m in live),
        "cache_hit_rate": (sum(m["cache_hits"] for m in live) / searches) if searches else None,
        # Weighted by each copy's computed searches: an approximation of the overall median.
        "latency_p50": (sum(count * p50 for count, p50 in computed) / weight) if weight else None,
        "latency_p95": max(p95s) if p95s else None,
        "stage_seconds": stages,
        "refused_rate_limit": sum(m["refused_rate_limit"] for m in live),
        "refused_busy": sum(m["refused_busy"] for m in live),
        "running": sum(m["running"] for m in live),
        "waiting": sum(m["waiting"] for m in live),
        "concurrency": sum(m["concurrency"] for m in live),
        "cache_entries": max(m["cache_entries"] for m in live),
    }

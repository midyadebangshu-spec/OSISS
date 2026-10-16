"""Load protection without services: answer cache, rate limit, search queue, metrics, precision and the API wiring."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("torch")

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

import admin
import admin_api
import api_server
import load_control
import search
from config import settings

TOKEN = "t" * 40


@pytest.fixture
def override():
    saved = {}

    def apply(**values):
        for key, value in values.items():
            saved.setdefault(key, getattr(settings, key))
            object.__setattr__(settings, key, value)

    yield apply
    for key, value in saved.items():
        object.__setattr__(settings, key, value)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# --- cache --------------------------------------------------------------------------------------------------------


def test_cache_hits_expires_and_evicts(override):
    override(search_cache_size=2, search_cache_ttl_seconds=600)
    clock = Clock()
    cache = load_control.SearchCache(clock)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)  # "b" is now least recently used
    assert cache.get("b") is None and cache.get("c") == 3
    clock.now += 601
    assert cache.get("a") is None and len(cache) == 1
    assert (cache.hits, cache.misses) == (2, 2)
    cache.put("d", 4)
    cache.invalidate()
    assert cache.get("d") is None


def test_cache_off_when_size_or_ttl_zero(override):
    cache = load_control.SearchCache()
    for values in ({"search_cache_size": 0, "search_cache_ttl_seconds": 600},
                   {"search_cache_size": 10, "search_cache_ttl_seconds": 0}):
        override(**values)
        cache.put("a", 1)
        assert cache.get("a") is None


def test_cache_key_normalises_query_and_tracks_settings(override):
    override(mmr_lambda=0.85, model_precision="fp32")
    key = load_control.SearchCache.key("  What is  LIGHT? ", 3)
    assert key == load_control.SearchCache.key("what is light?", 3)
    assert key != load_control.SearchCache.key("what is light?", 5)
    override(mmr_lambda=0.7)
    assert key != load_control.SearchCache.key("what is light?", 3)
    override(mmr_lambda=0.85, search_cache_ttl_seconds=5)  # load settings do not change answers
    assert key == load_control.SearchCache.key("what is light?", 3)


# --- rate limit ---------------------------------------------------------------------------------------------------


def test_rate_limiter_window():
    clock = Clock()
    limiter = load_control.RateLimiter(clock)
    assert [limiter.check("ip:1", 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = limiter.check("ip:1", 3)
    assert 59 < wait <= 60
    assert limiter.check("ip:2", 3) == 0.0  # other clients are unaffected
    clock.now += 61
    assert limiter.check("ip:1", 3) == 0.0
    assert all(limiter.check("ip:3", 0) == 0.0 for _ in range(100))  # 0 = unlimited


def test_rate_limiter_forgets_idle_clients():
    clock = Clock()
    limiter = load_control.RateLimiter(clock)
    for client in range(50):
        limiter.check(f"ip:{client}", 5)
    clock.now += 120
    limiter.check("ip:new", 5)
    assert list(limiter._hits) == ["ip:new"]


# --- queue --------------------------------------------------------------------------------------------------------


def test_gate_limits_concurrency_queues_fifo_and_refuses_overflow(override):
    override(search_queue_limit=2, search_queue_timeout_seconds=5)

    async def scenario():
        gate = load_control.SearchGate(concurrency=1)
        release = asyncio.Event()
        order = []

        async def job(name):
            order.append(f"start {name}")
            if name == "A":
                await release.wait()
            return name

        first = asyncio.create_task(gate.run(lambda: job("A")))
        await asyncio.sleep(0)
        waiting = [asyncio.create_task(gate.run(lambda n=n: job(n))) for n in ("B", "C")]
        await asyncio.sleep(0)
        assert (gate.running, gate.waiting) == (1, 2)
        with pytest.raises(load_control.Busy) as busy:
            await gate.run(lambda: job("D"))
        assert busy.value.retry_after >= 1
        release.set()
        assert await asyncio.gather(first, *waiting) == ["A", "B", "C"]
        assert order == ["start A", "start B", "start C"]
        assert (gate.running, gate.waiting) == (0, 0)

    asyncio.run(scenario())


def test_gate_wait_times_out(override):
    override(search_queue_limit=5, search_queue_timeout_seconds=0.05)

    async def scenario():
        gate = load_control.SearchGate(concurrency=1)
        release = asyncio.Event()
        blocker = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        with pytest.raises(load_control.Busy):
            await gate.run(lambda: asyncio.sleep(0))
        release.set()
        await blocker
        assert gate.waiting == 0

    asyncio.run(scenario())


# --- metrics ------------------------------------------------------------------------------------------------------


def test_metrics_snapshot():
    clock = Clock()
    metrics = load_control.Metrics(clock)
    for seconds in (1.0, 2.0, 3.0, 4.0):
        metrics.record(seconds, cached=False, stages={"rerank": seconds / 2})
    metrics.record(0.001, cached=True)
    metrics.refused(429)
    metrics.refused(503)
    metrics.refused(503)
    snap = metrics.snapshot()
    assert snap["searches"] == 5 and snap["cache_hits"] == 1 and snap["cache_hit_rate"] == 0.2
    assert snap["latency_p50"] == 2.0 and snap["latency_p95"] == 4.0
    assert snap["stage_seconds"]["rerank"] == 1.25 and snap["stage_seconds"]["qa"] == 0.0
    assert (snap["refused_rate_limit"], snap["refused_busy"]) == (1, 2)
    clock.now += load_control.METRICS_WINDOW_SECONDS + 1
    later = metrics.snapshot()
    assert later["searches"] == 0 and later["latency_p50"] is None and later["refused_busy"] == 0


# --- precision ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precision,device,expected",
    [("fp32", "cuda", False), ("fp16", "cuda", True), ("auto", "cuda", True), ("fp16", "cpu", False), ("auto", "cpu", False)],
)
def test_half_precision_only_on_gpu(override, precision, device, expected):
    override(model_precision=precision)
    assert search.use_half_precision(device) is expected


def test_rerank_uses_configured_batch_size(override, monkeypatch):
    override(rerank_batch_size=32)
    seen = {}

    class FakeReranker:
        def predict(self, pairs, batch_size, show_progress_bar):
            seen["batch_size"] = batch_size
            return [0.5] * len(pairs)

    monkeypatch.setattr(search, "get_reranker", lambda: FakeReranker())
    search.rerank_candidates("q", [{"id": "a", "source": {"text": "t"}}])
    assert seen["batch_size"] == 32


# --- API wiring ---------------------------------------------------------------------------------------------------


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def client(override, monkeypatch):
    override(admin_token=TOKEN, admin_allowed_ips="", search_access="", cors_origins="", public_base_url="",
             search_rate_limit_per_minute=0, api_key_rate_limit_per_minute=0, search_cache_size=1000,
             search_cache_ttl_seconds=600, search_queue_limit=50)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    monkeypatch.setattr(load_control, "cache", load_control.SearchCache())
    monkeypatch.setattr(load_control, "limiter", load_control.RateLimiter())
    monkeypatch.setattr(load_control, "metrics", load_control.Metrics())
    monkeypatch.setattr(load_control, "_gate", None)
    calls = []

    def fake_search(query, top_k, timings=None):
        calls.append(query)
        if timings is not None:
            timings.update(retrieve=0.01, rerank=0.02)
        return {"query": query, "results": [{"quote": "Q", "qa_score": 0.9, "qa_answer": "A",
                                              "source": {"book_title": "B", "page_number": 1, "file_path": "./data/pdfs/b.pdf"}}]}

    monkeypatch.setattr(api_server, "search_and_extract", fake_search)
    with TestClient(api_server.app) as test_client:
        test_client.calls = calls
        yield test_client


def search_once(client, query="what is light?", headers=None):
    return client.post("/api/search", json={"query": query}, headers=headers or {})


def test_repeated_question_is_answered_from_cache(client):
    first, second = search_once(client), search_once(client, "  What is LIGHT? ")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert client.calls == ["what is light?"]
    load_control.cache.invalidate()  # e.g. a book was edited
    search_once(client)
    assert len(client.calls) == 2


def test_book_edit_clears_cache(client, monkeypatch):
    search_once(client)
    monkeypatch.setattr(admin_api, "db", lambda: _null_db())
    monkeypatch.setattr(admin, "update_book_metadata", lambda *args: {"id": 1, "title": "New"})
    monkeypatch.setattr(admin_api.context, "es_client", lambda: None)
    assert client.patch("/api/admin/books/1", json={"title": "New"}, headers={"X-Admin-Token": TOKEN}).status_code == 200
    assert len(load_control.cache) == 0


class _null_db:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def test_rate_limit_per_ip_answers_429(client, override):
    override(search_rate_limit_per_minute=2)
    assert [search_once(client, f"q{i}").status_code for i in range(3)] == [200, 200, 429]
    refused = search_once(client, "q4")
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1


def test_api_key_gets_its_own_limit_and_is_counted_in_open_mode(client, override, monkeypatch):
    override(search_rate_limit_per_minute=1, api_key_rate_limit_per_minute=3)
    counted = []
    monkeypatch.setattr(api_server, "db", lambda: _null_db())
    monkeypatch.setattr(admin, "use_api_key", lambda connection, key: counted.append(key) or (7 if key == "osk_good" else None))
    key = {"X-API-Key": "osk_good"}
    assert [search_once(client, f"k{i}", key).status_code for i in range(4)] == [200, 200, 200, 429]
    assert counted == ["osk_good"] * 4
    # An unknown key in open mode is ignored (no 401), so it falls under the per-IP limit.
    assert search_once(client, "x1", {"X-API-Key": "osk_bad"}).status_code == 200
    assert search_once(client, "x2", {"X-API-Key": "osk_bad"}).status_code == 429


def test_full_queue_answers_503_with_retry_after(client, monkeypatch):
    class FullGate:
        running, waiting, concurrency = 2, 50, 2

        async def run(self, fn):
            raise load_control.Busy("The server is busy; please try again in a few seconds.", 7)

    monkeypatch.setattr(load_control, "_gate", FullGate())
    response = search_once(client)
    assert response.status_code == 503 and response.headers["retry-after"] == "7"


def test_metrics_endpoint(client):
    search_once(client)
    search_once(client)
    body = client.get("/api/admin/metrics", headers={"X-Admin-Token": TOKEN}).json()
    assert body["searches"] == 2 and body["cache_hits"] == 1
    assert body["stage_seconds"]["rerank"] == 0.02
    assert body["limits"]["search_cache_size"] == 1000
    assert client.get("/api/admin/metrics").status_code == 401


def test_worker_reports_corpus_changes():
    from ingest_worker import IngestWorker

    changes = []
    worker = IngestWorker(connect=lambda: None, es_client=lambda: None, embedder=lambda: None,
                          on_corpus_change=lambda: changes.append(1))
    counts = iter([0, 2])

    def scan(job):
        job.params["ingested"] = next(counts)
        return "scanned"

    worker._job_scan = scan
    worker._job_reingest = lambda job: "ok"
    jobs = [worker.submit("scan", "empty scan"), worker.submit("scan", "scan"), worker.submit("reingest", "re")]
    worker.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and worker.get(jobs[-1].id).status != "done":
            time.sleep(0.02)
    finally:
        worker.shutdown()
    assert len(changes) == 2  # the scan that ingested books and the re-ingest


# --- readiness ----------------------------------------------------------------------------------------------------


def test_ready_reflects_checks(client, override, monkeypatch):
    import replica

    override(state_backend="local", warmup_on_start=False)
    replica._ready_cache.update(at=0.0, value=None)
    monkeypatch.setattr(replica, "_check_elasticsearch", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["checks"] == {"elasticsearch": False}

    replica._ready_cache.update(at=0.0, value=None)
    monkeypatch.setattr(replica, "_check_elasticsearch", lambda: True)
    override(warmup_on_start=True)  # then the models must be loaded too
    replica._warm.clear()
    assert client.get("/ready").status_code == 503
    replica._ready_cache.update(at=0.0, value=None)
    replica._warm.set()
    monkeypatch.setattr(replica, "models_loaded", lambda: True)
    body = client.get("/ready").json()
    assert body["ready"] is True and body["checks"] == {"elasticsearch": True, "models": True}
    replica._warm.clear()
    replica._ready_cache.update(at=0.0, value=None)


def test_ready_is_cached_briefly(monkeypatch, override):
    import replica

    override(state_backend="local", warmup_on_start=False)
    calls = []
    monkeypatch.setattr(replica, "_check_elasticsearch", lambda: calls.append(1) or True)
    replica._ready_cache.update(at=0.0, value=None)
    for _ in range(5):
        replica.readiness()
    assert len(calls) == 1
    replica._ready_cache.update(at=0.0, value=None)

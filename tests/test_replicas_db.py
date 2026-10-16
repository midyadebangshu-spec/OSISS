"""STATE_BACKEND=postgres: what several API copies share through PostgreSQL.

Runs against a throwaway `<POSTGRES_DB>_test` database; skipped when PostgreSQL is not reachable. A "copy" is
simulated by resetting this process's in-memory view and syncing again, which is exactly what another process does.
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("torch")

import db_pool
import runtime_config
from config import settings

TEST_DB = f"{settings.postgres_db}_test"


def _connect(dbname):
    return psycopg2.connect(
        host=settings.postgres_host, port=settings.postgres_port, dbname=dbname, user=settings.postgres_user,
        password=settings.postgres_password, connect_timeout=2,
    )


@pytest.fixture(scope="module")
def test_database():
    try:
        maintenance = _connect(settings.postgres_db)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    maintenance.autocommit = True
    with maintenance.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not cursor.fetchone():
            cursor.execute(f'CREATE DATABASE "{TEST_DB}"')
    maintenance.close()

    from db_init import POSTGRES_SCHEMA_SQL

    connection = _connect(TEST_DB)
    with connection, connection.cursor() as cursor:
        cursor.execute(POSTGRES_SCHEMA_SQL)
    connection.close()
    saved = settings.postgres_db
    object.__setattr__(settings, "postgres_db", TEST_DB)
    db_pool.get_pool.cache_clear()
    yield
    object.__setattr__(settings, "postgres_db", saved)
    db_pool.get_pool.cache_clear()


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


@pytest.fixture
def shared(test_database, override, tmp_path):
    """Postgres backend with an empty shared state."""
    connection = _connect(TEST_DB)
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "TRUNCATE server_state, ingest_jobs, ingest_log, admin_auth_failures, replicas RESTART IDENTITY"
        )
    connection.close()
    override(state_backend="postgres", settings_overrides_path=str(tmp_path / "overrides.json"),
             mmr_lambda=0.85, qa_rank_weight=0.3, search_rate_limit_per_minute=0)
    runtime_config._state.clear()
    runtime_config._applied_version = None
    yield
    runtime_config._listeners.clear()


def other_copy_saves(values):
    """Another API copy saves settings: done through the store, then our in-memory view is stale."""
    def change(data):
        saved = dict(data.get("settings") or {})
        saved.update(values)
        data["settings"] = saved

    applied = runtime_config._applied_version
    runtime_config._PostgresStore().modify(change)
    runtime_config._applied_version = applied  # this process has not seen it yet


def test_first_use_imports_the_local_settings_file(shared, tmp_path):
    (tmp_path / "overrides.json").write_text(json.dumps({"settings": {"mmr_lambda": 0.6}, "state": {"ingester_polling": True}}))
    assert runtime_config.load_overrides() == []
    assert settings.mmr_lambda == 0.6 and runtime_config.get_state("ingester_polling") is True


def test_settings_saved_on_one_copy_reach_the_others(shared):
    runtime_config.load_overrides()
    seen = []
    runtime_config.on_sync(lambda changed: seen.append(changed))
    other_copy_saves({"mmr_lambda": 0.5, "search_rate_limit_per_minute": 30})
    assert settings.mmr_lambda == 0.85
    assert runtime_config.sync_once() == {"mmr_lambda": 0.5, "search_rate_limit_per_minute": 30}
    assert settings.mmr_lambda == 0.5 and settings.search_rate_limit_per_minute == 30
    assert seen == [{"mmr_lambda": 0.5, "search_rate_limit_per_minute": 30}]
    assert runtime_config.sync_once() == {}  # nothing new


def test_saving_also_applies_changes_made_elsewhere(shared):
    runtime_config.load_overrides()
    other_copy_saves({"qa_rank_weight": 0.4})
    runtime_config.update({"mmr_lambda": 0.7})
    assert settings.mmr_lambda == 0.7 and settings.qa_rank_weight == 0.4
    assert runtime_config.sync_once() == {}


def test_concurrent_saves_from_two_copies_both_survive(shared):
    runtime_config.load_overrides()
    errors = []

    def save(values):
        try:
            for _ in range(10):
                runtime_config._PostgresStore().modify(
                    lambda data: data.setdefault("settings", {}).update(values)
                )
        except Exception as exc:  # pylint: disable=broad-except
            errors.append(exc)

    threads = [threading.Thread(target=save, args=({name: value},)) for name, value in
               (("mmr_lambda", 0.55), ("qa_rank_weight", 0.45))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    data, version = runtime_config._PostgresStore().read()
    assert data["settings"] == {"mmr_lambda": 0.55, "qa_rank_weight": 0.45}
    assert version >= 21


def test_server_state_is_shared(shared):
    runtime_config.load_overrides()
    runtime_config.set_state("ingester_polling", True)
    runtime_config._state.clear()
    runtime_config.load_overrides()
    assert runtime_config.get_state("ingester_polling") is True


def test_background_sync_thread(shared, override):
    override(settings_sync_seconds=0.05)
    runtime_config.load_overrides()
    stop = threading.Event()
    thread = runtime_config.start_sync(stop)
    try:
        other_copy_saves({"mmr_lambda": 0.65})
        deadline = time.time() + 3
        while time.time() < deadline and settings.mmr_lambda != 0.65:
            time.sleep(0.02)
        assert settings.mmr_lambda == 0.65
    finally:
        stop.set()
        thread.join(1)


def test_local_backend_starts_no_sync_thread(override):
    override(state_backend="local")
    assert runtime_config.start_sync(threading.Event()) is None
    assert runtime_config.sync_once() == {}


# --- shared job queue ---------------------------------------------------------------------------------------------


def make_worker(worker_id, executed=None, lock=None):
    from ingest_worker import IngestWorker
    from job_store import PostgresJobStore

    worker = IngestWorker(connect=lambda: None, es_client=lambda: None, embedder=lambda: None,
                          store=PostgresJobStore(), worker_id=worker_id)
    if executed is not None:
        def run(job):
            time.sleep(0.01)
            with lock:
                executed.append((job.id, worker_id))
            return f"done by {worker_id}"

        worker._job_reingest = run
    return worker


def wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_two_workers_run_each_job_exactly_once(shared):
    executed, lock = [], threading.Lock()
    front = make_worker("search-copy")  # INGEST_WORKER=false: queues and reports only
    front.start(run_worker=False)
    jobs = [front.submit("reingest", f"job {i}", book_id=i) for i in range(20)]
    workers = [make_worker(name, executed, lock) for name in ("worker-a", "worker-b")]
    for worker in workers:
        worker.start(run_worker=True)
    try:
        assert wait_until(lambda: all(front.get(job.id).status == "done" for job in jobs))
    finally:
        for worker in workers:
            worker.shutdown()
    assert sorted(job_id for job_id, _ in executed) == sorted(job.id for job in jobs)  # once each
    assert {name for _, name in executed} <= {"worker-a", "worker-b"}
    listed = front.jobs()
    assert len(listed) == 20 and listed[0]["label"] == "job 19"  # newest first, visible on any copy
    assert any(entry["message"].endswith("job 0 - done by worker-a") or entry["message"].endswith("job 0 - done by worker-b")
               for entry in front.logs_since(0, 500)["entries"])
    assert front.state()["queued"] == 0 and front.state()["worker_here"] is False


def test_queued_job_can_be_cancelled_from_another_copy(shared):
    front, other = make_worker("copy-1"), make_worker("copy-2")
    job = front.submit("reingest", "later", book_id=1)
    assert other.cancel(job.id).status == "cancelled"
    assert front.get(job.id).status == "cancelled"
    with pytest.raises(KeyError):
        other.get("missing")


def test_jobs_of_a_stopped_worker_are_marked_failed(shared):
    from job_store import Job, PostgresJobStore

    store = PostgresJobStore()
    connection = _connect(TEST_DB)
    with connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO replicas (id, started_at, heartbeat_at) VALUES ('alive', %s, %s)",
                       (time.time(), time.time()))
    connection.close()
    for job_id, worker in (("j-dead", "crashed"), ("j-alive", "alive"), ("j-mine", "worker-x")):
        store.add(Job(id=job_id, kind="reingest", label=job_id))
        connection = _connect(TEST_DB)
        with connection, connection.cursor() as cursor:
            cursor.execute("UPDATE ingest_jobs SET status = 'running', worker = %s WHERE id = %s", (worker, job_id))
        connection.close()
    worker = make_worker("worker-x")
    worker.start(run_worker=True)
    try:
        assert wait_until(lambda: store.get("j-dead").status == "failed")
        assert store.get("j-mine").status == "failed"  # left over from this worker's previous life
        assert store.get("j-alive").status == "running"  # another worker is still on it
        assert "Interrupted" in store.get("j-dead").message
    finally:
        worker.shutdown()


def test_log_is_trimmed(shared, monkeypatch):
    import job_store

    monkeypatch.setattr(job_store, "LOG_KEEP", 50)
    store = job_store.PostgresJobStore()
    for index in range(401):
        store.log(f"line {index}")
    entries = store.logs_since(0, 1000)
    assert len(entries) <= 250 and entries[-1]["message"] == "line 400"


def test_polling_switched_on_another_copy_reaches_the_worker(shared, override):
    override(settings_sync_seconds=0.05, polling_interval_seconds=3600)
    runtime_config.load_overrides()
    worker = make_worker("worker-p")
    scans = []
    worker._job_scan = lambda job: scans.append(job.id) or "scanned"
    worker.start(polling=False, run_worker=True)
    stop = threading.Event()
    runtime_config.start_sync(stop)
    try:
        time.sleep(0.3)
        assert scans == []
        applied = runtime_config._applied_version
        runtime_config._PostgresStore().modify(lambda data: data.setdefault("state", {}).update(ingester_polling=True))
        runtime_config._applied_version = applied  # saved by another copy: this process has not seen it yet
        assert wait_until(lambda: len(scans) == 1)
    finally:
        stop.set()
        worker.shutdown()

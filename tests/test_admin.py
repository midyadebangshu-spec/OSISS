"""Admin features without services: runtime settings, metadata validation, admin auth, search access and CORS.

The admin API is exercised through FastAPI's TestClient with the database calls replaced by fakes.
"""

import os
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("torch")

from fastapi.testclient import TestClient

import admin
import admin_api
import api_server
import runtime_config
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


@pytest.fixture
def overrides_file(tmp_path, override):
    path = tmp_path / "admin" / "overrides.json"
    override(settings_overrides_path=str(path))
    runtime_config._state.clear()
    return path


@pytest.fixture
def client(override, monkeypatch):
    """TestClient on the real app without its startup work (schema, worker); admin token set."""
    override(admin_token=TOKEN, admin_allowed_ips="", search_access="", cors_origins="", public_base_url="")
    admin_api.auth_failures._failures.clear()
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    with TestClient(api_server.app) as test_client:
        yield test_client


@contextmanager
def _fake_db():
    yield object()


from contextlib import asynccontextmanager


@asynccontextmanager
async def _no_lifespan(_app):
    yield


def auth():
    return {"X-Admin-Token": TOKEN}


# --- runtime settings ---------------------------------------------------------------------------------------------


def test_runtime_settings_validate_apply_and_persist(overrides_file, override):
    override(qa_rank_weight=0.3, hybrid_mode="rrf", cors_origins="")
    applied = runtime_config.update(
        {"qa_rank_weight": 0.5, "hybrid_mode": "sum", "cors_origins": ["https://Search.Example.edu/", "http://x:5173"]}
    )
    assert applied["cors_origins"] == "https://search.example.edu,http://x:5173"
    assert settings.qa_rank_weight == 0.5 and settings.hybrid_mode == "sum"

    # A fresh process applies the saved values over the environment ones.
    override(qa_rank_weight=0.3, hybrid_mode="rrf")
    assert runtime_config.load_overrides() == []
    assert settings.qa_rank_weight == 0.5 and settings.hybrid_mode == "sum"
    assert oct(os.stat(overrides_file).st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    "values",
    [
        {"qa_rank_weight": 1.5},
        {"qa_rank_weight": "0.5"},
        {"rerank_enabled": "yes"},
        {"retrieval_candidates": 2.5},
        {"hybrid_mode": "max"},
        {"admin_token": "x"},
        {"postgres_host": "evil"},
        {"cors_origins": ["not a url"]},
        {"cors_origins": ["https://a.edu/path"]},
        {"public_base_url": "ftp://a.edu"},
    ],
)
def test_runtime_settings_reject_bad_or_restart_only_values(overrides_file, override, values):
    override(qa_rank_weight=0.3)
    with pytest.raises(runtime_config.SettingsError):
        runtime_config.update({"qa_rank_weight": 0.4, **values})
    assert settings.qa_rank_weight == 0.3  # nothing applied when one value is bad
    assert not overrides_file.exists()


def test_runtime_settings_ignore_bad_saved_values(overrides_file, override):
    overrides_file.parent.mkdir(parents=True)
    overrides_file.write_text('{"settings": {"qa_rank_weight": 9, "admin_token": "x", "mmr_lambda": 0.5}}')
    override(mmr_lambda=0.85, qa_rank_weight=0.3, admin_token=TOKEN)
    warnings = runtime_config.load_overrides()
    assert len(warnings) == 2
    assert settings.mmr_lambda == 0.5 and settings.qa_rank_weight == 0.3 and settings.admin_token == TOKEN


def test_runtime_state_persists(overrides_file):
    runtime_config.set_state("ingester_polling", True)
    runtime_config._state.clear()
    runtime_config.load_overrides()
    assert runtime_config.get_state("ingester_polling") is True


# --- metadata and file names --------------------------------------------------------------------------------------


def test_clean_metadata():
    assert admin.clean_metadata({"title": "  Data   Structures ", "author": "", "publication_year": "2019"}) == {
        "title": "Data Structures",
        "author": None,
        "publication_year": 2019,
    }
    assert admin.clean_metadata({"publication_year": None}, partial=True) == {"publication_year": None}
    for bad in ({"title": ""}, {"title": "x", "publication_year": "abc"}, {"title": "x", "publication_year": 99},
                {"title": "x", "isbn": "1"}):
        with pytest.raises(admin.AdminError):
            admin.clean_metadata(bad)
    with pytest.raises(admin.AdminError):
        admin.clean_metadata({"title": " "}, partial=True)


def test_safe_pdf_name_and_unique_path(tmp_path):
    assert admin.safe_pdf_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert admin.safe_pdf_name("C:\\books\\পদার্থবিদ্যা ১.PDF") == "পদার্থবিদ্যা ১.pdf"
    assert admin.safe_pdf_name("a;rm -rf.pdf") == "a_rm -rf.pdf"
    with pytest.raises(admin.AdminError):
        admin.safe_pdf_name("notes.txt")
    (tmp_path / "book.pdf").write_bytes(b"x")
    (tmp_path / "book (2).pdf").write_bytes(b"x")
    assert admin.unique_pdf_path(str(tmp_path), "book.pdf").endswith("book (3).pdf")


def test_pdf_link_and_guessed_title(override):
    override(public_base_url="")
    assert admin.pdf_link("./data/pdfs/a b.pdf") == "/data/pdfs/a b.pdf"
    override(public_base_url="https://search.example.edu")
    assert admin.pdf_link("./data/pdfs/a.pdf") == "https://search.example.edu/data/pdfs/a.pdf"
    assert admin.looks_guessed({"file_path": "./data/pdfs/DATA-structures.pdf", "title": "DATA", "author": "structures"})
    assert admin.looks_guessed({"file_path": "./data/pdfs/physics_book.pdf", "title": "book", "author": None})
    assert not admin.looks_guessed({"file_path": "./data/pdfs/x.pdf", "title": "Physics", "author": "Verma"})


# --- admin auth ---------------------------------------------------------------------------------------------------


def test_admin_disabled_without_token(client, override):
    override(admin_token="")
    assert client.get("/api/admin/settings", headers=auth()).status_code == 503


def test_admin_token_required_and_lockout(client):
    assert client.get("/api/admin/settings").status_code == 401
    assert client.get("/api/admin/settings", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/admin/settings", headers=auth()).status_code == 200

    for _ in range(admin_api.MAX_AUTH_FAILURES):
        client.get("/api/admin/settings", headers={"X-Admin-Token": "wrong"})
    blocked = client.get("/api/admin/settings", headers=auth())
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_admin_lockout_expires(monkeypatch):
    failures = admin_api.AuthFailures()
    now = [1000.0]
    monkeypatch.setattr(admin_api.time, "time", lambda: now[0])
    for _ in range(admin_api.MAX_AUTH_FAILURES):
        failures.record("1.2.3.4")
    assert failures.blocked_for("1.2.3.4") > 0
    assert failures.blocked_for("5.6.7.8") == 0
    now[0] += admin_api.AUTH_WINDOW_SECONDS + 1
    assert failures.blocked_for("1.2.3.4") == 0


def test_admin_ip_allowlist(client, override):
    override(admin_allowed_ips="10.0.0.0/8, 192.168.1.5")
    # TestClient connects from "testclient", which is not an IP in the list.
    assert client.get("/api/admin/settings", headers=auth()).status_code == 403
    override(admin_allowed_ips="")
    assert client.get("/api/admin/settings", headers=auth()).status_code == 200


def test_admin_settings_route_applies_changes(client, overrides_file, override):
    override(mmr_lambda=0.85)
    response = client.put("/api/admin/settings", json={"mmr_lambda": 0.7}, headers=auth())
    assert response.status_code == 200 and settings.mmr_lambda == 0.7
    assert client.put("/api/admin/settings", json={"embedding_dim": 3}, headers=auth()).status_code == 400
    fields = {item["name"]: item for item in client.get("/api/admin/settings", headers=auth()).json()["editable"]}
    assert fields["mmr_lambda"]["value"] == 0.7


def test_access_mode_starts_unconfigured_then_admin_chooses(client, overrides_file):
    access = client.get("/api/admin/access", headers=auth()).json()
    assert access == {"search_access": "open", "configured": False, "cors_origins": [], "public_base_url": ""}
    response = client.put(
        "/api/admin/access",
        json={"search_access": "key", "cors_origins": ["https://search.example.edu"]},
        headers=auth(),
    )
    assert response.json()["configured"] is True and settings.search_access == "key"
    assert client.put("/api/admin/access", json={"search_access": "closed"}, headers=auth()).status_code == 400


def test_metadata_guess_route(client):
    response = client.get("/api/admin/metadata-guess", params={"filename": "Physics - H C Verma - 2019.pdf"}, headers=auth())
    assert response.json() == {"title": "Physics", "author": "H C Verma", "publication_year": 2019, "department": None}


def test_upload_rejects_non_pdf_and_cleans_staging(client, override, tmp_path, monkeypatch):
    override(upload_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(admin_api, "db", _fake_db)
    response = client.post(
        "/api/admin/books",
        headers=auth(),
        data={"title": "Notes"},
        files={"file": ("notes.pdf", b"hello", "application/pdf")},
    )
    assert response.status_code == 400
    assert os.listdir(tmp_path / "uploads") == []


def test_upload_queues_job_with_metadata(client, override, tmp_path, monkeypatch):
    override(upload_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(admin_api, "db", _fake_db)
    monkeypatch.setattr(admin, "find_book_by_hash", lambda connection, file_hash: None)
    submitted = {}

    class FakeWorker:
        def submit(self, kind, label, book_id=None, **params):
            submitted.update(kind=kind, label=label, **params)
            from ingest_worker import Job

            return Job(id="j1", kind=kind, label=label)

    monkeypatch.setattr(admin_api.context, "worker", FakeWorker())
    response = client.post(
        "/api/admin/books",
        headers=auth(),
        data={"title": "Physics", "author": "Verma", "publication_year": "2019"},
        files={"file": ("../x/Physics.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 200 and response.json()["id"] == "j1"
    assert submitted["file_name"] == "Physics.pdf"
    assert submitted["metadata"] == {"title": "Physics", "author": "Verma", "publication_year": 2019, "department": None}
    assert open(submitted["staged_path"], "rb").read() == b"%PDF-1.4 fake"


def test_upload_rejects_duplicate(client, override, tmp_path, monkeypatch):
    override(upload_dir=str(tmp_path / "uploads"))
    monkeypatch.setattr(admin_api, "db", _fake_db)
    monkeypatch.setattr(admin, "find_book_by_hash", lambda connection, file_hash: {"id": 4, "title": "Physics"})
    response = client.post(
        "/api/admin/books", headers=auth(), data={"title": "P"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 409 and "book 4" in response.json()["detail"]
    assert os.listdir(tmp_path / "uploads") == []


# --- public search access, CORS and PDF serving -------------------------------------------------------------------


@pytest.fixture
def fake_search(monkeypatch):
    monkeypatch.setattr(api_server, "search_and_extract", lambda query, top_k: {"query": query, "results": []})


def test_open_mode_needs_no_key(client, fake_search):
    assert client.post("/api/search", json={"query": "x"}).status_code == 200


def test_key_mode_requires_valid_key(client, fake_search, override, monkeypatch):
    override(search_access="key")
    monkeypatch.setattr(api_server, "db", _fake_db)
    monkeypatch.setattr(admin, "use_api_key", lambda connection, key: 7 if key == "osk_good" else None)
    assert client.post("/api/search", json={"query": "x"}).status_code == 401
    assert client.post("/api/search", json={"query": "x"}, headers={"X-API-Key": "osk_bad"}).status_code == 401
    assert client.post("/api/search", json={"query": "x"}, headers={"X-API-Key": "osk_good"}).status_code == 200


def test_key_mode_lets_own_site_and_allowed_origins_through(client, fake_search, override):
    override(search_access="key", cors_origins="https://search.example.edu")
    same_site = client.post("/api/search", json={"query": "x"}, headers={"Sec-Fetch-Site": "same-origin"})
    assert same_site.status_code == 200
    allowed = client.post("/api/search", json={"query": "x"}, headers={"Origin": "https://search.example.edu"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://search.example.edu"
    other = client.post("/api/search", json={"query": "x"}, headers={"Origin": "https://evil.example"})
    assert other.status_code == 401 and "access-control-allow-origin" not in other.headers


def test_cors_preflight(client, override):
    override(cors_origins="https://search.example.edu")
    headers = {"Origin": "https://search.example.edu", "Access-Control-Request-Method": "POST"}
    response = client.options("/api/search", headers=headers)
    assert response.status_code == 204 and "X-API-Key" in response.headers["access-control-allow-headers"]
    denied = client.options("/api/search", headers={**headers, "Origin": "https://evil.example"})
    assert denied.status_code == 403
    # Admin routes never answer cross-origin preflights.
    assert "access-control-allow-origin" not in client.options("/api/admin/status", headers=headers).headers


def test_data_route_serves_only_pdfs_in_pdf_dir(client, override, tmp_path):
    pdf_dir = os.path.join(api_server.DATA_DIR, "pdfs")
    os.makedirs(pdf_dir, exist_ok=True)
    name = f"test-serve-{os.getpid()}.pdf"
    path = os.path.join(pdf_dir, name)
    with open(path, "wb") as handle:
        handle.write(b"%PDF-1.4 test")
    try:
        override(pdf_dir="./data/pdfs")
        ok = client.get(f"/data/pdfs/{name}")
        assert ok.status_code == 200 and ok.content == b"%PDF-1.4 test"
        assert client.get("/data/admin/settings_overrides.json").status_code == 404
        assert client.get("/data/pdfs/../../src/config.py").status_code == 404
        assert client.get("/data/uploads/x.pdf").status_code == 404
    finally:
        os.remove(path)


def test_pdf_link_absolute_with_public_base_url(override):
    override(public_base_url="https://search.example.edu")
    item = {"source": {"file_path": "./data/pdfs/a.pdf", "page_number": 3}, "quote": "q"}
    assert api_server.map_result(item).pdf_link == "https://search.example.edu/data/pdfs/a.pdf"


def test_health_reports_admin_enabled(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["admin_enabled"] is True


def test_console_search_returns_scores_and_timings(client, monkeypatch):
    def fake(query, top_k, timings):
        timings["retrieve"] = 0.01
        return {
            "query": query,
            "mode": "native",
            "results": [
                {"quote": "Q", "qa_answer": "A", "qa_score": 0.9, "rerank_score": 0.8, "retrieval_score": 0.1,
                 "source": {"book_title": "B", "page_number": 2, "file_path": "./data/pdfs/b.pdf"}}
            ],
        }

    monkeypatch.setattr(admin_api.context, "search", fake)
    body = client.post("/api/admin/search", json={"query": "x"}, headers=auth()).json()
    assert body["timings"]["retrieve"] == 0.01 and body["timings"]["total"] >= 0
    assert body["results"][0]["qa_score"] == 0.9 and body["results"][0]["pdf_link"] == "/data/pdfs/b.pdf"


def test_worker_routes_without_worker_answer_503(client, monkeypatch):
    monkeypatch.setattr(admin_api.context, "worker", None)
    assert client.get("/api/admin/jobs", headers=auth()).status_code == 503


def test_worker_runs_jobs_in_order_and_cancels_queued():
    from ingest_worker import IngestWorker

    worker = IngestWorker(connect=lambda: None, es_client=lambda: None, embedder=lambda: None)
    order = []
    worker._job_scan = lambda job: order.append(job.id) or "ok"
    first = worker.submit("scan", "one")
    second = worker.submit("scan", "two")
    third = worker.submit("scan", "three")
    worker.cancel(second.id)
    worker.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and worker.get(third.id).status != "done":
            time.sleep(0.02)
    finally:
        worker.shutdown()
    assert order == [first.id, third.id]
    assert worker.get(second.id).status == "cancelled"
    assert any("Finished: three" in entry["message"] for entry in worker.logs_since(0)["entries"])
    with pytest.raises(KeyError):
        worker.cancel("missing")

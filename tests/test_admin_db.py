"""Admin book operations and ingestion jobs against a real PostgreSQL (a throwaway `<POSTGRES_DB>_test` database).

Elasticsearch, PDF extraction and the embedder are faked. Skipped when PostgreSQL is not reachable.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("torch")

import admin
import ingest
from config import settings
from utils import PageText

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
    yield
    object.__setattr__(settings, "postgres_db", saved)


@pytest.fixture
def connection(test_database):
    conn = _connect(TEST_DB)
    with conn, conn.cursor() as cursor:
        cursor.execute("TRUNCATE books, chunks, excluded_files, api_clients RESTART IDENTITY CASCADE")
    yield conn
    conn.close()


class FakeES:
    """Keeps documents by id; supports the calls the admin code and ingestion make."""

    def __init__(self):
        self.docs = {}

    def delete_by_query(self, index, query=None, body=None, **_):
        book_id = (query or body["query"])["term"]["book_id"]
        self.docs = {key: doc for key, doc in self.docs.items() if doc["book_id"] != book_id}

    def update_by_query(self, index, query, script, **_):
        for doc in self.docs.values():
            if doc["book_id"] == query["term"]["book_id"]:
                doc.update(script["params"]["fields"])


class FakeEmbedder:
    def encode(self, texts, **_):
        return np.ones((len(texts), 4), dtype=np.float32)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Fake ES + extraction + bulk indexing; PDFs live in a temp PDF_DIR."""
    es = FakeES()

    def fake_bulk(_client, actions, **_):
        for action in actions:
            es.docs[action["_id"]] = dict(action["_source"])
        return len(actions), []

    monkeypatch.setattr(ingest, "bulk", fake_bulk)
    monkeypatch.setattr(
        ingest, "extract_pdf_pages",
        lambda path: [PageText(1, "Light reflection is the bouncing back of light. " * 10), PageText(2, "Second page " * 30)],
    )
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    saved = {name: getattr(settings, name) for name in ("pdf_dir", "upload_dir")}
    object.__setattr__(settings, "pdf_dir", str(pdf_dir))
    object.__setattr__(settings, "upload_dir", str(tmp_path / "uploads"))
    yield es, pdf_dir
    for name, value in saved.items():
        object.__setattr__(settings, name, value)


def write_pdf(directory, name, content=b"%PDF-1.4 one"):
    path = directory / name
    path.write_bytes(content)
    return str(path)


def test_scan_ingests_then_metadata_edit_updates_both_stores(connection, pipeline):
    es, pdf_dir = pipeline
    path = write_pdf(pdf_dir, "DATA-structures.pdf")
    assert ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None) == 1

    book = admin.list_books(connection)[0]
    assert book["title"] == "DATA" and book["title_guessed"] and book["chunk_count"] >= 2 and book["page_count"] == 2
    assert {doc["title"] for doc in es.docs.values()} == {"DATA"}

    updated = admin.update_book_metadata(
        connection, es, book["id"], {"title": "Data Structures", "author": "Lipschutz", "publication_year": 2014}
    )
    assert updated["title"] == "Data Structures" and updated["publication_year"] == 2014 and not updated["title_guessed"]
    assert {(doc["title"], doc["author"]) for doc in es.docs.values()} == {("Data Structures", "Lipschutz")}

    # Unchanged file: nothing to do.
    assert ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None) == 0

    # Changed file: re-ingested with the edited metadata, not the file-name guess.
    with open(path, "ab") as handle:
        handle.write(b" more")
    assert ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None) == 1
    (book,) = admin.list_books(connection)
    assert (book["title"], book["author"], book["publication_year"]) == ("Data Structures", "Lipschutz", 2014)
    assert {doc["title"] for doc in es.docs.values()} == {"Data Structures"}


def test_metadata_edit_rolls_back_when_elasticsearch_fails(connection, pipeline):
    es, pdf_dir = pipeline
    write_pdf(pdf_dir, "physics.pdf")
    ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None)
    book_id = admin.list_books(connection)[0]["id"]

    def broken(**_):
        raise RuntimeError("ES down")

    es.update_by_query = lambda index, query, script, **kw: broken()
    with pytest.raises(RuntimeError):
        admin.update_book_metadata(connection, es, book_id, {"title": "New"})
    assert admin.get_book(connection, book_id)["title"] == "physics"


def test_delete_keeps_file_and_poller_skips_it(connection, pipeline):
    es, pdf_dir = pipeline
    path = write_pdf(pdf_dir, "chem.pdf")
    ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None)
    book_id = admin.list_books(connection)[0]["id"]
    admin.update_book_metadata(connection, es, book_id, {"title": "Chemistry", "department": "Science"})

    with ingest.ingest_lock(connection):
        admin.exclude_and_delete_book(connection, es, book_id)

    assert os.path.isfile(path)
    assert admin.list_books(connection) == [] and es.docs == {}
    (deleted,) = admin.list_excluded(connection)
    assert deleted["file_path"] == path and deleted["metadata"]["title"] == "Chemistry" and deleted["file_exists"]

    assert ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None) == 0
    with open(path, "ab") as handle:  # still excluded after the content changes
        handle.write(b"changed")
    assert ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None) == 0


def test_advisory_lock_blocks_a_second_session(connection, test_database):
    other = _connect(TEST_DB)
    try:
        with ingest.ingest_lock(connection):
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (ingest.INGEST_LOCK_KEY,))
                assert cursor.fetchone()[0] is False
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (ingest.INGEST_LOCK_KEY,))
            assert cursor.fetchone()[0] is True
            cursor.execute("SELECT pg_advisory_unlock(%s)", (ingest.INGEST_LOCK_KEY,))
    finally:
        other.close()


def test_api_keys(connection, test_database):
    created = admin.create_api_client(connection, "Library portal")
    assert created["key"].startswith("osk_") and created["key_prefix"] == created["key"][:10]
    assert admin.use_api_key(connection, created["key"]) == created["id"]
    assert admin.use_api_key(connection, created["key"] + "x") is None
    (listed,) = admin.list_api_clients(connection)
    assert listed["request_count"] == 1 and "key" not in listed and listed["last_used_at"]

    admin.set_api_client_enabled(connection, created["id"], False)
    assert admin.use_api_key(connection, created["key"]) is None
    admin.delete_api_client(connection, created["id"])
    with pytest.raises(admin.AdminError):
        admin.delete_api_client(connection, created["id"])


def run_worker_job(connection, es, submit):
    from ingest_worker import IngestWorker

    worker = IngestWorker(connect=lambda: _connect(TEST_DB), es_client=lambda: es, embedder=FakeEmbedder)
    worker.start()
    try:
        job = submit(worker)
        deadline = time.time() + 20
        while time.time() < deadline and worker.get(job.id).status in {"queued", "running"}:
            time.sleep(0.05)
        return worker.get(job.id)
    finally:
        worker.shutdown()


def test_upload_job_uses_admin_metadata_and_rejects_duplicates(connection, pipeline, tmp_path):
    es, pdf_dir = pipeline
    write_pdf(pdf_dir, "Physics.pdf", b"%PDF-1.4 other")  # name clash with a different file
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-1.4 upload")
    metadata = {"title": "Concepts of Physics", "author": "H C Verma", "publication_year": 2019, "department": "Physics"}
    from utils import compute_file_hash

    file_hash = compute_file_hash(str(staged))
    job = run_worker_job(connection, es, lambda worker: worker.submit(
        "upload", "Upload", staged_path=str(staged), file_name="Physics.pdf", file_hash=file_hash, metadata=metadata
    ))
    assert job.status == "done", job.message
    book = admin.get_book(connection, job.book_id)
    assert book["file_path"].endswith("Physics (2).pdf") and book["title"] == "Concepts of Physics"
    assert {doc["author"] for doc in es.docs.values()} == {"H C Verma"}
    assert not staged.exists()

    again = tmp_path / "again.pdf"
    again.write_bytes(b"%PDF-1.4 upload")
    job = run_worker_job(connection, es, lambda worker: worker.submit(
        "upload", "Upload", staged_path=str(again), file_name="Copy.pdf", file_hash=file_hash, metadata=metadata
    ))
    assert job.status == "failed" and "already ingested" in job.message and not again.exists()


def test_restore_and_reingest_jobs_keep_metadata(connection, pipeline):
    es, pdf_dir = pipeline
    write_pdf(pdf_dir, "bio.pdf")
    ingest.scan_once(connection, es, FakeEmbedder(), log=lambda _: None)
    book_id = admin.list_books(connection)[0]["id"]
    admin.update_book_metadata(connection, es, book_id, {"title": "Biology", "author": "Campbell"})

    job = run_worker_job(connection, es, lambda worker: worker.submit("reingest", "Re-ingest", book_id=book_id))
    assert job.status == "done", job.message
    assert admin.get_book(connection, job.book_id)["author"] == "Campbell"

    with ingest.ingest_lock(connection):
        admin.exclude_and_delete_book(connection, es, job.book_id)
    path = admin.list_excluded(connection)[0]["file_path"]
    job = run_worker_job(connection, es, lambda worker: worker.submit("restore", "Restore", file_path=path))
    assert job.status == "done", job.message
    restored = admin.get_book(connection, job.book_id)
    assert (restored["title"], restored["author"]) == ("Biology", "Campbell")
    assert admin.list_excluded(connection) == []


def test_lock_timeout_raises_busy(connection, test_database):
    other = _connect(TEST_DB)
    try:
        with ingest.ingest_lock(other):
            started = time.time()
            with pytest.raises(ingest.IngestBusy):
                with ingest.ingest_lock(connection, timeout=0.5):
                    pass
            assert 0.4 < time.time() - started < 3
        with ingest.ingest_lock(connection, timeout=0.5):
            pass
    finally:
        other.close()

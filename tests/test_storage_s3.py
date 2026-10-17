"""STORAGE_BACKEND=s3 against moto's in-process fake S3; the ingestion tests also need PostgreSQL (else skipped)."""

import hashlib
import os
import sys
import time
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

pytest.importorskip("torch")
moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

import storage
from config import settings
from utils import PageText

BUCKET = "osiss-test-pdfs"


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
def s3(override, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        storage._s3_client.cache_clear()
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        override(storage_backend="s3", s3_bucket=BUCKET, s3_prefix="pdfs/", s3_upload_prefix="uploads/",
                 s3_region="us-east-1", s3_endpoint_url="", pdf_serve_mode="redirect", public_base_url="")
        yield client
        storage._s3_client.cache_clear()


def put(client, key, body, sha=None):
    extra = {"Metadata": {"sha256": sha}} if sha else {}
    client.put_object(Bucket=BUCKET, Key=key, Body=body, **extra)


def test_list_skips_uploads_and_non_pdfs(s3):
    put(s3, "pdfs/b.pdf", b"%PDF-b")
    put(s3, "pdfs/a.pdf", b"%PDF-a")
    put(s3, "pdfs/notes.txt", b"x")
    put(s3, "uploads/staged.pdf", b"%PDF-s")
    files = storage.get_storage().list_pdfs()
    assert [f.path for f in files] == [f"s3://{BUCKET}/pdfs/a.pdf", f"s3://{BUCKET}/pdfs/b.pdf"]
    assert all(f.version for f in files)


def test_stage_promote_unique_names_and_hash(s3, tmp_path):
    store = storage.get_storage()
    put(s3, "pdfs/Physics.pdf", b"%PDF-existing")
    local = store.new_staging_file()
    with open(local, "wb") as handle:
        handle.write(b"%PDF-new")
    sha = hashlib.sha256(b"%PDF-new").hexdigest()
    staged = store.stage(local, sha)
    assert staged.startswith(f"s3://{BUCKET}/uploads/") and not os.path.exists(local)
    assert store.staged_exists(staged)
    final = store.promote(staged, "Physics.pdf", sha)
    assert final == f"s3://{BUCKET}/pdfs/Physics (2).pdf"
    assert not store.staged_exists(staged)
    assert store.file_hash(final) == sha  # from the object metadata, no download


def test_hash_without_metadata_downloads_once(s3):
    put(s3, "pdfs/old.pdf", b"%PDF-old")
    store = storage.get_storage()
    assert store.file_hash(f"s3://{BUCKET}/pdfs/old.pdf") == hashlib.sha256(b"%PDF-old").hexdigest()


def test_local_copy_is_removed(s3):
    put(s3, "pdfs/a.pdf", b"%PDF-a")
    with storage.get_storage().local_copy(f"s3://{BUCKET}/pdfs/a.pdf") as local:
        assert open(local, "rb").read() == b"%PDF-a"
    assert not os.path.exists(local)


def test_exists_many_uses_one_listing(s3):
    put(s3, "pdfs/a.pdf", b"%PDF-a")
    paths = [f"s3://{BUCKET}/pdfs/a.pdf", f"s3://{BUCKET}/pdfs/gone.pdf"]
    assert storage.get_storage().exists_many(paths) == {paths[0]}


def test_pdf_links(s3, override):
    import admin

    assert storage.pdf_url_path(f"s3://{BUCKET}/pdfs/sub/পদার্থ.pdf") == "/data/pdfs/sub/পদার্থ.pdf"
    assert storage.pdf_url_path("./data/pdfs/a.pdf") == "/data/pdfs/a.pdf"
    override(public_base_url="https://ossis.example.com")
    assert admin.pdf_link(f"s3://{BUCKET}/pdfs/a.pdf") == "https://ossis.example.com/data/pdfs/a.pdf"


def test_missing_bucket_setting_is_reported(override):
    override(storage_backend="s3", s3_bucket="")
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        storage.get_storage()


# --- serving ------------------------------------------------------------------------------------------------------


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def client(s3, override, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server

    override(search_access="", admin_token="t" * 40)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    with TestClient(api_server.app, follow_redirects=False) as test_client:
        yield test_client


def test_pdf_route_redirects_to_signed_url(client, s3):
    put(s3, "pdfs/a b.pdf", b"%PDF-a")
    response = client.get("/data/pdfs/a b.pdf")
    assert response.status_code == 302
    location = response.headers["location"]
    assert BUCKET in location and "Signature" in location and "response-content-type=application%2Fpdf" in location
    assert client.get("/data/pdfs/missing.pdf").status_code == 404
    assert client.get("/data/pdfs/../uploads/x.pdf").status_code == 404
    assert client.get("/data/admin/settings.json").status_code == 404


def test_pdf_route_can_proxy(client, s3, override):
    override(pdf_serve_mode="proxy")
    put(s3, "pdfs/a.pdf", b"%PDF-proxied")
    response = client.get("/data/pdfs/a.pdf")
    assert response.status_code == 200 and response.content == b"%PDF-proxied"
    assert response.headers["content-type"] == "application/pdf"


# --- ingestion from S3 (needs PostgreSQL) -------------------------------------------------------------------------

psycopg2 = pytest.importorskip("psycopg2")
TEST_DB = f"{settings.postgres_db}_test"


@pytest.fixture
def pg(override):
    try:
        connection = psycopg2.connect(host=settings.postgres_host, port=settings.postgres_port, dbname=TEST_DB,
                                      user=settings.postgres_user, password=settings.postgres_password, connect_timeout=2)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"PostgreSQL test database not reachable: {exc}")
    from db_init import POSTGRES_SCHEMA_SQL

    with connection, connection.cursor() as cursor:
        cursor.execute(POSTGRES_SCHEMA_SQL)
        cursor.execute("TRUNCATE books, chunks, excluded_files, ingest_jobs, ingest_log RESTART IDENTITY CASCADE")
    override(postgres_db=TEST_DB, state_backend="local")
    yield connection
    connection.close()


class FakeES:
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
def pipeline(monkeypatch, s3, pg):
    import ingest

    es = FakeES()

    def fake_bulk(_client, actions, **_):
        for action in actions:
            es.docs[action["_id"]] = dict(action["_source"])
        return len(actions), []

    read = []

    def fake_extract(path):
        read.append(open(path, "rb").read())  # a real local file (downloaded from S3)
        return [PageText(1, "Light reflection is the bouncing back of light. " * 10)]

    monkeypatch.setattr(ingest, "bulk", fake_bulk)
    monkeypatch.setattr(ingest, "extract_pdf_pages", fake_extract)
    return es, read


def test_scan_ingests_from_s3_and_skips_unchanged_objects(pipeline, pg, s3):
    import admin
    import ingest

    es, read = pipeline
    put(s3, "pdfs/DATA-structures.pdf", b"%PDF-1")
    assert ingest.scan_once(pg, es, FakeEmbedder(), log=lambda _: None) == 1
    (book,) = admin.list_books(pg)
    assert book["file_path"] == f"s3://{BUCKET}/pdfs/DATA-structures.pdf" and book["file_exists"]
    assert book["pdf_link"] == "/data/pdfs/DATA-structures.pdf"
    assert {doc["file_path"] for doc in es.docs.values()} == {book["file_path"]}
    admin.update_book_metadata(pg, es, book["id"], {"title": "Data Structures", "author": "Lipschutz"})

    downloads = len(read)
    assert ingest.scan_once(pg, es, FakeEmbedder(), log=lambda _: None) == 0
    assert len(read) == downloads  # same ETag: nothing downloaded

    put(s3, "pdfs/DATA-structures.pdf", b"%PDF-2 changed")
    assert ingest.scan_once(pg, es, FakeEmbedder(), log=lambda _: None) == 1
    (book,) = admin.list_books(pg)
    assert (book["title"], book["author"]) == ("Data Structures", "Lipschutz")  # metadata kept


def test_upload_job_promotes_into_s3_and_ingests(pipeline, pg, s3):
    import admin
    from ingest_worker import IngestWorker

    es, read = pipeline
    store = storage.get_storage()
    local = store.new_staging_file()
    with open(local, "wb") as handle:
        handle.write(b"%PDF-upload")
    sha = hashlib.sha256(b"%PDF-upload").hexdigest()
    staged = store.stage(local, sha)
    worker = IngestWorker(
        connect=lambda: psycopg2.connect(host=settings.postgres_host, port=settings.postgres_port, dbname=TEST_DB,
                                         user=settings.postgres_user, password=settings.postgres_password),
        es_client=lambda: es, embedder=FakeEmbedder,
    )
    worker.start()
    try:
        job = worker.submit("upload", "Upload", staged_path=staged, file_name="Physics.pdf", file_hash=sha,
                            metadata={"title": "Concepts of Physics", "author": "H C Verma", "publication_year": 2019,
                                      "department": "Physics"})
        deadline = time.time() + 20
        while time.time() < deadline and worker.get(job.id).status in {"queued", "running"}:
            time.sleep(0.05)
        done = worker.get(job.id)
    finally:
        worker.shutdown()
    assert done.status == "done", done.message
    book = admin.get_book(pg, done.book_id)
    assert book["file_path"] == f"s3://{BUCKET}/pdfs/Physics.pdf" and book["title"] == "Concepts of Physics"
    assert read == [b"%PDF-upload"]
    assert not store.staged_exists(staged)
    with pg.cursor() as cursor:
        cursor.execute("SELECT source_version FROM books WHERE id = %s", (done.book_id,))
        assert cursor.fetchone()[0]  # ETag remembered: the next scan will not download it again
    pg.rollback()


def test_sync_models_round_trip(s3, tmp_path, override):
    import sync_models

    source = tmp_path / "models"
    (source / "BAAI_bge-m3").mkdir(parents=True)
    (source / "BAAI_bge-m3" / "model.safetensors").write_bytes(b"weights")
    (source / "english_vocab.txt").write_text("light\n")
    uri = f"s3://{BUCKET}/models/"
    assert sync_models.upload(str(source), uri) == 2
    assert sync_models.upload(str(source), uri) == 0  # unchanged files are skipped
    target = tmp_path / "fresh"
    assert sync_models.download(uri, str(target)) == 2
    assert (target / "BAAI_bge-m3" / "model.safetensors").read_bytes() == b"weights"
    assert sync_models.download(uri, str(target)) == 0
    with pytest.raises(ValueError):
        sync_models.split_uri("https://not-s3")

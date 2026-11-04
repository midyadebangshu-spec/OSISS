"""Library (library.py, /api/library) against a real PostgreSQL (throwaway `<POSTGRES_DB>_test`) and a synthetic PDF.

Skipped when PostgreSQL is not reachable.
"""

import os
import sys
from contextlib import asynccontextmanager, contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("fastapi")
pytest.importorskip("torch")

import fitz
from fastapi.testclient import TestClient

import library
import library_api
import page_assets
from config import settings
from test_page_assets import make_pdf

TEST_DB = f"{settings.postgres_db}_test"
FILE_HASH = "c" * 64


def _connect():
    return psycopg2.connect(
        host=settings.postgres_host, port=settings.postgres_port, dbname=TEST_DB, user=settings.postgres_user,
        password=settings.postgres_password, connect_timeout=2,
    )


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    try:
        maintenance = psycopg2.connect(
            host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
            user=settings.postgres_user, password=settings.postgres_password, connect_timeout=2,
        )
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    maintenance.autocommit = True
    with maintenance.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not cursor.fetchone():
            cursor.execute(f'CREATE DATABASE "{TEST_DB}"')
    maintenance.close()

    root = tmp_path_factory.mktemp("library")
    saved = settings.asset_dir
    object.__setattr__(settings, "asset_dir", str(root / "assets"))
    pdf_path = str(root / "optics.pdf")
    make_pdf(pdf_path)
    with fitz.open(pdf_path) as pdf:  # give it an outline
        pdf.set_toc([[1, "Chapter 1 Light", 1], [2, "1.1 Reflection", 1], [1, "Chapter 2 Scans", 2]])
        pdf.saveIncr()
    page_assets.build_pages(pdf_path, FILE_HASH, pages=[1])  # page 2 is built on demand by the pages endpoint

    from db_init import POSTGRES_SCHEMA_SQL

    connection = _connect()
    with connection, connection.cursor() as cursor:
        cursor.execute(POSTGRES_SCHEMA_SQL)
        cursor.execute("TRUNCATE concept_links, concept_mentions, concept_figures, concepts RESTART IDENTITY")
        cursor.execute("TRUNCATE books RESTART IDENTITY CASCADE")
        cursor.execute(
            "INSERT INTO books (title, author, department, language_code, file_path, file_hash) VALUES "
            "('Optics', 'A. Author', 'Physics', 'en', %s, %s), ('Zoology', NULL, 'Biology', 'bn', './missing.pdf', NULL)",
            (pdf_path, FILE_HASH),
        )
        cursor.execute("INSERT INTO chunks (book_id, chunk_index, page_number, chunk_text) VALUES (1, 0, 1, 'Light travels.')")
        cursor.execute("INSERT INTO concepts (key, label, book_count) VALUES ('reflection', 'reflection', 2)")
        cursor.execute(
            "INSERT INTO concept_mentions (concept_id, book_id, page_number, is_definition, sentence) VALUES "
            "(1, 1, 1, TRUE, 'Reflection is the bouncing of light.'), (1, 1, 1, FALSE, 'Reflection again.')"
        )
    library.clear_cache()
    yield connection
    connection.close()
    library.clear_cache()
    object.__setattr__(settings, "asset_dir", saved)


def test_list_books(stored):
    books = {book["title"]: book for book in library.list_books(stored)}
    optics = books["Optics"]
    assert optics["page_count"] == 2 and optics["chunk_count"] == 1 and optics["concept_count"] == 1
    assert optics["cover"] == f"/data/assets/{FILE_HASH}/p1.webp"
    assert optics["figure_count"] == 2 and optics["table_count"] == 1 and optics["has_contents"]
    assert optics["language"] == "en" and optics["department"] == "Physics"
    zoology = books["Zoology"]  # unreadable PDF, no assets: listed anyway
    assert zoology["cover"] is None and zoology["page_count"] is None


def test_book_detail(stored):
    book = library.get_book(stored, 1)
    assert [entry["title"] for entry in book["toc"]] == ["Chapter 1 Light", "1.1 Reflection", "Chapter 2 Scans"]
    assert book["toc"][1] == {"level": 2, "title": "1.1 Reflection", "page": 1}
    assert book["concepts"] == [{"id": 1, "label": "reflection", "book_count": 2, "mentions": 2, "definitions": 1}]
    assert {figure["label"] for figure in book["figures"]} == {"2.1", "2.2"}
    assert library.get_book(stored, 999) is None


def test_book_pages_builds_missing_images(stored):
    pages = library.book_pages(stored, 1, 1, 24)
    assert pages["page_count"] == 2
    assert [page["page"] for page in pages["pages"]] == [1, 2]
    assert all(page["image"] for page in pages["pages"])  # page 2 was built on demand


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def client(stored, monkeypatch):
    import api_server

    @contextmanager
    def test_db():
        connection = _connect()
        try:
            yield connection
        finally:
            connection.close()

    saved = settings.search_access
    object.__setattr__(settings, "search_access", "")
    monkeypatch.setattr(library_api, "db", test_db)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    with TestClient(api_server.app) as test_client:
        yield test_client
    object.__setattr__(settings, "search_access", saved)


def test_library_routes(client):
    books = client.get("/api/library").json()["books"]
    assert [book["title"] for book in books] == ["Optics", "Zoology"]
    detail = client.get("/api/library/1").json()
    assert detail["toc"][0]["title"] == "Chapter 1 Light" and detail["concepts"][0]["label"] == "reflection"
    pages = client.get("/api/library/1/pages", params={"start": 2, "count": 5}).json()
    assert [page["page"] for page in pages["pages"]] == [2]
    assert client.get("/api/library/999").status_code == 404
    assert client.get("/api/library/1/pages", params={"count": 500}).status_code == 422

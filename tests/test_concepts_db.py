"""Concept graph storage and the /api/concepts routes against a real PostgreSQL (throwaway `<POSTGRES_DB>_test`).

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

from fastapi.testclient import TestClient

import concept_api
import concept_store
import concepts
from config import settings
from test_concepts import _chunks

TEST_DB = f"{settings.postgres_db}_test"


def _connect():
    return psycopg2.connect(
        host=settings.postgres_host, port=settings.postgres_port, dbname=TEST_DB, user=settings.postgres_user,
        password=settings.postgres_password, connect_timeout=2,
    )


@pytest.fixture(scope="module")
def stored():
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

    from db_init import POSTGRES_SCHEMA_SQL

    connection = _connect()
    with connection, connection.cursor() as cursor:
        cursor.execute(POSTGRES_SCHEMA_SQL)
        cursor.execute("TRUNCATE books RESTART IDENTITY CASCADE")
        for book_id, title in ((1, "Data Structures"), (2, "Algorithms"), (3, "Biology")):
            cursor.execute(
                "INSERT INTO books (id, title, author, file_path, file_hash) VALUES (%s, %s, 'A', %s, %s)",
                (book_id, title, f"./data/pdfs/{title}.pdf", str(book_id) * 64),
            )
    graph = concepts.build_graph(_chunks(), bold_lines=[(1, 1, "Data Structure"), (2, 4, "data structures")], log=lambda _: None)
    figure = {"image": "p4-f1.webp", "caption": "Figure 3.1 A stack of plates", "label": "3.1", "width": 300, "height": 200}
    figures = [("stack", 2, 4, figure)]
    counts = concept_store.write_graph(connection, graph, figures)
    yield connection, graph, counts, figures
    connection.close()


def ids(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT key, id FROM concepts")
        return dict(cursor.fetchall())


def test_write_and_read_back(stored):
    connection, graph, counts, _ = stored
    assert counts["concepts"] == len(graph.concepts)
    concept_ids = ids(connection)
    stack = concept_store.get_concept(connection, concept_ids["stack"])
    assert stack["label"] == "stack" and stack["book_count"] == 2
    assert {b["title"] for b in stack["books"]} == {"Data Structures", "Algorithms"}
    assert any("linear data structure" in d["sentence"] for d in stack["definitions"])
    neighbours = {n["label"]: n for n in stack["neighbours"]}
    assert neighbours["queue"]["relation"] == "contrast"
    assert neighbours["data structure"]["relation"] == "is_a" and neighbours["data structure"]["direction"] == "out"
    assert stack["figures"][0]["caption"].startswith("Figure 3.1")

    link = concept_store.get_link(connection, concept_ids["queue"], concept_ids["stack"])
    assert link["relation"] == "contrast" and link["evidence"][0]["title"] in {"Data Structures", "Algorithms"}
    assert concept_store.get_link(connection, concept_ids["stack"], concept_ids["photosynthesis"]) is None


def test_rebuild_replaces_the_graph(stored):
    connection, graph, _, figures = stored
    small = concepts.Graph({"stack": graph.concepts["stack"]}, [m for m in graph.mentions if m.key == "stack"], [])
    concept_store.write_graph(connection, small)
    assert list(ids(connection)) == ["stack"]
    concept_store.write_graph(connection, graph, figures)  # restore for the API tests
    assert len(ids(connection)) == len(graph.concepts)


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
    monkeypatch.setattr(concept_api, "db", test_db)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    concept_store.invalidate_index()
    with TestClient(api_server.app) as test_client:
        yield test_client
    concept_store.invalidate_index()
    object.__setattr__(settings, "search_access", saved)


def test_api_search_detail_and_link(client, stored):
    connection = stored[0]
    concept_ids = ids(connection)
    browse = client.get("/api/concepts").json()
    assert browse["built"] and browse["concepts"][0]["book_count"] >= 2
    found = client.get("/api/concepts", params={"q": "What is a stack?"}).json()["concepts"]
    assert found[0]["label"] == "stack"

    detail = client.get(f"/api/concepts/{concept_ids['stack']}").json()
    assert detail["books"][0]["pdf_link"].startswith("/data/pdfs/")
    assert detail["figures"][0]["image"] == f"/data/assets/{'2' * 64}/p4-f1.webp"

    link = client.get(f"/api/concepts/{concept_ids['stack']}/links/{concept_ids['queue']}").json()
    assert link["relation"] == "contrast" and link["evidence"][0]["pdf_link"].startswith("/data/pdfs/")
    assert client.get("/api/concepts/999999").status_code == 404
    assert client.get(f"/api/concepts/{concept_ids['stack']}/links/{concept_ids['photosynthesis']}").status_code == 404


def test_concepts_named_in_a_search_hit(client):
    named = concept_api.concepts_in_text(["Push adds an element to a stack, unlike a queue. A queue is fair."])
    assert {item["label"] for item in named} == {"stack", "queue"}
    assert named[0]["label"] == "queue"  # named most often
    asked = concept_api.concepts_in_text(["Push adds an element to a stack, unlike a queue. A queue is fair."], query="what is a stack")
    assert asked[0]["label"] == "stack"  # named in the question

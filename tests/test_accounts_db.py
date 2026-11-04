"""Scholar accounts and the workspace (accounts.py, workspace.py and their routes) against a real PostgreSQL
(throwaway `<POSTGRES_DB>_test`). Skipped when PostgreSQL is not reachable. The pure helpers at the top need nothing."""

import os
import sys
from contextlib import asynccontextmanager, contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import accounts
import workspace
from config import settings


# --- pure helpers -------------------------------------------------------------------------------------------------


def test_password_hash_roundtrip():
    stored = accounts.hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert accounts.verify_password("correct horse", stored)
    assert not accounts.verify_password("correct horsf", stored)
    assert accounts.hash_password("correct horse") != stored  # random salt
    assert not accounts.verify_password("x", "garbage")


def test_input_rules():
    assert accounts.normalize_email("  Ada@Example.EDU ") == "ada@example.edu"
    for bad in ("", "no-at", "a@b", "a b@c.de"):
        with pytest.raises(accounts.AccountError):
            accounts.normalize_email(bad)
    with pytest.raises(accounts.AccountError):
        accounts.check_password_rules("short")
    assert accounts.clean_name("  Ada   Lovelace ") == "Ada Lovelace"


def test_concept_links_and_quote_hash():
    assert workspace.concept_links("See [[Stack]] and [[queue]], again [[stack]]; [[ ]] and [[a\nb]] are not links") == [
        "Stack",
        "queue",
    ]
    assert workspace.quote_hash("a  b\n c") == workspace.quote_hash("a b c")
    assert workspace.quote_hash("a b c") != workspace.quote_hash("a b d")


# --- database -----------------------------------------------------------------------------------------------------

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("fastapi")
pytest.importorskip("torch")  # api_server imports the search stack

from fastapi.testclient import TestClient

TEST_DB = f"{settings.postgres_db}_test"
PASSWORD = "a-good-password"


def _connect():
    return psycopg2.connect(
        host=settings.postgres_host, port=settings.postgres_port, dbname=TEST_DB, user=settings.postgres_user,
        password=settings.postgres_password, connect_timeout=2,
    )


@pytest.fixture(scope="module")
def database():
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
    yield connection
    connection.close()


@pytest.fixture
def clean(database):
    with database, database.cursor() as cursor:
        cursor.execute("TRUNCATE users, user_login_failures RESTART IDENTITY CASCADE")
        cursor.execute("TRUNCATE books RESTART IDENTITY CASCADE")
        cursor.execute(
            "INSERT INTO books (title, author, file_path) VALUES ('Data Structures', 'A. Author', './ds.pdf'), "
            "('Algorithms', 'B. Author', './algo.pdf')"
        )
    return database


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def app(clean, monkeypatch):
    import accounts_api
    import admin_api
    import api_server
    import workspace_api

    @contextmanager
    def test_db():
        connection = _connect()
        try:
            yield connection
        finally:
            connection.close()

    for module in (accounts_api, workspace_api, admin_api):
        monkeypatch.setattr(module, "db", test_db)
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    saved = (settings.admin_token, settings.registration_mode)
    object.__setattr__(settings, "admin_token", "t" * 40)
    object.__setattr__(settings, "registration_mode", "open")
    yield api_server.app
    object.__setattr__(settings, "admin_token", saved[0])
    object.__setattr__(settings, "registration_mode", saved[1])


def scholar(app, email="ada@example.edu", register=True):
    client = TestClient(app)
    if register:
        response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": "Ada"})
        assert response.status_code == 200, response.text
    return client


ADMIN = {"X-Admin-Token": "t" * 40}
PASSAGE = {"kind": "passage", "book_id": 1, "page_number": 12, "quote": "A stack is a LIFO structure."}


def test_register_login_logout(app):
    client = scholar(app)
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "ada@example.edu" and me["name"] == "Ada" and "password_hash" not in me
    login = client.post("/api/auth/login", json={"email": "ada@example.edu", "password": PASSWORD})
    assert "HttpOnly" in login.headers["set-cookie"] and "samesite=lax" in login.headers["set-cookie"].lower()
    cookie = client.cookies.get("osiss_session")

    duplicate = TestClient(app).post("/api/auth/register", json={"email": "ADA@example.edu", "password": PASSWORD})
    assert duplicate.status_code == 409
    assert TestClient(app).post("/api/auth/register", json={"email": "x@y.zz", "password": "short"}).status_code == 400

    assert client.post("/api/auth/logout").status_code == 200
    assert TestClient(app).get("/api/auth/me").status_code == 401
    client.cookies.set("osiss_session", cookie)  # the old token no longer works
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_lockout(app):
    scholar(app)
    other = TestClient(app)
    for _ in range(accounts.MAX_FAILURES):
        assert other.post("/api/auth/login", json={"email": "ada@example.edu", "password": "nope-nope"}).status_code == 401
    refused = other.post("/api/auth/login", json={"email": "ada@example.edu", "password": PASSWORD})
    assert refused.status_code == 429 and refused.headers["retry-after"]
    # an unknown email gets the same answer as a wrong password
    unknown = TestClient(app).post("/api/auth/login", json={"email": "who@example.edu", "password": PASSWORD})
    assert unknown.status_code == 401 and unknown.json()["detail"] == "Wrong email or password."


def test_registration_closed(app):
    object.__setattr__(settings, "registration_mode", "closed")
    assert TestClient(app).get("/api/auth/config").json() == {"registration": False}
    assert TestClient(app).post("/api/auth/register", json={"email": "a@b.cc", "password": PASSWORD}).status_code == 403
    created = TestClient(app).post("/api/admin/users", headers=ADMIN, json={"email": "new@example.edu", "password": PASSWORD})
    assert created.status_code == 200 and created.json()["must_change_password"]


def test_cross_site_writes_are_refused(app):
    client = scholar(app)
    response = client.post(
        "/api/workspace/collections", json={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403
    response = client.post("/api/workspace/collections", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.post("/api/workspace/collections", json={"name": "x"}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_workspace_needs_login(app):
    anonymous = TestClient(app)
    for path in ("/api/workspace/dashboard", "/api/workspace/evidence", "/api/workspace/collections", "/api/workspace/notes"):
        assert anonymous.get(path).status_code == 401


def test_evidence_collections_and_dedupe(app):
    client = scholar(app)
    first = client.post("/api/workspace/evidence", json=PASSAGE).json()
    assert first["book_title"] == "Data Structures" and first["book_author"] == "A. Author" and first["page_number"] == 12
    again = client.post("/api/workspace/evidence", json={**PASSAGE, "quote": "A stack  is a LIFO\nstructure."}).json()
    assert again["id"] == first["id"]  # same passage, whitespace aside
    assert client.get("/api/workspace/evidence").json()["total"] == 1

    # the title comes from the books table, never from the request
    forged = client.post("/api/workspace/evidence", json={**PASSAGE, "quote": "Other.", "meta": {"x": 1}, "book_title": "Fake"}).json()
    assert forged["book_title"] == "Data Structures"

    assert client.post("/api/workspace/evidence", json={"kind": "passage", "book_id": 99, "quote": "q"}).status_code == 404
    assert client.post("/api/workspace/evidence", json={"kind": "passage", "book_id": 1}).status_code == 400
    assert client.post("/api/workspace/evidence", json={"kind": "nonsense", "book_id": 1, "quote": "q"}).status_code == 400
    concept = client.post("/api/workspace/evidence", json={"kind": "concept", "concept_key": "stack"}).json()
    assert concept["book_id"] is None

    collection = client.post("/api/workspace/collections", json={"name": "Week 1"}).json()
    assert client.post("/api/workspace/collections", json={"name": "week 1"}).status_code == 409
    saved = client.post("/api/workspace/evidence", json={**PASSAGE, "quote": "Third.", "collection_id": collection["id"]}).json()
    assert saved["collection_ids"] == [collection["id"]]
    assert client.put(f"/api/workspace/collections/{collection['id']}/items/{first['id']}").status_code == 200
    detail = client.get(f"/api/workspace/collections/{collection['id']}").json()
    assert detail["item_count"] == 2 and {item["id"] for item in detail["items"]} == {first["id"], saved["id"]}
    assert client.get(f"/api/workspace/evidence?collection_id={collection['id']}&q=third").json()["total"] == 1

    assert client.delete(f"/api/workspace/collections/{collection['id']}/items/{first['id']}").status_code == 200
    assert client.delete(f"/api/workspace/collections/{collection['id']}/items/{first['id']}").status_code == 404
    assert client.patch(f"/api/workspace/collections/{collection['id']}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert client.delete(f"/api/workspace/collections/{collection['id']}").status_code == 200
    assert client.get("/api/workspace/evidence").json()["total"] == 4  # deleting a collection keeps its items
    assert client.delete(f"/api/workspace/evidence/{first['id']}").status_code == 200


def test_users_cannot_see_each_others_data(app):
    ada, bob = scholar(app), scholar(app, "bob@example.edu")
    item = ada.post("/api/workspace/evidence", json=PASSAGE).json()
    collection = ada.post("/api/workspace/collections", json={"name": "Private"}).json()
    note = ada.post("/api/workspace/notes", json={"title": "Mine", "body": "secret"}).json()

    assert bob.get("/api/workspace/evidence").json()["total"] == 0
    assert bob.get("/api/workspace/collections").json()["collections"] == []
    assert bob.get("/api/workspace/notes").json()["notes"] == []
    assert bob.get(f"/api/workspace/collections/{collection['id']}").status_code == 404
    assert bob.get(f"/api/workspace/notes/{note['id']}").status_code == 404
    assert bob.patch(f"/api/workspace/notes/{note['id']}", json={"body": "hijack"}).status_code == 404
    assert bob.patch(f"/api/workspace/collections/{collection['id']}", json={"name": "Hijack"}).status_code == 404
    assert bob.delete(f"/api/workspace/notes/{note['id']}").status_code == 404
    assert bob.delete(f"/api/workspace/evidence/{item['id']}").status_code == 404
    assert bob.delete(f"/api/workspace/collections/{collection['id']}").status_code == 404
    assert bob.put(f"/api/workspace/collections/{collection['id']}/items/{item['id']}").status_code == 404
    # his own collection cannot take her item, nor can a note point at it
    mine = bob.post("/api/workspace/collections", json={"name": "Mine"}).json()
    assert bob.put(f"/api/workspace/collections/{mine['id']}/items/{item['id']}").status_code == 404
    assert bob.post("/api/workspace/notes", json={"body": "x", "evidence_id": item["id"]}).status_code == 404
    assert bob.post("/api/workspace/evidence", json={**PASSAGE, "collection_id": collection["id"]}).status_code == 404
    assert ada.get(f"/api/workspace/notes/{note['id']}").json()["body"] == "secret"  # untouched
    assert ada.get("/api/workspace/evidence").json()["total"] == 1


def test_notes_and_backlinks(app):
    client = scholar(app)
    item = client.post("/api/workspace/evidence", json=PASSAGE).json()
    note = client.post(
        "/api/workspace/notes", json={"title": "LIFO", "body": "A [[Stack]] pops the newest. See [[queue]].", "evidence_id": item["id"]}
    ).json()
    assert note["concept_links"] == ["Stack", "queue"] and note["evidence_id"] == item["id"]
    client.post("/api/workspace/notes", json={"body": "Unrelated text"})
    assert [n["id"] for n in client.get("/api/workspace/notes?concept=stack").json()["notes"]] == [note["id"]]
    assert len(client.get("/api/workspace/notes?q=unrelated").json()["notes"]) == 1
    updated = client.patch(f"/api/workspace/notes/{note['id']}", json={"body": "Now [[heap]]"}).json()
    assert updated["concept_links"] == ["heap"] and updated["title"] == "LIFO"
    assert client.post("/api/workspace/notes", json={}).status_code == 400
    client.delete(f"/api/workspace/evidence/{item['id']}")
    assert client.get(f"/api/workspace/notes/{note['id']}").json()["evidence_id"] is None  # the note survives
    assert client.delete(f"/api/workspace/notes/{note['id']}").status_code == 200


def test_dashboard_activity_and_export(app):
    client = scholar(app)
    client.post("/api/workspace/activity", json={"kind": "search", "ref": "what is a stack"})
    client.post("/api/workspace/activity", json={"kind": "search", "ref": "what is a queue"})
    client.post("/api/workspace/activity", json={"kind": "search", "ref": "what is a stack"})  # moves to the top
    client.post("/api/workspace/activity", json={"kind": "book", "ref": "1", "book_id": 1})
    assert client.post("/api/workspace/activity", json={"kind": "book", "ref": "9", "book_id": 9}).status_code == 404
    assert client.post("/api/workspace/activity", json={"kind": "other", "ref": "x"}).status_code == 400
    item = client.post("/api/workspace/evidence", json=PASSAGE).json()
    client.post("/api/workspace/collections", json={"name": "C"})
    client.post("/api/workspace/notes", json={"title": "N"})

    board = client.get("/api/workspace/dashboard").json()
    assert board["counts"] == {"evidence": 1, "collections": 1, "notes": 1}
    assert [s["query"] for s in board["recent_searches"]] == ["what is a stack", "what is a queue"]
    assert board["continue_reading"][0]["title"] == "Data Structures"
    assert board["recent_evidence"][0]["id"] == item["id"]

    export = client.get("/api/workspace/export").json()
    assert len(export["evidence"]) == 1 and len(export["collections"]) == 1 and len(export["notes"]) == 1


def test_admin_reset_disable_and_delete(app, clean):
    client = scholar(app)
    other_device = TestClient(app)
    other_device.post("/api/auth/login", json={"email": "ada@example.edu", "password": PASSWORD})
    client.post("/api/workspace/evidence", json=PASSAGE)
    user_id = client.get("/api/auth/me").json()["user"]["id"]

    assert TestClient(app).get("/api/admin/users").status_code == 401  # needs the admin token
    listing = TestClient(app).get("/api/admin/users", headers=ADMIN).json()
    assert listing["users"][0]["email"] == "ada@example.edu" and listing["users"][0]["evidence_count"] == 1
    assert "password_hash" not in str(listing)

    reset = TestClient(app).post(f"/api/admin/users/{user_id}/reset-password", headers=ADMIN).json()
    temporary = reset["temporary_password"]
    assert client.get("/api/auth/me").status_code == 401 and other_device.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"email": "ada@example.edu", "password": PASSWORD}).status_code == 401

    fresh = TestClient(app)
    login = fresh.post("/api/auth/login", json={"email": "ada@example.edu", "password": temporary})
    assert login.status_code == 200 and login.json()["user"]["must_change_password"]
    assert fresh.get("/api/workspace/dashboard").status_code == 403  # blocked until the password is changed
    wrong = fresh.post("/api/auth/password", json={"current_password": "wrong-wrong", "new_password": "brand-new-pass"})
    assert wrong.status_code == 403
    assert fresh.post("/api/auth/password", json={"current_password": temporary, "new_password": "brand-new-pass"}).status_code == 200
    assert fresh.get("/api/workspace/dashboard").status_code == 200
    assert fresh.get("/api/auth/me").json()["user"]["must_change_password"] is False

    assert TestClient(app).patch(f"/api/admin/users/{user_id}", headers=ADMIN, json={"disabled": True}).status_code == 200
    assert fresh.get("/api/auth/me").status_code == 401
    refused = TestClient(app).post("/api/auth/login", json={"email": "ada@example.edu", "password": "brand-new-pass"})
    assert refused.status_code == 403
    TestClient(app).patch(f"/api/admin/users/{user_id}", headers=ADMIN, json={"disabled": False})

    assert TestClient(app).delete(f"/api/admin/users/{user_id}", headers=ADMIN).status_code == 200
    with clean, clean.cursor() as cursor:
        cursor.execute("SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM evidence_items), (SELECT COUNT(*) FROM user_sessions)")
        assert cursor.fetchone() == (0, 0, 0)
    assert TestClient(app).delete(f"/api/admin/users/{user_id}", headers=ADMIN).status_code == 404


def test_projects_books_evidence_and_notes(app):
    client = scholar(app)
    project = client.post(
        "/api/workspace/projects", json={"title": "Sorting", "question": "Which sort is stable?", "book_ids": [2, 1, 1]}
    ).json()
    assert [b["title"] for b in project["books"]] == ["Algorithms", "Data Structures"] and project["book_count"] == 2
    assert client.post("/api/workspace/projects", json={"title": " "}).status_code == 400
    assert client.post("/api/workspace/projects", json={"title": "X", "book_ids": [99]}).status_code == 404

    pid = project["id"]
    assert client.post(f"/api/workspace/projects/{pid}/books", json={"book_ids": [99]}).status_code == 404
    assert client.post(f"/api/workspace/projects/{pid}/books", json={"book_ids": []}).status_code == 422
    assert client.delete(f"/api/workspace/projects/{pid}/books/2").status_code == 200
    assert client.delete(f"/api/workspace/projects/{pid}/books/2").status_code == 404
    assert client.post(f"/api/workspace/projects/{pid}/books", json={"book_ids": [2]}).json()["book_count"] == 2

    item = client.post("/api/workspace/evidence", json={**PASSAGE, "project_id": pid}).json()
    other = client.post("/api/workspace/evidence", json={**PASSAGE, "quote": "Not in the project."}).json()
    assert client.put(f"/api/workspace/projects/{pid}/evidence/{other['id']}").status_code == 200
    assert client.put(f"/api/workspace/projects/{pid}/evidence/{other['id']}").status_code == 200  # idempotent
    assert client.delete(f"/api/workspace/projects/{pid}/evidence/{other['id']}").status_code == 200
    assert client.post("/api/workspace/evidence", json={**PASSAGE, "project_id": 999}).status_code == 404
    note = client.post("/api/workspace/notes", json={"title": "Plan", "body": "Compare [[merge sort]]", "project_id": pid}).json()
    assert note["project_id"] == pid
    client.post("/api/workspace/notes", json={"title": "Elsewhere"})
    assert [n["id"] for n in client.get(f"/api/workspace/notes?project_id={pid}").json()["notes"]] == [note["id"]]
    assert client.post("/api/workspace/notes", json={"title": "x", "project_id": 999}).status_code == 404

    detail = client.get(f"/api/workspace/projects/{pid}").json()
    assert [e["id"] for e in detail["evidence"]] == [item["id"]] and [n["id"] for n in detail["notes"]] == [note["id"]]
    assert detail["evidence_count"] == 1 and detail["note_count"] == 1
    assert client.patch(f"/api/workspace/projects/{pid}", json={"question": "Which is fastest?"}).json()["question"] == "Which is fastest?"
    assert client.get("/api/workspace/dashboard").json()["projects"][0]["id"] == pid
    export = client.get("/api/workspace/export").json()
    assert export["projects"][0]["book_ids"] == [1, 2] and export["projects"][0]["evidence_ids"] == [item["id"]]

    assert client.delete(f"/api/workspace/projects/{pid}").status_code == 200  # items and notes survive
    assert client.get("/api/workspace/evidence").json()["total"] == 2
    assert client.get(f"/api/workspace/notes/{note['id']}").json()["project_id"] is None


def test_projects_are_private(app):
    ada, bob = scholar(app), scholar(app, "bob@example.edu")
    pid = ada.post("/api/workspace/projects", json={"title": "Mine", "book_ids": [1]}).json()["id"]
    item = ada.post("/api/workspace/evidence", json=PASSAGE).json()
    assert bob.get("/api/workspace/projects").json()["projects"] == []
    assert bob.get(f"/api/workspace/projects/{pid}").status_code == 404
    assert bob.patch(f"/api/workspace/projects/{pid}", json={"title": "Hijack"}).status_code == 404
    assert bob.delete(f"/api/workspace/projects/{pid}").status_code == 404
    assert bob.post(f"/api/workspace/projects/{pid}/books", json={"book_ids": [2]}).status_code == 404
    assert bob.delete(f"/api/workspace/projects/{pid}/books/1").status_code == 404
    assert bob.put(f"/api/workspace/projects/{pid}/evidence/{item['id']}").status_code == 404
    mine = bob.post("/api/workspace/projects", json={"title": "Bob's"}).json()["id"]
    assert bob.put(f"/api/workspace/projects/{mine}/evidence/{item['id']}").status_code == 404  # her item
    assert bob.post("/api/workspace/notes", json={"body": "x", "project_id": pid}).status_code == 404
    assert bob.post("/api/workspace/evidence", json={**PASSAGE, "project_id": pid}).status_code == 404
    assert ada.get(f"/api/workspace/projects/{pid}").json()["title"] == "Mine"


def test_highlights(app):
    client = scholar(app)
    rects = [[0.1, 0.2, 0.5, 0.23], [0.1, 0.24, 0.3, 0.27]]
    saved = client.post(
        "/api/workspace/evidence",
        json={"kind": "highlight", "book_id": 1, "page_number": 12, "quote": "A stack is LIFO", "meta": {"rects": rects}},
    ).json()
    assert saved["kind"] == "highlight" and saved["meta"] == {"rects": rects} and saved["book_title"] == "Data Structures"
    again = client.post(
        "/api/workspace/evidence",
        json={"kind": "highlight", "book_id": 1, "page_number": 12, "quote": "A stack is LIFO", "meta": {"rects": rects}},
    ).json()
    assert again["id"] == saved["id"]
    elsewhere = client.post(
        "/api/workspace/evidence",
        json={"kind": "highlight", "book_id": 1, "page_number": 12, "quote": "A stack is LIFO", "meta": {"rects": [[0.1, 0.6, 0.4, 0.63]]}},
    ).json()
    assert elsewhere["id"] != saved["id"]  # the same words highlighted at another place is another highlight

    for bad in (
        {"rects": []}, {"rects": [[0.5, 0.2, 0.1, 0.3]]}, {"rects": [[0, 0, 2, 1]]}, {"rects": [[0, 0, 1]]},
        {"rects": [[0.1, 0.1, 0.2, 0.2]] * 61}, {},
    ):
        response = client.post(
            "/api/workspace/evidence",
            json={"kind": "highlight", "book_id": 1, "page_number": 3, "quote": "x", "meta": bad},
        )
        assert response.status_code == 400, bad
    assert client.post("/api/workspace/evidence", json={"kind": "highlight", "book_id": 1, "quote": "x", "meta": {"rects": rects}}).status_code == 400

    client.post("/api/workspace/evidence", json={**PASSAGE, "page_number": 12})
    on_page = client.get("/api/workspace/evidence?kind=highlight&book_id=1&page_number=12").json()
    assert on_page["total"] == 2 and {h["id"] for h in on_page["items"]} == {saved["id"], elsewhere["id"]}
    assert client.get("/api/workspace/evidence?kind=highlight&book_id=1&page_number=13").json()["total"] == 0
    assert client.get("/api/workspace/evidence?kind=highlight&book_id=2").json()["total"] == 0


def test_reading_progress_is_remembered(app):
    client = scholar(app)
    client.post("/api/workspace/activity", json={"kind": "book", "ref": "1", "book_id": 1, "page_number": 40})
    client.post("/api/workspace/activity", json={"kind": "book", "ref": "1", "book_id": 1})  # opening it elsewhere keeps the page
    assert client.get("/api/workspace/dashboard").json()["continue_reading"][0]["page_number"] == 40
    client.post("/api/workspace/activity", json={"kind": "book", "ref": "1", "book_id": 1, "page_number": 41})
    assert client.get("/api/workspace/dashboard").json()["continue_reading"][0]["page_number"] == 41
    assert client.post("/api/workspace/activity", json={"kind": "book", "ref": "1", "book_id": 1, "page_number": 0}).status_code == 422


def test_saved_items_carry_the_printed_page(app, clean):
    client = scholar(app)
    item = client.post("/api/workspace/evidence", json=PASSAGE).json()
    assert item["printed_page"] is None  # nothing known about this book's numbering
    with clean, clean.cursor() as cursor:
        cursor.execute("UPDATE books SET page_offset = 2 WHERE id = 1")
    assert client.get("/api/workspace/evidence").json()["items"][0]["printed_page"] == "10"  # PDF page 12
    assert client.get("/api/workspace/dashboard").json()["recent_evidence"][0]["printed_page"] == "10"
    concept = client.post("/api/workspace/evidence", json={"kind": "concept", "concept_key": "stack"}).json()
    assert concept["printed_page"] is None

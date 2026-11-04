"""A scholar's workspace: evidence items, collections, notes, dashboard. Model- and web-framework-free.

Every function takes an open psycopg2 connection and the owner's user id, and only ever touches that owner's rows
(ownership is part of every WHERE clause, so a guessed id from another account finds nothing). Nothing commits; the
caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

KINDS = ("passage", "definition", "figure", "table", "formula", "page", "concept")
MAX_QUOTE = 8000
MAX_NOTE = 100_000
CONCEPT_LINK_RE = re.compile(r"\[\[([^\[\]\n]{1,120})\]\]")


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def quote_hash(text: str) -> str:
    """SHA-256 of the quote with whitespace collapsed, so the same passage saved twice is one item and a draft can
    later be checked against the page it came from."""
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()


def concept_links(body: str) -> List[str]:
    """The `[[concept]]` names used in a note, in order, without repeats."""
    seen: List[str] = []
    for name in CONCEPT_LINK_RE.findall(body or ""):
        name = " ".join(name.split())
        if name and name.lower() not in [item.lower() for item in seen]:
            seen.append(name)
    return seen


# --- evidence items -----------------------------------------------------------------------------------------------

EVIDENCE_COLUMNS = (
    "e.id, e.kind, e.book_id, e.book_title, e.book_author, e.page_number, e.quote, e.concept_key, e.meta, e.created_at"
)


def _evidence(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "kind": row[1],
        "book_id": row[2],
        "book_title": row[3],
        "book_author": row[4],
        "page_number": row[5],
        "quote": row[6],
        "concept_key": row[7],
        "meta": row[8] or {},
        "created_at": row[9].isoformat() if row[9] else None,
    }


def save_evidence(connection, owner_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """Save a piece of evidence (idempotent: the same quote from the same page returns the existing item).

    The book's title and author are read from the books table, not trusted from the request, so a citation cannot be
    invented for a real book."""
    kind = item.get("kind")
    if kind not in KINDS:
        raise WorkspaceError(f"kind must be one of {', '.join(KINDS)}.")
    quote = (item.get("quote") or "").strip()[:MAX_QUOTE]
    book_id = item.get("book_id")
    page = item.get("page_number")
    concept_key = (item.get("concept_key") or "").strip()[:200] or None
    if kind == "concept":
        if not concept_key:
            raise WorkspaceError("A concept needs a concept_key.")
    elif book_id is None:
        raise WorkspaceError("Evidence from a book needs book_id.")
    elif kind in ("passage", "definition", "formula") and not quote:
        raise WorkspaceError("A passage needs its quote.")
    if page is not None and page < 1:
        raise WorkspaceError("page_number must be 1 or more.")

    title = author = None
    if book_id is not None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT title, author FROM books WHERE id = %s", (book_id,))
            book = cursor.fetchone()
        if not book:
            raise WorkspaceError("Unknown book.", 404)
        title, author = book

    meta = item.get("meta") or {}
    if not isinstance(meta, dict) or len(json.dumps(meta)) > 4000:
        raise WorkspaceError("meta must be a small object.")
    digest = quote_hash(quote or concept_key or f"{kind}:{meta.get('image', '')}")
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO evidence_items (owner_id, kind, book_id, book_title, book_author, page_number, quote, "
            "content_hash, concept_key, meta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (owner_id, kind, COALESCE(book_id, 0), COALESCE(page_number, 0), content_hash) "
            "DO UPDATE SET content_hash = evidence_items.content_hash RETURNING id",
            (owner_id, kind, book_id, title, author, page, quote, digest, concept_key, json.dumps(meta)),
        )
        evidence_id = cursor.fetchone()[0]
    return get_evidence(connection, owner_id, evidence_id)  # type: ignore[return-value]


def get_evidence(connection, owner_id: int, evidence_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {EVIDENCE_COLUMNS}, "
            "(SELECT COALESCE(json_agg(ci.collection_id), '[]') FROM collection_items ci WHERE ci.evidence_id = e.id) "
            "FROM evidence_items e WHERE e.id = %s AND e.owner_id = %s",
            (evidence_id, owner_id),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return {**_evidence(row), "collection_ids": row[10]}


def list_evidence(
    connection, owner_id: int, collection_id: Optional[int] = None, kind: Optional[str] = None,
    query: Optional[str] = None, limit: int = 100, offset: int = 0,
) -> Dict[str, Any]:
    where, params = ["e.owner_id = %s"], [owner_id]
    join = ""
    if collection_id is not None:
        join = "JOIN collection_items ci ON ci.evidence_id = e.id AND ci.collection_id = %s"
        params.insert(0, collection_id)
    if kind:
        where.append("e.kind = %s")
        params.append(kind)
    if query:
        where.append("(e.quote ILIKE %s OR e.book_title ILIKE %s OR e.concept_key ILIKE %s)")
        like = f"%{query.strip()}%"
        params.extend([like, like, like])
    clause = " AND ".join(where)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM evidence_items e {join} WHERE {clause}", params)
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT {EVIDENCE_COLUMNS}, "
            "(SELECT COALESCE(json_agg(c.collection_id), '[]') FROM collection_items c WHERE c.evidence_id = e.id) "
            f"FROM evidence_items e {join} WHERE {clause} ORDER BY e.created_at DESC, e.id DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = cursor.fetchall()
    return {"total": total, "items": [{**_evidence(row), "collection_ids": row[10]} for row in rows]}


def delete_evidence(connection, owner_id: int, evidence_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM evidence_items WHERE id = %s AND owner_id = %s", (evidence_id, owner_id))
        return cursor.rowcount > 0


# --- collections --------------------------------------------------------------------------------------------------


def _collection(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "item_count": row[3],
        "updated_at": row[4].isoformat() if row[4] else None,
    }


COLLECTION_SELECT = (
    "SELECT c.id, c.name, c.description, (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id), "
    "c.updated_at FROM collections c"
)


def clean_text(value: Optional[str], limit: int, what: str, required: bool = False) -> str:
    value = (value or "").strip()
    if required and not value:
        raise WorkspaceError(f"{what} is required.")
    return value[:limit]


def list_collections(connection, owner_id: int) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"{COLLECTION_SELECT} WHERE c.owner_id = %s ORDER BY c.updated_at DESC, c.id DESC", (owner_id,))
        return [_collection(row) for row in cursor.fetchall()]


def get_collection(connection, owner_id: int, collection_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"{COLLECTION_SELECT} WHERE c.id = %s AND c.owner_id = %s", (collection_id, owner_id))
        row = cursor.fetchone()
    return _collection(row) if row else None


def create_collection(connection, owner_id: int, name: str, description: str = "") -> Dict[str, Any]:
    name = clean_text(name, 120, "Name", required=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM collections WHERE owner_id = %s AND LOWER(name) = LOWER(%s)", (owner_id, name))
        if cursor.fetchone():
            raise WorkspaceError("You already have a collection with this name.", 409)
        cursor.execute(
            "INSERT INTO collections (owner_id, name, description) VALUES (%s, %s, %s) RETURNING id",
            (owner_id, name, clean_text(description, 2000, "Description")),
        )
        collection_id = cursor.fetchone()[0]
    return get_collection(connection, owner_id, collection_id)  # type: ignore[return-value]


def update_collection(
    connection, owner_id: int, collection_id: int, name: Optional[str] = None, description: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    current = get_collection(connection, owner_id, collection_id)
    if current is None:
        return None
    new_name = clean_text(name, 120, "Name", required=True) if name is not None else current["name"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM collections WHERE owner_id = %s AND LOWER(name) = LOWER(%s) AND id <> %s",
            (owner_id, new_name, collection_id),
        )
        if cursor.fetchone():
            raise WorkspaceError("You already have a collection with this name.", 409)
        cursor.execute(
            "UPDATE collections SET name = %s, description = %s, updated_at = NOW() WHERE id = %s AND owner_id = %s",
            (
                new_name,
                clean_text(description, 2000, "Description") if description is not None else current["description"],
                collection_id,
                owner_id,
            ),
        )
    return get_collection(connection, owner_id, collection_id)


def delete_collection(connection, owner_id: int, collection_id: int) -> bool:
    """Deletes the collection only; the evidence items in it stay saved."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM collections WHERE id = %s AND owner_id = %s", (collection_id, owner_id))
        return cursor.rowcount > 0


def add_to_collection(connection, owner_id: int, collection_id: int, evidence_id: int) -> bool:
    """False when the collection or the item is not the owner's."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO collection_items (collection_id, evidence_id) "
            "SELECT c.id, e.id FROM collections c, evidence_items e "
            "WHERE c.id = %s AND c.owner_id = %s AND e.id = %s AND e.owner_id = %s ON CONFLICT DO NOTHING",
            (collection_id, owner_id, evidence_id, owner_id),
        )
        added = cursor.rowcount > 0
        if not added:
            cursor.execute(
                "SELECT 1 FROM collection_items i JOIN collections c ON c.id = i.collection_id "
                "WHERE i.collection_id = %s AND i.evidence_id = %s AND c.owner_id = %s",
                (collection_id, evidence_id, owner_id),
            )
            present = cursor.fetchone() is not None
        else:
            present = True
        if present:
            cursor.execute("UPDATE collections SET updated_at = NOW() WHERE id = %s", (collection_id,))
    return present


def remove_from_collection(connection, owner_id: int, collection_id: int, evidence_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM collection_items USING collections c WHERE collection_items.collection_id = c.id "
            "AND c.id = %s AND c.owner_id = %s AND collection_items.evidence_id = %s",
            (collection_id, owner_id, evidence_id),
        )
        return cursor.rowcount > 0


# --- notes --------------------------------------------------------------------------------------------------------


def _note(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "title": row[1],
        "body": row[2],
        "evidence_id": row[3],
        "concept_links": concept_links(row[2]),
        "created_at": row[4].isoformat() if row[4] else None,
        "updated_at": row[5].isoformat() if row[5] else None,
    }


NOTE_COLUMNS = "id, title, body, evidence_id, created_at, updated_at"


def _own_evidence_id(connection, owner_id: int, evidence_id: Optional[int]) -> Optional[int]:
    if evidence_id is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM evidence_items WHERE id = %s AND owner_id = %s", (evidence_id, owner_id))
        if not cursor.fetchone():
            raise WorkspaceError("Unknown evidence item.", 404)
    return evidence_id


def create_note(
    connection, owner_id: int, title: str = "", body: str = "", evidence_id: Optional[int] = None
) -> Dict[str, Any]:
    body = clean_text(body, MAX_NOTE, "Body")
    title = clean_text(title, 200, "Title")
    if not title and not body:
        raise WorkspaceError("A note needs a title or some text.")
    evidence_id = _own_evidence_id(connection, owner_id, evidence_id)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO notes (owner_id, title, body, evidence_id) VALUES (%s, %s, %s, %s) RETURNING {NOTE_COLUMNS}",
            (owner_id, title, body, evidence_id),
        )
        return _note(cursor.fetchone())


def get_note(connection, owner_id: int, note_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {NOTE_COLUMNS} FROM notes WHERE id = %s AND owner_id = %s", (note_id, owner_id))
        row = cursor.fetchone()
    return _note(row) if row else None


def update_note(
    connection, owner_id: int, note_id: int, title: Optional[str] = None, body: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    current = get_note(connection, owner_id, note_id)
    if current is None:
        return None
    new_title = clean_text(title, 200, "Title") if title is not None else current["title"]
    new_body = clean_text(body, MAX_NOTE, "Body") if body is not None else current["body"]
    if not new_title and not new_body:
        raise WorkspaceError("A note needs a title or some text.")
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE notes SET title = %s, body = %s, updated_at = NOW() WHERE id = %s AND owner_id = %s "
            f"RETURNING {NOTE_COLUMNS}",
            (new_title, new_body, note_id, owner_id),
        )
        return _note(cursor.fetchone())


def delete_note(connection, owner_id: int, note_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM notes WHERE id = %s AND owner_id = %s", (note_id, owner_id))
        return cursor.rowcount > 0


def list_notes(
    connection, owner_id: int, query: Optional[str] = None, concept: Optional[str] = None, limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Newest first. `concept` keeps the notes that link `[[concept]]` (the concept's backlinks)."""
    where, params = ["owner_id = %s"], [owner_id]
    if query:
        where.append("(title ILIKE %s OR body ILIKE %s)")
        like = f"%{query.strip()}%"
        params.extend([like, like])
    if concept:
        where.append("LOWER(body) LIKE %s")
        params.append(f"%[[{concept.strip().lower()}]]%")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {NOTE_COLUMNS} FROM notes WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        return [_note(row) for row in cursor.fetchall()]


# --- activity and dashboard ---------------------------------------------------------------------------------------

ACTIVITY_KINDS = ("search", "book")
KEEP_ACTIVITY = 50  # per kind


def record_activity(
    connection, owner_id: int, kind: str, ref: str, label: str, book_id: Optional[int] = None
) -> None:
    """Remember a search or an opened book (dashboard: recent searches, continue reading)."""
    if kind not in ACTIVITY_KINDS:
        raise WorkspaceError("kind must be search or book.")
    ref = ref.strip()[:300]
    label = (label or ref).strip()[:300]
    if not ref:
        raise WorkspaceError("ref is required.")
    if kind == "book":
        with connection.cursor() as cursor:
            cursor.execute("SELECT title FROM books WHERE id = %s", (book_id,))
            row = cursor.fetchone()
        if not row:
            raise WorkspaceError("Unknown book.", 404)
        label = row[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO user_activity (owner_id, kind, ref, label, book_id) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (owner_id, kind, ref) DO UPDATE SET at = NOW(), label = EXCLUDED.label",
            (owner_id, kind, ref, label, book_id),
        )
        cursor.execute(
            "DELETE FROM user_activity WHERE owner_id = %s AND kind = %s AND ref NOT IN "
            "(SELECT ref FROM user_activity WHERE owner_id = %s AND kind = %s ORDER BY at DESC LIMIT %s)",
            (owner_id, kind, owner_id, kind, KEEP_ACTIVITY),
        )


def dashboard(connection, owner_id: int) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM evidence_items WHERE owner_id = %s), "
            "(SELECT COUNT(*) FROM collections WHERE owner_id = %s), (SELECT COUNT(*) FROM notes WHERE owner_id = %s)",
            (owner_id, owner_id, owner_id),
        )
        evidence_count, collection_count, note_count = cursor.fetchone()
        cursor.execute(
            "SELECT ref, label, at FROM user_activity WHERE owner_id = %s AND kind = 'search' "
            "ORDER BY at DESC LIMIT 8",
            (owner_id,),
        )
        searches = [{"query": row[0], "at": row[2].isoformat()} for row in cursor.fetchall()]
        cursor.execute(
            "SELECT a.book_id, a.label, b.author, b.department, a.at FROM user_activity a "
            "JOIN books b ON b.id = a.book_id WHERE a.owner_id = %s AND a.kind = 'book' ORDER BY a.at DESC LIMIT 6",
            (owner_id,),
        )
        books = [
            {"book_id": row[0], "title": row[1], "author": row[2], "department": row[3], "at": row[4].isoformat()}
            for row in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT COUNT(*) FROM books WHERE created_at > NOW() - INTERVAL '30 days'"
        )
        new_books = cursor.fetchone()[0]
    return {
        "counts": {"evidence": evidence_count, "collections": collection_count, "notes": note_count},
        "recent_searches": searches,
        "continue_reading": books,
        "recent_evidence": list_evidence(connection, owner_id, limit=5)["items"],
        "collections": list_collections(connection, owner_id)[:6],
        "recent_notes": list_notes(connection, owner_id, limit=5),
        "new_books_30_days": new_books,
    }


def export_all(connection, owner_id: int) -> Dict[str, Any]:
    """Everything the scholar saved, as one JSON-able dict (data portability)."""
    evidence = list_evidence(connection, owner_id, limit=1_000_000)["items"]
    collections = []
    for collection in list_collections(connection, owner_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT evidence_id FROM collection_items WHERE collection_id = %s", (collection["id"],))
            collections.append({**collection, "evidence_ids": [row[0] for row in cursor.fetchall()]})
    return {
        "evidence": evidence,
        "collections": collections,
        "notes": list_notes(connection, owner_id, limit=1_000_000),
    }

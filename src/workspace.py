"""A scholar's workspace: evidence items, collections, notes, dashboard. Model- and web-framework-free.

Every function takes an open psycopg2 connection and the owner's user id, and only ever touches that owner's rows
(ownership is part of every WHERE clause, so a guessed id from another account finds nothing). Nothing commits; the
caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence

import page_labels

KINDS = ("passage", "definition", "figure", "table", "formula", "page", "concept", "highlight")
MAX_RECTS = 60
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


def clean_rects(rects: Any) -> List[List[float]]:
    """Highlight rectangles as page fractions [x0, y0, x1, y1] in 0..1 (at most MAX_RECTS)."""
    if not isinstance(rects, list) or not rects or len(rects) > MAX_RECTS:
        raise WorkspaceError(f"A highlight needs 1 to {MAX_RECTS} rectangles.")
    cleaned = []
    for rect in rects:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(isinstance(v, (int, float)) for v in rect):
            raise WorkspaceError("Each rectangle is [x0, y0, x1, y1].")
        x0, y0, x1, y1 = (round(float(v), 4) for v in rect)
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise WorkspaceError("Rectangles are fractions of the page (0 to 1).")
        cleaned.append([x0, y0, x1, y1])
    return cleaned


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
    if kind == "highlight":
        meta = {"rects": clean_rects(meta.get("rects"))}
        if page is None or not quote:
            raise WorkspaceError("A highlight needs page_number and the highlighted text.")
        digest = quote_hash(quote + json.dumps(meta["rects"]))
    else:
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


def add_printed_pages(connection, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Set `printed_page` (label printed on the page, None when unknown) on evidence dicts, in place."""
    ids = sorted({item["book_id"] for item in items if item.get("book_id") and item.get("page_number")})
    info: Dict[int, Any] = {}
    if ids:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, page_offset, page_labels FROM books WHERE id = ANY(%s)", (ids,))
            info = {row[0]: (row[1], row[2] or None) for row in cursor.fetchall()}
    for item in items:
        offset, labels = info.get(item.get("book_id"), (None, None))
        item["printed_page"] = (
            page_labels.printed_label(item["page_number"], offset, labels) if item.get("page_number") else None
        )
    return items


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
    return add_printed_pages(connection, [{**_evidence(row), "collection_ids": row[10]}])[0]


def list_evidence(
    connection, owner_id: int, collection_id: Optional[int] = None, kind: Optional[str] = None,
    query: Optional[str] = None, limit: int = 100, offset: int = 0, project_id: Optional[int] = None,
    book_id: Optional[int] = None, page_number: Optional[int] = None,
) -> Dict[str, Any]:
    where, params = ["e.owner_id = %s"], [owner_id]
    join, join_params = "", []
    if collection_id is not None:
        join += " JOIN collection_items ci ON ci.evidence_id = e.id AND ci.collection_id = %s"
        join_params.append(collection_id)
    if project_id is not None:
        join += " JOIN project_evidence pe ON pe.evidence_id = e.id AND pe.project_id = %s"
        join_params.append(project_id)
    params = join_params + params
    if book_id is not None:
        where.append("e.book_id = %s")
        params.append(book_id)
    if page_number is not None:
        where.append("e.page_number = %s")
        params.append(page_number)
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
    items = [{**_evidence(row), "collection_ids": row[10]} for row in rows]
    return {"total": total, "items": add_printed_pages(connection, items)}


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
        "project_id": row[6],
    }


NOTE_COLUMNS = "id, title, body, evidence_id, created_at, updated_at, project_id"


def _own_evidence_id(connection, owner_id: int, evidence_id: Optional[int]) -> Optional[int]:
    if evidence_id is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM evidence_items WHERE id = %s AND owner_id = %s", (evidence_id, owner_id))
        if not cursor.fetchone():
            raise WorkspaceError("Unknown evidence item.", 404)
    return evidence_id


def create_note(
    connection, owner_id: int, title: str = "", body: str = "", evidence_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    body = clean_text(body, MAX_NOTE, "Body")
    title = clean_text(title, 200, "Title")
    if not title and not body:
        raise WorkspaceError("A note needs a title or some text.")
    evidence_id = _own_evidence_id(connection, owner_id, evidence_id)
    if project_id is not None and get_project(connection, owner_id, project_id, detail=False) is None:
        raise WorkspaceError("Unknown project.", 404)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO notes (owner_id, title, body, evidence_id, project_id) VALUES (%s, %s, %s, %s, %s) "
            f"RETURNING {NOTE_COLUMNS}",
            (owner_id, title, body, evidence_id, project_id),
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
    offset: int = 0, project_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Newest first. `concept` keeps the notes that link `[[concept]]` (the concept's backlinks)."""
    where, params = ["owner_id = %s"], [owner_id]
    if query:
        where.append("(title ILIKE %s OR body ILIKE %s)")
        like = f"%{query.strip()}%"
        params.extend([like, like])
    if project_id is not None:
        where.append("project_id = %s")
        params.append(project_id)
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
    connection, owner_id: int, kind: str, ref: str, label: str, book_id: Optional[int] = None,
    page_number: Optional[int] = None,
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
            "INSERT INTO user_activity (owner_id, kind, ref, label, book_id, page_number) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (owner_id, kind, ref) DO UPDATE SET at = NOW(), label = EXCLUDED.label, "
            "page_number = COALESCE(EXCLUDED.page_number, user_activity.page_number)",
            (owner_id, kind, ref, label, book_id, page_number),
        )
        cursor.execute(
            "DELETE FROM user_activity WHERE owner_id = %s AND kind = %s AND ref NOT IN "
            "(SELECT ref FROM user_activity WHERE owner_id = %s AND kind = %s ORDER BY at DESC LIMIT %s)",
            (owner_id, kind, owner_id, kind, KEEP_ACTIVITY),
        )


def dashboard(connection, owner_id: int) -> Dict[str, Any]:
    import alerts  # pylint: disable=import-outside-toplevel
    import study  # pylint: disable=import-outside-toplevel

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
            "SELECT a.book_id, a.label, b.author, b.department, a.at, a.page_number FROM user_activity a "
            "JOIN books b ON b.id = a.book_id WHERE a.owner_id = %s AND a.kind = 'book' ORDER BY a.at DESC LIMIT 6",
            (owner_id,),
        )
        books = [
            {"book_id": row[0], "title": row[1], "author": row[2], "department": row[3], "at": row[4].isoformat(),
             "page_number": row[5]}
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
        "projects": list_projects(connection, owner_id)[:4],
        "study": study.stats(connection, owner_id),
        "saved_searches": alerts.list_searches(connection, owner_id),
        "unread_notifications": alerts.list_notifications(connection, owner_id, unread_only=True),
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
    projects = []
    for project in list_projects(connection, owner_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT book_id FROM project_books WHERE project_id = %s ORDER BY book_id", (project["id"],))
            book_ids = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT evidence_id FROM project_evidence WHERE project_id = %s ORDER BY evidence_id", (project["id"],))
            evidence_ids = [row[0] for row in cursor.fetchall()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, kind, text, evidence_id, note_id, concept_key, x, y FROM board_nodes WHERE project_id = %s ORDER BY id", (project["id"],))
            nodes = [dict(zip(("id", "kind", "text", "evidence_id", "note_id", "concept_key", "x", "y"), row)) for row in cursor.fetchall()]
            cursor.execute("SELECT id, source_id, target_id, relation, note FROM board_edges WHERE project_id = %s ORDER BY id", (project["id"],))
            edges = [dict(zip(("id", "source_id", "target_id", "relation", "note"), row)) for row in cursor.fetchall()]
        projects.append({**project, "book_ids": book_ids, "evidence_ids": evidence_ids, "board": {"nodes": nodes, "edges": edges}})
    import alerts  # pylint: disable=import-outside-toplevel
    import drafts  # pylint: disable=import-outside-toplevel
    import study  # pylint: disable=import-outside-toplevel

    return {
        "evidence": evidence,
        "collections": collections,
        "notes": list_notes(connection, owner_id, limit=1_000_000),
        "projects": projects,
        "drafts": [drafts.get_draft(connection, owner_id, d["id"]) for d in drafts.list_drafts(connection, owner_id)],
        "cards": study.list_cards(connection, owner_id, limit=500)["cards"],
        "saved_searches": alerts.list_searches(connection, owner_id),
    }


# --- projects -----------------------------------------------------------------------------------------------------

PROJECT_SELECT = (
    "SELECT p.id, p.title, p.question, p.description, p.created_at, p.updated_at, "
    "(SELECT COUNT(*) FROM project_books b WHERE b.project_id = p.id), "
    "(SELECT COUNT(*) FROM project_evidence e WHERE e.project_id = p.id), "
    "(SELECT COUNT(*) FROM notes n WHERE n.project_id = p.id) FROM projects p"
)


def _project(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "title": row[1],
        "question": row[2],
        "description": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
        "updated_at": row[5].isoformat() if row[5] else None,
        "book_count": row[6],
        "evidence_count": row[7],
        "note_count": row[8],
    }


def list_projects(connection, owner_id: int) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"{PROJECT_SELECT} WHERE p.owner_id = %s ORDER BY p.updated_at DESC, p.id DESC", (owner_id,))
        return [_project(row) for row in cursor.fetchall()]


def project_books(connection, project_id: int) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT b.id, b.title, b.author, b.department, b.language_code FROM project_books pb "
            "JOIN books b ON b.id = pb.book_id WHERE pb.project_id = %s ORDER BY LOWER(b.title), b.id",
            (project_id,),
        )
        return [
            {"id": row[0], "title": row[1], "author": row[2], "department": row[3], "language": row[4]}
            for row in cursor.fetchall()
        ]


def get_project(connection, owner_id: int, project_id: int, detail: bool = True) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"{PROJECT_SELECT} WHERE p.id = %s AND p.owner_id = %s", (project_id, owner_id))
        row = cursor.fetchone()
    if not row:
        return None
    project = _project(row)
    if detail:
        project["books"] = project_books(connection, project_id)
        project["evidence"] = list_evidence(connection, owner_id, project_id=project_id, limit=200)["items"]
        project["notes"] = list_notes(connection, owner_id, project_id=project_id, limit=200)
    return project


def create_project(
    connection, owner_id: int, title: str, question: str = "", description: str = "",
    book_ids: Sequence[int] = (),
) -> Dict[str, Any]:
    title = clean_text(title, 200, "Title", required=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO projects (owner_id, title, question, description) VALUES (%s, %s, %s, %s) RETURNING id",
            (owner_id, title, clean_text(question, 1000, "Question"), clean_text(description, 4000, "Description")),
        )
        project_id = cursor.fetchone()[0]
    if book_ids:
        add_project_books(connection, owner_id, project_id, book_ids)
    return get_project(connection, owner_id, project_id)  # type: ignore[return-value]


def update_project(
    connection, owner_id: int, project_id: int, title: Optional[str] = None, question: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    current = get_project(connection, owner_id, project_id, detail=False)
    if current is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE projects SET title = %s, question = %s, description = %s, updated_at = NOW() "
            "WHERE id = %s AND owner_id = %s",
            (
                clean_text(title, 200, "Title", required=True) if title is not None else current["title"],
                clean_text(question, 1000, "Question") if question is not None else current["question"],
                clean_text(description, 4000, "Description") if description is not None else current["description"],
                project_id,
                owner_id,
            ),
        )
    return get_project(connection, owner_id, project_id)


def delete_project(connection, owner_id: int, project_id: int) -> bool:
    """Deletes the project; its saved items stay in the workspace and its notes stay (without a project)."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
        return cursor.rowcount > 0


def _touch_project(cursor, project_id: int) -> None:
    cursor.execute("UPDATE projects SET updated_at = NOW() WHERE id = %s", (project_id,))


def add_project_books(connection, owner_id: int, project_id: int, book_ids: Sequence[int]) -> bool:
    """False when the project is not the owner's. Unknown book ids are an error."""
    if get_project(connection, owner_id, project_id, detail=False) is None:
        return False
    ids = sorted({int(book) for book in book_ids})
    if not ids:
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM books WHERE id = ANY(%s)", (ids,))
        found = {row[0] for row in cursor.fetchall()}
        if found != set(ids):
            raise WorkspaceError(f"Unknown book(s): {', '.join(str(i) for i in sorted(set(ids) - found))}.", 404)
        cursor.executemany(
            "INSERT INTO project_books (project_id, book_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(project_id, book) for book in ids],
        )
        _touch_project(cursor, project_id)
    return True


def remove_project_book(connection, owner_id: int, project_id: int, book_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM project_books USING projects p WHERE project_books.project_id = p.id AND p.id = %s "
            "AND p.owner_id = %s AND project_books.book_id = %s",
            (project_id, owner_id, book_id),
        )
        removed = cursor.rowcount > 0
        if removed:
            _touch_project(cursor, project_id)
        return removed


def attach_evidence(connection, owner_id: int, project_id: int, evidence_id: int) -> bool:
    """False when the project or the item is not the owner's."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO project_evidence (project_id, evidence_id) "
            "SELECT p.id, e.id FROM projects p, evidence_items e "
            "WHERE p.id = %s AND p.owner_id = %s AND e.id = %s AND e.owner_id = %s ON CONFLICT DO NOTHING",
            (project_id, owner_id, evidence_id, owner_id),
        )
        attached = cursor.rowcount > 0
        if not attached:
            cursor.execute(
                "SELECT 1 FROM project_evidence pe JOIN projects p ON p.id = pe.project_id "
                "WHERE pe.project_id = %s AND pe.evidence_id = %s AND p.owner_id = %s",
                (project_id, evidence_id, owner_id),
            )
            attached = cursor.fetchone() is not None
        if attached:
            _touch_project(cursor, project_id)
    return attached


def detach_evidence(connection, owner_id: int, project_id: int, evidence_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM project_evidence USING projects p WHERE project_evidence.project_id = p.id AND p.id = %s "
            "AND p.owner_id = %s AND project_evidence.evidence_id = %s",
            (project_id, owner_id, evidence_id),
        )
        return cursor.rowcount > 0

"""Saved searches and in-app notifications: "tell me when a new book answers this".

A saved search remembers which books answered it last time (`seen`). When the library changes (`library_fingerprint`
differs), it is run again and a notification is made for every book that now answers it and did not before. The first run
only records a baseline. Notifications are in-app only (no email). The search itself is run by the caller, through the
API's search queue, so this module needs no models. Functions take an open psycopg2 connection and the owner's id.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

MAX_SAVED = 20
CHECKS_PER_CALL = 4  # searches re-run per check, so one dashboard visit stays cheap
MAX_ALERTS_PER_SEARCH = 3
KEEP_NOTIFICATIONS = 100


class AlertError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- pure ---------------------------------------------------------------------------------------------------------------


def answers_from(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The books (and best page in each) that answer a search, from `search_and_extract`'s result items."""
    seen: Dict[Any, Dict[str, Any]] = {}
    for item in results:
        source = item.get("source", {})
        book_id = source.get("book_id")
        if book_id is None or book_id in seen:
            continue
        seen[book_id] = {"book_id": book_id, "book_title": source.get("book_title") or "", "page_number": source.get("page_number")}
    return list(seen.values())


def new_sources(previous: Sequence[Dict[str, Any]], current: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Books answering now that did not before. A page moving inside a book already known is not news."""
    known = {entry["book_id"] for entry in previous}
    return [entry for entry in current if entry["book_id"] not in known]


# --- library fingerprint --------------------------------------------------------------------------------------------------


def library_fingerprint(connection) -> str:
    """Changes whenever a book is added, removed or re-ingested (a re-ingest creates a new row)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM books")
        count, newest = cursor.fetchone()
    return f"{count}:{newest}"


# --- saved searches --------------------------------------------------------------------------------------------------------

SEARCH_COLUMNS = "id, query, project_id, last_checked_at, created_at"


def _search(row) -> Dict[str, Any]:
    return {"id": row[0], "query": row[1], "project_id": row[2],
            "last_checked_at": row[3].isoformat() if row[3] else None, "created_at": row[4].isoformat() if row[4] else None}


def save_search(connection, owner_id: int, query: str, project_id: Optional[int] = None) -> Dict[str, Any]:
    query = " ".join((query or "").split())[:300]
    if not query:
        raise AlertError("A saved search needs its text.")
    if project_id is not None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
            if not cursor.fetchone():
                raise AlertError("Unknown project.", 404)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {SEARCH_COLUMNS} FROM saved_searches WHERE owner_id = %s AND LOWER(query) = LOWER(%s) "
            "AND project_id IS NOT DISTINCT FROM %s", (owner_id, query, project_id),
        )
        existing = cursor.fetchone()
        if existing:
            return _search(existing)
        cursor.execute("SELECT COUNT(*) FROM saved_searches WHERE owner_id = %s", (owner_id,))
        if cursor.fetchone()[0] >= MAX_SAVED:
            raise AlertError(f"You can keep at most {MAX_SAVED} saved searches.", 409)
        cursor.execute(
            f"INSERT INTO saved_searches (owner_id, query, project_id) VALUES (%s, %s, %s) RETURNING {SEARCH_COLUMNS}",
            (owner_id, query, project_id),
        )
        return _search(cursor.fetchone())


def list_searches(connection, owner_id: int) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {SEARCH_COLUMNS} FROM saved_searches WHERE owner_id = %s ORDER BY created_at DESC, id DESC", (owner_id,))
        return [_search(row) for row in cursor.fetchall()]


def delete_search(connection, owner_id: int, search_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM saved_searches WHERE id = %s AND owner_id = %s", (search_id, owner_id))
        return cursor.rowcount > 0


# --- checking --------------------------------------------------------------------------------------------------------------


def due_searches(connection, owner_id: int, fingerprint: str, limit: int = CHECKS_PER_CALL) -> List[Dict[str, Any]]:
    """Saved searches not yet run against the current library: {id, query, book_ids (the project's books or None)}."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, query, project_id FROM saved_searches WHERE owner_id = %s AND fingerprint IS DISTINCT FROM %s "
            "ORDER BY id LIMIT %s", (owner_id, fingerprint, limit),
        )
        rows = cursor.fetchall()
        found = []
        for search_id, query, project_id in rows:
            book_ids = None
            if project_id is not None:
                cursor.execute("SELECT book_id FROM project_books WHERE project_id = %s ORDER BY book_id", (project_id,))
                book_ids = [row[0] for row in cursor.fetchall()]
            found.append({"id": search_id, "query": query, "book_ids": book_ids})
    return found


def record_result(connection, owner_id: int, search_id: int, fingerprint: str, results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Store what answers a search now; returns the notifications this made (none for the first, baseline run)."""
    current = answers_from(results)
    with connection.cursor() as cursor:
        cursor.execute("SELECT query, seen FROM saved_searches WHERE id = %s AND owner_id = %s FOR UPDATE", (search_id, owner_id))
        row = cursor.fetchone()
        if row is None:
            return []
        query, previous = row
        made = []
        if previous is not None:
            for entry in new_sources(previous, current)[:MAX_ALERTS_PER_SEARCH]:
                page = f", p. {entry['page_number']}" if entry.get("page_number") else ""
                message = f"New answer to “{query}”: {entry['book_title']}{page}"
                cursor.execute(
                    "INSERT INTO notifications (owner_id, saved_search_id, kind, message, data) VALUES (%s, %s, 'new_answer', %s, %s) "
                    "RETURNING id", (owner_id, search_id, message, json.dumps({"query": query, **entry})),
                )
                made.append({"id": cursor.fetchone()[0], "message": message})
        cursor.execute(
            "UPDATE saved_searches SET seen = %s, fingerprint = %s, last_checked_at = NOW() WHERE id = %s AND owner_id = %s",
            (json.dumps(current), fingerprint, search_id, owner_id),
        )
        cursor.execute(
            "DELETE FROM notifications WHERE owner_id = %s AND id NOT IN "
            "(SELECT id FROM notifications WHERE owner_id = %s ORDER BY created_at DESC, id DESC LIMIT %s)",
            (owner_id, owner_id, KEEP_NOTIFICATIONS),
        )
    return made


def mark_searched_without_results(connection, owner_id: int, search_id: int, fingerprint: str) -> None:
    record_result(connection, owner_id, search_id, fingerprint, [])


# --- notifications -----------------------------------------------------------------------------------------------------------


def list_notifications(connection, owner_id: int, unread_only: bool = False, limit: int = 30) -> List[Dict[str, Any]]:
    where = "owner_id = %s" + (" AND NOT read" if unread_only else "")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, kind, message, data, read, created_at FROM notifications WHERE {where} ORDER BY created_at DESC, id DESC LIMIT %s",
            (owner_id, limit),
        )
        return [{"id": r[0], "kind": r[1], "message": r[2], "data": r[3], "read": r[4], "created_at": r[5].isoformat()} for r in cursor.fetchall()]


def mark_read(connection, owner_id: int, notification_id: Optional[int] = None) -> int:
    """Marks one notification (or all of the owner's) as read; returns how many changed."""
    with connection.cursor() as cursor:
        if notification_id is None:
            cursor.execute("UPDATE notifications SET read = TRUE WHERE owner_id = %s AND NOT read", (owner_id,))
        else:
            cursor.execute("UPDATE notifications SET read = TRUE WHERE id = %s AND owner_id = %s AND NOT read", (notification_id, owner_id))
        return cursor.rowcount

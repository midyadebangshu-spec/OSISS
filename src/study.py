"""Flashcards: cards made from saved evidence, the due queue, reviews (SM-2, see srs.py) and study statistics.

The back of a card is always the saved item's quote with its source, never text written by the tool. The front is made
automatically only for definitions that carry their term ("What is meant by “stack”?"); otherwise the scholar writes it.
Functions take an open psycopg2 connection and the owner's id; nothing commits.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import srs

MAX_FRONT = 500
MAX_CARDS = 5000
CARD_KINDS = ("passage", "definition", "formula", "highlight")  # items with a quote to put on the back


class StudyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def auto_front(item: Dict[str, Any]) -> Optional[str]:
    """“What is meant by X?” for a definition that carries its term; None when the scholar has to write the front."""
    term = " ".join(str((item.get("meta") or {}).get("term") or "").split())
    if item.get("kind") == "definition" and term:
        return f"What is meant by “{term}”?"
    return None


def clean_front(front: Optional[str]) -> str:
    return " ".join((front or "").split())[:MAX_FRONT]


CARD_SQL = (
    "SELECT c.id, c.front, c.project_id, c.ease, c.interval_days, c.reps, c.lapses, c.due, c.suspended, c.last_reviewed, "
    "e.id, e.kind, e.book_id, e.book_title, e.book_author, e.page_number, e.quote FROM cards c "
    "JOIN evidence_items e ON e.id = c.evidence_id"
)


def _card(row) -> Dict[str, Any]:
    state = srs.State(ease=row[3], interval=row[4], reps=row[5], lapses=row[6])
    return {
        "id": row[0], "front": row[1], "project_id": row[2],
        "ease": round(row[3], 2), "interval_days": row[4], "reps": row[5], "lapses": row[6],
        "due": row[7].isoformat(), "suspended": row[8], "last_reviewed": row[9].isoformat() if row[9] else None,
        "is_new": row[5] == 0 and row[4] == 0 and row[9] is None,
        "next_intervals": {srs.GRADE_NAMES[g]: days for g, days in srs.preview(state).items()},
        "evidence": {"id": row[10], "kind": row[11], "book_id": row[12], "book_title": row[13], "book_author": row[14],
                     "page_number": row[15], "quote": row[16]},
    }


def _with_printed_pages(connection, cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    import workspace  # pylint: disable=import-outside-toplevel

    workspace.add_printed_pages(connection, [card["evidence"] for card in cards])
    return cards


def _own_project(connection, owner_id: int, project_id: Optional[int]) -> Optional[int]:
    if project_id is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
        if not cursor.fetchone():
            raise StudyError("Unknown project.", 404)
    return project_id


def get_card(connection, owner_id: int, card_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"{CARD_SQL} WHERE c.id = %s AND c.owner_id = %s", (card_id, owner_id))
        row = cursor.fetchone()
    return _with_printed_pages(connection, [_card(row)])[0] if row else None


def create_card(
    connection, owner_id: int, evidence_id: int, front: Optional[str] = None, project_id: Optional[int] = None
) -> Dict[str, Any]:
    project_id = _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT kind, quote, meta FROM evidence_items WHERE id = %s AND owner_id = %s", (evidence_id, owner_id))
        row = cursor.fetchone()
        if row is None:
            raise StudyError("Unknown evidence item.", 404)
        kind, quote, meta = row
        if kind not in CARD_KINDS or not (quote or "").strip():
            raise StudyError("Only saved passages, definitions, formulas and highlights can become cards.")
        text = clean_front(front) or auto_front({"kind": kind, "meta": meta}) or ""
        if not text:
            raise StudyError("Write the question for the front of this card.")
        cursor.execute("SELECT COUNT(*) FROM cards WHERE owner_id = %s", (owner_id,))
        if cursor.fetchone()[0] >= MAX_CARDS:
            raise StudyError(f"You can have at most {MAX_CARDS} cards.", 409)
        cursor.execute(
            "INSERT INTO cards (owner_id, evidence_id, project_id, front) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (owner_id, evidence_id, front) DO UPDATE SET project_id = COALESCE(EXCLUDED.project_id, cards.project_id) "
            "RETURNING id",
            (owner_id, evidence_id, project_id, text),
        )
        card_id = cursor.fetchone()[0]
    return get_card(connection, owner_id, card_id)  # type: ignore[return-value]


def create_cards_bulk(
    connection, owner_id: int, collection_id: Optional[int] = None, project_id: Optional[int] = None
) -> Dict[str, int]:
    """Cards with automatic fronts for the definitions in a collection or project. Items that need a hand-written front
    are counted as `skipped`; cards that already exist as `existing`."""
    if (collection_id is None) == (project_id is None):
        raise StudyError("Give a collection or a project.")
    with connection.cursor() as cursor:
        if collection_id is not None:
            cursor.execute("SELECT 1 FROM collections WHERE id = %s AND owner_id = %s", (collection_id, owner_id))
            join, key = "JOIN collection_items x ON x.evidence_id = e.id AND x.collection_id = %s", collection_id
        else:
            cursor.execute("SELECT 1 FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
            join, key = "JOIN project_evidence x ON x.evidence_id = e.id AND x.project_id = %s", project_id
        if not cursor.fetchone():
            raise StudyError("Unknown collection." if collection_id is not None else "Unknown project.", 404)
        cursor.execute(f"SELECT e.id, e.kind, e.quote, e.meta FROM evidence_items e {join} WHERE e.owner_id = %s ORDER BY e.id", (key, owner_id))
        items = cursor.fetchall()
    created = existing = skipped = 0
    for evidence_id, kind, quote, meta in items:
        front = auto_front({"kind": kind, "meta": meta}) if kind in CARD_KINDS and (quote or "").strip() else None
        if not front:
            skipped += 1
            continue
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM cards WHERE owner_id = %s AND evidence_id = %s AND front = %s", (owner_id, evidence_id, front))
            if cursor.fetchone():
                existing += 1
                continue
        create_card(connection, owner_id, evidence_id, front, project_id)
        created += 1
    return {"created": created, "existing": existing, "skipped": skipped}


def list_cards(connection, owner_id: int, project_id: Optional[int] = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    where, params = "c.owner_id = %s", [owner_id]
    if project_id is not None:
        where += " AND c.project_id = %s"
        params.append(project_id)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM cards c WHERE {where}", params)
        total = cursor.fetchone()[0]
        cursor.execute(f"{CARD_SQL} WHERE {where} ORDER BY c.id DESC LIMIT %s OFFSET %s", params + [limit, offset])
        cards = [_card(row) for row in cursor.fetchall()]
    return {"total": total, "cards": _with_printed_pages(connection, cards)}


def update_card(
    connection, owner_id: int, card_id: int, front: Optional[str] = None, suspended: Optional[bool] = None,
    project_id: Optional[int] = None, set_project: bool = False,
) -> Optional[Dict[str, Any]]:
    current = get_card(connection, owner_id, card_id)
    if current is None:
        return None
    new_front = clean_front(front) if front is not None else current["front"]
    if not new_front:
        raise StudyError("A card needs a front.")
    new_project = _own_project(connection, owner_id, project_id) if set_project else current["project_id"]
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE cards SET front = %s, suspended = %s, project_id = %s WHERE id = %s AND owner_id = %s",
                (new_front, current["suspended"] if suspended is None else suspended, new_project, card_id, owner_id),
            )
    except Exception as exc:  # unique (owner, evidence, front) clash
        if "unique" in str(exc).lower():
            raise StudyError("You already have a card with that front for this passage.", 409) from exc
        raise
    return get_card(connection, owner_id, card_id)


def delete_card(connection, owner_id: int, card_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM cards WHERE id = %s AND owner_id = %s", (card_id, owner_id))
        return cursor.rowcount > 0


# --- reviewing -----------------------------------------------------------------------------------------------------------


def today() -> dt.date:
    return dt.date.today()


def due_cards(connection, owner_id: int, project_id: Optional[int] = None, limit: int = 20, on: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    """Cards to review now: overdue ones first (oldest due date), then new cards."""
    where, params = "c.owner_id = %s AND NOT c.suspended AND c.due <= %s", [owner_id, on or today()]
    if project_id is not None:
        where += " AND c.project_id = %s"
        params.append(project_id)
    with connection.cursor() as cursor:
        cursor.execute(f"{CARD_SQL} WHERE {where} ORDER BY c.due, c.id LIMIT %s", params + [limit])
        return _with_printed_pages(connection, [_card(row) for row in cursor.fetchall()])


def review_card(connection, owner_id: int, card_id: int, grade: int, on: Optional[dt.date] = None) -> Optional[Dict[str, Any]]:
    """Apply a review: the next state comes from SM-2, the due date from today, and the review is logged."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ease, interval_days, reps, lapses FROM cards WHERE id = %s AND owner_id = %s FOR UPDATE", (card_id, owner_id)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        before = srs.State(ease=row[0], interval=row[1], reps=row[2], lapses=row[3])
        try:
            after = srs.review(before, grade)
        except ValueError as exc:
            raise StudyError(str(exc)) from exc
        due = srs.due_date(on or today(), after)
        cursor.execute(
            "UPDATE cards SET ease = %s, interval_days = %s, reps = %s, lapses = %s, due = %s, last_reviewed = NOW() "
            "WHERE id = %s AND owner_id = %s",
            (after.ease, after.interval, after.reps, after.lapses, due, card_id, owner_id),
        )
        cursor.execute(
            "INSERT INTO review_log (card_id, owner_id, grade, interval_before, interval_after) VALUES (%s, %s, %s, %s, %s)",
            (card_id, owner_id, grade, before.interval, after.interval),
        )
    return get_card(connection, owner_id, card_id)


def stats(connection, owner_id: int, project_id: Optional[int] = None, on: Optional[dt.date] = None) -> Dict[str, Any]:
    scope, params = "owner_id = %s", [owner_id]
    if project_id is not None:
        scope += " AND project_id = %s"
        params.append(project_id)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT suspended AND due <= %s), "
            f"COUNT(*) FILTER (WHERE reps = 0 AND interval_days = 0 AND last_reviewed IS NULL), "
            f"COUNT(*) FILTER (WHERE interval_days >= 21) FROM cards WHERE {scope}",
            [on or today()] + params,
        )
        total, due, new, mature = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) FILTER (WHERE reviewed_at >= date_trunc('day', NOW())), "
            "COUNT(*) FILTER (WHERE reviewed_at >= NOW() - INTERVAL '30 days'), "
            "COUNT(*) FILTER (WHERE reviewed_at >= NOW() - INTERVAL '30 days' AND grade >= 2) "
            "FROM review_log WHERE owner_id = %s", (owner_id,),
        )
        today_count, month, month_passed = cursor.fetchone()
    return {
        "total": total, "due": due, "new": new, "mature": mature, "reviewed_today": today_count,
        "reviews_30_days": month, "retention_30_days": round(month_passed / month, 2) if month else None,
    }

"""The website's library: every book with its cover, details, contents, figures and key concepts.

Covers and page images come from the page-asset cache (page_assets); the table of contents is the PDF's own outline;
counts of figures / tables come from the cached page JSON. Facts read from files are cached per content hash, so they
are computed once per book version.
"""

from __future__ import annotations

import glob
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import fitz

import admin
import page_assets
from config import settings

MAX_TOC_ENTRIES = 400
# Facts are re-read after this long, so page assets built later (or by another copy) show up.
FACTS_TTL_SECONDS = 600

_file_facts: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_facts_lock = threading.Lock()


def _read_pdf_facts(file_path: str) -> Dict[str, Any]:
    from storage import get_storage  # pylint: disable=import-outside-toplevel

    with get_storage().local_copy(file_path) as local_file, page_assets._RENDER_LOCK:  # pylint: disable=protected-access
        with fitz.open(local_file) as pdf:
            toc = [
                {"level": level, "title": " ".join(title.split())[:200], "page": page}
                for level, title, page in pdf.get_toc(simple=True)[:MAX_TOC_ENTRIES]
                if title.strip() and page and page > 0
            ]
            return {"page_count": pdf.page_count, "toc": toc}


def _read_asset_facts(file_hash: str) -> Dict[str, Any]:
    """Figure / table counts and captioned figures from the page-asset cache."""
    figures, figure_count, table_count, built_pages = [], 0, 0, 0
    for path in glob.glob(os.path.join(page_assets.book_dir(file_hash), "p*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                page = json.load(handle)
        except (OSError, ValueError):
            continue
        built_pages += 1
        figure_count += len(page.get("figures", []))
        table_count += len(page.get("tables", []))
        for figure in page.get("figures", []):
            if figure.get("caption"):
                figures.append({**figure, "page_number": page["page"]})
    figures.sort(key=lambda figure: figure["page_number"])
    return {"figure_count": figure_count, "table_count": table_count, "built_pages": built_pages, "figures": figures}


def book_facts(file_path: str, file_hash: Optional[str]) -> Dict[str, Any]:
    """Page count, outline and asset counts for a book, cached by content hash (a changed file gets a new hash)."""
    key = file_hash or file_path
    with _facts_lock:
        cached = _file_facts.get(key)
    if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_SECONDS:
        return cached[1]
    facts: Dict[str, Any] = {"page_count": None, "toc": [], "figure_count": 0, "table_count": 0, "figures": [], "built_pages": 0}
    try:
        facts.update(_read_pdf_facts(file_path))
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Library: could not read '{file_path}': {exc}")
    if file_hash:
        facts.update(_read_asset_facts(file_hash))
    with _facts_lock:
        _file_facts[key] = (time.monotonic(), facts)
    return facts


def clear_cache() -> None:
    with _facts_lock:
        _file_facts.clear()


# A blank page compresses to 1-2 KB of WebP; sparse title pages are 7 KB and up.
BLANK_PAGE_BYTES = 4096


def _cover(file_hash: Optional[str]) -> Optional[str]:
    """The first page that is not blank (scans often open with empty pages), among the first eight."""
    if not file_hash:
        return None
    first = None
    for number in range(1, 9):
        path = os.path.join(page_assets.book_dir(file_hash), f"p{number}.webp")
        if not os.path.isfile(path):
            continue
        first = first or number
        if os.path.getsize(path) >= BLANK_PAGE_BYTES:
            return page_assets.asset_url(file_hash, f"p{number}.webp")
    return page_assets.asset_url(file_hash, f"p{first}.webp") if first else None


BOOK_SQL = """
SELECT b.id, b.title, b.author, b.department, b.publication_year, b.language_code, b.file_path, b.file_hash,
       b.created_at, COUNT(c.id) AS chunk_count,
       (SELECT COUNT(DISTINCT m.concept_id) FROM concept_mentions m WHERE m.book_id = b.id) AS concept_count
FROM books b LEFT JOIN chunks c ON c.book_id = b.id
"""


def _row_to_book(row) -> Dict[str, Any]:
    book_id, title, author, department, year, language, file_path, file_hash, created_at, chunks, concepts = row
    facts = book_facts(file_path, file_hash)
    return {
        "id": book_id,
        "title": title,
        "author": author,
        "department": department,
        "publication_year": year,
        "language": language,
        "pdf_link": admin.pdf_link(file_path or "") or "#",
        "cover": _cover(file_hash),
        "page_count": facts["page_count"],
        "chunk_count": chunks,
        "concept_count": concepts,
        "figure_count": facts["figure_count"],
        "table_count": facts["table_count"],
        "has_contents": bool(facts["toc"]),
        "added": created_at.isoformat() if created_at else None,
    }


def list_books(connection) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(BOOK_SQL + " GROUP BY b.id ORDER BY lower(b.title), b.id")
        rows = cursor.fetchall()
    return [_row_to_book(row) for row in rows]


def get_book(connection, book_id: int, figure_limit: int = 24, concept_limit: int = 30) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(BOOK_SQL + " WHERE b.id = %s GROUP BY b.id", (book_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        # This book's best concepts: defined here first, then most mentioned; ones other books share rank higher.
        cursor.execute(
            "SELECT c.id, c.label, c.book_count, COUNT(*) AS mentions, SUM(CASE WHEN m.is_definition THEN 1 ELSE 0 END) "
            "FROM concept_mentions m JOIN concepts c ON c.id = m.concept_id WHERE m.book_id = %s "
            "GROUP BY c.id, c.label, c.book_count "
            "ORDER BY SUM(CASE WHEN m.is_definition THEN 1 ELSE 0 END) * 3 + COUNT(*) + LEAST(c.book_count, 5) DESC, c.label "
            "LIMIT %s",
            (book_id, concept_limit),
        )
        concepts = [
            {"id": cid, "label": label, "book_count": books, "mentions": mentions, "definitions": int(defs or 0)}
            for cid, label, books, mentions, defs in cursor.fetchall()
        ]
    book = _row_to_book(row)
    file_path, file_hash = row[6], row[7]
    facts = book_facts(file_path, file_hash)
    book["toc"] = facts["toc"]
    book["concepts"] = concepts
    book["figures"] = [
        {
            "image": page_assets.asset_url(file_hash, figure["image"]),
            "caption": figure.get("caption"),
            "label": figure.get("label"),
            "width": figure.get("width"),
            "height": figure.get("height"),
            "page_number": figure["page_number"],
            "primary": True,
        }
        for figure in facts["figures"][:figure_limit]
    ] if file_hash else []
    return book


def book_pages(connection, book_id: int, start: int, count: int) -> Optional[Dict[str, Any]]:
    """Page images start..start+count-1 (built on demand when missing)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path, file_hash FROM books WHERE id = %s", (book_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    file_path, file_hash = row
    total = book_facts(file_path, file_hash)["page_count"] or 0
    numbers = list(range(max(1, start), min(total, start + count - 1) + 1))
    pages = []
    if file_hash and numbers:
        missing = [n for n in numbers if not os.path.isfile(os.path.join(page_assets.book_dir(file_hash), f"p{n}.webp"))]
        if missing and settings.page_assets:
            try:
                page_assets.ensure_pages(file_path, file_hash, missing)
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[OSISS] Library: page images unavailable for book {book_id}: {exc}")
        for number in numbers:
            exists = os.path.isfile(os.path.join(page_assets.book_dir(file_hash), f"p{number}.webp"))
            pages.append({"page": number, "image": page_assets.asset_url(file_hash, f"p{number}.webp") if exists else None})
    return {"book_id": book_id, "page_count": total, "start": start, "pages": pages}

"""Writing studio: drafts with citation tokens, rendering with a bibliography, and the quote-integrity check.

A draft body is Markdown-like text. Evidence is cited with a token `[@e12]` (`e` + the saved item's id):

    > A stack is a LIFO structure. [@e12]            a block quotation (consecutive lines starting with ">")
    “A stack is a LIFO structure” [@e12]             an inline quotation followed by its citation
    Stacks are discussed in detail [@e12].           a plain citation (a paraphrase)
    # Heading, ## Subheading                         headings;  *text* is italic

Parsing, rendering and checking are pure functions over plain dicts; only `load_context` / `page_text` touch the
database and the page assets, and the DB-facing CRUD takes an open psycopg2 connection (nothing commits).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import citations
import quotes

MAX_BODY = 200_000
CITE_RE = re.compile(r"\[@e(\d+)\]")
INLINE_CURLY = re.compile(r"“([^”]{3,})”(\s*)(\[@e\d+\])?")
INLINE_STRAIGHT = re.compile(r'"([^"\n]{3,})"(\s*)(\[@e\d+\])?')
MIN_UNCITED_WORDS = 8  # shorter quoted phrases are taken as scare quotes or terms, not quotations


class DraftError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- parsing --------------------------------------------------------------------------------------------------------


def parse_blocks(body: str) -> List[Dict[str, Any]]:
    """Blocks of the draft in order: {"type": "heading"|"paragraph"|"quote", "text", "level"?, "line"} (1-based line)."""
    blocks: List[Dict[str, Any]] = []
    current: List[Tuple[int, str]] = []

    def flush() -> None:
        if not current:
            return
        start = current[0][0]
        lines = [text for _, text in current]
        if all(line.lstrip().startswith(">") for line in lines):
            text = " ".join(line.lstrip()[1:].strip() for line in lines).strip()
            blocks.append({"type": "quote", "text": text, "line": start})
        else:
            blocks.append({"type": "paragraph", "text": " ".join(line.strip() for line in lines), "line": start})
        current.clear()

    for number, line in enumerate((body or "").splitlines(), start=1):
        heading = re.match(r"^(#{1,3})\s+(.*\S)\s*$", line)
        if heading:
            flush()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2), "line": number})
        elif not line.strip():
            flush()
        else:
            current.append((number, line))
    flush()
    return blocks


def cited_ids(body: str) -> List[int]:
    """Evidence ids cited in the draft, in order of first use."""
    return list(dict.fromkeys(int(match) for match in CITE_RE.findall(body or "")))


def find_quotations(body: str) -> List[Dict[str, Any]]:
    """Every quotation: {"kind": "block"|"inline", "text", "evidence_id" or None, "line"}.

    A block quotation is cited by a token anywhere in the block (the last one counts); an inline quotation by the
    token right after its closing quote mark. Short quoted phrases without a citation are not reported."""
    found: List[Dict[str, Any]] = []
    for block in parse_blocks(body):
        if block["type"] == "quote":
            tokens = CITE_RE.findall(block["text"])
            found.append({
                "kind": "block", "text": CITE_RE.sub("", block["text"]).strip(),
                "evidence_id": int(tokens[-1]) if tokens else None, "line": block["line"],
            })
        elif block["type"] == "paragraph":
            taken: List[Tuple[int, int]] = []
            for pattern in (INLINE_CURLY, INLINE_STRAIGHT):
                for match in pattern.finditer(block["text"]):
                    if any(match.start() < end and start < match.end() for start, end in taken):
                        continue
                    token = match.group(3)
                    text = match.group(1).strip()
                    if token is None and len(text.split()) < MIN_UNCITED_WORDS:
                        continue
                    taken.append((match.start(), match.end()))
                    found.append({
                        "kind": "inline", "text": text, "evidence_id": int(CITE_RE.fullmatch(token).group(1)) if token else None,
                        "line": block["line"],
                    })
    return found


# --- context: evidence and books for the cited ids ---------------------------------------------------------------------

BOOK_FIELDS = "id, title, author, publication_year, publisher, edition, isbn, page_offset, page_labels"


def book_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0], "title": row[1], "author": row[2], "publication_year": row[3], "publisher": row[4],
        "edition": row[5], "isbn": row[6], "page_offset": row[7], "has_page_labels": bool(row[8]),
    }


def load_context(connection, owner_id: int, ids: Sequence[int]) -> Tuple[Dict[int, Dict[str, Any]], Dict[Any, Dict[str, Any]]]:
    """(evidence by id, books by key) for the cited evidence ids. Only the owner's items are found. A book removed from
    the library is cited from the title and author saved with the item."""
    import workspace  # pylint: disable=import-outside-toplevel

    evidence: Dict[int, Dict[str, Any]] = {}
    if ids:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {workspace.EVIDENCE_COLUMNS} FROM evidence_items e WHERE e.owner_id = %s AND e.id = ANY(%s)",
                (owner_id, list(ids)),
            )
            rows = cursor.fetchall()
        evidence = {row[0]: workspace._evidence(row) for row in rows}  # pylint: disable=protected-access
        workspace.add_printed_pages(connection, list(evidence.values()))
    book_ids = sorted({item["book_id"] for item in evidence.values() if item["book_id"]})
    books: Dict[Any, Dict[str, Any]] = {}
    if book_ids:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {BOOK_FIELDS} FROM books WHERE id = ANY(%s)", (book_ids,))
            books = {row[0]: book_dict(row) for row in cursor.fetchall()}
    for item in evidence.values():
        if item["book_id"] not in books and item.get("book_title"):
            key = ("saved", item["book_title"], item.get("book_author"))
            books.setdefault(key, {"title": item["book_title"], "author": item.get("book_author")})
            item["_book_key"] = key
        else:
            item["_book_key"] = item["book_id"]
    return evidence, books


# --- rendering ------------------------------------------------------------------------------------------------------


def render(body: str, style: str, evidence: Dict[int, Dict[str, Any]], books: Dict[Any, Dict[str, Any]]) -> Dict[str, Any]:
    """The draft with citations resolved: blocks (`*italic*` markers kept), the bibliography, and what is missing."""
    if style not in citations.STYLES:
        raise DraftError(f"style must be one of {', '.join(citations.STYLES)}")
    citer = citations.Citer(style, books)
    missing: List[int] = []
    used: List[Any] = []

    def resolve(match: "re.Match[str]") -> str:
        evidence_id = int(match.group(1))
        item = evidence.get(evidence_id)
        book_key = item.get("_book_key") if item else None
        if item is None or book_key not in books:
            if evidence_id not in missing:
                missing.append(evidence_id)
            return "[source missing]"
        used.append(book_key)
        return citer.cite(book_key, item.get("printed_page"), item.get("page_number"))

    blocks = []
    for block in parse_blocks(body):
        text = CITE_RE.sub(resolve, block["text"])
        blocks.append({**block, "text": text})
    bibliography = [{"key": str(key), "text": text} for key, text in citer.bibliography(used)]
    notes = [
        {"book": books[key].get("title"), "messages": citations.warnings(books[key])}
        for key in dict.fromkeys(used) if citations.warnings(books[key])
    ]
    cited_books = [{**books[key], "id": books[key].get("id", index)} for index, key in enumerate(dict.fromkeys(used), start=1)]
    return {
        "style": style, "blocks": blocks, "bibliography": bibliography, "warnings": notes, "missing": missing,
        "books": cited_books,
    }


# --- quote integrity ----------------------------------------------------------------------------------------------------

PageText = Callable[[int, int], Optional[str]]  # (book_id, pdf page) -> the page's text, or None when unavailable


def check(body: str, evidence: Dict[int, Dict[str, Any]], page_text: PageText) -> Dict[str, Any]:
    """Compare every quotation with the page it cites.

    Result per quotation: status `verbatim` (on the cited page), `other_page` (found on a neighbouring page: `page`),
    `partial` / `not_found` (with what the page says), `unknown_source` (the cited item does not exist),
    `uncited` (a quotation with no citation) or `unchecked` (no page text available; compared with the saved quote only).
    """
    results = []
    for quotation in find_quotations(body):
        base = {"kind": quotation["kind"], "text": quotation["text"], "line": quotation["line"],
                "evidence_id": quotation["evidence_id"]}
        evidence_id = quotation["evidence_id"]
        if evidence_id is None:
            results.append({**base, "status": "uncited"})
            continue
        item = evidence.get(evidence_id)
        if item is None:
            results.append({**base, "status": "unknown_source"})
            continue
        base["book"] = item.get("book_title")
        base["page"] = item.get("page_number")
        base["printed_page"] = item.get("printed_page")
        book_id, page = item.get("book_id"), item.get("page_number")
        text = page_text(book_id, page) if book_id and page else None
        if text:
            outcome = quotes.verify(quotation["text"], text)
            if outcome["status"] != "verbatim":
                for neighbour in (page + 1, page - 1):
                    other = page_text(book_id, neighbour) if neighbour >= 1 else None
                    if other and quotes.verify(quotation["text"], other)["status"] == "verbatim":
                        results.append({**base, "status": "other_page", "found_on_page": neighbour, "matched": 1.0})
                        outcome = None
                        break
            if outcome is not None:
                results.append({**base, "status": outcome["status"], "matched": outcome["matched"], "closest": outcome["closest"]})
            continue
        saved = item.get("quote") or ""
        outcome = quotes.verify(quotation["text"], saved) if saved else {"status": "not_found", "matched": 0.0, "closest": None}
        results.append({**base, "status": "unchecked", "matches_saved_quote": outcome["status"] == "verbatim"})
    counts: Dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    problems = sum(counts.get(status, 0) for status in ("partial", "not_found", "other_page", "unknown_source", "uncited"))
    return {"quotes": results, "counts": counts, "problems": problems}


def page_text(connection, book_id: int, page: int) -> Optional[str]:
    """The text of one PDF page: the page-asset word boxes (OCR for scans), else the stored chunks of that page."""
    import page_assets  # pylint: disable=import-outside-toplevel
    from config import settings  # pylint: disable=import-outside-toplevel

    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path, file_hash, language_code FROM books WHERE id = %s", (book_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    file_path, file_hash, language = row
    if file_hash and settings.page_assets:
        try:
            data = page_assets.ensure_pages(file_path, file_hash, [page]).get(page)
            if data is not None and (not data.get("words") or not data.get("text_usable", True)) and settings.ocr_highlight:
                data = page_assets.add_ocr_text(file_path, file_hash, page, language or "en") or data
            words = (data or {}).get("words") or []
            if words:
                return " ".join(word[4] for word in words)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[OSISS] Draft check: page text for book {book_id} page {page} unavailable from the PDF: {exc}")
    with connection.cursor() as cursor:
        cursor.execute("SELECT chunk_text FROM chunks WHERE book_id = %s AND page_number = %s ORDER BY chunk_index", (book_id, page))
        chunks = [row[0] for row in cursor.fetchall()]
    return " ".join(chunks) or None


# --- storage ------------------------------------------------------------------------------------------------------------

DRAFT_COLUMNS = "id, title, body, style, project_id, created_at, updated_at"


def _draft(row, with_body: bool = True) -> Dict[str, Any]:
    draft = {
        "id": row[0], "title": row[1], "style": row[3], "project_id": row[4],
        "created_at": row[5].isoformat() if row[5] else None, "updated_at": row[6].isoformat() if row[6] else None,
    }
    if with_body:
        draft["body"] = row[2]
    else:
        plain = " ".join(re.sub(r"^[#>\s]+", "", line) for line in CITE_RE.sub("", row[2]).splitlines())
        draft["excerpt"] = " ".join(plain.split())[:160]
    return draft


def _own_project(connection, owner_id: int, project_id: Optional[int]) -> Optional[int]:
    if project_id is None:
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM projects WHERE id = %s AND owner_id = %s", (project_id, owner_id))
        if not cursor.fetchone():
            raise DraftError("Unknown project.", 404)
    return project_id


def _clean(title: Optional[str], body: Optional[str], style: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if title is not None:
        title = " ".join(title.split())[:200]
    if body is not None and len(body) > MAX_BODY:
        raise DraftError(f"A draft can hold at most {MAX_BODY} characters.", 413)
    if style is not None and style not in citations.STYLES:
        raise DraftError(f"style must be one of {', '.join(citations.STYLES)}")
    return title, body, style


def create_draft(connection, owner_id: int, title: str = "", body: str = "", style: str = "apa", project_id: Optional[int] = None) -> Dict[str, Any]:
    title, body, style = _clean(title, body, style)
    project_id = _own_project(connection, owner_id, project_id)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO drafts (owner_id, title, body, style, project_id) VALUES (%s, %s, %s, %s, %s) RETURNING {DRAFT_COLUMNS}",
            (owner_id, title or "Untitled draft", body or "", style or "apa", project_id),
        )
        return _draft(cursor.fetchone())


def get_draft(connection, owner_id: int, draft_id: int) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {DRAFT_COLUMNS} FROM drafts WHERE id = %s AND owner_id = %s", (draft_id, owner_id))
        row = cursor.fetchone()
    return _draft(row) if row else None


def list_drafts(connection, owner_id: int, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    where, params = "owner_id = %s", [owner_id]
    if project_id is not None:
        where += " AND project_id = %s"
        params.append(project_id)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {DRAFT_COLUMNS} FROM drafts WHERE {where} ORDER BY updated_at DESC, id DESC", params)
        return [_draft(row, with_body=False) for row in cursor.fetchall()]


def update_draft(
    connection, owner_id: int, draft_id: int, title: Optional[str] = None, body: Optional[str] = None,
    style: Optional[str] = None, project_id: Optional[int] = None, set_project: bool = False,
) -> Optional[Dict[str, Any]]:
    current = get_draft(connection, owner_id, draft_id)
    if current is None:
        return None
    title, body, style = _clean(title, body, style)
    new_project = _own_project(connection, owner_id, project_id) if set_project else current["project_id"]
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE drafts SET title = %s, body = %s, style = %s, project_id = %s, updated_at = NOW() "
            "WHERE id = %s AND owner_id = %s",
            (
                (title or current["title"]) if title is not None else current["title"],
                body if body is not None else current["body"],
                style or current["style"], new_project, draft_id, owner_id,
            ),
        )
    return get_draft(connection, owner_id, draft_id)


def delete_draft(connection, owner_id: int, draft_id: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM drafts WHERE id = %s AND owner_id = %s", (draft_id, owner_id))
        return cursor.rowcount > 0


def render_draft(connection, owner_id: int, draft: Dict[str, Any], style: Optional[str] = None) -> Dict[str, Any]:
    evidence, books = load_context(connection, owner_id, cited_ids(draft["body"]))
    return {**render(draft["body"], style or draft["style"], evidence, books), "title": draft["title"]}


def check_draft(connection, owner_id: int, draft: Dict[str, Any]) -> Dict[str, Any]:
    ids = cited_ids(draft["body"])
    evidence, _ = load_context(connection, owner_id, ids)
    cache: Dict[Tuple[int, int], Optional[str]] = {}

    def lookup(book_id: int, page: int) -> Optional[str]:
        if (book_id, page) not in cache:
            cache[(book_id, page)] = page_text(connection, book_id, page)
        return cache[(book_id, page)]

    return check(draft["body"], evidence, lookup)


# --- references of a workspace ------------------------------------------------------------------------------------------


def reference_books(connection, owner_id: int, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Books the scholar works with: those they saved from, cited in drafts, or put in a project (only that project's
    when `project_id` is given). Each has `warnings` (what is missing for a citation)."""
    params: List[Any] = [owner_id]
    if project_id is None:
        scope = (
            "SELECT book_id FROM evidence_items WHERE owner_id = %s AND book_id IS NOT NULL "
            "UNION SELECT pb.book_id FROM project_books pb JOIN projects p ON p.id = pb.project_id WHERE p.owner_id = %s"
        )
        params.append(owner_id)
    else:
        scope = (
            "SELECT pb.book_id FROM project_books pb JOIN projects p ON p.id = pb.project_id "
            "WHERE p.owner_id = %s AND p.id = %s "
            "UNION SELECT e.book_id FROM project_evidence pe JOIN projects p ON p.id = pe.project_id "
            "JOIN evidence_items e ON e.id = pe.evidence_id WHERE p.owner_id = %s AND p.id = %s AND e.book_id IS NOT NULL"
        )
        params = [owner_id, project_id, owner_id, project_id]
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {BOOK_FIELDS} FROM books WHERE id IN ({scope}) ORDER BY LOWER(title), id", params)
        books = [book_dict(row) for row in cursor.fetchall()]
    for book in books:
        book["warnings"] = citations.warnings(book)
    return books


def reference_entries(books: Sequence[Dict[str, Any]], style: str) -> List[Dict[str, Any]]:
    """Formatted entries (plain and HTML) for a list of books, in the style's bibliography order."""
    if style not in citations.STYLES:
        raise DraftError(f"style must be one of {', '.join(citations.STYLES)}")
    by_id = {book["id"]: book for book in books}
    citer = citations.Citer(style, by_id)
    entries = []
    for key, text in citer.bibliography(by_id.keys()):
        entries.append({
            "book_id": key, "plain": citations.render(text), "marked": text, "html": citations.render(text, "html"),
            "warnings": by_id[key].get("warnings", []),
        })
    return entries


def bibtex_file(books: Sequence[Dict[str, Any]]) -> str:
    used: Dict[str, int] = {}
    out = []
    for index, book in enumerate(books, start=1):
        key = citations.bibtex_key(book, book.get("id", index))
        used[key] = used.get(key, 0) + 1
        out.append(citations.bibtex(book, key if used[key] == 1 else f"{key}{chr(96 + used[key])}"))
    return "\n\n".join(out) + ("\n" if out else "")


def ris_file(books: Sequence[Dict[str, Any]]) -> str:
    return "\n\n".join(citations.ris(book) for book in books) + ("\n" if books else "")


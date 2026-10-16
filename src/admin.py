"""Book, exclusion and API-key operations behind the admin API (no models needed).

Search reads title / author / department from the Elasticsearch documents, not from PostgreSQL, so every
metadata change is written to both stores. Deleting a book keeps its PDF on disk and records the path in
`excluded_files` so the polling ingester does not ingest it again; restoring removes that record.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import unicodedata
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from config import settings

METADATA_FIELDS = ("title", "author", "publication_year", "department")

BOOK_COLUMNS = """
    b.id, b.title, b.author, b.publication_year, b.department, b.file_path, b.language_code, b.file_hash,
    b.created_at, COUNT(c.id) AS chunk_count, COUNT(DISTINCT c.page_number) AS page_count,
    COUNT(c.id) FILTER (WHERE c.is_exercise) AS exercise_chunks
"""


class AdminError(Exception):
    """A request the admin API should reject; `status` is the HTTP status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def pdf_link(file_path: str) -> str:
    """URL path of a stored PDF as served by the API (`./data/pdfs/x.pdf` -> `/data/pdfs/x.pdf`)."""
    normalized = file_path[2:] if file_path.startswith("./") else file_path
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    base = settings.public_base_url.rstrip("/")
    return f"{base}{normalized}" if base and normalized.startswith("/") else normalized


def _book_row(row, description) -> Dict[str, Any]:
    book = dict(zip([column.name for column in description], row))
    book["created_at"] = book["created_at"].isoformat() if book.get("created_at") else None
    book["pdf_link"] = pdf_link(book["file_path"])
    book["file_exists"] = os.path.isfile(book["file_path"])
    book["title_guessed"] = looks_guessed(book)
    return book


def looks_guessed(book: Dict[str, Any]) -> bool:
    """True when title and author are still what the ingester guessed from the file name."""
    from utils import infer_metadata_from_filename  # pylint: disable=import-outside-toplevel

    guess = infer_metadata_from_filename(book.get("file_path") or "")
    return (book.get("title") or "") == (guess.get("title") or "") and (book.get("author") or "") == (
        guess.get("author") or ""
    )


def list_books(connection) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {BOOK_COLUMNS} FROM books b LEFT JOIN chunks c ON c.book_id = b.id "
            "GROUP BY b.id ORDER BY lower(b.title), b.id"
        )
        return [_book_row(row, cursor.description) for row in cursor.fetchall()]


def get_book(connection, book_id: int) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {BOOK_COLUMNS} FROM books b LEFT JOIN chunks c ON c.book_id = b.id WHERE b.id = %s GROUP BY b.id",
            (book_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise AdminError(f"Book {book_id} not found", 404)
        return _book_row(row, cursor.description)


def find_book_by_hash(connection, file_hash: str) -> Optional[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM books WHERE file_hash = %s ORDER BY id LIMIT 1", (file_hash,))
        row = cursor.fetchone()
    return get_book(connection, row[0]) if row else None


def clean_metadata(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    """Validate title / author / publication_year / department; empty strings become NULL."""
    unknown = set(data) - set(METADATA_FIELDS)
    if unknown:
        raise AdminError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    cleaned: Dict[str, Any] = {}
    for name in METADATA_FIELDS:
        if name not in data:
            continue
        value = data[name]
        if name == "publication_year":
            if value in (None, ""):
                cleaned[name] = None
                continue
            try:
                year = int(value)
            except (TypeError, ValueError) as exc:
                raise AdminError("publication_year must be a year like 2019") from exc
            if not 1000 <= year <= 2100:
                raise AdminError("publication_year must be between 1000 and 2100")
            cleaned[name] = year
        else:
            text = " ".join(str(value or "").split())
            if len(text) > 500:
                raise AdminError(f"{name} is too long (500 characters max)")
            cleaned[name] = text or None
    if not partial and not cleaned.get("title"):
        raise AdminError("title is required")
    if partial and "title" in cleaned and not cleaned["title"]:
        raise AdminError("title cannot be empty")
    return cleaned


def update_book_metadata(connection, es_client, book_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Change a book's metadata in PostgreSQL and in all its Elasticsearch documents (no re-embedding)."""
    fields = clean_metadata(data, partial=True)
    if not fields:
        return get_book(connection, book_id)
    get_book(connection, book_id)

    assignments = ", ".join(f"{name} = %s" for name in fields)
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE books SET {assignments} WHERE id = %s", (*fields.values(), book_id))
        es_fields = {name: fields[name] for name in ("title", "author", "department") if name in fields}
        if es_fields:
            # Inside the transaction: if Elasticsearch fails the PostgreSQL change is rolled back too.
            es_client.update_by_query(
                index=settings.elasticsearch_index,
                query={"term": {"book_id": book_id}},
                script={
                    "lang": "painless",
                    "source": "for (entry in params.fields.entrySet()) { ctx._source[entry.getKey()] = entry.getValue(); }",
                    "params": {"fields": es_fields},
                },
                conflicts="proceed",
                refresh=True,
            )
    return get_book(connection, book_id)


def exclude_and_delete_book(connection, es_client, book_id: int) -> Dict[str, Any]:
    """Delete a book from both stores but keep its PDF, remembering it in `excluded_files`.

    Callers hold the ingestion lock so the poller cannot re-ingest the file in between.
    """
    book = get_book(connection, book_id)
    es_client.delete_by_query(
        index=settings.elasticsearch_index, query={"term": {"book_id": book_id}}, conflicts="proceed", refresh=True
    )
    metadata = {name: book.get(name) for name in METADATA_FIELDS}
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO excluded_files (file_path, file_hash, metadata) VALUES (%s, %s, %s) "
                "ON CONFLICT (file_path) DO UPDATE SET file_hash = EXCLUDED.file_hash, "
                "metadata = EXCLUDED.metadata, deleted_at = NOW()",
                (book["file_path"], book["file_hash"], Json(metadata)),
            )
            cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))
    return book


def list_excluded(connection) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path, file_hash, metadata, deleted_at FROM excluded_files ORDER BY deleted_at DESC")
        rows = cursor.fetchall()
    return [
        {
            "file_path": path,
            "file_hash": file_hash,
            "metadata": metadata or {},
            "deleted_at": deleted_at.isoformat() if deleted_at else None,
            "file_exists": os.path.isfile(path),
            "pdf_link": pdf_link(path),
        }
        for path, file_hash, metadata, deleted_at in rows
    ]


def get_excluded(connection, file_path: str) -> Dict[str, Any]:
    for item in list_excluded(connection):
        if item["file_path"] == file_path:
            return item
    raise AdminError(f"'{file_path}' is not in the deleted list", 404)


def remove_exclusion(connection, file_path: str) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM excluded_files WHERE file_path = %s", (file_path,))


def safe_pdf_name(name: str) -> str:
    """A file name safe to place in the PDF directory (keeps Unicode letters, drops path parts)."""
    base = os.path.basename((name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(unicodedata.normalize("NFC", base))
    if ext.lower() != ".pdf":
        raise AdminError("Only .pdf files can be uploaded")
    # Letters (incl. Indic vowel signs, category M), digits and a few punctuation marks; everything else -> "_".
    stem = "".join(
        char if unicodedata.category(char)[0] in "LMN" or char in " -.()&," else "_" for char in stem
    )
    stem = re.sub(r"_+", "_", stem).strip(" ._")[:150]
    return f"{stem or 'upload'}.pdf"


def unique_pdf_path(directory: str, name: str) -> str:
    """`directory/name`, with ` (2)`, ` (3)`... appended when that file already exists."""
    stem, ext = os.path.splitext(name)
    candidate = os.path.join(directory, name)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
        counter += 1
    return candidate


def source_path(file_name: str) -> str:
    """Path of a file in the PDF directory in the form the ingester stores (`./data/pdfs/x.pdf`)."""
    return os.path.join(settings.pdf_dir, file_name)


def counts(connection) -> Dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM books), (SELECT COUNT(*) FROM chunks), "
            "(SELECT COUNT(DISTINCT page_number || ':' || book_id) FROM chunks), (SELECT COUNT(*) FROM excluded_files), "
            "(SELECT COUNT(*) FROM api_clients WHERE enabled)"
        )
        books, chunks, pages, excluded, keys = cursor.fetchone()
    return {"books": books, "chunks": chunks, "pages": pages, "excluded": excluded, "api_keys": keys}


# --- API keys ---------------------------------------------------------------------------------------------------

API_KEY_PREFIX = "osk_"


def hash_api_key(key: str) -> str:
    # Keys are 32 random bytes, so a plain SHA-256 is enough (no brute-forceable secret to stretch).
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _client_row(row) -> Dict[str, Any]:
    client_id, name, prefix, enabled, created_at, last_used_at, request_count = row
    return {
        "id": client_id,
        "name": name,
        "key_prefix": prefix,
        "enabled": enabled,
        "created_at": created_at.isoformat() if created_at else None,
        "last_used_at": last_used_at.isoformat() if last_used_at else None,
        "request_count": request_count,
    }


CLIENT_COLUMNS = "id, name, key_prefix, enabled, created_at, last_used_at, request_count"


def list_api_clients(connection) -> List[Dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {CLIENT_COLUMNS} FROM api_clients ORDER BY created_at DESC, id DESC")
        return [_client_row(row) for row in cursor.fetchall()]


def create_api_client(connection, name: str) -> Dict[str, Any]:
    """Create an API key; the full key is only in this return value."""
    name = " ".join(str(name or "").split())
    if not name or len(name) > 100:
        raise AdminError("Give the app a name (100 characters max)")
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO api_clients (name, key_prefix, key_hash) VALUES (%s, %s, %s) RETURNING {CLIENT_COLUMNS}",
                (name, key[:10], hash_api_key(key)),
            )
            client = _client_row(cursor.fetchone())
    client["key"] = key
    return client


def set_api_client_enabled(connection, client_id: int, enabled: bool) -> Dict[str, Any]:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE api_clients SET enabled = %s WHERE id = %s RETURNING {CLIENT_COLUMNS}", (enabled, client_id)
            )
            row = cursor.fetchone()
    if row is None:
        raise AdminError(f"API key {client_id} not found", 404)
    return _client_row(row)


def delete_api_client(connection, client_id: int) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM api_clients WHERE id = %s", (client_id,))
            if cursor.rowcount == 0:
                raise AdminError(f"API key {client_id} not found", 404)


def use_api_key(connection, key: str) -> Optional[int]:
    """Validate an API key and count the request; returns the client id or None."""
    if not key or not key.startswith(API_KEY_PREFIX):
        return None
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE api_clients SET last_used_at = NOW(), request_count = request_count + 1 "
                "WHERE key_hash = %s AND enabled RETURNING id",
                (hash_api_key(key),),
            )
            row = cursor.fetchone()
    return row[0] if row else None

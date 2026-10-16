"""OSISS ingestion pipeline.

Responsibilities:
1) Monitor local PDF directory for new files.
2) Extract Unicode text with PyMuPDF.
3) Chunk text into overlapping segments.
4) Insert metadata/chunks into PostgreSQL.
5) Insert chunk text + embedding vectors into Elasticsearch.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from elasticsearch.helpers import bulk
from sentence_transformers import SentenceTransformer
import torch

from clients import get_elasticsearch_client, get_postgres_connection
from config import settings
from utils import (
    build_page_aware_chunks,
    compute_file_hash,
    detect_language_code,
    extract_pdf_pages,
    is_exercise_chunk,
    infer_metadata_from_filename,
    sanitize_text,
)


def resolve_embedder_device() -> str:
    """Resolve SentenceTransformer runtime device from config and availability."""
    configured = settings.inference_device
    if configured in {"cpu", "cuda"}:
        if configured == "cuda" and not torch.cuda.is_available():
            print("[OSISS] CUDA requested but unavailable. Falling back to CPU.")
            return "cpu"
        return configured

    return "cuda" if torch.cuda.is_available() else "cpu"


def list_pdf_files(pdf_dir: str) -> List[str]:
    """Return all PDF paths from the source directory, sorted for determinism."""
    if not os.path.isdir(pdf_dir):
        return []

    entries = []
    for name in os.listdir(pdf_dir):
        if name.lower().endswith(".pdf"):
            entries.append(os.path.join(pdf_dir, name))
    return sorted(entries)


def fetch_ingested_books(connection) -> Dict[str, Tuple[int, Optional[str]]]:
    """Map file_path -> (book_id, file_hash) for books already in PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path, id, file_hash FROM books")
        rows = cursor.fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


# Key of the PostgreSQL advisory lock held while a book is added, re-ingested or deleted, so the polling ingester
# (ingestion.sh or the API's worker) and admin uploads never work on the same file at once.
INGEST_LOCK_KEY = 7_051_155

METADATA_FIELDS = ("title", "author", "publication_year", "department")

Log = Callable[[str], None]


class IngestBusy(RuntimeError):
    """The ingestion lock was not free within the requested time."""


@contextmanager
def ingest_lock(connection, timeout: Optional[float] = None) -> Iterator[None]:
    """Hold the ingestion advisory lock (session level, so it spans the transactions inside).

    Waits indefinitely by default; with `timeout` (seconds) raises IngestBusy instead of waiting longer.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        with connection.cursor() as cursor:
            if deadline is None:
                cursor.execute("SELECT pg_advisory_lock(%s)", (INGEST_LOCK_KEY,))
                acquired = True
            else:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_LOCK_KEY,))
                acquired = cursor.fetchone()[0]
        connection.commit()
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise IngestBusy("Ingestion is busy with another book; try again when it finishes.")
        time.sleep(0.2)
    try:
        yield
    finally:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (INGEST_LOCK_KEY,))
        connection.commit()


def fetch_excluded_files(connection) -> set:
    """Paths of PDFs whose book an admin deleted; the poller leaves them alone."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path FROM excluded_files")
        return {row[0] for row in cursor.fetchall()}


def fetch_book_metadata(connection, book_id: int) -> Dict:
    """Title / author / year / department of a stored book."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT title, author, publication_year, department FROM books WHERE id = %s", (book_id,))
        row = cursor.fetchone()
    return dict(zip(METADATA_FIELDS, row)) if row else {}


def set_book_hash(connection, book_id: int, file_hash: str) -> None:
    """Backfill the content hash for a book ingested before hashing existed."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE books SET file_hash = %s WHERE id = %s", (file_hash, book_id))


def delete_book(connection, es_client, book_id: int) -> None:
    """Remove a book's rows (chunks cascade) and its Elasticsearch documents."""
    es_client.delete_by_query(
        index=settings.elasticsearch_index,
        body={"query": {"term": {"book_id": book_id}}},
        refresh=True,
    )
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))


def insert_book(connection, metadata: Dict, file_path: str, file_hash: str) -> int:
    """Insert a book record and return its ID."""
    query = """
    INSERT INTO books (title, author, publication_year, department, file_path, language_code, file_hash)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                metadata.get("title"),
                metadata.get("author"),
                metadata.get("publication_year"),
                metadata.get("department"),
                file_path,
                metadata.get("language_code"),
                file_hash,
            ),
        )
        return cursor.fetchone()[0]


def insert_chunk(
    connection, book_id: int, chunk_index: int, page_number: int, chunk_text: str, language_code: str, is_exercise: bool
) -> int:
    """Insert a chunk row and return chunk ID."""
    query = """
    INSERT INTO chunks (book_id, chunk_index, page_number, chunk_text, language_code, es_doc_id, is_exercise)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id
    """

    with connection.cursor() as cursor:
        cursor.execute(query, (book_id, chunk_index, page_number, chunk_text, language_code, None, is_exercise))
        return cursor.fetchone()[0]


def update_chunk_es_doc_id(connection, chunk_id: int, es_doc_id: str) -> None:
    """Store reverse reference from PostgreSQL chunk row to Elasticsearch document."""
    with connection.cursor() as cursor:
        cursor.execute("UPDATE chunks SET es_doc_id = %s WHERE id = %s", (es_doc_id, chunk_id))


def process_pdf(
    file_path: str,
    connection,
    es_client,
    embedder: SentenceTransformer,
    file_hash: str,
    metadata: Optional[Dict] = None,
) -> Tuple[bool, str]:
    """Process a single PDF and index all extracted chunks.

    `metadata` (title / author / publication_year / department) comes from the admin app or a previous
    ingestion of the same file; without it the fields are guessed from the file name.

    Returns:
    - (True, message) on success.
    - (False, error_message) on failure.
    """
    try:
        metadata = dict(metadata) if metadata else infer_metadata_from_filename(file_path)
        pages = extract_pdf_pages(file_path)
        if not pages:
            return False, f"No readable text found in '{file_path}'."

        chunks = build_page_aware_chunks(
            pages,
            chunk_size=settings.chunk_size_words,
            overlap=settings.chunk_overlap_words,
        )

        if not chunks:
            return False, f"No chunks generated from '{file_path}'."

        full_title = metadata.get("title") or os.path.basename(file_path)
        metadata["title"] = full_title

        exercise_flags = [is_exercise_chunk(text) for _, text in chunks]
        chunk_languages = [detect_language_code(text) for _, text in chunks]
        metadata["language_code"] = Counter(chunk_languages).most_common(1)[0][0]

        with connection:
            book_id = insert_book(connection, metadata, file_path, file_hash)

            texts = [sanitize_text(chunk_text) for _, chunk_text in chunks]
            embeddings = embedder.encode(texts, normalize_embeddings=True, convert_to_numpy=True)

            bulk_actions = []
            chunk_records: List[Tuple[int, int]] = []

            for idx, (page_number, chunk_text) in enumerate(chunks):
                chunk_text = sanitize_text(chunk_text)
                chunk_id = insert_chunk(
                    connection, book_id, idx, page_number, chunk_text, chunk_languages[idx], exercise_flags[idx]
                )
                chunk_records.append((chunk_id, idx))

                action = {
                    "_index": settings.elasticsearch_index,
                    "_id": f"book-{book_id}-chunk-{idx}",
                    "_source": {
                        "book_id": book_id,
                        "chunk_id": chunk_id,
                        "chunk_index": idx,
                        "page_number": page_number,
                        "title": metadata.get("title"),
                        "author": metadata.get("author"),
                        "department": metadata.get("department"),
                        "language_code": chunk_languages[idx],
                        "file_path": file_path,
                        "is_exercise": exercise_flags[idx],
                        "text": chunk_text,
                        "embedding": embeddings[idx].tolist(),
                    },
                }
                bulk_actions.append(action)

            success_count, _ = bulk(es_client, bulk_actions, raise_on_error=False)
            if success_count != len(bulk_actions):
                raise RuntimeError(
                    f"Elasticsearch bulk indexing mismatch: {success_count}/{len(bulk_actions)} indexed"
                )

            for chunk_id, idx in chunk_records:
                update_chunk_es_doc_id(connection, chunk_id, f"book-{book_id}-chunk-{idx}")

        return True, f"Ingested '{file_path}' with {len(chunks)} chunks."
    except Exception as exc:  # pylint: disable=broad-except
        return False, f"Failed to process '{file_path}': {exc}"


def plan_for_path(connection, path: str, file_hash: str) -> Tuple[str, Optional[int]]:
    """What a scan should do with a PDF: "new", "changed" (book id), "backfill" (book id) or "skip"."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM excluded_files WHERE file_path = %s", (path,))
        if cursor.fetchone():
            return "skip", None
        cursor.execute("SELECT id, file_hash FROM books WHERE file_path = %s", (path,))
        row = cursor.fetchone()
    if row is None:
        return "new", None
    book_id, stored_hash = row
    if stored_hash is None:
        return "backfill", book_id
    return ("changed", book_id) if stored_hash != file_hash else ("skip", book_id)


def ingest_path(connection, es_client, embedder, path: str, log: Log = print) -> bool:
    """Ingest one PDF from the source directory if it is new or changed (under the ingestion lock).

    A changed file keeps the metadata of its previous ingestion. Returns True when a book was (re)ingested.
    """
    with ingest_lock(connection):
        file_hash = compute_file_hash(path)
        action, book_id = plan_for_path(connection, path, file_hash)
        connection.rollback()
        if action == "skip":
            return False
        if action == "backfill":
            with connection:
                set_book_hash(connection, book_id, file_hash)
            return False

        metadata = None
        if action == "changed":
            log(f"[OSISS] Content changed for '{path}'; re-ingesting.")
            metadata = fetch_book_metadata(connection, book_id)
            connection.rollback()
            delete_book(connection, es_client, book_id)

        ok, message = process_pdf(path, connection, es_client, embedder, file_hash, metadata=metadata)
        log(f"[OSISS] {message}" if ok else f"[OSISS] ERROR: {message}")
        return ok


def scan_once(connection, es_client, embedder, only: Optional[List[str]] = None, log: Log = print) -> int:
    """One pass over the PDF directory; returns how many books were (re)ingested.

    The cheap pre-check runs without the lock; each candidate is re-checked under it in `ingest_path`.
    """
    with connection:
        known_books = fetch_ingested_books(connection)
        excluded = fetch_excluded_files(connection)

    candidates: List[str] = []
    for path in list_pdf_files(settings.pdf_dir):
        if only and not any(part.lower() in os.path.basename(path).lower() for part in only):
            continue
        if path in excluded:
            continue
        known = known_books.get(path)
        if known is None or known[1] is None or known[1] != compute_file_hash(path):
            candidates.append(path)

    if not candidates:
        log("[OSISS] No new or changed PDFs found.")
        return 0

    log(f"[OSISS] Found {len(candidates)} new or changed PDF(s).")
    return sum(1 for path in candidates if ingest_path(connection, es_client, embedder, path, log))


def run_ingestion_loop(once: bool, only: Optional[List[str]] = None) -> int:
    """Run ingestion either once or continuously in watch mode.

    `only` restricts work to PDFs whose file name contains one of the given substrings.
    """
    try:
        connection = get_postgres_connection()
        es_client = get_elasticsearch_client()
        embedder_device = resolve_embedder_device()
        embedder = SentenceTransformer(settings.bge_model_path, device=embedder_device, local_files_only=True)
        print(f"[OSISS] Retriever device: {embedder_device}")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Startup failure in ingestion pipeline: {exc}", file=sys.stderr)
        return 1

    print("[OSISS] Ingestion pipeline started.")
    print(f"[OSISS] Watching directory: {settings.pdf_dir}")

    try:
        while True:
            scan_once(connection, es_client, embedder, only)
            if once:
                break
            time.sleep(settings.polling_interval_seconds)

        return 0
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for ingestion mode."""
    parser = argparse.ArgumentParser(description="OSISS PDF ingestion pipeline")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan of data/pdfs and exit.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="Only handle PDFs whose file name contains one of these substrings.",
    )
    return parser.parse_args()


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    return run_ingestion_loop(once=args.once, only=args.only)


if __name__ == "__main__":
    raise SystemExit(main())

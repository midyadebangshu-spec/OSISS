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
from typing import Dict, List, Optional, Tuple

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
) -> Tuple[bool, str]:
    """Process a single PDF and index all extracted chunks.

    Returns:
    - (True, message) on success.
    - (False, error_message) on failure.
    """
    try:
        metadata = infer_metadata_from_filename(file_path)
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
            with connection:
                known_books = fetch_ingested_books(connection)

            new_files: List[Tuple[str, str]] = []
            for path in list_pdf_files(settings.pdf_dir):
                if only and not any(part.lower() in os.path.basename(path).lower() for part in only):
                    continue
                file_hash = compute_file_hash(path)
                known = known_books.get(path)
                if known is None:
                    new_files.append((path, file_hash))
                    continue

                book_id, stored_hash = known
                if stored_hash is None:
                    with connection:
                        set_book_hash(connection, book_id, file_hash)
                elif stored_hash != file_hash:
                    print(f"[OSISS] Content changed for '{path}'; re-ingesting.")
                    delete_book(connection, es_client, book_id)
                    new_files.append((path, file_hash))

            if not new_files:
                print("[OSISS] No new or changed PDFs found.")
            else:
                print(f"[OSISS] Found {len(new_files)} new or changed PDF(s).")

            for pdf_path, file_hash in new_files:
                ok, message = process_pdf(pdf_path, connection, es_client, embedder, file_hash)
                if ok:
                    print(f"[OSISS] {message}")
                else:
                    print(f"[OSISS] ERROR: {message}", file=sys.stderr)

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

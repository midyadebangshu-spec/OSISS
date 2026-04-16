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
from typing import Dict, List, Set, Tuple

from elasticsearch.helpers import bulk
from sentence_transformers import SentenceTransformer
import torch

from clients import get_elasticsearch_client, get_postgres_connection
from config import settings
from utils import build_page_aware_chunks, extract_pdf_pages, infer_metadata_from_filename, sanitize_text


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


def fetch_ingested_file_paths(connection) -> Set[str]:
    """Load file paths already registered in PostgreSQL books table."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT file_path FROM books")
        rows = cursor.fetchall()
    return {row[0] for row in rows}


def insert_book(connection, metadata: Dict, file_path: str) -> int:
    """Insert a book record and return its ID."""
    query = """
    INSERT INTO books (title, author, publication_year, department, file_path, language_code)
    VALUES (%s, %s, %s, %s, %s, %s)
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
                None,
            ),
        )
        return cursor.fetchone()[0]


def insert_chunk(connection, book_id: int, chunk_index: int, page_number: int, chunk_text: str) -> int:
    """Insert a chunk row and return chunk ID."""
    query = """
    INSERT INTO chunks (book_id, chunk_index, page_number, chunk_text, language_code, es_doc_id)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id
    """

    with connection.cursor() as cursor:
        cursor.execute(query, (book_id, chunk_index, page_number, chunk_text, None, None))
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

        with connection:
            book_id = insert_book(connection, metadata, file_path)

            texts = [sanitize_text(chunk_text) for _, chunk_text in chunks]
            embeddings = embedder.encode(texts, normalize_embeddings=True, convert_to_numpy=True)

            bulk_actions = []
            chunk_records: List[Tuple[int, int]] = []

            for idx, (page_number, chunk_text) in enumerate(chunks):
                chunk_text = sanitize_text(chunk_text)
                chunk_id = insert_chunk(connection, book_id, idx, page_number, chunk_text)
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
                        "language_code": metadata.get("language_code"),
                        "file_path": file_path,
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


def run_ingestion_loop(once: bool) -> int:
    """Run ingestion either once or continuously in watch mode."""
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
                known_files = fetch_ingested_file_paths(connection)

            all_pdfs = list_pdf_files(settings.pdf_dir)
            new_files = [path for path in all_pdfs if path not in known_files]

            if not new_files:
                print("[OSISS] No new PDFs found.")
            else:
                print(f"[OSISS] Found {len(new_files)} new PDF(s).")

            for pdf_path in new_files:
                ok, message = process_pdf(pdf_path, connection, es_client, embedder)
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
    return parser.parse_args()


def main() -> int:
    """Program entrypoint."""
    args = parse_args()
    return run_ingestion_loop(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())

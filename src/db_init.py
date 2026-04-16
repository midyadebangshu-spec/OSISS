"""Initialize PostgreSQL schema and Elasticsearch index for OSISS."""

from __future__ import annotations

import sys

from clients import get_elasticsearch_client, get_postgres_connection
from config import settings


POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    publication_year INT,
    department TEXT,
    file_path TEXT NOT NULL UNIQUE,
    language_code TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    book_id INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    page_number INT,
    chunk_text TEXT NOT NULL,
    language_code TEXT,
    es_doc_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (book_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page_number ON chunks(page_number);
"""


def init_postgres() -> None:
    """Create required PostgreSQL tables and indexes."""
    connection = get_postgres_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(POSTGRES_SCHEMA_SQL)
        print("[OSISS] PostgreSQL schema initialized.")
    finally:
        connection.close()


def init_elasticsearch() -> None:
    """Create Elasticsearch index with dense vector mapping for BGE-M3."""
    client = get_elasticsearch_client()
    index_name = settings.elasticsearch_index

    mapping = {
        "mappings": {
            "properties": {
                "book_id": {"type": "integer"},
                "chunk_id": {"type": "long"},
                "chunk_index": {"type": "integer"},
                "page_number": {"type": "integer"},
                "title": {"type": "text"},
                "author": {"type": "text"},
                "language_code": {"type": "keyword"},
                "file_path": {"type": "keyword"},
                "text": {"type": "text"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.embedding_dim,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        }
    }

    if client.indices.exists(index=index_name):
        print(f"[OSISS] Elasticsearch index '{index_name}' already exists.")
        return

    client.indices.create(index=index_name, body=mapping)
    print(f"[OSISS] Elasticsearch index '{index_name}' created.")


def main() -> int:
    """Execute all initialization steps with robust error handling."""
    try:
        init_postgres()
        init_elasticsearch()
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Initialization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

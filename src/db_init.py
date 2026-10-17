"""Initialize PostgreSQL schema and Elasticsearch index for OSISS."""

from __future__ import annotations

import sys

from clients import get_elasticsearch_client, get_postgres_connection
from config import settings


POSTGRES_SCHEMA_SQL = """
-- Several API copies may start at once: serialise schema changes (concurrent CREATE ... IF NOT EXISTS can fail).
SELECT pg_advisory_xact_lock(7051156);

CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    publication_year INT,
    department TEXT,
    file_path TEXT NOT NULL UNIQUE,
    language_code TEXT,
    file_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE books ADD COLUMN IF NOT EXISTS file_hash TEXT;

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

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS is_exercise BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_chunks_book_id ON chunks(book_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page_number ON chunks(page_number);

-- PDFs whose book an admin deleted: the file stays on disk but is not ingested again until restored.
-- `metadata` keeps the deleted book's title/author/year/department for the restore.
CREATE TABLE IF NOT EXISTS excluded_files (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT,
    metadata JSONB,
    deleted_at TIMESTAMPTZ DEFAULT NOW()
);

-- API keys handed to apps that call /api/search. Only a SHA-256 of each key is stored.
CREATE TABLE IF NOT EXISTS api_clients (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    request_count BIGINT NOT NULL DEFAULT 0
);

-- Shared by all API copies when STATE_BACKEND=postgres (README "Running several copies").
-- One row: admin-app settings + server state (`version` goes up on every change) and the corpus version
-- (goes up whenever books change, so every copy drops its cached answers).
CREATE TABLE IF NOT EXISTS server_state (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    version BIGINT NOT NULL DEFAULT 0,
    corpus_version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    seq BIGSERIAL PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    book_id INT,
    worker TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    finished_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status ON ingest_jobs(status, seq);

CREATE TABLE IF NOT EXISTS ingest_log (
    seq BIGSERIAL PRIMARY KEY,
    time DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    worker TEXT
);

CREATE TABLE IF NOT EXISTS admin_auth_failures (
    address TEXT NOT NULL,
    at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_auth_failures ON admin_auth_failures(address, at);

CREATE TABLE IF NOT EXISTS replicas (
    id TEXT PRIMARY KEY,
    started_at DOUBLE PRECISION NOT NULL,
    heartbeat_at DOUBLE PRECISION NOT NULL,
    info JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- For S3 storage: the object's ETag when it was ingested, so the poller does not download unchanged files.
ALTER TABLE books ADD COLUMN IF NOT EXISTS source_version TEXT;
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


# BM25 subfields with Elasticsearch's built-in language analyzers (stemming,
# stopwords, Indic normalization). Search queries the one matching the query.
TEXT_FIELD_MAPPING = {
    "type": "text",
    "fields": {
        "bn": {"type": "text", "analyzer": "bengali"},
        "hi": {"type": "text", "analyzer": "hindi"},
        "en": {"type": "text", "analyzer": "english"},
    },
}


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
                "department": {"type": "keyword"},
                "is_exercise": {"type": "boolean"},
                "language_code": {"type": "keyword"},
                "file_path": {"type": "keyword"},
                "text": TEXT_FIELD_MAPPING,
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
        client.indices.put_mapping(
            index=index_name,
            properties={"is_exercise": {"type": "boolean"}, "text": TEXT_FIELD_MAPPING},
        )
        print(f"[OSISS] Elasticsearch index '{index_name}' already exists (mapping up to date).")
        return

    try:
        client.indices.create(index=index_name, body=mapping)
    except Exception as exc:  # pylint: disable=broad-except
        # Another copy created it in the meantime.
        if "resource_already_exists_exception" not in str(exc):
            raise
        print(f"[OSISS] Elasticsearch index '{index_name}' was just created by another process.")
        return
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

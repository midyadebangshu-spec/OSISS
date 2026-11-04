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

-- Context Explorer (src/concepts.py, rebuilt as a whole by src/build_concepts.py).
CREATE TABLE IF NOT EXISTS concepts (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    book_count INT NOT NULL DEFAULT 0,
    mention_count INT NOT NULL DEFAULT 0,
    definition_count INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS concept_mentions (
    concept_id INT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    book_id INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chunk_id BIGINT,
    page_number INT,
    is_definition BOOLEAN NOT NULL DEFAULT FALSE,
    sentence TEXT
);
CREATE INDEX IF NOT EXISTS idx_concept_mentions_concept ON concept_mentions(concept_id);
CREATE TABLE IF NOT EXISTS concept_links (
    source_id INT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    target_id INT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    weight REAL NOT NULL,
    npmi REAL NOT NULL,
    sentence_count INT NOT NULL,
    chunk_count INT NOT NULL,
    book_count INT NOT NULL,
    relation TEXT,
    relation_source_id INT,
    order_hint INT NOT NULL DEFAULT 0,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (source_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_concept_links_target ON concept_links(target_id);
CREATE TABLE IF NOT EXISTS concept_figures (
    concept_id INT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    book_id INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_number INT NOT NULL,
    image TEXT NOT NULL,
    caption TEXT,
    label TEXT,
    width INT,
    height INT
);
CREATE INDEX IF NOT EXISTS idx_concept_figures_concept ON concept_figures(concept_id);

-- Citation details, and printed page numbers (src/page_labels.py). `page_offset`: printed page = PDF page - offset,
-- set by an admin; `page_labels`: the PDF's own labels, read once (NULL = not read yet, [] = it has none).
ALTER TABLE books ADD COLUMN IF NOT EXISTS publisher TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS edition TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS isbn TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS page_offset INT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS page_labels JSONB;

-- Scholar accounts and their workspace (src/accounts.py, src/workspace.py). Everything a scholar saves cascades away
-- with the account.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);
CREATE TABLE IF NOT EXISTS user_login_failures (
    key TEXT NOT NULL,
    at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_login_failures ON user_login_failures(key, at);

-- One saved piece of evidence: a passage, definition, figure, table, page or concept, with where it came from.
-- title/author are copied so the citation survives if the book's metadata is edited or the book is deleted.
CREATE TABLE IF NOT EXISTS evidence_items (
    id BIGSERIAL PRIMARY KEY,
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    book_id INT REFERENCES books(id) ON DELETE SET NULL,
    book_title TEXT,
    book_author TEXT,
    page_number INT,
    quote TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    concept_key TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_unique
    ON evidence_items(owner_id, kind, COALESCE(book_id, 0), COALESCE(page_number, 0), content_hash);
CREATE INDEX IF NOT EXISTS idx_evidence_owner ON evidence_items(owner_id, created_at DESC);
CREATE TABLE IF NOT EXISTS collections (
    id BIGSERIAL PRIMARY KEY,
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_name ON collections(owner_id, LOWER(name));
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    evidence_id BIGINT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (collection_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_collection_items_evidence ON collection_items(evidence_id);
CREATE TABLE IF NOT EXISTS notes (
    id BIGSERIAL PRIMARY KEY,
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    evidence_id BIGINT REFERENCES evidence_items(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes(owner_id, updated_at DESC);
-- What the dashboard shows as "recent searches" and "continue reading".
CREATE TABLE IF NOT EXISTS user_activity (
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    label TEXT NOT NULL,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (owner_id, kind, ref)
);

-- Research projects (phase 2): a question, a set of books to search, and the evidence / notes gathered for it.
CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS project_books (
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    book_id INT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, book_id)
);
CREATE TABLE IF NOT EXISTS project_evidence (
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    evidence_id BIGINT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_project_evidence_evidence ON project_evidence(evidence_id);
ALTER TABLE notes ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_id);
-- Writing studio drafts (src/drafts.py): Markdown-like text with [@e<evidence id>] citation tokens.
CREATE TABLE IF NOT EXISTS drafts (
    id BIGSERIAL PRIMARY KEY,
    owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT 'apa',
    body TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_drafts_owner ON drafts(owner_id, updated_at DESC);
-- Where the scholar stopped reading a book (the reader resumes there).
ALTER TABLE user_activity ADD COLUMN IF NOT EXISTS page_number INT;
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

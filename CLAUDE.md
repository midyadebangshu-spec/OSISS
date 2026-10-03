# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow rules

- For every multi-step build or change: first write out a plan (what changes, which files, how it will be verified), wait for the user's explicit approval, and only then start implementing. Do not begin work on the plan's steps before approval. Trivial single-step edits don't need a plan.

OSISS is an offline multilingual (bn/hi/en) semantic search over institutional PDFs. It returns extractive quotes (no generative answers) with book, page and PDF link. Backend is Python/FastAPI in `src/`; frontend is React + Vite + Tailwind in `frontend/`. See `README.md` for setup and env var docs.

## Commands

Always use the `.venv` interpreter (created by `./setup.sh`); scripts in `src/` use flat imports (`from config import settings`), so run them as files or via the wrappers, not as `python -m src.x` (except uvicorn, see below).

```bash
./setup.sh                      # venv, deps, docker (postgres+ES), schema, model download
./ingestion.sh --once           # ingest new/changed PDFs from data/pdfs/ (omit --once to poll)
./api.sh                        # uvicorn src.api_server:app on :8000
cd frontend && npm run dev      # :5173, proxies /api, /health, /data to :8000
cd frontend && npm run lint && npm run build
.venv/bin/python src/db_init.py # idempotent; adds new columns/index after upgrades
.venv/bin/python src/search.py --query "..."   # CLI search

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests                 # unit tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/test_utils.py::test_detect_language_code
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is required: system ROS pytest plugins otherwise crash collection.

## Architecture

Two pipelines share Postgres + Elasticsearch (docker-compose; Postgres on host port **5433**, ES security disabled):

- **Ingestion** (`ingest.py`, helpers in `utils.py`): PyMuPDF text + Tesseract OCR of embedded images per page -> word chunks (300/50 overlap, chunked *per page* so `page_number` stays exact) -> BGE-M3 embeddings -> rows in Postgres `books`/`chunks` and docs in ES index `osiss_chunks` (`_id = book-{id}-chunk-{idx}`). Dedup/re-ingest is keyed on `books.file_path` plus a SHA-256 `file_hash`; a changed file is deleted (PG cascade + ES `delete_by_query` on `book_id`) and re-ingested. Metadata (title/author/year/department) is guessed from the filename only, and is poor for names with hyphens (e.g. both data-structures books get title "DATA").
- **Search** (`search.py`, called by `api_server.py`; model-free helpers in `ranking.py`): `normalize_query` (NFC, invisible chars) -> two ES searches, kNN on `embedding` and BM25 (`build_keyword_query`: question words stripped, both nukta spellings, `multi_match` over `text` + the query language's `text.bn|hi|en` subfield; bn/hi queries filtered to same-`language_code` chunks via `KEYWORD_SAME_LANGUAGE`, since all current eval answers are in English books and stray Latin tokens like `b` were pure noise), fused with RRF (`HYBRID_MODE=rrf`; `sum` = the old single additive query) into `RETRIEVAL_CANDIDATES` chunks -> cross-encoder rerank (`bge-reranker-v2-m3`, skipped if model dir missing or `RERANK_ENABLED=false`) -> `diversify_candidates` (best chunk per page, Jaccard near-duplicate drop, MMR on stored embeddings, `MMR_LAMBDA`) -> extractive QA (xlm-roberta-squad2) on `top_k + QA_EXTRA_CANDIDATES` -> sentence/paragraph extraction around the answer span -> sort by `(1-w)*rerank + w*qa` (`QA_RANK_WEIGHT`), cut to `top_k`. QA normally uses the Transformers pipeline; if it fails to load, `make_windowed_qa_runner` reads long chunks in 384-token windows (stride 128) and works with slow SentencePiece tokenizers (answers snap to whole words). Models and the ES client are process-wide `lru_cache` singletons (`get_models`, `get_reranker`, `get_es_client`); all models load with `local_files_only=True` from `models/` (download via `src/download_models.py`).
- **API contract**: `api_server.map_result` converts search output to `{exact_quote, book_title, author, department, page_number, pdf_link, paragraph_text}`. `frontend/src/lib/searchApi.ts` normalizes that shape, and the page viewer is an iframe on `pdf_link#page=N`. Stored `page_number` is the PDF page position, not the printed page number.
- All settings are env-driven in the frozen `Settings` dataclass in `config.py` (read at import time; tests/sweeps override with `object.__setattr__(settings, ...)`).

Chunks get an `is_exercise` flag (`utils.is_exercise_chunk`, a deliberately conservative heuristic) that `retrieve_top_chunks` filters out via `EXCLUDE_EXERCISES`; after changing the heuristic, re-apply it to existing data with `src/backfill_exercise_flags.py` (updates PG + ES in place, no re-embedding). Chunk-boundary changes (e.g. `MIN_CHUNK_WORDS` tail merging) only take effect for newly ingested/re-ingested books.

Changing the ES mapping or embedding dim generally requires recreating the index (`db_init.py` only creates it if absent) and re-ingesting. Adding subfields is the exception: `db_init.py` puts the `text.bn|hi|en` multi-fields on an existing index and `src/reindex_es.py` fills them for existing docs via `_update_by_query` (no re-embedding; only docs missing `text.en` unless `--all`).

## Evaluating search changes

`tests/eval_search.py` runs 38 hand-labelled questions (`tests/eval/questions.json`, expected file + page set + answer keyword) against the live ingested corpus and reports hit@k / MRR:

```bash
.venv/bin/python tests/eval_search.py --validate              # DB only: check labels against ingested text
.venv/bin/python tests/eval_search.py --mode retrieval        # retrieval only
.venv/bin/python tests/eval_search.py --mode full --depth 3   # full pipeline as the API serves it
```

`--compare` runs the set once per feature variant (baseline all off, all on, each of RRF / language fields / question-word stripping / same-language keywords / dedupe+MMR / extra QA candidates off) and prints one table; in `--mode retrieval` only the first four matter. `--set key=value` overrides any `Settings` field (lower-case attribute name).

`tests/test_ranking.py` and `tests/test_search.py` need no services: the latter uses a fake ES client, fake models, and a SentencePiece tokenizer + random tiny XLM-R trained on the fly.

Needs Postgres/ES running and the PDFs in `data/pdfs/` ingested (PDFs, `models/`, `.venv` are gitignored). Several corpus books legitimately answer the same question, so when a "miss" looks correct, widen the label rather than tuning the system. The eval set is small (2 questions each for bn/hi), so treat one- or two-question differences as noise. Two corpus books are scanned (OCR-only) and noisy.

`.mcp.json` (code-review-graph, local paths) and `.code-review-graph/` are gitignored.

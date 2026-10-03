# OSISS — Open-Source Institutional Scholar Search

OSISS is a multilingual, offline-capable academic search system for institutional PDFs.  
It indexes textbook/research PDF content, retrieves semantically relevant chunks, and returns extractive answers with source metadata and page reference.

## Features

- Multilingual semantic retrieval (`BAAI/bge-m3`)
- Extractive QA (no generative hallucination) with `deepset/xlm-roberta-large-squad2`
- PDF ingestion with Unicode-safe extraction (PyMuPDF)
- PostgreSQL for book/chunk metadata + Elasticsearch for vector search
- Dockerized infra setup (Postgres + Elasticsearch)
- Frontend search UI with right-side in-page PDF viewer

## Project Structure

- `src/` — backend scripts (ingestion, search pipeline, API server, DB init)
- `frontend/` — React + TypeScript + Tailwind app
- `data/pdfs/` — source PDFs for ingestion
- `models/` — local model cache for offline use
- `docker-compose.yml` — Postgres + Elasticsearch services
- `setup.sh` — one-click bootstrap
- `ingestion.sh` — ingestion runner wrapper
- `api.sh` — API runner wrapper

## Prerequisites

- Linux/macOS with:
	- Python 3.10+
	- Docker + Docker Compose plugin
	- Node.js 18+ and npm
	- Tesseract OCR (system package for image text extraction)

## Quick Start

1) Run setup:

```bash
./setup.sh
```

2) Put PDFs in:

```bash
data/pdfs/
```

3) Ingest once:

```bash
./ingestion.sh --once
```

4) Start API:

```bash
./api.sh
```

5) Start frontend:

```bash
cd frontend
npm install
npm run dev
```

6) Open:

```text
http://localhost:5173
```

## Common Commands

- Re-ingest new PDFs once: `./ingestion.sh --once`
- Continuous watch ingestion: `./ingestion.sh`
- CLI query test:

```bash
.venv/bin/python src/search.py --query "What are the laws of probability?"
```

## Runtime Endpoints

- API health: `GET http://localhost:8000/health`
- Search: `POST http://localhost:8000/api/search`
- Static PDFs: `http://localhost:8000/data/pdfs/<file>.pdf`

## Environment Notes

- Default Postgres host port is `5433` (to avoid local `5432` conflicts).
- Inference device can be controlled with:

```bash
INFERENCE_DEVICE=auto|cpu|cuda
```

- Retrieval is hybrid: dense kNN (BGE-M3) plus BM25 keyword search, fused with Reciprocal Rank Fusion (`HYBRID_MODE=rrf`, default; `RRF_K` default `60`, `RRF_KEYWORD_WEIGHT` default `1.0` scales the BM25 list). `HYBRID_MODE=sum` restores the older single query that adds kNN and `HYBRID_KEYWORD_BOOST` (default `0.01`) × BM25 scores.
- BM25 also searches a language-analyzed copy of the text (`text.bn` / `text.hi` / `text.en`, Elasticsearch's built-in analyzers: stemming, stopwords, Indic normalization) chosen by the query's script (`LANGUAGE_ANALYZERS=false` to turn off). Bengali/Hindi queries only keyword-match chunks of the same language (`KEYWORD_SAME_LANGUAGE`, default on): against English books their only matchable tokens are stray Latin ones such as the `b` in a Van der Waals question, so cross-language matching is left to the dense search. **After upgrading an existing install** run `.venv/bin/python src/db_init.py` and then `.venv/bin/python src/reindex_es.py` once to fill these fields for already-indexed chunks (in place, no re-embedding; `--all` re-indexes everything).
- Queries are NFC-normalized with invisible characters removed. The BM25 query drops question words (what/explain, কী/কাকে বলে, क्या/परिभाषा …; `QUERY_STOPWORDS=false` to keep them) and includes both spellings of nukta letters (e.g. ড় as one or two code points). The embedder, reranker and QA still see the full question.
- Search retrieves `RETRIEVAL_CANDIDATES` (default 30) chunks, reranks them with `BAAI/bge-reranker-v2-m3` (`RERANK_ENABLED=false` to turn off; needs `python src/download_models.py`), keeps the best chunk per page and drops near-identical texts (`DEDUPE_RESULTS`), diversifies with MMR over the chunk embeddings (`MMR_LAMBDA`, default `0.85`; `1.0` turns it off), then runs QA on `top_k + QA_EXTRA_CANDIDATES` (default `2`) chunks and returns the best `top_k`. Final order blends rerank and QA scores via `QA_RANK_WEIGHT` (default `0.3`). Reranking adds roughly 1.5 s per query on an RTX 3060; each extra QA candidate adds one QA pass.
- Evaluate accuracy: `.venv/bin/python tests/eval_search.py --validate` then `--mode full --depth 3`. `--compare` prints a table with each search feature switched off in turn (and all off as a baseline); `--set KEY=VALUE` overrides a setting for one run, e.g. `--set mmr_lambda=0.9 --set rrf_keyword_weight=0.5`.
- Exercise / question-bank chunks (review questions, MCQ lists, `[LO x.y]` exercise tags) are flagged `is_exercise` at ingestion and excluded from search (`EXCLUDE_EXERCISES=false` to include them). For already-ingested data run `.venv/bin/python src/backfill_exercise_flags.py [--dry-run --samples 3]` (no re-embedding).
- Trailing chunk fragments under `MIN_CHUNK_WORDS` (default 40) are merged into the previous chunk of the same page at ingestion time; existing chunks are only affected if a book is re-ingested.
- `MIN_QA_SCORE` drops low-confidence QA results (default `0.0`, no filtering).
- `OCR_LANGUAGE=eng+ben+hin` enables Bengali/Hindi OCR (language packs are installed by `setup.sh`).
- Ingestion tracks a SHA-256 hash per PDF; a PDF modified at the same path is automatically re-indexed.
- `language_code` (`bn`/`hi`/`en`) is detected per chunk from Unicode script.
- Run tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests`
- After upgrading an existing install, run `.venv/bin/python src/db_init.py` to add the `file_hash` column. Existing books get their hash backfilled on the next ingestion run.

## Troubleshooting

- Docker permission issue:
	- Add user to docker group and relogin, or run setup with sudo.
- Missing Python modules when running scripts:
	- Use wrappers (`./ingestion.sh`, `./api.sh`) or activate `.venv`.
- API returns stale behavior:
	- Restart API: `pkill -f "uvicorn src.api_server:app" || true && ./api.sh`

## License

Open-source project intended for academic and institutional deployment.

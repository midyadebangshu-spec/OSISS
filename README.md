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

## Troubleshooting

- Docker permission issue:
	- Add user to docker group and relogin, or run setup with sudo.
- Missing Python modules when running scripts:
	- Use wrappers (`./ingestion.sh`, `./api.sh`) or activate `.venv`.
- API returns stale behavior:
	- Restart API: `pkill -f "uvicorn src.api_server:app" || true && ./api.sh`

## License

Open-source project intended for academic and institutional deployment.

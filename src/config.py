"""Central configuration utilities for OSISS.

This module keeps environment-dependent settings in one place to make
deployment and local overrides predictable and easy to maintain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5433"))
    postgres_db: str = os.getenv("POSTGRES_DB", "osiss")
    postgres_user: str = os.getenv("POSTGRES_USER", "osiss")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "osiss")

    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    elasticsearch_index: str = os.getenv("ELASTICSEARCH_INDEX", "osiss_chunks")

    models_dir: str = os.getenv("MODELS_DIR", "./models")
    bge_model_path: str = os.getenv("BGE_MODEL_PATH", "./models/BAAI_bge-m3")
    qa_model_path: str = os.getenv("QA_MODEL_PATH", "./models/deepset_xlm-roberta-large-squad2")

    reranker_model_path: str = os.getenv("RERANKER_MODEL_PATH", "./models/BAAI_bge-reranker-v2-m3")
    rerank_enabled: bool = os.getenv("RERANK_ENABLED", "true").lower() in {"1", "true", "yes"}
    # Romanized Bengali/Hindi queries ("goti kake bole") are transliterated to native script before searching.
    translit_enabled: bool = os.getenv("TRANSLIT_ENABLED", "true").lower() in {"1", "true", "yes"}
    translit_model_path: str = os.getenv(
        "TRANSLIT_MODEL_PATH", "./models/Singla0009_all-indic-transliteration/indicxlit_ct2_fp32"
    )
    # Candidate spellings per word; extra ones are added to the keyword (BM25) query only.
    translit_topk: int = int(os.getenv("TRANSLIT_TOPK", "3"))
    # RRF weight of the untransliterated query's results relative to each script variant's (1.0).
    translit_original_weight: float = float(os.getenv("TRANSLIT_ORIGINAL_WEIGHT", "0.5"))
    # Ambiguous queries (unknown words, no Indic marker words) are searched as typed first; below this top
    # rerank score the romanized rewrites are searched too and chunks keep their best score.
    translit_confidence: float = float(os.getenv("TRANSLIT_CONFIDENCE", "0.5"))
    # English words (built by src/build_english_vocab.py) used to tell English from romanized queries.
    english_vocab_path: str = os.getenv("ENGLISH_VOCAB_PATH", "./models/english_vocab.txt")
    retrieval_candidates: int = int(os.getenv("RETRIEVAL_CANDIDATES", "30"))
    # Weight of the QA score when combining it with the rerank score (0 = rerank only, 1 = QA only).
    qa_rank_weight: float = float(os.getenv("QA_RANK_WEIGHT", "0.3"))

    pdf_dir: str = os.getenv("PDF_DIR", "./data/pdfs")
    polling_interval_seconds: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "10"))

    ocr_language: str = os.getenv("OCR_LANGUAGE", "eng")
    # Books whose PDF text layer is unusable (legacy-font encodings, symbol garbage, scans) are OCRed page by page.
    ocr_fallback: bool = os.getenv("OCR_FALLBACK", "true").lower() in {"1", "true", "yes"}
    ocr_dpi: int = int(os.getenv("OCR_DPI", "300"))
    # Tesseract languages tried when probing which script such a book is in.
    ocr_languages: str = os.getenv("OCR_LANGUAGES", "ben+hin+eng")
    ocr_workers: int = int(os.getenv("OCR_WORKERS", "8"))

    chunk_size_words: int = int(os.getenv("CHUNK_SIZE_WORDS", "300"))
    chunk_overlap_words: int = int(os.getenv("CHUNK_OVERLAP_WORDS", "50"))

    min_chunk_words: int = int(os.getenv("MIN_CHUNK_WORDS", "40"))
    exclude_exercises: bool = os.getenv("EXCLUDE_EXERCISES", "true").lower() in {"1", "true", "yes"}

    min_qa_score: float = float(os.getenv("MIN_QA_SCORE", "0.0"))

    hybrid_keyword_boost: float = float(os.getenv("HYBRID_KEYWORD_BOOST", "0.01"))
    # "rrf": separate kNN and BM25 queries fused by rank; "sum": one query adding kNN + boost * BM25 scores.
    hybrid_mode: str = os.getenv("HYBRID_MODE", "rrf").lower()
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    rrf_keyword_weight: float = float(os.getenv("RRF_KEYWORD_WEIGHT", "1.0"))
    # BM25 also searches the text.bn / text.hi / text.en subfield matching the query language.
    language_analyzers: bool = os.getenv("LANGUAGE_ANALYZERS", "true").lower() in {"1", "true", "yes"}
    query_stopwords: bool = os.getenv("QUERY_STOPWORDS", "true").lower() in {"1", "true", "yes"}
    # Bengali/Hindi queries: BM25 only over chunks of the same language (cross-language is left to kNN).
    keyword_same_language: bool = os.getenv("KEYWORD_SAME_LANGUAGE", "true").lower() in {"1", "true", "yes"}

    dedupe_results: bool = os.getenv("DEDUPE_RESULTS", "true").lower() in {"1", "true", "yes"}
    # MMR trade-off between relevance (1.0 = no diversification) and novelty.
    mmr_lambda: float = float(os.getenv("MMR_LAMBDA", "0.85"))
    # Extra reranked chunks sent to QA beyond top_k, so a strong QA answer can still make the cut.
    qa_extra_candidates: int = int(os.getenv("QA_EXTRA_CANDIDATES", "2"))

    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1024"))
    inference_device: str = os.getenv("INFERENCE_DEVICE", "auto").lower()
    # "fp32", "fp16" or "auto" (fp16 on a GPU). Half precision is faster on a GPU; the CPU always uses fp32.
    model_precision: str = os.getenv("MODEL_PRECISION", "fp32").lower()
    # (query, chunk) pairs scored per reranker forward pass; larger is faster on a GPU, up to its memory.
    rerank_batch_size: int = int(os.getenv("RERANK_BATCH_SIZE", "8"))

    # Load protection for /api/search (see README "Capacity and load").
    # Recent answers are kept in memory; 0 turns the cache off. Book changes clear it; TTL bounds staleness.
    search_cache_size: int = int(os.getenv("SEARCH_CACHE_SIZE", "1000"))
    search_cache_ttl_seconds: int = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "600"))
    # Searches running the models at once; more wait in a queue of SEARCH_QUEUE_LIMIT, beyond that 503.
    search_concurrency: int = int(os.getenv("SEARCH_CONCURRENCY", "2"))
    search_queue_limit: int = int(os.getenv("SEARCH_QUEUE_LIMIT", "50"))
    search_queue_timeout_seconds: int = int(os.getenv("SEARCH_QUEUE_TIMEOUT_SECONDS", "60"))
    # Searches per minute per client IP / per API key (0 = no limit); over the limit answers 429.
    search_rate_limit_per_minute: int = int(os.getenv("SEARCH_RATE_LIMIT_PER_MINUTE", "0"))
    api_key_rate_limit_per_minute: int = int(os.getenv("API_KEY_RATE_LIMIT_PER_MINUTE", "0"))

    # Admin API (/api/admin/*) is disabled unless this is set; setup.sh generates one into .env.
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    # Optional comma-separated IPs / CIDRs allowed to call the admin API (empty = any).
    admin_allowed_ips: str = os.getenv("ADMIN_ALLOWED_IPS", "")
    # Uploaded PDFs are staged here until ingestion moves them into PDF_DIR.
    upload_dir: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    upload_max_mb: int = int(os.getenv("UPLOAD_MAX_MB", "500"))
    # Settings changed from the admin app; loaded at API startup over the environment values.
    settings_overrides_path: str = os.getenv("SETTINGS_OVERRIDES_PATH", "./data/admin/settings_overrides.json")

    # Who may call /api/search and /data: "open" (anyone), "key" (X-API-Key or an allowed origin), or "" (not
    # chosen yet by the admin; behaves like "open").
    search_access: str = os.getenv("SEARCH_ACCESS", "").lower()
    # Comma-separated browser origins allowed to call the search API (CORS), e.g. https://search.example.edu.
    cors_origins: str = os.getenv("CORS_ORIGINS", "")
    # When set, pdf_link in search results is absolute (for apps calling the API from elsewhere).
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "")
    # Serve the built web frontend (frontend/dist) at / from the API process.
    serve_frontend: bool = os.getenv("SERVE_FRONTEND", "false").lower() in {"1", "true", "yes"}


settings = Settings()

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


settings = Settings()

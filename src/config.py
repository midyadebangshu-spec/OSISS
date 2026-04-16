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

    pdf_dir: str = os.getenv("PDF_DIR", "./data/pdfs")
    polling_interval_seconds: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "10"))

    chunk_size_words: int = int(os.getenv("CHUNK_SIZE_WORDS", "300"))
    chunk_overlap_words: int = int(os.getenv("CHUNK_OVERLAP_WORDS", "50"))

    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1024"))
    inference_device: str = os.getenv("INFERENCE_DEVICE", "auto").lower()


settings = Settings()

"""Client factories for PostgreSQL and Elasticsearch.

All scripts import from this module so connection handling and retries stay
consistent across ingestion, search, and initialization workflows.
"""

from __future__ import annotations

import time
from typing import Optional

import psycopg2
from elasticsearch import Elasticsearch

from config import settings


def get_postgres_connection(max_retries: int = 10, retry_delay_seconds: int = 2):
    """Create and return a PostgreSQL connection with retry logic."""
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return psycopg2.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
            )
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_delay_seconds)

    raise RuntimeError(f"Failed to connect to PostgreSQL after retries: {last_error}")


def get_elasticsearch_client(max_retries: int = 10, retry_delay_seconds: int = 2) -> Elasticsearch:
    """Create and return an Elasticsearch client with connectivity check."""
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            client = Elasticsearch(settings.elasticsearch_url)
            if client.ping():
                return client
            raise RuntimeError("Elasticsearch ping failed")
        except Exception as exc:  # pylint: disable=broad-except
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_delay_seconds)

    raise RuntimeError(f"Failed to connect to Elasticsearch after retries: {last_error}")

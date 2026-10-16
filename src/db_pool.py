"""PostgreSQL connection pool for the API process (admin routes and API-key checks)."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from psycopg2.pool import ThreadedConnectionPool

from clients import postgres_params


@lru_cache(maxsize=1)
def get_pool() -> ThreadedConnectionPool:
    # Not cached when it raises, so a database that was down at first use is retried on the next request.
    return ThreadedConnectionPool(1, 10, connect_timeout=5, **postgres_params())


def connect():
    """A standalone connection (for the ingestion worker, which holds one for a long time)."""
    import psycopg2  # pylint: disable=import-outside-toplevel

    return psycopg2.connect(connect_timeout=5, **postgres_params())


@contextmanager
def db() -> Iterator:
    """Borrow a pooled connection; uncommitted work is rolled back when it is returned."""
    pool = get_pool()
    connection = pool.getconn()
    try:
        yield connection
    finally:
        broken = bool(connection.closed)
        if not broken:
            try:
                connection.rollback()
            except Exception:  # pylint: disable=broad-except
                broken = True
        pool.putconn(connection, close=broken)

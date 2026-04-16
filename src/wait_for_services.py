"""Wait until PostgreSQL and Elasticsearch become reachable.

This script is used by setup.sh to provide deterministic startup behavior,
especially on first boot where containers may take time to initialize.
"""

from __future__ import annotations

import sys

from clients import get_elasticsearch_client, get_postgres_connection


def main() -> int:
    """Wait for both services and return a process exit code."""
    try:
        pg_conn = get_postgres_connection(max_retries=60, retry_delay_seconds=2)
        pg_conn.close()

        es_client = get_elasticsearch_client(max_retries=60, retry_delay_seconds=2)
        es_client.info()

        print("[OSISS] Services are ready.")
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Service readiness check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

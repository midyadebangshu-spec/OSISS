"""Flag exercise / question-bank chunks in existing data without re-embedding.

Reads stored chunk text from PostgreSQL, computes `is_exercise`, and updates
both PostgreSQL and the Elasticsearch documents in place.

Usage: python src/backfill_exercise_flags.py [--dry-run] [--samples N]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from elasticsearch.helpers import bulk

from clients import get_elasticsearch_client, get_postgres_connection
from config import settings
from db_init import init_elasticsearch, init_postgres
from utils import is_exercise_chunk


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill is_exercise flags")
    parser.add_argument("--dry-run", action="store_true", help="report counts and samples without writing")
    parser.add_argument("--samples", type=int, default=0, help="print N flagged chunk previews per book")
    args = parser.parse_args()

    init_postgres()
    init_elasticsearch()
    connection = get_postgres_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT c.id, c.es_doc_id, c.chunk_text, c.is_exercise, b.id, b.file_path "
        "FROM chunks c JOIN books b ON b.id = c.book_id ORDER BY c.id"
    )

    changes = []
    stats = defaultdict(lambda: [0, 0])  # book path -> [flagged, total]
    samples = defaultdict(list)
    for chunk_id, es_doc_id, text, current, _book_id, path in cursor.fetchall():
        flag = is_exercise_chunk(text)
        stats[path][1] += 1
        if flag:
            stats[path][0] += 1
            if len(samples[path]) < args.samples:
                samples[path].append(text[:150])
        if flag != current:
            changes.append((chunk_id, es_doc_id, flag))

    for path, (flagged, total) in sorted(stats.items()):
        print(f"{flagged:5d} / {total:5d} flagged  {path}")
        for preview in samples[path]:
            print(f"        - {preview}")
    print(f"{len(changes)} chunk(s) need an update.")

    if args.dry_run or not changes:
        return 0

    with connection:
        cursor.executemany("UPDATE chunks SET is_exercise = %s WHERE id = %s", [(flag, cid) for cid, _, flag in changes])

    es_client = get_elasticsearch_client()
    actions = [
        {"_op_type": "update", "_index": settings.elasticsearch_index, "_id": doc_id, "doc": {"is_exercise": flag}}
        for _, doc_id, flag in changes
        if doc_id
    ]
    success, errors = bulk(es_client, actions, raise_on_error=False, refresh=True)
    print(f"[OSISS] Updated {success} Elasticsearch document(s); errors: {len(errors) if errors else 0}.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

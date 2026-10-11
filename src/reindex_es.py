"""Populate the language-analyzed text subfields (text.bn / text.hi / text.en) in place.

New subfields added to an existing mapping only apply to documents indexed
afterwards. This adds the mapping (via db_init) and re-indexes the existing
documents from their stored _source with `_update_by_query`, so nothing is
re-extracted or re-embedded.

Usage: python src/reindex_es.py [--all]
"""

from __future__ import annotations

import argparse
import sys
import time

from clients import get_elasticsearch_client
from config import settings
from db_init import init_elasticsearch

MISSING_SUBFIELDS = {"bool": {"must_not": [{"exists": {"field": "text.en"}}]}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-index Elasticsearch docs for the language subfields")
    parser.add_argument("--all", action="store_true", help="re-index every document, not only ones missing subfields")
    args = parser.parse_args()

    init_elasticsearch()
    client = get_elasticsearch_client()
    index = settings.elasticsearch_index
    query = {"match_all": {}} if args.all else MISSING_SUBFIELDS

    pending = client.count(index=index, query=query)["count"]
    print(f"[OSISS] {pending} document(s) to re-index in '{index}'.")
    if not pending:
        return 0

    task_id = client.update_by_query(
        index=index, query=query, conflicts="proceed", slices="auto", refresh=True, wait_for_completion=False
    )["task"]
    while True:
        task = client.tasks.get(task_id=task_id)
        status = task["task"]["status"]
        print(f"[OSISS] updated {status.get('updated', 0)} / {status.get('total', pending)}")
        if task.get("completed"):
            break
        time.sleep(3)

    response = task.get("response", {})
    failures = response.get("failures", [])
    if failures:
        print(f"[OSISS] {len(failures)} failure(s), first: {failures[0]}", file=sys.stderr)
        return 1
    print(f"[OSISS] Done; {client.count(index=index, query=MISSING_SUBFIELDS)['count']} document(s) still missing subfields.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

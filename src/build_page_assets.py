"""Build the page content the web UI shows (figures, tables, formulas, page images) for ingested books.

New books get it during ingestion (PAGE_ASSETS_AT_INGEST); run this once after upgrading, after changing the
extractor (page_assets.ASSET_VERSION), or with --prune to delete folders of books that no longer exist.
Searches also build missing pages on demand, so this is an optimisation, not a requirement.

    .venv/bin/python src/build_page_assets.py                 # every book, missing pages only
    .venv/bin/python src/build_page_assets.py --only Class-X  # books whose file name contains a substring
    .venv/bin/python src/build_page_assets.py --force         # rebuild everything
    .venv/bin/python src/build_page_assets.py --prune         # also remove orphaned folders
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import List, Optional

import fitz

from clients import get_postgres_connection
from config import settings
import page_assets
from storage import get_storage


def _build_range(local_file: str, file_hash: str, pages: List[int], force: bool) -> int:
    return page_assets.build_pages(local_file, file_hash, pages, force=force, log=print)


def build_book(local_file: str, file_hash: str, workers: int, force: bool) -> int:
    with fitz.open(local_file) as pdf:
        total = len(pdf)
    numbers = list(range(1, total + 1))
    if workers <= 1 or total < 20:
        return _build_range(local_file, file_hash, numbers, force)
    batches = [numbers[i::workers] for i in range(workers)]
    # spawn: the worker processes only need PyMuPDF, not this process's state.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        return sum(pool.map(_build_range, [local_file] * workers, [file_hash] * workers, batches, [force] * workers))


def prune(known_hashes: set) -> int:
    if not os.path.isdir(settings.asset_dir):
        return 0
    removed = 0
    for name in os.listdir(settings.asset_dir):
        path = os.path.join(settings.asset_dir, name)
        if os.path.isdir(path) and len(name) == 64 and name not in known_hashes:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build OSISS page assets for ingested books")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="Books whose file name contains one of these.")
    parser.add_argument("--force", action="store_true", help="Rebuild pages that are already cached.")
    parser.add_argument("--prune", action="store_true", help="Delete asset folders of books no longer ingested.")
    parser.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    args = parser.parse_args(argv)

    connection = get_postgres_connection()
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, file_path, file_hash FROM books ORDER BY id")
        books = cursor.fetchall()
    connection.close()

    failures = 0
    for book_id, file_path, file_hash in books:
        name = os.path.basename(file_path)
        if args.only and not any(part.lower() in name.lower() for part in args.only):
            continue
        if not file_hash:
            print(f"[OSISS] Skipping book {book_id} ({name}): no content hash yet (run ingestion once).")
            continue
        started = time.monotonic()
        try:
            with get_storage().local_copy(file_path) as local_file:
                built = build_book(local_file, file_hash, args.workers, args.force)
        except Exception as exc:  # pylint: disable=broad-except
            failures += 1
            print(f"[OSISS] ERROR: {name}: {exc}", file=sys.stderr)
            continue
        print(f"[OSISS] {name}: {built} page(s) built in {time.monotonic() - started:.0f}s")

    if args.prune:
        removed = prune({file_hash for _, _, file_hash in books if file_hash})
        print(f"[OSISS] Pruned {removed} orphaned asset folder(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

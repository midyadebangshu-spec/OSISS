"""Build the Context Explorer's concept graph from the ingested books (src/concepts.py) and store it.

Reads chunks from PostgreSQL and bold lines / figure captions from the page-asset cache (books without cached pages
just contribute fewer candidate terms), then replaces the stored graph in one transaction. Run after ingesting or
deleting books; the admin app's maintenance job "concepts" runs the same script.

    .venv/bin/python src/build_concepts.py             # build and store
    .venv/bin/python src/build_concepts.py --dry-run   # build, print statistics and samples, store nothing
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import time
from typing import Dict, List, Tuple

from clients import get_postgres_connection
from concepts import AliasMatcher, Chunk, Graph, build_graph
from config import settings
from db_init import init_postgres

LINE_BOLD = 4  # page_assets line flag


def load_chunks(connection) -> Tuple[List[Chunk], Dict[int, Tuple[str, str]]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, title, file_hash FROM books")
        books = {book_id: (title, file_hash) for book_id, title, file_hash in cursor.fetchall()}
        cursor.execute("SELECT id, book_id, page_number, chunk_text FROM chunks WHERE NOT is_exercise ORDER BY book_id, chunk_index")
        chunks = [Chunk(chunk_id, book_id, page or 0, text) for chunk_id, book_id, page, text in cursor.fetchall()]
    return chunks, books


def asset_pages(file_hash: str):
    for path in glob.glob(os.path.join(settings.asset_dir, file_hash, "p*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                yield json.load(handle)
        except (OSError, ValueError):
            continue


def load_assets(books: Dict[int, Tuple[str, str]]):
    """(bold lines as (book_id, page, text), captioned figures as (book_id, page, figure))."""
    bold, figures = [], []
    for book_id, (_, file_hash) in books.items():
        if not file_hash:
            continue
        for page in asset_pages(file_hash):
            if not page.get("text_usable", True):
                continue
            for line in page.get("lines", []):
                if len(line) > 6 and line[6] & LINE_BOLD and 1 <= len(line[4].split()) <= 6:
                    bold.append((book_id, page["page"], line[4]))
            for figure in page.get("figures", []):
                if figure.get("caption"):
                    figures.append((book_id, page["page"], figure))
    return bold, figures


def match_figures(graph: Graph, figures) -> List[Tuple[str, int, int, Dict]]:
    """Figures whose caption names a concept."""
    matcher = AliasMatcher({alias: key for key, concept in graph.concepts.items() for alias in concept.aliases})
    matched = []
    for book_id, page, figure in figures:
        for key in dict.fromkeys(key for key, _ in matcher.find(figure["caption"])):
            matched.append((key, book_id, page, figure))
    return matched


def report(graph: Graph, books: Dict[int, Tuple[str, str]], samples: int) -> None:
    by_books: Dict[str, set] = {}
    for mention in graph.mentions:
        by_books.setdefault(mention.key, set()).add(mention.book_id)
    multi = [key for key, ids in by_books.items() if len(ids) >= 2]
    print(f"[OSISS] concepts in 2+ books: {len(multi)}; links: {len(graph.links)}; "
          f"typed links: {sum(1 for link in graph.links if link.relation)}")
    rng = random.Random(7)
    print("\nSample concepts (label | books | aliases):")
    for key in rng.sample(sorted(graph.concepts), min(samples, len(graph.concepts))):
        concept = graph.concepts[key]
        print(f"  {concept.label} | {len(by_books.get(key, ()))} | {sorted(concept.aliases)[:4]}")
    print("\nSample links (a -- b | weight | sentences | books | relation):")
    for link in rng.sample(graph.links, min(samples, len(graph.links))):
        a, b = graph.concepts[link.a].label, graph.concepts[link.b].label
        print(f"  {a} -- {b} | {link.weight:.2f} | {link.sentence_count} | {len(link.books)} | {link.relation or '-'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the OSISS concept graph")
    parser.add_argument("--dry-run", action="store_true", help="Build and report, but do not store.")
    parser.add_argument("--samples", type=int, default=30, help="Random concepts / links to print in the report.")
    args = parser.parse_args()

    started = time.monotonic()
    init_postgres()
    connection = get_postgres_connection()
    chunks, books = load_chunks(connection)
    bold, figures = load_assets(books)
    print(f"[OSISS] {len(chunks)} chunks, {len(bold)} bold lines, {len(figures)} captioned figures from {len(books)} books.")
    graph = build_graph(chunks, bold)
    matched = match_figures(graph, figures)
    report(graph, books, args.samples)
    if args.dry_run:
        print("\n[OSISS] Dry run: nothing stored.")
    else:
        from concept_store import write_graph  # pylint: disable=import-outside-toplevel

        counts = write_graph(connection, graph, matched)
        print(f"\n[OSISS] Stored {counts}.")
    connection.close()
    print(f"[OSISS] Done in {time.monotonic() - started:.0f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

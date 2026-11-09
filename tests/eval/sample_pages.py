"""Draw random ingested pages to write independent evaluation questions from.

The current question set was written from definition-style passages, so it is easy. Writing questions for pages that
were picked at random (not by someone looking for a good question) is what keeps the new set independent.

  .venv/bin/python tests/eval/sample_pages.py --count 60 --seed 7 > pages.json

Pages are spread evenly over the books (round-robin), skip near-empty pages and exercise chunks, and are printed as
JSON: {file, page, language_code, words, text}. Write 1-2 questions per page into tests/eval/questions_v2.json (see
tests/eval/README.md), then check the labels with `tests/eval_search.py --questions tests/eval/questions_v2.json --validate`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))


def round_robin(pages_by_book: Dict[str, List[Dict]], count: int, rng: random.Random) -> List[Dict]:
    """Pick `count` pages, taking one from each book in turn (books with fewer pages drop out)."""
    pools = {book: rng.sample(pages, len(pages)) for book, pages in pages_by_book.items()}
    order = sorted(pools)
    rng.shuffle(order)
    picked: List[Dict] = []
    while len(picked) < count and any(pools.values()):
        for book in order:
            if pools[book] and len(picked) < count:
                picked.append(pools[book].pop())
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample pages for writing evaluation questions")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-words", type=int, default=80)
    parser.add_argument("--max-chars", type=int, default=2500, help="shorten printed page text")
    args = parser.parse_args()

    from clients import get_postgres_connection

    cursor = get_postgres_connection(max_retries=2).cursor()
    cursor.execute(
        """
        SELECT b.file_path, c.page_number, MIN(c.language_code), string_agg(c.chunk_text, ' ' ORDER BY c.chunk_index)
        FROM chunks c JOIN books b ON b.id = c.book_id
        WHERE NOT COALESCE(c.is_exercise, FALSE)
        GROUP BY b.file_path, c.page_number
        """
    )
    pages_by_book: Dict[str, List[Dict]] = {}
    for file_path, page, language, text in cursor.fetchall():
        words = len((text or "").split())
        if words < args.min_words:
            continue
        pages_by_book.setdefault(file_path, []).append(
            {
                "file": os.path.basename(file_path),
                "page": page,
                "language_code": language,
                "words": words,
                "text": " ".join(text.split())[: args.max_chars],
            }
        )
    json.dump(round_robin(pages_by_book, args.count, random.Random(args.seed)), sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

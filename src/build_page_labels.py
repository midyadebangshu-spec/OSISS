"""Read every book's PDF page labels once and store them in `books.page_labels` (search results use them for printed
page numbers without opening PDFs). Safe to repeat; `--all` re-reads books that already have labels."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import library
from clients import get_postgres_connection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="also re-read books whose labels are already stored")
    args = parser.parse_args()
    connection = get_postgres_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, title FROM books" + ("" if args.all else " WHERE page_labels IS NULL") + " ORDER BY id")
            books = cursor.fetchall()
        if args.all:
            with connection, connection.cursor() as cursor:
                cursor.execute("UPDATE books SET page_labels = NULL")
        for book_id, title in books:
            offset, labels = library.book_label_info(connection, book_id)
            print(f"[OSISS] {title}: {'PDF page labels found' if labels else 'no PDF page labels'}"
                  f"{'' if offset is None else f' (page offset {offset})'}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

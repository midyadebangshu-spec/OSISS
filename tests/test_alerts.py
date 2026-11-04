"""Saved-search alerts (alerts.py): the pure helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import alerts


def item(book_id, title="B", page=1):
    return {"source": {"book_id": book_id, "book_title": title, "page_number": page}}


def test_answers_keep_one_entry_per_book_in_rank_order():
    results = [item(2, "Two", 5), item(1, "One", 9), item(2, "Two", 7), {"source": {}}, {}]
    assert alerts.answers_from(results) == [
        {"book_id": 2, "book_title": "Two", "page_number": 5}, {"book_id": 1, "book_title": "One", "page_number": 9},
    ]
    assert alerts.answers_from([]) == []


def test_only_books_new_to_the_answer_count_as_news():
    before = [{"book_id": 1, "book_title": "One", "page_number": 3}]
    now = [{"book_id": 1, "book_title": "One", "page_number": 40}, {"book_id": 7, "book_title": "Seven", "page_number": 2}]
    assert alerts.new_sources(before, now) == [now[1]]  # a page moving inside book 1 is not news
    assert alerts.new_sources(now, before) == []  # losing a book is not news either
    assert alerts.new_sources([], now) == now  # nothing answered it before, something does now
    assert alerts.new_sources(now, []) == []

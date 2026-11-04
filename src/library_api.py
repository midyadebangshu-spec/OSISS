"""Library API: /api/library (all books), /api/library/{id} (one book), /api/library/{id}/pages (page images).

Same access rule as /api/search (the path is under the public prefixes in api_server).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import library
from db_pool import db

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("")
def list_books():
    with db() as connection:
        return {"books": library.list_books(connection)}


@router.get("/{book_id}")
def book_detail(book_id: int):
    with db() as connection:
        book = library.get_book(connection, book_id)
    if book is None:
        raise HTTPException(404, "Unknown book")
    return book


@router.get("/{book_id}/pages")
def book_pages(book_id: int, start: int = Query(default=1, ge=1), count: int = Query(default=24, ge=1, le=60)):
    with db() as connection:
        pages = library.book_pages(connection, book_id, start, count)
    if pages is None:
        raise HTTPException(404, "Unknown book")
    return pages

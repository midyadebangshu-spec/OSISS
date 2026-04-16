"""HTTP API server for OSISS search endpoints."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from search import search_and_extract


app = FastAPI(title="OSISS API", version="1.0.0")

PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")


class SearchRequest(BaseModel):
    """Request payload for semantic search."""

    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class SearchResultItem(BaseModel):
    """Frontend-friendly result format."""

    exact_quote: str
    book_title: str
    author: str
    department: Optional[str] = None
    page_number: int
    pdf_link: str
    paragraph_text: str


class SearchResponse(BaseModel):
    """Search response payload."""

    query: str
    results: List[SearchResultItem]


def map_result(item: Dict[str, Any]) -> SearchResultItem:
    """Map internal search result to API response contract."""
    source = item.get("source", {})
    file_path = source.get("file_path") or ""
    normalized_file_path = file_path[2:] if file_path.startswith("./") else file_path
    pdf_link = f"/{normalized_file_path}" if normalized_file_path and not normalized_file_path.startswith("/") else normalized_file_path

    return SearchResultItem(
        exact_quote=item.get("quote", ""),
        book_title=source.get("book_title") or "Unknown Title",
        author=source.get("author") or "Unknown Author",
        department=source.get("department"),
        page_number=int(source.get("page_number") or 0),
        pdf_link=pdf_link or "#",
        paragraph_text=item.get("matched_paragraph") or item.get("chunk_preview", ""),
    )


@app.get("/health")
def health() -> Dict[str, str]:
    """Lightweight health endpoint."""
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def api_search(payload: SearchRequest) -> SearchResponse:
    """Execute semantic retrieval + extractive QA and return normalized results."""
    try:
        result = search_and_extract(query=payload.query, top_k=payload.top_k)
        mapped = [map_result(item) for item in result.get("results", [])]
        return SearchResponse(query=result.get("query", payload.query), results=mapped)
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(status_code=500, detail=f"Search execution failed: {exc}") from exc

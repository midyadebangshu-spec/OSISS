"""Shared utility functions for PDF extraction, chunking, and metadata parsing."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

import fitz


@dataclass
class PageText:
    """Container for text extracted from a specific PDF page."""

    page_number: int
    text: str


def sanitize_text(value: str) -> str:
    """Remove characters that break downstream storage/query systems.

    PostgreSQL does not allow NUL (\x00) in text literals, so this sanitizer
    strips them before ingestion.
    """
    return value.replace("\x00", "")


def infer_metadata_from_filename(file_path: str) -> dict:
    """Infer basic metadata from file names.

    Expected loose patterns include:
    - Title - Author - Year.pdf
    - Department_Title_Year.pdf
    """
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    title = base_name
    author: Optional[str] = None
    publication_year: Optional[int] = None
    department: Optional[str] = None

    dash_parts = [part.strip() for part in base_name.split("-") if part.strip()]
    if len(dash_parts) >= 2:
        title = dash_parts[0]
        author = dash_parts[1]

    year_match = re.search(r"(19\d{2}|20\d{2})", base_name)
    if year_match:
        publication_year = int(year_match.group(1))

    underscore_parts = [part.strip() for part in base_name.split("_") if part.strip()]
    if len(underscore_parts) >= 2 and not author:
        department = underscore_parts[0]
        title = " ".join(underscore_parts[1:])

    return {
        "title": title,
        "author": author,
        "publication_year": publication_year,
        "department": department,
    }


def extract_pdf_pages(file_path: str) -> List[PageText]:
    """Extract plain Unicode text per page using PyMuPDF.

    Raises an exception for unreadable/corrupted PDFs so callers can decide
    whether to skip or stop.
    """
    pages: List[PageText] = []

    with fitz.open(file_path) as pdf:
        for index, page in enumerate(pdf):
            raw_text = sanitize_text(page.get_text("text"))
            normalized_text = " ".join(raw_text.split())
            if normalized_text:
                pages.append(PageText(page_number=index + 1, text=normalized_text))

    return pages


def chunk_words(text: str, chunk_size: int, overlap: int) -> Iterator[str]:
    """Split a string into overlapping word chunks.

    This strategy is language-agnostic and robust for multilingual Unicode text
    while keeping context windows manageable for embeddings and QA.
    """
    words = text.split()
    if not words:
        return

    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        end = start + chunk_size
        piece = words[start:end]
        if not piece:
            continue
        yield " ".join(piece)


def build_page_aware_chunks(
    pages: List[PageText],
    chunk_size: int,
    overlap: int,
) -> List[Tuple[int, str]]:
    """Create chunks while preserving source page numbers.

    Each page is chunked independently so page references remain exact in
    search results and downstream answer highlighting.
    """
    page_chunks: List[Tuple[int, str]] = []
    for page in pages:
        for chunk in chunk_words(page.text, chunk_size=chunk_size, overlap=overlap):
            page_chunks.append((page.page_number, chunk))
    return page_chunks

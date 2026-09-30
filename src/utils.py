"""Shared utility functions for PDF extraction, chunking, and metadata parsing."""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

import fitz
from PIL import Image
import pytesseract

from config import settings


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


def compute_file_hash(file_path: str) -> str:
    """Return the SHA-256 hex digest of a file, read in blocks."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_language_code(text: str) -> str:
    """Guess the dominant language from Unicode script (bn, hi, else en)."""
    bengali = devanagari = latin = 0
    for char in text:
        code = ord(char)
        if 0x0980 <= code <= 0x09FF:
            bengali += 1
        elif 0x0900 <= code <= 0x097F:
            devanagari += 1
        elif char.isascii() and char.isalpha():
            latin += 1
    top = max(bengali, devanagari, latin)
    if top == 0 or top == latin:
        return "en"
    return "bn" if top == bengali else "hi"


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


def extract_page_image_text(pdf: fitz.Document, page: fitz.Page) -> str:
    """Run OCR on embedded images from a PDF page."""
    ocr_chunks: List[str] = []

    for image_info in page.get_images(full=True):
        xref = image_info[0]
        try:
            image_data = pdf.extract_image(xref)
            image_bytes = image_data.get("image")
            if not image_bytes:
                continue

            image = Image.open(io.BytesIO(image_bytes))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")

            ocr_text = pytesseract.image_to_string(image, lang=settings.ocr_language)
            ocr_text = sanitize_text(ocr_text)
            if ocr_text.strip():
                ocr_chunks.append(ocr_text)
        except Exception:  # pylint: disable=broad-except
            continue

    return " ".join(ocr_chunks)


def extract_pdf_pages(file_path: str) -> List[PageText]:
    """Extract Unicode text per page, including OCR from images.

    Raises an exception for unreadable/corrupted PDFs so callers can decide
    whether to skip or stop.
    """
    pages: List[PageText] = []

    with fitz.open(file_path) as pdf:
        for index, page in enumerate(pdf):
            raw_text = sanitize_text(page.get_text("text"))
            ocr_text = extract_page_image_text(pdf, page)
            combined_text = f"{raw_text} {ocr_text}".strip()
            normalized_text = " ".join(combined_text.split())
            if normalized_text:
                pages.append(PageText(page_number=index + 1, text=normalized_text))

    return pages


def chunk_words(text: str, chunk_size: int, overlap: int, min_tail_words: int = 0) -> Iterator[str]:
    """Split a string into overlapping word chunks.

    This strategy is language-agnostic and robust for multilingual Unicode text
    while keeping context windows manageable for embeddings and QA. If fewer than
    `min_tail_words` new words would remain after a chunk, the chunk is extended
    to the end of the text instead of emitting a tiny trailing fragment.
    """
    words = text.split()
    total = len(words)
    step = max(1, chunk_size - overlap)
    start = 0
    while start < total:
        end = start + chunk_size
        if end >= total or total - end < min_tail_words:
            yield " ".join(words[start:])
            return
        yield " ".join(words[start:end])
        start += step


EXERCISE_HEADING_RE = re.compile(
    r"(review questions|multiple[- ]choice questions|programming exercises|debugging exercises|"
    r"interview questions|supplementary problems|programming problems|problems\s+\d+\.\d+)",
    re.IGNORECASE,
)
LEARNING_OBJECTIVE_TAG_RE = re.compile(r"\[LO \d+\.\d+[^\]]*\]")
NUMBERING_RE = re.compile(r"^\d+\.\d+$")
QUESTION_SENTENCE_RE = re.compile(
    r"(?:^|[.?!]\s+|\d[.)]\s+)"
    r"(?:What|Why|How|Which|Who|Where|When|Explain|Discuss|Describe|Define|Compare|Name|Tell|"
    r"Do you|Can you|Is|Are|Does|Did|Give|Show|Write|Find)\b[^.?!]{3,250}\?"
)


def is_exercise_chunk(text: str) -> bool:
    """Heuristically flag question-bank / exercise chunks that rarely hold answers.

    Deliberately conservative (missing an exercise page is cheaper than hiding a
    real one). Signals: several question-style sentences, learning-objective tags
    like "[LO 2.2 E]", dense exercise numbering ("2.3 2.4 ..."), or an exercise
    heading near the start of the chunk (after any running page header).
    """
    if text.count("?") >= 5 and len(QUESTION_SENTENCE_RE.findall(text)) >= 3:
        return True
    if len(LEARNING_OBJECTIVE_TAG_RE.findall(text)) >= 3:
        return True

    words = text.split()
    numbering = sum(1 for word in words if NUMBERING_RE.match(word))
    if numbering >= 8 and numbering / max(1, len(words)) > 0.25:
        return True

    return bool(EXERCISE_HEADING_RE.search(text[:120]))


def build_page_aware_chunks(
    pages: List[PageText],
    chunk_size: int,
    overlap: int,
    min_tail_words: Optional[int] = None,
) -> List[Tuple[int, str]]:
    """Create chunks while preserving source page numbers.

    Each page is chunked independently so page references remain exact in
    search results and downstream answer highlighting. Tiny trailing fragments
    are merged into the previous chunk of the same page.
    """
    if min_tail_words is None:
        min_tail_words = settings.min_chunk_words
    page_chunks: List[Tuple[int, str]] = []
    for page in pages:
        for chunk in chunk_words(page.text, chunk_size=chunk_size, overlap=overlap, min_tail_words=min_tail_words):
            page_chunks.append((page.page_number, chunk))
    return page_chunks

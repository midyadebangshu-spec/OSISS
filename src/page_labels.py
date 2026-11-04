"""Printed page numbers and ISBN checks. Model- and service-free (PyMuPDF only for the two PDF readers).

A book's stored `page_number` is the position in the PDF. The number printed on the page differs (front matter,
roman numerals), and citations need the printed one. Resolution order for a page:

1. `page_offset` set by an admin (printed = pdf page - offset): an explicit choice always wins;
2. the PDF's own page labels (PyMuPDF `page.get_label()`), which also give roman numerals;
3. nothing (the caller shows only the PDF position).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence

MIN_OFFSET, MAX_OFFSET = -1000, 1000
GUESS_AGREEMENT = 0.7  # share of sampled pages that must agree on one offset
GUESS_MIN_PAGES = 4  # pages with a printed number found before an offset is suggested
MARGIN = 0.08  # header / footer band, as a fraction of the page height


# --- ISBN ---------------------------------------------------------------------------------------------------------


def clean_isbn(text: str) -> Optional[str]:
    """The ISBN-10 or ISBN-13 in `text` as digits (a final X kept for ISBN-10), or None when the check digit is wrong."""
    value = re.sub(r"(?i)^isbn(-1[03])?:?\s*", "", (text or "").strip())
    value = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"\d{13}", value):
        total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(value[:12]))
        return value if (10 - total % 10) % 10 == int(value[12]) else None
    if re.fullmatch(r"\d{9}[\dX]", value):
        total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(value))
        return value if total % 11 == 0 else None
    return None


# --- labels -------------------------------------------------------------------------------------------------------


def to_roman(number: int) -> str:
    parts = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
             (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
    out = ""
    for value, symbol in parts:
        while number >= value:
            out += symbol
            number -= value
    return out


def useful_labels(labels: Sequence[str]) -> Optional[List[str]]:
    """The PDF's labels, or None when they carry no information (empty, or just "1", "2", "3", ...)."""
    cleaned = [(label or "").strip() for label in labels]
    if not cleaned or all(label == str(index + 1) for index, label in enumerate(cleaned)):
        return None
    if not any(cleaned):
        return None
    return cleaned


def printed_label(page: int, offset: Optional[int] = None, labels: Optional[Sequence[str]] = None) -> Optional[str]:
    """The printed label of PDF page `page` (1-based), or None when unknown or when the page has no number
    (before the first numbered page with an offset)."""
    if offset is not None:
        number = page - offset
        return str(number) if number >= 1 else None
    if labels and 1 <= page <= len(labels):
        return labels[page - 1] or None
    return None


def citation_page(page: int, label: Optional[str]) -> str:
    """"233" when the printed number equals the PDF position, else "233 (PDF 245)"; without a label just the position."""
    if not label or label == str(page):
        return str(page)
    return f"{label} (PDF {page})"


# --- reading them from a PDF --------------------------------------------------------------------------------------


def read_pdf_labels(pdf) -> Optional[List[str]]:
    """Labels from an open `fitz.Document`, or None when the PDF defines none that differ from 1..n."""
    try:
        labels = [pdf[index].get_label() for index in range(pdf.page_count)]
    except Exception:  # pylint: disable=broad-except
        return None
    return useful_labels(labels)


def sample_pages(page_count: int, wanted: int = 14) -> List[int]:
    """1-based pages spread over the body of the book (skips the first 8% and the last 4%)."""
    if page_count < 6:
        return list(range(1, page_count + 1))
    start, end = max(1, int(page_count * 0.08)), max(2, int(page_count * 0.96))
    step = max(1, (end - start) // wanted)
    return list(range(start, end + 1, step))[:wanted]


def margin_numbers(pdf, page_number: int) -> List[int]:
    """Bare numbers in the header / footer band of a page (candidates for the printed page number)."""
    page = pdf[page_number - 1]
    height = page.rect.height
    found: List[int] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if (y1 < height * MARGIN or y0 > height * (1 - MARGIN)) and re.fullmatch(r"\d{1,4}", text):
            found.append(int(text))
    return found


def guess_offset(samples: Dict[int, Iterable[int]]) -> Optional[int]:
    """Suggest `page_offset` from {pdf page: candidate printed numbers}: the offset most sampled pages agree on, or
    None when too few pages have a number or they disagree."""
    votes: Counter = Counter()
    with_candidates = 0
    for page, candidates in samples.items():
        offsets = {page - number for number in set(candidates) if number >= 1 and MIN_OFFSET <= page - number <= MAX_OFFSET}
        if offsets:
            with_candidates += 1
            votes.update(offsets)
    if with_candidates < GUESS_MIN_PAGES or not votes:
        return None
    offset, count = votes.most_common(1)[0]
    return offset if count / with_candidates >= GUESS_AGREEMENT else None


def suggest_offset(pdf) -> Optional[int]:
    return guess_offset({page: margin_numbers(pdf, page) for page in sample_pages(pdf.page_count)})

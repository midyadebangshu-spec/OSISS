"""Shared utility functions for PDF extraction, chunking, and metadata parsing."""

from __future__ import annotations

import hashlib
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
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
    bengali, devanagari, latin = _script_counts(text)
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


# Common English function words; real English prose is full of them, legacy-font text
# (Kruti Dev, Bijoy: Indic text stored as Latin glyph codes) is not.
_EN_STOPWORDS = frozenset(
    "the of and a an in to is are was were be been it that this for on with as by at from or which "
    "can has have not but if when then their its these those we you they he she i".split()
)
_INDIC_RANGE = (0x0900, 0x0DFF)
_PUNCTUATION_RANGE = (0x2000, 0x206F)
_SAMPLE_PAGES = 12
_MIN_LAYER_CHARS = 80


def _script_counts(text: str) -> Tuple[int, int, int]:
    """Return (bengali, devanagari, latin) letter counts."""
    bengali = devanagari = latin = 0
    for char in text:
        code = ord(char)
        if 0x0980 <= code <= 0x09FF:
            bengali += 1
        elif 0x0900 <= code <= 0x097F:
            devanagari += 1
        elif char.isascii() and char.isalpha():
            latin += 1
    return bengali, devanagari, latin


def classify_text_layer(text: str) -> str:
    """Judge a page's PDF text layer: "empty", "good" or "bad" (unusable garbage)."""
    chars = [char for char in text if not char.isspace()]
    if len(chars) < _MIN_LAYER_CHARS:
        return "empty"

    indic = odd = 0
    for char in chars:
        code = ord(char)
        if _INDIC_RANGE[0] <= code <= _INDIC_RANGE[1]:
            indic += 1
        elif code > 127 and not (_PUNCTUATION_RANGE[0] <= code <= _PUNCTUATION_RANGE[1]):
            odd += 1
    if indic / len(chars) >= 0.3:
        return "good"
    # Symbol / dingbat / legacy-glyph soup instead of letters.
    if odd / len(chars) > 0.12:
        return "bad"

    tokens = re.findall(r"[a-z']+", text.lower())
    if len(tokens) >= 30 and sum(token in _EN_STOPWORDS for token in tokens) / len(tokens) < 0.06:
        return "bad"
    return "good"


def _sample_page_indexes(page_count: int, wanted: int) -> List[int]:
    """Evenly spaced page indexes, skipping the first and last 5% (covers, indexes)."""
    low = int(page_count * 0.05)
    high = max(low + 1, int(page_count * 0.95))
    span = high - low
    count = min(wanted, span)
    return sorted({low + (span * i) // count for i in range(count)})


def _render_page(page: fitz.Page) -> Image.Image:
    """Render a page to a grayscale PIL image for OCR."""
    pixmap = page.get_pixmap(dpi=settings.ocr_dpi, colorspace=fitz.csGRAY)
    return Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)


def _ocr_image(image: Image.Image, lang: str) -> str:
    return " ".join(sanitize_text(pytesseract.image_to_string(image, lang=lang)).split())


def choose_ocr_language(pdf: fitz.Document) -> Optional[str]:
    """Decide whether a PDF needs full-page OCR and in which Tesseract language.

    Returns None to keep the normal path (usable text layer + OCR of embedded images), or a
    language string such as "ben+eng" / "hin+eng" / "eng" when the text layer is garbage or the
    book is a scan in an Indic script.
    """
    if not settings.ocr_fallback or pdf.page_count == 0:
        return None

    sampled = _sample_page_indexes(pdf.page_count, _SAMPLE_PAGES)
    verdicts = {index: classify_text_layer(pdf[index].get_text("text")) for index in sampled}
    good = sum(v == "good" for v in verdicts.values())
    bad = sum(v == "bad" for v in verdicts.values())
    if good >= bad and good > 0:
        return None

    # Garbage or missing text layer: OCR a few pages to see which script the book is in.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    bengali = devanagari = latin = 0
    for index in sampled[:: max(1, len(sampled) // 4)][:4]:
        counts = _script_counts(_ocr_image(_render_page(pdf[index]), settings.ocr_languages))
        bengali, devanagari, latin = bengali + counts[0], devanagari + counts[1], latin + counts[2]

    if bengali + devanagari > 0.3 * (bengali + devanagari + latin):
        return "ben+eng" if bengali >= devanagari else "hin+eng"
    # Latin text behind a garbage layer needs OCR; an English scan keeps the existing image-OCR path.
    return "eng" if bad > 0 else None


def _ocr_pages(pdf: fitz.Document, lang: str) -> List[PageText]:
    """OCR every page of a PDF; Tesseract runs in worker threads, rendering stays on this one."""
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    workers = max(1, settings.ocr_workers)
    pages: List[PageText] = []
    total = pdf.page_count

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch_start in range(0, total, workers * 2):
            batch = range(batch_start, min(total, batch_start + workers * 2))
            futures = [(index, pool.submit(_ocr_image, _render_page(pdf[index]), lang)) for index in batch]
            for index, future in futures:
                text = future.result()
                if text:
                    pages.append(PageText(page_number=index + 1, text=text))
            print(f"[OSISS] OCR {min(total, batch_start + workers * 2)}/{total} pages ({lang})", flush=True)

    return pages


def extract_pdf_pages(file_path: str) -> List[PageText]:
    """Extract Unicode text per page, including OCR from images.

    Books whose text layer is unusable (legacy-font encodings, symbol garbage, Indic scans) are
    OCRed page by page instead; see `choose_ocr_language`.

    Raises an exception for unreadable/corrupted PDFs so callers can decide
    whether to skip or stop.
    """
    pages: List[PageText] = []

    with fitz.open(file_path) as pdf:
        ocr_lang = choose_ocr_language(pdf)
        if ocr_lang:
            print(f"[OSISS] Unusable text layer in '{os.path.basename(file_path)}'; full-page OCR ({ocr_lang}).")
            return _ocr_pages(pdf, ocr_lang)

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

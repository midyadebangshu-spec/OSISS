"""Rich page content for the search UI: figures, tables, formulas, text lines and a page image.

Each PDF page is analysed once with PyMuPDF and cached under ASSET_DIR/<file_hash>/:
`p{N}.json` (layout data) plus WebP crops (`p{N}-f1.webp` figure, `-t1` table, `-m1` formula) and a page
snapshot (`p{N}.webp`). The cache is keyed by the PDF's SHA-256, so it survives re-ingestion and admin metadata
edits, and a changed file gets a fresh folder. Pages are built at ingestion (PAGE_ASSETS_AT_INGEST), by
`src/build_page_assets.py` for books ingested earlier, or on first use by a search.

Everything here is heuristic and deliberately conservative: a missed figure only means the UI shows less, while a
paragraph mistaken for a figure would look broken. What the search UI does with this data is in `layout.py`.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import fitz
from PIL import Image

from config import settings
from utils import classify_text_layer

# Bump when the extraction changes; cached pages with another version are rebuilt on use.
ASSET_VERSION = 2

Rect = Tuple[float, float, float, float]

ARROWS = set("→←↔⇌⇋⟶⟵⇒⇔⇄⇆↑↓")
MATH_SYMBOLS = set("=+−×÷±∓√∑∏∫∮∂∆Δ∇∞≈≅≡≠≤≥∝∈∉⊂⊃⊆⊇∪∩πθλμσωαβγδεφψΩΣ°′″^") | ARROWS
DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")
CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?s?|table|diagram|chart|graph|plate|activity|চিত্র|ছবি|সারণি|সারণী|তালিকা|"
    r"चित्र|आकृति|सारणी|तालिका|क्रियाकलाप)\s*[.:\-–]?\s*"
    r"([0-9০-৯०-९]+(?:\s*[.\-–]\s*[0-9০-৯०-९]+)*)",
    re.IGNORECASE,
)
MONO_FONT_RE = re.compile(r"cour|mono|consol|menlo|typewriter|inconsolata|cmtt|lucidasans-typewriter", re.IGNORECASE)
MATH_FONT_RE = re.compile(r"cmmi|cmsy|cmex|cambria-?math|mathematical|stix|mt-?extra|euclid|symbolmt", re.IGNORECASE)

# Same flags for words and lines, so (block, line) numbers agree between the two extractions.
TEXT_FLAGS = fitz.TEXTFLAGS_WORDS

LINE_MONO = 1
LINE_MATH = 2
LINE_BOLD = 4


# ---------------------------------------------------------------------------------------------- geometry helpers


def _area(rect: Rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersects(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _contains_center(outer: Rect, inner: Rect) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _overlap_fraction(inner: Rect, outer: Rect) -> float:
    """Share of `inner`'s area that lies inside `outer`."""
    box = (max(inner[0], outer[0]), max(inner[1], outer[1]), min(inner[2], outer[2]), min(inner[3], outer[3]))
    area = _area(inner)
    return _area(box) / area if area else 0.0


def _round(rect: Iterable[float]) -> List[float]:
    return [round(float(value), 1) for value in rect]


def merge_rects(rects: Sequence[Rect], pad: float = 6.0) -> List[Rect]:
    """Union rectangles that touch (within `pad`) until none do."""
    merged = [tuple(rect) for rect in rects]
    changed = True
    while changed:
        changed = False
        result: List[Rect] = []
        for rect in merged:
            for index, other in enumerate(result):
                if _intersects(rect, other, pad):
                    result[index] = _union(rect, other)
                    changed = True
                    break
            else:
                result.append(rect)
        merged = result
    return merged


def normalize_label(label: str) -> str:
    """'১০.৩', '10 - 3', '10.3' -> '10.3' (figure / table numbers compared across scripts)."""
    return re.sub(r"\s*[.\-–]\s*", ".", label.translate(DIGIT_MAP).strip())


def caption_label(text: str) -> Optional[Tuple[str, str]]:
    """('figure'|'table'|'activity', '10.3') for a caption line, else None."""
    match = CAPTION_RE.match(text)
    if not match:
        return None
    word = match.group(1).lower()
    if word in {"table", "সারণি", "সারণী", "তালিকা", "सारणी", "तालिका"}:
        kind = "table"
    elif word in {"activity", "क्रियाकलाप"}:
        kind = "activity"
    else:
        kind = "figure"
    return kind, normalize_label(match.group(2))


# ---------------------------------------------------------------------------------------------- text lines


def _is_math_char(char: str) -> bool:
    return char in MATH_SYMBOLS or unicodedata.category(char) == "Sm"


def extract_lines(page: fitz.Page) -> List[Dict]:
    """Text lines in reading order: {bbox, text, block, line, flags (mono / math / bold), size}."""
    lines: List[Dict] = []
    data = page.get_text("dict", flags=TEXT_FLAGS)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line_no, line in enumerate(block.get("lines", [])):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(span["text"] for span in line["spans"]).strip()
            chars = sum(len(span["text"].strip()) for span in spans) or 1
            mono = sum(len(span["text"].strip()) for span in spans if MONO_FONT_RE.search(span.get("font", "")))
            bold = sum(len(span["text"].strip()) for span in spans if span.get("flags", 0) & 16)
            math = any(MATH_FONT_RE.search(span.get("font", "")) for span in spans)
            flags = (LINE_MONO if mono / chars > 0.6 else 0) | (LINE_MATH if math else 0)
            flags |= LINE_BOLD if bold / chars > 0.6 else 0
            sizes = sorted(span.get("size", 0) for span in spans)
            lines.append(
                {
                    "bbox": tuple(line["bbox"]),
                    "text": text,
                    "block": block.get("number", 0),
                    "line": line_no,
                    "flags": flags,
                    "size": round(sizes[len(sizes) // 2], 1),
                }
            )
    return lines


def extract_words(page: fitz.Page, lines: List[Dict]) -> List[Tuple]:
    """Words as (x0, y0, x1, y1, text, line index); the line index points into `lines` (-1 if unknown)."""
    by_key = {(line["block"], line["line"]): index for index, line in enumerate(lines)}
    words = []
    for x0, y0, x1, y1, text, block_no, line_no, _ in page.get_text("words", flags=TEXT_FLAGS, sort=False):
        index = by_key.get((block_no, line_no))
        if index is None:
            index = next((i for i, line in enumerate(lines) if _contains_center(line["bbox"], (x0, y0, x1, y1))), -1)
        words.append((x0, y0, x1, y1, text, index))
    return words


# ---------------------------------------------------------------------------------------------- figures and tables


def _is_text_box(rect: Rect, lines: List[Dict]) -> bool:
    """A frame or shaded panel around prose (NCERT 'Activity' boxes, tips, notes), not a diagram.

    Diagram labels are short; prose lines are long. A figure caption right below overrules this, because some
    real figures are boxes of explanatory text (e.g. memory layouts in programming books).
    """
    inside = [line for line in lines if _contains_center(rect, line["bbox"])]
    if inside and (caption_label(inside[0]["text"]) or ("", ""))[0] == "activity":
        return True
    if len(inside) < 3:
        return False
    # Shaded code listings: shown as code blocks, not pictures.
    if sum(1 for line in inside if line["flags"] & LINE_MONO) >= max(3, len(inside) // 2):
        return True
    width = rect[2] - rect[0]

    def prose(line: Dict) -> bool:
        words = len(line["text"].split())
        return len(line["text"]) >= 25 or (words >= 3 and line["bbox"][2] - line["bbox"][0] >= 0.6 * width)

    long_lines = sum(1 for line in inside if prose(line))
    if long_lines < 2 or long_lines / len(inside) < 0.4:
        return False
    return not _has_caption_below(rect, lines)


def _has_caption_below(rect: Rect, lines: List[Dict]) -> bool:
    for line in lines:
        box = line["bbox"]
        if 0 <= box[1] - rect[3] <= 60 and box[0] < rect[2] and box[2] > rect[0]:
            parsed = caption_label(line["text"])
            if parsed and parsed[0] == "figure":
                return True
    return False


def _is_heading_banner(rect: Rect, lines: List[Dict], body_size: float) -> bool:
    """Chapter-opening banners: big title text with ornaments around it."""
    return any(
        _contains_center(rect, line["bbox"])
        and (line["size"] >= 1.8 * body_size or (line["size"] >= 1.6 * body_size and len(line["text"].split()) >= 2))
        for line in lines
    )


def _body_size(lines: List[Dict]) -> float:
    sizes = sorted(line["size"] for line in lines if line["size"])
    return sizes[len(sizes) // 2] if sizes else 10.0


def _in_margin(rect: Rect, page_rect: Rect) -> bool:
    """Running-header / footer ornaments (page-number badges, rules)."""
    height = page_rect[3] - page_rect[1]
    if (rect[3] - rect[1]) >= 0.1 * height:
        return False
    center = (rect[1] + rect[3]) / 2
    return center < page_rect[1] + 0.12 * height or center > page_rect[3] - 0.12 * height


def _table_is_real(rows: List[List[Optional[str]]]) -> bool:
    """find_tables also boxes in diagrams (array cells, linked-list nodes); keep grids of short cell text."""
    if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
        return False
    cells = [cell for row in rows for cell in row]
    filled = [cell.strip() for cell in cells if cell and cell.strip()]
    if len(filled) < 4 or len(filled) / len(cells) < 0.5:
        return False
    if sum(len(cell) for cell in filled) / len(filled) > 90:
        return False
    # Each column needs text in at least two rows, otherwise it is a row of boxes (an array drawing).
    columns = max(len(row) for row in rows)
    for col in range(columns):
        if sum(1 for row in rows if col < len(row) and row[col] and row[col].strip()) < 2:
            return False
    return True


def _clean_cell(cell: Optional[str]) -> str:
    return " ".join((cell or "").split())


def _find_tables(page: fitz.Page) -> Tuple[List[Dict], List[Rect]]:
    """(real tables, rectangles of grid-like drawings that are really figures)."""
    tables, figure_like = [], []
    try:
        found = page.find_tables().tables
    except Exception:  # pylint: disable=broad-except
        return [], []
    for table in found:
        rect = tuple(table.bbox)
        try:
            rows = table.extract()
        except Exception:  # pylint: disable=broad-except
            continue
        if _table_is_real(rows):
            header = None
            try:
                if not table.header.external:
                    header = [_clean_cell(name) for name in table.header.names]
            except Exception:  # pylint: disable=broad-except
                header = None
            body = [[_clean_cell(cell) for cell in row] for row in rows]
            if header and body and body[0] == header:
                body = body[1:]
            tables.append({"bbox": rect, "header": header, "rows": body})
        elif _area(rect) > 400:
            figure_like.append(rect)
    return tables, figure_like


def _figure_candidates(page: fitz.Page, lines: List[Dict], tables: List[Dict], extra: List[Rect]) -> List[Tuple[Rect, str]]:
    page_rect = tuple(page.rect)
    page_area = _area(page_rect)
    candidates: List[Tuple[Rect, str]] = []

    for info in page.get_image_info():
        rect = tuple(fitz.Rect(info["bbox"]) & page.rect)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 36 or height < 36 or _area(rect) < 0.004 * page_area or _area(rect) > 0.7 * page_area:
            continue
        candidates.append((rect, "image"))

    try:
        clusters = page.cluster_drawings()
    except Exception:  # pylint: disable=broad-except
        clusters = []
    for cluster in clusters:
        rect = tuple(cluster & page.rect)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width < 40 or height < 30 or _area(rect) > 0.85 * page_area:
            continue
        if any(_overlap_fraction(rect, table["bbox"]) > 0.6 for table in tables):
            continue
        candidates.append((rect, "drawing"))

    candidates.extend((rect, "drawing") for rect in extra)
    return [
        (rect, kind) for rect, kind in candidates if not _in_margin(rect, page_rect) and not _is_text_box(rect, lines)
    ]


def _drop_banners(regions: List[Rect], lines: List[Dict]) -> List[Rect]:
    body = _body_size(lines)
    return [region for region in regions if not _is_heading_banner(region, lines, body)]


def _grow_with_labels(rect: Rect, lines: List[Dict], captions: set) -> Rect:
    """Pull short label lines that touch a figure's edge into it, so the crop does not cut them off."""
    grown = rect
    for index, line in enumerate(lines):
        if index in captions or _contains_center(rect, line["bbox"]):
            continue
        if len(line["text"].split()) <= 4 and _intersects(line["bbox"], rect, pad=10):
            grown = _union(grown, line["bbox"])
    return grown


def _attach_caption(rect: Rect, lines: List[Dict], taken: set, kind: str) -> Tuple[Optional[str], Optional[str]]:
    """Caption text + normalized label of the nearest caption line below (or above) a figure / table."""
    best, best_distance = None, None
    for index, line in enumerate(lines):
        if index in taken:
            continue
        parsed = caption_label(line["text"])
        if not parsed or (kind == "table") != (parsed[0] == "table"):
            continue
        box = line["bbox"]
        if box[2] < rect[0] or box[0] > rect[2]:
            continue
        if 0 <= box[1] - rect[3] <= 60:
            distance = box[1] - rect[3]
        elif 0 <= rect[1] - box[3] <= 40:
            distance = rect[1] - box[3] + 5  # captions below are more usual
        else:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = index, distance
    if best is None:
        return None, None
    taken.add(best)
    head = lines[best]
    parts = [head["text"]]
    for index in range(best + 1, len(lines)):
        line = lines[index]
        if line["block"] != head["block"] or len(" ".join(parts).split()) > 40:
            break
        taken.add(index)
        parts.append(line["text"])
    return " ".join(parts), caption_label(head["text"])[1]


def _table_caption_near(rect: Rect, lines: List[Dict]) -> bool:
    for line in lines:
        box = line["bbox"]
        if box[2] < rect[0] or box[0] > rect[2]:
            continue
        if -2 <= rect[1] - box[3] <= 40 or -2 <= box[1] - rect[3] <= 40:
            parsed = caption_label(line["text"])
            if parsed and parsed[0] == "table":
                return True
    return False


def _promote_captioned_tables(page: fitz.Page, figures: List[Dict], tables: List[Dict], lines: List[Dict]) -> List[Dict]:
    """Shaded tables without ruling lines look like drawings; a 'Table n' caption next to one says otherwise."""
    kept = []
    for figure in figures:
        rect = figure["bbox"]
        if _table_caption_near(rect, lines):
            try:
                found = page.find_tables(clip=fitz.Rect(rect), strategy="text").tables
            except Exception:  # pylint: disable=broad-except
                found = []
            rows = found[0].extract() if found else []
            if found and _table_is_real(rows):
                body = [[_clean_cell(cell) for cell in row] for row in rows]
                tables.append({"bbox": tuple(found[0].bbox), "header": None, "rows": body})
                continue
        kept.append(figure)
    return kept


def detect_figures_and_tables(page: fitz.Page, lines: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    tables, grid_figures = _find_tables(page)
    candidates = _figure_candidates(page, lines, tables, grid_figures)
    kinds: Dict[Rect, set] = {}
    regions = _drop_banners(merge_rects([rect for rect, _ in candidates], pad=8), lines)
    for region in regions:
        kinds[region] = {kind for rect, kind in candidates if _contains_center(region, rect)}

    page_area = _area(tuple(page.rect))
    caption_lines = {i for i, line in enumerate(lines) if caption_label(line["text"])}
    figures = []
    for region in regions:
        if _is_text_box(region, lines):
            continue
        found = kinds.get(region) or {"drawing"}
        kind = "mixed" if len(found) > 1 else next(iter(found))
        region = _grow_with_labels(region, lines, caption_lines)
        if _area(region) < 0.006 * page_area or (region[3] - region[1]) < 30 or _is_text_box(region, lines):
            continue
        figures.append({"bbox": region, "kind": kind})
    # Drop figures inside other figures (after growing they can nest).
    figures = [f for f in figures if not any(g is not f and _overlap_fraction(f["bbox"], g["bbox"]) > 0.9 and _area(g["bbox"]) > _area(f["bbox"]) for g in figures)]
    figures.sort(key=lambda f: (round(f["bbox"][1]), f["bbox"][0]))

    figures = _promote_captioned_tables(page, figures, tables, lines)

    taken: set = set()
    for table in tables:
        table["caption"], table["label"] = _attach_caption(table["bbox"], lines, taken, "table")
    for figure in figures:
        figure["caption"], figure["label"] = _attach_caption(figure["bbox"], lines, taken, "figure")
    return figures, tables


# ---------------------------------------------------------------------------------------------- formulas


def is_formula_line(text: str, flags: int = 0) -> bool:
    """Display equations and chemical equations: symbol-dense, few prose words."""
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped) > 160:
        return False
    tokens = stripped.split()
    if len(tokens) > 25:
        return False
    if caption_label(stripped):
        return False
    prose = sum(1 for token in tokens if len(token) >= 4 and token.isalpha())
    has_arrow = any(char in ARROWS for char in stripped)
    has_equals = "=" in stripped or "≡" in stripped or "≈" in stripped
    math_chars = sum(1 for char in stripped if _is_math_char(char))
    if prose > 4 or (prose > 2 and prose / len(tokens) > 0.4):
        return False
    if has_arrow and (math_chars >= 2 or "+" in stripped):
        return True
    if has_equals and len(tokens) >= 2 and math_chars + sum(char.isdigit() for char in stripped) >= 2:
        # "x = 5", "F = ma", "v = u + at"; but not code ("int x = 5;") or the tail of a sentence ("BP=36.1°C).")
        if stripped.endswith((";", "{", "}")):
            return False
        return math_chars >= 3 or not stripped.endswith((".", ","))
    return bool(flags & LINE_MATH) and math_chars >= 2


def detect_formulas(lines: List[Dict], figures: List[Dict], tables: List[Dict]) -> List[Dict]:
    blocked = [item["bbox"] for item in figures + tables]
    hits = [
        index
        for index, line in enumerate(lines)
        if not line["flags"] & LINE_MONO
        and is_formula_line(line["text"], line["flags"])
        and not any(_contains_center(rect, line["bbox"]) for rect in blocked)
    ]
    formulas: List[Dict] = []
    for index in hits:
        line = lines[index]
        height = line["bbox"][3] - line["bbox"][1]
        if formulas and index - formulas[-1]["last"] <= 2 and line["bbox"][1] - formulas[-1]["bbox"][3] < 1.5 * height:
            formulas[-1]["bbox"] = _union(formulas[-1]["bbox"], line["bbox"])
            formulas[-1]["text"] += "\n" + line["text"]
            formulas[-1]["last"] = index
        else:
            formulas.append({"bbox": line["bbox"], "text": line["text"], "last": index})
    for formula in formulas:
        formula.pop("last")
    return formulas[:12]


# ---------------------------------------------------------------------------------------------- rendering + cache


def _render(page: fitz.Page, rect: Rect, path: str, dpi: int, pad: float = 4.0, max_side: int = 1800) -> Tuple[int, int]:
    clip = fitz.Rect(rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad) & page.rect
    zoom = dpi / 72
    longest = max(clip.width, clip.height) * zoom
    if longest > max_side:
        zoom *= max_side / longest
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    _atomic_write(path, _webp(image))
    return pixmap.width, pixmap.height


def _webp(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=82, method=4)
    return buffer.getvalue()


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


# PyMuPDF is not thread-safe: searches build missing pages and OCR in worker threads, one render at a time.
_RENDER_LOCK = threading.Lock()


def book_dir(file_hash: str) -> str:
    return os.path.join(settings.asset_dir, file_hash)


def page_json_path(file_hash: str, page_number: int) -> str:
    return os.path.join(book_dir(file_hash), f"p{page_number}.json")


def analyse_page(page: fitz.Page, page_number: int, out_dir: str) -> Dict:
    """Extract and render one page into `out_dir`; returns the JSON-ready description."""
    os.makedirs(out_dir, exist_ok=True)
    width, height = page.rect.width, page.rect.height
    lines = extract_lines(page)
    words = extract_words(page, lines)
    page_area = width * height
    background = any(_area(tuple(fitz.Rect(info["bbox"]) & page.rect)) > 0.7 * page_area for info in page.get_image_info())
    scanned = background and len(words) < 25
    # Legacy-font pages (Kruti Dev, Bijoy) hold glyph soup: their text cannot be highlighted or read as formulas.
    text_usable = not scanned and classify_text_layer(" ".join(line["text"] for line in lines)) != "bad"
    if not text_usable:
        words = []

    figures, tables, formulas = ([], [], []) if scanned else (*detect_figures_and_tables(page, lines), [])
    if text_usable:
        formulas = detect_formulas(lines, figures, tables)
    else:
        tables = []

    stem = f"p{page_number}"
    for prefix, items in (("f", figures), ("t", tables), ("m", formulas)):
        for index, item in enumerate(items, start=1):
            name = f"{stem}-{prefix}{index}.webp"
            dpi = settings.asset_dpi if prefix != "m" else max(settings.asset_dpi, 200)
            item["width"], item["height"] = _render(page, item["bbox"], os.path.join(out_dir, name), dpi)
            item["id"] = f"{prefix}{index}"
            item["image"] = name
            item["bbox"] = _round(item["bbox"])
    page_image = f"{stem}.webp"
    _render(page, tuple(page.rect), os.path.join(out_dir, page_image), settings.page_image_dpi, pad=0, max_side=2200)

    return {
        "version": ASSET_VERSION,
        "page": page_number,
        "width": round(width, 1),
        "height": round(height, 1),
        "scanned": scanned,
        "text_usable": text_usable,
        "image": page_image,
        "lines": [[*_round(line["bbox"]), line["text"], line["block"], line["flags"]] for line in lines],
        "words": [[*_round(word[:4]), word[4], word[5]] for word in words],
        "figures": figures,
        "tables": tables,
        "formulas": formulas,
    }


def _write_page(file_hash: str, page: fitz.Page, page_number: int) -> Dict:
    data = analyse_page(page, page_number, book_dir(file_hash))
    _atomic_write(page_json_path(file_hash, page_number), json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return data


def read_page(file_hash: str, page_number: int) -> Optional[Dict]:
    """Cached page data, or None when missing or built by an older extractor."""
    try:
        with open(page_json_path(file_hash, page_number), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if data.get("version") == ASSET_VERSION else None


def build_pages(pdf_path: str, file_hash: str, pages: Optional[Iterable[int]] = None, force: bool = False, log=None) -> int:
    """Build (missing) page assets for a local PDF; returns how many pages were analysed.

    One bad page is logged and skipped; it does not stop the book.
    """
    built = 0
    with fitz.open(pdf_path) as pdf:
        numbers = list(pages) if pages is not None else range(1, len(pdf) + 1)
        for number in numbers:
            if not 1 <= number <= len(pdf) or (not force and read_page(file_hash, number) is not None):
                continue
            try:
                _write_page(file_hash, pdf[number - 1], number)
                built += 1
            except Exception as exc:  # pylint: disable=broad-except
                if log:
                    log(f"[OSISS] Page assets failed for page {number} of '{os.path.basename(pdf_path)}': {exc}")
    return built


def ensure_pages(file_path: str, file_hash: str, page_numbers: Iterable[int]) -> Dict[int, Dict]:
    """Page data for a stored book (`file_path` as in the database), building missing pages on demand."""
    wanted = sorted({number for number in page_numbers if number and number > 0})
    found = {number: data for number in wanted if (data := read_page(file_hash, number)) is not None}
    missing = [number for number in wanted if number not in found]
    if missing:
        from storage import get_storage  # pylint: disable=import-outside-toplevel

        with get_storage().local_copy(file_path) as local_file, _RENDER_LOCK:
            build_pages(local_file, file_hash, missing, log=print)
        for number in missing:
            data = read_page(file_hash, number)
            if data is not None:
                found[number] = data
    return found


OCR_LANGS = {"bn": "ben+eng", "hi": "hin+eng", "en": "eng"}


def ocr_words(image: Image.Image, scale: float, lang: str) -> Tuple[List[Dict], List[Tuple]]:
    """Lines and words (page_assets format, PDF points) from Tesseract word boxes of a rendered page."""
    import pytesseract  # pylint: disable=import-outside-toplevel

    data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
    lines: List[Dict] = []
    words: List[Tuple] = []
    index_of: Dict[Tuple[int, int, int], int] = {}
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text or float(data["conf"][i]) < 0:
            continue
        x0, y0 = data["left"][i] / scale, data["top"][i] / scale
        x1, y1 = x0 + data["width"][i] / scale, y0 + data["height"][i] / scale
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in index_of:
            index_of[key] = len(lines)
            lines.append({"bbox": (x0, y0, x1, y1), "text": text, "block": data["block_num"][i], "flags": 0})
        else:
            line = lines[index_of[key]]
            line["bbox"] = _union(line["bbox"], (x0, y0, x1, y1))
            line["text"] += " " + text
        words.append((x0, y0, x1, y1, text, index_of[key]))
    return lines, words


def add_ocr_text(file_path: str, file_hash: str, page_number: int, language: str) -> Optional[Dict]:
    """OCR one page that has no usable text layer (scans, text in images, legacy fonts) so the hit can be
    highlighted; the word boxes are cached in the page JSON. Returns the updated page data."""
    data = read_page(file_hash, page_number)
    if data is None or data.get("ocr") or (data.get("words") and data.get("text_usable", True)):
        return data
    from storage import get_storage  # pylint: disable=import-outside-toplevel

    dpi = settings.ocr_highlight_dpi
    with get_storage().local_copy(file_path) as local_file, _RENDER_LOCK:
        with fitz.open(local_file) as pdf:
            pixmap = pdf[page_number - 1].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    lang = OCR_LANGS.get(language, settings.ocr_languages)
    lines, words = ocr_words(image, dpi / 72, lang)
    data["lines"] = [[*_round(line["bbox"]), line["text"], line["block"], 0] for line in lines]
    data["words"] = [[*_round(word[:4]), word[4], word[5]] for word in words]
    data["ocr"] = lang
    _atomic_write(page_json_path(file_hash, page_number), json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return data


def asset_url(file_hash: str, name: str) -> str:
    """Public URL of a cached image, absolute when PUBLIC_BASE_URL is set (like pdf_link)."""
    path = f"/data/assets/{file_hash}/{name}"
    base = settings.public_base_url.rstrip("/")
    return f"{base}{path}" if base else path

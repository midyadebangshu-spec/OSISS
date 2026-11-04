"""Quote integrity: is a quotation in a draft really on the page it cites? Pure, no services.

The comparison ignores differences that come from extraction rather than from the writer: whitespace and line breaks,
hyphens (so "compu-ter" at a line break matches "computer"), curly versus straight quotes, dash variants, case and
Unicode forms. An omission marker (`...`, `…`, `[...]`, `[…]`) or a bracketed insertion (`[the stack]`) splits the
quote into segments that must each appear on the page, in order.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List, Optional

OMISSION_OR_BRACKET = re.compile(r"\[[^\[\]]*\]|\.\.\.|…|\.\s\.\s\.")
PARTIAL_AT = 0.6  # share of the quote found before a miss is reported as "partial" rather than "not_found"
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "′": "'", "″": '"',
                         "–": "-", "—": "-", "‑": "-", "−": "-"})


def key(text: str) -> str:
    """Comparison form: NFKC, case-folded, quotes unified, and no whitespace, hyphens, dashes or invisible joiners."""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).casefold()
    return "".join(
        char for char in text
        if not char.isspace() and unicodedata.category(char) not in ("Pd", "Cf")  # dashes; soft hyphen, zero-width
    )


def segments(quote: str) -> List[str]:
    """The pieces of a quotation between omission markers / bracketed insertions (empty pieces dropped)."""
    pieces = (piece.strip().strip("\"'“”‘’„«»").strip() for piece in OMISSION_OR_BRACKET.split(quote or ""))
    return [piece for piece in pieces if key(piece)]


def _closest(page_tokens: List[str], segment: str) -> Optional[str]:
    """The run of page words most like `segment`, for showing what the page actually says."""
    words = segment.split()
    if not words or not page_tokens:
        return None
    wanted = key(segment)
    size = min(len(words), len(page_tokens))
    best, best_ratio = None, 0.0
    for start in range(0, len(page_tokens) - size + 1):
        window = page_tokens[start : start + size]
        ratio = difflib.SequenceMatcher(None, key(" ".join(window)), wanted, autojunk=False).quick_ratio()
        if ratio > best_ratio:
            best, best_ratio = start, ratio
    if best is None or best_ratio < 0.5:
        return None
    return " ".join(page_tokens[best : best + size])


def verify(quote: str, page_text: str) -> Dict[str, object]:
    """Check a quotation against a page's text.

    Returns {"status": "verbatim" | "partial" | "not_found" | "empty", "matched": 0..1, "closest": text or None}.
    "partial": most of the quote is there but some words differ (`closest` shows the page's wording).
    """
    pieces = segments(quote)
    if not pieces:
        return {"status": "empty", "matched": 0.0, "closest": None}
    page_key = key(page_text)
    page_tokens = (page_text or "").split()
    position, found, total = 0, 0.0, 0
    closest_text: Optional[str] = None
    missed = False
    for piece in pieces:
        piece_key = key(piece)
        total += len(piece_key)
        index = page_key.find(piece_key, position)
        if index >= 0:
            found += len(piece_key)
            position = index + len(piece_key)
            continue
        # Not in order or not at all: how much of it matches the page's most similar run of words.
        missed = True
        near = _closest(page_tokens, piece)
        closest_text = closest_text or near
        if near:
            blocks = difflib.SequenceMatcher(None, key(near), piece_key, autojunk=False).get_matching_blocks()
            found += sum(block.size for block in blocks)
    if not missed:
        return {"status": "verbatim", "matched": 1.0, "closest": None}
    matched = found / total if total else 0.0
    return {"status": "partial" if matched >= PARTIAL_AT else "not_found", "matched": round(matched, 3), "closest": closest_text}

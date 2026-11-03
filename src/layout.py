"""Rich search results: turn pipeline hits + cached page content into typed blocks and a layout.

Model-free and PDF-free (page content comes from `page_assets`, injected as a loader), so it is unit-tested
without services. Everything stays extractive: blocks are pieces of the book (a definition sentence, a list, a code
listing, a figure crop, a table, a formula), never generated text.

Output per result: `kind` (how the UI should lead with it), `blocks` (definition / list / code), `figures`,
`tables`, `formulas` (each marked `primary` when tied to the hit, e.g. referenced as "Fig. 3.2" or captioned with
query words) and `page` (snapshot with highlight rectangles as fractions of the page). `choose_layout` picks the
overall arrangement from the query's intent and what the results contain.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")
# Separators for word comparison; Indic vowel signs are not \w in Python, so split on punctuation instead.
TOKEN_SPLIT_RE = re.compile(r"[\s\.,;:!?()\[\]{}\"'“”‘’।॥|/\\<>=+*&^%$#@~`…–—-]+")

LINE_MONO = 1  # page_assets line flags

STOPWORDS = frozenset(
    """a an the of and or in on to is are was were be been for with as by at from that this these those it its what
    which who whom how why when where do does did can could will would shall should may might define definition
    meaning explain describe write give name state mean means called call about between into than then there their
    কী কি কাকে বলে হয় হল হলো এবং ও কোন কোনো কেন কীভাবে কিভাবে করে এর একটি এক সংজ্ঞা লেখ দাও
    क्या है हैं किसे कहते को की का के में से और एक कैसे क्यों कौन परिभाषा लिखिए बताइए""".split()
)

INTENT_PATTERNS = {
    "visual": re.compile(
        r"\b(diagram|figure|fig|picture|image|draw|sketch|illustrat\w*|structure of|label+ed|graph|chart|map)\b"
        r"|চিত্র|ছবি|গঠন|আঁক|चित्र|आकृति|संरचना|आरेख|ग्राफ",
        re.IGNORECASE,
    ),
    "compare": re.compile(
        r"\b(difference|differences|differentiate|distinguish|compare|comparison|versus|vs\.?|contrast)\b"
        r"|পার্থক্য|তুলনা|অন্তর|अंतर|अन्तर|तुलना|भेद",
        re.IGNORECASE,
    ),
    "procedure": re.compile(
        r"\b(how (to|do|does|can|is|are)|steps?|procedure|process|method|algorithm|stages?)\b"
        r"|কীভাবে|কিভাবে|পদ্ধতি|প্রক্রিয়া|ধাপ|कैसे|विधि|प्रक्रिया|चरण",
        re.IGNORECASE,
    ),
    "definition": re.compile(
        r"^\s*(what (is|are|do you mean by)|define|definition of|meaning of)\b|কাকে বলে|সংজ্ঞা|কী\s*[?？]?\s*$"
        r"|কি\s*[?？]?\s*$|क्या (है|हैं|होता|होती)|परिभाषा|किसे कहते",
        re.IGNORECASE,
    ),
    "code": re.compile(r"\b(program|code|syntax|function|implement\w*|pseudo ?code|write a)\b", re.IGNORECASE),
}

FIGURE_REF_RE = re.compile(
    r"(?:fig(?:ure)?s?\.?|চিত্র|चित्र|आकृति)\s*[.:\-]?\s*([0-9০-৯०-९]+(?:\s*[.\-]\s*[0-9০-৯०-९]+)*)", re.IGNORECASE
)
TABLE_REF_RE = re.compile(
    r"(?:table|সারণি|সারণী|सारणी|तालिका)\s*[.:\-]?\s*([0-9০-৯०-९]+(?:\s*[.\-]\s*[0-9০-৯०-९]+)*)", re.IGNORECASE
)

BULLET_RE = re.compile(
    r"^\s*(?:[•●▪■◆◦‣∙·✓✔➢►▶❖]|[-–*]\s|\(?[0-9০-৯०-९]{1,2}[.)]\s?|\(?[ivx]{1,5}[.)]\s?|\([a-zক-হक-ह]\)\s?|[a-h][.)]\s)",
    re.IGNORECASE,
)
INLINE_ITEM_RE = re.compile(
    r"(?:(?<=\s)|^)(\((?:[0-9০-৯०-९]{1,2}|[ivx]{1,5}|[a-hক-ঙक-ङ])\)|[0-9০-৯०-९]{1,2}[.)](?=\s)|[•●▪■◆])\s*",
    re.IGNORECASE,
)
CODE_LINE_RE = re.compile(
    r"(;\s*$|\{\s*$|^\s*\}|#\s*include|#\s*define|\b(printf|scanf|malloc|free|cout|cin)\s*\(|\bint\s+main\b"
    r"|^\s*(for|while|if|else|switch|case|return|struct|void|int|char|float|double)\b.*[;{(:]\s*$)"
)

DEFINITION_PATTERNS = {
    "en": [
        # "... is called X." (an etymology in brackets may follow: "... called photosynthesis (Photo: light; ...).")
        re.compile(r"(?P<body>.+?)\s+(?:is|are)\s+(?:called|known as|termed)\s+(?:an?\s+|the\s+)?(?P<term>[^.,;:()]{2,60}?)\s*(?:\([^)]*\))?\s*[.]?$", re.I),
        # "... by a process called X."
        re.compile(r"(?P<body>.*?\b(?:a|the)\s+(?:process|phenomenon|method|technique|property|reaction|force|substance|structure|device|unit|type)\s+(?:called|known as|termed))\s+(?:an?\s+|the\s+)?(?P<term>[^.,;:()]{2,60}?)\s*(?:\([^)]*\))?\s*[.]?$", re.I),
        re.compile(
            # "X is a/an/the ..." (not "X are used for ..."), "X refers to / means / is defined as ...".
            r"^(?:an?\s+|the\s+)?(?P<term>[^.,;:]{2,60}?)\s+(?:(?:is|are)\s+(?=(?:a|an|the|one|any|said\s+to\s+be|also\s+called|known\s+as)\b)|refers\s+to|means|(?:is|are)\s+defined\s+as)\s*(?P<body>.+)$",
            re.I,
        ),
    ],
    "bn": [
        re.compile(r"^(?P<body>.+?)(?:কে|-কে)\s+(?P<term>[^।,]{2,40}?)\s+(?:বলে|বলা হয়)"),
        re.compile(r"^(?P<term>[^।,]{2,40}?)\s+(?:হল|হলো|হচ্ছে)\s+(?P<body>.+)$"),
    ],
    "hi": [
        re.compile(r"^(?P<body>.+?)\s+(?:को|को ही)\s+(?P<term>[^।,]{2,40}?)\s+(?:कहते|कहा जाता)"),
        re.compile(r"^(?P<term>[^।,]{2,40}?)\s+(?:वह|वे)\s+(?P<body>.+)$"),
    ],
}


# ---------------------------------------------------------------------------------------------- text helpers


def tokens(text: str) -> List[str]:
    text = unicodedata.normalize("NFC", text or "").lower().translate(DIGIT_MAP)
    return [token for token in TOKEN_SPLIT_RE.split(text) if token]


def content_tokens(text: str) -> Set[str]:
    return {token for token in tokens(text) if token not in STOPWORDS and (len(token) > 2 or token.isdigit())}


def query_intents(query: str) -> List[str]:
    return [name for name, pattern in INTENT_PATTERNS.items() if pattern.search(query or "")]


def language_of(text: str) -> str:
    bengali = sum(1 for char in text if "ঀ" <= char <= "৿")
    devanagari = sum(1 for char in text if "ऀ" <= char <= "ॿ")
    latin = sum(1 for char in text if char.isascii() and char.isalpha())
    top = max(bengali, devanagari, latin)
    if top == 0 or top == latin:
        return "en"
    return "bn" if top == bengali else "hi"


def normalize_label(label: str) -> str:
    return re.sub(r"\s*[.\-]\s*", ".", label.translate(DIGIT_MAP).strip())


def referenced_labels(text: str) -> Tuple[Set[str], Set[str]]:
    """Figure and table numbers mentioned in a passage ("see Fig. 10.3", "সারণি ২.১")."""
    figures = {normalize_label(match) for match in FIGURE_REF_RE.findall(text or "")}
    tables = {normalize_label(match) for match in TABLE_REF_RE.findall(text or "")}
    return figures, tables


# ---------------------------------------------------------------------------------------------- definitions


ARTICLES = {"a", "an", "the"}
PREPOSITIONS = {"of", "in", "on", "at", "for", "to", "by", "with", "from", "into", "about", "between", "through"}


def rejoin_split_words(text: str, query: str) -> str:
    """Undo letter-spacing splits of query words that PDF extraction produces ("A p ointer" -> "A pointer")."""
    for word in content_tokens(query):
        if len(word) < 4:
            continue
        pattern = r"(?<!\w)" + r"\s?".join(re.escape(char) for char in word) + r"(?!\w)"
        text = re.sub(pattern, lambda match: match.group(0).replace(" ", ""), text, flags=re.IGNORECASE)
    return text


def clean_term(term: str) -> str:
    """Drop a heading glued in front of the term: "Introduction A pointer" -> "pointer", "Osmosis Osmosis" -> "Osmosis"."""
    words = [word for index, word in enumerate(term.split()) if index == 0 or word.lower() != term.split()[index - 1].lower()]
    term = " ".join(words)
    for index in range(1, len(words) - 1):
        # Not after a preposition: "principal focus of the concave mirror" is one term, not a heading + term.
        if words[index].lower() in ARTICLES and words[index - 1].lower() not in PREPOSITIONS:
            return " ".join(words[index + 1 :])
    if words and words[0].lower() in ARTICLES and len(words) > 1:
        return " ".join(words[1:])
    return term


def definition_block(quote: str, query: str, intents: Sequence[str]) -> Optional[Dict]:
    """{'type': 'definition', 'term', 'text'} when the quote is a definition of something the query asks about."""
    quote = rejoin_split_words(" ".join((quote or "").split()), query).lstrip("•●▪■◆◦*-– ")
    if not quote or len(quote.split()) > 80:
        return None
    query_words = content_tokens(query)
    for pattern in DEFINITION_PATTERNS.get(language_of(quote), []):
        match = pattern.search(quote)
        if not match:
            continue
        term = clean_term(match.group("term").strip(" -–:"))
        if not term or len(term.split()) > 6:
            continue
        overlap = bool(content_tokens(term) & query_words)
        if overlap or ("definition" in intents and len(term.split()) <= 4):
            return {"type": "definition", "term": term, "text": quote}
    return None


# ---------------------------------------------------------------------------------------------- locating the hit


def locate_words(words: Sequence[Sequence], text: str, max_spread: float = 2.5) -> List[int]:
    """Indices of page words that spell out `text` (best aligned run), or [] if it is not on the page."""
    target = tokens(text)
    if not target or not words:
        return []
    page_tokens: List[str] = []
    owners: List[int] = []
    for index, word in enumerate(words):
        for token in tokens(word[4]):
            page_tokens.append(token)
            owners.append(index)
    if not page_tokens:
        return []
    matcher = SequenceMatcher(None, page_tokens, target, autojunk=False)
    min_block = 1 if len(target) <= 3 else 2
    blocks = [block for block in matcher.get_matching_blocks() if block.size >= min_block]
    if not blocks:
        return []
    # Keep the densest run: blocks whose page positions stay within a window not much longer than the target.
    best: List = []
    for start in range(len(blocks)):
        run = [blocks[start]]
        for block in blocks[start + 1 :]:
            if block.a + block.size - run[0].a > max_spread * len(target) + 5:
                break
            run.append(block)
        if sum(b.size for b in run) > sum(b.size for b in best):
            best = run
    matched = sum(block.size for block in best)
    if matched < max(2, 0.5 * len(target)) and not (len(target) <= 3 and matched == len(target)):
        return []
    first, last = best[0].a, best[-1].a + best[-1].size - 1
    return sorted({owners[i] for i in range(first, last + 1)})


def word_rects(words: Sequence[Sequence], indices: Iterable[int], width: float, height: float) -> List[List[float]]:
    """Merge matched words into one rectangle per text line, as fractions of the page size."""
    by_line: Dict = {}
    for index in indices:
        x0, y0, x1, y1, _, line = words[index][:6]
        key = line if line >= 0 else ("y", round(y0))
        box = by_line.get(key)
        by_line[key] = [x0, y0, x1, y1] if box is None else [min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)]
    rects = sorted(by_line.values(), key=lambda box: (box[1], box[0]))
    return [
        [round(box[0] / width, 4), round(box[1] / height, 4), round(box[2] / width, 4), round(box[3] / height, 4)]
        for box in rects
    ]


def matched_lines(words: Sequence[Sequence], indices: Iterable[int]) -> List[int]:
    return sorted({words[index][5] for index in indices if words[index][5] >= 0})


# ---------------------------------------------------------------------------------------------- lists and code


def _same_column(line: Sequence, x_range: Tuple[float, float]) -> bool:
    return line[0] < x_range[1] and line[2] > x_range[0]


def list_from_lines(lines: Sequence[Sequence], hit_lines: Sequence[int]) -> Optional[Dict]:
    """A bulleted / numbered list on the page that contains the hit."""
    if not hit_lines:
        return None
    x_range = (min(lines[i][0] for i in hit_lines), max(lines[i][2] for i in hit_lines))
    low = max(0, hit_lines[0] - 10)
    high = min(len(lines), hit_lines[-1] + 14)
    window = [i for i in range(low, high) if _same_column(lines[i], x_range)]
    starts = [i for i in window if BULLET_RE.match(lines[i][4]) and len(lines[i][4].strip()) > 2]
    if len(starts) < 3:
        return None
    # The contiguous run of items around the hit: neighbouring item starts at most ~6 lines apart.
    runs: List[List[int]] = [[starts[0]]]
    for index in starts[1:]:
        gap_lines = index - runs[-1][-1]
        height = lines[index][3] - lines[index][1]
        gap = lines[index][1] - lines[runs[-1][-1]][3]
        if gap_lines <= 6 and gap < 4 * max(height, 6):
            runs[-1].append(index)
        else:
            runs.append([index])
    hit = set(hit_lines)
    run = next(
        (r for r in runs if len(r) >= 3 and any(r[0] - 1 <= h <= r[-1] + 6 for h in hit)),
        None,
    )
    if run is None:
        return None
    items: List[str] = []
    for position, start in enumerate(run):
        end = run[position + 1] if position + 1 < len(run) else start + 1
        if position + 1 == len(run):
            # Last item: continuation lines until a gap or a different indent.
            while end < len(lines) and end - start < 6 and _same_column(lines[end], x_range):
                gap = lines[end][1] - lines[end - 1][3]
                if gap > 0.8 * (lines[end][3] - lines[end][1]) or lines[end][0] < lines[start][0] - 2:
                    break
                end += 1
        text = " ".join(lines[i][4].strip() for i in range(start, end) if i in window or i == start)
        items.append(BULLET_RE.sub("", text, count=1).strip())
    ordered = bool(re.match(r"^\s*\(?([0-9০-৯०-९]{1,2}|[ivx]{1,5}|[a-hক-হक-ह])[.)]", lines[run[0]][4], re.I))
    markers = [BULLET_RE.match(lines[start][4]).group(0).strip() for start in run] if ordered else None
    title = None
    before = run[0] - 1
    if before >= 0 and lines[before][4].rstrip().endswith((":", "ঃ", "—")) and len(lines[before][4].split()) <= 20:
        title = lines[before][4].strip()
    keep = [index for index, item in enumerate(items) if item][:15]
    return {
        "type": "list",
        "ordered": ordered,
        "title": title,
        "items": [items[index] for index in keep],
        "markers": [markers[index] for index in keep] if markers else None,
    }


ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}
BULLET_MARKERS = {"•", "●", "▪", "■", "◆"}


def _marker_ordinals(markers: List[str]) -> Optional[List[int]]:
    """Position of each enumerator in its sequence ((a)=1, (iii)=3, ৩=3), or None for bullets."""
    if all(marker in BULLET_MARKERS for marker in markers):
        return None
    if all(marker.isdigit() for marker in markers):
        return [int(marker) for marker in markers]
    if all(marker in ROMAN for marker in markers) and markers[0] == "i":
        return [ROMAN[marker] for marker in markers]
    letters = "abcdefghijklmnopqrstuvwxyz"
    for alphabet in (letters, "কখগঘঙচছজঝঞ", "कखगघङचछजझञ"):
        if all(marker in alphabet for marker in markers):
            return [alphabet.index(marker) + 1 for marker in markers]
    return []


def list_from_text(text: str, quote: str = "") -> Optional[Dict]:
    """Inline enumerations in flattened text: '... are: (i) a (ii) b (iii) c'.

    When the passage holds several enumerations ((a)(b)(c) ... (a)(b)), the run containing the quote is used.
    """
    text = " ".join((text or "").split())
    matches = list(INLINE_ITEM_RE.finditer(text))
    if len(matches) < 3:
        return None
    markers = [match.group(1).strip("().").lower().translate(DIGIT_MAP) for match in matches]
    ordinals = _marker_ordinals(markers)
    if ordinals == []:
        return None
    runs: List[List[int]] = [[0]]
    for index in range(1, len(matches)):
        if ordinals is not None and ordinals[index] != ordinals[index - 1] + 1:
            runs.append([index])
        else:
            runs[-1].append(index)
    runs = [run for run in runs if len(run) >= 3 and (ordinals is None or ordinals[run[0]] == 1)]
    if not runs:
        return None
    anchor = text.find(" ".join(quote.split())[:40]) if quote else -1
    run = next((r for r in runs if anchor >= 0 and matches[r[0]].start() <= anchor + 2 and (
        r[-1] + 1 >= len(matches) or anchor < matches[r[-1] + 1].start())), None) or max(runs, key=len)

    items = []
    for position in run:
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        item = text[matches[position].end() : end].strip(" ;,")
        if not item:
            return None
        items.append(item)
    # The last item runs to the end of the passage; cut it at its first sentence end.
    items[-1] = re.split(r"(?<=[.।?!])\s", items[-1], maxsplit=1)[0]
    if max(len(item.split()) for item in items) > 60:
        return None
    before = text[: matches[run[0]].start()].strip()
    title = re.split(r"(?<=[.।?!])\s", before)[-1] if before else None
    if title and (len(title.split()) > 40 or INLINE_ITEM_RE.search(title)):
        title = None
    ordered = ordinals is not None
    return {
        "type": "list",
        "ordered": ordered,
        "title": title or None,
        "items": items[:15],
        "markers": [matches[position].group(1).strip() for position in run][:15] if ordered else None,
    }


def code_from_lines(lines: Sequence[Sequence], hit_lines: Sequence[int]) -> Optional[Dict]:
    """A code listing around the hit: monospace lines, or lines that read as C-like code."""
    if not hit_lines:
        return None

    def is_code(index: int) -> bool:
        line = lines[index]
        return bool(line[6] & LINE_MONO) or bool(CODE_LINE_RE.search(line[4]))

    center = next((i for i in hit_lines if is_code(i)), None)
    if center is None:
        return None
    start = end = center
    while start > 0 and is_code(start - 1) and center - start < 40:
        start -= 1
    while end + 1 < len(lines) and is_code(end + 1) and end - center < 40:
        end += 1
    span = list(range(start, end + 1))
    if len(span) < 3 or sum(1 for i in span if CODE_LINE_RE.search(lines[i][4]) or lines[i][6] & LINE_MONO) < 3:
        return None
    # Monospace alone is not enough (tables of numbers, diagram labels): real code has code punctuation.
    if sum(1 for i in span if CODE_LINE_RE.search(lines[i][4]) or re.search(r"[;{}()=\[\]]", lines[i][4])) < 2:
        return None
    left = min(lines[i][0] for i in span)
    widths = [(lines[i][2] - lines[i][0]) / max(1, len(lines[i][4])) for i in span]
    char_width = sorted(widths)[len(widths) // 2] or 5.0
    rows = []
    previous_y = None
    for index in span:
        x0, y0, _, _, text = lines[index][:5]
        if previous_y is not None and abs(y0 - previous_y) < 1:
            rows[-1] += " " + text  # same visual line split into two PDF lines
            continue
        rows.append(" " * max(0, round((x0 - left) / char_width)) + text)
        previous_y = y0
    return {"type": "code", "text": "\n".join(rows)}


# ---------------------------------------------------------------------------------------------- assets


def _score_asset(asset: Dict, refs: Set[str], query_words: Set[str], hit_box: Optional[Tuple[float, float]]) -> float:
    score = 0.0
    if asset.get("label") and asset["label"] in refs:
        score += 3.0
    caption_words = content_tokens(asset.get("caption") or "")
    score += min(2.0, len(caption_words & query_words))
    if hit_box is not None:
        top, bottom = asset["bbox"][1], asset["bbox"][3]
        distance = max(0.0, top - hit_box[1], hit_box[0] - bottom)
        if distance < 160:
            score += 0.5
    return score


def _is_strip(item: Dict) -> bool:
    width, height = item.get("width") or 0, item.get("height") or 0
    return bool(width and height) and width / height > 3.2


def _figure_out(asset: Dict, page_number: int, url: Callable[[str], str], score: float) -> Dict:
    return {
        "image": url(asset["image"]),
        "width": asset.get("width"),
        "height": asset.get("height"),
        "caption": asset.get("caption"),
        "label": asset.get("label"),
        "page_number": page_number,
        "primary": score >= 1.0,
    }


def pick_assets(
    page: Dict,
    neighbours: Dict[int, Dict],
    passage: str,
    query: str,
    hit_box: Optional[Tuple[float, float]],
    url: Callable[[str], str],
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """(figures, tables, formulas) worth showing with a hit, most relevant first."""
    figure_refs, table_refs = referenced_labels(passage)
    query_words = content_tokens(query)
    page_number = page["page"]

    figures: List[Tuple[float, Dict]] = []
    for asset in page.get("figures", []):
        score = _score_asset(asset, figure_refs, query_words, hit_box)
        figures.append((score, _figure_out(asset, page_number, url, score)))
    seen_labels = {item["label"] for _, item in figures if item["label"]}
    for number, other in neighbours.items():
        for asset in other.get("figures", []):
            if asset.get("label") in figure_refs and asset["label"] not in seen_labels:
                figures.append((3.0, _figure_out(asset, number, url, 3.0)))
                seen_labels.add(asset["label"])
    figures.sort(key=lambda pair: -pair[0])
    primary = [item for score, item in figures if score >= 1.0][:3]
    # Uncaptioned wide strips are mostly chapter banners or text panels baked into images; leave them out.
    secondary = [item for score, item in figures if score < 1.0 and (item["caption"] or not _is_strip(item))]
    secondary = secondary[: max(0, 3 - len(primary))]

    tables: List[Tuple[float, Dict]] = []
    for number, source in [(page_number, page)] + sorted(neighbours.items()):
        for asset in source.get("tables", []):
            if number != page_number and asset.get("label") not in table_refs:
                continue
            score = _score_asset(asset, table_refs, query_words, hit_box if number == page_number else None)
            if number != page_number:
                score = max(score, 3.0)
            row_words = content_tokens(" ".join(" ".join(row) for row in asset.get("rows", [])[:12]))
            score += min(1.5, 0.5 * len(row_words & query_words))
            item = _figure_out(asset, number, url, score)
            item.update({"rows": asset.get("rows", []), "header": asset.get("header")})
            tables.append((score, item))
    tables.sort(key=lambda pair: -pair[0])

    formulas = []
    passage_words = content_tokens(passage)
    for asset in page.get("formulas", []):
        near = hit_box is not None and max(0.0, asset["bbox"][1] - hit_box[1], hit_box[0] - asset["bbox"][3]) < 120
        shared = len(content_tokens(asset.get("text", "")) & (passage_words | query_words))
        if near or shared >= 2:
            formulas.append(
                {"image": url(asset["image"]), "text": asset.get("text", ""), "width": asset.get("width"), "height": asset.get("height")}
            )
    return primary + secondary, [item for _, item in tables[:2]], formulas[:3]


# ---------------------------------------------------------------------------------------------- one result


def enrich_result(
    item: Dict,
    query: str,
    intents: Sequence[str],
    page: Optional[Dict],
    neighbours: Dict[int, Dict],
    url: Callable[[str], str],
) -> Dict:
    """Blocks, assets and page snapshot for one pipeline hit (`item` as returned by search_and_extract)."""
    quote = item.get("quote") or ""
    paragraph = item.get("matched_paragraph") or ""
    chunk = item.get("chunk_preview") or ""
    blocks: List[Dict] = []
    figures: List[Dict] = []
    tables: List[Dict] = []
    formulas: List[Dict] = []
    page_out = None

    hit_lines: List[int] = []
    hit_box = None
    if page is not None:
        words = page.get("words") or []
        lines = page.get("lines") or []
        width, height = page.get("width") or 1, page.get("height") or 1
        quote_words = locate_words(words, quote)
        context_words = locate_words(words, paragraph) if paragraph and paragraph != quote else []
        if quote_words:
            hit_lines = matched_lines(words, quote_words)
            ys = [words[i][1] for i in quote_words] + [words[i][3] for i in quote_words]
            hit_box = (min(ys), max(ys))
        page_out = {
            "image": url(page["image"]),
            "width": width,
            "height": height,
            "scanned": bool(page.get("scanned")),
            "highlights": word_rects(words, quote_words, width, height),
            "context": word_rects(words, context_words, width, height),
        }
        # Labels inside figures (diagram text, often monospace) are neither code nor list items.
        boxes = [figure["bbox"] for figure in page.get("figures", [])]
        lines = [
            [*line[:4], "", line[5], 0] if any(_inside(box, line) for box in boxes) else line for line in lines
        ]
        code = code_from_lines(lines, hit_lines) if lines else None
        if code:
            blocks.append(code)
        else:
            listing = list_from_lines(lines, hit_lines) if lines else None
            if listing:
                blocks.append(listing)
        figures, tables, formulas = pick_assets(page, neighbours, f"{paragraph} {chunk}", query, hit_box, url)

    if not any(block["type"] in {"list", "code"} for block in blocks):
        listing = list_from_text(paragraph, quote)
        if listing and ("procedure" in intents or len(listing["items"]) >= 3):
            blocks.append(listing)
    definition = definition_block(quote, query, intents)
    if definition:
        blocks.insert(0, definition)

    return {
        "kind": result_kind(blocks, figures, tables, intents),
        "blocks": blocks,
        "figures": figures,
        "tables": tables,
        "formulas": formulas,
        "page": page_out,
    }


def _ocr_missing_text(items, hashes, loaded, ocr_page: PageOcr, log) -> None:
    from concurrent.futures import ThreadPoolExecutor  # pylint: disable=import-outside-toplevel

    jobs = {}
    for position, (item, (page, _)) in enumerate(zip(items, loaded)):
        if needs_ocr(page):
            source = item.get("source", {})
            file_path = source.get("file_path") or ""
            key = (file_path, page["page"])
            jobs.setdefault(key, (hashes[file_path], language_of(item.get("quote") or ""), []))[2].append(position)
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        futures = {key: pool.submit(ocr_page, key[0], file_hash, key[1], lang) for key, (file_hash, lang, _) in jobs.items()}
    for key, future in futures.items():
        try:
            page = future.result()
        except Exception as exc:  # pylint: disable=broad-except
            log(f"[OSISS] OCR for highlighting failed on {key[0]} p{key[1]}: {exc}")
            continue
        if page is not None:
            for position in jobs[key][2]:
                loaded[position] = (page, loaded[position][1])


def _inside(box: Sequence[float], line: Sequence) -> bool:
    cx, cy = (line[0] + line[2]) / 2, (line[1] + line[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def result_kind(blocks: List[Dict], figures: List[Dict], tables: List[Dict], intents: Sequence[str]) -> str:
    types = [block["type"] for block in blocks]
    has_primary_figure = any(figure["primary"] for figure in figures)
    has_primary_table = any(table["primary"] for table in tables)
    if "visual" in intents and has_primary_figure:
        return "figure"
    if "compare" in intents and has_primary_table:
        return "table"
    if "code" in types:
        return "code"
    if "definition" in types:
        return "definition"
    if "list" in types:
        return "list"
    if has_primary_figure:
        return "figure"
    if has_primary_table:
        return "table"
    return "text"


# ---------------------------------------------------------------------------------------------- whole response


def choose_layout(query: str, results: Sequence[Dict], books: Sequence[str]) -> Dict:
    """{'kind': answer|visual|table|compare|steps|empty, 'hero': index, 'intents': [...]}

    `results` are enriched results in rank order and `books` their books (file paths: titles can collide).
    """
    intents = query_intents(query)
    if not results:
        return {"kind": "empty", "hero": None, "intents": intents}

    def first(predicate) -> Optional[int]:
        return next((index for index, result in enumerate(results) if predicate(result)), None)

    figure_hit = first(lambda r: any(f["primary"] for f in r["figures"]))
    any_figure = first(lambda r: bool(r["figures"]))
    table_hit = first(lambda r: any(t["primary"] for t in r["tables"]))
    steps_hit = first(lambda r: r["kind"] in {"list", "code"})

    if "visual" in intents and (figure_hit is not None or any_figure is not None):
        hero = figure_hit if figure_hit is not None else any_figure
        return {"kind": "visual", "hero": hero, "intents": intents}
    if "compare" in intents and table_hit is not None and table_hit <= 2:
        return {"kind": "table", "hero": table_hit, "intents": intents}
    if "compare" in intents and len(set(books[:3])) >= 2:
        return {"kind": "compare", "hero": 0, "intents": intents}
    if "visual" in intents:
        return {"kind": "answer", "hero": 0, "intents": intents}  # no figure to show: lead with the page image
    if steps_hit == 0 or (("procedure" in intents or "code" in intents) and steps_hit is not None and steps_hit <= 1):
        return {"kind": "steps", "hero": steps_hit, "intents": intents}
    return {"kind": "answer", "hero": 0, "intents": intents}


PageLoader = Callable[[str, str, Iterable[int]], Dict[int, Dict]]
# (file_path, file_hash, page_number, language) -> page data with OCR word boxes
PageOcr = Callable[[str, str, int, str], Optional[Dict]]


def needs_ocr(page: Optional[Dict]) -> bool:
    return page is not None and not page.get("ocr") and (not page.get("words") or not page.get("text_usable", True))


def enrich_results(
    query: str,
    items: Sequence[Dict],
    hashes: Dict[str, str],
    load_pages: Optional[PageLoader],
    url_for: Callable[[str, str], str],
    log: Callable[[str], None] = print,
    ocr_page: Optional[PageOcr] = None,
) -> Tuple[List[Dict], Dict]:
    """Enrich every hit; a failure for one book only drops its rich parts. Returns (rich parts, layout).

    With `ocr_page`, hit pages without a usable text layer are OCRed (in parallel) so the quote can be marked.
    """
    intents = query_intents(query)
    loaded: List[Tuple[Optional[Dict], Dict[int, Dict]]] = []
    for item in items:
        source = item.get("source", {})
        file_path = source.get("file_path") or ""
        page_number = int(source.get("page_number") or 0)
        file_hash = hashes.get(file_path)
        page, neighbours = None, {}
        if file_hash and load_pages is not None and page_number > 0:
            try:
                figure_refs, table_refs = referenced_labels(f"{item.get('matched_paragraph', '')} {item.get('chunk_preview', '')}")
                wanted = [page_number] + ([page_number - 1, page_number + 1] if figure_refs or table_refs else [])
                pages = load_pages(file_path, file_hash, wanted)
                page = pages.get(page_number)
                neighbours = {n: data for n, data in pages.items() if n != page_number}
            except Exception as exc:  # pylint: disable=broad-except
                log(f"[OSISS] Page content unavailable for {file_path} p{page_number}: {exc}")
        loaded.append((page, neighbours))

    if ocr_page is not None:
        _ocr_missing_text(items, hashes, loaded, ocr_page, log)

    rich: List[Dict] = []
    for item, (page, neighbours) in zip(items, loaded):
        source = item.get("source", {})
        file_path = source.get("file_path") or ""
        page_number = int(source.get("page_number") or 0)
        file_hash = hashes.get(file_path)
        url = (lambda name, h=file_hash: url_for(h, name)) if file_hash else (lambda name: name)
        try:
            rich.append(enrich_result(item, query, intents, page, neighbours, url))
        except Exception as exc:  # pylint: disable=broad-except
            log(f"[OSISS] Could not build rich result for {file_path} p{page_number}: {exc}")
            rich.append({"kind": "text", "blocks": [], "figures": [], "tables": [], "formulas": [], "page": None})
    books = [item.get("source", {}).get("file_path") or "" for item in items]
    return rich, choose_layout(query, rich, books)

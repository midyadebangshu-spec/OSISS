"""Citations and bibliographies for books: APA 7, MLA 9, Chicago (author-date) and IEEE; BibTeX and RIS.

Pure functions, no services. A book is a dict with `title`, `author` (free text: several authors separated by ";", "&"
or "and"; "Family, Given" or "Given Family"), `publication_year`, `publisher`, `edition`, `isbn` (all optional but the
title). Missing details are left out of the citation and reported by `warnings`, never invented.

Formatted text uses `*italic*` markers (titles); `render` turns them into plain text, HTML, Markdown or LaTeX.
Page numbers cited are the printed ones when known; otherwise the PDF position is cited as "PDF p. N".

Limits (deliberate): books only; no editors, translators or chapters; the title is used as stored (APA's sentence-case
rule is not applied); the publisher's place is not known, so Chicago and IEEE entries omit it.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STYLES = ("apa", "mla", "chicago", "ieee")
STYLE_NAMES = {"apa": "APA 7", "mla": "MLA 9", "chicago": "Chicago (author-date)", "ieee": "IEEE"}

ORG_WORDS = {
    "council", "university", "press", "board", "institute", "department", "ministry", "committee", "association",
    "society", "college", "academy", "commission", "foundation", "organization", "organisation", "authority", "ncert",
    "openstax", "government", "publications", "publishers", "company", "group", "centre", "center", "directorate",
}


@dataclass(frozen=True)
class Author:
    family: str
    given: str = ""
    literal: bool = False  # an organisation or a non-Latin name: never inverted or abbreviated

    @property
    def initials(self) -> str:
        if self.literal or not self.given:
            return ""
        parts = [p for p in re.split(r"[\s]+", self.given.replace("-", " -")) if p]
        out = []
        for part in parts:
            hyphen = part.startswith("-")
            part = part.lstrip("-")
            if part:
                out.append(("-" if hyphen else "") + part[0].upper() + ".")
        return " ".join(out).replace(" -", "-")

    @property
    def natural(self) -> str:
        """Given Family."""
        return self.family if self.literal or not self.given else f"{self.given} {self.family}"

    @property
    def inverted(self) -> str:
        """Family, Given."""
        return self.family if self.literal or not self.given else f"{self.family}, {self.given}"


def _latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(c, "") for c in letters)


def _clean(text: Optional[str]) -> str:
    return " ".join((text or "").replace("*", "").split())


def parse_authors(text: Optional[str]) -> List[Author]:
    """Authors from the free-text field. An organisation (or a name in another script) is kept whole."""
    text = _clean(text)
    if not text:
        return []
    words = {w.lower().strip(".,") for w in text.split()}
    if words & ORG_WORDS or not _latin(text):
        # Organisations and Bengali / Hindi names: split only on explicit separators.
        pieces = [p.strip() for p in re.split(r"\s*;\s*|\s+&\s+", text) if p.strip()]
        return [Author(p, literal=True) for p in pieces]
    pieces = [p.strip() for p in re.split(r"\s*;\s*|\s+&\s+|\s+and\s+", text) if p.strip()]
    authors: List[Author] = []
    for piece in pieces:
        if piece.lower() in ("et al", "et al."):
            continue
        if piece.count(",") == 1:
            family, given = (part.strip() for part in piece.split(","))
            authors.append(Author(family, given) if given else Author(family, literal=True))
            continue
        tokens = piece.split()
        if len(tokens) == 1:
            authors.append(Author(tokens[0], literal=True))
        else:
            authors.append(Author(tokens[-1], " ".join(tokens[:-1])))
    return authors


# --- small helpers ------------------------------------------------------------------------------------------------


def _end(text: str, mark: str = ".") -> str:
    """`text` ending with sentence punctuation (not doubled)."""
    text = text.rstrip()
    return text if not text or text[-1] in ".?!" else text + mark


def _year(book: Dict[str, Any]) -> Optional[int]:
    year = book.get("publication_year")
    return int(year) if year else None


def _edition(book: Dict[str, Any]) -> str:
    """"2nd ed." from "2nd", "2", "Second edition", "2nd ed."; anything else is kept as given."""
    raw = _clean(book.get("edition"))
    if not raw:
        return ""
    cleaned = re.sub(r"(?i)\s*(edition|ed\.?)\s*$", "", raw).strip()
    if re.fullmatch(r"\d+", cleaned):
        n = int(cleaned)
        if n == 1:
            return ""  # a first edition is not stated in a citation
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        cleaned = f"{n}{suffix}"
    return f"{cleaned} ed."


def _page(printed: Optional[str], pdf_page: Optional[int]) -> Optional[Tuple[str, bool]]:
    """(label, is_printed): the printed page number, else the PDF position, else None."""
    if printed:
        return printed, True
    if pdf_page:
        return f"PDF p. {pdf_page}", False
    return None


def warnings(book: Dict[str, Any]) -> List[str]:
    found = []
    if not _clean(book.get("author")):
        found.append("No author: the title stands in for it.")
    if not _year(book):
        found.append("No publication year.")
    if not _clean(book.get("publisher")):
        found.append("No publisher.")
    if book.get("page_offset") is None and not book.get("has_page_labels"):
        found.append("Printed page numbers unknown: pages are cited as PDF positions.")
    return found


# --- author lists -------------------------------------------------------------------------------------------------


def _join(items: Sequence[str], conj: str, oxford: bool = True) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + (", " if oxford else " ") + f"{conj} {items[-1]}"


def _join_inverted(names: Sequence[str]) -> str:
    """MLA / Chicago lists whose first name is inverted: "Smith, Ann, and Bo Jones" (a comma even for two names)."""
    return f"{names[0]}, and {names[1]}" if len(names) == 2 else _join(names, "and")


def _authors_apa(authors: Sequence[Author]) -> str:
    names = [a.family if a.literal else (f"{a.family}, {a.initials}" if a.initials else a.family) for a in authors]
    if len(names) > 20:
        names = names[:19] + ["…", names[-1]]
        return _end(", ".join(names[:-2]) + " " + names[-2] + " " + names[-1])
    if len(names) == 2:
        return _end(f"{names[0]}, & {names[1]}")
    return _end(_join(names, "&"))


def _authors_mla(authors: Sequence[Author]) -> str:
    if len(authors) >= 3:
        return authors[0].inverted + ", et al."
    names = [authors[0].inverted] + [a.natural for a in authors[1:]]
    return _end(_join_inverted(names))


def _authors_chicago(authors: Sequence[Author]) -> str:
    if len(authors) > 10:
        names = [authors[0].inverted] + [a.natural for a in authors[1:7]]
        return _end(", ".join(names) + ", et al.", "")
    names = [authors[0].inverted] + [a.natural for a in authors[1:]]
    return _end(_join_inverted(names))


def _authors_ieee(authors: Sequence[Author]) -> str:
    if len(authors) > 6:
        return _name_ieee(authors[0]) + " et al."
    names = [_name_ieee(a) for a in authors]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return _join(names, "and")


def _name_ieee(author: Author) -> str:
    return author.family if author.literal or not author.initials else f"{author.initials} {author.family}"


def _families(authors: Sequence[Author], style: str) -> str:
    """The author part of an in-text citation."""
    names = [a.family for a in authors]
    if len(names) == 1:
        return names[0]
    if style == "chicago" and len(names) <= 3:
        return _join(names, "and")
    if len(names) == 2:
        return f"{names[0]} {'&' if style == 'apa' else 'and'} {names[1]}"
    return f"{names[0]} et al."


# --- the citer ----------------------------------------------------------------------------------------------------


class Citer:
    """Formats references and in-text citations for a set of books in one style.

    `books` maps a key to a book dict. Call `cite` for each citation in document order (IEEE numbers by first use);
    `bibliography` then lists the cited books.
    """

    def __init__(self, style: str, books: Dict[Any, Dict[str, Any]]):
        if style not in STYLES:
            raise ValueError(f"style must be one of {', '.join(STYLES)}")
        self.style = style
        self.books = books
        self.numbers: Dict[Any, int] = {}
        self.suffix = self._disambiguate() if style in ("apa", "chicago") else {}

    # same first author and year, different books: 2019a, 2019b (by title)
    def _disambiguate(self) -> Dict[Any, str]:
        groups: Dict[Tuple, List[Any]] = {}
        for key, book in self.books.items():
            authors = parse_authors(book.get("author"))
            family = authors[0].family.lower() if authors else _clean(book.get("title")).lower()
            groups.setdefault((family, _year(book)), []).append(key)
        suffix: Dict[Any, str] = {}
        for (_, year), keys in groups.items():
            if year and len(keys) > 1:
                for index, key in enumerate(sorted(keys, key=lambda k: _clean(self.books[k].get("title")).lower())):
                    suffix[key] = "abcdefghijklmnopqrstuvwxyz"[index % 26]
        return suffix

    def _year_label(self, key: Any) -> str:
        year = _year(self.books[key])
        if not year:
            return "n.d."
        return f"{year}{self.suffix.get(key, '')}"

    def number(self, key: Any) -> int:
        if key not in self.numbers:
            self.numbers[key] = len(self.numbers) + 1
        return self.numbers[key]

    # --- reference list entry ---
    def reference(self, key: Any) -> str:
        book = self.books[key]
        authors = parse_authors(book.get("author"))
        title = _clean(book.get("title")) or "Untitled"
        stop = "" if title.endswith(("?", "!", ".")) else "."  # a title already ending in . ? or ! needs no full stop
        italic = f"*{title}*"
        edition = _edition(book)
        publisher = _clean(book.get("publisher"))
        year = self._year_label(key)
        year_plain = str(_year(book) or "")
        style = self.style

        if style == "apa":
            edition_part = f" ({edition})" if edition else ""
            if authors:
                text = f"{_authors_apa(authors)} ({year}). {italic}{edition_part}{stop if edition_part == '' else '.'}"
            else:
                text = f"{italic}{edition_part}{stop if edition_part == '' else '.'} ({year})."
            return text + (f" {_end(publisher)}" if publisher else "")

        if style == "mla":
            tail = ", ".join(part for part in (edition, publisher, year_plain) if part)
            head = f"{_authors_mla(authors)} " if authors else ""
            return f"{head}{italic}{stop}" + (f" {_end(tail)}" if tail else "")

        if style == "chicago":
            dated = year if year.endswith(".") else year + "."  # "n.d." already ends with its full stop
            if authors:
                text = f"{_authors_chicago(authors)} {dated} {italic}{stop}"
            else:
                text = f"{italic}{stop} {dated}"
            if edition:
                text += f" {edition}"
            if publisher:
                text += f" {_end(publisher)}"
            return text

        # IEEE
        lead = f"{_authors_ieee(authors)}, " if authors else ""
        text = f"[{self.number(key)}] {lead}{italic}"
        tail = ", ".join(part for part in (publisher, year_plain) if part)
        if edition:
            text += f", {edition}" + (f" {tail}" if tail else "")  # the edition already ends with a full stop
        elif tail:
            text += f". {tail}"
        return _end(text)

    # --- in-text citation ---
    def cite(self, key: Any, printed: Optional[str] = None, pdf_page: Optional[int] = None) -> str:
        book = self.books[key]
        authors = parse_authors(book.get("author"))
        page = _page(printed, pdf_page)
        style = self.style
        if style == "ieee":
            number = self.number(key)
            if not page:
                return f"[{number}]"
            return f"[{number}, {'p. ' + page[0] if page[1] else page[0]}]"
        who = _families(authors, style) if authors else f"*{_clean(book.get('title')) or 'Untitled'}*"
        year = self._year_label(key)
        if style == "apa":
            inner = f"{who}, {year}"
            if page:
                inner += f", {'p. ' + page[0] if page[1] else page[0]}"
            return f"({inner})"
        if style == "chicago":
            inner = f"{who} {year}"
            if page:
                inner += f", {page[0]}"
            return f"({inner})"
        # MLA: (Author page), with the PDF position set off by a comma
        if not page:
            return f"({who})"
        return f"({who} {page[0]})" if page[1] else f"({who}, {page[0]})"

    def sort_key(self, key: Any) -> Tuple:
        book = self.books[key]
        authors = parse_authors(book.get("author"))
        lead = authors[0].family if authors else _clean(book.get("title"))
        return (lead.lower(), (_year(book) or 9999), _clean(book.get("title")).lower())

    def bibliography(self, keys: Iterable[Any]) -> List[Tuple[Any, str]]:
        """(key, formatted entry) for the cited books: alphabetical, or by number for IEEE."""
        wanted = list(dict.fromkeys(keys))
        if self.style == "ieee":
            wanted.sort(key=self.number)
        else:
            wanted.sort(key=self.sort_key)
        return [(key, self.reference(key)) for key in wanted]


# --- rendering formatted text ---------------------------------------------------------------------------------------

_ITALIC = re.compile(r"\*([^*]+)\*")
_LATEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
                  "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def latex_escape(text: str) -> str:
    return "".join(_LATEX_ESCAPES.get(char, char) for char in text)


def render(text: str, mode: str = "plain") -> str:
    """Turn `*italic*` markers into plain text, HTML, Markdown or LaTeX (escaping the rest)."""
    out: List[str] = []
    position = 0
    for match in _ITALIC.finditer(text):
        out.append(_plain_piece(text[position:match.start()], mode))
        inner = _plain_piece(match.group(1), mode)
        out.append({"plain": inner, "html": f"<em>{inner}</em>", "md": f"*{inner}*", "latex": f"\\textit{{{inner}}}"}[mode])
        position = match.end()
    out.append(_plain_piece(text[position:], mode))
    return "".join(out)


def _plain_piece(text: str, mode: str) -> str:
    if mode == "html":
        return html.escape(text)
    if mode == "latex":
        return latex_escape(text)
    return text


def runs(text: str) -> List[Tuple[str, bool]]:
    """(text, italic) runs, for Word."""
    result: List[Tuple[str, bool]] = []
    position = 0
    for match in _ITALIC.finditer(text):
        if match.start() > position:
            result.append((text[position:match.start()], False))
        result.append((match.group(1), True))
        position = match.end()
    if position < len(text):
        result.append((text[position:], False))
    return result


# --- BibTeX and RIS -------------------------------------------------------------------------------------------------


def bibtex_key(book: Dict[str, Any], fallback: Any) -> str:
    authors = parse_authors(book.get("author"))
    family = authors[0].family if authors else ""
    ascii_family = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", family).encode("ascii", "ignore").decode().lower())
    if not ascii_family:
        return f"book{fallback}"
    title_words = re.findall(r"[a-z]+", unicodedata.normalize("NFKD", _clean(book.get("title"))).encode("ascii", "ignore").decode().lower())
    first = next((w for w in title_words if w not in {"the", "a", "an", "of", "on", "and"}), "")
    return f"{ascii_family}{_year(book) or ''}{first}"


def bibtex(book: Dict[str, Any], key: str) -> str:
    authors = parse_authors(book.get("author"))
    fields = []
    if authors:
        names = " and ".join(f"{{{latex_escape(a.family)}}}" if a.literal else f"{latex_escape(a.family)}, {latex_escape(a.given)}" for a in authors)
        fields.append(("author", names))
    fields.append(("title", "{" + latex_escape(_clean(book.get("title")) or "Untitled") + "}"))
    if _clean(book.get("publisher")):
        fields.append(("publisher", latex_escape(_clean(book["publisher"]))))
    if _year(book):
        fields.append(("year", str(_year(book))))
    edition = _clean(book.get("edition"))
    if edition:
        fields.append(("edition", latex_escape(re.sub(r"(?i)\s*(edition|ed\.?)\s*$", "", edition))))
    if _clean(book.get("isbn")):
        fields.append(("isbn", _clean(book["isbn"])))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@book{{{key},\n{body}\n}}"


def ris(book: Dict[str, Any]) -> str:
    lines = ["TY  - BOOK"]
    for author in parse_authors(book.get("author")):
        lines.append(f"AU  - {author.inverted}")
    lines.append(f"TI  - {_clean(book.get('title')) or 'Untitled'}")
    if _year(book):
        lines.append(f"PY  - {_year(book)}")
    if _clean(book.get("publisher")):
        lines.append(f"PB  - {_clean(book['publisher'])}")
    if _clean(book.get("edition")):
        lines.append(f"ET  - {_clean(book['edition'])}")
    if _clean(book.get("isbn")):
        lines.append(f"SN  - {_clean(book['isbn'])}")
    lines.append("ER  - ")
    return "\n".join(lines)

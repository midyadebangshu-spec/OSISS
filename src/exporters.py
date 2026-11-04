"""Export a rendered draft (drafts.render) as Word (.docx), LaTeX (+ BibTeX, zipped) or Markdown.

Pure formatting over the dict `drafts.render` returns: {"title", "style", "blocks", "bibliography", "books"}. Text carries
`*italic*` markers (see citations.render). Bengali and Devanagari runs get a complex-script font in Word and a font
switch in LaTeX (compile that with XeLaTeX or LuaLaTeX).
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Dict, List, Tuple

import citations

HEADINGS = {"apa": "References", "mla": "Works Cited", "chicago": "References", "ieee": "References"}
HANGING = {"apa", "mla", "chicago"}  # IEEE entries are numbered instead
BENGALI = re.compile(r"[\u0980-\u09ff](?:[\u0980-\u09ff ]*[\u0980-\u09ff])?")  # a run of words, spaces included
DEVANAGARI = re.compile(r"[\u0900-\u097f](?:[\u0900-\u097f ]*[\u0900-\u097f])?")
CS_FONT = "Nirmala UI"  # a Windows font with Bengali and Devanagari; other systems fall back to an installed one


def safe_name(title: str, extension: str) -> str:
    stem = " ".join(re.sub(r'[\\/:*?"<>|\r\n]+', " ", title or "").split())[:80] or "draft"
    return f"{stem}.{extension}"


def export(rendered: Dict[str, Any], fmt: str) -> Tuple[bytes, str, str]:
    """(file bytes, media type, file name)."""
    title = rendered.get("title") or "draft"
    if fmt == "docx":
        return (to_docx(rendered), "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                safe_name(title, "docx"))
    if fmt == "tex":
        return to_latex_zip(rendered), "application/zip", safe_name(title, "zip")
    if fmt == "md":
        return to_markdown(rendered).encode("utf-8"), "text/markdown; charset=utf-8", safe_name(title, "md")
    raise ValueError("format must be docx, tex or md")


# --- Markdown ---------------------------------------------------------------------------------------------------------


def to_markdown(rendered: Dict[str, Any]) -> str:
    lines = [f"# {rendered.get('title') or 'Draft'}", ""]
    for block in rendered["blocks"]:
        text = citations.render(block["text"], "md")
        if block["type"] == "heading":
            lines.append(f"{'#' * (block['level'] + 1)} {text}")
        elif block["type"] == "quote":
            lines.append(f"> {text}")
        else:
            lines.append(text)
        lines.append("")
    if rendered["bibliography"]:
        lines += [f"## {HEADINGS[rendered['style']]}", ""]
        lines += [citations.render(entry["text"], "md") + "\n" for entry in rendered["bibliography"]]
    return "\n".join(lines).rstrip() + "\n"


# --- LaTeX -------------------------------------------------------------------------------------------------------------


def _latex_text(text: str) -> str:
    """Escaped text with italics, and Bengali / Devanagari runs set in their own fonts."""
    out = citations.render(text, "latex")
    out = BENGALI.sub(lambda m: "{\\bengalifont " + m.group(0) + "}", out)
    return DEVANAGARI.sub(lambda m: "{\\devanagarifont " + m.group(0) + "}", out)


def to_latex(rendered: Dict[str, Any]) -> str:
    body: List[str] = []
    uses = {"bengali": False, "devanagari": False}
    for block in rendered["blocks"]:
        uses["bengali"] |= bool(BENGALI.search(block["text"]))
        uses["devanagari"] |= bool(DEVANAGARI.search(block["text"]))
        text = _latex_text(block["text"])
        if block["type"] == "heading":
            command = {1: "section", 2: "subsection", 3: "subsubsection"}[block["level"]]
            body.append(f"\\{command}{{{text}}}\n")
        elif block["type"] == "quote":
            body.append(f"\\begin{{quote}}\n{text}\n\\end{{quote}}\n")
        else:
            body.append(text + "\n")
    for entry in rendered["bibliography"]:
        uses["bengali"] |= bool(BENGALI.search(entry["text"]))
        uses["devanagari"] |= bool(DEVANAGARI.search(entry["text"]))
    head = [
        "% Compile with XeLaTeX or LuaLaTeX (xelatex draft.tex). references.bib holds the same works for BibTeX users.",
        "\\documentclass[12pt]{article}",
        "\\usepackage{fontspec}",
        "\\usepackage[margin=2.5cm]{geometry}",
        "\\setlength{\\parindent}{0pt}\\setlength{\\parskip}{0.7em}",
    ]
    if uses["bengali"]:
        head.append("\\newfontfamily\\bengalifont[Script=Bengali]{Noto Serif Bengali}")
    if uses["devanagari"]:
        head.append("\\newfontfamily\\devanagarifont[Script=Devanagari]{Noto Serif Devanagari}")
    title = _latex_text(rendered.get("title") or "Draft")
    head += [f"\\title{{{title}}}", "\\date{}", "\\begin{document}", "\\maketitle", ""]
    tail: List[str] = []
    if rendered["bibliography"]:
        tail.append(f"\\section*{{{HEADINGS[rendered['style']]}}}")
        hanging = rendered["style"] in HANGING
        for entry in rendered["bibliography"]:
            text = _latex_text(entry["text"])
            tail.append(f"\\hangindent=1.27cm\\hangafter=1 {text}\n" if hanging else text + "\n")
    tail.append("\\end{document}")
    return "\n".join(head + body + tail) + "\n"


def to_latex_zip(rendered: Dict[str, Any]) -> bytes:
    import drafts  # pylint: disable=import-outside-toplevel

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("draft.tex", to_latex(rendered))
        archive.writestr("references.bib", drafts.bibtex_file(rendered.get("books", [])))
    return buffer.getvalue()


# --- Word --------------------------------------------------------------------------------------------------------------


def _add_runs(paragraph, text: str, italic_all: bool = False) -> None:
    from docx.oxml import OxmlElement  # pylint: disable=import-outside-toplevel
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel

    for piece, italic in citations.runs(text):
        run = paragraph.add_run(piece)
        run.italic = True if (italic or italic_all) else None
        if BENGALI.search(piece) or DEVANAGARI.search(piece):
            properties = run._element.get_or_add_rPr()  # pylint: disable=protected-access
            fonts = properties.find(qn("w:rFonts"))
            if fonts is None:
                fonts = OxmlElement("w:rFonts")
                properties.insert(0, fonts)
            fonts.set(qn("w:cs"), CS_FONT)
            fonts.set(qn("w:hint"), "cs")


def to_docx(rendered: Dict[str, Any]) -> bytes:
    from docx import Document  # pylint: disable=import-outside-toplevel
    from docx.shared import Cm, Pt  # pylint: disable=import-outside-toplevel

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    title = document.add_paragraph(style="Title")
    _add_runs(title, rendered.get("title") or "Draft")
    for block in rendered["blocks"]:
        if block["type"] == "heading":
            paragraph = document.add_paragraph(style=f"Heading {block['level']}")
        elif block["type"] == "quote":
            paragraph = document.add_paragraph(style="Quote")
            paragraph.paragraph_format.left_indent = Cm(1.27)
        else:
            paragraph = document.add_paragraph()
        _add_runs(paragraph, block["text"])
    if rendered["bibliography"]:
        document.add_paragraph(HEADINGS[rendered["style"]], style="Heading 1")
        for entry in rendered["bibliography"]:
            paragraph = document.add_paragraph()
            if rendered["style"] in HANGING:
                paragraph.paragraph_format.left_indent = Cm(1.27)
                paragraph.paragraph_format.first_line_indent = Cm(-1.27)
            _add_runs(paragraph, entry["text"])
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()

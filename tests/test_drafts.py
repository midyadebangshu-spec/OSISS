"""Drafts (drafts.py) and exports (exporters.py): pure logic with fake evidence and page text, no services."""

import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import drafts
import exporters

PAGE_233 = "Stack is an important data structure which stores its elements in an ordered manner. A stack is called a LIFO structure."
PAGE_234 = "Queues are different. The topmost plate is removed first because plates are added on top."
TEXTS = {(1, 233): PAGE_233, (1, 234): PAGE_234}
BOOKS = {1: {"id": 1, "title": "Data Structures Using C", "author": "Reema Thareja", "publication_year": 2014,
             "publisher": "Oxford University Press", "edition": "2nd", "page_offset": 12}}
EVIDENCE = {
    12: {"id": 12, "book_id": 1, "page_number": 233, "printed_page": "221", "quote": "Stack is an important data structure",
         "book_title": "Data Structures Using C", "_book_key": 1},
    13: {"id": 13, "book_id": 1, "page_number": 233, "printed_page": "221", "quote": "", "book_title": "x", "_book_key": 1},
}


def page_text(book_id, page):
    return TEXTS.get((book_id, page))


BODY = """# Stacks

A stack is a LIFO structure [@e12]. As Thareja puts it, “Stack is an important data structure which stores its elements in an ordered manner” [@e12].

> Stack is an important data structure which stores its elements in an ordered manner. [@e12]

> A stack is called a LIFO queue. [@e12]

She also says “the topmost plate is removed first because plates are added on top” [@e12] on the next page.
"""


def test_parse_blocks():
    blocks = drafts.parse_blocks("# H\n\nline one\nline two\n\n> q1\n> q2 [@e1]\n\n### Small\n")
    assert [(b["type"], b["text"]) for b in blocks] == [
        ("heading", "H"), ("paragraph", "line one line two"), ("quote", "q1 q2 [@e1]"), ("heading", "Small"),
    ]
    assert blocks[0]["level"] == 1 and blocks[3]["level"] == 3 and blocks[1]["line"] == 3
    assert drafts.parse_blocks("") == []


def test_cited_ids_in_order_of_first_use():
    assert drafts.cited_ids("a [@e5] b [@e2] c [@e5] [@e9]") == [5, 2, 9]
    assert drafts.cited_ids("no citations [@x] [@e] [e3]") == []


def test_find_quotations():
    found = drafts.find_quotations(BODY)
    assert [(f["kind"], f["evidence_id"]) for f in found] == [("inline", 12), ("block", 12), ("block", 12), ("inline", 12)]
    assert found[1]["text"] == "Stack is an important data structure which stores its elements in an ordered manner."
    assert found[1]["line"] == 5
    # a long quotation without a citation is reported; a short quoted term is not
    found = drafts.find_quotations('The word “stack” is short. But “this quoted passage has plenty of words in it” has no source.\n\n> Block without citation')
    assert [(f["kind"], f["evidence_id"]) for f in found] == [("inline", None), ("block", None)]
    assert drafts.find_quotations('She said "this is a straight quoted passage with many words" [@e3].')[0]["evidence_id"] == 3


def test_check_statuses():
    result = drafts.check(BODY, EVIDENCE, page_text)
    statuses = [q["status"] for q in result["quotes"]]
    assert statuses == ["verbatim", "verbatim", "partial", "other_page"]
    assert result["quotes"][2]["closest"].startswith("A stack is called a LIFO")  # shows what the page says
    assert result["quotes"][3]["found_on_page"] == 234
    assert result["counts"] == {"verbatim": 2, "partial": 1, "other_page": 1} and result["problems"] == 2


def test_check_unknown_uncited_and_unchecked():
    body = "> Some quoted words here [@e99]\n\n> Quote with no source at all\n\n“Quoted words that are copied from the saved text” [@e13]"
    result = drafts.check(body, EVIDENCE, lambda b, p: None)
    assert [q["status"] for q in result["quotes"]] == ["unknown_source", "uncited", "unchecked"]
    assert result["quotes"][2]["matches_saved_quote"] is False
    saved = {12: {**EVIDENCE[12], "quote": "Stack is an important data structure"}}
    ok = drafts.check("> Stack is an important data structure [@e12]", saved, lambda b, p: None)
    assert ok["quotes"][0]["status"] == "unchecked" and ok["quotes"][0]["matches_saved_quote"] is True


def test_render_resolves_citations_and_builds_the_bibliography():
    rendered = drafts.render(BODY, "apa", EVIDENCE, BOOKS)
    assert rendered["blocks"][1]["text"].startswith("A stack is a LIFO structure (Thareja, 2014, p. 221).")
    assert rendered["blocks"][3]["text"].endswith("(Thareja, 2014, p. 221)")  # the token becomes the citation
    assert [e["text"] for e in rendered["bibliography"]] == ["Thareja, R. (2014). *Data Structures Using C* (2nd ed.). Oxford University Press."]
    assert rendered["missing"] == [] and rendered["warnings"] == [] and rendered["books"][0]["id"] == 1
    ieee = drafts.render("A [@e12] B [@e12]", "ieee", EVIDENCE, BOOKS)
    assert ieee["blocks"][0]["text"] == "A [1, p. 221] B [1, p. 221]"
    assert ieee["bibliography"][0]["text"].startswith("[1] R. Thareja")


def test_render_reports_missing_sources_and_warnings():
    books = {1: {"title": "T"}}
    ev = {5: {"id": 5, "book_id": 1, "page_number": 3, "printed_page": None, "_book_key": 1}}
    rendered = drafts.render("x [@e5] y [@e77]", "mla", ev, books)
    assert rendered["blocks"][0]["text"] == "x (*T*, PDF p. 3) y [source missing]" and rendered["missing"] == [77]
    assert "No publisher." in rendered["warnings"][0]["messages"]
    with pytest.raises(drafts.DraftError):
        drafts.render("x", "harvard", {}, {})


def test_reference_files():
    books = [{**BOOKS[1]}, {"id": 2, "title": "Other", "author": "Reema Thareja", "publication_year": 2014}]
    bib = drafts.bibtex_file(books)
    assert "@book{thareja2014data," in bib and "@book{thareja2014otherb" not in bib and "thareja2014other," in bib
    assert drafts.ris_file(books).count("TY  - BOOK") == 2 and drafts.bibtex_file([]) == ""
    twin = drafts.bibtex_file([{"id": 1, "title": "Data A", "author": "A. Smith", "publication_year": 2000},
                               {"id": 2, "title": "Data B", "author": "A. Smith", "publication_year": 2000}])
    assert "smith2000data," in twin and "smith2000datab," in twin  # a clash gets a letter
    entries = drafts.reference_entries(books, "apa")
    assert [e["book_id"] for e in entries] == [1, 2] and "<em>Data Structures Using C</em>" in entries[0]["html"]


RENDERED = drafts.render(BODY + "\nস্ট্যাক হল একটি কাঠামো [@e12].\n", "apa", EVIDENCE, BOOKS) | {"title": "My draft: stacks"}


def test_markdown_export():
    text, media, name = exporters.export(RENDERED, "md")
    assert media.startswith("text/markdown") and name == "My draft stacks.md"
    md = text.decode()
    assert md.startswith("# My draft: stacks") and "## Stacks" in md and "> Stack is an important" in md
    assert "## References\n" in md and "*Data Structures Using C*" in md


def test_latex_export_is_a_zip_with_tex_and_bib():
    data, media, name = exporters.export(RENDERED, "tex")
    assert media == "application/zip" and name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["draft.tex", "references.bib"]
        tex = archive.read("draft.tex").decode()
        assert "@book{thareja2014data" in archive.read("references.bib").decode()
    assert r"\section{Stacks}" in tex and r"\begin{quote}" in tex and r"\textit{Data Structures Using C}" in tex
    assert r"\newfontfamily\bengalifont" in tex and r"{\bengalifont স্ট্যাক হল একটি কাঠামো}" in tex
    assert tex.count(r"\begin{document}") == 1 and tex.rstrip().endswith(r"\end{document}")
    plain = exporters.to_latex(drafts.render("R&D costs 5% [@e12]", "apa", EVIDENCE, BOOKS) | {"title": "A_B"})
    assert r"R\&D costs 5\%" in plain and r"\title{A\_B}" in plain and "bengalifont" not in plain


def test_docx_export_text_italics_and_complex_script_font():
    docx = pytest.importorskip("docx")
    data, media, name = exporters.export(RENDERED, "docx")
    assert name == "My draft stacks.docx" and media.endswith("wordprocessingml.document")
    document = docx.Document(io.BytesIO(data))
    texts = [p.text for p in document.paragraphs]
    assert texts[0] == "My draft: stacks" and "Stacks" in texts and "References" in texts
    assert any(t.startswith("Thareja, R. (2014). Data Structures Using C (2nd ed.).") for t in texts)
    entry = next(p for p in document.paragraphs if p.text.startswith("Thareja, R."))
    assert [r.italic for r in entry.runs if r.text == "Data Structures Using C"] == [True]
    assert entry.paragraph_format.first_line_indent < 0  # hanging indent for APA
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode()
    assert re.search(r'w:cs="Nirmala UI"', xml) and "স্ট্যাক" in xml
    ieee = exporters.export(drafts.render("x [@e12]", "ieee", EVIDENCE, BOOKS) | {"title": "t"}, "docx")[0]
    entry = next(p for p in docx.Document(io.BytesIO(ieee)).paragraphs if p.text.startswith("[1]"))
    assert entry.paragraph_format.first_line_indent is None


def test_unknown_format():
    with pytest.raises(ValueError):
        exporters.export(RENDERED, "pdf")

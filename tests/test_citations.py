"""Citations (citations.py): pure formatting, no services. Expected strings follow each style guide's book examples."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import citations as c

THAREJA = {"title": "Data Structures Using C", "author": "Reema Thareja", "publication_year": 2014,
           "publisher": "Oxford University Press", "edition": "2nd"}
MORIN = {"title": "Open Data Structures", "author": "Pat Morin", "publication_year": 2013, "publisher": "AU Press"}
CLRS = {"title": "Introduction to Algorithms", "publication_year": 2022, "publisher": "MIT Press", "edition": "4",
        "author": "Cormen, Thomas H.; Leiserson, Charles E.; Rivest, Ronald L.; Stein, Clifford"}
OPENSTAX = {"title": "Physics", "author": "OpenStax", "publication_year": 2020}
BENGALI = {"title": "বিজ্ঞানের ইতিহাস", "author": "রবীন্দ্রনাথ ঠাকুর", "publication_year": 1930}
BOOKS = {1: THAREJA, 2: MORIN, 3: CLRS, 4: OPENSTAX, 5: BENGALI}


def ref(style, key):
    return c.render(c.Citer(style, BOOKS).reference(key))


def test_authors_are_parsed():
    assert c.parse_authors("Reema Thareja") == [c.Author("Thareja", "Reema")]
    assert c.parse_authors("H C Verma")[0].initials == "H. C."
    assert c.parse_authors("Verma, H. C.") == [c.Author("Verma", "H. C.")]
    assert [a.family for a in c.parse_authors("Ada Lovelace and Charles Babbage")] == ["Lovelace", "Babbage"]
    assert [a.family for a in c.parse_authors("A. Author; B. Writer")] == ["Author", "Writer"]
    assert c.parse_authors("OpenStax")[0].literal
    assert c.parse_authors("National Council of Educational Research and Training") == [
        c.Author("National Council of Educational Research and Training", literal=True)
    ]  # an organisation is not split on "and"
    assert c.parse_authors("রবীন্দ্রনাথ ঠাকুর")[0].literal  # never inverted or abbreviated
    assert c.parse_authors("") == [] and c.parse_authors(None) == []
    assert c.Author("Jean-Paul", "Anne-Marie").initials == "A.-M."


def test_apa_references():
    assert ref("apa", 1) == "Thareja, R. (2014). Data Structures Using C (2nd ed.). Oxford University Press."
    assert ref("apa", 2) == "Morin, P. (2013). Open Data Structures. AU Press."
    assert ref("apa", 3) == "Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. (2022). Introduction to Algorithms (4th ed.). MIT Press."
    assert ref("apa", 4) == "OpenStax. (2020). Physics."
    assert ref("apa", 5) == "রবীন্দ্রনাথ ঠাকুর. (1930). বিজ্ঞানের ইতিহাস."
    two = {"title": "T", "author": "A. Smith; B. Jones", "publication_year": 2001}
    assert c.render(c.Citer("apa", {1: two}).reference(1)) == "Smith, A., & Jones, B. (2001). T."
    assert c.render(c.Citer("apa", {1: {"title": "What is a stack?", "publication_year": 2001}}).reference(1)) == "What is a stack? (2001)."
    assert c.Citer("apa", {1: {"title": "T"}}).reference(1) == "*T*. (n.d.)."


def test_a_title_ending_in_a_full_stop_is_not_given_another():
    dotted = {"title": "Data Structures Using C, 2nd ed.", "author": "Reema Thareja", "publication_year": 2014, "publisher": "P"}
    for style, expected in (("apa", "Thareja, R. (2014). Data Structures Using C, 2nd ed. P."),
                            ("mla", "Thareja, Reema. Data Structures Using C, 2nd ed. P, 2014."),
                            ("chicago", "Thareja, Reema. 2014. Data Structures Using C, 2nd ed. P.")):
        assert c.render(c.Citer(style, {1: dotted}).reference(1)) == expected


def test_chicago_undated_book_has_one_full_stop():
    undated = {"title": "T", "author": "Ann Smith", "publisher": "P"}
    assert c.render(c.Citer("chicago", {1: undated}).reference(1)) == "Smith, Ann. n.d. T. P."
    assert c.render(c.Citer("chicago", {1: {"title": "T"}}).reference(1)) == "T. n.d."


def test_mla_references():
    assert ref("mla", 1) == "Thareja, Reema. Data Structures Using C. 2nd ed., Oxford University Press, 2014."
    assert ref("mla", 2) == "Morin, Pat. Open Data Structures. AU Press, 2013."
    assert ref("mla", 3) == "Cormen, Thomas H., et al. Introduction to Algorithms. 4th ed., MIT Press, 2022."
    two = {"title": "T", "author": "Ann Smith; Bo Jones", "publication_year": 2001, "publisher": "P"}
    assert c.render(c.Citer("mla", {1: two}).reference(1)) == "Smith, Ann, and Bo Jones. T. P, 2001."


def test_chicago_references():
    assert ref("chicago", 1) == "Thareja, Reema. 2014. Data Structures Using C. 2nd ed. Oxford University Press."
    assert ref("chicago", 3) == "Cormen, Thomas H., Charles E. Leiserson, Ronald L. Rivest, and Clifford Stein. 2022. Introduction to Algorithms. 4th ed. MIT Press."
    assert ref("chicago", 4) == "OpenStax. 2020. Physics."


def test_ieee_references_number_by_first_citation():
    citer = c.Citer("ieee", BOOKS)
    assert citer.cite(2, "10") == "[1, p. 10]" and citer.cite(1) == "[2]" and citer.cite(2) == "[1]"
    assert citer.cite(5, None, 7) == "[3, PDF p. 7]"
    entries = [c.render(text) for _, text in citer.bibliography([1, 2, 5])]
    assert entries == [
        "[1] P. Morin, Open Data Structures. AU Press, 2013.",
        "[2] R. Thareja, Data Structures Using C, 2nd ed. Oxford University Press, 2014.",
        "[3] রবীন্দ্রনাথ ঠাকুর, বিজ্ঞানের ইতিহাস. 1930.",
    ]
    long = {"title": "T", "author": "A One; B Two; C Three; D Four; E Five; F Six; G Seven", "publication_year": 2000, "publisher": "P"}
    assert c.render(c.Citer("ieee", {1: long}).reference(1)) == "[1] A. One et al., T. P, 2000."


def test_in_text_citations():
    apa, mla, chi = (c.Citer(s, BOOKS) for s in ("apa", "mla", "chicago"))
    assert apa.cite(1, "221") == "(Thareja, 2014, p. 221)"
    assert apa.cite(1, None, 233) == "(Thareja, 2014, PDF p. 233)"
    assert apa.cite(1) == "(Thareja, 2014)"
    assert apa.cite(3, "5") == "(Cormen et al., 2022, p. 5)"
    assert mla.cite(1, "221") == "(Thareja 221)" and mla.cite(1, None, 233) == "(Thareja, PDF p. 233)"
    assert chi.cite(1, "221") == "(Thareja 2014, 221)" and chi.cite(1) == "(Thareja 2014)"
    two = {"title": "T", "author": "A. Smith; B. Jones", "publication_year": 2001}
    assert c.Citer("apa", {1: two}).cite(1, "3") == "(Smith & Jones, 2001, p. 3)"
    assert c.Citer("mla", {1: two}).cite(1, "3") == "(Smith and Jones 3)"
    assert c.Citer("chicago", {1: two}).cite(1) == "(Smith and Jones 2001)"
    assert c.render(c.Citer("apa", {1: {"title": "Untitled work"}}).cite(1)) == "(Untitled work, n.d.)"
    assert apa.cite(1, "xii") == "(Thareja, 2014, p. xii)"


def test_same_author_and_year_get_letters_in_apa_and_chicago():
    books = {1: {"title": "Beta", "author": "A. Smith", "publication_year": 2020},
             2: {"title": "Alpha", "author": "A. Smith", "publication_year": 2020},
             3: {"title": "Gamma", "author": "A. Smith", "publication_year": 2019}}
    apa = c.Citer("apa", books)
    assert apa.cite(2) == "(Smith, 2020a)" and apa.cite(1) == "(Smith, 2020b)" and apa.cite(3) == "(Smith, 2019)"
    assert [text.split(" (")[1][:6] for _, text in apa.bibliography([1, 2, 3])] == ["2019).", "2020a)", "2020b)"]
    assert c.Citer("mla", books).cite(1) == "(Smith)"  # MLA does not use year letters


def test_editions_and_missing_details():
    assert c._edition({"edition": "2"}) == "2nd ed." and c._edition({"edition": "Third edition"}) == "Third ed."
    assert c._edition({"edition": "1"}) == "" and c._edition({"edition": "11th ed."}) == "11th ed."
    assert c._edition({"edition": "22"}) == "22nd ed." and c._edition({}) == ""
    assert c.warnings({"title": "T"}) == [
        "No author: the title stands in for it.", "No publication year.", "No publisher.",
        "Printed page numbers unknown: pages are cited as PDF positions.",
    ]
    assert c.warnings({**THAREJA, "page_offset": 12}) == []
    assert c.warnings({**THAREJA, "has_page_labels": True}) == []


def test_asterisks_in_data_cannot_break_italics():
    book = {"title": "A *starred* title", "author": "A * Writer", "publication_year": 2000}
    text = c.Citer("apa", {1: book}).reference(1)
    assert c.render(text, "plain") == "Writer, A. (2000). A starred title."


def test_render_modes():
    text = "Smith, A. (2000). *Fish & Chips_1*."
    assert c.render(text, "plain") == "Smith, A. (2000). Fish & Chips_1."
    assert c.render(text, "html") == "Smith, A. (2000). <em>Fish &amp; Chips_1</em>."
    assert c.render(text, "md") == text
    assert c.render(text, "latex") == r"Smith, A. (2000). \textit{Fish \& Chips\_1}."
    assert c.runs(text) == [("Smith, A. (2000). ", False), ("Fish & Chips_1", True), (".", False)]


def test_bibtex_and_ris():
    assert c.bibtex_key(THAREJA, 1) == "thareja2014data" and c.bibtex_key(BENGALI, 5) == "book5"
    assert c.bibtex({**THAREJA, "isbn": "9780306406157"}, "thareja2014data") == (
        "@book{thareja2014data,\n  author = {Thareja, Reema},\n  title = {{Data Structures Using C}},\n"
        "  publisher = {Oxford University Press},\n  year = {2014},\n  edition = {2nd},\n  isbn = {9780306406157}\n}"
    )
    assert "author = {{OpenStax}}" in c.bibtex(OPENSTAX, "x")
    assert "title = {{A \\& B}}" in c.bibtex({"title": "A & B"}, "x")
    assert c.ris(CLRS).splitlines()[:3] == ["TY  - BOOK", "AU  - Cormen, Thomas H.", "AU  - Leiserson, Charles E."]
    assert c.ris(THAREJA).splitlines()[-3:] == ["PB  - Oxford University Press", "ET  - 2nd", "ER  - "]
    assert c.ris(THAREJA).endswith("ER  - ")


def test_unknown_style():
    with pytest.raises(ValueError):
        c.Citer("harvard", {})

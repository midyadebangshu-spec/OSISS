"""Printed page numbers and ISBN checks (page_labels.py): pure logic, no services."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import page_labels as pl


def test_isbn_checks():
    assert pl.clean_isbn("978-0-306-40615-7") == "9780306406157"
    assert pl.clean_isbn("ISBN 0-306-40615-2") == "0306406152"
    assert pl.clean_isbn("080442957X") == "080442957X"  # X check digit
    assert pl.clean_isbn("978-0-306-40615-8") is None  # wrong check digit
    assert pl.clean_isbn("12345") is None
    assert pl.clean_isbn("") is None


def test_roman_and_useful_labels():
    assert [pl.to_roman(n) for n in (1, 4, 9, 14, 2024)] == ["i", "iv", "ix", "xiv", "mmxxiv"]
    assert pl.useful_labels(["1", "2", "3"]) is None  # says nothing the page position does not
    assert pl.useful_labels(["", "", ""]) is None
    assert pl.useful_labels(["i", "ii", "1", "2"]) == ["i", "ii", "1", "2"]


def test_printed_label_precedence():
    labels = ["i", "ii", "1", "2"]
    assert pl.printed_label(3, None, labels) == "1"
    assert pl.printed_label(3, 2, labels) == "1"  # an admin offset and PDF labels agree here
    assert pl.printed_label(3, 0, labels) == "3"  # the explicit offset wins over the PDF's labels
    assert pl.printed_label(2, 2, None) is None  # before the first numbered page
    assert pl.printed_label(12, 10, None) == "2"
    assert pl.printed_label(5, None, None) is None and pl.printed_label(9, None, labels) is None


def test_citation_page():
    assert pl.citation_page(233, "233") == "233"
    assert pl.citation_page(245, "233") == "233 (PDF 245)"
    assert pl.citation_page(7, "vii") == "vii (PDF 7)"
    assert pl.citation_page(7, None) == "7"


def test_guess_offset_needs_agreement():
    agree = {20: [8], 30: [18, 400], 40: [28], 50: [38], 60: [48]}
    assert pl.guess_offset(agree) == 12
    scattered = {20: [8], 30: [5], 40: [99], 50: [38], 60: [3]}
    assert pl.guess_offset(scattered) is None
    assert pl.guess_offset({20: [8], 30: [18]}) is None  # too few pages to trust
    assert pl.guess_offset({page: [] for page in range(10, 90, 10)}) is None  # no numbers at all
    mostly = {20: [9], 30: [19], 40: [29], 50: [39], 60: [49], 70: [200]}
    assert pl.guess_offset(mostly) == 11  # one stray page out of six still agrees (5/6 >= 0.7)


def test_sample_pages_stay_in_the_body():
    pages = pl.sample_pages(500)
    assert pages[0] >= 40 and pages[-1] <= 480 and len(pages) <= 14
    assert pl.sample_pages(3) == [1, 2, 3]


def test_labels_and_margin_numbers_from_a_real_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = str(tmp_path / "numbered.pdf")
    pdf = fitz.open()
    for index in range(12):
        page = pdf.new_page(width=300, height=400)
        page.insert_text((40, 120), "Body text of the chapter goes here.", fontsize=11)
        page.insert_text((140, 385), str(index + 1 - 2) if index >= 2 else "x", fontsize=9)  # printed footer
    pdf.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                         {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}])
    pdf.save(path)
    with fitz.open(path) as doc:
        labels = pl.read_pdf_labels(doc)
        assert labels[:3] == ["i", "ii", "1"] and labels[-1] == "10"
        assert pl.printed_label(3, None, labels) == "1"
        assert pl.margin_numbers(doc, 6) == [4]  # footer "4" on PDF page 6, not the body text
        assert pl.suggest_offset(doc) == 2


def test_admin_validates_the_new_metadata_fields():
    admin = pytest.importorskip("admin")
    cleaned = admin.clean_metadata(
        {"publisher": "  Oxford   Univ Press ", "edition": "2nd", "isbn": "978-0-306-40615-7", "page_offset": "12"}, partial=True
    )
    assert cleaned == {"publisher": "Oxford Univ Press", "edition": "2nd", "isbn": "9780306406157", "page_offset": 12}
    assert admin.clean_metadata({"isbn": "", "page_offset": None, "publisher": ""}, partial=True) == {
        "isbn": None, "page_offset": None, "publisher": None,
    }
    for bad in ({"isbn": "978-0-306-40615-8"}, {"page_offset": "x"}, {"page_offset": 5000}, {"publisher": "x" * 501}):
        with pytest.raises(admin.AdminError):
            admin.clean_metadata(bad, partial=True)

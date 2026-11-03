"""Page content extraction on a synthetic PDF (no services): figures, captions, tables, formulas, code, cache."""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz
import pytest
from PIL import Image

import page_assets
from config import settings

PROSE = [
    "Light travels in straight lines and is reflected by smooth surfaces such as mirrors.",
    "The angle of incidence is always equal to the angle of reflection for a plane mirror.",
    "Rough surfaces scatter the light in many different directions instead of one direction.",
    "This is called diffuse reflection and it lets us see objects from every side of a room.",
]


def _png(width=240, height=160):
    image = Image.new("RGB", (width, height), (40, 120, 200))
    for x in range(0, width, 20):
        for y in range(height):
            image.putpixel((x, y), (250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_pdf(path):
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    y = 60
    for line in PROSE:
        page.insert_text((50, y), line, fontname="helv", fontsize=10)
        y += 14
    # Raster figure + caption.
    page.insert_image(fitz.Rect(60, 140, 300, 300), stream=_png())
    page.insert_text((60, 318), "Fig. 2.1 Reflection from a plane mirror", fontname="helv", fontsize=9)
    # Vector diagram (circle, lines, short labels) + caption.
    shape = page.new_shape()
    shape.draw_circle((450, 210), 50)
    shape.draw_line((380, 150), (520, 270))
    shape.draw_line((380, 270), (520, 150))
    shape.finish(color=(0, 0, 0), width=1.2)
    shape.commit()
    page.insert_text((392, 145), "lens", fontname="helv", fontsize=8)
    page.insert_text((360, 318), "Figure 2.2 Rays through a lens", fontname="helv", fontsize=9)
    # Ruled table with a caption above.
    page.insert_text((60, 352), "Table 2.1 Refractive index", fontname="helv", fontsize=9)
    rows = [["Medium", "Index", "Speed"], ["Air", "1.0003", "fast"], ["Water", "1.33", "slower"], ["Glass", "1.5", "slowest"]]
    x_edges, top, height = [60, 180, 280, 380], 360, 18
    shape = page.new_shape()
    for i in range(len(rows) + 1):
        shape.draw_line((x_edges[0], top + i * height), (x_edges[-1], top + i * height))
    for x in x_edges:
        shape.draw_line((x, top), (x, top + len(rows) * height))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x_edges[c] + 4, top + r * height + 13), cell, fontname="helv", fontsize=9)
    # Display formula.
    page.insert_text((240, 470), "F = m × a", fontname="helv", fontsize=12)
    # Code listing in a monospace font.
    code = ["#include <stdio.h>", "int main(void) {", "    printf(\"hi\");", "    return 0;", "}"]
    for i, line in enumerate(code):
        page.insert_text((60, 510 + i * 12), line, fontname="cour", fontsize=9)
    # A framed prose box (activity / note): not a figure.
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(50, 600, 545, 680))
    shape.finish(color=(0, 0, 0), fill=(0.9, 0.95, 1.0))
    shape.commit()
    for i, line in enumerate(PROSE[:4]):
        page.insert_text((58, 618 + i * 15), line, fontname="helv", fontsize=9)
    # Page 2: a scan (full-page image, no text layer).
    scan = pdf.new_page(width=595, height=842)
    scan.insert_image(scan.rect, stream=_png(600, 840))
    pdf.save(path)
    pdf.close()


@pytest.fixture
def built(tmp_path):
    saved = settings.asset_dir
    object.__setattr__(settings, "asset_dir", str(tmp_path / "assets"))
    pdf_path = str(tmp_path / "book.pdf")
    make_pdf(pdf_path)
    file_hash = "a" * 64
    assert page_assets.build_pages(pdf_path, file_hash) == 2
    yield file_hash, pdf_path
    object.__setattr__(settings, "asset_dir", saved)


def test_figures_with_captions_and_no_text_box(built):
    file_hash, _ = built
    page = page_assets.read_page(file_hash, 1)
    labels = sorted(figure["label"] for figure in page["figures"])
    assert labels == ["2.1", "2.2"], page["figures"]
    kinds = {figure["label"]: figure["kind"] for figure in page["figures"]}
    assert kinds["2.1"] == "image"
    lens = next(figure for figure in page["figures"] if figure["label"] == "2.2")
    assert lens["bbox"][1] <= 140, "the short label above the drawing is pulled into the crop"
    for figure in page["figures"]:
        assert figure["bbox"][3] < 600, "the framed prose box is not a figure"
        assert os.path.isfile(os.path.join(page_assets.book_dir(file_hash), figure["image"]))


def test_table_rows_and_caption(built):
    page = page_assets.read_page(built[0], 1)
    assert len(page["tables"]) == 1
    table = page["tables"][0]
    assert table["label"] == "2.1"
    cells = [cell for row in ([table["header"]] if table["header"] else []) + table["rows"] for cell in row]
    assert "Water" in cells and "1.33" in cells


def test_formula_and_code_lines(built):
    page = page_assets.read_page(built[0], 1)
    assert [formula["text"] for formula in page["formulas"]] == ["F = m × a"]
    mono = [line[4] for line in page["lines"] if line[6] & page_assets.LINE_MONO]
    assert "int main(void) {" in mono
    assert page["text_usable"] and page["words"]


def test_scanned_page_has_no_figures(built):
    page = page_assets.read_page(built[0], 2)
    assert page["scanned"] and page["figures"] == [] and page["words"] == []
    assert os.path.isfile(os.path.join(page_assets.book_dir(built[0]), page["image"]))


def test_cache_versioning_and_on_demand_build(built, monkeypatch):
    file_hash, pdf_path = built
    assert page_assets.build_pages(pdf_path, file_hash) == 0  # all cached
    monkeypatch.setattr(page_assets, "ASSET_VERSION", page_assets.ASSET_VERSION + 1)
    assert page_assets.read_page(file_hash, 1) is None  # stale extractor version
    pages = page_assets.ensure_pages(pdf_path, file_hash, [1, 99])
    assert list(pages) == [1]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Fig. 10.3 Human heart", ("figure", "10.3")),
        ("চিত্র ১০.৩ : মানব হৃৎপিণ্ড", ("figure", "10.3")),
        ("सारणी 2.2 कुछ पदार्थ", ("table", "2.2")),
        ("Figures show that", None),
    ],
)
def test_caption_label(text, expected):
    assert page_assets.caption_label(text) == expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("F = m × a", True),
        ("Zn + H2SO4 → ZnSO4 + H2", True),
        ("v = u + at", True),
        ("int x = 5;", False),
        ("72, BP=36.1°C).", False),
        ("The force is equal to the mass of the body times its acceleration = ma", False),
    ],
)
def test_formula_lines(line, expected):
    assert page_assets.is_formula_line(line) is expected


def test_merge_rects():
    assert page_assets.merge_rects([(0, 0, 10, 10), (12, 0, 20, 10), (100, 100, 110, 110)], pad=4) == [
        (0, 0, 20, 10),
        (100, 100, 110, 110),
    ]

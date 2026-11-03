"""Rich result building (layout.py): no services, no PDFs; page data is written by hand in page_assets' format."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import layout


def line(x0, y0, text, flags=0, block=0, width=None):
    return [x0, y0, x0 + (width or 5.5 * len(text)), y0 + 10, text, block, flags]


def words_for(lines):
    words = []
    for index, (x0, y0, _, y1, text, _, _) in enumerate(lines):
        x = x0
        for word in text.split():
            words.append([x, y0, x + 5.5 * len(word), y1, word, index])
            x += 5.5 * (len(word) + 1)
    return words


def page_data(lines, figures=(), tables=(), formulas=(), number=5):
    return {
        "page": number, "width": 600.0, "height": 800.0, "image": f"p{number}.webp", "scanned": False,
        "lines": lines, "words": words_for(lines), "figures": list(figures), "tables": list(tables),
        "formulas": list(formulas),
    }


def url(name):
    return f"/data/assets/h/{name}"


@pytest.mark.parametrize(
    "query,intents",
    [
        ("Draw a labelled diagram of the human heart", {"visual"}),
        ("difference between mitosis and meiosis", {"compare"}),
        ("How does a stack work?", {"procedure"}),
        ("What is photosynthesis?", {"definition"}),
        ("আলোর প্রতিফলন কাকে বলে?", {"definition"}),
        ("ऊष्मागतिकी की परिभाषा क्या है?", {"definition"}),
        ("write a program to reverse a linked list", {"code"}),
        ("হৃৎপিণ্ডের গঠন চিত্র", {"visual"}),
    ],
)
def test_query_intents(query, intents):
    assert set(layout.query_intents(query)) == intents


@pytest.mark.parametrize(
    "quote,query,term",
    [
        ("Photosynthesis is the process by which green plants make food from sunlight.", "what is photosynthesis", "Photosynthesis"),
        ("The bending of light when it passes from one medium to another is called refraction.", "define refraction", "refraction"),
        ("আলোর এক মাধ্যম থেকে অন্য মাধ্যমে যাওয়ার সময় দিক পরিবর্তনকে আলোর প্রতিসরণ বলে।", "আলোর প্রতিসরণ কাকে বলে", "আলোর প্রতিসরণ"),
        ("ऊष्मा और कार्य के संबंध के अध्ययन को ऊष्मागतिकी कहते हैं।", "ऊष्मागतिकी क्या है", "ऊष्मागतिकी"),
    ],
)
def test_definition_block(quote, query, term):
    block = layout.definition_block(quote, query, layout.query_intents(query))
    assert block == {"type": "definition", "term": term, "text": quote}


def test_definition_repairs_split_words_and_drops_headings():
    block = layout.definition_block("Introduction A p ointer is a derived data type in C.", "What is a pointer", ["definition"])
    assert block == {"type": "definition", "term": "pointer", "text": "Introduction A pointer is a derived data type in C."}


def test_definition_needs_a_link_to_the_query():
    assert layout.definition_block("A queue is a linear data structure.", "how does a stack work", ["procedure"]) is None


def test_list_from_text_numbered_and_roman():
    listing = layout.list_from_text("The main parts are: (i) the cornea (ii) the lens (iii) the retina. Light enters first.")
    assert listing["title"] == "The main parts are:"
    assert listing["items"] == ["the cornea", "the lens", "the retina."]
    assert listing["markers"] == ["(i)", "(ii)", "(iii)"]
    assert layout.list_from_text("Steps 1. heat 3. cool 4. stir") is None  # numbers out of sequence
    assert layout.list_from_text("Only one (i) item here") is None
    mixed = layout.list_from_text(
        "Examples: (a) jobs queue up (b) calls nest (c) staff are hired. "
        "Two kinds: (a) Queue. first come, first served (b) Stack. last in, first out (c) Stack. last hired first fired.",
        "(c) Stack. last hired first fired.")
    assert mixed["markers"] == ["(a)", "(b)", "(c)"] and mixed["items"][0] == "Queue. first come, first served"
    assert mixed["title"] == "Two kinds:"


def test_list_from_page_lines():
    lines = [
        line(50, 100, "A stack supports the following operations:"),
        line(60, 114, "• push adds an element at the top"),
        line(60, 128, "• pop removes the top element"),
        line(60, 142, "• peek returns the top element"),
        line(70, 154, "without removing it"),
        line(50, 190, "Stacks are used for recursion."),
    ]
    page = page_data(lines)
    hit = layout.matched_lines(page["words"], layout.locate_words(page["words"], "pop removes the top element"))
    listing = layout.list_from_lines(lines, hit)
    assert listing["title"] == "A stack supports the following operations:"
    assert listing["items"] == [
        "push adds an element at the top",
        "pop removes the top element",
        "peek returns the top element without removing it",
    ]


def test_code_from_lines_keeps_indentation():
    lines = [
        line(50, 100, "The program below prints a greeting."),
        line(60, 120, "int main(void) {", flags=layout.LINE_MONO, width=88),
        line(82, 132, "printf(\"hi\");", flags=layout.LINE_MONO, width=71.5),
        line(82, 144, "return 0;", flags=layout.LINE_MONO, width=49.5),
        line(60, 156, "}", flags=layout.LINE_MONO, width=5.5),
    ]
    code = layout.code_from_lines(lines, [2])
    assert code["type"] == "code"
    assert code["text"].splitlines() == ["int main(void) {", "    printf(\"hi\");", "    return 0;", "}"]
    assert layout.code_from_lines(lines, [0]) is None
    numbers = [line(60, 120 + 12 * i, text, flags=layout.LINE_MONO) for i, text in enumerate(["9 39 45 81", "BEG MID END", "INDEX", "TEMP"])]
    assert layout.code_from_lines(numbers, [1]) is None


def test_locate_words_and_rects():
    lines = [line(50, 100, "Light travels in straight lines."), line(50, 114, "It reflects from mirrors at equal angles.")]
    page = page_data(lines)
    found = layout.locate_words(page["words"], "travels in straight lines. It reflects")
    assert [page["words"][i][4] for i in found] == ["travels", "in", "straight", "lines.", "It", "reflects"]
    rects = layout.word_rects(page["words"], found, 600, 800)
    assert len(rects) == 2 and all(0 <= value <= 1 for rect in rects for value in rect)
    assert layout.locate_words(page["words"], "photosynthesis happens in leaves") == []


def test_pick_assets_prefers_referenced_and_captioned_figures():
    figures = [
        {"bbox": [50, 500, 300, 700], "image": "p5-f1.webp", "caption": "Fig. 3.1 A bar magnet", "label": "3.1"},
        {"bbox": [320, 500, 550, 700], "image": "p5-f2.webp", "caption": None, "label": None},
    ]
    neighbour = page_data([], figures=[{"bbox": [0, 0, 1, 1], "image": "p6-f1.webp", "caption": "Fig. 3.2 Field lines", "label": "3.2"}], number=6)
    page = page_data([line(50, 100, "x")], figures=figures)
    picked, tables, formulas = layout.pick_assets(page, {6: neighbour}, "as shown in Fig. 3.2 the lines", "magnet field", (100, 110), url)
    assert [(f["label"], f["page_number"], f["primary"]) for f in picked] == [("3.2", 6, True), ("3.1", 5, True), (None, 5, False)]
    assert picked[0]["image"] == "/data/assets/h/p6-f1.webp"
    assert tables == [] and formulas == []

    banner = {"bbox": [50, 40, 550, 150], "image": "p5-f3.webp", "caption": None, "label": None, "width": 900, "height": 200}
    page = page_data([line(50, 100, "x")], figures=[banner])
    assert layout.pick_assets(page, {}, "", "magnet", (100, 110), url)[0] == []


def test_enrich_result_end_to_end():
    lines = [
        line(50, 100, "Photosynthesis is the process by which green plants make food."),
        line(50, 114, "It needs sunlight, water and carbon dioxide (see Fig. 7.1)."),
    ]
    figures = [{"bbox": [60, 140, 400, 400], "image": "p5-f1.webp", "caption": "Fig. 7.1 Photosynthesis in a leaf", "label": "7.1", "width": 500, "height": 380}]
    formulas = [{"bbox": [200, 130, 400, 138], "image": "p5-m1.webp", "text": "6CO2 + 6H2O → C6H12O6 + 6O2"}]
    page = page_data(lines, figures=figures, formulas=formulas)
    item = {
        "quote": "Photosynthesis is the process by which green plants make food.",
        "matched_paragraph": "Photosynthesis is the process by which green plants make food. It needs sunlight, water and carbon dioxide (see Fig. 7.1).",
        "chunk_preview": "",
        "source": {"page_number": 5, "file_path": "x.pdf"},
    }
    query = "what is photosynthesis"
    rich = layout.enrich_result(item, query, layout.query_intents(query), page, {}, url)
    assert rich["kind"] == "definition"
    assert rich["blocks"][0]["term"] == "Photosynthesis"
    assert rich["figures"][0]["primary"] and rich["figures"][0]["label"] == "7.1"
    assert rich["formulas"][0]["text"].startswith("6CO2")
    assert rich["page"]["image"] == "/data/assets/h/p5.webp"
    assert len(rich["page"]["highlights"]) == 1 and len(rich["page"]["context"]) == 2


def _rich(kind="text", figures=(), tables=()):
    return {"kind": kind, "blocks": [], "figures": list(figures), "tables": list(tables), "formulas": [], "page": None}


def test_choose_layout():
    figure = {"primary": True}
    table = {"primary": True}
    assert layout.choose_layout("diagram of heart", [_rich(), _rich(figures=[figure])], ["A", "B"]) == {
        "kind": "visual", "hero": 1, "intents": ["visual"]}
    assert layout.choose_layout("difference between acids and bases", [_rich(tables=[table])], ["A"])["kind"] == "table"
    assert layout.choose_layout("compare stack and queue", [_rich(), _rich()], ["A", "B"])["kind"] == "compare"
    assert layout.choose_layout("how does bubble sort work", [_rich("text"), _rich("code")], ["A", "B"])["kind"] == "steps"
    assert layout.choose_layout("what is light", [_rich("definition")], ["A"])["kind"] == "answer"
    assert layout.choose_layout("diagram of a linked list", [_rich("list")], ["A"])["kind"] == "answer"
    assert layout.choose_layout("anything", [], [])["kind"] == "empty"


def test_enrich_results_survives_missing_pages_and_loader_errors():
    items = [
        {"quote": "q", "source": {"file_path": "a.pdf", "page_number": 3, "book_title": "A"}},
        {"quote": "q", "source": {"file_path": "b.pdf", "page_number": 4, "book_title": "B"}},
    ]

    def loader(file_path, file_hash, pages):
        if file_path == "b.pdf":
            raise OSError("storage down")
        return {}

    logs = []
    rich, chosen = layout.enrich_results("q", items, {"a.pdf": "h1", "b.pdf": "h2"}, loader, lambda h, n: n, logs.append)
    assert [r["page"] for r in rich] == [None, None]
    assert chosen["kind"] == "answer" and any("storage down" in message for message in logs)


def test_pages_without_text_are_ocred_for_highlighting():
    bare = page_data([])
    bare["text_usable"] = True
    lines = [line(50, 100, "আলোর প্রতিফলন ঘটে মসৃণ তলে।")]
    ocred = dict(page_data(lines), ocr="ben+eng")
    calls = []

    def ocr_page(file_path, file_hash, page_number, language):
        calls.append((file_path, file_hash, page_number, language))
        return ocred

    items = [{"quote": "আলোর প্রতিফলন ঘটে", "source": {"file_path": "a.pdf", "page_number": 5, "book_title": "A"}}]
    rich, _ = layout.enrich_results("q", items, {"a.pdf": "h"}, lambda *args: {5: bare}, lambda h, n: n, print, ocr_page)
    assert calls == [("a.pdf", "h", 5, "bn")]
    assert rich[0]["page"]["highlights"]
    assert not layout.needs_ocr(ocred)

import os
import sys

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import utils
from utils import (
    PageText,
    build_page_aware_chunks,
    choose_ocr_language,
    chunk_words,
    classify_text_layer,
    extract_pdf_pages,
    infer_metadata_from_filename,
    sanitize_text,
)


def test_sanitize_text_strips_nul():
    assert sanitize_text("a\x00b") == "ab"


def test_chunk_words_overlap():
    text = " ".join(str(i) for i in range(10))
    chunks = list(chunk_words(text, chunk_size=4, overlap=1))
    assert chunks[0] == "0 1 2 3"
    assert chunks[1].startswith("3 ")


def test_page_aware_chunks_keep_page_numbers():
    pages = [PageText(1, "a b c"), PageText(2, "d e f")]
    assert [p for p, _ in build_page_aware_chunks(pages, 10, 2)] == [1, 2]


def test_metadata_dash_pattern():
    meta = infer_metadata_from_filename("Physics Basics - H C Verma - 2010.pdf")
    assert meta["title"] == "Physics Basics"
    assert meta["author"] == "H C Verma"
    assert meta["publication_year"] == 2010


def test_metadata_underscore_pattern():
    meta = infer_metadata_from_filename("Physics_Thermodynamics_2015.pdf")
    assert meta["department"] == "Physics"
    assert meta["title"] == "Thermodynamics 2015"


def test_detect_language_code():
    from utils import detect_language_code

    assert detect_language_code("আলোর প্রতিফলন কাকে বলে") == "bn"
    assert detect_language_code("ऊष्मागतिकी की परिभाषा क्या है") == "hi"
    assert detect_language_code("What is thermodynamics?") == "en"
    assert detect_language_code("1234 !!") == "en"


def test_compute_file_hash_changes_with_content(tmp_path):
    from utils import compute_file_hash

    f = tmp_path / "a.pdf"
    f.write_bytes(b"one")
    first = compute_file_hash(str(f))
    f.write_bytes(b"two")
    assert compute_file_hash(str(f)) != first


def test_chunk_words_merges_tiny_tail():
    text = " ".join(str(i) for i in range(320))
    chunks = list(chunk_words(text, chunk_size=300, overlap=50, min_tail_words=40))
    assert len(chunks) == 1
    assert len(chunks[0].split()) == 320


def test_chunk_words_keeps_substantial_tail():
    text = " ".join(str(i) for i in range(400))
    chunks = list(chunk_words(text, chunk_size=300, overlap=50, min_tail_words=40))
    assert len(chunks) == 2
    assert chunks[-1].split()[-1] == "399"


def test_chunk_words_no_redundant_tail_chunk():
    text = " ".join(str(i) for i in range(300))
    assert len(list(chunk_words(text, chunk_size=300, overlap=50))) == 1


def test_is_exercise_chunk_positive_examples():
    from utils import is_exercise_chunk

    assert is_exercise_chunk("40 Data Structures Using C Exercises Review Questions 1. Discuss the structure of a C program.")
    assert is_exercise_chunk("[LO 2.2 E] 2.3 [LO 2.2 H] 2.4 [LO 2.3 M] 2.5 [LO 2.2 H]")
    assert is_exercise_chunk(
        "1. What is a stack? 2. Why use queues? 3. How is a tree traversed? 4. Which sort is fastest? 5. Explain hashing?"
    )


def test_is_exercise_chunk_negative_examples():
    from utils import is_exercise_chunk

    assert not is_exercise_chunk("A stack is a linear data structure in which insertion and deletion happen at one end.")
    assert not is_exercise_chunk("sorrows and problems of life, or humbly confident, looking up to the God they dared to call")
    rhetorical = "The future is uncertain. Will it rain tomorrow? Will the team win? Will prices rise? Will I pass? Will it end?"
    assert not is_exercise_chunk(rhetorical)


ENGLISH = "The pointer is a variable that contains the address of another variable in the memory of the computer. " * 3
KRUTI_DEV = "izdk'k ijkorZu ,oa viorZu lery lrg ls 8- Økafrd dks.k ls vki D;k le>rs gS izdk'k ok;q ls dh dk¡p dh IysV esa izos'k djrk gSA " * 3
BIJOY = "KvwRb weeván àeevwnK Üjbá`b ev ÜhäZzK ev Kb®vcY wbáq Üewk mgm®vq coáZ nq bv| ÜKbbv KvwRb weeván " * 3
SYMBOLS = "✁✂✄☎✆✝ ✞✟✠✝ ✡✄☛ ❢☞✌✍ ✎✏✑✒ ❡✓✔✕ ✖✓ ✔✗ ✘ ✔✔ ✙✚✛ ✓ ✜✢✣✤ ✂ ✥✦ ✞ ✤ ✬ ✤ ✧★ " * 3
UNICODE_BENGALI = "এই ক্ষেত্রে গাড়ির গতির বেগ সময় লেখচিত্র দেখানো হয়েছে লেখের প্রকৃতি থেকে বুঝা যায় " * 3
UNICODE_HINDI = "जनन तंत्र के विषय में जानें नर जनन तंत्र जनन कोशिका उत्पादित करने वाले अंग " * 3


def test_classify_text_layer():
    assert classify_text_layer(ENGLISH) == "good"
    assert classify_text_layer(UNICODE_BENGALI) == "good"
    assert classify_text_layer(UNICODE_HINDI) == "good"
    assert classify_text_layer("short") == "empty"
    assert classify_text_layer("") == "empty"
    for garbage in (KRUTI_DEV, BIJOY, SYMBOLS):
        assert classify_text_layer(garbage) == "bad"


def make_pdf(path, page_text: str, pages: int = 20) -> str:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_textbox(fitz.Rect(40, 40, 550, 800), page_text, fontsize=8)
    doc.save(path)
    doc.close()
    return str(path)


def test_choose_ocr_language(tmp_path, monkeypatch):
    # Pretend Tesseract reads Devanagari / Bengali / English from the rendered page.
    ocr_output = {"text": UNICODE_HINDI}
    monkeypatch.setattr(utils, "_ocr_image", lambda image, lang: ocr_output["text"])

    english_pdf = make_pdf(tmp_path / "en.pdf", ENGLISH)
    legacy_pdf = make_pdf(tmp_path / "legacy.pdf", KRUTI_DEV)

    with fitz.open(english_pdf) as pdf:
        assert choose_ocr_language(pdf) is None
    with fitz.open(legacy_pdf) as pdf:
        assert choose_ocr_language(pdf) == "hin+eng"
        ocr_output["text"] = UNICODE_BENGALI
        assert choose_ocr_language(pdf) == "ben+eng"
        ocr_output["text"] = ENGLISH
        assert choose_ocr_language(pdf) == "eng"

    object.__setattr__(utils.settings, "ocr_fallback", False)
    try:
        with fitz.open(legacy_pdf) as pdf:
            assert choose_ocr_language(pdf) is None
    finally:
        object.__setattr__(utils.settings, "ocr_fallback", True)


def test_extract_pdf_pages_uses_ocr_only_for_bad_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "_ocr_image", lambda image, lang: UNICODE_HINDI)

    legacy = extract_pdf_pages(make_pdf(tmp_path / "legacy.pdf", KRUTI_DEV, pages=5))
    assert [page.page_number for page in legacy] == [1, 2, 3, 4, 5]
    assert all(page.text == UNICODE_HINDI for page in legacy)

    english = extract_pdf_pages(make_pdf(tmp_path / "en.pdf", ENGLISH, pages=5))
    assert len(english) == 5
    assert all("pointer is a variable" in page.text for page in english)

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import PageText, build_page_aware_chunks, chunk_words, infer_metadata_from_filename, sanitize_text


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

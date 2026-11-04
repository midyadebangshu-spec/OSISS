"""Quote integrity (quotes.py): pure, no services."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import quotes as q

PAGE = (
    "7.1 INTRODUCTION Stack is an important data struc- ture which stores its elements in an ordered manner. "
    "We will explain the concept of stacks using an analogy. You must have seen a pile of plates where one plate "
    "is placed on top of another as shown in Fig. 7.1 . Now, when you want to remove a plate, you remove the "
    "topmost plate first. A stack is called a LIFO (Last-In-First-Out) data structure."
)


def test_verbatim_ignores_extraction_noise():
    assert q.verify("Stack is an important data structure which stores its elements", PAGE)["status"] == "verbatim"
    assert q.verify("stack  is an IMPORTANT data\nstructure", PAGE)["status"] == "verbatim"  # case, whitespace
    assert q.verify("as shown in Fig. 7.1.", PAGE)["status"] == "verbatim"  # a space before the full stop on the page
    assert q.verify("A stack is called a LIFO (Last‑In‑First‑Out) data structure.", PAGE)["status"] == "verbatim"  # dashes
    assert q.verify("“when you want to remove a plate,”", PAGE)["status"] == "verbatim"  # curly quotes around it


def test_omissions_and_brackets_split_the_quote():
    assert q.segments("Stack is [...] ordered manner") == ["Stack is", "ordered manner"]
    assert q.segments("a … b ... c [the stack] d") == ["a", "b", "c", "d"]
    assert q.verify("Stack is an important data structure […] in an ordered manner.", PAGE)["status"] == "verbatim"
    assert q.verify("A stack [the structure] is called a LIFO", PAGE)["status"] == "verbatim"  # a bracketed insertion
    assert q.verify("ordered manner [...] Stack is an important", PAGE)["status"] != "verbatim"  # segments out of order


def test_a_changed_word_is_reported_with_the_pages_wording():
    result = q.verify("Stack is an important data structure which stores its items in an ordered manner.", PAGE)
    assert result["status"] == "partial" and 0.8 < result["matched"] < 1
    assert "elements" in result["closest"] and "items" not in result["closest"]


def test_not_found_and_empty():
    result = q.verify("Queues are first in first out structures used in scheduling jobs.", PAGE)
    assert result["status"] == "not_found" and result["matched"] < 0.6
    assert q.verify("", PAGE)["status"] == "empty" and q.verify("[...]", PAGE)["status"] == "empty"
    assert q.verify("anything", "")["status"] == "not_found"


def test_bengali_and_hindi_pages():
    page = "স্ট্যাক একটি রৈখিক ডেটা কাঠামো যেখানে উপাদানগুলি এক প্রান্ত থেকে যোগ করা হয়।"
    assert q.verify("রৈখিক ডেটা  কাঠামো যেখানে", page)["status"] == "verbatim"
    assert q.verify("रैखिक डेटा संरचना", "स्टैक एक रैखिक डेटा संरचना है")["status"] == "verbatim"
    assert q.verify("রৈখিক ডেটা গঠন যেখানে", page)["status"] in ("partial", "not_found")


def test_key_is_stable():
    assert q.key("A  b\n-c") == q.key("ab c") == "abc"
    assert q.key("ＡＢ") == "ab"  # compatibility forms

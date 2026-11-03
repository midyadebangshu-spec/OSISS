"""Concept graph building (concepts.py) and concept search (concept_store.rank_matches) on plain strings."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import concepts
from concepts import AliasMatcher, Candidate, Chunk


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("• The Plant Kingdom", "plant kingdom"),
        (" Colloids:", "colloids"),
        ("4.2.1 Linked Lists", "linked lists"),
        ("“Stack”", "stack"),
        ("ﬁrst law", "first law"),
    ],
)
def test_normalize(raw, expected):
    assert concepts.normalize(raw) == expected


@pytest.mark.parametrize(
    "term,ok",
    [
        ("linked list", True),
        ("photosynthesis", True),
        ("প্রতিসরণ", True),
        ("this", False),
        ("following", False),
        ("element data[k", False),
        ("binary tree t", False),
        ("objects", False),  # too generic alone
        ("the stack", False),  # starts with a function word
        ("ab", False),
        ("chapter 4", False),
    ],
)
def test_acceptable(term, ok):
    assert concepts.acceptable(term) is ok


def test_singular_and_term_key():
    assert [concepts.singular(w) for w in ["queues", "bodies", "classes", "process", "bus", "basis"]] == [
        "queue", "body", "class", "process", "bus", "basis"]
    assert concepts.term_key("Binary Heaps") == concepts.term_key("binary heap") == "binary heap"
    assert concepts.term_key("far-sightedness") == "far sightedness"


def test_definition_candidates_in_three_languages():
    assert concepts.definition_candidates("A stack is a linear data structure in which insertion happens at one end.") == ["stack"]
    assert concepts.definition_candidates("The bending of light at a boundary is called refraction.") == ["refraction"]
    assert concepts.definition_candidates("আলোর দিক পরিবর্তনকে আলোর প্রতিসরণ বলে।") == ["আলোর প্রতিসরণ"]
    assert concepts.definition_candidates("धातु के धीरे-धीरे नष्ट होने को संक्षारण कहते हैं।") == ["संक्षारण"]
    assert concepts.definition_candidates("It is a good idea.") == []


@pytest.mark.parametrize(
    "text,pair",
    [
        ("इस दोष को दूर-दृष्टिता (far-sightedness) कहते हैं।", ("दूर-दृष्टिता", "far-sightedness")),
        ("दर्पण का द्वारक (aperture) छोटा होता है।", ("द्वारक", "aperture")),
        ("পরিমানই হচ্ছে আর্দ্রতা (humidity)।", ("আর্দ্রতা", "humidity")),
        ("হলুদ বা বর্ণ (colour) বদলায়", ("বর্ণ", "colour")),
        ("पौधों में प्रकाश संश्लेषण (photosynthesis) होता है", ("प्रकाश संश्लेषण", "photosynthesis")),
    ],
)
def test_gloss_pairs_trim_the_indic_side(text, pair):
    assert concepts.gloss_pairs(text) == [pair]


def test_glosses_merge_indic_names_into_the_english_concept():
    built = concepts.build_concepts([
        Candidate("রুপান্তর", "definition", 1, 3),
        Candidate("দূর-দৃষ্টি", "gloss", 2, 5, gloss="far-sightedness"),
        Candidate("दूर-दृष्टिता", "gloss", 3, 7, gloss="far-sightedness"),
        Candidate("far-sightedness", "definition", 4, 9),
    ])
    assert set(built) == {"রুপান্তর", "far sightedness"}
    merged = built["far sightedness"]
    assert merged.label == "far-sightedness"
    assert merged.aliases == {"far-sightedness", "দূর-দৃষ্টি", "दूर-दृष्टिता"}


def test_alias_matcher_longest_match_plurals_and_indic_case_endings():
    matcher = AliasMatcher({"linked list": "linked list", "list": "list", "stack": "stack", "সমাজ": "society",
                            "संक्षारण": "corrosion"})
    assert matcher.find("Linked lists and stacks differ.") == [("linked list", 0), ("stack", 3)]
    assert [key for key, _ in matcher.find("সমাজের গঠন ও সমাজ")] == ["society", "society"]
    assert [key for key, _ in matcher.find("लोहे का संक्षारण")] == ["corrosion"]
    assert matcher.find("সমাজবিজ্ঞান") == []  # a longer word, not a case ending


@pytest.mark.parametrize(
    "sentence,first,second,expected",
    [
        ("A stack is a type of data structure.", "stack", "data structure", ("is_a", True)),
        ("Data structures such as stacks are common.", "data structure", "stack", ("is_a", False)),
        ("A cell consists of a nucleus and cytoplasm.", "cell", "nucleus", ("has_part", True)),
        ("The nucleus is part of the cell.", "nucleus", "cell", ("has_part", False)),
        ("A stack is used for recursion.", "stack", "recursion", ("used_for", True)),
        ("The difference between a stack and a queue is order.", "stack", "queue", ("contrast", True)),
        ("A stack and a queue are both lists.", "stack", "queue", None),  # "and" alone is no contrast
        ("Heat causes expansion of metals.", "heat", "expansion", ("causes", True)),
    ],
)
def test_classify_relation(sentence, first, second, expected):
    assert concepts.classify_relation(sentence, first, second) == expected


def _chunks():
    texts = [
        (1, 1, 1, "A stack is a linear data structure. Push adds an element to a stack."),
        (2, 1, 2, "A queue is a linear data structure. The difference between a stack and a queue is the order of removal."),
        (3, 1, 3, "A stack is used for recursion. Recursion needs a stack."),
        (4, 2, 4, "Stacks and queues are introduced first. A stack is a type of data structure."),
        (5, 2, 9, "A queue is used for scheduling. Scheduling needs a queue."),
        (6, 2, 12, "Photosynthesis happens in leaves. Photosynthesis is the process by which plants make food."),
        (7, 3, 2, "Photosynthesis is the way plants turn light into sugar. Photosynthesis needs sunlight."),
    ]
    return [Chunk(chunk_id, book, page, text) for chunk_id, book, page, text in texts]


def test_build_graph_end_to_end():
    graph = concepts.build_graph(_chunks(), bold_lines=[(1, 1, "Data Structure"), (2, 4, "data structures")], log=lambda _: None)
    assert {"stack", "queue", "data structure", "photosynthesis"} <= set(graph.concepts)
    by_pair = {(link.a, link.b): link for link in graph.links}
    stack_queue = by_pair[("queue", "stack")]
    assert stack_queue.relation == "contrast" and stack_queue.books == {1, 2}
    assert stack_queue.evidence and all("stack" in item["sentence"].lower() for item in stack_queue.evidence)
    structure = by_pair[("data structure", "stack")]
    assert structure.relation == "is_a" and structure.relation_source == "stack"
    assert not any("photosynthesis" in pair for pair in by_pair)  # never in a sentence with the others
    definitions = [m for m in graph.mentions if m.is_definition]
    assert {m.key for m in definitions} >= {"stack", "queue", "photosynthesis"}


def test_order_hint_counts_books_that_reach_a_concept_first():
    first_page = {(1, "stack"): 2, (1, "queue"): 5, (2, "stack"): 1, (2, "queue"): 9, (3, "stack"): 8, (3, "queue"): 3}
    assert concepts._order_hint("stack", "queue", first_page) == 1


def test_rank_matches():
    from concept_store import rank_matches

    items = [
        {"id": 1, "label": "stack", "aliases": ["stack", "stacks"], "book_count": 3, "mention_count": 40, "definition_count": 3},
        {"id": 2, "label": "stack frame", "aliases": ["stack frame"], "book_count": 1, "mention_count": 5, "definition_count": 0},
        {"id": 3, "label": "refraction", "aliases": ["refraction", "प्रतिसरण", "অপবর্তন"], "book_count": 4, "mention_count": 30, "definition_count": 2},
    ]
    assert [i["id"] for i in rank_matches("What is a stack?", items)] == [1, 2]
    assert [i["id"] for i in rank_matches("stacks", items)][0] == 1
    assert [i["id"] for i in rank_matches("प्रतिसरण क्या है", items)] == [3]
    assert rank_matches("photosynthesis", items) == []
    items.append({"id": 4, "label": "प्रकाश का अपवर्तन", "aliases": ["प्रकाश का अपवर्तन"], "book_count": 3, "mention_count": 9, "definition_count": 1})
    assert rank_matches("प्रकाश संश्लेषण", items) == []  # sharing one word ("light") is not a match


def test_normalize_running_headers_and_conjunctions():
    assert concepts.normalize("10 • Liquids and Solids") == "liquids and solids"
    assert not concepts.acceptable("since log") and not concepts.acceptable("because earth")
    assert concepts.normalize("E • Water Properties") == "water properties"


def test_support_rules_boilerplate_and_prose():
    assert not concepts.acceptable("functions functions")
    single = concepts.Concept("control")
    single.sources.update({"definition": 1})
    assert not concepts.supported(single)
    single.source_books["bold"].update({1, 2})
    assert concepts.supported(single)
    phrase = concepts.Concept("binary heap")
    phrase.sources.update({"definition": 1})
    assert concepts.supported(phrase)
    chunks = [Chunk(i, i, 1, f"Access for free at openstax.org today. Topic {i} is here.") for i in range(3)]
    assert all("openstax" not in c.text for c in concepts.drop_boilerplate(chunks))
    assert not concepts.is_prose("17 29 18 21 31 PTR NEXT Head 12 18")
    assert concepts.is_prose("A union member is accessed with the dot operator.")
    assert not concepts.is_prose("Section Key Terms angular acceleration kinematics of rotational motion lever arm torque")
    assert not concepts.is_prose("Searching, sorting, hashing, traversal, minimum spanning trees, shortest paths, heaps are examples.")

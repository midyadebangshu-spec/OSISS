import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from eval_search import auc, covers_quote, is_negative, select_questions, wilson_interval


def test_wilson_interval_brackets_the_proportion():
    low, high = wilson_interval(9, 10)
    assert low < 0.9 < high <= 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)
    # more questions, narrower interval
    narrow = wilson_interval(90, 100)
    assert narrow[1] - narrow[0] < high - low


def test_auc():
    assert auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert auc([0.1], [0.9]) == 0.0
    assert auc([0.5], [0.5]) == 0.5
    assert auc([], [0.5]) == 0.5


def test_covers_quote_ignores_case_spacing_and_hyphen_breaks():
    page = "Intermolecular forces are the forces of attrac-\ntion between neighbour molecules."
    assert covers_quote("forces of attraction between neighbour molecules", [page])
    assert not covers_quote("forces of repulsion", [page])
    assert not covers_quote("", [page])


def test_select_questions_and_negatives():
    questions = [{"id": "a"}, {"id": "b", "split": "heldout"}, {"id": "c", "split": "tune", "no_answer": True}]
    assert [q["id"] for q in select_questions(questions, "heldout")] == ["b"]
    assert [q["id"] for q in select_questions(questions, "tune")] == ["a", "c"]
    assert len(select_questions(questions)) == 3
    assert [is_negative(q) for q in questions] == [False, False, True]

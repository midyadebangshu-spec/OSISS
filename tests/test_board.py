"""Research board (board.py): the pure helpers, with hand-written nodes and edges."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import board


def claim(i, text, x=0.0, y=0.0):
    return {"id": i, "kind": "claim", "text": text, "x": x, "y": y}


def evidence(i, evidence_id, quote, x=0.0, y=0.0):
    return {"id": i, "kind": "evidence", "text": "", "x": x, "y": y, "evidence": {"id": evidence_id, "quote": quote}}


def edge(i, source, target, relation):
    return {"id": i, "source_id": source, "target_id": target, "relation": relation, "note": ""}


def test_grid_and_position_limits():
    assert board.grid_position(0) == (0.0, 0.0) and board.grid_position(3) == (3 * 260.0, 0.0)
    assert board.grid_position(4) == (0.0, 170.0) and board.grid_position(9) == (260.0, 340.0)
    assert board.clamp_position("12.345") == 12.3 and board.clamp_position(-4999) == -4999
    for bad in (5001, float("nan"), "x", None):
        with pytest.raises(board.BoardError):
            board.clamp_position(bad)


def test_ring_positions():
    assert board.ring_positions(0, 0, 0) == []
    four = board.ring_positions(4, 100.0, 200.0, 50.0)
    assert four[0] == (100.0, 150.0) and four[1] == (150.0, 200.0) and four[2] == (100.0, 250.0) and four[3] == (50.0, 200.0)
    assert len(set(board.ring_positions(6, 0, 0))) == 6


def test_unsupported_claims():
    nodes = [claim(1, "A"), claim(2, "B"), claim(3, "C"), evidence(4, 40, "q"), {"id": 5, "kind": "note", "text": "", "x": 0, "y": 0}]
    edges = [edge(1, 4, 1, "supports"),  # evidence supports claim A
             edge(2, 2, 4, "supports"),  # a "supports" edge drawn the other way round still counts
             edge(3, 5, 3, "supports"),  # a note is not evidence
             ]
    assert board.unsupported_claims(nodes, edges) == [3]
    assert board.unsupported_claims(nodes, []) == [1, 2, 3]
    assert board.unsupported_claims([evidence(4, 1, "q")], []) == []  # only claims are reported
    assert board.unsupported_claims(nodes, [edge(1, 4, 1, "contradicts")]) == [1, 2, 3]  # counter-evidence is not support


def test_outline_body():
    nodes = [
        claim(1, "Stacks are LIFO", y=0), claim(2, "Queues are FIFO", y=100), claim(3, "Unsupported idea", y=200),
        evidence(4, 11, "A stack   is LIFO.", y=10), evidence(5, 12, "Queues serve first come.", y=110),
        evidence(6, 13, "Surprisingly, a stack can act like a queue.", y=20), evidence(7, 14, "Unlinked passage.", y=300),
        {"id": 8, "kind": "note", "text": "", "x": 0, "y": 5, "note": {"id": 1, "title": "T", "body": "My own   explanation."}},
        {"id": 9, "kind": "concept", "text": "", "x": 0, "y": 0, "concept_key": "stack"},
    ]
    edges = [edge(1, 4, 1, "supports"), edge(2, 5, 2, "supports"), edge(3, 6, 1, "contradicts"), edge(4, 8, 1, "explains"),
             edge(5, 9, 1, "relates")]
    assert board.outline_body(nodes, edges) == (
        "## Stacks are LIFO\n\n> A stack is LIFO. [@e11]\n\nCounter-evidence:\n\n> Surprisingly, a stack can act like a queue. [@e13]\n\n"
        "My own explanation.\n\n## Queues are FIFO\n\n> Queues serve first come. [@e12]\n\n"
        "## Unsupported idea\n\nTODO: add supporting evidence.\n\n## Other evidence\n\n> Unlinked passage. [@e14]\n"
    )
    assert board.outline_body([], []) == ""


def test_outline_orders_claims_top_to_bottom_then_left_to_right():
    nodes = [claim(1, "Right", x=300, y=0), claim(2, "Left", x=0, y=0), claim(3, "Below", x=0, y=50)]
    headings = [line for line in board.outline_body(nodes, []).splitlines() if line.startswith("## ")]
    assert headings == ["## Left", "## Right", "## Below"]

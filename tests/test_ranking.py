import os
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from ranking import (
    best_answer_span,
    dedupe_candidates,
    keyword_fields,
    keyword_query_text,
    locate_subsequence,
    mmr_select,
    normalize_query,
    plan_token_windows,
    reciprocal_rank_fusion,
)


def chunk(file_path, page, text="", embedding=None, score=0.0, rerank=None):
    item = {"score": score, "source": {"file_path": file_path, "page_number": page, "text": text}}
    if embedding is not None:
        item["source"]["embedding"] = embedding
    if rerank is not None:
        item["rerank_score"] = rerank
    return item


def test_rrf_rewards_agreement_between_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]], k=60)
    ids = [doc_id for doc_id, _ in fused]
    assert ids[0] == "a"  # ranks 1 and 2
    assert ids[1] == "c"  # ranks 3 and 1
    assert set(ids) == {"a", "b", "c", "d"}
    assert abs(dict(fused)["a"] - (1 / 61 + 1 / 62)) < 1e-12


def test_rrf_weights_scale_a_list():
    fused = dict(reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0, 0.5], k=60))
    assert fused["a"] > fused["b"]
    assert abs(fused["b"] - 0.5 / 61) < 1e-12


def test_normalize_query_removes_invisibles_and_keeps_joiners():
    assert normalize_query("  what​ is  ﻿heat­?  ") == "what is heat?"
    # ZWNJ / ZWJ change Bengali spelling, so they are kept.
    assert "‌" in normalize_query("র‌ক")


def test_normalize_query_is_nfc():
    decomposed = "é"
    assert normalize_query(decomposed) == unicodedata.normalize("NFC", decomposed)


def test_keyword_query_strips_question_words():
    assert keyword_query_text("What is the second law of thermodynamics?") == "second law thermodynamics"
    assert keyword_query_text("আলোর প্রতিফলন কাকে বলে?") == "আলোর প্রতিফলন"
    assert keyword_query_text("ऊष्मागतिकी की परिभाषा क्या है?") == "ऊष्मागतिकी"


def test_keyword_query_keeps_everything_when_only_stopwords():
    assert keyword_query_text("What is?") == "What is"


def test_keyword_query_can_keep_stopwords():
    assert keyword_query_text("What is a stack?", strip_stopwords=False) == "What is a stack"


def test_keyword_query_adds_both_nukta_spellings():
    precomposed = "নদীর পানি বাড়ে"  # contains ড়
    precomposed = precomposed.replace("ড়", "ড়")
    terms = keyword_query_text(precomposed).split()
    assert any("ড়" in term for term in terms)
    assert any("ড়" in term for term in terms)


def test_keyword_fields_by_language():
    assert keyword_fields("bn") == ["text", "text.bn"]
    assert keyword_fields("hi") == ["text", "text.hi"]
    assert keyword_fields("en") == ["text", "text.en"]
    assert keyword_fields("bn", use_language_analyzers=False) == ["text"]
    assert keyword_fields("xx") == ["text"]


def test_dedupe_keeps_best_chunk_per_page_and_drops_copies():
    candidates = [
        chunk("a.pdf", 1, "stack is a linear data structure"),
        chunk("a.pdf", 1, "push and pop operations on the stack"),
        chunk("b.pdf", 7, "stack is a linear data structure"),
        chunk("b.pdf", 8, "queue is first in first out"),
    ]
    kept = dedupe_candidates(candidates)
    assert [(c["source"]["file_path"], c["source"]["page_number"]) for c in kept] == [("a.pdf", 1), ("b.pdf", 8)]


def test_mmr_prefers_novel_chunk_over_near_duplicate():
    candidates = [
        chunk("a.pdf", 1, embedding=[1.0, 0.0], rerank=0.90),
        chunk("a.pdf", 2, embedding=[0.99, 0.14], rerank=0.85),
        chunk("b.pdf", 5, embedding=[0.0, 1.0], rerank=0.80),
    ]
    picked = mmr_select(candidates, limit=2, lambda_=0.7)
    assert [c["source"]["page_number"] for c in picked] == [1, 5]


def test_mmr_handles_negative_logits():
    candidates = [
        chunk("a.pdf", 1, embedding=[1.0, 0.0], rerank=2.0),
        chunk("a.pdf", 2, embedding=[1.0, 0.0], rerank=1.5),
        chunk("b.pdf", 3, embedding=[0.0, 1.0], rerank=-1.0),
    ]
    assert [c["source"]["page_number"] for c in mmr_select(candidates, 3, 0.7)][0] == 1


def test_mmr_lambda_one_keeps_order():
    candidates = [chunk("a.pdf", i, embedding=[1.0, 0.0], rerank=1.0 - i / 10) for i in range(4)]
    assert mmr_select(candidates, limit=3, lambda_=1.0) == candidates[:3]


def test_mmr_without_embeddings_keeps_order():
    candidates = [chunk("a.pdf", i, score=1.0 - i / 10) for i in range(4)]
    assert mmr_select(candidates, limit=2, lambda_=0.5) == candidates[:2]


def test_mmr_uses_retrieval_score_without_rerank():
    candidates = [
        chunk("a.pdf", 1, embedding=[1.0, 0.0], score=0.03),
        chunk("a.pdf", 2, embedding=[1.0, 0.0], score=0.02),
        chunk("b.pdf", 3, embedding=[0.0, 1.0], score=0.01),
    ]
    assert [c["source"]["page_number"] for c in mmr_select(candidates, 2, 0.7)] == [1, 3]


def test_plan_token_windows_single_window_when_it_fits():
    assert plan_token_windows([1] * 5, budget=10, stride=3) == [(0, 5)]


def test_plan_token_windows_overlap_and_cover_everything():
    windows = plan_token_windows([1] * 25, budget=10, stride=3)
    assert windows == [(0, 10), (7, 17), (14, 24), (21, 25)]


def test_plan_token_windows_multi_token_words_and_oversized_word():
    windows = plan_token_windows([3, 3, 3, 20, 3, 3], budget=10, stride=4)
    covered = set()
    for start, end in windows:
        covered.update(range(start, end))
        assert end > start
    assert covered == set(range(6))
    assert (3, 4) in windows  # the 20-token word sits alone


def test_best_answer_span_respects_positions_and_order():
    start_logits = np.array([9.0, 0.0, 5.0, 0.0, 0.0])
    end_logits = np.array([9.0, 0.0, 0.0, 6.0, 0.0])
    # Position 0 is a special token and not a candidate.
    start, end, prob = best_answer_span(start_logits, end_logits, positions=[2, 3, 4])
    assert (start, end) == (2, 3)
    assert 0.0 < prob <= 1.0


def test_best_answer_span_never_ends_before_start():
    start_logits = np.array([0.0, 0.0, 8.0])
    end_logits = np.array([8.0, 0.0, 0.0])
    start, end, _ = best_answer_span(start_logits, end_logits, positions=[0, 1, 2])
    assert end >= start


def test_locate_subsequence_finds_pair_segment():
    assert locate_subsequence([0, 5, 6, 2, 2, 5, 6, 7, 2], [5, 6, 7]) == 5
    assert locate_subsequence([0, 5, 6, 2, 2, 5, 6, 2], [5, 6]) == 5

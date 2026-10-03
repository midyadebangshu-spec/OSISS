"""Query cleanup, rank fusion, diversification and QA-window helpers for search.

Kept free of model and Elasticsearch imports so the logic can be unit-tested
without the heavy runtime dependencies.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

import numpy as np

# Soft hyphen, zero-width space, word joiner, BOM. ZWJ/ZWNJ are left alone:
# they change the spelling of Bengali/Hindi conjuncts.
INVISIBLE_CHARS_RE = re.compile("[­​⁠﻿]")
QUERY_PUNCTUATION = "?？!！.,;:।॥\"'“”‘’()[]{}"

_QUERY_STOPWORDS = {
    # English question / instruction words.
    "what", "what's", "which", "who", "whom", "whose", "why", "how", "when", "where", "is", "are", "was",
    "were", "be", "do", "does", "did", "the", "a", "an", "of", "to", "in", "on", "for", "explain", "define",
    "describe", "discuss", "meaning", "mean", "means", "tell", "me", "about", "give", "please", "briefly",
    "write", "short", "note", "can", "you",
    # Bengali.
    "কী", "কি", "কাকে", "বলে", "বলা", "হয়", "কেন", "কোথায়", "কখন", "কীভাবে", "কিভাবে", "কে", "কোন",
    "কোনটি", "কত", "হলো", "হল", "এর", "এবং", "ও", "ব্যাখ্যা", "করো", "কর", "লেখ", "লেখো", "দাও",
    "সংজ্ঞা", "সংক্ষেপে", "বর্ণনা",
    # Hindi.
    "क्या", "क्यों", "कैसे", "कब", "कहाँ", "कहां", "कौन", "कौनसा", "किसे", "किसको", "कहते", "है", "हैं",
    "की", "का", "के", "में", "को", "से", "एक", "परिभाषा", "समझाइए", "बताइए", "लिखिए", "कीजिए", "होता",
    "होती", "होते",
}
QUERY_STOPWORDS = frozenset(unicodedata.normalize("NFC", word) for word in _QUERY_STOPWORDS)

# NFC decomposes these nukta letters (they are composition exclusions), while
# PDF text often carries the precomposed code point. Keyword queries include
# both spellings so BM25 matches whichever form the indexed text uses.
_PRECOMPOSED_NUKTA = "ড়ঢ়য়क़ख़ग़ज़ड़ढ़फ़य़"
NUKTA_COMPOSITIONS = {unicodedata.normalize("NFD", char): char for char in _PRECOMPOSED_NUKTA}


def normalize_query(text: str) -> str:
    """NFC-normalize, drop invisible characters and collapse whitespace."""
    text = INVISIBLE_CHARS_RE.sub("", unicodedata.normalize("NFC", text))
    return " ".join(text.split())


def precomposed_spelling(token: str) -> str:
    """Return the token with decomposed nukta sequences replaced by precomposed letters."""
    for decomposed, composed in NUKTA_COMPOSITIONS.items():
        token = token.replace(decomposed, composed)
    return token


def keyword_query_text(query: str, strip_stopwords: bool = True) -> str:
    """Build the BM25 query: drop question words, add precomposed nukta spellings.

    Falls back to all tokens when every token is a stopword.
    """
    tokens = [token.strip(QUERY_PUNCTUATION) for token in normalize_query(query).split()]
    tokens = [token for token in tokens if token]
    if strip_stopwords:
        content = [token for token in tokens if token.casefold() not in QUERY_STOPWORDS]
        tokens = content or tokens

    terms: List[str] = []
    for token in tokens:
        for variant in (token, precomposed_spelling(token)):
            if variant not in terms:
                terms.append(variant)
    return " ".join(terms)


def keyword_fields(language_code: str, use_language_analyzers: bool = True) -> List[str]:
    """Fields searched by BM25: the standard-analyzed text plus the query language's subfield."""
    if use_language_analyzers and language_code in {"bn", "hi", "en"}:
        return ["text", f"text.{language_code}"]
    return ["text"]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Hashable]],
    weights: Optional[Sequence[float]] = None,
    k: int = 60,
) -> List[Tuple[Hashable, float]]:
    """Fuse rankings with RRF: score(d) = sum_i w_i / (k + rank_i(d)), ranks starting at 1.

    Returns (id, score) pairs best-first; ties keep first-seen order.
    """
    weights = weights or [1.0] * len(ranked_lists)
    scores: Dict[Hashable, float] = {}
    for ranking, weight in zip(ranked_lists, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _word_set(text: str) -> frozenset:
    return frozenset(text.casefold().split())


def jaccard(first: frozenset, second: frozenset) -> float:
    if not first and not second:
        return 1.0
    return len(first & second) / len(first | second)


def dedupe_candidates(candidates: List[Dict], max_jaccard: float = 0.8) -> List[Dict]:
    """Keep the best chunk per (file, page) and drop near-identical texts (e.g. another edition).

    `candidates` must be sorted best-first; order is preserved.
    """
    kept: List[Dict] = []
    seen_pages = set()
    kept_words: List[frozenset] = []
    for item in candidates:
        src = item.get("source", {})
        page_key = (src.get("file_path"), src.get("page_number"))
        if page_key in seen_pages:
            continue
        words = _word_set(src.get("text", ""))
        if any(jaccard(words, other) >= max_jaccard for other in kept_words):
            continue
        seen_pages.add(page_key)
        kept_words.append(words)
        kept.append(item)
    return kept


def relevance_score(item: Dict) -> float:
    """Rerank score when available, else the first-stage retrieval score."""
    rerank = item.get("rerank_score")
    return float(rerank) if rerank is not None else float(item.get("score", 0.0))


def mmr_select(candidates: List[Dict], limit: int, lambda_: float) -> List[Dict]:
    """Maximal Marginal Relevance over the stored chunk embeddings.

    Picks argmax of lambda * relevance - (1 - lambda) * max cosine similarity to
    the already selected chunks. Relevance is divided by the best score (after
    shifting negative logits to start at 0) so lambda means the same for rerank
    probabilities and RRF scores. lambda >= 1 (or chunks without embeddings)
    keeps the input order.
    """
    if limit <= 0:
        return []
    if lambda_ >= 1.0 or len(candidates) <= 1:
        return candidates[:limit]

    vectors = [item.get("source", {}).get("embedding") for item in candidates]
    if any(vector is None for vector in vectors):
        return candidates[:limit]
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1.0, norms)
    similarity = matrix @ matrix.T

    relevance = np.asarray([relevance_score(item) for item in candidates], dtype=np.float32)
    if relevance.min() < 0:  # logits rather than probabilities: shift to start at 0
        relevance = relevance - relevance.min()
    top = float(relevance.max())
    relevance = relevance / top if top > 0 else np.ones_like(relevance)

    selected = [int(np.argmax(relevance))]
    remaining = [idx for idx in range(len(candidates)) if idx != selected[0]]
    while remaining and len(selected) < limit:
        redundancy = similarity[np.ix_(remaining, selected)].max(axis=1)
        mmr = lambda_ * relevance[remaining] - (1.0 - lambda_) * redundancy
        best = remaining[int(np.argmax(mmr))]
        selected.append(best)
        remaining.remove(best)
    return [candidates[idx] for idx in selected]


def plan_token_windows(word_token_counts: Sequence[int], budget: int, stride: int) -> List[Tuple[int, int]]:
    """Split words into (start, end) word ranges that fit `budget` tokens.

    Consecutive windows overlap by up to `stride` tokens (rounded down to whole
    words) so an answer near a boundary appears whole in one window. A single
    word longer than the budget gets a window of its own.
    """
    total = len(word_token_counts)
    windows: List[Tuple[int, int]] = []
    start = 0
    while start < total:
        end, used = start, 0
        while end < total and used + word_token_counts[end] <= budget:
            used += word_token_counts[end]
            end += 1
        end = max(end, start + 1)
        windows.append((start, end))
        if end >= total:
            break
        next_start, overlap = end, 0
        while next_start - 1 > start and overlap + word_token_counts[next_start - 1] <= stride:
            next_start -= 1
            overlap += word_token_counts[next_start]
        start = next_start
    return windows


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = np.exp(values - values.max())
    return shifted / shifted.sum()


def best_answer_span(
    start_logits: Sequence[float],
    end_logits: Sequence[float],
    positions: Sequence[int],
    max_answer_tokens: int = 30,
) -> Tuple[int, int, float]:
    """Best (start, end, probability) over the given context token positions.

    Like the Transformers QA pipeline: softmax start/end logits over the
    context tokens, then maximise p_start * p_end with start <= end and at
    most `max_answer_tokens` tokens.
    """
    positions = np.asarray(positions)
    start_probs = _softmax(np.asarray(start_logits, dtype=np.float64)[positions])
    end_probs = _softmax(np.asarray(end_logits, dtype=np.float64)[positions])
    scores = np.outer(start_probs, end_probs)
    gap = positions[None, :] - positions[:, None]
    scores[(gap < 0) | (gap >= max_answer_tokens)] = 0.0
    start_idx, end_idx = np.unravel_index(int(np.argmax(scores)), scores.shape)
    return int(positions[start_idx]), int(positions[end_idx]), float(scores[start_idx, end_idx])


def locate_subsequence(sequence: Sequence[int], part: Sequence[int]) -> int:
    """Start index of the last occurrence of `part` in `sequence` (where a pair's second segment sits)."""
    sequence, part = list(sequence), list(part)
    for start in range(len(sequence) - len(part), -1, -1):
        if sequence[start : start + len(part)] == part:
            return start
    raise ValueError("subsequence not found")

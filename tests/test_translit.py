"""Romanized-query support: detection, transliteration plumbing and search integration (no models/services)."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import search
import translit
from config import settings
from translit import classify_query, romanized_variants

VOCAB = frozenset("what is a stack pointer define the entropy of in how does work gibbs free energy".split())

# word -> candidate spellings (best first) per language, as the model would return them
SPELLINGS = {
    "bn": {"goti": ["গতি", "গটি"], "kake": ["কাকে"], "bole": ["বলে", "বোলে"], "alor": ["আলৰ"]},
    "hi": {"goti": ["गोटी"], "kake": ["काके"], "bole": ["बोले", "बोल"], "alor": ["आलोर"]},
}


class FakeTranslator:
    def translate_batch(self, sources, **_):
        results = []
        for source in sources:
            language = source[0].strip("_")
            word = "".join(source[1:])
            options = SPELLINGS[language].get(word, [word])
            results.append(SimpleNamespace(hypotheses=[list(option) for option in options]))
        return results


@pytest.fixture
def fake_translator(monkeypatch):
    monkeypatch.setattr(translit, "get_translator", lambda: FakeTranslator())
    monkeypatch.setattr(translit, "get_english_vocabulary", lambda: VOCAB)


def test_classify_query():
    assert classify_query("Goti kake bole?", VOCAB) == "romanized"
    assert classify_query("Gati kise kehte hain?", VOCAB) == "romanized"  # marker words
    assert classify_query("vashpan", VOCAB) == "ambiguous"  # unknown word: rare English term or romanized
    assert classify_query("What is gati in the stack", VOCAB) == "ambiguous"  # mixed
    assert classify_query("What is a stack?", VOCAB) == "english"
    assert classify_query("What is a pointer?", VOCAB) == "english"  # single letters are ignored
    assert classify_query("গতি কাকে বলে?", VOCAB) == "english"  # already native script
    assert classify_query("1234 ??", VOCAB) == "english"
    assert classify_query("goti", frozenset()) == "english"  # no vocabulary: only marker words count
    assert classify_query("goti kake", frozenset()) == "romanized"


def test_romanized_variants_cover_both_scripts(fake_translator):
    bengali, hindi = romanized_variants("Goti kake bole?")

    assert (bengali.language, hindi.language) == ("bn", "hi")
    assert bengali.text == "গতি কাকে বলে ?"
    assert bengali.keyword_text == "গতি গটি কাকে বলে ?"  # every spelling of content words, for BM25
    assert hindi.text == "गोटी काके बोले ?"


def test_partial_variants_keep_known_english_words(fake_translator):
    bengali, hindi = romanized_variants("what is goti in stack", partial=True)

    assert bengali.text == "what is গতি in stack"
    assert hindi.text == "what is गोटी in stack"
    assert romanized_variants("what is a stack", partial=True) == []  # nothing to rewrite


def test_assamese_letters_are_mapped_to_bengali(fake_translator):
    bengali, _ = romanized_variants("alor kake bole")
    assert bengali.text.startswith("আলর ")


def test_no_variants_for_native_script_or_disabled(fake_translator, monkeypatch):
    assert romanized_variants("গতি কাকে বলে?") == []
    monkeypatch.setattr(translit, "get_translator", lambda: None)
    assert romanized_variants("Goti kake bole?") == []


# --- search integration ---------------------------------------------------------------------


def hit(doc_id, language, page, text):
    return {
        "_id": doc_id,
        "_score": 1.0,
        "_source": {
            "file_path": f"{language}.pdf", "page_number": page, "text": text, "language_code": language,
            "embedding": [1.0, 0.0],
        },
    }


class RecordingES:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"hits": {"hits": self.hits}}


class Embedder:
    def __init__(self):
        self.texts = None

    def encode(self, texts, **_):
        self.texts = list(texts)
        return np.ones((len(texts), 2), dtype=np.float32)


class RecordingReranker:
    def __init__(self):
        self.pairs = []

    def predict(self, pairs, **_):
        self.pairs = list(pairs)
        return [1.0 - idx * 0.1 for idx in range(len(pairs))]


@pytest.fixture
def override():
    saved = {}

    def apply(**values):
        for key, value in values.items():
            saved.setdefault(key, getattr(settings, key))
            object.__setattr__(settings, key, value)

    yield apply
    for key, value in saved.items():
        object.__setattr__(settings, key, value)


def test_romanized_search_uses_script_variants(monkeypatch, override, fake_translator):
    override(hybrid_mode="rrf", retrieval_candidates=5, dedupe_results=False, mmr_lambda=1.0, qa_extra_candidates=0,
             min_qa_score=0.0, exclude_exercises=True, translit_original_weight=0.5)
    es = RecordingES([hit("b1", "bn", 1, "গতি বলে"), hit("h1", "hi", 2, "गति कहते"), hit("e1", "en", 3, "motion")])
    embedder, reranker = Embedder(), RecordingReranker()
    asked = []

    def fake_qa(question, context):
        asked.append(question)
        return {"answer": context[:3], "score": 0.5, "start": 0, "end": 3}

    monkeypatch.setattr(search, "get_es_client", lambda: es)
    monkeypatch.setattr(search, "get_models", lambda: (embedder, fake_qa))
    monkeypatch.setattr(search, "get_reranker", lambda: reranker)

    result = search.search_and_extract("Goti kake bole?", top_k=3)

    # query as typed + Bengali + Hindi rewrite are embedded, and each gets a kNN and a BM25 search
    assert embedder.texts == ["Goti kake bole?", "গতি কাকে বলে ?", "गोटी काके बोले ?"]
    assert len(es.calls) == 6
    bm25_queries = [call["query"] for call in es.calls if "query" in call]
    assert any("গতি গটি" in str(query) and "বোলে" not in str(query) for query in bm25_queries)
    # every chunk is scored against the rewrite in its own script; English chunks use the query as typed
    scored = {text: question for question, text in reranker.pairs}
    assert scored["গতি বলে"] == "গতি কাকে বলে ?"
    assert scored["गति कहते"] == "गोटी काके बोले ?"
    assert scored["motion"] == "Goti kake bole?"
    assert set(asked) == {"গতি কাকে বলে ?", "गोटी काके बोले ?", "Goti kake bole?"}
    assert len(result["results"]) == 3


def test_english_search_is_unchanged(monkeypatch, override, fake_translator):
    override(hybrid_mode="rrf", retrieval_candidates=5, dedupe_results=False, mmr_lambda=1.0, qa_extra_candidates=0,
             min_qa_score=0.0, exclude_exercises=True)
    es = RecordingES([hit("e1", "en", 1, "A stack is LIFO.")])
    embedder = Embedder()
    monkeypatch.setattr(search, "get_es_client", lambda: es)
    monkeypatch.setattr(search, "get_models", lambda: (embedder, lambda q, c: {"answer": "A", "score": 1.0, "start": 0, "end": 1}))
    monkeypatch.setattr(search, "get_reranker", lambda: RecordingReranker())

    search.search_and_extract("What is a stack?", top_k=1)

    assert embedder.texts == ["What is a stack?"]
    assert len(es.calls) == 2


# --- ambiguous queries: typed search first, romanized rewrites only when it found nothing convincing ----


class ScoringReranker:
    """Scores (question, text) pairs through `rule(question, text)` and records every call."""

    def __init__(self, rule):
        self.rule, self.calls = rule, []

    def predict(self, pairs, **_):
        self.calls.append(list(pairs))
        return [self.rule(question, text) for question, text in pairs]


class CountingEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **_):
        self.calls.append(list(texts))
        return np.ones((len(texts), 2), dtype=np.float32)


def run_search(monkeypatch, query, reranker, hits):
    es, embedder = RecordingES(hits), CountingEmbedder()
    asked = []

    def fake_qa(question, context):
        asked.append((question, context))
        return {"answer": context[:3], "score": 0.5, "start": 0, "end": 3}

    monkeypatch.setattr(search, "get_es_client", lambda: es)
    monkeypatch.setattr(search, "get_models", lambda: (embedder, fake_qa))
    monkeypatch.setattr(search, "get_reranker", lambda: reranker)
    result = search.search_and_extract(query, top_k=2)
    return result, es, embedder, asked


@pytest.fixture
def pipeline(override, fake_translator):
    override(hybrid_mode="rrf", retrieval_candidates=5, dedupe_results=False, mmr_lambda=1.0, qa_extra_candidates=0,
             min_qa_score=0.0, exclude_exercises=True, translit_confidence=0.8)


HITS = [hit("b1", "bn", 1, "গতি বলে"), hit("h1", "hi", 2, "गति कहते"), hit("e1", "en", 3, "motion is change")]


def test_ambiguous_query_with_confident_typed_result_skips_rewrites(monkeypatch, pipeline):
    reranker = ScoringReranker(lambda question, text: 0.95)

    result, es, embedder, _ = run_search(monkeypatch, "goti", reranker, HITS)

    assert embedder.calls == [["goti"]]
    assert len(es.calls) == 2
    assert len(reranker.calls) == 1
    assert len(result["results"]) == 2


def test_ambiguous_query_with_weak_typed_result_adds_rewrites_and_ranks_by_rerank(monkeypatch, pipeline):
    reranker = ScoringReranker(lambda question, text: 0.97 if (question, text) == ("গতি", "গতি বলে") else 0.05)

    result, es, embedder, asked = run_search(monkeypatch, "goti", reranker, HITS)

    assert embedder.calls == [["goti"], ["গতি", "गोटी"]]  # typed, then the two rewrites
    assert len(es.calls) == 2 + 4
    # the second rerank only scores chunks that need it: the English chunk was already scored as typed
    assert {text for _, text in reranker.calls[1]} == {"গতি বলে", "गति कहते"}
    assert result["results"][0]["source"]["file_path"] == "bn.pdf"
    assert asked[0][0] == "গতি"  # QA is asked in the script that scored the chunk best


def test_weak_typed_result_for_english_chunk_is_not_displaced_by_rewrites(monkeypatch, pipeline):
    scores = {"motion is change": 0.6}
    reranker = ScoringReranker(lambda question, text: scores.get(text, 0.01))

    result, _, _, asked = run_search(monkeypatch, "goti", reranker, HITS)

    assert result["results"][0]["source"]["file_path"] == "en.pdf"
    assert asked[0][0] == "goti"


def test_ambiguous_query_without_reranker_behaves_as_english(monkeypatch, pipeline):
    result, es, embedder, _ = run_search(monkeypatch, "goti", None, HITS)

    assert embedder.calls == [["goti"]]
    assert len(es.calls) == 2


def test_merge_by_rerank_keeps_best_copy():
    a_low = {"id": "a", "rerank_score": 0.2, "scored_query": "typed"}
    a_high = {"id": "a", "rerank_score": 0.9, "scored_query": "rewrite"}
    b = {"id": "b", "rerank_score": 0.5}

    merged = search.merge_by_rerank([a_low, b], [a_high])

    assert [item["id"] for item in merged] == ["a", "b"]
    assert merged[0]["scored_query"] == "rewrite"

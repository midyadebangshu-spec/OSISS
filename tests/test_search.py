"""Search pipeline tests with a fake Elasticsearch client and fake/tiny models (no services needed)."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import search
from config import settings


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


def hit(doc_id, file_path, page, text, embedding=(1.0, 0.0), score=1.0):
    return {
        "_id": doc_id,
        "_score": score,
        "_source": {"file_path": file_path, "page_number": page, "text": text, "embedding": list(embedding)},
    }


class FakeES:
    def __init__(self, dense, keyword):
        self.dense, self.keyword, self.calls = dense, keyword, []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"hits": {"hits": self.dense if "knn" in kwargs else self.keyword}}


def test_rrf_retrieval_runs_two_searches_and_fuses(override):
    override(hybrid_mode="rrf", retrieval_candidates=5, exclude_exercises=True, language_analyzers=True,
             query_stopwords=True, keyword_same_language=True)
    es = FakeES(
        dense=[hit("a", "x.pdf", 1, "a"), hit("b", "x.pdf", 2, "b"), hit("c", "x.pdf", 3, "c")],
        keyword=[hit("c", "x.pdf", 3, "c"), hit("d", "x.pdf", 4, "d")],
    )
    results = search.retrieve_top_chunks(es, "আলোর প্রতিফলন কাকে বলে?", [0.1, 0.2], top_k=3)

    assert [r["id"] for r in results] == ["c", "a", "b"]
    knn_call, keyword_call = es.calls
    assert "query" not in knn_call
    assert knn_call["knn"]["k"] == 5
    assert knn_call["knn"]["filter"] == {"bool": {"must_not": [{"term": {"is_exercise": True}}]}}
    clause = keyword_call["query"]["bool"]["must"][0]["multi_match"]
    assert clause["fields"] == ["text", "text.bn"]
    assert clause["query"] == "আলোর প্রতিফলন"
    assert keyword_call["query"]["bool"]["must_not"] == [{"term": {"is_exercise": True}}]
    assert keyword_call["query"]["bool"]["filter"] == [{"term": {"language_code": "bn"}}]


def test_keyword_language_filter_only_for_bengali_and_hindi(override):
    override(exclude_exercises=False, language_analyzers=True, keyword_same_language=True)
    hindi = search.build_keyword_query("विशिष्ट घूर्णन की परिभाषा क्या है?")
    assert hindi["bool"]["filter"] == [{"term": {"language_code": "hi"}}]
    assert hindi["bool"]["must"][0]["multi_match"]["fields"] == ["text", "text.hi"]

    english = search.build_keyword_query("What is the van der Waals b constant?")
    assert "bool" not in english  # no filter, no exclusion: the bare clause
    assert english["multi_match"]["fields"] == ["text", "text.en"]

    override(keyword_same_language=False)
    assert "bool" not in search.build_keyword_query("ভ্যান ডার ওয়ালস সমীকরণে b ধ্রুবক কী নির্দেশ করে?")


def test_sum_mode_is_one_boosted_query(override):
    override(hybrid_mode="sum", hybrid_keyword_boost=0.01, exclude_exercises=False, language_analyzers=False)
    es = FakeES(dense=[hit("a", "x.pdf", 1, "a", score=0.7)], keyword=[])
    results = search.retrieve_top_chunks(es, "What is a stack?", [0.1], top_k=3)

    assert len(es.calls) == 1
    call = es.calls[0]
    assert call["query"] == {"match": {"text": {"query": "stack", "boost": 0.01}}}
    assert call["knn"]["num_candidates"] == 30
    assert results[0]["score"] == 0.7


class FakeEmbedder:
    def encode(self, texts, **_):
        return np.ones((len(texts), 2), dtype=np.float32)


class FakeReranker:
    def predict(self, pairs, **_):
        return [1.0 - idx * 0.1 for idx in range(len(pairs))]


def test_search_and_extract_dedupes_and_gives_extra_candidates_to_qa(monkeypatch, override):
    override(hybrid_mode="rrf", retrieval_candidates=10, dedupe_results=True, mmr_lambda=1.0, qa_extra_candidates=2,
             qa_rank_weight=0.9, min_qa_score=0.0)
    dense = [
        hit("p1a", "a.pdf", 1, "Stacks are lists. Push adds an item."),
        hit("p1b", "a.pdf", 1, "Pop removes the top item. Stacks are LIFO."),  # same page, dropped
        hit("p2", "a.pdf", 2, "Queues are FIFO. Enqueue adds at the rear."),
        hit("p3", "a.pdf", 3, "Trees have nodes. Roots have no parent."),
        hit("p4", "b.pdf", 9, "A stack is a LIFO structure. ANSWER is here."),
        hit("p5", "b.pdf", 10, "Graphs have edges."),
    ]
    es = FakeES(dense=dense, keyword=[])
    seen_by_qa = []

    def fake_qa(question, context):
        seen_by_qa.append(context)
        start = context.find("ANSWER")
        if start >= 0:
            return {"answer": "ANSWER", "score": 0.99, "start": start, "end": start + 6}
        return {"answer": context[:5], "score": 0.01, "start": 0, "end": 5}

    monkeypatch.setattr(search, "get_es_client", lambda: es)
    monkeypatch.setattr(search, "get_models", lambda: (FakeEmbedder(), fake_qa))
    monkeypatch.setattr(search, "get_reranker", lambda: FakeReranker())

    result = search.search_and_extract("what​ is a stack?", top_k=2)

    assert result["query"] == "what is a stack?"
    assert len(seen_by_qa) == 4  # top_k + 2 after removing the same-page duplicate
    assert all("Pop removes" not in context for context in seen_by_qa)
    pages = [(r["source"]["file_path"], r["source"]["page_number"]) for r in result["results"]]
    assert len(pages) == 2
    assert pages[0] == ("b.pdf", 9)  # rank 4 after reranking, promoted by its QA answer
    assert result["results"][0]["quote"] == "ANSWER is here."


class WordTokenizer:
    """Fake slow tokenizer: one id per short word, two for words longer than six characters."""

    model_input_names = ["input_ids", "attention_mask"]
    pad_token_id = 1

    def __init__(self):
        self.vocab = {}

    def _id(self, piece):
        return self.vocab.setdefault(piece, len(self.vocab) + 10)

    def _word(self, word):
        if len(word) > 6:
            return [self._id(word + "#0"), self._id(word + "#1")]
        return [self._id(word)]

    def __call__(self, text, add_special_tokens=False):
        assert not add_special_tokens
        if isinstance(text, list):
            return {"input_ids": [[i for w in item.split() for i in self._word(w)] for item in text]}
        return {"input_ids": [i for w in text.split() for i in self._word(w)]}

    def num_special_tokens_to_add(self, pair=False):
        return 4 if pair else 2

    def build_inputs_with_special_tokens(self, first, second):
        return [0] + first + [2, 2] + second + [2]


class PeakModel:
    """Fake QA model whose start/end logits peak on chosen token ids."""

    def __init__(self, start_id, end_id):
        self.start_id, self.end_id = start_id, end_id

    def __call__(self, input_ids, attention_mask):
        start = (input_ids == self.start_id).float() * 10.0
        end = (input_ids == self.end_id).float() * 10.0
        return SimpleNamespace(start_logits=start, end_logits=end)


def test_windowed_qa_finds_answer_beyond_first_window():
    tokenizer = WordTokenizer()
    words = [f"w{i}" for i in range(1000)]
    words[950] = "answerword"  # two tokens; well past the 384-token first window
    context = "  ".join(words)
    answer_ids = tokenizer._word("answerword")
    runner = search.make_windowed_qa_runner(tokenizer, PeakModel(answer_ids[0], answer_ids[1]), torch.device("cpu"))

    result = runner("where is answerword", context)

    assert result["answer"] == "answerword"
    assert context[result["start"] : result["end"]] == "answerword"
    assert result["score"] > 0.5


def test_windowed_qa_multi_word_span_and_ignores_question_tokens():
    tokenizer = WordTokenizer()
    context = "intro text alpha beta omega outro"
    start_id, end_id = tokenizer._word("alpha")[0], tokenizer._word("omega")[0]
    runner = search.make_windowed_qa_runner(tokenizer, PeakModel(start_id, end_id), torch.device("cpu"))

    # "omega" in the question must not be picked: only context tokens are candidates.
    result = runner("omega", context)

    assert result["answer"] == "alpha beta omega"
    assert (result["start"], result["end"]) == (context.index("alpha"), context.index("omega") + 5)


def test_windowed_qa_empty_context():
    runner = search.make_windowed_qa_runner(WordTokenizer(), PeakModel(0, 0), torch.device("cpu"))
    assert runner("q", "   ") == {"answer": "", "score": 0.0, "start": -1, "end": -1}


def test_windowed_qa_with_sentencepiece_tokenizer_and_tiny_model(tmp_path):
    spm = pytest.importorskip("sentencepiece")
    transformers = pytest.importorskip("transformers")

    corpus = tmp_path / "corpus.txt"
    lines = [
        "A stack is a linear data structure where insertion and deletion happen at one end.",
        "আলোর প্রতিফলন হলো আলো কোনো মসৃণ তলে পড়ে ফিরে আসা।",
        "ऊष्मागतिकी ऊर्जा और ताप के संबंध का अध्ययन है।",
    ]
    corpus.write_text("\n".join(lines * 50), encoding="utf-8")
    prefix = str(tmp_path / "spm")
    spm.SentencePieceTrainer.train(
        input=str(corpus), model_prefix=prefix, vocab_size=120, character_coverage=1.0, hard_vocab_limit=False
    )
    tokenizer = transformers.XLMRobertaTokenizer(vocab_file=prefix + ".model")
    torch.manual_seed(0)
    config = transformers.XLMRobertaConfig(
        vocab_size=len(tokenizer), hidden_size=32, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=64, max_position_embeddings=520, pad_token_id=tokenizer.pad_token_id,
    )
    model = transformers.XLMRobertaForQuestionAnswering(config).eval()
    runner = search.make_windowed_qa_runner(tokenizer, model, torch.device("cpu"))

    context = " ".join(lines * 40)  # several windows long
    result = runner("আলোর প্রতিফলন কাকে বলে?", context)

    assert 0 <= result["start"] < result["end"] <= len(context)
    assert context[result["start"] : result["end"]] == result["answer"]
    assert result["answer"] == result["answer"].strip() and result["answer"]
    assert 0.0 < result["score"] <= 1.0

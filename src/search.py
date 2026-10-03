"""OSISS semantic search + extractive QA pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Tuple

from sentence_transformers import CrossEncoder, SentenceTransformer
import torch
from transformers import AutoModelForQuestionAnswering, AutoTokenizer, XLMRobertaTokenizer, pipeline

from clients import get_elasticsearch_client
from config import settings
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
from utils import detect_language_code

# Extractive QA windowing for the manual runner (same defaults as the Transformers QA pipeline).
QA_MAX_SEQ_LEN = 384
QA_DOC_STRIDE = 128
QA_MAX_QUESTION_TOKENS = 64


def resolve_runtime_devices() -> tuple[str, int]:
    """Resolve runtime device configuration for embedder and QA pipelines."""
    configured = settings.inference_device

    if configured == "cpu":
        return "cpu", -1

    if configured == "cuda":
        if torch.cuda.is_available():
            return "cuda", 0
        print("[OSISS] CUDA requested but unavailable. Falling back to CPU.")
        return "cpu", -1

    if torch.cuda.is_available():
        return "cuda", 0
    return "cpu", -1


def build_qa_runner(qa_device: int) -> Callable[[str, str], Dict]:
    """Build an extractive QA runner compatible with different Transformers versions."""
    try:
        qa_pipeline = pipeline(
            "question-answering",
            model=settings.qa_model_path,
            tokenizer=settings.qa_model_path,
            device=qa_device,
            local_files_only=True,
        )

        def run_with_pipeline(question: str, context: str) -> Dict:
            return qa_pipeline(question=question, context=context)

        return run_with_pipeline
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Falling back to manual QA runner: {exc}")

    device = torch.device("cuda:0" if qa_device == 0 else "cpu")
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            settings.qa_model_path,
            local_files_only=True,
            use_fast=False,
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] AutoTokenizer load failed, using SentencePiece fallback: {exc}")
        spm_path = os.path.join(settings.qa_model_path, "sentencepiece.bpe.model")
        tokenizer = XLMRobertaTokenizer(vocab_file=spm_path)

    model = AutoModelForQuestionAnswering.from_pretrained(settings.qa_model_path, local_files_only=True)
    model.to(device)
    model.eval()
    return make_windowed_qa_runner(tokenizer, model, device)


def make_windowed_qa_runner(tokenizer, model, device) -> Callable[[str, str], Dict]:
    """QA over overlapping token windows, so long chunks are read to the end.

    Works with slow (SentencePiece) tokenizers, which have no offset mapping:
    the context is tokenized word by word and answers map back to whole words.
    """
    num_special = tokenizer.num_special_tokens_to_add(pair=True)
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else 0
    use_token_types = "token_type_ids" in getattr(tokenizer, "model_input_names", [])

    def run_windowed_qa(question: str, context: str) -> Dict:
        empty = {"answer": "", "score": 0.0, "start": -1, "end": -1}
        word_spans = [match.span() for match in re.finditer(r"\S+", context)]
        if not word_spans:
            return empty

        question_ids = tokenizer(question, add_special_tokens=False)["input_ids"][:QA_MAX_QUESTION_TOKENS]
        word_ids = tokenizer([context[s:e] for s, e in word_spans], add_special_tokens=False)["input_ids"]
        budget = max(1, QA_MAX_SEQ_LEN - len(question_ids) - num_special)
        windows = plan_token_windows([max(1, len(ids)) for ids in word_ids], budget, QA_DOC_STRIDE)

        batch = []
        for first_word, end_word in windows:
            context_ids: List[int] = []
            owners: List[int] = []
            for word_index in range(first_word, end_word):
                context_ids.extend(word_ids[word_index])
                owners.extend([word_index] * len(word_ids[word_index]))
            context_ids, owners = context_ids[:budget], owners[:budget]
            if not context_ids:
                continue
            input_ids = tokenizer.build_inputs_with_special_tokens(question_ids, context_ids)
            token_types = (
                tokenizer.create_token_type_ids_from_sequences(question_ids, context_ids) if use_token_types else None
            )
            batch.append((input_ids, token_types, locate_subsequence(input_ids, context_ids), owners))
        if not batch:
            return empty

        width = max(len(input_ids) for input_ids, _, _, _ in batch)
        input_tensor = torch.full((len(batch), width), pad_id, dtype=torch.long)
        attention_mask = torch.zeros_like(input_tensor)
        type_tensor = torch.zeros_like(input_tensor)
        for row, (input_ids, token_types, _, _) in enumerate(batch):
            input_tensor[row, : len(input_ids)] = torch.tensor(input_ids, dtype=torch.long)
            attention_mask[row, : len(input_ids)] = 1
            if token_types is not None:
                type_tensor[row, : len(token_types)] = torch.tensor(token_types, dtype=torch.long)

        model_inputs = {"input_ids": input_tensor.to(device), "attention_mask": attention_mask.to(device)}
        if use_token_types:
            model_inputs["token_type_ids"] = type_tensor.to(device)
        with torch.no_grad():
            outputs = model(**model_inputs)
        start_logits = outputs.start_logits.float().cpu().numpy()
        end_logits = outputs.end_logits.float().cpu().numpy()

        best: Optional[Tuple[int, int, float]] = None
        for row, (_, _, offset, owners) in enumerate(batch):
            positions = list(range(offset, offset + len(owners)))
            start, end, score = best_answer_span(start_logits[row], end_logits[row], positions)
            if best is None or score > best[2]:
                best = (owners[start - offset], owners[end - offset], score)

        first_word, last_word, score = best
        start_char, end_char = word_spans[first_word][0], word_spans[last_word][1]
        return {"answer": context[start_char:end_char], "score": score, "start": start_char, "end": end_char}

    return run_windowed_qa


def exercise_exclusion() -> Optional[Dict]:
    """Bool clause hiding exercise chunks, or None when they are included."""
    if not settings.exclude_exercises:
        return None
    return {"must_not": [{"term": {"is_exercise": True}}]}


def build_keyword_query(query_text: str, boost: float = 1.0) -> Dict:
    """BM25 query over the standard field plus the query language's analyzed subfield."""
    text = keyword_query_text(query_text, strip_stopwords=settings.query_stopwords)
    fields = keyword_fields(detect_language_code(query_text), settings.language_analyzers)
    if fields == ["text"]:
        clause: Dict = {"match": {"text": {"query": text, "boost": boost}}}
    else:
        clause = {
            "multi_match": {"query": text, "fields": fields, "type": "best_fields", "tie_breaker": 0.3, "boost": boost}
        }
    exclusion = exercise_exclusion()
    return {"bool": {"must": [clause], **exclusion}} if exclusion else clause


def build_knn(query_vector: List[float], k: int, num_candidates: int) -> Dict:
    knn: Dict = {"field": "embedding", "query_vector": query_vector, "k": k, "num_candidates": num_candidates}
    exclusion = exercise_exclusion()
    if exclusion:
        knn["filter"] = {"bool": exclusion}
    return knn


def to_candidate(hit: Dict, score: float) -> Dict:
    return {"id": hit.get("_id"), "score": score, "source": hit.get("_source", {})}


def retrieve_top_chunks(es_client, query_text: str, query_vector: List[float], top_k: int = 3) -> List[Dict]:
    """Retrieve top-k chunks by hybrid kNN (semantic) + BM25 (keyword) retrieval.

    HYBRID_MODE=rrf (default) runs both searches and fuses them by rank;
    HYBRID_MODE=sum adds kNN and boosted BM25 scores in one query.
    """
    if settings.hybrid_mode == "sum":
        response = es_client.search(
            index=settings.elasticsearch_index,
            query=build_keyword_query(query_text, boost=settings.hybrid_keyword_boost),
            knn=build_knn(query_vector, top_k, max(10, top_k * 10)),
            size=top_k,
        )
        hits = response.get("hits", {}).get("hits", [])
        return [to_candidate(hit, hit.get("_score", 0.0)) for hit in hits]

    window = max(top_k, settings.retrieval_candidates)
    dense = es_client.search(
        index=settings.elasticsearch_index,
        knn=build_knn(query_vector, window, min(10000, max(100, window * 10))),
        size=window,
    )
    keyword = es_client.search(index=settings.elasticsearch_index, query=build_keyword_query(query_text), size=window)
    dense_hits = dense.get("hits", {}).get("hits", [])
    keyword_hits = keyword.get("hits", {}).get("hits", [])

    hits_by_id: Dict[str, Dict] = {}
    for hit in dense_hits + keyword_hits:
        hits_by_id.setdefault(hit["_id"], hit)
    fused = reciprocal_rank_fusion(
        [[hit["_id"] for hit in dense_hits], [hit["_id"] for hit in keyword_hits]],
        weights=[1.0, settings.rrf_keyword_weight],
        k=settings.rrf_k,
    )
    return [to_candidate(hits_by_id[doc_id], score) for doc_id, score in fused[:top_k]]


def answer_with_qa(qa_runner: Callable[[str, str], Dict], query: str, chunk_text: str) -> Dict:
    """Run extractive QA to identify exact answer span in a chunk."""
    result = qa_runner(query, chunk_text)
    return {
        "answer": result.get("answer", ""),
        "score": float(result.get("score", 0.0)),
        "start": int(result.get("start", -1)),
        "end": int(result.get("end", -1)),
    }


def split_sentence_spans(text: str) -> List[Tuple[int, int, str]]:
    """Split text into sentence spans while preserving character offsets."""
    spans: List[Tuple[int, int, str]] = []
    for match in re.finditer(r"[^.!?।！？]+[.!?।！？]?", text):
        start = match.start()
        end = match.end()
        sentence = match.group().strip()
        if sentence:
            spans.append((start, end, sentence))
    return spans


def get_anchor_sentence_index(sentence_spans: List[Tuple[int, int, str]], start: int, end: int) -> int:
    """Find the sentence index that contains the QA answer span."""
    if not sentence_spans:
        return -1

    for idx, (sent_start, sent_end, _) in enumerate(sentence_spans):
        if start >= sent_start and end <= sent_end:
            return idx

    for idx, (sent_start, sent_end, _) in enumerate(sentence_spans):
        if sent_start <= start < sent_end:
            return idx

    return 0


def extract_complete_sentence(chunk_text: str, start: int, end: int) -> Tuple[str, int, int]:
    """Return complete sentence containing answer span and its bounds."""
    normalized = " ".join(chunk_text.split())
    if not normalized:
        return "", -1, -1

    sentence_spans = split_sentence_spans(normalized)
    if not sentence_spans:
        return normalized, 0, len(normalized)

    anchor_idx = get_anchor_sentence_index(sentence_spans, start, end)
    if anchor_idx < 0:
        first_start, first_end, first_sentence = sentence_spans[0]
        return first_sentence, first_start, first_end

    sent_start, sent_end, sent_text = sentence_spans[anchor_idx]
    return sent_text, sent_start, sent_end


def extract_matched_paragraph(chunk_text: str, start: int, end: int, max_words: int = 140) -> str:
    """Build a paragraph-like excerpt using complete sentences only."""
    normalized = " ".join(chunk_text.split())
    if not normalized:
        return ""

    sentence_spans = split_sentence_spans(normalized)
    if not sentence_spans:
        return normalized

    anchor_idx = get_anchor_sentence_index(sentence_spans, start, end)
    if anchor_idx < 0:
        return sentence_spans[0][2]

    selected_indices = {anchor_idx}
    total_words = len(sentence_spans[anchor_idx][2].split())
    left = anchor_idx - 1
    right = anchor_idx + 1

    while total_words < max_words and (left >= 0 or right < len(sentence_spans)):
        added = False
        if left >= 0:
            left_words = len(sentence_spans[left][2].split())
            if total_words + left_words <= max_words or total_words < max_words * 0.6:
                selected_indices.add(left)
                total_words += left_words
                left -= 1
                added = True
        if right < len(sentence_spans):
            right_words = len(sentence_spans[right][2].split())
            if total_words + right_words <= max_words or total_words < max_words * 0.8:
                selected_indices.add(right)
                total_words += right_words
                right += 1
                added = True
        if not added:
            break

    ordered_sentences = [sentence_spans[idx][2] for idx in sorted(selected_indices)]
    return " ".join(ordered_sentences).strip()


@lru_cache(maxsize=1)
def get_es_client():
    """Create the Elasticsearch client once and reuse it across requests."""
    return get_elasticsearch_client()


@lru_cache(maxsize=1)
def get_models() -> Tuple[SentenceTransformer, Callable[[str, str], Dict]]:
    """Load the embedder and QA runner once per process."""
    embedder_device, qa_device = resolve_runtime_devices()
    print(f"[OSISS] Retriever device: {embedder_device} | QA device: {'cuda:0' if qa_device == 0 else 'cpu'}")
    embedder = SentenceTransformer(settings.bge_model_path, device=embedder_device, local_files_only=True)
    return embedder, build_qa_runner(qa_device)


def final_score(result: Dict) -> float:
    """Blend rerank and QA scores; falls back to QA alone when reranking is off."""
    rerank = result.get("rerank_score")
    if rerank is None:
        return result["qa_score"]
    weight = settings.qa_rank_weight
    return (1.0 - weight) * rerank + weight * result["qa_score"]


@lru_cache(maxsize=1)
def get_reranker() -> Optional[CrossEncoder]:
    """Load the cross-encoder reranker once; None when disabled or not downloaded."""
    if not settings.rerank_enabled:
        return None
    if not os.path.isdir(settings.reranker_model_path):
        print(f"[OSISS] Reranker not found at '{settings.reranker_model_path}'; skipping reranking.")
        return None
    device, _ = resolve_runtime_devices()
    return CrossEncoder(settings.reranker_model_path, device=device, local_files_only=True)


def rerank_candidates(query: str, candidates: List[Dict]) -> List[Dict]:
    """Score (query, chunk) pairs with the cross-encoder and sort best-first."""
    reranker = get_reranker()
    if reranker is None or not candidates:
        return candidates

    pairs = [(query, item["source"].get("text", "")) for item in candidates]
    scores = reranker.predict(pairs, batch_size=8, show_progress_bar=False)
    for item, score in zip(candidates, scores):
        item["rerank_score"] = float(score)
    return sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)


def diversify_candidates(candidates: List[Dict], limit: int) -> List[Dict]:
    """Drop same-page / near-duplicate chunks, then pick `limit` with MMR."""
    if settings.dedupe_results:
        candidates = dedupe_candidates(candidates)
    return mmr_select(candidates, limit, settings.mmr_lambda)


def search_and_extract(query: str, top_k: int = 3) -> Dict:
    """Full search pipeline from query embedding to extractive answer JSON."""
    es_client = get_es_client()
    embedder, qa_runner = get_models()

    query = normalize_query(query)
    query_embedding = embedder.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0].tolist()
    candidates = retrieve_top_chunks(
        es_client, query, query_embedding, top_k=max(top_k, settings.retrieval_candidates)
    )
    candidates = rerank_candidates(query, candidates)
    candidates = diversify_candidates(candidates, top_k + max(0, settings.qa_extra_candidates))

    if not candidates:
        return {
            "query": query,
            "results": [],
            "message": "No relevant chunks found.",
        }

    enriched_results = []
    for rank, item in enumerate(candidates, start=1):
        src = item["source"]
        chunk_text = src.get("text", "")
        if not chunk_text:
            continue

        qa_result = answer_with_qa(qa_runner, query, chunk_text)
        if qa_result["score"] < settings.min_qa_score:
            continue
        full_answer, answer_start, answer_end = extract_complete_sentence(
            chunk_text=chunk_text,
            start=qa_result["start"],
            end=qa_result["end"],
        )
        matched_paragraph = extract_matched_paragraph(
            chunk_text=chunk_text,
            start=qa_result["start"],
            end=qa_result["end"],
        )

        enriched_results.append(
            {
                "rank": rank,
                "retrieval_score": item["score"],
                "rerank_score": item.get("rerank_score"),
                "qa_score": qa_result["score"],
                "quote": full_answer or qa_result["answer"],
                "answer_span": {
                    "start": answer_start,
                    "end": answer_end,
                },
                "source": {
                    "book_title": src.get("title"),
                    "author": src.get("author"),
                    "department": src.get("department"),
                    "page_number": src.get("page_number"),
                    "file_path": src.get("file_path"),
                },
                "matched_paragraph": matched_paragraph,
                "chunk_preview": chunk_text,
            }
        )

    enriched_results.sort(key=final_score, reverse=True)
    return {
        "query": query,
        "results": enriched_results[:top_k],
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line inputs for search requests."""
    parser = argparse.ArgumentParser(description="OSISS Search + QA")
    parser.add_argument("--query", type=str, required=True, help="Search question in Bengali, Hindi, or English")
    parser.add_argument("--top-k", type=int, default=3, help="Number of chunks to retrieve")
    return parser.parse_args()


def main() -> int:
    """Program entrypoint for CLI usage."""
    args = parse_args()
    try:
        result = search_and_extract(query=args.query, top_k=args.top_k)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Search pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

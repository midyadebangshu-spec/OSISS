"""OSISS semantic search + extractive QA pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Callable, Dict, List, Tuple

from sentence_transformers import SentenceTransformer
import torch
from transformers import AutoModelForQuestionAnswering, AutoTokenizer, XLMRobertaTokenizer, pipeline

from clients import get_elasticsearch_client
from config import settings


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

    def run_manual_qa(question: str, context: str) -> Dict:
        encoded = tokenizer(
            question,
            context,
            return_tensors="pt",
            truncation="only_second",
            max_length=512,
            return_offsets_mapping=True,
        )

        offset_mapping = encoded.pop("offset_mapping")[0]
        sequence_ids = encoded.sequence_ids(0)
        model_inputs = {key: value.to(device) for key, value in encoded.items()}

        with torch.no_grad():
            outputs = model(**model_inputs)

        start_logits = outputs.start_logits[0]
        end_logits = outputs.end_logits[0]

        context_token_indices = [idx for idx, seq_id in enumerate(sequence_ids) if seq_id == 1]
        if not context_token_indices:
            return {"answer": "", "score": 0.0, "start": -1, "end": -1}

        best_start = max(context_token_indices, key=lambda i: float(start_logits[i]))
        candidate_ends = [i for i in context_token_indices if i >= best_start]
        best_end = max(candidate_ends, key=lambda i: float(end_logits[i])) if candidate_ends else best_start

        start_char = int(offset_mapping[best_start][0])
        end_char = int(offset_mapping[best_end][1])
        answer = context[start_char:end_char].strip()

        score = float(torch.sigmoid(start_logits[best_start]) * torch.sigmoid(end_logits[best_end]))
        return {
            "answer": answer,
            "score": score,
            "start": start_char,
            "end": end_char,
        }

    return run_manual_qa


def retrieve_top_chunks(es_client, query_vector: List[float], top_k: int = 3) -> List[Dict]:
    """Retrieve top-k semantically similar chunks from Elasticsearch."""
    response = es_client.search(
        index=settings.elasticsearch_index,
        knn={
            "field": "embedding",
            "query_vector": query_vector,
            "k": top_k,
            "num_candidates": max(10, top_k * 10),
        },
        size=top_k,
    )

    hits = response.get("hits", {}).get("hits", [])
    return [
        {
            "score": hit.get("_score", 0.0),
            "source": hit.get("_source", {}),
        }
        for hit in hits
    ]


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


def search_and_extract(query: str, top_k: int = 3) -> Dict:
    """Full search pipeline from query embedding to extractive answer JSON."""
    es_client = get_elasticsearch_client()
    embedder_device, qa_device = resolve_runtime_devices()
    print(f"[OSISS] Retriever device: {embedder_device} | QA device: {'cuda:0' if qa_device == 0 else 'cpu'}")

    embedder = SentenceTransformer(settings.bge_model_path, device=embedder_device, local_files_only=True)
    qa_runner = build_qa_runner(qa_device)

    query_embedding = embedder.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0].tolist()
    candidates = retrieve_top_chunks(es_client, query_embedding, top_k=top_k)

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
                "qa_score": qa_result["score"],
                "quote": full_answer or qa_result["answer"],
                "answer_span": {
                    "start": answer_start,
                    "end": answer_end,
                },
                "source": {
                    "book_title": src.get("title"),
                    "author": src.get("author"),
                    "page_number": src.get("page_number"),
                    "file_path": src.get("file_path"),
                },
                "matched_paragraph": matched_paragraph,
                "chunk_preview": chunk_text,
            }
        )

    enriched_results.sort(key=lambda x: x["qa_score"], reverse=True)
    return {
        "query": query,
        "results": enriched_results,
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

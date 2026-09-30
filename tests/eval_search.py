"""Search accuracy evaluation harness.

Modes:
  --validate         check the question set against the ingested text (DB only, no models)
  --mode retrieval   page-level retrieval metrics (embedder + Elasticsearch, no QA)
  --mode full        full pipeline: retrieval + QA; adds answer-keyword hit rate

Run from the project root:  .venv/bin/python tests/eval_search.py --mode retrieval
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "questions.json")


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def is_match(question: Dict, file_path: str, page: int) -> bool:
    return any(e["file"] in (file_path or "") and page in e["pages"] for e in question["expected"])


def validate(questions: List[Dict]) -> int:
    from clients import get_postgres_connection

    cursor = get_postgres_connection(max_retries=2).cursor()
    bad = 0
    for q in questions:
        found = False
        for exp in q["expected"]:
            cursor.execute(
                "SELECT string_agg(c.chunk_text, ' ') FROM chunks c JOIN books b ON b.id = c.book_id "
                "WHERE b.file_path LIKE %s AND c.page_number = ANY(%s)",
                (f"%{exp['file']}%", exp["pages"]),
            )
            text = cursor.fetchone()[0] or ""
            found = found or norm(q["answer_contains"]) in norm(text)
        if not found:
            bad += 1
            print(f"[INVALID] {q['id']} '{q['query']}': keyword '{q['answer_contains']}' not on expected pages")
    print(f"Validated {len(questions)} questions, {bad} invalid.")
    return 1 if bad else 0


def run(questions: List[Dict], mode: str, depth: int) -> int:
    from search import get_es_client, get_models, retrieve_top_chunks, search_and_extract

    ks = (1, 3, 5)
    hits = {k: 0 for k in ks}
    reciprocal = 0.0
    answer_hits = 0
    exercise_results = 0
    total_results = 0
    per_lang: Dict[str, List[int]] = {}
    failures = []

    for q in questions:
        if mode == "retrieval":
            embedder, _ = get_models()
            vector = embedder.encode([q["query"]], normalize_embeddings=True, convert_to_numpy=True)[0].tolist()
            chunks = retrieve_top_chunks(get_es_client(), q["query"], vector, top_k=depth)
            ranked = [(c["source"].get("file_path", ""), int(c["source"].get("page_number") or 0)) for c in chunks]
            texts = [c["source"].get("text", "") for c in chunks]
            chunk_texts = texts
        else:
            result = search_and_extract(q["query"], top_k=depth)
            ranked = [
                (r["source"].get("file_path", ""), int(r["source"].get("page_number") or 0)) for r in result["results"]
            ]
            texts = [f"{r['quote']} {r['matched_paragraph']}" for r in result["results"]]
            chunk_texts = [r["chunk_preview"] for r in result["results"]]

        from utils import is_exercise_chunk

        exercise_results += sum(is_exercise_chunk(t) for t in chunk_texts[:3])
        total_results += len(chunk_texts[:3])
        rank = next((i for i, (f, p) in enumerate(ranked, 1) if is_match(q, f, p)), None)
        for k in ks:
            hits[k] += bool(rank and rank <= k)
        reciprocal += 1.0 / rank if rank else 0.0
        if mode == "full" and texts and norm(q["answer_contains"]) in norm(texts[0]):
            answer_hits += 1
        per_lang.setdefault(q["lang"], []).append(1 if rank and rank <= 3 else 0)
        if rank is None or rank > 3:
            failures.append((q["id"], q["query"], rank))

    n = len(questions)
    print(f"\nMode: {mode} | questions: {n} | depth: {depth}")
    for k in ks:
        print(f"  hit@{k}: {hits[k] / n:.2f}")
    print(f"  MRR:   {reciprocal / n:.3f}")
    print(f"  exercise chunks in top 3: {exercise_results}/{total_results} ({exercise_results / max(1, total_results):.1%})")
    if mode == "full":
        print(f"  top-1 answer contains keyword: {answer_hits / n:.2f}")
    for lang, values in sorted(per_lang.items()):
        print(f"  hit@3 [{lang}]: {sum(values) / len(values):.2f} ({len(values)} q)")
    if failures:
        print("\nMisses (not in top 3):")
        for qid, query, rank in failures:
            print(f"  {qid} rank={rank} {query}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OSISS search evaluation")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    parser.add_argument("--depth", type=int, default=10, help="results considered per query")
    args = parser.parse_args()

    with open(QUESTIONS_PATH, encoding="utf-8") as handle:
        questions = json.load(handle)
    if args.validate:
        return validate(questions)
    return run(questions, args.mode, args.depth)


if __name__ == "__main__":
    raise SystemExit(main())

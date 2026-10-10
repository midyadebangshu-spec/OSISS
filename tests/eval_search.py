"""Search accuracy evaluation harness.

Modes:
  --validate         check the question set against the ingested text (DB only, no models)
  --mode retrieval   page-level retrieval metrics (embedder + Elasticsearch, no QA)
  --mode full        full pipeline: retrieval + QA; adds answer-keyword hit rate
  --compare          table of the search features (HYBRID_MODE, LANGUAGE_ANALYZERS, QUERY_STOPWORDS,
                     DEDUPE_RESULTS/MMR_LAMBDA, QA_EXTRA_CANDIDATES) toggled on/off
  --set KEY=VALUE    override a setting for this run, e.g. --set rrf_keyword_weight=0.5

Run from the project root:  .venv/bin/python tests/eval_search.py --mode retrieval
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
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


def first_match_key(question: Dict, file_path: str, page: int):
    for exp in question["expected"]:
        if exp["file"] in (file_path or "") and page in exp["pages"]:
            return exp["file"], page
    return None


def ndcg(question: Dict, ranked: List[Tuple[str, int]], cutoff: int) -> float:
    """Binary-relevance nDCG; each expected (file, page) counts once."""
    seen = set()
    dcg = 0.0
    for position, (file_path, page) in enumerate(ranked[:cutoff], 1):
        key = first_match_key(question, file_path, page)
        if key and key not in seen:
            seen.add(key)
            dcg += 1.0 / math.log2(position + 1)
    relevant = sum(len(exp["pages"]) for exp in question["expected"])
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(relevant, cutoff) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate(questions: List[Dict], mode: str, depth: int) -> Dict:
    """Run every question and return aggregate metrics (no printing)."""
    from ranking import normalize_query
    from search import get_es_client, get_models, retrieve_top_chunks, search_and_extract
    from utils import is_exercise_chunk

    ks = (1, 3, 5)
    hits = {k: 0 for k in ks}
    reciprocal = 0.0
    ndcg_total = 0.0
    answer_hits = 0
    exercise_results = 0
    total_results = 0
    per_lang: Dict[str, List[int]] = {}
    seconds: Dict[str, List[float]] = {}
    failures = []
    cutoff = min(10, depth)

    for q in questions:
        started = time.perf_counter()
        if mode == "retrieval":
            embedder, _ = get_models()
            query = normalize_query(q["query"])
            vector = embedder.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0].tolist()
            chunks = retrieve_top_chunks(get_es_client(), query, vector, top_k=depth)
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

        seconds.setdefault(q.get("group", q["lang"]), []).append(time.perf_counter() - started)
        exercise_results += sum(is_exercise_chunk(t) for t in chunk_texts[:3])
        total_results += len(chunk_texts[:3])
        rank = next((i for i, (f, p) in enumerate(ranked, 1) if is_match(q, f, p)), None)
        for k in ks:
            hits[k] += bool(rank and rank <= k)
        reciprocal += 1.0 / rank if rank else 0.0
        ndcg_total += ndcg(q, ranked, cutoff)
        if mode == "full" and texts and norm(q["answer_contains"]) in norm(texts[0]):
            answer_hits += 1
        per_lang.setdefault(q.get("group", q["lang"]), []).append(1 if rank and rank <= 3 else 0)
        if rank is None or rank > 3:
            failures.append((q["id"], q["query"], rank))

    n = len(questions)
    return {
        "mode": mode,
        "n": n,
        "depth": depth,
        "hit": {k: hits[k] / n for k in ks},
        "mrr": reciprocal / n,
        "ndcg_cutoff": cutoff,
        "ndcg": ndcg_total / n,
        "answer_hit": answer_hits / n if mode == "full" else None,
        "exercise": (exercise_results, total_results),
        "per_lang": {lang: (sum(v) / len(v), len(v)) for lang, v in sorted(per_lang.items())},
        "seconds": {lang: sum(v) / len(v) for lang, v in sorted(seconds.items())},
        "failures": failures,
    }


def print_report(metrics: Dict) -> None:
    print(f"\nMode: {metrics['mode']} | questions: {metrics['n']} | depth: {metrics['depth']}")
    for k, value in metrics["hit"].items():
        print(f"  hit@{k}: {value:.2f}")
    print(f"  MRR:   {metrics['mrr']:.3f}")
    print(f"  nDCG@{metrics['ndcg_cutoff']}: {metrics['ndcg']:.3f}")
    exercise, total = metrics["exercise"]
    print(f"  exercise chunks in top 3: {exercise}/{total} ({exercise / max(1, total):.1%})")
    if metrics["answer_hit"] is not None:
        print(f"  top-1 answer contains keyword: {metrics['answer_hit']:.2f}")
    for lang, (value, count) in metrics["per_lang"].items():
        print(f"  hit@3 [{lang}]: {value:.2f} ({count} q, {metrics['seconds'][lang]:.2f}s/query)")
    if metrics["failures"]:
        print("\nMisses (not in top 3):")
        for qid, query, rank in metrics["failures"]:
            print(f"  {qid} rank={rank} {query}")


def run(questions: List[Dict], mode: str, depth: int) -> int:
    print_report(evaluate(questions, mode, depth))
    return 0


# Feature switches from the search-improvement change set. "all on" uses the
# defaults (plus any --set overrides); each ablation turns one feature off;
# "baseline" turns all of them off.
ALL_ON = {
    "hybrid_mode": "rrf",
    "language_analyzers": True,
    "query_stopwords": True,
    "dedupe_results": True,
    "mmr_lambda": 0.85,
    "qa_extra_candidates": 2,
}
VARIANTS = [
    ("baseline (all off)", {"hybrid_mode": "sum", "language_analyzers": False, "query_stopwords": False,
                            "dedupe_results": False, "mmr_lambda": 1.0, "qa_extra_candidates": 0}),
    ("all on", {}),
    ("A off: score-sum fusion", {"hybrid_mode": "sum"}),
    ("B off: no language fields", {"language_analyzers": False}),
    ("C off: keep question words", {"query_stopwords": False}),
    ("D off: no dedupe / MMR", {"dedupe_results": False, "mmr_lambda": 1.0}),
    ("E off: QA on top_k only", {"qa_extra_candidates": 0}),
]


def apply_settings(overrides: Dict) -> Dict:
    """Override frozen settings in place; returns the previous values."""
    from config import settings

    previous = {}
    for key, value in overrides.items():
        previous[key] = getattr(settings, key)
        object.__setattr__(settings, key, value)
    return previous


def parse_overrides(pairs: List[str]) -> Dict:
    from config import settings

    overrides = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        key = key.strip().lower()
        if not hasattr(settings, key):
            raise SystemExit(f"Unknown setting '{key}'")
        current = getattr(settings, key)
        if isinstance(current, bool):
            value = raw.strip().lower() in {"1", "true", "yes"}
        else:
            value = type(current)(raw.strip())
        overrides[key] = value
    return overrides


def compare(questions: List[Dict], mode: str, depth: int, user_overrides: Dict) -> int:
    rows = []
    for name, overrides in VARIANTS:
        previous = apply_settings({**ALL_ON, **user_overrides, **overrides})
        try:
            print(f"[eval] running '{name}' ...", file=sys.stderr)
            rows.append((name, evaluate(questions, mode, depth)))
        finally:
            apply_settings(previous)

    langs = sorted({lang for _, m in rows for lang in m["per_lang"]})
    cutoff = rows[0][1]["ndcg_cutoff"]
    header = ["variant", "hit@1", "hit@3", "hit@5", "MRR", f"nDCG@{cutoff}"]
    if mode == "full":
        header.append("ans@1")
    header += [f"hit@3 {lang}" for lang in langs]
    print(f"\nMode: {mode} | questions: {rows[0][1]['n']} | depth: {depth}")
    if mode == "retrieval":
        print("(retrieval mode only exercises A-C; D and E act after reranking, use --mode full)")
    print(" | ".join(header))
    for name, m in rows:
        cells = [name, *(f"{m['hit'][k]:.2f}" for k in (1, 3, 5)), f"{m['mrr']:.3f}", f"{m['ndcg']:.3f}"]
        if mode == "full":
            cells.append(f"{m['answer_hit']:.2f}")
        cells += [f"{m['per_lang'].get(lang, (0.0, 0))[0]:.2f}" for lang in langs]
        print(" | ".join(cells))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OSISS search evaluation")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--mode", choices=["retrieval", "full"], default="retrieval")
    parser.add_argument("--depth", type=int, default=10, help="results considered per query")
    parser.add_argument("--compare", action="store_true", help="table of each search feature on/off")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a setting, e.g. --set mmr_lambda=0.8 (repeatable)")
    args = parser.parse_args()

    with open(QUESTIONS_PATH, encoding="utf-8") as handle:
        questions = json.load(handle)
    if args.validate:
        return validate(questions)
    overrides = parse_overrides(args.set)
    if args.compare:
        return compare(questions, args.mode, args.depth, overrides)
    apply_settings(overrides)
    return run(questions, args.mode, args.depth)


if __name__ == "__main__":
    raise SystemExit(main())

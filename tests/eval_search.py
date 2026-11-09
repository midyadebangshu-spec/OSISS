"""Search accuracy evaluation harness.

Modes:
  --validate         check the question set against the ingested text (DB only, no models)
  --mode retrieval   page-level retrieval metrics (embedder + Elasticsearch, no QA)
  --mode full        full pipeline: retrieval + QA; adds answer-keyword hit rate
  --compare          table of the search features (HYBRID_MODE, LANGUAGE_ANALYZERS, QUERY_STOPWORDS,
                     KEYWORD_SAME_LANGUAGE, DEDUPE_RESULTS/MMR_LAMBDA, QA_EXTRA_CANDIDATES) toggled on/off
  --set KEY=VALUE    override a setting for this run, e.g. --set rrf_keyword_weight=0.5
  --questions PATH   question file (default eval/questions.json; use eval/questions_v2.json for the independent set)
  --split NAME       only questions whose "split" is NAME ("tune" or "heldout"); questions without one count as "tune"
  --save PATH        write the metrics as JSON (the reference run)
  --baseline PATH    print the change against a saved run

Question fields: query, lang, id, expected [{file, pages}], answer_contains, plus the optional
  group      per-group breakdown (overrides lang)
  type       factoid | paraphrase | explain | compare | multi-para | negative (per-type breakdown)
  answer_quote  a verbatim passage that a good answer should cover (scored as quote coverage)
  no_answer  true when the corpus does not answer the question (expected [] and no answer_contains)
  split      tune | heldout (tune on one, report on the other, so the numbers are not overfitted)

Run from the project root:  .venv/bin/python tests/eval_search.py --mode retrieval
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "questions.json")


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def is_negative(question: Dict) -> bool:
    return bool(question.get("no_answer"))


def question_split(question: Dict) -> str:
    return question.get("split") or "tune"


def select_questions(questions: List[Dict], split: str = "") -> List[Dict]:
    """Questions of one split ("" = all)."""
    return [q for q in questions if not split or question_split(q) == split]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """95 % Wilson score interval of a proportion; (0, 0) for no questions."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def auc(positive: List[float], negative: List[float]) -> float:
    """Probability that a random positive scores above a random negative (ties count half); 0.5 if either is empty."""
    if not positive or not negative:
        return 0.5
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def covers_quote(answer_quote: str, texts: List[str]) -> bool:
    """True when the expected passage appears in one of the texts, ignoring case, whitespace and hyphen line breaks."""

    def squash(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    wanted = squash(answer_quote)
    return bool(wanted) and any(wanted in squash(text) for text in texts)


def is_match(question: Dict, file_path: str, page: int) -> bool:
    return any(e["file"] in (file_path or "") and page in e["pages"] for e in question["expected"])


def validate(questions: List[Dict]) -> int:
    from clients import get_postgres_connection

    cursor = get_postgres_connection(max_retries=2).cursor()
    bad = 0
    for q in questions:
        if is_negative(q):
            if q["expected"]:
                bad += 1
                print(f"[INVALID] {q['id']} '{q['query']}': no_answer question must have expected []")
            continue
        if not q["expected"]:
            bad += 1
            print(f"[INVALID] {q['id']} '{q['query']}': expected is empty (set no_answer for unanswerable questions)")
            continue
        found = False
        quote_found = not q.get("answer_quote")
        for exp in q["expected"]:
            cursor.execute(
                "SELECT string_agg(c.chunk_text, ' ') FROM chunks c JOIN books b ON b.id = c.book_id "
                "WHERE b.file_path LIKE %s AND c.page_number = ANY(%s)",
                (f"%{exp['file']}%", exp["pages"]),
            )
            text = cursor.fetchone()[0] or ""
            found = found or norm(q["answer_contains"]) in norm(text)
            quote_found = quote_found or covers_quote(q.get("answer_quote", ""), [text])
        if not found:
            bad += 1
            print(f"[INVALID] {q['id']} '{q['query']}': keyword '{q['answer_contains']}' not on expected pages")
        elif not quote_found:
            bad += 1
            print(f"[INVALID] {q['id']} '{q['query']}': answer_quote not on expected pages")
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
    """Run every question and return aggregate metrics (no printing).

    Questions with `no_answer` are not part of hit/MRR/nDCG; in full mode their top final score is compared with the
    top score of the answerable questions (AUC) to show how well the system can tell "not in the corpus".
    """
    from ranking import normalize_query
    from search import final_score, get_es_client, get_models, retrieve_top_chunks, search_and_extract
    from utils import is_exercise_chunk

    ks = (1, 3, 5)
    hits = {k: 0 for k in ks}
    reciprocal = 0.0
    ndcg_total = 0.0
    answer_hits = 0
    quote_hits = 0
    quote_total = 0
    quote_covered = 0
    exercise_results = 0
    total_results = 0
    per_lang: Dict[str, List[int]] = {}
    per_type: Dict[str, List[int]] = {}
    seconds: Dict[str, List[float]] = {}
    failures = []
    positive_scores: List[float] = []
    negative_scores: List[float] = []
    shown = 0
    answerable = 0
    from config import settings

    # depth 0: no top_k, as the website searches (relative score threshold, up to MAX_RESULTS).
    top_k = depth if depth > 0 else None
    cutoff = min(10, depth if depth > 0 else settings.max_results)

    for q in questions:
        negative = is_negative(q)
        if negative and mode == "retrieval":
            continue  # retrieval scores (RRF) say nothing about answerability
        started = time.perf_counter()
        top_score = None
        if mode == "retrieval":
            embedder, _ = get_models()
            query = normalize_query(q["query"])
            vector = embedder.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0].tolist()
            chunks = retrieve_top_chunks(get_es_client(), query, vector, top_k=depth)
            ranked = [(c["source"].get("file_path", ""), int(c["source"].get("page_number") or 0)) for c in chunks]
            texts = [c["source"].get("text", "") for c in chunks]
            quotes = texts
            chunk_texts = texts
        else:
            result = search_and_extract(q["query"], top_k=top_k)
            ranked = [
                (r["source"].get("file_path", ""), int(r["source"].get("page_number") or 0)) for r in result["results"]
            ]
            texts = [f"{r['quote']} {r['matched_paragraph']}" for r in result["results"]]
            quotes = [r["quote"] for r in result["results"]]
            chunk_texts = [r["chunk_preview"] for r in result["results"]]
            if result["results"]:
                top_score = max(final_score(r) for r in result["results"])

        group = q.get("group", q["lang"])
        seconds.setdefault(group, []).append(time.perf_counter() - started)
        shown += len(ranked)
        exercise_results += sum(is_exercise_chunk(t) for t in chunk_texts[:3])
        total_results += len(chunk_texts[:3])
        if negative:
            negative_scores.append(top_score if top_score is not None else 0.0)
            continue

        answerable += 1
        if top_score is not None:
            positive_scores.append(top_score)
        rank = next((i for i, (f, p) in enumerate(ranked, 1) if is_match(q, f, p)), None)
        for k in ks:
            hits[k] += bool(rank and rank <= k)
        reciprocal += 1.0 / rank if rank else 0.0
        ndcg_total += ndcg(q, ranked, cutoff)
        if mode == "full" and texts and norm(q["answer_contains"]) in norm(texts[0]):
            answer_hits += 1
        if mode == "full" and quotes and norm(q["answer_contains"]) in norm(quotes[0]):
            quote_hits += 1
        if q.get("answer_quote"):
            quote_total += 1
            quote_covered += covers_quote(q["answer_quote"], quotes[:1])
        found = 1 if rank and rank <= 3 else 0
        per_lang.setdefault(group, []).append(found)
        per_type.setdefault(q.get("type", "untyped"), []).append(found)
        if rank is None or rank > 3:
            failures.append((q["id"], q["query"], rank))

    n = answerable
    divisor = max(1, n)
    return {
        "mode": mode,
        "n": n,
        "negatives": len(negative_scores),
        "depth": depth,
        "hit": {k: hits[k] / divisor for k in ks},
        "hit3_interval": wilson_interval(hits[3], n),
        "mrr": reciprocal / divisor,
        "ndcg_cutoff": cutoff,
        "ndcg": ndcg_total / divisor,
        "answer_hit": answer_hits / divisor if mode == "full" else None,
        "quote_hit": quote_hits / divisor if mode == "full" else None,
        "quote_coverage": (quote_covered / quote_total, quote_total) if quote_total else None,
        "answerability_auc": auc(positive_scores, negative_scores) if negative_scores and mode == "full" else None,
        "exercise": (exercise_results, total_results),
        "per_lang": {lang: (sum(v) / len(v), len(v)) for lang, v in sorted(per_lang.items())},
        "per_type": {kind: (sum(v) / len(v), len(v)) for kind, v in sorted(per_type.items())},
        "seconds": {lang: sum(v) / len(v) for lang, v in sorted(seconds.items())},
        "failures": failures,
        "avg_results": shown / max(1, n + len(negative_scores)),
    }


def print_report(metrics: Dict, baseline: Optional[Dict] = None) -> None:
    def delta(value: float, *path: str) -> str:
        if baseline is None:
            return ""
        reference = baseline
        for key in path:
            reference = reference.get(key) if isinstance(reference, dict) else None
        if isinstance(reference, list):  # JSON turns tuples into lists
            reference = reference[0]
        if reference is None or isinstance(reference, (dict, str)):
            return ""
        return f"  ({value - reference:+.3f} vs baseline)"

    low, high = metrics["hit3_interval"]
    print(f"\nMode: {metrics['mode']} | questions: {metrics['n']} (+{metrics['negatives']} unanswerable) | depth: {metrics['depth']}")
    for k, value in metrics["hit"].items():
        extra = f"  (95% CI {low:.2f}-{high:.2f})" if k == 3 else ""
        print(f"  hit@{k}: {value:.2f}{extra}{delta(value, 'hit', str(k))}")
    print(f"  MRR:   {metrics['mrr']:.3f}{delta(metrics['mrr'], 'mrr')}")
    print(f"  results per query: {metrics['avg_results']:.1f}")
    print(f"  nDCG@{metrics['ndcg_cutoff']}: {metrics['ndcg']:.3f}{delta(metrics['ndcg'], 'ndcg')}")
    exercise, total = metrics["exercise"]
    print(f"  exercise chunks in top 3: {exercise}/{total} ({exercise / max(1, total):.1%})")
    if metrics["answer_hit"] is not None:
        print(f"  top-1 quote or paragraph contains keyword: {metrics['answer_hit']:.2f}{delta(metrics['answer_hit'], 'answer_hit')}")
        print(f"  top-1 quote alone contains keyword:        {metrics['quote_hit']:.2f}{delta(metrics['quote_hit'], 'quote_hit')}")
    if metrics["quote_coverage"]:
        value, count = metrics["quote_coverage"]
        print(f"  top-1 quote covers the expected passage: {value:.2f} ({count} q)")
    if metrics["answerability_auc"] is not None:
        print(f"  answerable vs unanswerable top score AUC: {metrics['answerability_auc']:.2f} (0.5 = cannot tell)")
    for lang, (value, count) in metrics["per_lang"].items():
        print(f"  hit@3 [{lang}]: {value:.2f} ({count} q, {metrics['seconds'][lang]:.2f}s/query)")
    if len(metrics["per_type"]) > 1 or "untyped" not in metrics["per_type"]:
        for kind, (value, count) in metrics["per_type"].items():
            print(f"  hit@3 <{kind}>: {value:.2f} ({count} q)")
    if metrics["failures"]:
        print("\nMisses (not in top 3):")
        for qid, query, rank in metrics["failures"]:
            print(f"  {qid} rank={rank} {query}")
    if metrics["n"] < 30 or any(count < 10 for _, count in metrics["per_lang"].values()):
        print("\nNote: some groups have fewer than 10 questions; differences of 1-2 questions are noise.")


def run(questions: List[Dict], mode: str, depth: int, save: str = "", baseline_path: str = "") -> int:
    metrics = evaluate(questions, mode, depth)
    baseline = None
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as handle:
            baseline = json.load(handle)
    print_report(metrics, baseline)
    if save:
        with open(save, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, ensure_ascii=False, indent=1)
        print(f"\nSaved metrics to {save}")
    return 0


# Feature switches from the search-improvement change set. "all on" uses the
# defaults (plus any --set overrides); each ablation turns one feature off;
# "baseline" turns all of them off.
ALL_ON = {
    "hybrid_mode": "rrf",
    "language_analyzers": True,
    "query_stopwords": True,
    "keyword_same_language": True,
    "dedupe_results": True,
    "mmr_lambda": 0.85,
    "qa_extra_candidates": 2,
}
VARIANTS = [
    ("baseline (all off)", {"hybrid_mode": "sum", "language_analyzers": False, "query_stopwords": False,
                            "keyword_same_language": False, "dedupe_results": False, "mmr_lambda": 1.0,
                            "qa_extra_candidates": 0}),
    ("all on", {}),
    ("A off: score-sum fusion", {"hybrid_mode": "sum"}),
    ("B off: no language fields", {"language_analyzers": False}),
    ("C off: keep question words", {"query_stopwords": False}),
    ("S off: bn/hi keywords hit all", {"keyword_same_language": False}),
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
        print("(retrieval mode only exercises A-C and S; D and E act after reranking, use --mode full)")
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
    parser.add_argument(
        "--depth", type=int, default=10, help="results per query; 0 = no top_k (relative score threshold, as the website)"
    )
    parser.add_argument("--compare", action="store_true", help="table of each search feature on/off")
    parser.add_argument("--questions", default=QUESTIONS_PATH, help="question file (JSON list)")
    parser.add_argument("--split", default="", choices=["", "tune", "heldout"], help="only this split")
    parser.add_argument("--save", default="", help="write the metrics to this JSON file")
    parser.add_argument("--baseline", default="", help="compare against metrics saved with --save")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a setting, e.g. --set mmr_lambda=0.8 (repeatable)")
    args = parser.parse_args()

    with open(args.questions, encoding="utf-8") as handle:
        questions = select_questions(json.load(handle), args.split)
    if args.validate:
        return validate(questions)
    overrides = parse_overrides(args.set)
    if args.compare:
        return compare(questions, args.mode, args.depth, overrides)
    apply_settings(overrides)
    return run(questions, args.mode, args.depth, args.save, args.baseline)


if __name__ == "__main__":
    raise SystemExit(main())

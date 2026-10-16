"""Load test for a running OSISS server: N simulated users searching at once for a fixed time.

Each user sends the evaluation questions (tests/eval/questions.json) one after another, starting at a different
question, and waits for each answer before sending the next. Reports searches per second, response times and how
many requests the server refused (429 rate limit, 503 busy).

    .venv/bin/python tests/load_test.py --url http://localhost:8000 --users 10 --duration 60
    .venv/bin/python tests/load_test.py --url https://ossis.example.com --users 20 --api-key osk_... --json out.json

The answer cache makes repeated questions nearly free. To measure the models themselves, set "Answer cache size"
to 0 in the admin app (or SEARCH_CACHE_SIZE=0) for the run; pass --admin-token to also print the server's own
view (cache hit rate, time per pipeline stage).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from collections import Counter
from typing import Dict, List, Optional

import httpx

DEFAULT_QUESTIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "questions.json")


def percentile(values: List[float], percent: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(percent / 100 * len(ordered))) - 1]


def load_questions(path: str) -> List[str]:
    with open(path, encoding="utf-8") as handle:
        return [item["query"] for item in json.load(handle) if item.get("query")]


async def user(client: httpx.AsyncClient, url: str, questions: List[str], start: int, top_k: int, deadline: float,
               headers: Dict[str, str], latencies: List[float], statuses: Counter, errors: Counter) -> None:
    index = start
    while time.monotonic() < deadline:
        query = questions[index % len(questions)]
        index += 1
        began = time.monotonic()
        try:
            response = await client.post(f"{url}/api/search", json={"query": query, "top_k": top_k}, headers=headers)
        except httpx.HTTPError as exc:
            errors[type(exc).__name__] += 1
            await asyncio.sleep(1)
            continue
        statuses[response.status_code] += 1
        if response.status_code == 200:
            latencies.append(time.monotonic() - began)
        elif response.status_code in (429, 503):
            # A well-behaved client waits as told; that is what the limits are designed for.
            await asyncio.sleep(min(float(response.headers.get("retry-after", "1")), max(0.0, deadline - time.monotonic())))


async def run(args: argparse.Namespace) -> Dict:
    questions = load_questions(args.questions)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    latencies: List[float] = []
    statuses: Counter = Counter()
    errors: Counter = Counter()
    limits = httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        health = await client.get(f"{args.url}/health")
        health.raise_for_status()
        started = time.monotonic()
        deadline = started + args.duration
        offsets = random.Random(args.seed).sample(range(len(questions)), k=min(args.users, len(questions)))
        offsets += [random.Random(args.seed + i).randrange(len(questions)) for i in range(args.users - len(offsets))]
        await asyncio.gather(*(
            user(client, args.url, questions, offset, args.top_k, deadline, headers, latencies, statuses, errors)
            for offset in offsets
        ))
        elapsed = time.monotonic() - started
        server = None
        if args.admin_token:
            response = await client.get(f"{args.url}/api/admin/metrics", headers={"X-Admin-Token": args.admin_token})
            server = response.json() if response.status_code == 200 else {"error": response.status_code}
    return {
        "url": args.url,
        "users": args.users,
        "duration_seconds": round(elapsed, 1),
        "answered": len(latencies),
        "searches_per_second": round(len(latencies) / elapsed, 3),
        "statuses": dict(sorted(statuses.items())),
        "network_errors": dict(errors),
        "latency_seconds": {
            name: (round(value, 3) if value is not None else None)
            for name, value in (("p50", percentile(latencies, 50)), ("p90", percentile(latencies, 90)),
                                ("p95", percentile(latencies, 95)), ("p99", percentile(latencies, 99)),
                                ("max", max(latencies) if latencies else None))
        },
        "server_metrics": server,
    }


def print_report(report: Dict) -> None:
    latency = report["latency_seconds"]
    print(f"OSISS load test: {report['users']} users for {report['duration_seconds']} s against {report['url']}")
    print(f"  answered            {report['answered']}  ({report['searches_per_second']} searches/s)")
    print(f"  response time       p50 {latency['p50']} s · p90 {latency['p90']} s · p95 {latency['p95']} s · "
          f"p99 {latency['p99']} s · max {latency['max']} s")
    print(f"  HTTP statuses       {report['statuses']}")
    statuses = report["statuses"]
    if statuses.get(429):
        print(f"  rate-limited (429)  {statuses[429]}: the per-IP / per-key limit; all simulated users share one address")
    if statuses.get(503):
        print(f"  busy (503)          {statuses[503]}: the search queue was full; see SEARCH_QUEUE_LIMIT")
    if report["network_errors"]:
        print(f"  network errors      {report['network_errors']}")
    server = report.get("server_metrics")
    if server and "searches" in server:
        stages = {name: round(value, 3) for name, value in (server.get("stage_seconds") or {}).items() if value}
        print(f"  server (last 5 min) cache hit rate {server.get('cache_hit_rate')}, time per stage {stages}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load-test a running OSISS server")
    parser.add_argument("--url", default="http://localhost:8000", help="server base URL")
    parser.add_argument("--users", type=int, default=10, help="simultaneous simulated users")
    parser.add_argument("--duration", type=float, default=60, help="seconds to run")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--api-key", help="send X-API-Key (needed in 'approved apps only' mode)")
    parser.add_argument("--admin-token", help="also fetch the server's own metrics afterwards")
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS, help="JSON list of {query: ...}")
    parser.add_argument("--timeout", type=float, default=180, help="per-request timeout in seconds")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", help="also write the report to this file")
    args = parser.parse_args()
    if args.users < 1 or args.duration <= 0:
        parser.error("--users must be >= 1 and --duration > 0")
    try:
        report = asyncio.run(run(args))
    except httpx.HTTPError as exc:
        print(f"Cannot reach {args.url}: {exc}", file=sys.stderr)
        return 1
    print_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

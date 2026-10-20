#!/usr/bin/env python3
"""Measures the quality of the notes the pipeline actually drafts.

Submits realistic synthetic visits (tools/transcripts/generate.py, messy style) through the full
running stack, waits for the drafts, and grades each one against its visit's answer key
(grading.py): which required facts the note states, and which distractors it wrongly includes
(a corrected dose, a declined drug, a relative's medication, small talk). Latency, safety flags and
token usage are recorded alongside, so one run answers "how good, how fast, how much".

    # with whatever model the gateway is running (fake by default, free)
    .venv/bin/python eval/run_note_eval.py --count 20 --label fake
    # re-grade a saved run after changing the grader, without new model calls
    .venv/bin/python eval/run_note_eval.py --regrade ../../docs/results/note-eval-fake.json

Results go to docs/results/note-eval-<label>.json. The notes are synthetic, so the file keeps
them for review.
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "chaos"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib  # noqa: E402  (chaos/lib.py: API, Prometheus and database helpers)
from grading import Grade, grade, summarise  # noqa: E402

# USD per million tokens (input, output), Claude API list prices.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def seconds_between(start: str, end: str | None) -> float | None:
    if not end:
        return None
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return (parse(end) - parse(start)).total_seconds()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, round(p * len(ordered)) - 1)]


def collect(visits: list[dict]) -> tuple[list[dict], dict]:
    """Runs the visits through the stack; returns per-note rows and token usage."""
    tokens_before = {t: lib.prom(f'sum(llm_gateway_tokens_total{{type="{t}"}})') or 0 for t in ("input", "output")}
    scribe, clinician = lib.login(), lib.login("clinician")
    ids = [lib.create_encounter(scribe, v) for v in visits]
    print(f"submitted {len(ids)} encounters; waiting for drafts...")
    lib.wait_until(lambda: lib.drafting_count(ids) == 0, timeout=1800, interval=5)
    time.sleep(10)  # let Prometheus scrape the gateway's final token counts
    tokens = {t: int((lib.prom(f'sum(llm_gateway_tokens_total{{type="{t}"}})') or 0) - tokens_before[t])
              for t in ("input", "output")}

    rows = []
    for encounter_id, visit in zip(ids, visits):
        d = lib.http("GET", f"{lib.API}/api/encounters/{encounter_id}", token=clinician)
        s = d["summary"]
        rows.append({
            "encounter_id": encounter_id,
            "status": s["status"],
            "draft_seconds": seconds_between(s["createdAt"], s["draftedAt"]),
            "model": d["latest"]["model"] if d["latest"] else None,
            "safety_flags": d["draftSafetyFlags"],
            "note": d["latest"]["soap"] if d["latest"] else None,
            "transcript": visit["transcript"],
            "facts": visit["facts"],
        })
    return rows, tokens


def report(rows: list[dict], tokens: dict, label: str) -> dict:
    grades: list[Grade] = []
    for row in rows:
        if row["note"]:
            g = grade(row["note"], row["facts"])
            row["grade"] = g.to_dict()
            grades.append(g)
    latencies = [r["draft_seconds"] for r in rows if r["draft_seconds"] is not None]
    models = sorted({r["model"] for r in rows if r["model"]})
    price = PRICES.get(models[0]) if len(models) == 1 else None
    summary = {
        "label": label,
        "encounters": len(rows),
        "drafted": len(grades),
        "failed": sum(1 for r in rows if r["status"] == "DRAFT_FAILED"),
        "models": models,
        **summarise(grades),
        "safety_flagged_notes": sum(1 for r in rows if any(f["severity"] == "high" for f in r["safety_flags"])),
        "draft_seconds_p50": round(statistics.median(latencies), 1) if latencies else None,
        "draft_seconds_p95": round(percentile(latencies, 0.95), 1) if latencies else None,
        "input_tokens": tokens.get("input"),
        "output_tokens": tokens.get("output"),
        "estimated_cost_usd": round(tokens["input"] / 1e6 * price[0] + tokens["output"] / 1e6 * price[1], 3)
        if price and tokens.get("input") is not None else (0.0 if models and models[0].startswith("fake") else None),
    }
    return summary


def print_summary(summary: dict) -> None:
    print(f"\n{summary['drafted']}/{summary['encounters']} drafted by {', '.join(summary['models']) or '?'}")
    print(f"fact recall: {summary['fact_recall']:.1%}   distractor violations: {summary['violation_rate']:.1%} "
          f"({summary['notes_with_violation']} of {summary['notes']} notes)")
    print(f"draft p50 {summary['draft_seconds_p50']} s, p95 {summary['draft_seconds_p95']} s; "
          f"tokens in/out {summary['input_tokens']}/{summary['output_tokens']}; cost ${summary['estimated_cost_usd']}")
    print(f"\n{'fact kind':<30}{'type':<11}{'n':>5}{'rate':>8}")
    for kind, k in summary["by_kind"].items():
        print(f"{kind:<30}{k['type']:<11}{k['total']:>5}{k['rate']:>8.1%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visits", default=str(ROOT / "tools" / "transcripts" / "out" / "visits.json"))
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0, help="start position in the visits file")
    ap.add_argument("--label", default="run")
    ap.add_argument("--regrade", help="re-grade a saved results file instead of running visits")
    args = ap.parse_args()

    if args.regrade:
        path = Path(args.regrade)
        saved = json.loads(path.read_text())
        rows, label = saved["notes"], saved["summary"]["label"]
        tokens = {"input": saved["summary"]["input_tokens"], "output": saved["summary"]["output_tokens"]}
    else:
        visits = json.loads(Path(args.visits).read_text())[args.offset: args.offset + args.count]
        if not visits or "facts" not in visits[0]:
            sys.exit("These visits have no answer key. Regenerate them: python3 tools/transcripts/generate.py")
        rows, tokens = collect(visits)
        label = args.label
        path = lib.RESULTS / f"note-eval-{label}.json"

    summary = report(rows, tokens, label)
    if args.regrade:
        summary = {**saved["summary"], **{k: v for k, v in summary.items() if k not in ("input_tokens", "output_tokens")}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": summary, "notes": rows}, indent=2) + "\n")
    print_summary(summary)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

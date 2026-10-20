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
    # also have the LLM judge label every sentence (judge.py; costs money on a real model)
    .venv/bin/python eval/run_note_eval.py --count 20 --label opus --judge
    # rebuild a run from encounters already drafted (after a crash), without new drafts
    .venv/bin/python eval/run_note_eval.py --recover claude-haiku-5-5 --since 2026-10-20T01:04:00Z --label haiku

The results file is written after the drafts are collected and again after every judgement, so
an interrupted run keeps what it paid for.

Results go to docs/results/note-eval-<label>.json. The notes are synthetic, so the file keeps
them for review.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "chaos"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib  # noqa: E402  (chaos/lib.py: API, Prometheus and database helpers)
import judge as judging  # noqa: E402
from grading import Grade, grade, summarise  # noqa: E402

GATEWAY = "localhost:50051"
JUDGE_CLIENT = "note-eval-judge"

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


def token_counters() -> dict[tuple[str, str, str], float]:
    """Gateway token counters by (client, model, type)."""
    url = f"{lib.PROM}/api/v1/query?" + urllib.parse.urlencode(
        {"query": "sum by (client, model, type) (llm_gateway_tokens_total)"})
    return {(r["metric"].get("client", ""), r["metric"].get("model", ""), r["metric"].get("type", "")): float(r["value"][1])
            for r in lib.http("GET", url)["data"]["result"]}


def usage_since(before: dict) -> dict:
    """Tokens and estimated cost per client and model since a token_counters() snapshot."""
    time.sleep(10)  # let Prometheus scrape the gateway's final counts
    after = token_counters()
    usage: dict[str, dict] = {}
    for (client, model, kind), value in after.items():
        delta = int(value - before.get((client, model, kind), 0))
        if delta <= 0:
            continue
        u = usage.setdefault(f"{client}/{model}", {"client": client, "model": model, "input_tokens": 0, "output_tokens": 0})
        u[f"{kind}_tokens"] += delta
    for u in usage.values():
        price = PRICES.get(u["model"], (0.0, 0.0) if u["model"].startswith("fake") else None)
        u["estimated_cost_usd"] = (round(u["input_tokens"] / 1e6 * price[0] + u["output_tokens"] / 1e6 * price[1], 4)
                                   if price else None)
    return usage


def collect(visits: list[dict]) -> tuple[list[dict], dict]:
    """Runs the visits through the stack; returns per-note rows and token usage."""
    before = token_counters()
    scribe, clinician = lib.login(), lib.login("clinician")
    ids = [lib.create_encounter(scribe, v) for v in visits]
    print(f"submitted {len(ids)} encounters; waiting for drafts...")
    lib.wait_until(lambda: lib.drafting_count(ids) == 0, timeout=1800, interval=5)
    usage = usage_since(before)
    return fetch_rows(ids, visits, clinician), usage


def recover(model: str, since: str, visits: list[dict]) -> tuple[list[dict], dict]:
    """Rows for encounters already drafted by a model since a time, matched to their visits by
    transcript, with token usage from Prometheus over the same window."""
    ids = [r[0] for r in lib.psql(f"""
        select e.id from encounter e join note_version v on v.encounter_id = e.id and v.version = 1
        where v.model = '{model}' and e.created_at >= '{since}' order by e.created_at""")]
    clinician = lib.login("clinician")
    by_transcript = {v["transcript"]: v for v in visits}
    transcripts = [lib.http("GET", f"{lib.API}/api/encounters/{i}", token=clinician)["transcript"] for i in ids]
    window = int(time.time() - datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()) + 60
    url = f"{lib.PROM}/api/v1/query?" + urllib.parse.urlencode(
        {"query": f'sum by (client, model, type) (increase(llm_gateway_tokens_total{{model="{model}"}}[{window}s]))'})
    usage: dict[str, dict] = {}
    for r in lib.http("GET", url)["data"]["result"]:
        m = r["metric"]
        u = usage.setdefault(f"{m['client']}/{m['model']}", {"client": m["client"], "model": m["model"],
                                                            "input_tokens": 0, "output_tokens": 0})
        u[f"{m['type']}_tokens"] += round(float(r["value"][1]))
    for u in usage.values():
        price = PRICES.get(u["model"])
        u["estimated_cost_usd"] = (round(u["input_tokens"] / 1e6 * price[0] + u["output_tokens"] / 1e6 * price[1], 4)
                                   if price else None)
    print(f"recovered {len(ids)} encounters drafted by {model}")
    return fetch_rows(ids, [by_transcript[t] for t in transcripts], clinician), usage


def fetch_rows(ids: list[str], visits: list[dict], clinician: str) -> list[dict]:
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
            "draft_detail": d.get("draftDetail"),
            "note": d["latest"]["soap"] if d["latest"] else None,
            "transcript": visit["transcript"],
            "facts": visit["facts"],
            # What the model saw of the record (synthetic), for the judge and for review.
            "record": lib.http("GET", f"{lib.API}/api/encounters/{encounter_id}/patient-context", token=clinician),
        })
    return rows


def run_judge(rows: list[dict], model: str, only_missing: bool = False, checkpoint=lambda: None) -> dict:
    from note_worker.errors import GatewayRejected, WorkerError
    from note_worker.gateway_client import GatewayClient

    client = GatewayClient(GATEWAY, JUDGE_CLIENT, deadline_s=300, retry_budget_s=900)
    before = token_counters()
    todo = [r for r in rows if r["note"] and not (only_missing and "sentences" in r.get("judge", {}))]
    rejected_in_a_row = 0
    for i, row in enumerate(todo):
        for attempt in (1, 2):  # one retry: a reply that skips sentences is usually a one-off
            try:
                row["judge"] = judging.judge(client, request_id=row["encounter_id"], note=row["note"],
                                             transcript=row["transcript"], record=row["record"], model=model).to_dict()
                rejected_in_a_row = 0
                break
            except judging.JudgeError as e:
                row["judge"] = {"error": str(e), "attempts": attempt}
            except WorkerError as e:
                row["judge"] = {"error": e.safe_reason, "attempts": attempt}
                rejected_in_a_row += isinstance(e, GatewayRejected)
                break
        checkpoint()
        print(f"  judged {i + 1}", end="\r", flush=True)
        if rejected_in_a_row >= 3:
            # Several permanent rejections in a row is the account or the request, not one note
            # (a spent credit balance looks like this); stop instead of paying for nothing.
            print(f"\nstopping: the gateway rejected {rejected_in_a_row} judge calls in a row")
            break
    client.close()
    print()
    return usage_since(before)


def judge_summary(rows: list[dict]) -> dict:
    verdicts = [judging.Verdict(r["judge"]["sentences"], r["judge"]["omissions"], r["judge"]["model"])
                for r in rows if r.get("judge") and "sentences" in r["judge"]]
    return {**judging.summarise(verdicts), "judge_errors": sum(1 for r in rows if r.get("judge", {}).get("error"))}


def recheck_safety(rows: list[dict]) -> None:
    """Re-runs the current safety check on saved soap-v2 drafts (rebuilt from their evidence), so
    a change to the check can be measured on real model output without new model calls."""
    from note_worker import safety
    from note_worker.soap import SoapDraft

    for row in rows:
        detail = row.get("draft_detail")
        if not detail or not row.get("record"):
            continue
        draft = SoapDraft.model_validate({
            **{section: [{"text": e["text"], "transcript_lines": e["lines"], "from_record": e["fromRecord"]}
                         for e in detail["evidence"] if e["section"] == section]
               for section in ("subjective", "objective", "assessment", "plan")},
            "medications_mentioned": detail["medications"]})
        row["safety_recheck"] = [f.to_event() for f in safety.check(
            draft, transcript=row["transcript"], record_medications=row["record"]["medications"],
            allergies=row["record"]["allergies"])]


def report(rows: list[dict], usage: dict, label: str) -> dict:
    grades: list[Grade] = []
    for row in rows:
        if row["note"]:
            g = grade(row["note"], row["facts"])
            row["grade"] = g.to_dict()
            grades.append(g)
    latencies = [r["draft_seconds"] for r in rows if r["draft_seconds"] is not None]
    models = sorted({r["model"] for r in rows if r["model"]})
    drafting = [u for u in usage.values() if u["client"] != JUDGE_CLIENT]
    summary = {
        "label": label,
        "encounters": len(rows),
        "drafted": len(grades),
        "failed": sum(1 for r in rows if r["status"] == "DRAFT_FAILED"),
        "models": models,
        **summarise(grades),
        "safety_flagged_notes": sum(1 for r in rows if any(f["severity"] == "high" for f in r["safety_flags"])),
        "safety_flags_by_code": dict(sorted(Counter(f["code"] for r in rows for f in r["safety_flags"]).items())),
        **({"safety_flags_by_code_current_check": dict(sorted(Counter(
            f["code"] for r in rows for f in r["safety_recheck"]).items()))} if any("safety_recheck" in r for r in rows) else {}),
        **sourcing(rows),
        "draft_seconds_p50": round(statistics.median(latencies), 1) if latencies else None,
        "draft_seconds_p95": round(percentile(latencies, 0.95), 1) if latencies else None,
        "input_tokens": sum(u["input_tokens"] for u in drafting),
        "output_tokens": sum(u["output_tokens"] for u in drafting),
        "estimated_cost_usd": round(sum(u["estimated_cost_usd"] or 0 for u in drafting), 3),
        "usage": list(usage.values()),
    }
    if any(r.get("judge") for r in rows):
        summary["judge"] = judge_summary(rows)
    return summary


def sourcing(rows: list[dict]) -> dict:
    """How the draft's statements are sourced (soap-v2 and later; empty for older prompts)."""
    evidence = [e for r in rows if r.get("draft_detail") for e in r["draft_detail"]["evidence"]]
    if not evidence:
        return {}
    return {"statements": len(evidence),
            "statements_citing_transcript": round(sum(1 for e in evidence if e["lines"]) / len(evidence), 4),
            "statements_from_record_only": round(sum(1 for e in evidence if not e["lines"] and e["fromRecord"]) / len(evidence), 4),
            "statements_unsourced": round(sum(1 for e in evidence if not e["lines"] and not e["fromRecord"]) / len(evidence), 4)}


def print_summary(summary: dict) -> None:
    print(f"\n{summary['drafted']}/{summary['encounters']} drafted by {', '.join(summary['models']) or '?'}")
    print(f"fact recall: {summary['fact_recall']:.1%}   distractor violations: {summary['violation_rate']:.1%} "
          f"({summary['notes_with_violation']} of {summary['notes']} notes)")
    print(f"draft p50 {summary['draft_seconds_p50']} s, p95 {summary['draft_seconds_p95']} s; "
          f"tokens in/out {summary['input_tokens']}/{summary['output_tokens']}; cost ${summary['estimated_cost_usd']}")
    if "statements" in summary:
        print(f"statements: {summary['statements']}, citing the transcript {summary['statements_citing_transcript']:.1%}, "
              f"record only {summary['statements_from_record_only']:.1%}, unsourced {summary['statements_unsourced']:.1%}")
    print(f"safety flags: {summary['safety_flags_by_code'] or 'none'}")
    if "safety_flags_by_code_current_check" in summary:
        print(f"safety flags from the current check: {summary['safety_flags_by_code_current_check'] or 'none'}")
    if "judge" in summary:
        j = summary["judge"]
        print(f"judge ({', '.join(j['judge_models'])}): {j['sentences']} sentences, {j['unsupported_rate']:.1%} unsupported, "
              f"{j['contradicted_rate']:.1%} contradicted, {j['omissions_per_note']} omissions per note, "
              f"{j['judge_errors']} errors")
    for u in summary["usage"]:
        print(f"  {u['client']:<16} {u['model']:<20} in {u['input_tokens']:>8} out {u['output_tokens']:>8} "
              f"${u['estimated_cost_usd']}")
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
    ap.add_argument("--facts", help="with --regrade: take answer keys from this visits file (matched by transcript)")
    ap.add_argument("--recover", metavar="MODEL", help="rebuild a run from encounters this model already drafted")
    ap.add_argument("--since", help="with --recover: only encounters created at or after this ISO time")
    ap.add_argument("--recheck-safety", action="store_true",
                    help="with --regrade: re-run the current safety check on the saved drafts")
    ap.add_argument("--judge", action="store_true", help="have the LLM judge label every note sentence")
    ap.add_argument("--judge-model", default=judging.JUDGE_MODEL)
    args = ap.parse_args()

    if args.regrade:
        path = Path(args.regrade)
        saved = json.loads(path.read_text())
        rows, label = saved["notes"], saved["summary"]["label"]
        usage = {f"{u['client']}/{u['model']}": u for u in saved["summary"].get("usage", [])}
        if not usage and saved["summary"].get("input_tokens") is not None:  # files from before per-client usage
            model = saved["summary"]["models"][0]
            usage = {f"note-worker/{model}": {"client": "note-worker", "model": model,
                                               "input_tokens": saved["summary"]["input_tokens"],
                                               "output_tokens": saved["summary"]["output_tokens"],
                                               "estimated_cost_usd": saved["summary"]["estimated_cost_usd"]}}
        if args.facts:
            keys = {v["transcript"]: v["facts"] for v in json.loads(Path(args.facts).read_text())}
            for row in rows:
                row["facts"] = keys[row["transcript"]]
        if args.recheck_safety:
            recheck_safety(rows)
    elif args.recover:
        rows, usage = recover(args.recover, args.since, json.loads(Path(args.visits).read_text()))
        label = args.label
        path = lib.RESULTS / f"note-eval-{label}.json"
    else:
        visits = json.loads(Path(args.visits).read_text())[args.offset: args.offset + args.count]
        if not visits or "facts" not in visits[0]:
            sys.exit("These visits have no answer key. Regenerate them: python3 tools/transcripts/generate.py")
        rows, usage = collect(visits)
        label = args.label
        path = lib.RESULTS / f"note-eval-{label}.json"

    def checkpoint() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"summary": report(rows, usage, label), "notes": rows}, indent=2) + "\n")

    checkpoint()
    if args.judge:
        print("judging...")
        if args.regrade:
            # Only notes without a judgement (a failed reply); their cost adds to the saved total.
            for key, u in run_judge(rows, args.judge_model, only_missing=True, checkpoint=checkpoint).items():
                total = usage.setdefault(key, {**u, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0})
                for field in ("input_tokens", "output_tokens"):
                    total[field] += u[field]
                total["estimated_cost_usd"] = round((total["estimated_cost_usd"] or 0) + (u["estimated_cost_usd"] or 0), 4)
        else:
            usage = {k: u for k, u in usage.items() if u["client"] != JUDGE_CLIENT}
            usage.update(run_judge(rows, args.judge_model, checkpoint=checkpoint))
    summary = report(rows, usage, label)
    if args.regrade:
        kept = ("draft_seconds_p50", "draft_seconds_p95")
        summary = {**summary, **{k: saved["summary"][k] for k in kept if k in saved["summary"]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": summary, "notes": rows}, indent=2) + "\n")
    print_summary(summary)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

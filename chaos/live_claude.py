#!/usr/bin/env python3
"""Runs synthetic visits through the full pipeline with the real Claude model and records what
happened: draft latency against the SLO, safety flags raised on real model output, and token
usage (for cost).

Prerequisite: the gateway is running with LLM_PROVIDER=anthropic (see README). Costs real money:
roughly a few cents per draft with claude-opus-5.

    python3 chaos/live_claude.py --count 20 --label opus-5-default
"""

import argparse
import statistics
import time

import lib

# claude-opus-5 list prices, USD per million tokens (see the Claude API pricing page).
PRICE_IN, PRICE_OUT = 5.00, 25.00


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--label", default="claude")
    ap.add_argument("--offset", type=int, default=0, help="start position in visits.json")
    args = ap.parse_args()

    tokens_in_before = lib.prom('sum(llm_gateway_tokens_total{type="input"})') or 0
    tokens_out_before = lib.prom('sum(llm_gateway_tokens_total{type="output"})') or 0

    scribe, clinician = lib.login(), lib.login("clinician")
    visits = lib.visits()[args.offset: args.offset + args.count]
    ids = [lib.create_encounter(scribe, v) for v in visits]
    print(f"submitted {len(ids)} encounters; waiting for Claude drafts...")
    lib.wait_until(lambda: lib.drafting_count(ids) == 0, timeout=1800, interval=5)
    time.sleep(10)  # let the gateway's token counters be scraped

    rows = []
    for i in ids:
        d = lib.http("GET", f"{lib.API}/api/encounters/{i}", token=clinician)
        s = d["summary"]
        latency = None
        if s["draftedAt"]:
            from datetime import datetime
            latency = (datetime.fromisoformat(s["draftedAt"].replace("Z", "+00:00"))
                       - datetime.fromisoformat(s["createdAt"].replace("Z", "+00:00"))).total_seconds()
        rows.append({
            "encounter_id": i,
            "status": s["status"],
            "draft_seconds": latency,
            "model": d["latest"]["model"] if d["latest"] else None,
            "flags": d["draftSafetyFlags"],
            # Synthetic data only, so the note is kept for manual review of flags.
            "note": d["latest"]["soap"] if d["latest"] else None,
            "transcript": d["transcript"],
        })

    latencies = sorted(r["draft_seconds"] for r in rows if r["draft_seconds"] is not None)
    tokens_in = (lib.prom('sum(llm_gateway_tokens_total{type="input"})') or 0) - tokens_in_before
    tokens_out = (lib.prom('sum(llm_gateway_tokens_total{type="output"})') or 0) - tokens_out_before
    p95 = latencies[max(0, round(0.95 * len(latencies)) - 1)] if latencies else None
    summary = {
        "label": args.label,
        "encounters": len(ids),
        "drafted": len(latencies),
        "failed": sum(1 for r in rows if r["status"] == "DRAFT_FAILED"),
        "models": sorted({r["model"] for r in rows if r["model"]}),
        "draft_seconds_p50": round(statistics.median(latencies), 1) if latencies else None,
        "draft_seconds_p95": round(p95, 1) if p95 else None,
        "draft_seconds_max": round(latencies[-1], 1) if latencies else None,
        "within_30s": round(sum(1 for x in latencies if x <= 30) / len(latencies), 3) if latencies else None,
        "drafts_flagged": sum(1 for r in rows if any(f["severity"] == "high" for f in r["flags"])),
        "flags": [{"encounter_id": r["encounter_id"], **f} for r in rows for f in r["flags"]],
        "input_tokens": int(tokens_in),
        "output_tokens": int(tokens_out),
        "estimated_cost_usd": round(tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT, 3),
    }
    path = lib.save(f"live-{args.label}", {"summary": summary, "drafts": rows})
    print({k: v for k, v in summary.items() if k != "flags"})
    for f in summary["flags"]:
        print(f"  flag {f['severity']} {f['code']} '{f['subject']}' in {f['section']} ({f['encounter_id'][:8]})")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

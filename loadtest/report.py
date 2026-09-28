#!/usr/bin/env python3
"""Reads the SLO metrics for a load-test window from Prometheus and charts them.

    python3 loadtest/report.py --start <unix> --end <unix> --name load-1-worker
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chaos"))
import lib  # noqa: E402

QUERIES = {
    "created_per_s": "sum(rate(chartwise_encounters_submitted_total[30s]))",
    "drafted_per_s": "sum(rate(chartwise_drafts_received_total[30s]))",
    "p50_s": "histogram_quantile(0.50, sum by (le) (rate(chartwise_note_draft_latency_seconds_bucket[30s])))",
    "p95_s": "histogram_quantile(0.95, sum by (le) (rate(chartwise_note_draft_latency_seconds_bucket[30s])))",
    "backlog": 'sum(kafka_consumergroup_lag{consumergroup="note-worker",topic="encounter.created"})',
    "rate_limited_per_s": 'sum(rate(llm_gateway_requests_total{outcome="rate_limited"}[30s]))',
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--name", default="load-test")
    ap.add_argument("--slo-seconds", type=float, default=30)
    args = ap.parse_args()

    series = {k: lib.prom_range(q, args.start, args.end, "5s") for k, q in QUERIES.items()}
    window = f"{int(args.end - args.start)}s"
    within = lib.prom(f'sum(increase(chartwise_note_draft_latency_seconds_bucket{{le="{args.slo_seconds:.1f}"}}[{window}] @ {args.end}))')
    total = lib.prom(f"sum(increase(chartwise_note_draft_latency_seconds_count[{window}] @ {args.end}))")
    p95_overall = lib.prom(f"histogram_quantile(0.95, sum by (le) (increase(chartwise_note_draft_latency_seconds_bucket[{window}] @ {args.end})))")
    summary = {
        "window_s": int(args.end - args.start),
        "drafts": round(total or 0),
        "within_slo_ratio": round(within / total, 4) if total else None,
        "p95_draft_latency_s": round(p95_overall, 2) if p95_overall else None,
        "peak_created_per_s": round(max((v for _, v in series["created_per_s"]), default=0), 2),
        "peak_drafted_per_s": round(max((v for _, v in series["drafted_per_s"]), default=0), 2),
        "peak_backlog": int(max((v for _, v in series["backlog"]), default=0)),
    }
    lib.save(args.name, {"summary": summary,
                         "series": {k: [(round(t - args.start, 1), v) for t, v in s] for k, s in series.items()}})
    print(summary)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rel = lambda s: [t - args.start for t, _ in s]  # noqa: E731
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    axes[0].plot(rel(series["created_per_s"]), [v for _, v in series["created_per_s"]], color="#7a8699", lw=2, label="created / s")
    axes[0].plot(rel(series["drafted_per_s"]), [v for _, v in series["drafted_per_s"]], color="#067647", lw=2, label="drafted / s")
    axes[0].legend(fontsize=8)
    axes[0].set_title("Throughput", fontsize=10, loc="left")
    axes[1].plot(rel(series["p50_s"]), [v for _, v in series["p50_s"]], color="#1f6feb", lw=2, label="p50")
    axes[1].plot(rel(series["p95_s"]), [v for _, v in series["p95_s"]], color="#b42318", lw=2, label="p95")
    axes[1].axhline(args.slo_seconds, color="#b42318", ls=":", lw=1, label=f"SLO {args.slo_seconds:.0f} s")
    axes[1].legend(fontsize=8)
    axes[1].set_title("Draft latency (encounter created → draft ready)", fontsize=10, loc="left")
    axes[2].plot(rel(series["backlog"]), [v for _, v in series["backlog"]], color="#1f6feb", lw=2)
    axes[2].set_title("Draft backlog (Kafka consumer lag)", fontsize=10, loc="left")
    axes[2].set_xlabel("seconds")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    ratio = summary["within_slo_ratio"]
    fig.suptitle(f"{args.name}: {summary['drafts']} drafts, "
                 f"{(ratio or 0):.1%} within {args.slo_seconds:.0f} s, p95 {summary['p95_draft_latency_s']} s", fontsize=10)
    fig.tight_layout()
    fig.savefig(lib.RESULTS / f"{args.name}.png", dpi=150)


if __name__ == "__main__":
    main()

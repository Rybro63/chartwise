#!/usr/bin/env python3
"""Chaos experiment: take the LLM provider down under steady load, then bring it back.

Timeline (defaults): steady load throughout; provider outage from t=60s to t=120s; load stops
at t=180s; then wait for the backlog to drain.

Expected behaviour, which the report checks:
- Circuit breaker opens within seconds of the outage (fail fast instead of hammering the provider).
- Encounters keep being accepted; the draft backlog (Kafka consumer lag) grows.
- After the outage the breaker half-opens, a probe succeeds, it closes, and the backlog drains.
- No encounter is dead-lettered, lost or duplicated.

    python3 chaos/llm_outage.py --rate 2 --outage-start 60 --outage-seconds 60 --load-seconds 180
"""

import argparse
import threading
import time

import lib

SERIES = {
    "circuit_state": "max(llm_gateway_circuit_state)",
    "backlog": 'sum(kafka_consumergroup_lag{consumergroup="note-worker",topic="encounter.created"})',
    "created_per_min": "sum(rate(chartwise_encounters_submitted_total[30s]))*60",
    "drafted_per_min": "sum(rate(chartwise_drafts_received_total[30s]))*60",
    "circuit_open_rejections_per_s": 'sum(rate(llm_gateway_requests_total{outcome="circuit_open"}[30s]))',
    "provider_errors_per_s": 'sum(rate(llm_gateway_provider_attempts_total{result="retryable_error"}[30s]))',
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=2.0, help="encounters per second")
    ap.add_argument("--load-seconds", type=int, default=180)
    ap.add_argument("--outage-start", type=int, default=60)
    ap.add_argument("--outage-seconds", type=int, default=60)
    args = ap.parse_args()

    lib.chaos("none")
    token = lib.login()
    visits = lib.visits()
    ids: list[str] = []
    t0 = time.time()

    def load():
        i = 0
        while time.time() - t0 < args.load_seconds:
            ids.append(lib.create_encounter(token, visits[i % len(visits)]))
            i += 1
            time.sleep(max(0.0, t0 + i / args.rate - time.time()))

    loader = threading.Thread(target=load)
    loader.start()

    time.sleep(args.outage_start)
    lib.chaos("outage", durationSeconds=args.outage_seconds)
    outage_start = time.time()
    print(f"t={outage_start - t0:.0f}s provider outage injected for {args.outage_seconds}s")
    time.sleep(args.outage_seconds)
    lib.chaos("none")
    outage_end = time.time()
    print(f"t={outage_end - t0:.0f}s provider restored")

    loader.join()
    print(f"load finished: {len(ids)} encounters")
    drained = lib.wait_until(lambda: lib.drafting_count(ids) == 0, timeout=900)
    drained_at = time.time()
    time.sleep(15)
    end = time.time()

    series = {name: lib.prom_range(q, t0, end, "2s") for name, q in SERIES.items()}
    first_open = next((t for t, v in series["circuit_state"] if v == 2 and t >= outage_start), None)
    last_open = max((t for t, v in series["circuit_state"] if v >= 1), default=None)
    peak_backlog = max((v for _, v in series["backlog"]), default=0)

    report = lib.encounter_report(ids)
    report.update({
        "rate_per_s": args.rate,
        "outage_window_s": [round(outage_start - t0, 1), round(outage_end - t0, 1)],
        "circuit_opened_after_s": round(first_open - outage_start, 1) if first_open else None,
        "circuit_closed_after_recovery_s": round(last_open - outage_end, 1) if last_open else None,
        "peak_backlog": int(peak_backlog),
        "backlog_drained_after_recovery_s": round(drained_at - outage_end, 1),
        "dead_lettered": lib.prom_increase("worker_dead_letters_total", time.time() - t0),
        "drained": drained,
        "series": {k: [(round(t - t0, 1), v) for t, v in s] for k, s in series.items()},
    })
    path = lib.save("chaos-llm-outage", report)
    print({k: v for k, v in report.items() if k != "series"})
    print(f"wrote {path}")
    ok = drained and report["lost"] == 0 and report["duplicated"] == 0 and report["dead_lettered"] == 0 and first_open
    print("RESULT:", "PASS - breaker opened, backlog drained, nothing lost" if ok else "FAIL")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    s = report["series"]
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    for ax in axes:
        ax.axvspan(*report["outage_window_s"], color="#b42318", alpha=0.08, label="provider outage")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].step([t for t, _ in s["circuit_state"]], [v for _, v in s["circuit_state"]], where="post", color="#b42318", lw=2)
    axes[0].set_yticks([0, 1, 2], ["closed", "half-open", "open"])
    axes[0].set_title("Circuit breaker", fontsize=10, loc="left")
    axes[1].plot([t for t, _ in s["backlog"]], [v for _, v in s["backlog"]], color="#1f6feb", lw=2)
    axes[1].set_title("Draft backlog (Kafka consumer lag)", fontsize=10, loc="left")
    axes[2].plot([t for t, _ in s["created_per_min"]], [v for _, v in s["created_per_min"]], color="#7a8699", lw=2, label="created / min")
    axes[2].plot([t for t, _ in s["drafted_per_min"]], [v for _, v in s["drafted_per_min"]], color="#067647", lw=2, label="drafted / min")
    axes[2].set_title("Throughput", fontsize=10, loc="left")
    axes[2].legend(fontsize=8, loc="upper right")
    axes[2].set_xlabel("seconds since load started")
    fig.suptitle(f"LLM outage: circuit opened in {report['circuit_opened_after_s']} s, backlog peaked at "
                 f"{report['peak_backlog']}, drained {report['backlog_drained_after_recovery_s']} s after recovery; "
                 f"{report['lost']} lost, {report['duplicated']} duplicated", fontsize=10)
    fig.tight_layout()
    fig.savefig(lib.RESULTS / "chaos-llm-outage.png", dpi=150)


if __name__ == "__main__":
    main()

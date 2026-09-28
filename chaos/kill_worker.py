#!/usr/bin/env python3
"""Chaos experiment: SIGKILL the note worker partway through a batch.

1. Submit N encounters as fast as the API accepts them.
2. Once some, but not all, have been drafted, `docker kill` the worker (no graceful shutdown).
3. Restart it and wait for the backlog to drain.
4. Check the database: every encounter must end with exactly one AI draft. No encounter may be
   left undrafted (lost) or have two AI drafts (duplicated).

The worker commits Kafka offsets only after a batch's drafts are published, so the killed batch
is redelivered (nothing lost). Redelivery re-publishes some drafts, and the encounter service's
idempotent consumer absorbs them (nothing duplicated). The duplicate counters show that it
happened rather than being assumed.

    python3 chaos/kill_worker.py --count 200
"""

import argparse
import subprocess
import threading
import time

import lib


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--kill-after", type=float, default=0.3, help="kill once this share is drafted")
    ap.add_argument("--batch-size", type=int, default=10, help="the worker's BATCH_SIZE")
    args = ap.parse_args()

    token = lib.login()
    visits = lib.visits()
    timeline: list[tuple[float, int]] = []
    drafted_topic_before = lib.topic_messages("note.drafted")
    dup_before = lib.prom('sum(chartwise_events_duplicates_total{consumer="note-drafted"})') or 0
    t0 = time.time()

    ids: list[str] = []
    for i in range(args.count):
        ids.append(lib.create_encounter(token, visits[i % len(visits)]))
    print(f"created {len(ids)} encounters in {time.time() - t0:.1f}s")

    stop = threading.Event()

    def sample():
        while not stop.is_set():
            timeline.append((time.time() - t0, len(ids) - lib.drafting_count(ids)))
            time.sleep(1)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()

    lib.wait_until(lambda: len(ids) - lib.drafting_count(ids) >= args.kill_after * len(ids), timeout=300, interval=0.5)
    # Kill only when the in-flight batch is partly published (drafted count not on a batch
    # boundary). That is the hard case: some of the batch's drafts are out, its offsets are not
    # committed, so redelivery necessarily produces duplicates that must be absorbed.
    lib.wait_until(lambda: (len(ids) - lib.drafting_count(ids)) % args.batch_size != 0, timeout=120, interval=0.1)
    killed_at = time.time() - t0
    drafted_at_kill = len(ids) - lib.drafting_count(ids)
    subprocess.run(["docker", "compose", "kill", "-s", "SIGKILL", "note-worker"], cwd=lib.ROOT, check=True, capture_output=True)
    print(f"SIGKILL note-worker at t={killed_at:.1f}s with {drafted_at_kill}/{len(ids)} drafted")
    time.sleep(10)
    restarted_at = time.time() - t0
    subprocess.run(["docker", "compose", "start", "note-worker"], cwd=lib.ROOT, check=True, capture_output=True)
    print(f"restarted note-worker at t={restarted_at:.1f}s")

    drained = lib.wait_until(lambda: lib.drafting_count(ids) == 0, timeout=600)
    finished_at = time.time() - t0
    time.sleep(15)  # let late duplicates arrive and metrics be scraped
    stop.set()
    sampler.join()

    report = lib.encounter_report(ids)
    report.update({
        "drained": drained,
        "killed_at_s": round(killed_at, 1),
        "drafted_when_killed": drafted_at_kill,
        "restarted_at_s": round(restarted_at, 1),
        "all_drafted_at_s": round(finished_at, 1),
        # Ground truth from Kafka: every draft message published during the run.
        "drafts_published_to_kafka": lib.topic_messages("note.drafted") - drafted_topic_before,
        # The encounter service ran throughout, so before/after is exact.
        "duplicate_drafts_absorbed_by_encounter_service": round((lib.prom('sum(chartwise_events_duplicates_total{consumer="note-drafted"})') or 0) - dup_before),
        # The worker process restarted mid-run, so its counter only covers the restarted
        # process, which is exactly where redeliveries happen.
        "redeliveries_skipped_by_worker": round(lib.prom("sum(worker_messages_skipped_total)") or 0),
        "timeline": [(round(t, 1), n) for t, n in timeline],
    })
    path = lib.save("chaos-kill-worker", report)
    print({k: v for k, v in report.items() if k != "timeline"})
    print(f"wrote {path}")
    verdict = report["lost"] == 0 and report["duplicated"] == 0 and drained
    print("RESULT:", "PASS - no note lost or duplicated" if verdict else "FAIL")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot([t for t, _ in timeline], [n for _, n in timeline], color="#1f6feb", lw=2, label="encounters drafted")
    ax.axvline(killed_at, color="#b42318", ls="--", lw=1.5, label="worker SIGKILLed")
    ax.axvline(restarted_at, color="#067647", ls="--", lw=1.5, label="worker restarted")
    ax.axhline(len(ids), color="#7a8699", lw=1, ls=":")
    ax.set_xlabel("seconds since first encounter")
    ax.set_ylabel("drafted")
    ax.set_title(f"Worker killed mid-batch: {report['lost']} lost, {report['duplicated']} duplicated of {len(ids)} "
                 f"({report['drafts_published_to_kafka']} drafts published, duplicates absorbed)", fontsize=10)
    ax.legend(loc="lower right", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(lib.RESULTS / "chaos-kill-worker.png", dpi=150)


if __name__ == "__main__":
    main()

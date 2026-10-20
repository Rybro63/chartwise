#!/usr/bin/env python3
"""A human gold set for the LLM judge: how often does the judge agree with a clinician?

1. export: pick notes from a judged eval run and write them out for blind labelling, with the
   judge's labels left out so they can't anchor the human:
     eval/gold/labels.csv   one row per note sentence; fill in "label" (supported, unsupported or
                            contradicted, by the same definitions the judge gets) and optionally
                            "comment"
     eval/gold/visits.md    each note's numbered transcript, record and sentences, to label from

2. agree: compare the filled-in labels with the judge's and report Cohen's kappa, per-label
   precision and recall, and the confusion matrix (docs/results/judge-agreement.json).

    .venv/bin/python eval/gold.py export --run ../../docs/results/note-eval-opus-5-5-soap-v2.json --notes 10
    .venv/bin/python eval/gold.py agree  --run ../../docs/results/note-eval-opus-5-5-soap-v2.json

All of it is synthetic data, so the files can be committed.
"""

import argparse
import csv
import json
import random
from pathlib import Path

LABELS = ("supported", "unsupported", "contradicted")
HERE = Path(__file__).resolve().parent
GOLD = HERE / "gold"
RESULTS = HERE.parents[2] / "docs" / "results"
DEFINITIONS = """Label each sentence of the note against the transcript and the record:

- supported: everything it states was said in the visit or is in the record (paraphrase and
  clinical terms are fine; "not documented" statements are supported when true)
- unsupported: it states something found in neither the transcript nor the record
- contradicted: it conflicts with the transcript or the record (wrong number, a corrected dose,
  a stopped drug described as continued, a declined drug as started, a relative's drug as the
  patient's)

Label a sentence by its worst part."""


def judged_notes(run: dict) -> list[dict]:
    return [n for n in run["notes"] if n.get("judge") and "sentences" in n["judge"]]


def export(run_path: Path, count: int, seed: int) -> None:
    notes = judged_notes(json.loads(run_path.read_text()))
    if not notes:
        raise SystemExit("That run has no judge labels. Run run_note_eval.py with --judge first.")
    picked = sorted(random.Random(seed).sample(notes, min(count, len(notes))), key=lambda n: n["encounter_id"])
    GOLD.mkdir(exist_ok=True)

    with (GOLD / "labels.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["note", "n", "section", "sentence", "label", "comment"])
        for i, note in enumerate(picked, 1):
            for s in note["judge"]["sentences"]:
                w.writerow([f"N{i}:{note['encounter_id']}", s["n"], s["section"], s["text"], "", ""])

    out = [f"# Gold set: {len(picked)} notes from {run_path.name}\n", DEFINITIONS, ""]
    for i, note in enumerate(picked, 1):
        r = note["record"]
        out += [f"\n## N{i}\n", f"Record: {r.get('ageYears')} y, {r.get('gender')}. "
                f"Conditions: {'; '.join(r.get('conditions') or []) or 'none'}. "
                f"Medications: {'; '.join(r.get('medications') or []) or 'none'}. "
                f"Allergies: {'; '.join(r.get('allergies') or []) or 'none'}.\n", "Transcript:\n", "```"]
        out += [f"[{j}] {line}" for j, line in enumerate(note["transcript"].splitlines(), 1)]
        out += ["```\n", "Note:\n"]
        out += [f"{s['n']}. ({s['section']}) {s['text']}" for s in note["judge"]["sentences"]]
    (GOLD / "visits.md").write_text("\n".join(out) + "\n")
    rows = sum(len(n["judge"]["sentences"]) for n in picked)
    print(f"wrote {rows} sentences from {len(picked)} notes to {GOLD / 'labels.csv'} (label them) "
          f"and {GOLD / 'visits.md'} (read from)")


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    expected = sum((sum(a == k for a, _ in pairs) / n) * (sum(b == k for _, b in pairs) / n) for k in LABELS)
    return round((observed - expected) / (1 - expected), 4) if expected < 1 else 1.0


def agreement(pairs: list[tuple[str, str]]) -> dict:
    """pairs: (human, judge) labels for the same sentences."""
    confusion = {h: {j: sum(1 for a, b in pairs if a == h and b == j) for j in LABELS} for h in LABELS}
    per_label = {}
    for k in LABELS:
        judged_k = sum(1 for _, b in pairs if b == k)
        human_k = sum(1 for a, _ in pairs if a == k)
        both = confusion[k][k]
        per_label[k] = {"human": human_k, "judge": judged_k,
                        "judge_precision": round(both / judged_k, 4) if judged_k else None,
                        "judge_recall": round(both / human_k, 4) if human_k else None}
    # The question that matters most: does the judge catch the sentences a clinician says are wrong?
    human_bad = [(a, b) for a, b in pairs if a != "supported"]
    return {
        "sentences": len(pairs),
        "raw_agreement": round(sum(a == b for a, b in pairs) / len(pairs), 4) if pairs else None,
        "cohen_kappa": cohen_kappa(pairs),
        "judge_flags_human_problems": round(sum(b != "supported" for _, b in human_bad) / len(human_bad), 4)
        if human_bad else None,
        "per_label": per_label,
        "confusion_human_rows_judge_columns": confusion,
    }


def agree(run_path: Path, labels_path: Path) -> None:
    judge = {(n["encounter_id"], s["n"]): s["label"] for n in judged_notes(json.loads(run_path.read_text()))
             for s in n["judge"]["sentences"]}
    pairs, skipped = [], 0
    with labels_path.open() as f:
        for row in csv.DictReader(f):
            label = row["label"].strip().lower()
            key = (row["note"].split(":", 1)[1], int(row["n"]))
            if label not in LABELS or key not in judge:
                skipped += 1
                continue
            pairs.append((label, judge[key]))
    if not pairs:
        raise SystemExit(f"No labelled rows in {labels_path} yet.")
    result = {"run": run_path.name, "labelled_by": "human", "skipped_rows": skipped, **agreement(pairs)}
    out = RESULTS / "judge-agreement.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['sentences']} sentences: raw agreement {result['raw_agreement']:.1%}, "
          f"Cohen's kappa {result['cohen_kappa']}")
    if result["judge_flags_human_problems"] is not None:
        print(f"judge flags {result['judge_flags_human_problems']:.1%} of the sentences the human marked as problems")
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--run", required=True)
    e.add_argument("--notes", type=int, default=10)
    e.add_argument("--seed", type=int, default=1)
    a = sub.add_parser("agree")
    a.add_argument("--run", required=True)
    a.add_argument("--labels", default=str(GOLD / "labels.csv"))
    args = ap.parse_args()
    if args.cmd == "export":
        export(Path(args.run), args.notes, args.seed)
    else:
        agree(Path(args.run), Path(args.labels))


if __name__ == "__main__":
    main()

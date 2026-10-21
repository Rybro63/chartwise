#!/usr/bin/env python3
"""Measures the audio path end to end: recording -> transcriber -> transcript -> draft.

Uploads the synthetic recordings from tools/audio/synthesize.py through the running stack, waits
for the transcripts and drafts, then reports:

- transcription accuracy against the text that was spoken (asr_metrics.py): word error rate,
  speaker attribution, and drug names heard;
- transcription speed: the transcriber's processing time per minute of audio (from its own
  metrics), and seconds from upload to transcript, which with many uploads at once also counts
  the time a recording waits for a free transcriber;
- what the errors cost downstream: each draft graded against its visit's answer key, next to the
  same visit submitted in the same run as its typed transcript, so both go through the same
  drafting model.

    python3 tools/audio/synthesize.py --count 20
    .venv/bin/python eval/run_audio_eval.py --label fake

The recordings are synthetic speech (macOS text-to-speech, one clean voice per channel), which is
far easier than a real exam room; read the error rates as a floor.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "chaos"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib  # noqa: E402
from asr_metrics import score  # noqa: E402
from grading import grade, summarise  # noqa: E402

FORM_WORDS = {"tablet", "capsule", "extended", "release", "oral", "injection", "solution", "cream", "spray",
              "hydrochloride", "sodium", "succinate", "tartrate", "potassium"}


def upload(token: str, patient_id: str, audio: bytes) -> str:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"patientId\"\r\n\r\n{patient_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"visit.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{lib.API}/api/encounters/recording", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)["id"]


def pending(ids: list[str]) -> int:
    id_list = ",".join(f"'{i}'" for i in ids)
    return int(lib.psql(f"select count(*) from encounter where status in ('TRANSCRIBING', 'DRAFTING') and id in ({id_list})")[0][0])


def record_drugs(medications: list[str]) -> set[str]:
    words = set()
    for med in medications:
        words |= {w.lower() for w in med.replace("[", " ").split() if w.isalpha() and len(w) > 4 and w.lower() not in FORM_WORDS}
    return words


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, round(p * len(ordered)) - 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", default=str(ROOT / "tools" / "audio" / "out"))
    ap.add_argument("--visits", default=str(ROOT / "tools" / "transcripts" / "out" / "visits.json"))
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    audio_dir = Path(args.audio_dir)
    manifest = json.loads((audio_dir / "manifest.json").read_text())
    visits = json.loads(Path(args.visits).read_text())
    scribe, clinician = lib.login(), lib.login("clinician")

    processing_before = lib.prom("sum(transcriber_duration_seconds_sum)") or 0
    audio_before = lib.prom("sum(transcriber_audio_seconds_total)") or 0
    ids = [upload(scribe, m["patientId"], (audio_dir / m["file"]).read_bytes()) for m in manifest]
    typed_ids = [lib.create_encounter(scribe, visits[m["visit_index"]]) for m in manifest]
    print(f"uploaded {len(ids)} recordings and the same visits typed; waiting for transcripts and drafts...")
    lib.wait_until(lambda: pending(ids + typed_ids) == 0, timeout=3600, interval=5)
    time.sleep(10)  # let Prometheus scrape the transcriber's final counts
    processing = (lib.prom("sum(transcriber_duration_seconds_sum)") or 0) - processing_before
    audio_processed = (lib.prom("sum(transcriber_audio_seconds_total)") or 0) - audio_before

    rows, grades, typed_grades = [], [], []
    for encounter_id, typed_id, m in zip(ids, typed_ids, manifest):
        visit = visits[m["visit_index"]]
        d = lib.http("GET", f"{lib.API}/api/encounters/{encounter_id}", token=clinician)
        record = lib.http("GET", f"{lib.API}/api/encounters/{encounter_id}/patient-context", token=clinician)
        created, transcribed = lib.psql(
            f"select extract(epoch from created_at), extract(epoch from transcribed_at) from encounter where id = '{encounter_id}'")[0]
        seconds = float(transcribed) - float(created) if transcribed else None
        row = {
            "encounter_id": encounter_id,
            "visit_index": m["visit_index"],
            "status": d["summary"]["status"],
            "audio_seconds": m["seconds"],
            "transcription_seconds": round(seconds, 1) if seconds is not None else None,
            "reference": visit["transcript"],
            "transcript": d["transcript"],
            "asr": score(visit["transcript"], d["transcript"] or "", speakers={"Daughter": "Patient"},
                         extra_drugs=record_drugs(record["medications"])),
            "note": d["latest"]["soap"] if d["latest"] else None,
            "model": d["latest"]["model"] if d["latest"] else None,
        }
        if row["note"]:
            g = grade(row["note"], visit["facts"])
            row["grade"] = g.to_dict()
            grades.append(g)
            typed = lib.http("GET", f"{lib.API}/api/encounters/{typed_id}", token=clinician)["latest"]
            if typed:
                typed_grades.append(grade(typed["soap"], visit["facts"]))
        rows.append(row)

    done = [r for r in rows if r["transcript"]]
    words = sum(r["asr"]["reference_words"] for r in done)
    latencies = [r["transcription_seconds"] for r in done if r["transcription_seconds"] is not None]
    audio = sum(r["audio_seconds"] for r in done)
    spoken = sum(r["asr"]["drugs_spoken"] for r in done)
    summary = {
        "label": args.label,
        "recordings": len(rows),
        "transcribed": len(done),
        "audio_minutes": round(audio / 60, 1),
        "wer": round(sum(r["asr"]["word_errors"] for r in done) / words, 4) if words else None,
        "wer_per_recording_median": round(statistics.median(r["asr"]["wer"] for r in done), 4) if done else None,
        "speaker_accuracy": round(statistics.mean(r["asr"]["speaker_accuracy"] for r in done), 4) if done else None,
        "drug_names_spoken": spoken,
        "drug_names_heard": sum(r["asr"]["drugs_heard"] for r in done),
        "drug_recall": round(sum(r["asr"]["drugs_heard"] for r in done) / spoken, 4) if spoken else None,
        "missed_drugs": sorted({d for r in done for d in r["asr"]["missed_drugs"]}),
        "transcription_seconds_p50": round(statistics.median(latencies), 1) if latencies else None,
        "transcription_seconds_p95": round(percentile(latencies, 0.95), 1) if latencies else None,
        "processing_seconds_per_audio_minute": round(processing / (audio_processed / 60), 1) if audio_processed else None,
        "draft_models": sorted({r["model"] for r in rows if r["model"]}),
        "notes_from_audio": summarise(grades),
        "notes_from_typed_transcripts": summarise(typed_grades) if typed_grades else None,
    }
    path = lib.save(f"audio-eval-{args.label}", {"summary": summary, "recordings": rows})
    print(f"{summary['transcribed']}/{summary['recordings']} transcribed, {summary['audio_minutes']} min of audio")
    print(f"WER {summary['wer']:.1%} (median {summary['wer_per_recording_median']:.1%}), speaker accuracy "
          f"{summary['speaker_accuracy']:.1%}, drug names heard {summary['drug_names_heard']}/{spoken}"
          f" {summary['missed_drugs']}")
    print(f"processing {summary['processing_seconds_per_audio_minute']} s per audio minute; upload to transcript "
          f"p50 {summary['transcription_seconds_p50']} s, p95 {summary['transcription_seconds_p95']} s (includes queueing)")
    a = summary["notes_from_audio"]
    print(f"notes from audio: fact recall {a['fact_recall']:.1%}, distractors {a['violation_rate']:.1%}")
    if summary["notes_from_typed_transcripts"]:
        t = summary["notes_from_typed_transcripts"]
        print(f"same visits typed: fact recall {t['fact_recall']:.1%}, distractors {t['violation_rate']:.1%}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

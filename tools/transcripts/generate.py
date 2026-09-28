#!/usr/bin/env python3
"""Generates synthetic visit transcripts for the Synthea patients in HAPI FHIR.

Each transcript is a templated doctor-patient conversation built from the patient's (synthetic)
active conditions, medications and allergies, with seeded randomness. Output is a JSON list of
{"patientId", "transcript"} used by the demo seeder and the k6 load test.

    python3 generate.py --fhir http://localhost:8090/fhir --per-patient 4 --out out/visits.json

Standard library only.
"""

import argparse
import json
import random
import re
import urllib.request
from pathlib import Path

COMPLAINTS = [
    ("hypertension", "I'm here to follow up on my blood pressure.", "I've had a few headaches in the mornings."),
    ("diabetes", "I'm here for my diabetes check.", "I've been more thirsty than usual lately."),
    ("prediabetes", "My last labs said my sugar was borderline.", "I've been trying to cut back on sweets."),
    ("asthma", "My breathing has been worse at night.", "I've been using my inhaler more than usual."),
    ("sinusitis", "I've had sinus pressure for about a week.", "There's yellow drainage and my face hurts."),
    ("pharyngitis", "My throat has been really sore.", "It hurts to swallow and I had a low fever."),
    ("bronchitis", "I've had a cough for two weeks.", "It's worse at night and I bring up some mucus."),
    ("hyperlipidemia", "I'm here to go over my cholesterol results.", "I've been walking more like you suggested."),
    ("obesity", "I want to talk about my weight.", "I've gained about ten pounds this year."),
    ("osteoarthritis", "My knees have been aching.", "It's worse going up stairs."),
    ("back pain", "My lower back has been bothering me.", "It started after I moved some furniture."),
    ("anemia", "I've been feeling tired all the time.", "I get winded walking up stairs."),
    ("depress", "I've been feeling down lately.", "I'm not sleeping well and I don't enjoy things like I used to."),
    ("anxiety", "I've been really anxious.", "My heart races and I can't settle down."),
    ("migraine", "My headaches are back.", "They come with light sensitivity and nausea."),
]
DEFAULT = ("I'm here for a routine follow-up.", "Overall I've been doing okay.")
OTC = ["ibuprofen", "acetaminophen", "loratadine", "cetirizine"]
FORM_WORDS = {"hr", "ml", "mg", "oral", "tablet", "capsule", "extended", "release", "injection", "solution",
              "inhaler", "metered", "dose", "actuat", "auto", "injector", "prefilled", "syringe", "topical",
              "cream", "nda", "day", "pack", "suspension", "injectable", "chewable", "delayed", "hydrochloride"}


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def entries(bundle: dict) -> list[dict]:
    return [e["resource"] for e in bundle.get("entry", [])]


def display(concept: dict | None) -> str | None:
    if not concept:
        return None
    return concept.get("text") or next((c.get("display") for c in concept.get("coding", []) if c.get("display")), None)


def generic(med: str) -> str | None:
    for word in re.findall(r"[A-Za-z]+", med):
        w = word.lower()
        if len(w) > 3 and w not in FORM_WORDS:
            return w
    return None


def patient_record(fhir: str, pid: str) -> dict:
    conds = [display(c.get("code")) for c in entries(fetch(f"{fhir}/Condition?patient={pid}&clinical-status=active&_count=100"))]
    meds = [display(m.get("medicationCodeableConcept")) for m in entries(fetch(f"{fhir}/MedicationRequest?patient={pid}&status=active&_count=100"))]
    alls = [display(a.get("code")) for a in entries(fetch(f"{fhir}/AllergyIntolerance?patient={pid}&_count=100"))]
    return {"conditions": [c for c in conds if c], "medications": [m for m in meds if m], "allergies": [a for a in alls if a]}


def transcript(record: dict, rng: random.Random) -> str:
    conditions = " ".join(record["conditions"]).lower()
    matches = [c for c in COMPLAINTS if c[0] in conditions]
    reason, symptom = (rng.choice(matches)[1:] if matches else DEFAULT)
    meds = sorted({g for g in (generic(m) for m in record["medications"]) if g})
    discussed = rng.sample(meds, k=min(len(meds), rng.randint(1, 3))) if meds else []

    lines = [
        "Doctor: Hi, good to see you. What brings you in today?",
        f"Patient: {reason}",
        "Doctor: Tell me more about that.",
        f"Patient: {symptom}",
    ]
    for med in discussed:
        lines.append(f"Doctor: Are you still taking the {med}?")
        lines.append(rng.choice([
            "Patient: Yes, every day.",
            "Patient: Yes, though I missed a couple of doses last week.",
            "Patient: Most days. Sometimes I forget in the evening.",
        ]))
    if rng.random() < 0.4:
        otc = rng.choice(OTC)
        lines.append(f"Patient: I've also been taking some {otc} from the pharmacy.")
    # Synthea also records "Allergic disposition (finding)", which is not something one is allergic to.
    allergens = [a for a in record["allergies"] if "(finding)" not in a]
    if allergens and rng.random() < 0.6:
        lines.append(f"Doctor: And you're still allergic to {allergens[0].split(' (')[0].lower()}, correct?")
        lines.append("Patient: That's right.")
    sys_bp, dia_bp = rng.randint(112, 158), rng.randint(70, 96)
    lines.append(f"Doctor: Your blood pressure today is {sys_bp} over {dia_bp}, heart rate {rng.randint(60, 96)}, "
                 f"temperature {rng.choice(['98.2', '98.6', '99.1'])}.")
    lines.append(rng.choice([
        "Doctor: Your lungs sound clear and your heart sounds normal.",
        "Doctor: Lungs are clear. There's some mild tenderness where you pointed.",
        "Doctor: Exam looks reassuring overall.",
    ]))
    if discussed:
        lines.append(f"Doctor: Let's continue the {discussed[0]} at the same dose.")
    lines.append(rng.choice([
        "Doctor: I'd like to recheck some labs in three months.",
        "Doctor: Let's follow up in four weeks, sooner if things get worse.",
        "Doctor: I'll see you back in six months.",
    ]))
    lines.append("Patient: Sounds good, thank you.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fhir", default="http://localhost:8090/fhir")
    ap.add_argument("--per-patient", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out" / "visits.json"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    patients = entries(fetch(f"{args.fhir}/Patient?_count=200&_elements=id"))
    visits = []
    for p in patients:
        record = patient_record(args.fhir, p["id"])
        for _ in range(args.per_patient):
            visits.append({"patientId": p["id"], "transcript": transcript(record, rng)})
    rng.shuffle(visits)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(visits, indent=1))
    print(f"wrote {len(visits)} synthetic transcripts for {len(patients)} patients to {args.out}")


if __name__ == "__main__":
    main()

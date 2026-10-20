"""The realistic visit generator (tools/transcripts/messy.py) and its answer key."""

import importlib.util
import random
import re
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "tools" / "transcripts" / "messy.py"
_spec = importlib.util.spec_from_file_location("messy", _PATH)
messy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(messy)

# Synthetic records shaped like the generator's FHIR view of a Synthea patient.
RECORDS = [
    {"conditions": ["Essential hypertension (disorder)", "Hyperlipidemia (disorder)"],
     "medications": ["lisinopril 10 MG Oral Tablet", "Simvastatin 20 MG Oral Tablet", "Hydrochlorothiazide 25 MG Oral Tablet"],
     "allergies": ["Penicillin V"]},
    {"conditions": ["Chronic sinusitis (disorder)"], "medications": [], "allergies": ["Penicillin V", "Allergic disposition (finding)"]},
    {"conditions": ["Prediabetes (finding)", "Anemia (disorder)"], "medications": ["ferrous sulfate 325 MG Oral Tablet"],
     "allergies": []},
    {"conditions": ["Chronic low back pain (finding)"], "medications": ["Naproxen sodium 220 MG Oral Tablet"], "allergies": []},
    {"conditions": [], "medications": [], "allergies": []},
]


def visits(n_per_record: int = 40, seed: int = 1):
    rng = random.Random(seed)
    return [(record, messy.visit(record, rng)) for record in RECORDS for _ in range(n_per_record)]


def test_same_seed_same_visits():
    assert [v for _, v in visits(seed=5)] == [v for _, v in visits(seed=5)]


def test_answer_key_shape():
    for _, v in visits():
        facts = v["facts"]
        assert facts["schema"] == "visit-facts-v1"
        assert facts["expected"], "every visit has something a note must say"
        for fact in facts["expected"] + facts["forbidden"]:
            assert fact["all"] and all(group for group in fact["all"])
            for keyword in [k for group in fact["all"] for k in group] + fact.get("unless", []):
                assert keyword == keyword.lower(), keyword
        assert len({f["id"] for f in facts["expected"]}) == len(facts["expected"])


def test_every_visit_has_distractors_to_leave_out():
    kinds = {f["kind"] for _, v in visits() for f in v["facts"]["forbidden"]}
    assert {"small_talk", "superseded_dose", "stale_dose", "stopped_med_continued", "relative_medication",
            "declined_medication_started", "superseded_duration", "allergy_conflict"} <= kinds


def test_penicillin_allergy_means_no_amoxicillin_is_started():
    for record, v in visits():
        if any("penicillin" in a.lower() for a in record["allergies"]):
            assert not any(f["kind"] == "medication_started" and "amoxicillin" in f["text"] for f in v["facts"]["expected"])
            assert "start you on amoxicillin" not in v["transcript"]


def test_dose_change_is_in_the_transcript():
    seen = 0
    for _, v in visits():
        for fact in v["facts"]["expected"]:
            if fact["kind"] == "medication_change":
                dose = fact["all"][1][0].split()[0]
                spoken = messy.NUMBER_WORDS.get(dose, dose)
                assert re.search(rf"(?<![\d.])({re.escape(dose)}|{spoken}) milligrams", v["transcript"]), fact
                seen += 1
    assert seen


def test_vitals_in_the_key_are_the_final_reading():
    for _, v in visits():
        bp = next(f for f in v["facts"]["expected"] if f["text"].startswith("blood pressure"))
        systolic, diastolic = bp["all"][0][0].split("/")
        readings = re.findall(r"(\d+) over (\d+)", v["transcript"])
        assert readings[-1] == (systolic, diastolic)


def test_follow_up_never_collides_with_the_visit_duration():
    for _, v in visits():
        expected = v["facts"]["expected"]
        follow = next(f for f in expected if f["kind"] == "follow_up")["all"][0]
        for f in expected:
            if f["kind"] == "duration":
                assert not set(follow) & set(f["all"][0])
        for f in v["facts"]["forbidden"]:
            if f["kind"] == "superseded_duration":
                assert not set(follow) & set(f["all"][0])


@pytest.mark.parametrize("display,expected", [
    ("Simvastatin 20 MG Oral Tablet", {"name": "simvastatin", "dose": "20"}),
    ("24 HR metoprolol succinate 100 MG Extended Release Oral Tablet", {"name": "metoprolol", "dose": "100"}),
    ("Abuse-Deterrent 12 HR Oxycodone Hydrochloride 10 MG Extended Release Oral Tablet [Oxycontin]", {"name": "oxycodone", "dose": "10"}),
    ("Nitroglycerin 0.4 MG/ACTUAT Mucosal Spray", {"name": "nitroglycerin", "dose": None}),
    ("amLODIPine 2.5 MG Oral Tablet", {"name": "amlodipine", "dose": "2.5"}),
])
def test_parse_med(display, expected):
    parsed = messy.parse_med(display)
    assert {"name": parsed["name"], "dose": parsed["dose"]} == expected

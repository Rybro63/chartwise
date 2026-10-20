import pytest

from grading import grade, normalise, sentences, summarise

FACTS = {
    "schema": "visit-facts-v1",
    "expected": [
        {"id": "medication_change-1", "kind": "medication_change", "text": "lisinopril increased to 20 mg",
         "all": [["lisinopril"], ["20 mg"]]},
        {"id": "vital-2", "kind": "vital", "text": "blood pressure 138/86", "all": [["138/86"]]},
        {"id": "vital-3", "kind": "vital", "text": "heart rate 78", "all": [["78"], ["heart rate", "hr", "pulse", "bpm"]]},
        {"id": "follow_up-4", "kind": "follow_up", "text": "follow up in 4 weeks", "all": [["4 week", "1 month"]]},
    ],
    "forbidden": [
        {"id": "small_talk-1", "kind": "small_talk", "text": "small talk (soccer)", "all": [["soccer"]], "unless": []},
        {"id": "superseded_dose-2", "kind": "superseded_dose", "text": "lisinopril 40 mg", "all": [["lisinopril"], ["40 mg"]],
         "unless": ["20 mg", "correct"]},
        {"id": "declined_medication_started-3", "kind": "declined_medication_started", "text": "sertraline declined",
         "all": [["sertraline"], ["start", "begin", "initiat", "prescrib"]], "unless": ["declin", "hold", "defer", "not "]},
    ],
}


@pytest.mark.parametrize("raw,expected", [
    ("Increase to twenty milligrams", "increase to 20 mg"),
    ("one hundred fifty mg", "150 mg"),
    ("twenty-five milligrams daily", "25 mg daily"),
    ("BP 148 over 92", "bp 148/92"),
    ("BP 148/92 mmHg", "bp 148/92 mmhg"),
    ("a four-week follow-up", "a 4 week follow-up"),
    ("Pain 6 out of ten", "pain 6 out of 10"),
    ("20mg", "20 mg"),
])
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_sentences_do_not_split_decimals():
    assert sentences("Temp 98.6 F. HR 78.\n- Continue lisinopril") == ["temp 98.6 f.", "hr 78.", "continue lisinopril"]


def test_faithful_note_finds_every_fact_and_violates_nothing():
    note = {"subjective": "Hypertension follow-up.",
            "objective": "BP 138/86 on recheck. Heart rate 78.",
            "assessment": "Hypertension, not at goal.",
            "plan": "Increase lisinopril from 10 mg to twenty milligrams daily.\nSertraline offered; patient declined.\n"
                    "Follow up in four weeks."}
    g = grade(note, FACTS)
    assert g.recall == 1.0
    assert g.violations == 0


def test_superseded_dose_and_small_talk_are_violations():
    note = {"subjective": "Patient's granddaughter plays soccer.", "objective": "", "assessment": "",
            "plan": "Increase lisinopril to 40 mg daily. Start sertraline 25 mg."}
    g = grade(note, FACTS)
    violated = {f["id"] for f in g.forbidden if f["violated"]}
    assert violated == {"small_talk-1", "superseded_dose-2", "declined_medication_started-3"}
    assert g.recall == 0.0


def test_forbidden_fact_with_sections_only_counts_there():
    facts = {"expected": [], "forbidden": [
        {"id": "stale_dose-1", "kind": "stale_dose", "text": "lisinopril kept at 10 mg", "all": [["lisinopril"], ["10 mg"]],
         "unless": ["20 mg"], "sections": ["plan"]}]}
    history = {"subjective": "Taking lisinopril 10 mg every morning.", "objective": "", "assessment": "", "plan": ""}
    assert grade(history, facts).violations == 0
    plan = {**history, "plan": "Continue lisinopril 10 mg daily."}
    assert grade(plan, facts).violations == 1


def test_expected_facts_may_span_two_adjacent_sentences_on_a_line():
    joined = {"subjective": "", "objective": "HR elevated. Value was 78.", "assessment": "", "plan": ""}
    assert grade(joined, FACTS).expected[2]["found"]
    apart = {**joined, "objective": "HR elevated.\nValue was 78."}
    assert not grade(apart, FACTS).expected[2]["found"]
    distant = {**joined, "objective": "HR elevated. Patient is well. Value was 78."}
    assert not grade(distant, FACTS).expected[2]["found"]


def test_forbidden_facts_must_be_in_one_sentence():
    note = {"subjective": "", "objective": "", "assessment": "", "plan": "Sertraline was discussed. Start counseling."}
    assert grade(note, FACTS).violations == 0


def test_numbers_match_whole():
    note = {"subjective": "", "objective": "HR 178 bpm", "assessment": "", "plan": ""}
    assert not grade(note, FACTS).expected[2]["found"]


def test_summary_rates_by_kind():
    good = grade({"subjective": "", "objective": "BP 138/86. HR 78.", "assessment": "",
                  "plan": "Lisinopril 20 mg. Return in 1 month."}, FACTS)
    bad = grade({"subjective": "Talked about soccer.", "objective": "", "assessment": "", "plan": ""}, FACTS)
    s = summarise([good, bad])
    assert s["notes"] == 2
    assert s["fact_recall"] == 0.5
    assert s["by_kind"]["small_talk"] == {"type": "forbidden", "total": 2, "hits": 1, "rate": 0.5}
    assert s["notes_with_violation"] == 1

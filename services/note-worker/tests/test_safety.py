from note_worker import safety
from tests.conftest import make_draft

RECORD = ["lisinopril 10 MG Oral Tablet", "24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet"]
TRANSCRIPT = "Doctor: Any new medicines?\nPatient: Just the lisinopril and metformin, and some Advil for my knee."


def codes(flags):
    return [(f.code, f.subject) for f in flags]


def test_clean_draft_has_no_flags():
    draft = make_draft(plan="Continue lisinopril and metformin. Ibuprofen as needed for knee pain.",
                       meds=[{"name": "lisinopril", "status": "continued"}, {"name": "metformin", "status": "continued"},
                             {"name": "ibuprofen", "status": "mentioned"}])
    assert safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[]) == []


def test_flags_medication_in_neither_record_nor_transcript():
    draft = make_draft(plan="Continue lisinopril. Start warfarin 5 mg daily.",
                       meds=[{"name": "lisinopril", "status": "continued"}, {"name": "warfarin", "status": "started"}])
    flags = safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[])
    assert codes(flags) == [("UNSUPPORTED_MEDICATION", "warfarin")]
    assert flags[0].section == "plan"
    assert flags[0].severity == "high"


def test_text_scan_catches_drug_missing_from_models_own_list():
    draft = make_draft(plan="Continue lisinopril. Add digoxin 0.125 mg.",
                       meds=[{"name": "lisinopril", "status": "continued"}])
    assert codes(safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[])) == [
        ("UNSUPPORTED_MEDICATION", "digoxin")]


def test_brand_name_is_resolved_to_generic():
    draft = make_draft(plan="Start Coumadin 5 mg.", meds=[])
    assert codes(safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[])) == [
        ("UNSUPPORTED_MEDICATION", "coumadin")]


def test_brand_in_transcript_grounds_generic_in_note():
    # Patient said "Advil"; the note says "ibuprofen". Same drug, so no flag.
    draft = make_draft(plan="Ibuprofen 400 mg as needed.", meds=[{"name": "ibuprofen", "status": "mentioned"}])
    assert safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[]) == []


def test_transcription_misspelling_still_grounds():
    draft = make_draft(plan="Continue atorvastatin 20 mg.", meds=[{"name": "atorvastatin", "status": "continued"}])
    transcript = "Patient: I'm still on the atorvastattin."
    assert safety.check(draft, transcript=transcript, record_medications=[], allergies=[]) == []


def test_record_only_medication_is_grounded():
    draft = make_draft(plan="Continue metformin 500 mg ER.", meds=[{"name": "metformin", "status": "continued"}])
    assert safety.check(draft, transcript="Patient: I feel fine.", record_medications=RECORD, allergies=[]) == []


def test_allergy_class_conflict():
    draft = make_draft(plan="Start amoxicillin 500 mg three times daily for 10 days.",
                       meds=[{"name": "amoxicillin", "status": "started"}])
    transcript = "Doctor: I'll send in amoxicillin for the ear infection."
    flags = safety.check(draft, transcript=transcript, record_medications=[], allergies=["Allergy to penicillin"])
    assert codes(flags) == [("ALLERGY_CONFLICT", "amoxicillin")]


def test_stopping_an_allergen_is_not_a_conflict():
    draft = make_draft(plan="Stop amoxicillin.", meds=[{"name": "amoxicillin", "status": "stopped"}])
    transcript = "Doctor: Stop the amoxicillin, you're allergic to penicillin."
    assert safety.check(draft, transcript=transcript, record_medications=[], allergies=["Penicillin V"]) == []


def test_empty_section_is_flagged_low():
    draft = make_draft(objective="")
    flags = safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[])
    assert codes(flags) == [("EMPTY_SECTION", "objective")]
    assert flags[0].severity == "low"

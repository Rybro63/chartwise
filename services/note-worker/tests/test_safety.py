from note_worker import safety
from note_worker.soap import SoapDraft
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
    draft = make_draft(plan="Ibuprofen as needed.", meds=[{"name": "ibuprofen", "status": "mentioned"}])
    assert safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[]) == []


def test_transcription_misspelling_still_grounds():
    draft = make_draft(plan="Continue atorvastatin.", meds=[{"name": "atorvastatin", "status": "continued"}])
    transcript = "Patient: I'm still on the atorvastattin."
    assert safety.check(draft, transcript=transcript, record_medications=[], allergies=[]) == []


def test_record_only_medication_is_grounded():
    draft = make_draft(plan="Continue metformin 500 mg ER.", meds=[{"name": "metformin", "status": "continued"}])
    assert safety.check(draft, transcript="Patient: I feel fine.", record_medications=RECORD, allergies=[]) == []


def test_allergy_class_conflict():
    draft = make_draft(plan="Start amoxicillin 500 mg three times daily for 10 days.",
                       meds=[{"name": "amoxicillin", "status": "started"}])
    transcript = "Doctor: I'll send in amoxicillin, five hundred milligrams, for the ear infection."
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


def test_mentioning_a_recorded_allergy_is_not_a_hallucination():
    # Found in a real Claude draft: the note documented the record's allergies without the visit
    # discussing them. That is accurate documentation, not a fabricated medication.
    draft = make_draft(subjective="Documented allergies to aspirin and penicillin V were not discussed.",
                       plan="Continue metformin.", meds=[{"name": "metformin", "status": "continued"}])
    assert safety.check(draft, transcript="Patient: fine.", record_medications=RECORD,
                        allergies=["Aspirin", "Penicillin V"]) == []


def test_statement_without_any_source_is_flagged():
    draft = SoapDraft.model_validate({
        "subjective": [{"text": "Reports knee pain.", "transcript_lines": [2], "from_record": False}],
        "objective": [{"text": "Respiratory rate not documented.", "transcript_lines": [], "from_record": False},
                      {"text": "Oxygen saturation 97% on room air.", "transcript_lines": [], "from_record": False}],
        "assessment": [{"text": "Type 2 diabetes.", "transcript_lines": [], "from_record": True}],
        "plan": [{"text": "Continue lisinopril.", "transcript_lines": [7], "from_record": False}],
        "medications_mentioned": [{"name": "lisinopril", "status": "continued", "dose": ""}]})
    flags = safety.check(draft, transcript=TRANSCRIPT, record_medications=RECORD, allergies=[])
    # Line 7 doesn't exist in a 2-line transcript, so the plan statement has no real source either.
    assert codes(flags) == [("UNSUPPORTED_STATEMENT", "Oxygen saturation 97% on room air."),
                            ("UNSUPPORTED_STATEMENT", "Continue lisinopril.")]
    assert {f.severity for f in flags} == {"medium"}
    assert [f.section for f in flags] == ["objective", "plan"]


DOSE_RECORD = ["Simvastatin 10 MG Oral Tablet", "lisinopril 10 MG Oral Tablet"]
DOSE_TRANSCRIPT = """Doctor: You're on the simvastatin, ten milligrams. Taking it every day?
Patient: Every morning.
Doctor: I'd like to go up on that. Let's do 40, sorry, no, twenty milligrams once a day.
Patient: Okay.
Doctor: And keep the lisinipril the same."""


def dose_flags(plan: str, meds=None):
    draft = make_draft(plan=plan, meds=meds if meds is not None else [])
    return [(f.code, f.subject, f.section) for f in safety.check(draft, transcript=DOSE_TRANSCRIPT,
                                                                   record_medications=DOSE_RECORD, allergies=[])]


def test_doses_from_the_visit_or_record_pass():
    assert dose_flags("Increase simvastatin from 10 mg to 20 mg daily.\nContinue lisinopril 10 mg daily.") == []
    assert dose_flags("Simvastatin 20 mg once daily.") == []


def test_dose_nobody_stated_is_flagged():
    assert dose_flags("Continue lisinopril 40 mg daily.") == [("DOSE_MISMATCH", "lisinopril 40 mg", "plan")]


def test_dose_corrected_while_speaking_is_flagged():
    flags = dose_flags("Increase simvastatin to 40 mg daily.")
    assert flags == [("DOSE_MISMATCH", "simvastatin 40 mg", "plan")]


def test_documenting_the_correction_itself_is_fine():
    # Found in real Claude Haiku 5.5 output.
    assert dose_flags("Increase simvastatin to 20 mg once daily (corrected from the initially stated 40 mg).") == []


def test_plan_keeping_the_previous_dose_after_a_change_is_flagged():
    assert dose_flags("Continue simvastatin 10 mg daily.") == [("DOSE_MISMATCH", "simvastatin 10 mg", "plan")]


def test_previous_dose_in_the_history_is_fine():
    draft = make_draft(subjective="Taking simvastatin 10 mg every morning.", plan="Simvastatin 20 mg daily.", meds=[])
    assert safety.check(draft, transcript=DOSE_TRANSCRIPT, record_medications=DOSE_RECORD, allergies=[]) == []


def test_dose_in_the_models_medication_list_is_checked():
    flags = dose_flags("Simvastatin 20 mg daily.", meds=[{"name": "simvastatin", "status": "changed", "dose": "40 mg"}])
    assert flags == [("DOSE_MISMATCH", "simvastatin 40 mg", "medications_mentioned")]


def test_dose_messages_say_which_problem():
    draft = make_draft(plan="Continue simvastatin 10 mg daily.", meds=[])
    [flag] = safety.check(draft, transcript=DOSE_TRANSCRIPT, record_medications=DOSE_RECORD, allergies=[])
    assert flag.severity == "high"
    assert "previous dose" in flag.message

import json

import pytest

from note_worker.errors import DraftFormatError
from note_worker.prompt import SOAP_SCHEMA_JSON, DraftContext, build_user_prompt
from note_worker.soap import parse_draft
from tests.conftest import soap_json


def test_parses_valid_draft():
    draft = parse_draft(soap_json())
    assert draft.sections()["plan"].startswith("Continue lisinopril")
    assert draft.medications_mentioned[0].status == "continued"


def test_sections_render_one_statement_per_line_and_evidence_keeps_sources():
    draft = parse_draft(soap_json(plan="Continue lisinopril 10 mg daily.\nFollow up in 4 weeks.", plan_lines=(2, 99)))
    assert draft.sections()["plan"] == "Continue lisinopril 10 mg daily.\nFollow up in 4 weeks."
    plan = [e for e in draft.evidence(transcript_line_count=3) if e["section"] == "plan"]
    # Line 99 doesn't exist in a 3-line transcript, so it isn't kept as a source.
    assert plan == [{"section": "plan", "text": "Continue lisinopril 10 mg daily.", "lines": [2], "fromRecord": False,
                     "absence": False},
                    {"section": "plan", "text": "Follow up in 4 weeks.", "lines": [2], "fromRecord": False, "absence": False}]
    objective = [e for e in draft.evidence(3) if e["section"] == "objective"]
    assert objective == [{"section": "objective", "text": "Not documented.", "lines": [], "fromRecord": False, "absence": True}]


def test_transcript_lines_are_numbered_for_citation(context_payload):
    prompt = build_user_prompt(DraftContext.from_api(context_payload))
    assert "[1] Doctor: How is the blood pressure medicine going?\n[2] Patient: Fine" in prompt


@pytest.mark.parametrize("text", ["not json", "{}", json.dumps({"subjective": "x"}),
                                  soap_json(meds=[{"name": "x", "status": "invented"}])])
def test_rejects_invalid_draft_without_echoing_content(text):
    with pytest.raises(DraftFormatError) as e:
        parse_draft(text)
    assert str(e.value) == "model output did not match the SOAP note schema"
    assert e.value.__cause__ is None


def test_prompt_contains_clinical_context_but_no_identity(context_payload):
    ctx = DraftContext.from_api(context_payload | {"patientName": "Should Not Appear"})
    prompt = build_user_prompt(ctx)
    for block in ("<transcript>", "<active_medications>", "<allergies>", "lisinopril 10 MG Oral Tablet", "Age: 64"):
        assert block in prompt
    assert "Should Not Appear" not in prompt


def test_schema_is_strict_json_schema():
    schema = json.loads(SOAP_SCHEMA_JSON)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])

import json

import pytest

from note_worker.errors import DraftFormatError
from note_worker.prompt import SOAP_SCHEMA_JSON, DraftContext, build_user_prompt
from note_worker.soap import parse_draft
from tests.conftest import soap_json


def test_parses_valid_draft():
    draft = parse_draft(soap_json())
    assert draft.plan.startswith("Continue lisinopril")
    assert draft.medications_mentioned[0].status == "continued"


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

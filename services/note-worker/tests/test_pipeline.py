import json
import logging

import pytest

from note_worker.errors import DraftFormatError, DraftIncomplete
from note_worker.pipeline import NotePipeline
from tests.conftest import CANARY_TRANSCRIPT, FakeEncounters, FakeLlm, soap_json


def test_drafts_note_with_safety_flags(context_payload):
    llm = FakeLlm(soap_json(plan="Continue lisinopril. Start warfarin 5 mg.",
                            meds=[{"name": "lisinopril", "status": "continued"}, {"name": "warfarin", "status": "started"}]))
    outcome = NotePipeline(FakeEncounters(context_payload), llm).draft(context_payload["encounterId"])

    event = outcome.event
    assert event["eventType"] == "NoteDrafted"
    assert event["encounterId"] == context_payload["encounterId"]
    assert event["promptVersion"] == "soap-v2"
    assert set(event["soap"]) == {"subjective", "objective", "assessment", "plan"}
    assert [f["subject"] for f in event["safetyFlags"]] == ["warfarin"]
    assert {"section": "plan", "text": "Continue lisinopril. Start warfarin 5 mg.", "lines": [2],
            "fromRecord": False} in event["evidence"]
    assert {"name": "warfarin", "status": "started", "dose": ""} in event["medications"]
    # The request carried the schema, and the encounter ID as the correlation ID.
    assert llm.calls[0]["request_id"] == context_payload["encounterId"]
    assert '"medications_mentioned"' in llm.calls[0]["json_schema"]


def test_skips_encounters_that_are_no_longer_drafting(context_payload):
    llm = FakeLlm(soap_json())
    outcome = NotePipeline(FakeEncounters(context_payload | {"status": "IN_REVIEW"}), llm).draft("x")
    assert outcome.event is None
    assert outcome.skipped_reason == "status_in_review"
    assert llm.calls == []


def test_retries_malformed_output_once_then_gives_up(context_payload):
    llm = FakeLlm("not json", soap_json())
    assert NotePipeline(FakeEncounters(context_payload), llm).draft("x").event is not None
    assert len(llm.calls) == 2

    with pytest.raises(DraftFormatError):
        NotePipeline(FakeEncounters(context_payload), FakeLlm("still not json")).draft("x")


PLACEHOLDER = json.dumps({"subjective": [], "objective": [], "plan": [], "medications_mentioned": [],
                          "assessment": [{"text": "placeholder", "transcript_lines": [], "from_record": False}]})


def test_incomplete_draft_is_retried_then_rejected(context_payload):
    # Seen with a real model: schema-valid output whose only statement is "placeholder".
    visit = context_payload | {"transcript": "\n".join(f"Patient: line {i}" for i in range(8))}
    llm = FakeLlm(PLACEHOLDER, soap_json())
    assert NotePipeline(FakeEncounters(visit), llm).draft("x").event is not None
    assert len(llm.calls) == 2

    with pytest.raises(DraftIncomplete) as e:
        NotePipeline(FakeEncounters(visit), FakeLlm(PLACEHOLDER)).draft("x")
    assert e.value.safe_reason == "model returned an incomplete draft"


def test_short_conversations_may_have_short_notes(context_payload):
    # The two-line test transcript is not a real visit, so a brief note is acceptable.
    llm = FakeLlm(PLACEHOLDER)
    assert NotePipeline(FakeEncounters(context_payload), llm).draft("x").event is not None


def test_no_phi_in_logs(context_payload, caplog):
    caplog.set_level(logging.DEBUG)
    NotePipeline(FakeEncounters(context_payload), FakeLlm(soap_json())).draft(context_payload["encounterId"])
    with pytest.raises(DraftFormatError):
        NotePipeline(FakeEncounters(context_payload), FakeLlm("{" + CANARY_TRANSCRIPT)).draft("x")

    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    assert CANARY_TRANSCRIPT not in logged
    assert "lisinopril" not in logged.lower()

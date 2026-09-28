import json

import pytest

from note_worker.soap import SoapDraft

CANARY_TRANSCRIPT = "PHI-CANARY-TRANSCRIPT-5d21"


def make_draft(plan: str = "Continue lisinopril 10 mg daily.", meds=None, **sections) -> SoapDraft:
    return SoapDraft.model_validate({
        "subjective": sections.get("subjective", "Patient reports feeling well."),
        "objective": sections.get("objective", "BP 138/86."),
        "assessment": sections.get("assessment", "Essential hypertension, controlled."),
        "plan": plan,
        "medications_mentioned": meds if meds is not None else [{"name": "lisinopril", "status": "continued"}],
    })


@pytest.fixture
def context_payload():
    return {
        "encounterId": "3f1c1d2e-6a57-4c1e-9f5b-2f3c7d9a1b11",
        "status": "DRAFTING",
        "attempt": 1,
        "transcript": f"Doctor: How is the blood pressure medicine going?\nPatient: Fine, I take my lisinopril every morning. {CANARY_TRANSCRIPT}",
        "patientAgeYears": 64,
        "patientGender": "female",
        "conditions": ["Essential hypertension (disorder)"],
        "medications": ["lisinopril 10 MG Oral Tablet"],
        "allergies": ["Allergy to penicillin"],
    }


class FakeResponse:
    def __init__(self, text: str, model: str = "test-model", attempts: int = 1):
        self.text = text
        self.model = model
        self.attempts = attempts


class FakeLlm:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.texts.pop(0) if len(self.texts) > 1 else self.texts[0])


class FakeEncounters:
    def __init__(self, payload: dict):
        self.payload = payload

    def draft_context(self, encounter_id: str) -> dict:
        return self.payload


def soap_json(plan="Continue lisinopril 10 mg daily.", meds=None) -> str:
    return json.dumps({
        "subjective": "Patient reports taking lisinopril daily. " + CANARY_TRANSCRIPT,
        "objective": "Not documented.",
        "assessment": "Essential hypertension.",
        "plan": plan,
        "medications_mentioned": meds if meds is not None else [{"name": "lisinopril", "status": "continued"}],
    })

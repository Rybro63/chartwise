import json
from types import SimpleNamespace

import pytest

import judge

NOTE = {"subjective": "Reports morning headaches. Takes lisinopril daily.", "objective": "BP 138/86.",
        "assessment": "", "plan": "- Increase lisinopril to 20 mg.\n- Follow up in 4 weeks."}
TRANSCRIPT = "Doctor: How are you?\nPatient: Headaches in the morning."
RECORD = {"ageYears": 61, "gender": "female", "conditions": ["Essential hypertension (disorder)"],
          "medications": ["lisinopril 10 MG Oral Tablet"], "allergies": []}


class StubClient:
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(self.reply), model="judge-model")


def test_prompt_numbers_transcript_lines_and_note_sentences():
    prompt = judge.build_prompt(NOTE, TRANSCRIPT, RECORD)
    assert "[1] Doctor: How are you?\n[2] Patient: Headaches in the morning." in prompt
    assert "[1] (subjective) Reports morning headaches." in prompt
    assert "[3] (objective) BP 138/86." in prompt
    assert "[5] (plan) Follow up in 4 weeks." in prompt
    assert "Active medications: lisinopril 10 MG Oral Tablet" in prompt


def test_judge_attaches_labels_to_sentences_and_uses_the_judge_model():
    reply = {"sentences": [{"n": n, "label": "supported" if n != 4 else "contradicted", "lines": [2], "reason": "r"}
                           for n in range(1, 6)],
             "omissions": [{"lines": [2], "fact": "x"}]}
    client = StubClient(reply)
    verdict = judge.judge(client, request_id="enc-1", note=NOTE, transcript=TRANSCRIPT, record=RECORD, model="m")
    assert client.calls[0]["model"] == "m"
    assert json.loads(client.calls[0]["json_schema"]) == judge.SCHEMA
    assert verdict.counts() == {"supported": 4, "unsupported": 0, "contradicted": 1}
    assert verdict.sentences[3] == {"n": 4, "section": "plan", "text": "Increase lisinopril to 20 mg.",
                                    "label": "contradicted", "lines": [2], "reason": "r"}
    summary = judge.summarise([verdict])
    assert summary["contradicted_rate"] == 0.2
    assert summary["omissions_per_note"] == 1


def test_judge_rejects_a_reply_that_skips_sentences():
    client = StubClient({"sentences": [{"n": 1, "label": "supported", "lines": [], "reason": ""}], "omissions": []})
    with pytest.raises(judge.JudgeError):
        judge.judge(client, request_id="enc-1", note=NOTE, transcript=TRANSCRIPT, record=RECORD)

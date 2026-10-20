import json
import logging

from transcriber.errors import EncounterApiError
from transcriber.transcribe import Segment, Transcript
from transcriber.worker import TranscribeWorker, drug_names

CANARY = "PHI-CANARY-SPOKEN-9d2e"
ENCOUNTER = "8c3717ac-0000-4000-8000-000000000001"


class FakeMessage:
    def __init__(self, value: bytes, key: bytes = ENCOUNTER.encode(), offset: int = 7):
        self._value, self._key, self._offset = value, key, offset

    def value(self): return self._value
    def key(self): return self._key
    def topic(self): return "encounter.recorded"
    def partition(self): return 0
    def offset(self): return self._offset
    def error(self): return None


class FakeConsumer:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.committed = []

    def poll(self, timeout):
        return self.messages.pop(0) if self.messages else None

    def commit(self, message, asynchronous):
        self.committed.append(message.offset())


class FakeProducer:
    def __init__(self):
        self.produced = []

    def produce(self, topic, key, value, headers):
        self.produced.append((topic, key, dict(headers)))

    def flush(self, timeout):
        return 0


class FakeApi:
    def __init__(self, status="TRANSCRIBING", fail_times=0):
        self.status = status
        self.fail_times = fail_times
        self.submitted = []

    def recording(self, encounter_id):
        if self.fail_times:
            self.fail_times -= 1
            raise EncounterApiError()
        return {"status": self.status, "medications": ["lisinopril 10 MG Oral Tablet", "24 HR Metformin hydrochloride 500 MG"]}

    def audio(self, encounter_id):
        return b"RIFF-synthetic"

    def submit(self, encounter_id, transcript, model, audio_seconds):
        self.submitted.append((encounter_id, transcript, model, audio_seconds))


class FakeTranscriber:
    def __init__(self, fail=False):
        self.fail = fail
        self.hotwords = None

    def transcribe(self, audio, hotwords=None):
        if self.fail:
            raise RuntimeError(f"Invalid data found when processing input: {CANARY}")
        self.hotwords = hotwords
        return Transcript([Segment(0, 1, "Doctor", f"How is the lisinopril? {CANARY}"), Segment(1, 2, "Patient", "Fine.")],
                          audio_seconds=2.0, channels=2, model="base.en")


def message():
    return FakeMessage(json.dumps({"eventType": "EncounterRecorded", "encounterId": ENCOUNTER}).encode())


def run(api, transcriber, *messages):
    consumer, producer = FakeConsumer(*messages), FakeProducer()
    worker = TranscribeWorker(consumer, producer, api, transcriber, sleep=lambda s: None)
    while worker.run_once():
        pass
    return consumer, producer


def test_transcribes_submits_then_commits():
    api, transcriber = FakeApi(), FakeTranscriber()
    consumer, producer = run(api, transcriber, message())
    [(encounter_id, transcript, model, seconds)] = api.submitted
    assert encounter_id == ENCOUNTER and model == "base.en" and seconds == 2.0
    assert transcript.startswith("Doctor: How is the lisinopril?") and transcript.endswith("Patient: Fine.")
    assert transcriber.hotwords == ["lisinopril", "metformin"]
    assert consumer.committed == [7] and producer.produced == []


def test_already_transcribed_is_skipped_and_committed():
    api = FakeApi(status="DRAFTING")
    consumer, producer = run(api, FakeTranscriber(), message())
    assert api.submitted == [] and consumer.committed == [7] and producer.produced == []


def test_unreadable_audio_is_dead_lettered_with_our_reason(caplog):
    caplog.set_level(logging.DEBUG)
    consumer, producer = run(FakeApi(), FakeTranscriber(fail=True), message())
    [(topic, key, headers)] = producer.produced
    assert topic == "chartwise.dead-letter" and key == ENCOUNTER.encode()
    assert headers["kafka_dlt-original-topic"] == b"encounter.recorded"
    assert headers["kafka_dlt-exception-fqcn"] == b"transcriber.errors.AudioUnreadable"
    assert headers["kafka_dlt-exception-message"] == b"the recording could not be decoded"
    assert consumer.committed == [7]
    assert CANARY not in caplog.text + str(producer.produced)


def test_encounter_service_outage_is_retried_then_dead_lettered():
    api = FakeApi(fail_times=1)
    consumer, producer = run(api, FakeTranscriber(), message())
    assert len(api.submitted) == 1 and producer.produced == []

    api = FakeApi(fail_times=10)
    consumer, producer = run(api, FakeTranscriber(), message())
    assert api.submitted == []
    assert producer.produced[0][2]["kafka_dlt-exception-fqcn"] == b"transcriber.errors.EncounterApiError"


def test_poison_message_is_dead_lettered():
    consumer, producer = run(FakeApi(), FakeTranscriber(), FakeMessage(b"not json"))
    assert producer.produced[0][2]["kafka_dlt-exception-fqcn"] == b"transcriber.errors.PoisonMessage"


def test_no_phi_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    run(FakeApi(), FakeTranscriber(), message())
    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    assert CANARY not in logged
    assert "lisinopril" not in logged


def test_drug_names_from_record_strings():
    assert drug_names(["24 HR metoprolol succinate 100 MG Extended Release Oral Tablet", "Simvastatin 20 MG Oral Tablet",
                       "Simvastatin 10 MG Oral Tablet"]) == ["metoprolol", "simvastatin"]

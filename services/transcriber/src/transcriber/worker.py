"""Consumes encounter.recorded and turns each recording into a transcript.

One recording at a time per instance (transcription is CPU-bound); scale out with replicas, up to
the topic's partition count. A message's offset is committed only after its transcript is stored
(or it has been dead-lettered), so a crash mid-transcription means it is transcribed again, and
the encounter service ignores a second transcript for the same encounter.
"""

import json
import logging
import re
import time
import uuid
from typing import Protocol

from confluent_kafka import Consumer, KafkaError, Message, Producer

from . import metrics
from .errors import AudioUnreadable, EncounterApiError, NoSpeech, PoisonMessage, WorkerError

log = logging.getLogger(__name__)

TOPIC = "encounter.recorded"
DEAD_LETTER = "chartwise.dead-letter"
_FORM_WORDS = {"tablet", "capsule", "extended", "release", "injection", "solution", "oral", "cream", "spray",
               "hydrochloride", "sodium", "succinate", "tartrate", "potassium", "topical", "mucosal", "actuat",
               "inhaler", "suspension", "injectable", "system", "transdermal", "chewable", "delayed"}


class Api(Protocol):
    def recording(self, encounter_id: str) -> dict: ...
    def audio(self, encounter_id: str) -> bytes: ...
    def submit(self, encounter_id: str, transcript: str, model: str, audio_seconds: float) -> None: ...


def drug_names(medications: list[str]) -> list[str]:
    """'24 HR metoprolol succinate 100 MG Extended Release Oral Tablet' -> 'metoprolol'."""
    names = []
    for med in medications:
        word = next((w.lower() for w in re.findall(r"[A-Za-z]+", med) if len(w) > 4 and w.lower() not in _FORM_WORDS), None)
        if word:
            names.append(word)
    return list(dict.fromkeys(names))


class TranscribeWorker:
    def __init__(self, consumer: Consumer, producer: Producer, api: Api, transcriber, *, transient_attempts: int = 3,
                 sleep=time.sleep):
        self._consumer = consumer
        self._producer = producer
        self._api = api
        self._transcriber = transcriber
        self._transient_attempts = transient_attempts
        self._sleep = sleep

    def run_once(self, timeout: float = 1.0) -> bool:
        """Processes at most one message; returns whether there was one."""
        message = self._consumer.poll(timeout)
        if message is None:
            return False
        if message.error():
            if message.error().code() != KafkaError._PARTITION_EOF:
                log.warning("kafka error", extra={"reason": message.error().name()})
            return False
        self.handle(message)
        self._producer.flush(10)
        self._consumer.commit(message=message, asynchronous=False)
        return True

    def handle(self, message: Message) -> None:
        try:
            encounter_id = json.loads(message.value())["encounterId"]
        except (ValueError, KeyError, TypeError):
            self._dead_letter(message, PoisonMessage())
            return
        for attempt in range(1, self._transient_attempts + 1):
            try:
                self._transcribe(encounter_id)
                return
            except EncounterApiError as e:
                if attempt == self._transient_attempts:
                    self._dead_letter(message, e)
                    return
                log.warning("encounter service unavailable; retrying", extra={"encounter_id": encounter_id, "attempt": attempt})
                self._sleep(min(5.0 * attempt, 30.0))
            except WorkerError as e:
                self._dead_letter(message, e)
                return
            except Exception as e:  # noqa: BLE001 - anything else is ours to classify, never to forward
                self._dead_letter(message, WorkerError(f"unexpected {type(e).__name__}"))
                return

    def _transcribe(self, encounter_id: str) -> None:
        info = self._api.recording(encounter_id)
        if info["status"] != "TRANSCRIBING":
            metrics.SKIPPED.labels(info["status"].lower()).inc()
            log.info("recording already handled; skipping", extra={"encounter_id": encounter_id, "reason": info["status"]})
            return
        audio = self._api.audio(encounter_id)
        started = time.monotonic()
        try:
            result = self._transcriber.transcribe(audio, hotwords=drug_names(info.get("medications") or []))
        except WorkerError:
            raise
        except Exception:  # decoding errors from PyAV/ffmpeg quote file details; ours instead
            raise AudioUnreadable() from None
        text = result.text()
        if not text.strip():
            raise NoSpeech()
        self._api.submit(encounter_id, text, result.model, result.audio_seconds)
        metrics.TRANSCRIBED.inc()
        metrics.AUDIO_SECONDS.inc(result.audio_seconds)
        metrics.SECONDS.observe(time.monotonic() - started)
        log.info("transcribed recording", extra={"encounter_id": encounter_id, "audio_seconds": result.audio_seconds,
                                                 "channels": result.channels,
                                                 "duration_ms": int((time.monotonic() - started) * 1000)})

    def _dead_letter(self, message: Message, error: WorkerError) -> None:
        # Spring's header names, so the encounter service reads these like its own dead letters.
        headers = [
            ("kafka_dlt-original-topic", message.topic().encode()),
            ("kafka_dlt-original-partition", str(message.partition()).encode()),
            ("kafka_dlt-original-offset", str(message.offset()).encode()),
            ("kafka_dlt-exception-fqcn", f"transcriber.errors.{type(error).__name__}".encode()),
            ("kafka_dlt-exception-message", error.safe_reason.encode()),
            ("dead-letter-id", str(uuid.uuid4()).encode()),
        ]
        self._producer.produce(DEAD_LETTER, key=message.key(), value=message.value(), headers=headers)
        metrics.DEAD_LETTERS.labels(type(error).__name__).inc()
        log.warning("dead-lettered recording", extra={"encounter_id": (message.key() or b"").decode("utf-8", "replace"),
                                                      "reason": error.safe_reason})


def consumer_config(bootstrap: str, group_id: str, max_poll_interval_ms: int) -> dict:
    return {
        "bootstrap.servers": bootstrap,
        "group.id": group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "max.poll.interval.ms": max_poll_interval_ms,
        "session.timeout.ms": 10_000,
    }


def producer_config(bootstrap: str) -> dict:
    return {"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all"}

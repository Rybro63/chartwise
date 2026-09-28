"""Kafka consume loop: batch in, drafts out, then commit.

Delivery semantics are at-least-once:

1. Poll a batch of encounter.created messages.
2. Draft every message in the batch concurrently.
3. Publish each draft to note.drafted (or the message to the dead-letter topic).
4. Flush the producer, and only then commit the batch's offsets.

If the process dies anywhere before step 4 finishes, the batch was never committed, so it is
redelivered and redrafted. Nothing is lost. The redrafted duplicates are harmless: this worker
skips encounters that are no longer DRAFTING, and the encounter service accepts only the first
draft per encounter ID.
"""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from confluent_kafka import Consumer, KafkaError, Message, Producer
from opentelemetry import trace

from . import metrics, telemetry
from .errors import DeliveryFailed, EncounterNotFound, PoisonMessage, WorkerError
from .pipeline import NotePipeline

log = logging.getLogger(__name__)

ENCOUNTER_CREATED = "encounter.created"
NOTE_DRAFTED = "note.drafted"
DEAD_LETTER = "chartwise.dead-letter"


class DraftConsumer:
    def __init__(self, consumer: Consumer, producer: Producer, pipeline: NotePipeline, *,
                 batch_size: int, concurrency: int):
        self._consumer = consumer
        self._producer = producer
        self._pipeline = pipeline
        self._batch_size = batch_size
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="draft")
        self._delivery_errors: list[str] = []
        self._lock = threading.Lock()

    def run(self, stop: threading.Event) -> None:
        self._consumer.subscribe([ENCOUNTER_CREATED])
        try:
            while not stop.is_set():
                self.run_once(timeout=1.0)
        finally:
            self._pool.shutdown(wait=True)

    def run_once(self, timeout: float = 1.0) -> int:
        """Processes one batch. Returns the number of messages handled."""
        messages = self._consumer.consume(num_messages=self._batch_size, timeout=timeout)
        batch = []
        for m in messages:
            if m.error():
                if m.error().code() != KafkaError._PARTITION_EOF:
                    log.warning("consumer error: %s", m.error().name())
                continue
            batch.append(m)
        if not batch:
            return 0
        metrics.BATCH_SIZE.observe(len(batch))

        for future in [self._pool.submit(self._handle, m) for m in batch]:
            future.result()  # _handle only raises for bugs; let those crash the process uncommitted

        remaining = self._producer.flush(30)
        with self._lock:
            failed, self._delivery_errors = self._delivery_errors, []
        if remaining or failed:
            raise DeliveryFailed(f"{remaining} undelivered, {len(failed)} failed deliveries; batch not committed")
        self._consumer.commit(asynchronous=False)
        return len(batch)

    def _handle(self, message: Message) -> None:
        parent = telemetry.extract(message.headers())
        with telemetry.tracer.start_as_current_span("note_worker.draft", context=parent,
                                                    kind=trace.SpanKind.CONSUMER) as span:
            try:
                encounter_id = _encounter_id(message)
            except PoisonMessage as e:
                self._dead_letter(message, e)
                return
            span.set_attribute("chartwise.encounter_id", encounter_id)
            metrics.IN_FLIGHT.inc()
            try:
                outcome = self._pipeline.draft(encounter_id)
            except EncounterNotFound:
                metrics.SKIPPED.labels("not_found").inc()
                log.info("encounter not found; skipping", extra={"encounter_id": encounter_id})
                return
            except WorkerError as e:
                self._dead_letter(message, e)
                return
            except Exception as e:  # noqa: BLE001 - an unexpected error must not stall the partition
                log.exception("unexpected error drafting note", extra={"encounter_id": encounter_id})
                self._dead_letter(message, WorkerError(f"unexpected {type(e).__name__}"))
                return
            finally:
                metrics.IN_FLIGHT.dec()

            if outcome.event is None:
                metrics.SKIPPED.labels(outcome.skipped_reason or "skipped").inc()
                log.info("skipped", extra={"encounter_id": encounter_id, "outcome": outcome.skipped_reason})
                return
            self._producer.produce(NOTE_DRAFTED, key=encounter_id, value=json.dumps(outcome.event).encode(),
                                   headers=telemetry.inject(), on_delivery=self._on_delivery)
            self._producer.poll(0)
            metrics.NOTES_DRAFTED.inc()

    def _dead_letter(self, message: Message, error: WorkerError) -> None:
        # Header names match Spring Kafka's DeadLetterPublishingRecoverer, so the encounter
        # service reads dead letters from Java and Python producers the same way.
        headers = [
            ("kafka_dlt-original-topic", message.topic().encode()),
            ("kafka_dlt-original-partition", str(message.partition()).encode()),
            ("kafka_dlt-original-offset", str(message.offset()).encode()),
            ("kafka_dlt-exception-fqcn", f"note_worker.errors.{type(error).__name__}".encode()),
            ("kafka_dlt-exception-message", error.safe_reason.encode()),
            ("dead-letter-id", str(uuid.uuid4()).encode()),
            *telemetry.inject(),
        ]
        self._producer.produce(DEAD_LETTER, key=message.key(), value=message.value(), headers=headers,
                               on_delivery=self._on_delivery)
        self._producer.poll(0)
        metrics.DEAD_LETTERS.labels(type(error).__name__).inc()
        log.warning("dead-lettered message", extra={"encounter_id": (message.key() or b"").decode("utf-8", "replace"),
                                                    "reason": error.safe_reason})

    def _on_delivery(self, err, msg) -> None:
        if err is not None:
            with self._lock:
                self._delivery_errors.append(err.name())


def _encounter_id(message: Message) -> str:
    try:
        payload = json.loads(message.value())
        return str(uuid.UUID(str(payload["encounterId"])))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise PoisonMessage() from None


def consumer_config(bootstrap: str, group_id: str, max_poll_interval_ms: int) -> dict:
    return {
        "bootstrap.servers": bootstrap,
        "group.id": group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "max.poll.interval.ms": max_poll_interval_ms,
        # A crashed worker never sends LeaveGroup; its partitions are reassigned only once the
        # broker stops hearing heartbeats. 10 s (default 45 s) bounds that stall.
        "session.timeout.ms": 10_000,
        "heartbeat.interval.ms": 3_000,
        "partition.assignment.strategy": "cooperative-sticky",
    }


def producer_config(bootstrap: str) -> dict:
    return {"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all", "linger.ms": 5}

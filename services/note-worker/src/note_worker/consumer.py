"""Kafka consume loop: continuous intake, per-partition sliding commits.

Delivery semantics are at-least-once:

1. Messages from encounter.created are started as soon as a worker slot is free (up to
   CONCURRENCY in flight). When every slot is busy, the assigned partitions are paused, which is
   the backpressure: the consumer keeps polling (so the group doesn't evict it) but fetches
   nothing until a slot frees up.
2. Each message's draft is published to note.drafted (or the message to the dead-letter topic).
3. Offsets are committed per partition, up to the end of the contiguous run of finished
   messages, and only after flushing the producer. A slow draft at offset 5 therefore holds back
   the commit for offsets 6 and up in its partition, but never blocks the processing of later
   messages.

If the process dies, every uncommitted message is redelivered and redrafted. Nothing is lost.
The redrafted duplicates are harmless: this worker skips encounters that are no longer DRAFTING,
and the encounter service accepts only the first draft per encounter ID.

This replaced a batch-then-commit loop, in which every batch waited for its slowest draft
before the next batch could start. With real LLM latency that head-of-line blocking roughly
doubled tail latency (see README).
"""

import json
import logging
import threading
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor

from confluent_kafka import Consumer, KafkaError, Message, Producer, TopicPartition
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
        self._max_in_flight = concurrency
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="draft")
        # Per partition, the offsets started and not yet committed, in offset order.
        self._pending: dict[tuple[str, int], deque[tuple[int, Future]]] = {}
        self._paused = False
        self._delivery_errors: list[str] = []
        self._lock = threading.Lock()

    @property
    def in_flight(self) -> int:
        return sum(len(q) for q in self._pending.values())

    def subscribe(self) -> None:
        self._consumer.subscribe([ENCOUNTER_CREATED], on_revoke=self._on_revoke, on_lost=self._on_lost)

    def run(self, stop: threading.Event) -> None:
        self.subscribe()
        try:
            while not stop.is_set():
                self.run_once(timeout=0.2)
            # Graceful shutdown: let in-flight drafts finish and commit them.
            for q in self._pending.values():
                for _, future in q:
                    future.result()
            self._commit_completed()
        finally:
            self._pool.shutdown(wait=True)

    def run_once(self, timeout: float = 0.2) -> int:
        """One loop iteration: commit what finished, then start what fits. Returns the number of
        messages committed plus started (0 means idle)."""
        committed = self._commit_completed()

        capacity = self._max_in_flight - self.in_flight
        if capacity <= 0 and not self._paused:
            self._consumer.pause(self._consumer.assignment())
            self._paused = True
        elif capacity > 0 and self._paused:
            self._consumer.resume(self._consumer.assignment())
            self._paused = False

        started = 0
        # Poll even while paused: it services heartbeats, rebalances and max.poll.interval.
        for m in self._consumer.consume(num_messages=max(1, min(capacity, self._batch_size)), timeout=timeout):
            if m.error():
                if m.error().code() != KafkaError._PARTITION_EOF:
                    log.warning("consumer error: %s", m.error().name())
                continue
            future = self._pool.submit(self._handle, m)
            self._pending.setdefault((m.topic(), m.partition()), deque()).append((m.offset(), future))
            started += 1
        if started:
            metrics.BATCH_SIZE.observe(started)
        return committed + started

    def _commit_completed(self, partitions: set[tuple[str, int]] | None = None) -> int:
        """Commits, per partition, up to the end of the contiguous run of finished messages."""
        offsets, done = [], 0
        for tp, queue in self._pending.items():
            if partitions is not None and tp not in partitions:
                continue
            last = None
            while queue and queue[0][1].done():
                offset, future = queue.popleft()
                future.result()  # _handle only raises for bugs; let those crash the process uncommitted
                last = offset
                done += 1
            if last is not None:
                offsets.append(TopicPartition(tp[0], tp[1], last + 1))
        if offsets:
            remaining = self._producer.flush(30)
            with self._lock:
                failed, self._delivery_errors = self._delivery_errors, []
            if remaining or failed:
                raise DeliveryFailed(f"{remaining} undelivered, {len(failed)} failed deliveries; offsets not committed")
            self._consumer.commit(offsets=offsets, asynchronous=False)
        return done

    def _on_revoke(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        # Finish and commit revoked partitions' in-flight work before another worker takes over,
        # which keeps redeliveries (duplicates) to a minimum on a normal rebalance.
        revoked = {(p.topic, p.partition) for p in partitions}
        for tp in revoked:
            for _, future in self._pending.get(tp, ()):
                future.result()
        self._commit_completed(revoked)
        for tp in revoked:
            self._pending.pop(tp, None)
        self._paused = False

    def _on_lost(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        # Ownership is already gone, so nothing can be committed. The new owner redelivers.
        for p in partitions:
            self._pending.pop((p.topic, p.partition), None)
        self._paused = False

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

"""At-least-once delivery against a real Kafka broker (Testcontainers).

Crashes a worker partway through a batch, starts a new one, and checks that every encounter is
still drafted (nothing lost) and that a poison message goes to the dead-letter topic.
"""

import json
import socket
import threading
import time
import uuid

import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from note_worker.consumer import DEAD_LETTER, ENCOUNTER_CREATED, NOTE_DRAFTED, DraftConsumer, consumer_config, producer_config
from note_worker.pipeline import NotePipeline
from tests.conftest import FakeLlm, soap_json

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def bootstrap():
    port = _free_port()
    kafka = (DockerContainer("apache/kafka:4.1.0")
             .with_bind_ports(9092, port)
             .with_env("KAFKA_NODE_ID", "1")
             .with_env("KAFKA_PROCESS_ROLES", "broker,controller")
             .with_env("KAFKA_LISTENERS", "PLAINTEXT://:9092,CONTROLLER://:9093")
             .with_env("KAFKA_ADVERTISED_LISTENERS", f"PLAINTEXT://localhost:{port}")
             .with_env("KAFKA_CONTROLLER_LISTENER_NAMES", "CONTROLLER")
             .with_env("KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT")
             .with_env("KAFKA_CONTROLLER_QUORUM_VOTERS", "1@localhost:9093")
             .with_env("KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", "1")
             .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
             .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
             .with_env("KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS", "0"))
    with kafka:
        wait_for_logs(kafka, "Kafka Server started", timeout=90)  # noqa: deprecated but stable
        servers = f"localhost:{port}"
        admin = AdminClient({"bootstrap.servers": servers})
        for f in admin.create_topics([NewTopic(t, num_partitions=3, replication_factor=1)
                                      for t in (ENCOUNTER_CREATED, NOTE_DRAFTED, DEAD_LETTER)]).values():
            f.result()
        yield servers


class SimulatedCrash(BaseException):
    """BaseException so it escapes the worker's error handling, like a SIGKILL would."""


class Encounters:
    def __init__(self, crash_after: int | None = None):
        self.crash_after = crash_after
        self.calls = 0
        self.lock = threading.Lock()

    def draft_context(self, encounter_id: str) -> dict:
        with self.lock:
            self.calls += 1
            if self.crash_after is not None and self.calls > self.crash_after:
                raise SimulatedCrash()
        return {"encounterId": encounter_id, "status": "DRAFTING", "attempt": 1, "transcript": "Patient: fine.",
                "medications": ["lisinopril 10 MG Oral Tablet"], "conditions": [], "allergies": []}


def _worker(bootstrap: str, encounters: Encounters) -> tuple[DraftConsumer, Consumer]:
    consumer = Consumer(consumer_config(bootstrap, "note-worker-it", 300_000))
    pipeline = NotePipeline(encounters, FakeLlm(soap_json()))
    worker = DraftConsumer(consumer, Producer(producer_config(bootstrap)), pipeline, batch_size=10, concurrency=4)
    worker.subscribe()
    return worker, consumer


def _drain(bootstrap: str, topic: str, expected_keys: int, timeout: float = 60) -> list:
    """Reads the topic until `expected_keys` distinct keys were seen (duplicates don't count)."""
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"reader-{uuid.uuid4()}", "auto.offset.reset": "earliest"})
    c.subscribe([topic])
    out, deadline = [], time.time() + timeout
    while time.time() < deadline and len({m.key() for m in out}) < expected_keys:
        m = c.poll(0.5)
        if m is not None and not m.error():
            out.append(m)
    c.close()
    return out


def test_crash_mid_batch_loses_nothing_and_poison_goes_to_dead_letter(bootstrap):
    producer = Producer({"bootstrap.servers": bootstrap})
    ids = [str(uuid.uuid4()) for _ in range(30)]
    for i in ids:
        producer.produce(ENCOUNTER_CREATED, key=i, value=json.dumps({"encounterId": i, "eventType": "EncounterCreated"}))
    producer.produce(ENCOUNTER_CREATED, key="poison", value=b"this is not json")
    producer.flush(10)

    # Worker 1 drafts a few encounters, then dies mid-batch before committing.
    worker1, consumer1 = _worker(bootstrap, Encounters(crash_after=5))
    with pytest.raises(SimulatedCrash):
        deadline = time.time() + 60
        while time.time() < deadline:
            worker1.run_once()
    # The process is dead: its in-memory bookkeeping is gone and no rebalance callback gets to run.
    worker1._pending.clear()
    consumer1.close()  # auto-commit is off: closing does not commit the in-flight messages

    # Worker 2 takes over the same consumer group and finishes the job.
    worker2, consumer2 = _worker(bootstrap, Encounters())
    idle, deadline = 0, time.time() + 60
    while idle < 10 and time.time() < deadline:
        idle = idle + 1 if worker2.run_once() == 0 and worker2.in_flight == 0 else 0
    consumer2.close()

    drafted = _drain(bootstrap, NOTE_DRAFTED, expected_keys=30)
    drafted_ids = [m.key().decode() for m in drafted]
    assert set(drafted_ids) == set(ids), "every encounter must be drafted at least once"
    # The uncommitted part of worker 1's batch was redrafted by worker 2. Those duplicates are
    # expected; the encounter service keeps only the first draft per encounter ID.
    print(f"drafts published: {len(drafted_ids)} for {len(ids)} encounters")
    for m in drafted:
        assert json.loads(m.value())["encounterId"] == m.key().decode()

    dead = _drain(bootstrap, DEAD_LETTER, expected_keys=1)
    assert len(dead) == 1
    headers = dict(dead[0].headers())
    assert headers["kafka_dlt-original-topic"] == ENCOUNTER_CREATED.encode()
    assert headers["kafka_dlt-exception-fqcn"] == b"note_worker.errors.PoisonMessage"
    assert dead[0].value() == b"this is not json"

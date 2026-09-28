"""Sliding-commit rules of the consumer loop, with fake Kafka clients and controllable drafts."""

import json
import threading
import time
import uuid

from note_worker.consumer import DraftConsumer
from note_worker.pipeline import DraftOutcome


class Msg:
    def __init__(self, partition: int, offset: int, encounter_id: str):
        self._p, self._o, self._id = partition, offset, encounter_id

    def error(self): return None
    def topic(self): return "encounter.created"
    def partition(self): return self._p
    def offset(self): return self._o
    def key(self): return self._id.encode()
    def value(self): return json.dumps({"encounterId": self._id}).encode()
    def headers(self): return []


class FakeConsumer:
    def __init__(self, messages):
        self.queue = list(messages)
        self.commits: list[list[tuple[int, int]]] = []
        self.paused = False

    def consume(self, num_messages, timeout):
        if self.paused:
            return []
        out, self.queue = self.queue[:num_messages], self.queue[num_messages:]
        return out

    def commit(self, offsets, asynchronous):
        self.commits.append(sorted((tp.partition, tp.offset) for tp in offsets))

    def assignment(self): return []
    def pause(self, _): self.paused = True
    def resume(self, _): self.paused = False


class FakeProducer:
    def __init__(self): self.produced = []
    def produce(self, topic, key, value, headers, on_delivery): self.produced.append((topic, key))
    def poll(self, _): return 0
    def flush(self, _): return 0


class GatedPipeline:
    """Each encounter's draft finishes only when its gate is opened."""

    def __init__(self):
        self.gates: dict[str, threading.Event] = {}

    def gate(self, encounter_id):
        return self.gates.setdefault(encounter_id, threading.Event())

    def draft(self, encounter_id):
        self.gate(encounter_id).wait(5)
        return DraftOutcome(encounter_id, {"encounterId": encounter_id})


def settle(worker, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        worker.run_once(timeout=0)
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_slow_message_blocks_commit_but_not_processing():
    ids = [str(uuid.uuid4()) for _ in range(3)]
    consumer = FakeConsumer([Msg(0, i, ids[i]) for i in range(3)])
    pipeline = GatedPipeline()
    worker = DraftConsumer(consumer, FakeProducer(), pipeline, batch_size=10, concurrency=4)

    worker.run_once(timeout=0)
    assert worker.in_flight == 3

    # Offsets 1 and 2 finish first; offset 0 is still drafting, so nothing may be committed.
    pipeline.gate(ids[1]).set()
    pipeline.gate(ids[2]).set()
    time.sleep(0.1)
    worker.run_once(timeout=0)
    assert consumer.commits == []

    # Offset 0 finishes: everything up to offset 2 is committed (next offset to read = 3).
    pipeline.gate(ids[0]).set()
    settle(worker, lambda: consumer.commits)
    assert consumer.commits == [[(0, 3)]]
    assert worker.in_flight == 0


def test_partitions_commit_independently():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    consumer = FakeConsumer([Msg(0, 10, a), Msg(1, 20, b)])
    pipeline = GatedPipeline()
    worker = DraftConsumer(consumer, FakeProducer(), pipeline, batch_size=10, concurrency=4)
    worker.run_once(timeout=0)

    pipeline.gate(b).set()  # partition 1 done, partition 0 still busy
    settle(worker, lambda: consumer.commits)
    assert consumer.commits == [[(1, 21)]]


def test_full_worker_pauses_intake_and_resumes():
    ids = [str(uuid.uuid4()) for _ in range(3)]
    consumer = FakeConsumer([Msg(0, i, ids[i]) for i in range(3)])
    pipeline = GatedPipeline()
    worker = DraftConsumer(consumer, FakeProducer(), pipeline, batch_size=10, concurrency=2)

    worker.run_once(timeout=0)
    assert worker.in_flight == 2          # only as many as there are slots
    worker.run_once(timeout=0)
    assert consumer.paused and worker.in_flight == 2

    # Offset 0 finishes and is committed; a slot frees up, intake resumes and offset 2 starts.
    pipeline.gate(ids[0]).set()
    pipeline.gate(ids[2]).set()
    settle(worker, lambda: not consumer.queue)
    assert consumer.commits == [[(0, 1)]]
    # Offset 2 is done but offset 1 isn't, so the commit waits.
    pipeline.gate(ids[1]).set()
    settle(worker, lambda: worker.in_flight == 0)
    assert consumer.commits[-1] == [(0, 3)]

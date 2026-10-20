"""JSON logging. PHI policy: encounter IDs, counts and durations only; never audio, transcripts,
medication names or patient details. tests/test_worker.py enforces this with a canary."""

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("encounter_id", "audio_seconds", "duration_ms", "channels", "reason", "attempt"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry)


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

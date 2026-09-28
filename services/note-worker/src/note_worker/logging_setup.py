"""JSON logging.

PHI policy: log encounter IDs, counts, codes and durations. Never transcripts, notes, prompts,
model output, patient names or medication names. tests/test_pipeline.py enforces this with a
canary transcript.
"""

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
        for key in ("encounter_id", "outcome", "attempt", "duration_ms", "flags", "reason", "batch", "partition", "offset"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: messages and tracebacks can include request/response content.
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry)


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)

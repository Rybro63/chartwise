import os
from dataclasses import dataclass


def _env(key: str, default: str) -> str:
    return os.environ.get(key) or default


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str = _env("KAFKA_BOOTSTRAP", "localhost:9092")
    group_id: str = _env("KAFKA_GROUP_ID", "transcriber")
    encounter_service_url: str = _env("ENCOUNTER_SERVICE_URL", "http://localhost:8080")
    username: str = _env("TRANSCRIBER_USERNAME", "transcriber")
    password: str = _env("TRANSCRIBER_PASSWORD", "chartwise-transcriber-dev")
    # base.en is about 145 MB and transcribes a minute of two-channel audio in about 5 s on two CPU
    # cores; small.en is about 3x slower and more accurate.
    model: str = _env("WHISPER_MODEL", "base.en")
    model_dir: str | None = os.environ.get("WHISPER_MODEL_DIR") or None
    # A long recording takes minutes; Kafka must not consider the consumer dead meanwhile.
    max_poll_interval_ms: int = int(_env("MAX_POLL_INTERVAL_MS", str(30 * 60 * 1000)))
    metrics_port: int = int(_env("METRICS_PORT", "9101"))

import os
from dataclasses import dataclass


def _env(key: str, default: str) -> str:
    return os.environ.get(key) or default


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str = _env("KAFKA_BOOTSTRAP", "localhost:9092")
    group_id: str = _env("KAFKA_GROUP_ID", "note-worker")
    batch_size: int = int(_env("BATCH_SIZE", "10"))
    concurrency: int = int(_env("CONCURRENCY", "8"))

    encounter_service_url: str = _env("ENCOUNTER_SERVICE_URL", "http://localhost:8080")
    worker_username: str = _env("WORKER_USERNAME", "note-worker")
    worker_password: str = _env("WORKER_PASSWORD", "chartwise-worker-dev")

    gateway_target: str = _env("GATEWAY_TARGET", "localhost:50051")
    gateway_client_id: str = _env("GATEWAY_CLIENT_ID", "note-worker")
    # Per-call deadline, and how long to keep retrying a gateway that is rate limiting or has an
    # open circuit before dead-lettering. Long enough to ride out a provider outage, so the queue
    # backs up and drains afterwards instead of failing every encounter.
    gateway_deadline_s: float = float(_env("GATEWAY_DEADLINE_SECONDS", "180"))
    gateway_retry_budget_s: float = float(_env("GATEWAY_RETRY_BUDGET_SECONDS", "600"))
    max_tokens: int = int(_env("MAX_TOKENS", "16000"))

    metrics_port: int = int(_env("METRICS_PORT", "9100"))

    @property
    def max_poll_interval_ms(self) -> int:
        # A batch can legitimately take as long as the retry budget plus one call; Kafka must not
        # consider the consumer dead before then.
        return int((self.gateway_retry_budget_s + self.gateway_deadline_s + 120) * 1000)

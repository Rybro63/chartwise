"""Worker error types.

Every error carries a ``safe_reason``: a short description written by us, free of PHI, that is
safe to log and to put in dead-letter headers. Exception messages from libraries are never
forwarded because they can echo request or response content.
"""


class WorkerError(Exception):
    safe_reason = "worker error"

    def __init__(self, safe_reason: str | None = None):
        if safe_reason:
            self.safe_reason = safe_reason
        super().__init__(self.safe_reason)


class PoisonMessage(WorkerError):
    """The Kafka message itself is unusable (not JSON, no encounter ID)."""

    safe_reason = "message is not a valid EncounterCreated event"


class EncounterNotFound(WorkerError):
    """The encounter no longer exists (for example, purged). Nothing to draft."""

    safe_reason = "encounter not found"


class EncounterApiError(WorkerError):
    safe_reason = "encounter service unavailable"


class GatewayUnavailable(WorkerError):
    """The gateway kept failing (circuit open, provider down) past the retry budget."""


class GatewayRejected(WorkerError):
    """The gateway or provider rejected the request permanently (bad request, refusal)."""


class DraftFormatError(WorkerError):
    """The model's output didn't match the SOAP schema."""

    safe_reason = "model output did not match the SOAP note schema"


class DeliveryFailed(Exception):
    """A produced message was not acknowledged by Kafka. Fatal: the batch must not be committed."""

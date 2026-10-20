"""Error types. Every error carries a safe_reason written by us, free of PHI, for logs and
dead-letter headers; library messages are never forwarded."""


class WorkerError(Exception):
    safe_reason = "transcriber error"

    def __init__(self, safe_reason: str | None = None):
        if safe_reason:
            self.safe_reason = safe_reason
        super().__init__(self.safe_reason)


class PoisonMessage(WorkerError):
    safe_reason = "message is not a valid EncounterRecorded event"


class EncounterNotFound(WorkerError):
    safe_reason = "encounter or recording not found"


class EncounterApiError(WorkerError):
    """The encounter service is unavailable or refused the call. Retried, then dead-lettered."""
    safe_reason = "encounter service unavailable"


class AudioUnreadable(WorkerError):
    safe_reason = "the recording could not be decoded"


class NoSpeech(WorkerError):
    safe_reason = "no speech found in the recording"

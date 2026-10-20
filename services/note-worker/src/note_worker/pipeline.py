"""Drafts one note: context -> prompt -> LLM (via gateway) -> parse -> safety check -> event."""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from . import metrics, safety
from .errors import DraftFormatError, DraftIncomplete
from .prompt import PROMPT_VERSION, SOAP_SCHEMA_JSON, SYSTEM_PROMPT, DraftContext, build_user_prompt
from .soap import SoapDraft, parse_draft

log = logging.getLogger(__name__)


class EncounterApi(Protocol):
    def draft_context(self, encounter_id: str) -> dict: ...


class LlmApi(Protocol):
    def complete(self, *, request_id: str, system: str, user: str, json_schema: str, max_tokens: int): ...


@dataclass(frozen=True)
class DraftOutcome:
    encounter_id: str
    event: dict | None  # NoteDrafted payload, or None when skipped
    skipped_reason: str | None = None


class NotePipeline:
    def __init__(self, encounters: EncounterApi, llm: LlmApi, *, max_tokens: int = 16000, format_attempts: int = 2):
        self._encounters = encounters
        self._llm = llm
        self._max_tokens = max_tokens
        self._format_attempts = format_attempts

    def draft(self, encounter_id: str) -> DraftOutcome:
        started = time.monotonic()
        ctx = DraftContext.from_api(self._encounters.draft_context(encounter_id))
        if ctx.status != "DRAFTING":
            # Duplicate delivery (already drafted) or a failed/retried encounter: nothing to do.
            return DraftOutcome(encounter_id, None, f"status_{ctx.status.lower()}")

        user_prompt = build_user_prompt(ctx)
        draft, response = self._generate(encounter_id, user_prompt, len((ctx.transcript or "").splitlines()))

        flags = safety.check(draft, transcript=ctx.transcript or "", record_medications=ctx.medications,
                             allergies=ctx.allergies)
        for flag in flags:
            metrics.SAFETY_FLAGS.labels(flag.code).inc()
        if any(f.severity == "high" for f in flags):
            metrics.DRAFTS_FLAGGED.inc()

        event = {
            "eventId": str(uuid.uuid4()),
            "eventType": "NoteDrafted",
            "encounterId": encounter_id,
            "attempt": ctx.attempt,
            "soap": {k: v for k, v in draft.sections().items()},
            # Which transcript lines each statement came from, and the model's medication list.
            "evidence": draft.evidence(len((ctx.transcript or "").splitlines())),
            "medications": [m.model_dump() for m in draft.medications_mentioned],
            "safetyFlags": [f.to_event() for f in flags],
            "model": response.model,
            "promptVersion": PROMPT_VERSION,
            "gatewayAttempts": response.attempts,
            "occurredAt": datetime.now(timezone.utc).isoformat(),
        }
        metrics.DRAFT_SECONDS.observe(time.monotonic() - started)
        log.info("drafted note", extra={"encounter_id": encounter_id, "flags": len(flags),
                                        "stop_reason": getattr(response, "stop_reason", ""),
                                        "duration_ms": int((time.monotonic() - started) * 1000)})
        return DraftOutcome(encounter_id, event)

    def _generate(self, encounter_id: str, user_prompt: str, transcript_lines: int) -> tuple[SoapDraft, object]:
        error: DraftFormatError = DraftFormatError()
        for attempt in range(1, self._format_attempts + 1):
            response = self._llm.complete(request_id=encounter_id, system=SYSTEM_PROMPT, user=user_prompt,
                                          json_schema=SOAP_SCHEMA_JSON, max_tokens=self._max_tokens)
            extra = {"encounter_id": encounter_id, "attempt": attempt, "stop_reason": getattr(response, "stop_reason", "")}
            try:
                draft = parse_draft(response.text)
            except DraftFormatError:
                log.warning("model output failed schema validation", extra=extra)
                error = DraftFormatError()
                continue
            if draft.is_incomplete(transcript_lines):
                # Retried like malformed output; if it persists the encounter is dead-lettered to
                # DRAFT_FAILED, where an admin sees it, instead of reaching review as a "note".
                metrics.INCOMPLETE_DRAFTS.inc()
                log.warning("model output was an incomplete draft", extra=extra)
                error = DraftIncomplete()
                continue
            return draft, response
        raise error

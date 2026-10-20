"""Parsing and validation of the model's SOAP output (soap-v2)."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import DraftFormatError

SECTIONS = ("subjective", "objective", "assessment", "plan")


class MedicationMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    status: Literal["continued", "started", "changed", "stopped", "mentioned"]
    dose: str = Field(default="", max_length=200)


class Statement(BaseModel):
    """One sentence or bullet of the note, with the transcript lines it came from."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=5_000)
    transcript_lines: list[int] = Field(default_factory=list, max_length=200)
    from_record: bool = False


class SoapDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjective: list[Statement] = Field(max_length=200)
    objective: list[Statement] = Field(max_length=200)
    assessment: list[Statement] = Field(max_length=200)
    plan: list[Statement] = Field(max_length=200)
    medications_mentioned: list[MedicationMention]

    def sections(self) -> dict[str, str]:
        """Each section as note text, one statement per line."""
        return {name: "\n".join(s.text.strip() for s in getattr(self, name) if s.text.strip()) for name in SECTIONS}

    def statements(self) -> list[tuple[str, Statement]]:
        return [(name, s) for name in SECTIONS for s in getattr(self, name) if s.text.strip()]

    def evidence(self, transcript_line_count: int) -> list[dict]:
        """Statements with their sources, for the reviewer. Citations of lines that don't exist are
        dropped, so a statement citing only those counts as unsourced."""
        return [{"section": section, "text": s.text.strip(),
                 "lines": sorted({n for n in s.transcript_lines if 1 <= n <= transcript_line_count}),
                 "fromRecord": s.from_record}
                for section, s in self.statements()]


def parse_draft(text: str) -> SoapDraft:
    try:
        return SoapDraft.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError, TypeError):
        # Not chained: pydantic and json errors quote the offending input, which is PHI.
        raise DraftFormatError() from None

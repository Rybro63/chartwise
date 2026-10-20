"""Parsing and validation of the model's SOAP output (soap-v2)."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import DraftFormatError

SECTIONS = ("subjective", "objective", "assessment", "plan")
# "Respiratory rate not documented." states an absence, which has no line to cite.
_ABSENCE = re.compile(r"\bnot (?:documented|discussed|reported|assessed|obtained|performed|taken|stated|mentioned|"
                      r"addressed|reviewed|recorded|measured|examined)\b|\bno (?:\w+ ){0,3}(?:was |were )?"
                      r"(?:documented|discussed|reported|recorded)\b|\bnone (?:documented|reported|discussed)\b",
                      re.IGNORECASE)
_FILLER = re.compile(r"^\W*(?:placeholder|tbd|todo|n/?a|none|pending|\.\.\.)\W*$", re.IGNORECASE)


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

    def is_incomplete(self, transcript_line_count: int) -> bool:
        """Valid JSON that isn't a note: for a real conversation, two or more sections with nothing
        but filler, or fewer than four real statements. Found in live use: a draft whose only
        statement was "placeholder"."""
        if transcript_line_count < 6:
            return False
        real = {name: [s for s in getattr(self, name) if s.text.strip() and not _FILLER.match(s.text)] for name in SECTIONS}
        empty = sum(1 for statements in real.values() if not statements)
        return empty >= 2 or sum(len(v) for v in real.values()) < 4

    def evidence(self, transcript_line_count: int) -> list[dict]:
        """Statements with their sources, for the reviewer. Citations of lines that don't exist are
        dropped, so a statement citing only those counts as unsourced. "absence" marks statements
        that something wasn't documented, which need no source; the safety check and the review
        app both rely on it, so the rule lives in one place."""
        return [{"section": section, "text": s.text.strip(),
                 "lines": sorted({n for n in s.transcript_lines if 1 <= n <= transcript_line_count}),
                 "fromRecord": s.from_record, "absence": bool(_ABSENCE.search(s.text))}
                for section, s in self.statements()]


def parse_draft(text: str) -> SoapDraft:
    try:
        return SoapDraft.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError, TypeError):
        # Not chained: pydantic and json errors quote the offending input, which is PHI.
        raise DraftFormatError() from None

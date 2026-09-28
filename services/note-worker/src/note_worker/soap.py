"""Parsing and validation of the model's SOAP output."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import DraftFormatError


class MedicationMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    status: Literal["continued", "started", "changed", "stopped", "mentioned"]


class SoapDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjective: str = Field(max_length=50_000)
    objective: str = Field(max_length=50_000)
    assessment: str = Field(max_length=50_000)
    plan: str = Field(max_length=50_000)
    medications_mentioned: list[MedicationMention]

    def sections(self) -> dict[str, str]:
        return {"subjective": self.subjective, "objective": self.objective,
                "assessment": self.assessment, "plan": self.plan}


def parse_draft(text: str) -> SoapDraft:
    try:
        return SoapDraft.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError, TypeError):
        # Not chained: pydantic and json errors quote the offending input, which is PHI.
        raise DraftFormatError() from None

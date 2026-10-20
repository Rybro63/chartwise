"""Dose checks for the safety check: is every dose in the draft one the visit or the record gave?

Three errors, all high severity because a wrong dose in a signed note propagates:

- a dose for a drug that matches nothing stated for that drug in the visit, nor the record's
  strength ("Continue lisinopril 40 mg" when everyone said 10);
- a dose the clinician corrected while speaking ("let's do 40, sorry, no, twenty milligrams"):
  the first number must not reach the note;
- a plan that keeps the record's previous dose after the visit changed it ("Continue simvastatin
  10 mg" after "let's go up to 20").

Doses are compared as numbers with their unit (mg or mcg), after spoken numbers are normalised
("twenty milligrams" -> "20 mg"). A dose is matched to the drug named closest before it in the
same sentence, and a drug's spoken doses are those within three lines of where it is mentioned,
because a dose is often said a line or two after the drug ("You're on the simvastatin?" ... "Let's
do twenty milligrams"). The window stops at a line that names a different drug.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .textnorm import normalise

_DOSE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mg|mcg)\b")
_NUMBER = r"(\d+(?:\.\d+)?)"
_CUE = r"(?:sorry|no|i mean|actually|wait|make that|rather|correction)"
# "40, sorry, no, 20 mg" or "40 mg - i mean 20 mg": the first number is withdrawn.
_CORRECTED = re.compile(_NUMBER + r"(?:\s*(?:mg|mcg))?[\s,.\-]+" + _CUE + r"(?:[\s,.\-]+" + _CUE + r")*[\s,.\-]+"
                        + _NUMBER + r"\s*(mg|mcg)\b")
_CHANGE = re.compile(r"\b(?:increas|decreas|go(?:ing)? up|go(?:ing)? down|bump|rais|lower|reduc|titrat|chang|switch|"
                     r"adjust|from \d)")
_WINDOW = 3


@dataclass
class DoseEvidence:
    """What the visit and the record say about each drug's dose."""
    spoken: dict[str, set[str]] = field(default_factory=dict)      # drug -> {"20 mg"}
    withdrawn: dict[str, set[str]] = field(default_factory=dict)   # drug -> doses corrected while speaking
    new: dict[str, set[str]] = field(default_factory=dict)         # drug -> doses stated while changing it
    record: dict[str, set[str]] = field(default_factory=dict)      # drug -> record strengths


@dataclass(frozen=True)
class DoseProblem:
    drug: str
    dose: str
    kind: str  # unsupported, withdrawn, stale
    section: str

    def message(self) -> str:
        return {
            "unsupported": f"'{self.drug} {self.dose}' matches no dose stated for {self.drug} in the visit or the record.",
            "withdrawn": f"'{self.drug} {self.dose}' is a dose the clinician corrected during the visit.",
            "stale": f"'{self.drug} {self.dose}' is the previous dose, but the visit changed {self.drug}'s dose.",
        }[self.kind]


def _doses(text: str) -> list[tuple[int, str]]:
    """(position, "20 mg") for each dose in normalised text."""
    return [(m.start(), f"{_trim(m.group(1))} {m.group(2)}") for m in _DOSE.finditer(text)]


def _trim(number: str) -> str:
    return number.rstrip("0").rstrip(".") if "." in number else number


def gather(transcript: str, record_medications: list[str], drugs_in: Callable[[str], list[tuple[int, str]]]) -> DoseEvidence:
    """drugs_in(normalised text) -> [(position, canonical drug)] for drug names found in it."""
    ev = DoseEvidence()
    lines = [normalise(line) for line in transcript.splitlines()]
    named = [{d for _, d in drugs_in(line)} for line in lines]
    for i in range(len(lines)):
        for drug in named[i]:
            for j in range(i, min(i + _WINDOW + 1, len(lines))):
                if j > i and named[j] and drug not in named[j]:
                    break
                later = lines[j]
                doses = {d for _, d in _doses(later)}
                for m in _CORRECTED.finditer(later):
                    ev.withdrawn.setdefault(drug, set()).add(f"{_trim(m.group(1))} {m.group(3)}")
                ev.spoken.setdefault(drug, set()).update(doses)
                if doses and _CHANGE.search(later):
                    ev.new.setdefault(drug, set()).update(doses)
    for drug, withdrawn in ev.withdrawn.items():
        # A number only counts as withdrawn if nobody also stated it as a dose for this drug.
        ev.spoken[drug] = ev.spoken.get(drug, set()) - withdrawn
    for med in record_medications:
        text = normalise(med)
        for _, drug in drugs_in(text):
            ev.record.setdefault(drug, set()).update(d for _, d in _doses(text))
    return ev


def check_sentence(sentence: str, section: str, ev: DoseEvidence,
                   drugs_in: Callable[[str], list[tuple[int, str]]]) -> list[DoseProblem]:
    text = normalise(sentence)
    mentions = drugs_in(text)
    if not mentions:
        return []
    problems = []
    doses = _doses(text)
    for pos, dose in doses:
        before = [d for p, d in mentions if p < pos]
        drug = before[-1] if before else mentions[0][1]
        if drug not in ev.spoken and drug not in ev.record:
            continue  # a drug nobody mentioned is already an UNSUPPORTED_MEDICATION
        spoken, record = ev.spoken.get(drug, set()), ev.record.get(drug, set())
        if dose in ev.withdrawn.get(drug, set()) and dose not in spoken:
            # "20 mg (corrected from the initially stated 40 mg)" documents the correction.
            if not any(other in spoken for _, other in doses if other != dose):
                problems.append(DoseProblem(drug, dose, "withdrawn", section))
        elif dose not in spoken and dose not in record:
            problems.append(DoseProblem(drug, dose, "unsupported", section))
        elif (section == "plan" and ev.new.get(drug) and dose not in ev.new[drug] and dose in record
              and not _CHANGE.search(text) and len(doses) == 1):
            # Not "from 10 mg to 20 mg", which names the old dose on purpose.
            problems.append(DoseProblem(drug, dose, "stale", section))
    return problems

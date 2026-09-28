"""Safety check run on every AI draft before a clinician sees it.

The main risk with an LLM-drafted note is fabrication: a medication, dose or diagnosis that
nobody discussed. This check targets the most dangerous and most checkable form of that:

UNSUPPORTED_MEDICATION (high)
    A medication in the draft that is in neither the patient's active medication list nor the
    visit transcript. Medications are found two ways: the model's own medications_mentioned list,
    and an independent scan of every section's text against a drug lexicon, so a drug the model
    wrote into the plan but left off its list is still caught.

ALLERGY_CONFLICT (high)
    A medication being continued, started or changed that matches a recorded allergy, including
    by class (a penicillin allergy conflicts with amoxicillin).

EMPTY_SECTION (low)
    A SOAP section left blank.

Grounding is by drug identity, not string equality: brand and generic names are unified
("Lipitor" in the transcript grounds "atorvastatin" in the note), and near-misspellings from
speech-to-text ("lisinipril") still count as mentioned.

Known limits (see the measured eval in eval/): it does not check doses, and it only recognises
drugs in its lexicon or the patient's record.
"""

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from .drug_lexicon import ALL_NAMES, ALLERGY_CLASSES, canonical
from .soap import SoapDraft

_WORD = re.compile(r"[a-z][a-z\-]+")
_ACTIVE = {"continued", "started", "changed"}


@dataclass(frozen=True)
class SafetyFlag:
    code: str
    severity: str
    subject: str
    message: str
    section: str

    def to_event(self) -> dict:
        return asdict(self)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _canonical_words(text: str) -> set[str]:
    return {canonical(w) for w in _words(text)}


def _fuzzy_in(word: str, vocabulary: set[str]) -> bool:
    """Tolerates one-or-two-letter transcription errors in longer drug names."""
    if word in vocabulary:
        return True
    if len(word) < 6:
        return False
    return any(abs(len(v) - len(word)) <= 2 and SequenceMatcher(None, word, v).ratio() >= 0.85 for v in vocabulary)


def _drugs_in(text: str, extra_names: set[str]) -> set[str]:
    """Drug names (as written) that appear in text."""
    words = _words(text)
    return {w for w in words if w in ALL_NAMES or w in extra_names}


def check(draft: SoapDraft, *, transcript: str, record_medications: list[str], allergies: list[str]) -> list[SafetyFlag]:
    flags: list[SafetyFlag] = []
    sections = draft.sections()

    for name, text in sections.items():
        if not text.strip():
            flags.append(SafetyFlag("EMPTY_SECTION", "low", name, f"The {name} section is empty.", name))

    # What the draft is allowed to mention: the record and the transcript, as canonical drug names.
    record_words = set().union(*(_canonical_words(m) for m in record_medications)) if record_medications else set()
    transcript_words = _canonical_words(transcript)
    grounded = record_words | transcript_words

    # Record drug names outside the lexicon (e.g. Synthea's less common drugs) are recognised too.
    record_drug_names = {w for w in record_words if len(w) >= 5}

    # Candidates: the model's own list, plus an independent scan of the note text.
    candidates: dict[str, tuple[str, str]] = {}  # canonical -> (as written, section)
    for mention in draft.medications_mentioned:
        for word in _words(mention.name):
            if word in ALL_NAMES or word in record_drug_names:
                candidates.setdefault(canonical(word), (word, _section_of(word, sections)))
    for section, text in sections.items():
        for word in _drugs_in(text, record_drug_names):
            candidates.setdefault(canonical(word), (word, section))

    for drug, (as_written, section) in sorted(candidates.items()):
        if not (_fuzzy_in(drug, grounded) or _fuzzy_in(as_written, _words(transcript))):
            flags.append(SafetyFlag(
                "UNSUPPORTED_MEDICATION", "high", as_written,
                f"'{as_written}' appears in the draft but is not in the patient's active medications or the visit transcript.",
                section))

    # Allergy conflicts, for drugs the draft keeps or starts.
    allergy_words = set().union(*(_canonical_words(a) for a in allergies)) if allergies else set()
    active = {canonical(w) for m in draft.medications_mentioned if m.status in _ACTIVE for w in _words(m.name)}
    for drug in sorted(active):
        conflict = drug if drug in allergy_words else next(
            (cls for cls, members in ALLERGY_CLASSES.items() if cls in allergy_words and drug in members), None)
        if conflict:
            flags.append(SafetyFlag(
                "ALLERGY_CONFLICT", "high", drug,
                f"'{drug}' is continued or started, but the patient has a recorded allergy ({conflict}).",
                _section_of(drug, sections)))

    return flags


def _section_of(word: str, sections: dict[str, str]) -> str:
    for name, text in sections.items():
        if word in _words(text) or canonical(word) in _canonical_words(text):
            return name
    return "medications_mentioned"

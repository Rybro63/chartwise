"""Accuracy of a speech-to-text transcript against the text that was spoken.

- word error rate: (substitutions + deletions + insertions) / reference words, after both sides
  are normalised (lower case, numbers as digits, no punctuation), ignoring speaker labels;
- speaker accuracy: of the words the transcript got right, the share attributed to the right
  speaker;
- drug recall: of the drug names spoken, the share the transcript contains (a misheard drug name
  is the error that matters most clinically).
"""

import re
from difflib import SequenceMatcher

from note_worker.drug_lexicon import ALL_NAMES, canonical
from note_worker.textnorm import normalise

_TOKEN = re.compile(r"[a-z0-9]+(?:[./][0-9]+)?")


def labelled_words(transcript: str) -> list[tuple[str, str]]:
    """(speaker, word) pairs; lines without a "Speaker:" prefix (such as "[crosstalk]") are skipped."""
    out = []
    for line in transcript.splitlines():
        speaker, sep, text = line.partition(":")
        if not sep or " " in speaker.strip():
            continue
        out += [(speaker.strip(), w) for w in _TOKEN.findall(normalise(text))]
    return out


def word_errors(ref: list[str], hyp: list[str]) -> int:
    """Levenshtein distance over words."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def score(reference: str, transcript: str, speakers: dict[str, str] | None = None,
          extra_drugs: set[str] | None = None) -> dict:
    """speakers maps reference speakers to the transcript's labels (a family member speaks on the
    patient's channel, so "Daughter" is expected as "Patient")."""
    speakers = speakers or {}
    ref = [(speakers.get(s, s), w) for s, w in labelled_words(reference)]
    hyp = labelled_words(transcript)
    ref_words, hyp_words = [w for _, w in ref], [w for _, w in hyp]
    errors = word_errors(ref_words, hyp_words)

    matched = same_speaker = 0
    for block in SequenceMatcher(None, ref_words, hyp_words, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            matched += 1
            same_speaker += ref[block.a + k][0] == hyp[block.b + k][0]

    vocabulary = ALL_NAMES | (extra_drugs or set())
    spoken = [w for w in ref_words if w in vocabulary]
    heard = {canonical(w) for w in hyp_words}
    found = [w for w in spoken if canonical(w) in heard]
    return {
        "reference_words": len(ref_words),
        "word_errors": errors,
        "wer": round(errors / len(ref_words), 4) if ref_words else None,
        "speaker_accuracy": round(same_speaker / matched, 4) if matched else None,
        "drugs_spoken": len(spoken),
        "drugs_heard": len(found),
        "missed_drugs": sorted(set(spoken) - {w for w in found}),
    }

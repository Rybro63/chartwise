"""Grades a SOAP note against a visit's answer key (tools/transcripts/messy.py).

Deterministic and inspectable. A note is split into sentences after normalisation (numbers as
digits, "mg" for milligrams, "148/92" for "148 over 92"). Then:

- an expected fact is found when one sentence, or two adjacent sentences on the same line
  ("Sertraline was offered. The patient declined."), contain an alternative from every keyword
  group;
- a forbidden fact is violated when one sentence matches all its groups and contains none of its
  "unless" cues (so "sertraline was offered; patient declined" does not count as starting it).

Keyword matching is a floor, not a ceiling: a correct paraphrase the key didn't anticipate counts
as an omission. The LLM judge (judge.py) and the human gold set (gold.py) exist to measure how
often that happens.
"""

import re
from dataclasses import dataclass, field

from note_worker.textnorm import normalise


def split_sentences(text: str) -> list[str]:
    """Sentences of a note as written, split on line breaks and sentence-ending punctuation
    (not decimals). The judge and the gold set label these."""
    out = []
    for line in text.splitlines():
        for s in re.split(r"(?<=[.!?;])\s+(?=\S)", line):
            s = s.strip(" -*•\t")
            if s:
                out.append(s)
    return out


def sentences(text: str) -> list[str]:
    """Normalised sentences, for keyword matching."""
    return [normalise(s) for s in split_sentences(text)]


def _windows(text: str) -> list[str]:
    """Each sentence, and each pair of adjacent sentences on the same line."""
    out = []
    for line in text.splitlines():
        ss = [normalise(s) for s in split_sentences(line)]
        out += ss + [f"{a} {b}" for a, b in zip(ss, ss[1:])]
    return out


def _has(sentence: str, keyword: str) -> bool:
    # Keywords match at a word start and may be stems ("cardiolog"), but numbers must be whole:
    # "78" must not match "178" or "7.8".
    pattern = r"(?<![\w.])" + re.escape(keyword) + (r"(?![\d])" if keyword[-1:].isdigit() else "")
    return re.search(pattern, sentence) is not None


def _matches(sentence: str, groups: list[list[str]]) -> bool:
    return all(any(_has(sentence, k) for k in group) for group in groups)


@dataclass
class Grade:
    expected: list[dict] = field(default_factory=list)   # {id, kind, text, found, sentence}
    forbidden: list[dict] = field(default_factory=list)  # {id, kind, text, violated, sentence}

    @property
    def recall(self) -> float:
        return sum(e["found"] for e in self.expected) / len(self.expected) if self.expected else 1.0

    @property
    def violations(self) -> int:
        return sum(f["violated"] for f in self.forbidden)

    def to_dict(self) -> dict:
        return {"recall": round(self.recall, 4), "violations": self.violations,
                "expected": self.expected, "forbidden": self.forbidden}


def grade(note: dict[str, str], facts: dict) -> Grade:
    """note: {"subjective", "objective", "assessment", "plan"}; facts: a visit's answer key."""
    sents = [(section, s) for section in ("subjective", "objective", "assessment", "plan")
             for s in sentences(note.get(section) or "")]
    windows = [w for section in ("subjective", "objective", "assessment", "plan") for w in _windows(note.get(section) or "")]
    result = Grade()
    for fact in facts["expected"]:
        hit = next((w for w in windows if _matches(w, fact["all"])), None)
        result.expected.append({"id": fact["id"], "kind": fact["kind"], "text": fact["text"], "found": hit is not None,
                                "sentence": hit})
    for fact in facts["forbidden"]:
        scope = fact.get("sections")
        hit = next((s for section, s in sents if (not scope or section in scope) and _matches(s, fact["all"])
                    and not any(_has(s, u) for u in fact["unless"])), None)
        result.forbidden.append({"id": fact["id"], "kind": fact["kind"], "text": fact["text"], "violated": hit is not None,
                                 "sentence": hit})
    return result


def summarise(grades: list[Grade]) -> dict:
    """Aggregate recall and violation rates, overall and per fact kind."""
    by_kind: dict[str, dict] = {}
    for g in grades:
        for e in g.expected:
            k = by_kind.setdefault(e["kind"], {"type": "expected", "total": 0, "hits": 0})
            k["total"] += 1
            k["hits"] += e["found"]
        for f in g.forbidden:
            k = by_kind.setdefault(f["kind"], {"type": "forbidden", "total": 0, "hits": 0})
            k["total"] += 1
            k["hits"] += f["violated"]
    for k in by_kind.values():
        k["rate"] = round(k["hits"] / k["total"], 4) if k["total"] else None
    expected = sum(len(g.expected) for g in grades)
    forbidden = sum(len(g.forbidden) for g in grades)
    return {
        "notes": len(grades),
        "fact_recall": round(sum(e["found"] for g in grades for e in g.expected) / expected, 4) if expected else None,
        "violation_rate": round(sum(g.violations for g in grades) / forbidden, 4) if forbidden else None,
        "notes_with_violation": sum(1 for g in grades if g.violations),
        "by_kind": dict(sorted(by_kind.items())),
    }

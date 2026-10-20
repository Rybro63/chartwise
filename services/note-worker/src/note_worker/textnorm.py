"""Normalises clinical text so numbers can be compared: "twenty milligrams" -> "20 mg",
"148 over 92" -> "148/92". Used by the safety check's dose comparison and the note eval's grader."""

import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD = re.compile(r"[a-z]+|[^a-z]+")


def number_words(text: str) -> str:
    """'one hundred fifty' -> '150', 'twenty-five' -> '25', 'of ten' -> 'of 10'. Expects lower case."""
    tokens = _WORD.findall(text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        value, j = _read_number(tokens, i)
        if j > i:
            out.append(str(value))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return "".join(out)


def _read_number(tokens: list[str], i: int) -> tuple[int, int]:
    """Reads a spelled-out number starting at tokens[i]; returns (value, next index), or (0, i)."""
    value, j = 0, i
    if j < len(tokens) and tokens[j] in _ONES and j + 2 < len(tokens) and tokens[j + 1] == " " and tokens[j + 2] == "hundred":
        value, j = 100 * _ONES[tokens[j]], j + 3
        if j + 2 < len(tokens) and tokens[j] == " " and tokens[j + 1] == "and" and tokens[j + 2] == " ":
            j += 2
        if j + 1 < len(tokens) and tokens[j] == " " and (tokens[j + 1] in _TENS or tokens[j + 1] in _ONES):
            j += 1
        else:
            return value, j
    if j < len(tokens) and tokens[j] in _TENS:
        value, j = value + _TENS[tokens[j]], j + 1
        if j + 1 < len(tokens) and tokens[j] in ("-", " ") and tokens[j + 1] in _ONES and tokens[j + 1] != "zero":
            value, j = value + _ONES[tokens[j + 1]], j + 2
        return value, j
    if j < len(tokens) and tokens[j] in _ONES:
        return value + _ONES[tokens[j]], j + 1
    return (value, j) if j > i else (0, i)


def normalise(text: str) -> str:
    """Lower case, numbers as digits, "mg" for milligrams, blood pressure as "148/92"."""
    t = text.lower().replace("–", "-").replace("—", " - ")
    t = re.sub(r"\ba hundred\b", "one hundred", t)
    t = number_words(t)
    t = re.sub(r"(\d)\s*(?:milligrams?|mgs?)\b", r"\1 mg", t)
    t = re.sub(r"(\d)\s*(?:micrograms?|mcg)\b", r"\1 mcg", t)
    t = re.sub(r"\b(\d{2,3})\s*(?:over|/)\s*(\d{2,3})\b", r"\1/\2", t)
    t = re.sub(r"(\d)\s*-\s*(day|week|month|year)", r"\1 \2", t)
    t = re.sub(r"(\d)\s*x\b", r"\1x", t)
    return re.sub(r"[ \t]+", " ", t)

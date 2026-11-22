"""the OCR date grammar: one `ocr_text` reading's lines -> one calendar date, or none (§8.1 P8).

M12 Task 9, design 8.1 rev 27 (owner decision D1: day precision only). It runs at PLAN time over
stored lines (P7, P9), so it is part of the model-free naming engine and re-reads nothing.
Pure: stdlib `re`, `datetime` and `unicodedata` only.

Each line scoring AT OR ABOVE the floor is NFKC-normalised and read separately. Every match of a
G-row is a CANDIDATE: the set of its readings that are real calendar dates (one reading for G1,
G2 and G5; two for the ambiguous G3/G4). A match with no valid reading is not a candidate
(P8: "a set of one or two calendar dates"). The rule: emit D iff some candidate is exactly {D}
AND every candidate contains D.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from unicodedata import normalize

DATE_GRAMMAR_VERSION = 1  # P8: ANY change to the grammar bumps this; frozen into config_json.


@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float  # the recogniser's score for the line, in [0, 1]


@dataclass(frozen=True)
class FoundDate:
    date: str  # "YYYY-MM-DD"
    text: str  # the exact substring the grammar matched
    confidence: float  # the score of the line that substring came from


_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MONTHS: dict[str, int] = {}
for _number, _name in enumerate(_MONTH_NAMES, start=1):
    _MONTHS[_name] = _number
    _MONTHS[_name[:3]] = _number
_MONTHS["sept"] = 9

_SEP = r"[-./ ]"  # G1, G2, G3: one of - . / space
_SEP4 = r"[-./]"  # G4: no space
_APOS = "['’]"
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY = r"(?P<d>\d{1,2})(?:st|nd|rd|th)?"
_YEAR_TAIL = r"(?:,\s*|\s+)(?P<y>\d{4})(?!\d)"

_G1 = re.compile(rf"(?<!\d)(?P<y>\d{{4}}){_SEP}(?P<m>\d{{1,2}}){_SEP}(?P<d>\d{{1,2}})(?!\d)")
_G2 = re.compile(
    rf"(?<!\d){_APOS}(?P<yy>\d{{2}}){_SEP}?(?P<m>\d{{1,2}}){_SEP}(?P<d>\d{{1,2}})(?!\d)"
)
_G3 = re.compile(
    rf"(?<!\d)(?P<a>\d{{1,2}}){_SEP}(?P<b>\d{{1,2}}){_SEP}?{_APOS}(?P<yy>\d{{2}})(?!\d)"
)
_G4 = re.compile(rf"(?<!\d)(?P<a>\d{{1,2}}){_SEP4}(?P<b>\d{{1,2}}){_SEP4}(?P<y>\d{{4}})(?!\d)")
_G5_MONTH_FIRST = re.compile(rf"(?<!\d)\b(?P<mon>{_MON})\b\s+{_DAY}{_YEAR_TAIL}", re.IGNORECASE)
_G5_DAY_FIRST = re.compile(rf"(?<!\d){_DAY}\s+(?P<mon>{_MON})\b{_YEAR_TAIL}", re.IGNORECASE)


def _pivot(yy: str) -> int:
    """POSIX `%y`: 69-99 -> 1969-1999, 00-68 -> 2000-2068."""
    value = int(yy)
    return 1900 + value if value >= 69 else 2000 + value


def _real(year: int, month: int, day: int) -> str | None:
    """The ISO date if (year, month, day) is a real calendar date, else None."""
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def _readings(pattern: re.Pattern[str], match: re.Match[str]) -> list[str | None]:
    """Every reading of one match, valid or not, per its G-row."""
    if pattern is _G1:
        year = int(match["y"])
        if not 1900 <= year <= 2099:
            return []
        return [_real(year, int(match["m"]), int(match["d"]))]
    if pattern is _G2:
        return [_real(_pivot(match["yy"]), int(match["m"]), int(match["d"]))]
    if pattern in (_G3, _G4):
        year = _pivot(match["yy"]) if pattern is _G3 else int(match["y"])
        a, b = int(match["a"]), int(match["b"])
        return [_real(year, a, b), _real(year, b, a)]
    return [_real(int(match["y"]), _MONTHS[match["mon"].lower()], int(match["d"]))]


_PATTERNS = (_G1, _G2, _G3, _G4, _G5_MONTH_FIRST, _G5_DAY_FIRST)


def _candidates(text: str) -> list[tuple[frozenset[str], str]]:
    """One line's candidates, in reading order, each with the substring it was read from."""
    line = normalize("NFKC", text)
    found: list[tuple[int, int, frozenset[str], str]] = []
    for rank, pattern in enumerate(_PATTERNS):
        for match in pattern.finditer(line):
            valid = frozenset(r for r in _readings(pattern, match) if r is not None)
            if valid:
                found.append((match.start(), rank, valid, match.group(0)))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(valid, substring) for _, _, valid, substring in found]


def find_date(lines: Sequence[OcrLine], *, floor: float) -> FoundDate | None:
    """P8 over the lines scoring at or above `floor`: emit D iff some candidate is exactly {D}
    and every candidate contains D. Equal scores go to the earliest line."""
    read = [(line, _candidates(line.text)) for line in lines if line.score >= floor]
    everything = [candidate for _, cands in read for candidate, _ in cands]
    exact = {next(iter(c)) for c in everything if len(c) == 1}
    agreed = [d for d in sorted(exact) if all(d in c for c in everything)]
    if not agreed:
        return None
    winner = frozenset(agreed[:1])
    best: FoundDate | None = None
    for line, cands in read:
        substring = next((s for c, s in cands if c == winner), None)
        if substring is not None and (best is None or line.score > best.confidence):
            best = FoundDate(date=agreed[0], text=substring, confidence=line.score)
    return best

"""Filesystem-safe name rules (design spec section 6.2). Pure functions only."""

import re
import unicodedata
from dataclasses import dataclass

MAX_FULL_PATH = 240  # spec 6.2 rule 5: margin under Windows' 260

_WHITESPACE = re.compile(r"\s+")
_ILLEGAL = re.compile(r"[^A-Za-z0-9._-]")
_SEPARATOR_RUNS = re.compile(r"[-_]{2,}")
_EDGES = re.compile(r"^[.\-_]+|[.\-_]+$")

# spec 6.2 rule 2 (rev 4): Latin letters NFKD does not decompose, which the
# allowlist would otherwise delete outright ('Ægir' -> 'gir'). Normative table.
# Deliberately NOT unidecode/ICU: those are pip dependencies, and pam's runtime
# stack is stdlib + click by design (spec 1). Fifteen entries cover every letter
# this pipeline can actually receive, so the dependency buys nothing. Extending
# the table is a one-row spec change; adding a library is an amendment.
_FOLD = {
    "ø": "o",
    "Ø": "O",
    "ł": "l",
    "Ł": "L",
    "đ": "d",
    "Đ": "D",
    "ß": "ss",
    "æ": "ae",
    "Æ": "AE",
    "œ": "oe",
    "Œ": "OE",
    "þ": "th",
    "Þ": "TH",
    "ð": "d",
    "Ð": "D",
}


def _collapse_run(match: re.Match[str]) -> str:
    return match.group()[0]


@dataclass(frozen=True)
class SanitizeReport:
    """What rules 1-3 DID to one token, reported by the code that did it.

    Spec 6.2 RULE 7 ("pam says what it changed") needs to name the rule that fired,
    and the only honest place to answer that is at each substitution site: a caller
    that diffed the input against `text` afterwards would be re-implementing 6.2 by
    inspection - it could say THAT something changed but never WHICH rule changed it,
    and the three normative sentences differ by exactly that. Each flag below is set
    by comparing one step's output with that same step's input, so no flag can be true
    for a rule that did not run and none can be true for a rule that ran and did
    nothing.

    The four flags are separate because they are four separate substitutions, not
    because rule 7 distinguishes them - rule 7's copy gives rules 1-3 ONE sentence, so
    `changed` is what the review UI reads. They are reported apart anyway because a
    single boolean cannot be tested against the rule that produced it, and because M9
    inherits rule 7's other half and may want finer copy than M8 owes.
    """

    text: str
    whitespace: bool  # rule 1: a run of whitespace became '-'
    folded: bool  # rule 2: NFC/NFKD normalisation, a stripped mark, or the fold table
    removed: bool  # rule 3: a character outside the allowlist was deleted
    tidied: bool  # rules 1-3's cleanup: a separator run collapsed, or an edge trimmed

    @property
    def changed(self) -> bool:
        """Did rules 1-3 alter the text at all - rule 7's rules-1-3 sentence."""
        return self.whitespace or self.folded or self.removed or self.tidied


def sanitize_report(text: str) -> SanitizeReport:
    """Rules 1-3 for one token, plus which of them fired (spec 6.2 rules 1-3 and 7).

    `sanitize_token` is this function's text, so there is exactly one implementation
    of rules 1-3 and the report cannot drift from the result it describes.
    """
    normalized = unicodedata.normalize("NFC", text)
    collapsed = _WHITESPACE.sub("-", normalized)
    # NFKD (not NFD) so compatibility forms fold instead of vanishing:
    # the 'ﬁ' ligature becomes 'fi', fullwidth 'Ａ' becomes 'A'.
    decomposed = unicodedata.normalize("NFKD", collapsed)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    folded = "".join(_FOLD.get(ch, ch) for ch in stripped)
    kept = _ILLEGAL.sub("", folded)
    runs = _SEPARATOR_RUNS.sub(_collapse_run, kept)
    edged = _EDGES.sub("", runs)
    return SanitizeReport(
        text=edged,
        whitespace=collapsed != normalized,
        # The NFC pass is rule 2's normalisation and is measured with the rest of it:
        # on its own it can only compose or decompose marks, which is the same rule.
        folded=folded != collapsed or normalized != text,
        removed=kept != folded,
        tidied=edged != kept,
    )


def sanitize_token(text: str) -> str:
    """Rules 1-3 for one token; may return '' when nothing survives.

    Deterministic and idempotent: same input -> same output, and a sanitized
    token passes through unchanged.
    """
    return sanitize_report(text).text


_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(0, 10)}  # rev 4: Windows reserves COM0/LPT0 too
    | {f"lpt{i}" for i in range(0, 10)}
)


def reserved_head(stem: str) -> str | None:
    """The stem's first dot-segment when Windows would read it as a device name.

    Returns None otherwise. A device name contains neither '.' nor '_', so the
    head this returns never spans a separator - build_stem relies on that to
    know the -x suffix lands inside the first token's value.
    """
    head = stem.partition(".")[0]
    return head if head.lower() in _RESERVED_STEMS else None


def apply_reserved_stem_rule(stem: str) -> str:
    """Rule 4: a stem Windows would treat as a device name gets an -x suffix.

    Windows reserves the segment before the first dot ('con.backup' is still
    CON to Win32), so the check and the suffix both apply to that segment.
    """
    head = reserved_head(stem)
    if head is None:
        return stem
    return f"{head}-x{stem[len(head) :]}"


def full_path_length(archive_root_abs: str, rel_dir: str, name: str) -> int:
    """Rule 5's formula, taken literally; MAX_FULL_PATH leaves margin under 260.

    The formula omits the two directory separators, so a real path can be up to
    2 characters longer than this returns. That is safe by arithmetic, not by
    accident: at the 240 budget the worst true path is 242 against Windows' 260,
    leaving 18 characters of margin. Do not trim MAX_FULL_PATH without redoing
    this sum.
    """
    return len(archive_root_abs) + len(rel_dir) + len(name)


def stem_budget(archive_root_abs: str, rel_dir: str, exts: tuple[str, ...]) -> int:
    """Characters available for the stem itself (rule 5); the longest ext wins.

    The single source of this arithmetic - build_stem uses it and the tests invert
    it, so the two cannot drift. exts holds every extension that will share the
    stem (companion members rename together, spec 5.4); a leading dot is optional
    because the formula already reserves a character for it.
    """
    longest_ext = max((len(e.lstrip(".")) for e in exts), default=0)
    return MAX_FULL_PATH - full_path_length(archive_root_abs, rel_dir, "") - longest_ext - 1

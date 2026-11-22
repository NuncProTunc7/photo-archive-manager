r"""OD-8's version grammar: `X.Y.Z` stable and `X.Y.ZrcN` a rehearsal (M15 Task 9).

A STRICT SUBSET OF PEP 440, ORDERED BY PEP 440'S RULES: a release candidate sorts below its
release, and every component compares as a NUMBER - `1.0.0rc10` above `1.0.0rc2`, `1.10.0` above
`1.9.0`, both of which a string comparison gets wrong. Nothing else PEP 440 allows is admitted
(no `v`, no separators before `rc`, no `a`/`b`/`.post`/`.dev`/`+local`, no leading zeros, no
`rc0`): `pam update` compares an offered release with the installed one, and a grammar wider
than what pam publishes is a grammar nobody has tested the order of.

`[0-9]`, never `\d`: `\d` matches every Unicode digit, so a full-width `1` would parse.
`fullmatch`, never `match` with `$`: `$` also matches before a trailing newline.
"""

import functools
import re
from dataclasses import dataclass

from pam.errors import PamError

_NUMBER = r"(0|[1-9][0-9]*)"
_GRAMMAR = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:rc([1-9][0-9]*))?")


class VersionError(PamError):
    """Text that is not OD-8's version grammar."""


@functools.total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    rc: int | None = None

    @property
    def is_rc(self) -> bool:
        return self.rc is not None

    def _key(self) -> tuple[int, int, int, int, int]:
        # A release sorts above every one of its candidates: (.., 0, N) < (.., 1, 0).
        if self.rc is None:
            return (self.major, self.minor, self.patch, 1, 0)
        return (self.major, self.minor, self.patch, 0, self.rc)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        return text if self.rc is None else f"{text}rc{self.rc}"


def parse(text: object) -> Version:
    """The `Version` that `text` spells, or `VersionError` naming it."""
    match = _GRAMMAR.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise VersionError(
            f"{text!r} is not a pam version: expected X.Y.Z or X.Y.ZrcN, for example 1.0.0"
            " or 1.0.0rc1."
        )
    major, minor, patch, rc = match.groups()
    return Version(int(major), int(minor), int(patch), None if rc is None else int(rc))

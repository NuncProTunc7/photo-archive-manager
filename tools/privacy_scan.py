r"""Scan an export's staged tree for private information before anything is published (M15 Task 14).

    python tools/privacy_scan.py STAGING [--denylist PATH]

STAGING is the folder `tools/export_public.py` wrote. Every file in it is read - text and binary
alike, decoded as UTF-8 with replacement, so a name inside an image's metadata is seen too - and
checked line by line against `PATTERNS` and against THE DENYLIST THE OWNER KEEPS.

THE DENYLIST lives outside every repository, at `%USERPROFILE%\.pam-privacy-denylist.txt` unless
`--denylist` names another file. One entry per line - a name, a place, a username, a PC name, a
folder path: any text that must never be published - with its spaces, backslashes and accented
letters kept as written; `#` comment lines and blank lines are ignored; entries match
case-insensitively as whole words. **The scan refuses to run without it**, because a scan that
silently skips the list the owner keeps would report "clean" over the names it exists for. Its
entries are read at run time and NEVER printed: a hit says `denylist entry N`, the entry's number
among the entries.

NO HIT IS EVER ECHOED. Every hit is reported as `path:line: pattern-name` (line 0 is the file's own
path), never the matched
text - the report is meant to be read by the owner, pasted into a review, and kept; the text it
points at is the private part. The owner opens the file to see it.

VERBATIM LICENCE TEXTS (the maintainer's ruling, 2026-11-21): a third-party licence text must ship word for
word, and a denylist entry can be an ordinary word inside it. The files in
`VERBATIM_LICENCES` are exempt from the DENYLIST - never from `PATTERNS`, never their path - and only
while their bytes are exactly the upstream text pinned here (SHA-256 with LF line endings). An edited
copy is scanned like any other file.

Exit 0 clean, 1 hits found, 2 refused (no denylist, no staging folder).
"""

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path

EXIT_CLEAN, EXIT_HITS, EXIT_REFUSED = 0, 1, 2
DENYLIST_NAME = ".pam-privacy-denylist.txt"

# The TEST-ONLY TLS keys (M15 Task 12) are private keys on purpose: a loopback TLS server in the
# update tests uses them and they protect nothing (tests/fixtures/tls/README.txt). Allowed by this
# exact folder AND this exact suffix, never by a broader glob.
TEST_KEY_FOLDER = "tests/fixtures/tls/"
TEST_KEY_SUFFIX = ".TEST-ONLY.key"

# name -> regex. Each is a shape of private information this project has carried or could carry.
PATTERNS: dict[str, re.Pattern[str]] = {
    # A personal Windows profile path; the stand-in `C:\Users\user\` and placeholders pass.
    "windows-user-path": re.compile(
        r"[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+"
        r"(?!(?:user|Public|Default|runneradmin|<|%|\$|\{)(?:\b|[\\/<%${]))[^\\/\s\"'`]+",
        re.IGNORECASE,
    ),
    "posix-home-path": re.compile(
        r"(?<![\w.:])/(?:home|Users)/(?!(?:user|runner)\b)[A-Za-z0-9._-]+"
    ),
    # An email address, except GitHub's noreply form, the reserved example domains and the reserved
    # top-level names (RFC 2606: .invalid, .test, .example, .localhost).
    "email": re.compile(
        r"(?<![\w/+.-])[\w.+-]+@(?!users\.noreply\.github\.com\b|example\.(?:com|org|net)\b)"
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?!(?:dll|exe|sys|invalid|test|example|localhost)\b)[A-Za-z]{2,24}\b"
    ),
    # Windows' generated PC names (the stand-in is MY-PC).
    "pc-name": re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{5,}\b"),
    # Number-in-context: an observation about a real library ("N of M real files"). The enrichers'
    # own summary line ("caption: 2 of 10 files read") is product copy, not an observation.
    # Every pattern below is written so that its own source text does not match it ("[ ]",
    # "[']"): this file is in the export and is scanned like every other.
    "archive-figure": re.compile(
        r"\b\d[\d,]*[ ]of[ ]\d[\d,]*[ ](?:real[ ])?files\b(?![ ]read\b)", re.IGNORECASE
    ),
    "real-archive": re.compile(r"\breal[ ]archive", re.IGNORECASE),
    "test-folder": re.compile(r"TEST[ ]FOLDER", re.IGNORECASE),
    "owners": re.compile(r"\bowner[']s\b", re.IGNORECASE),
    # The private repository must never be named by the public one.
    "private-repository": re.compile(r"photo-archive-manager[-]private", re.IGNORECASE),
    "private-key": re.compile(r"-----BEGIN[ ][A-Z ]*PRIVATE[ ]KEY-----"),
}


class Refused(Exception):
    """The scan cannot run; the message is the one sentence to print."""


def default_denylist() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / DENYLIST_NAME


def load_denylist(path: Path) -> list[re.Pattern[str]]:
    if not path.is_file():
        raise Refused(
            f"the privacy scan needs the denylist the owner keeps at {path}"
            " and refuses to run without it"
        )
    entries = []
    # utf-8-sig: a file saved by Notepad may start with a byte-order mark.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            entries.append(entry_pattern(entry))
    if not entries:
        raise Refused(f"the denylist at {path} has no entries; the scan would check nothing")
    return entries


def entry_pattern(entry: str) -> re.Pattern[str]:
    r"""One denylist entry as a case-insensitive whole-word match.

    "Whole word" is asked only at an edge that IS a word character: an entry such as a folder path
    ending in a backslash must still match when a file name follows it. Only a LETTER beside the
    entry makes it part of a longer word (Task 14 review F2): `_` and digits are edges, because
    pam's own names join tokens with `_` and number them, so a name inside `2019-07-04_<name>_001`
    must be flagged. `[^\W\d_]` is a Unicode letter, so an accented letter counts on both sides.
    """
    head = r"(?<![^\W\d_])" if re.match(r"\w", entry) else ""
    tail = r"(?![^\W\d_])" if re.search(r"\w$", entry) else ""
    return re.compile(head + re.escape(entry) + tail, re.IGNORECASE)


def allowed(name: str, rel: str) -> bool:
    """The one exemption: a private-key line inside a TEST-ONLY key file in the TLS fixtures."""
    return (
        name == "private-key"
        and rel.startswith(TEST_KEY_FOLDER)
        and "/" not in rel[len(TEST_KEY_FOLDER) :]
        and rel.endswith(TEST_KEY_SUFFIX)
    )


# Spelled here independently of tests/test_installer_build.py's LICENCE_TEXTS (each checks the other).
VERBATIM_LICENCES = {
    "tools/installer/licenses/GPL-2.0.txt": "d8afb1bcc7a2cfc603683b168d6987ef0a48e59e0da3693bf55c5d33b67e2b49",
    "tools/installer/licenses/LGPL-3.0.txt": "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
    "tools/installer/licenses/LGPL-2.1.txt": "1d429c98b7beaf7d70d5444b119458eabf211487b29c5f70c66d0948c1de3491",
    "tools/installer/licenses/Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


def verbatim_licence(rel: str, data: bytes) -> bool:
    """True only for a listed path whose bytes (LF line endings) hash to the pinned upstream text."""
    pinned = VERBATIM_LICENCES.get(rel)
    lf = data.replace(b"\r\n", b"\n")
    return pinned is not None and hashlib.sha256(lf).hexdigest() == pinned


def scan_text(
    rel: str, text: str, denylist: list[re.Pattern[str]]
) -> list[tuple[str, int, str]]:
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line) and not allowed(name, rel):
                hits.append((rel, number, name))
        for index, entry in enumerate(denylist, start=1):
            if entry.search(line):
                hits.append((rel, number, f"denylist entry {index}"))
    return hits


def scan(
    staging: Path, denylist: list[re.Pattern[str]]
) -> tuple[int, list[tuple[str, int, str]]]:
    """(files scanned, every hit) over every file under STAGING, `.git` excepted."""
    if not staging.is_dir():
        raise Refused(f"{staging} is not a folder")
    hits: list[tuple[str, int, str]] = []
    count = 0
    for current, dirs, names in os.walk(staging):
        dirs[:] = sorted(d for d in dirs if not (Path(current) == staging and d == ".git"))
        for name in sorted(names):
            path = Path(current) / name
            rel = path.relative_to(staging).as_posix()
            # The file's own path is checked as line 0, so a name in a FILE NAME is caught even
            # where no manifest lists it (Task 14 review F2).
            hits += [(r, 0, n) for r, _, n in scan_text(rel, rel, denylist)]
            data = path.read_bytes()
            content_denylist = [] if verbatim_licence(rel, data) else denylist
            hits += scan_text(rel, data.decode("utf-8", "replace"), content_denylist)
            count += 1
    return count, hits


def report(count: int, hits: list[tuple[str, int, str]]) -> list[str]:
    lines = [f"{rel}:{number}: {name}" for rel, number, name in hits]
    by_pattern = Counter(
        "denylist" if name.startswith("denylist") else name for _, _, name in hits
    )
    lines.append(f"scanned {count} files: {len(hits)} hits")
    lines += [f"  {name}: {by_pattern.get(name, 0)}" for name in [*PATTERNS, "denylist"]]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("staging", type=Path)
    parser.add_argument("--denylist", type=Path)
    args = parser.parse_args(argv)
    try:
        denylist = load_denylist(args.denylist or default_denylist())
        count, hits = scan(args.staging, denylist)
    except (Refused, OSError) as error:
        print(f"x {error}", file=sys.stderr)
        return EXIT_REFUSED
    print("\n".join(report(count, hits)))
    return EXIT_HITS if hits else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())

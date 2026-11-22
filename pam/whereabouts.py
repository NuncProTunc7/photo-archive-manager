"""Where one session's files are NOW: pam's own record, read against the folder.

M7 Task 4, closing the M6 review's condition 6. spec 7.0's closing paragraph names two
states pam cannot produce and something outside pam can - a `done` row whose new path is
gone, and one whose old path exists again - and rules that they are "reported for human
attention and acted on NEVER". spec 7.2's recovery obeyed the first half and not the
second: it reported them ONCE, on stderr, at the moment it met them. Then the console
scrolled away, and `pam verify` went on giving a clean bill - correctly, because the
catalog IS consistent. It is the ARCHIVE that changed, and the archive is what `pam scan`
re-derives. So this is a `pam report` question, and the answer must be re-derivable on
demand, for ever: the journal is append-only (J2), the disk is the truth, and this module
reads both and writes nothing.

WHY IT IS NOT IN `catalog/repo/`. That package is pure SQL by an architectural rule its
own docstring states and `tests/test_repo.py` enforces; a `Path.exists()` there would put
filesystem knowledge in the layer whose whole promise is that it has none. The journal
read is a repo call (`repo.latest_resolved_rows`), the disk read is here, and `pam/report.py`
is the consumer that pairs them.

WHY IT IS NOT IN `pam/report.py` EITHER. Only because that module reached 355 executable
lines with this in it, against spec 1's ~300 budget (rev 10, measured in executable
lines). It is the third subject's third half and nothing else; `report.py` owns the
command and this owns the question.

IT REPAIRS NOTHING AND ASSERTS NOTHING. Every state below is reachable by an ordinary
user renaming a file in Explorer, which spec 7.0 calls legitimate. Treating one as a bug
would make pam crash on ordinary use.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pam.catalog import repo

# The three ways one file can disagree with pam's record of where it is, IN THE ORDER
# THEY ARE PRINTED - descending by how much of a decision they ask of the reader:
#
#   1. both   - a file is at the name pam left it under AND at its other name. spec 7.0's
#               own example of a state pam cannot produce and something outside pam can.
#               Only a human can say which one they want, so it goes first.
#   2. gone   - neither name is in the folder. Nothing pam offers can find it; the reader
#               has to go looking.
#   3. moved  - the file is under its other name. The mildest of the three and the only
#               one a single command clears, so it is last.
BOTH = "both"
GONE = "gone"
MOVED = "moved"

# heading, explanation, and the parenthetical that names the OTHER path on each line.
WHERE_KINDS: tuple[tuple[str, str, str, str], ...] = (
    (
        BOTH,
        "Two files where pam left one",
        "pam left each of these under the first name, and there is now a file under the"
        " second name as well. pam has changed nothing and will not guess which one you"
        " want: look at both, keep the one you want, then run: pam scan",
        "and {other} is there too",
    ),
    (
        GONE,
        "pam cannot find these under either name",
        "Neither name is in this folder now. pam never deletes a file and never moves one"
        " out of the folder, so something else did.",
        "and not {other} either",
    ),
    (
        MOVED,
        "Not where this run left them",
        "Each of these has been renamed since this run - by you, by another program, or"
        " by a later pam run. pam's records for them are out of date. Run: pam scan",
        "a file is at {other} instead",
    ),
)


@dataclass(frozen=True)
class FileWhere:
    """One file whose two names on disk do not match what pam last recorded about it.

    `expected` is where pam's records put it and `other` is the only other name it could
    honestly be under - the pair from the journal row, never a search of the folder.
    """

    kind: str
    expected: str
    other: str


@dataclass(frozen=True)
class Whereabouts:
    """One session's rows against the folder. The three numbers total every row of it.

    Kept as a triple rather than a list of findings so a reader can never be left
    wondering where the rest went - the same reason `repo.SessionCounts` counts all four
    journal states rather than only the interesting ones. Reporting a subset as if it
    were the whole is the failure vision principle 9 names.
    """

    in_place: int
    elsewhere: tuple[FileWhere, ...]
    unresolved: int


def expected_and_other(row: repo.JournalRow) -> tuple[str, str]:
    """Where a resolved row says its file is, and the other name in its pair.

    Read off spec 7.0 J2 and J4: `done` means pam performed that move, so the file is at
    the name it moved TO; `failed` follows `intent` only, so the move never happened and
    the file is at the name it started under; `reverted` follows `done`, so the move
    happened and spec 7.3's revert put it back where it started.

    ONE TABLE FOR BOTH OPS, and that is not an omission. `execute/undo.py` builds its
    rows reversed - the journal's `new_rel_path` is where the file is now, its
    `old_rel_path` is where it goes back to - so `old -> new` is the direction of the
    move a row records whichever op wrote it, and an `undo_rename` row `done` resolves
    through the first branch to the original name exactly as it should.

    Never called with a row in `intent`: J3 says such a row tells you nothing about disk.
    """
    if row.state == "done":
        return row.new_rel_path, row.old_rel_path
    return row.old_rel_path, row.new_rel_path


def where_is(archive_root: Path, expected: str, other: str) -> str | None:
    """The kind of disagreement between pam's record and the disk, or None if there is none.

    Two `Path.exists()` calls per file and nothing else - no hashing, no directory walk.
    A human test's session is hundreds of rows and this runs on every `pam report session`.

    WINDOWS PATH LOOKUP IS CASE-INSENSITIVE, and a case-only rename is therefore INVISIBLE
    here. That is a stated limitation, not a bug to fix in this pass: both paths resolve
    to the one file, so the filesystem cannot be asked which case is on disk. It is also
    the common case rather than a curiosity - almost every item in a phone library's
    plan changes only the extension's case - so a pair that differs only in case is
    answered with the one question the filesystem CAN answer, "is it there at all", and
    never with "a file is at both names". The `casefold()` branch also keeps the answer
    the same on a case-SENSITIVE filesystem, where the two paths really are two files.
    """
    expected_there = (archive_root / expected).exists()
    other_there = (archive_root / other).exists()
    if expected.casefold() == other.casefold():
        return None if expected_there or other_there else GONE
    if expected_there:
        return BOTH if other_there else None
    return MOVED if other_there else GONE


def probe(
    root: Path, rows: list[repo.JournalRow], governing: dict[int, repo.JournalRow]
) -> Whereabouts:
    """Every row of one session against the folder. Reads the disk; writes nothing.

    `governing` is `repo.latest_resolved_rows` - the LAST thing pam recorded about each
    of these files, from any session. Comparing the disk against each row's OWN state
    instead would report every file of an undone session as interfered with, because
    spec 7.0 J2 keeps an apply's `rename` rows `done` for ever; that is a false alarm per file
    on HT-M5's run, printed directly under a catalog half reading "already undone".

    A row still in `intent` is counted, never classified: J3 says it records that pam
    MEANT to rename and nothing at all about disk, so any expectation drawn from it would
    be the guess spec 7.2 forbids. `pam report` runs no recovery (see `pam/report.py`'s
    docstring), so it is the one command that can meet one.

    `governing[...]` is indexed rather than `.get`, and cannot raise: `row` is resolved by
    the line above, and `latest_resolved_rows` selects every resolved row of every plan
    item this session touched, so `row` is in its own answer.
    """
    in_place = 0
    unresolved = 0
    elsewhere: list[FileWhere] = []
    for row in rows:
        if row.state not in repo.RESOLVED_STATES:
            unresolved += 1
            continue
        expected, other = expected_and_other(governing[row.plan_item_id])
        kind = where_is(root, expected, other)
        if kind is None:
            in_place += 1
        else:
            elsewhere.append(FileWhere(kind=kind, expected=expected, other=other))
    return Whereabouts(in_place=in_place, elsewhere=tuple(elsewhere), unresolved=unresolved)


def where_lines(found: Whereabouts, *, path_cap: int, files: Callable[[int], str]) -> list[str]:
    """The disk half: one count line always, then any file that disagrees, by name.

    THE COUNT LINE IS PRINTED EVEN WHEN EVERYTHING IS FINE, unlike `report.skipped_lines`'
    empty groups. "300 of 300" is the answer to the question the command exists for - does
    that run still hold? - and a silent report would leave a reader unable to tell "pam
    checked and all is well" from "pam did not check".

    The phrasing avoids a verb that has to agree with the number ("one of one files are"),
    because these lines are read at a count of one as often as at 300.

    `path_cap` and `files` are passed in rather than spelled here so that this block obeys
    `pam report`'s conventions - the listing cap and the "1 file" / "2 files" phrase - by
    USING them rather than by carrying a second copy that can drift from them.
    """
    total = found.in_place + len(found.elsewhere)
    lines = [
        "Where the files are now:",
        f"  Still where this run left them: {found.in_place:,} of {total:,}.",
    ]
    if found.unresolved:
        # spec 7.2's recovery resolves these; `pam report` deliberately runs none, so it
        # says what it can see and names the read-only command that does repair first.
        lines.append(
            f"  Interrupted and not sorted out yet: {found.unresolved:,}.   Run: pam verify"
        )
    lines.append("")
    for kind, heading, explanation, pair in WHERE_KINDS:
        entries = [entry for entry in found.elsewhere if entry.kind == kind]
        if not entries:
            continue
        lines.append(f"{heading} - {files(len(entries))}")
        lines.append(f"  {explanation}")
        lines.extend(
            f"    {entry.expected}   ({pair.format(other=entry.other)})"
            for entry in entries[:path_cap]
        )
        if len(entries) > path_cap:
            lines.append(f"    ... and {len(entries) - path_cap:,} more")
        lines.append("")
    return lines

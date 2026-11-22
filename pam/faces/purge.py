"""`pam faces purge` (M11 Task 13, amendment A19). Deletes pam's records, not your photos.

THE ORDER IS THE DESIGN: inventory -> confirmation -> delete -> compact -> disclose.
A19 requires the plan inventory to be taken BEFORE `persons` is deleted, because after
the deletion there is nothing left to inventory against; and UX 4's ritual requires the
user to see what they are about to lose BEFORE they type the word. Those two are the same
requirement read from two directions, so the inventory is read first and shown in the box.

THE FIVE DELETIONS ARE ONE TRANSACTION (A19), and each has a symptom nobody would
diagnose if it were missing:
  1. `face_instances` - the faces themselves.
  2. `persons` - the names. After the faces, because `face_instances.person_id`
     REFERENCES `persons(id)` and `catalog/db.py` turns foreign keys on.
  3. the `faces` MARKER FACTS. Leave them and spec 8.1's cache skip reports every file
     already done, so the next `pam enrich faces` detects nothing and says it succeeded.
  4. the `faces` `enrichment_runs` ROWS. Leave them and `open_run` resumes at the old
     cursor, so the next run starts PAST every file the purged one walked.
  5. every cached face crop - AND PAM STORES NONE. See `_CROPS_LINE`.

WHAT IS DELETED AND WHAT IS RECLAIMED ARE TWO DIFFERENT EVENTS, REPORTED SEPARATELY
(A19). The deletion is committed the moment the transaction commits; `VACUUM` is a
separate, later, best-effort act that can fail on a full disk or a locked file, and a
failure there must never read as "nothing happened". So compaction has its own line, its
own exit code and its own retry - and the retry is this same command, which is why a
second purge over an empty face catalog demands no confirmation for deleting nothing.

NO SECURE-ERASE PROMISE (A19). On a wear-levelled SSD no userspace command can guarantee
the bytes are gone, and the disclosure is what this command actually offers instead.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from sqlite3 import Connection

import click

from pam.catalog import repo
from pam.catalog.db import compact, freelist_count, migrate, open_catalog
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log
from pam.status import NextStep, step_lines

# The word, exactly, on `execute/reporter.py`'s precedent and for its reason: "anything
# other than exactly `purge` cancels" is a ritual a user cannot complete without reading.
CONFIRM_WORD = "purge"
CONFIRM_PROMPT = 'Type "purge" to proceed, anything else to cancel: '

# A19's deletion 5, answered honestly rather than performed. `webui/crop.py`'s docstring is
# the other half of this sentence: spec 8.2 permits a crop cache ONLY if it is inventoried
# and purgeable here, and neither exists - every crop is cut on demand and served with
# `Cache-Control: no-store`. A line claiming to have deleted N crops would be a number pam
# invented; a line saying there are none to delete is the thing a user can check.
_CROPS_LINE = (
    "Face crops: pam never stores one - each is cut on demand and not cached,"
    " so there is nothing here to delete."
)

# A19's disclosure, BY NAME. Every entry is somewhere a person's name can already be that
# this command cannot reach, and the list is spelled out here rather than assembled from
# whatever the catalog happens to hold: the point is to name the things pam has no query
# for. `tests/test_faces_purge.py` pins it against the spec's own sentence.
DISCLOSURE = (
    "exports you have already written with pam faces export - pam has no import path,"
    " so an export is a copy of your data and never a backup pam can undo this with",
    "plans and reports pam has already written, whose proposed filenames still carry the names",
    "session logs already written, which record what each run did",
    "your browser's cache, from the labeling page",
    "filesystem backups, file history and cloud sync copies of any of the above",
)


@dataclass(frozen=True)
class PurgeInventory:
    """What is there, read BEFORE anything is deleted (A19).

    `plan_items` and `plans` come from `repo.plan_items_with_people_tokens`, which matches
    on a token's `source_table` and never on label TEXT - A19 is explicit that label
    matching cannot be authoritative, because A14 records only the lowest `person_id` for
    a multi-person token, sanitisation changes the text, and a user-edited filename keeps
    the name with no attribution at all.
    """

    faces: int
    persons: int
    photographs: int
    plans: int
    plan_items: int
    facts: int
    runs: int

    @property
    def empty(self) -> bool:
        """Nothing A19 names is here - all FIVE of them, not the two that are visible.

        `faces == 0 and persons == 0` was not enough, and the case is ordinary rather than
        exotic: `pam enrich faces` over an archive of landscapes writes a `faces` marker
        fact per file and one `enrichment_runs` row and detects nothing, so the catalog
        holds deletions 3 and 4 with no face and no person anywhere in it. Gating the whole
        transaction on the first two counts made that purge delete NOTHING while printing
        "pam holds no faces and no people for this archive" - and left behind exactly the
        rows whose survival is deletion 3's named symptom, so the next `pam enrich faces`
        cache-skipped every file and reported success.

        THE RETRY IS UNTOUCHED (A19). After a purge that reached its commit, all four of
        these are 0, so a second run still finds nothing, asks nothing, and compacts.
        """
        return self.faces == 0 and self.persons == 0 and self.facts == 0 and self.runs == 0


@dataclass
class PurgeReport:
    """Deletion and compaction, as two separate results (A19).

    `compaction_error` being a STRING and not a bool is the amendment's own wording:
    "reports whether compaction succeeded" and offers a safe retry. A user whose VACUUM
    failed needs the reason - a full disk and a locked database are different problems -
    and a bare "compaction failed" sends them to the log for a sentence pam already had.
    """

    archive_root: Path
    inventory: PurgeInventory
    cancelled: bool = False
    deleted: bool = False
    faces: int = 0
    persons: int = 0
    facts: int = 0
    runs: int = 0
    crops: int = 0
    compacted: bool = False
    compaction_error: str = ""
    freelist_after: int = -1
    elapsed_s: float = 0.0
    log_path: Path | None = None
    disclosure: tuple[str, ...] = field(default_factory=lambda: DISCLOSURE)


ConfirmFn = Callable[[PurgeInventory, Path], bool]
CompactFn = Callable[[Connection], None]


def read_inventory(conn: Connection) -> PurgeInventory:
    """Everything the confirmation box and the disclosure need, in one read."""
    faces = repo.export_faces(conn)
    affected = repo.plan_items_with_people_tokens(conn)
    return PurgeInventory(
        faces=len(faces),
        persons=len(repo.persons_all(conn)),
        photographs=len({f.sha256 for f in faces}),
        plans=len(affected),
        plan_items=sum(affected.values()),
        # THE SAME PREDICATES THE DELETION USES, so the box and the transaction cannot
        # disagree about what is about to go: every kind `fact_kinds("faces")` names,
        # superseded rows included, and every `faces` run row, finished ones included.
        facts=repo.count_facts_of_kinds(conn, repo.fact_kinds("faces")),
        runs=repo.count_enrichment_runs(conn, "faces"),
    )


def confirmation_lines(inventory: PurgeInventory, root: Path) -> list[str]:
    """UX 4's block for this command: what goes, what stays, and the way to keep it.

    IT NAMES PHOTOGRAPHS AND SAYS THEY ARE NOT TOUCHED (Task 13 Step 1). "Faces" and
    "photographs" are two counts of two different things and a user reading one number
    could reasonably fear the other: 402 faces over 180 photographs is a sentence that
    answers "how much of my archive is this about" without anybody having to guess.

    IT POINTS AT `pam faces export` BEFORE the prompt, not after (A19). Afterwards is
    where a sentence about keeping your data goes when nobody expects it to be read.
    """
    lines = [f"Purge every face pam holds for {root}", ""]
    lines.append(
        f"  {inventory.faces:,} faces found over {inventory.photographs:,} photographs"
    )
    lines.append(f"  {inventory.persons:,} people you have named")
    # DELETIONS 3 AND 4 ARE IN THE BOX because they can be the ONLY thing in it: an
    # archive the detector found no face in holds a marker fact per file and a run record
    # and nothing else, and a box that named only faces and people would ask the user to
    # type `purge` under two zeroes and then delete something it never mentioned.
    lines.append(
        f"  {inventory.facts:,} note(s) recording which files pam has already looked at,"
        f" and {inventory.runs:,} record(s) of a face run"
    )
    lines.append("")
    lines.append(
        "NO PHOTOGRAPH IS TOUCHED. This deletes pam's own records - the faces it"
        " detected, the names you gave them, and the notes saying which files it has"
        " looked at. Not one file in your archive is read, written, renamed or deleted."
    )
    lines.append("")
    if inventory.plan_items:
        lines.append(
            f"  ! {inventory.plan_items:,} plan item(s) in {inventory.plans:,} plan(s)"
            " were named using a person. Purging does NOT change those proposed"
            " filenames - they keep the name."
        )
        lines.append("")
    lines.append(
        "To keep a copy of the names and faces first, cancel and run: pam faces export"
    )
    return lines


def confirm_purge(inventory: PurgeInventory, root: Path) -> bool:
    """Print the block and read the one word. True only for exactly `purge`.

    `input()` rather than `click.prompt`, for `confirm_apply`'s reason: the seam a test
    can hold is a stdlib builtin, and EOF is visible here rather than handled by a
    library. A CLOSED STDIN CANCELS - `pam faces purge < /dev/null` must not delete a
    year of somebody's labeling because a script pointed the wrong way.

    The branching is unit-tested; the CONSOLE behaviour is not and cannot be (CLAUDE.md:
    pytest has no console). HT-M11's Leg D is where this is confirmed by hand.
    """
    for line in confirmation_lines(inventory, root):
        click.echo(line)
    click.echo()
    click.echo(CONFIRM_PROMPT, nl=False)
    try:
        typed = input()
    except EOFError:
        click.echo()
        return False
    return typed == CONFIRM_WORD


def run_purge(
    archive: Path | None,
    *,
    confirm: ConfirmFn | None = None,
    compaction: CompactFn = compact,
) -> PurgeReport:
    """Delete every face record pam holds, then try to reclaim the space.

    `compaction` is an injected seam on the `clock:`/`confirm:` pattern this project uses
    elsewhere, and it exists for one test the product needs: a VACUUM that FAILS after a
    committed deletion. Monkeypatching sqlite to fail would be testing sqlite; passing a
    function that raises tests what this command does about it, which is the behaviour
    A19 legislates.
    """
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    started = time.monotonic()
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="faces-purge")
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                logger.info("faces purge start: root=%s", root)
                # Spec 7.2, inside the lock and before this command's own work. A purge
                # cannot itself be corrupted by a half-finished rename - it deletes rows
                # keyed by content - but it holds the workspace lock for its whole run,
                # and a command that occupies the one lock the healing machinery needs
                # while leaving a crash unhealed is the argument `pam verify` lost.
                run_recovery(conn, root, logger)
                report = _purge(conn, root, confirm, compaction, logger)
            finally:
                conn.close()
        except BaseException:
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return replace(report, elapsed_s=time.monotonic() - started, log_path=log_path)


def _purge(
    conn: Connection,
    root: Path,
    confirm: ConfirmFn | None,
    compaction: CompactFn,
    logger: logging.Logger,
) -> PurgeReport:
    """Inventory, confirmation, the one transaction, then compaction. In that order."""
    inventory = read_inventory(conn)
    report = PurgeReport(archive_root=root, inventory=inventory)
    # NOTHING TO DELETE MEANS NO CONFIRMATION, and that is what makes compaction's retry
    # safe (A19). A failed VACUUM leaves the user holding a committed deletion and a
    # command to run again; if that second run demanded they type `purge` to delete
    # nothing, the retry would be a ritual about a deletion that already happened.
    if not inventory.empty:
        if confirm is not None and not confirm(inventory, root):
            logger.info("faces purge cancelled at the confirmation")
            return replace(report, cancelled=True)
        report = _delete(conn, report, logger)
    _compact(conn, report, compaction, logger)
    return report


def _delete(conn: Connection, report: PurgeReport, logger: logging.Logger) -> PurgeReport:
    """A19's five deletions, ONE transaction, ONE commit at the end.

    NO `with conn:` AND NO INTERMEDIATE COMMIT. This project has found both: a `with conn:`
    that would have rolled back a write-ahead log, and a `conn.commit()` that split a
    transaction and passed 596 tests. Four deletions committed and the fifth lost is the
    state A19's one-transaction sentence exists to forbid - a catalog with no faces in it
    and a marker fact saying every file has already been looked at.
    """
    faces, persons = repo.purge_faces(conn)
    facts = repo.delete_facts_of_kinds(conn, repo.fact_kinds("faces"))
    runs = repo.delete_enrichment_runs(conn, "faces")
    conn.commit()
    logger.info(
        "faces purge deleted: faces=%d persons=%d facts=%d runs=%d crops=0",
        faces,
        persons,
        facts,
        runs,
    )
    return replace(
        report, deleted=True, faces=faces, persons=persons, facts=facts, runs=runs, crops=0
    )


def _compact(
    conn: Connection, report: PurgeReport, compaction: CompactFn, logger: logging.Logger
) -> None:
    """The physical half, after the commit, reported on its own terms.

    `sqlite3.Error` and `OSError` and nothing wider: a full disk and a locked database are
    the two real failures and both are ordinary, while an unexpected exception here is a
    bug that should reach spec 13's exit 2 rather than be summarised as "compaction
    failed". `freelist_count` is read afterwards either way - it is A19's oracle, and a
    number in the report is a number the reviewer's mutation can move.
    """
    try:
        compaction(conn)
        report.compacted = True
    except (sqlite3.Error, OSError) as error:
        report.compacted = False
        report.compaction_error = str(error) or error.__class__.__name__
        logger.info("faces purge compaction failed: %s", report.compaction_error)
    try:
        report.freelist_after = freelist_count(conn)
    except sqlite3.Error:  # pragma: no cover - a database that cannot answer a pragma
        report.freelist_after = -1


def purge_lines(report: PurgeReport) -> list[str]:
    """The closing block: deletion, compaction, crops, and what purge cannot reach."""
    lines = [f"Face purge in {report.archive_root}", ""]
    if report.deleted:
        lines.append(
            f"Deleted (committed): {report.faces:,} faces, {report.persons:,} people,"
            f" {report.facts:,} enrichment fact(s), {report.runs:,} run record(s)."
        )
    else:
        lines.append(
            "Deleted (committed): nothing - pam holds no faces, no people and no record"
            " of having looked for either in this archive."
        )
    lines.append(f"  {_CROPS_LINE}")
    if report.compacted:
        lines.append(
            "Compaction: succeeded. The space the deleted rows used has been released"
            " back to the filesystem."
        )
    else:
        lines.append(f"Compaction: FAILED - {report.compaction_error}")
        lines.append(
            "  The deletion above IS committed and will not come back. Only the space is"
            " still held. Running pam faces purge again is the safe retry: it will find"
            " nothing to delete, ask you nothing, and try the compaction once more."
        )
    lines.append("")
    lines.append("No photograph was read, written, renamed or deleted.")
    lines.append("")
    lines.extend(_disclosure_lines(report))
    lines.append("")
    lines.append(f"Done in {report.elapsed_s:.1f}s.")
    lines.append("")
    lines.extend(
        step_lines(
            (
                NextStep(
                    "pam enrich faces",
                    "detect faces again from scratch - pam has no record of the old ones",
                ),
            )
        )
    )
    return lines


def _disclosure_lines(report: PurgeReport) -> list[str]:
    """A19's disclosure, by name, AFTER the deletion - plus the measured leak.

    THE PLAN COUNT IS THE MEASUREMENT AND NOT AN ESTIMATE (A19, Task 13 Step 4). It comes
    from the inventory read before the deletion, over `tokens_json`'s `source_table` and
    never over label text, and `tests/test_faces_purge.py` counts the same rows with its
    own SQL after the purge and asserts the same number. A sentence about a leak that
    nobody measured is the shape of reassurance this product does not ship.
    """
    lines = ["What this could NOT reach:"]
    if report.inventory.plan_items:
        lines.append(
            f"  - {report.inventory.plan_items:,} plan item(s) in"
            f" {report.inventory.plans:,} plan(s) whose proposed filename was built from"
            " a person you named. Those filenames still carry the name; purging faces did"
            " not change one of them."
        )
    for item in report.disclosure:
        lines.append(f"  - {item}")
    lines.append(
        "  pam cannot promise the bytes are physically gone: on a modern SSD no program"
        " can. What it can promise is that it no longer holds these records, and that"
        " the list above is complete."
    )
    return lines


def print_purge_summary(report: PurgeReport) -> None:
    """stdout for the report, stderr for the log path (spec 13, UX 2)."""
    for line in purge_lines(report):
        click.echo(line)
    if report.log_path is not None:
        click.echo(f"Log: {report.log_path}", err=True)

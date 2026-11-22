"""`pam report` - the three questions spec 12 makes pam able to answer after the fact.

*Which files did pam not read, and why?* (`skipped`), *which of my files are the same
file twice?* (`duplicates`), and *what happened in that run, and does it still hold?*
(`session`). All three are reports about work already done; none of them does any.

WHY THIS RUNS NO RECOVERY, HOLDS NO LOCK AND OPENS NO SESSION LOG. `pam/status.py`'s
module docstring makes the argument in full and this module makes the same choice for
the same reasons: spec 7.2's recovery WRITES and branches on the disk, so running it
from a read-only report would make the most passive command in the product a mutating
one; and taking the workspace lock would make `pam report` refuse to answer while an
apply was running, which is exactly the moment a user wants to look something up. It
reads each catalog through `connect_readonly` and writes nothing at all. Anything
recovery would have healed is therefore reported as the catalog holds it, honestly -
`pam verify` (spec 7.5) is the command that repairs, and it does run recovery first.

WHAT THIS MODULE MAY NOT CLAIM. pam does not persist a per-file scan error: the
scanner logs the exception and moves on, so the catalog records the CONSEQUENCE (no
fingerprint, no metadata) and not the cause. `skipped` therefore says what the catalog
shows and points at the session log for the original error, rather than inventing a
reason it does not have. Vision principle 9 names reporting a subset as if it were the
whole as the failure to avoid, and a confident wrong reason is a worse version of it.

WHAT LIVES IN `pam/whereabouts.py` AND WHY. `session`'s "does it still hold?" half asks
the DISK, and that module owns the question - the journal row pairs, the four disk states
and their copy. It moved out of here only when this file crossed spec 1's ~300 executable
lines with it in; its docstring carries the reasoning that goes with it.

WHAT LIVES IN `pam/report_duplicates.py`. The `duplicates` subject, whole - its report type,
reader, rendering and printer - moved out at M13 Task 9, before look-alike grouping was added
to it, for the same reason. It imports `PATH_CAP`, `_files` and `_resolve` from here; nothing
here imports it back, so the two cannot become a cycle.
"""

from dataclasses import dataclass, field
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import open_catalog_readonly, read_snapshot
from pam.errors import PamError
from pam.execute.reporter import describe_session
from pam.paths import Workspace, resolve_archive, workspace_if_exists
from pam.whereabouts import Whereabouts, probe, where_lines

# How many paths a group lists before it stops naming them one by one. The COUNT is
# always given in full - the cap is on the listing, never on the number, because a
# truncated count is the lie and a truncated list is only an inconvenience. 20 is about
# what fits on a screen beside its heading.
PATH_CAP = 20

# The six reasons, IN THE ORDER THEY ARE TESTED. First match wins, and the order is
# not alphabetical or arbitrary - it runs from "pam could not have read this" to "pam
# chose not to":
#
#   1. missing      - the file was not on disk at the last scan, so nothing else about
#                     it can be the reason. It outranks every other test because a
#                     placeholder that has since been deleted is a missing file first.
#   2. cloud-only   - a deliberate exclusion (spec 5.2): the bytes are not on this
#                     machine and reading them would pull them down a metered
#                     connection. It outranks "could not read" because pam did not try.
#   3. unreadable   - exiftool WAS asked and answered that it could not identify the
#                     file at all (M7 Task 5). The only reason here that pam can quote
#                     a cause for, which is why it outranks the shrug below it.
#   4. unread       - the residue: an image or a video, present, not a placeholder, and
#                     yet pam has no fingerprint or no metadata for it. This is the one
#                     that means something went wrong, which is why it is tested only
#                     after the two innocent explanations above have been ruled out.
#   5. sidecar      - kept with its photo and never read for its own sake (spec 5.4).
#   6. other        - an extension that is in none of the configured lists.
#
# 5 and 6 sit at the bottom because they are properties of the file's TYPE, which is
# true of it permanently; the four above describe this particular scan.
MISSING = "missing"
CLOUD_ONLY = "cloud-only"
UNREADABLE = "unreadable"
UNREAD = "unread"
SIDECAR = "sidecar"
OTHER = "other"

# The heading and the explanation for each reason, in the order above. The explanation
# is what makes the report honest rather than merely accurate: "pam has no fingerprint
# for it" is a statement about the catalog, and the session log is where the cause is.
REASONS: tuple[tuple[str, str, str], ...] = (
    (
        MISSING,
        "Not there any more",
        "pam has a record of these files, but they were not in the folder when it last looked.",
    ),
    (
        CLOUD_ONLY,
        "Stored in the cloud, not on this computer",
        "pam left these alone on purpose. Reading one would download it."
        " Make them available offline if you want pam to read them.",
    ),
    (
        UNREADABLE,
        "pam opened these and could not tell what they are",
        "There was nothing inside pam could recognise - no photo, no video, no camera"
        " information at all - so the file is probably empty or was cut short while it"
        " was copied. Replace it from a backup or from the camera, then run pam scan"
        " again. Until then pam still proposes a name for it, dated from when the file"
        " was last changed on this computer, which is the least reliable date pam has."
        " pam does not check a photo for damage, so one that still opens is not here.",
    ),
    (
        UNREAD,
        "pam could not read these",
        "These are photos or videos that are on this computer, but pam has no"
        " fingerprint or no camera information for them - so something went wrong"
        " while reading them. The reason for each one is in the log of the scan that"
        " read this folder.",
    ),
    (
        SIDECAR,
        "Kept with a photo, not read on their own",
        "These travel with a photo or video and are renamed alongside it."
        " pam does not read them for camera information.",
    ),
    (
        OTHER,
        "Not a photo or a video, as far as pam is configured",
        "pam does not recognise these file types. If some of them are photos or"
        " videos, add their extensions to pam.toml.",
    ),
)


def reason_for(fact: repo.ReportFact) -> str | None:
    """The one reason this file went unread, or None if pam read it. First match wins.

    The order is the tuple above and the reasoning is there. Kept as a single function
    with one return per branch, rather than as a list of predicates, so that "first
    match wins" is a property of the code's shape and not of a loop somebody can later
    reorder without noticing.
    """
    # `"missing"` spelled out rather than reused from `MISSING` above: that constant is
    # the name of a REASON in this report, and this is a value of `files.status` in the
    # schema. They are equal today and are not the same thing, so a rename of either
    # must not silently follow the other.
    if fact.status == "missing":
        return MISSING
    if fact.placeholder:
        return CLOUD_ONLY
    # M7 Task 5, and it sits HERE for three reasons worth writing down, because the
    # position is the whole design of a first-match-wins chain:
    #
    #   * BELOW the two above. A deleted file is gone first, and a cloud-only file was
    #     not opened this scan - pam did not try, so it cannot have failed. Both can
    #     still be carrying an `Error` from whichever earlier scan DID read them, so
    #     neither ordering is theoretical.
    #   * ABOVE `unread`, which the comment on `REASONS` calls the residue: "pam has no
    #     fingerprint or no camera information, and the reason is in the log". This one
    #     HAS the reason, in exiftool's own words. A specific answer outranks a shrug.
    #     (It also cannot reach `unread` at all: an `Error` record is still a record, so
    #     `has_metadata` is true and the branch below would return None. Before M7 that
    #     is exactly what happened, and the file appeared in no group of this report.)
    #   * ABOVE `sidecar` and `other`, following the rule `REASONS` already states -
    #     those two are permanent properties of the file's TYPE and this describes one
    #     scan. Nothing reaches it that way today, since neither type is ever read; if a
    #     later milestone reads sidecars, "pam could not tell what this is" is the true
    #     reason for a broken one and "kept with a photo" would be a lie.
    if fact.has_read_error:
        return UNREADABLE
    if fact.media_type in ("image", "video"):
        return None if fact.has_hash and fact.has_metadata else UNREAD
    if fact.media_type == "sidecar":
        return SIDECAR
    return OTHER


@dataclass(frozen=True)
class SkippedReport:
    """Every file pam did not read, grouped by reason, plus the total to compare against."""

    archive_root: Path
    total_files: int
    by_reason: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def skipped(self) -> int:
        return sum(len(paths) for paths in self.by_reason.values())


def run_report_skipped(archive: Path | None) -> SkippedReport:
    """Read one archive's catalog and classify every file. Writes nothing."""
    root, ws = _resolve(archive)
    conn = open_catalog_readonly(ws.db_path)
    try:
        # S13: one SELECT is already one snapshot; wrapped anyway, because S13 says EVERY
        # lock-free read, and uniformity is cheaper than an argument per reader.
        with read_snapshot(conn):
            facts = repo.report_facts(conn)
    finally:
        conn.close()
    by_reason: dict[str, list[str]] = {}
    for fact in facts:
        reason = reason_for(fact)
        if reason is not None:
            by_reason.setdefault(reason, []).append(fact.rel_path)
    return SkippedReport(
        archive_root=root,
        total_files=len(facts),
        by_reason={reason: tuple(paths) for reason, paths in by_reason.items()},
    )


def skipped_lines(report: SkippedReport) -> list[str]:
    """The grouped listing. Groups with nothing in them are omitted, not printed as 0.

    Unlike `pam status`'s enrichment block - which prints its zeros because "not done
    yet" and "not applicable" are the distinction the user is deciding on - an empty
    reason here has no such ambiguity: no file has it, and five empty headings would
    bury the one group that does.
    """
    lines = [
        f"Files pam did not read in {report.archive_root}",
        "",
        f"  {_files(report.skipped)}, of {report.total_files:,} pam knows about here.",
        "",
    ]
    if not report.skipped:
        return lines[:3] + [
            "",
            "Next: pam plan   (propose new names - renames nothing)",
        ]
    for reason, heading, explanation in REASONS:
        paths = report.by_reason.get(reason, ())
        if not paths:
            continue
        lines.append(f"{heading} - {_files(len(paths))}")
        lines.append(f"  {explanation}")
        lines.extend(f"    {path}" for path in paths[:PATH_CAP])
        if len(paths) > PATH_CAP:
            lines.append(f"    ... and {len(paths) - PATH_CAP:,} more")
        lines.append("")
    lines.append("Next: pam scan   (read the folder again - anything fixed is picked up)")
    return lines


@dataclass(frozen=True)
class SessionReport:
    """One session three times: as it was written down, as the catalog holds it NOW, and
    as the DISK holds it now.

    The two "now" halves are the ones worth printing. A session report is a snapshot
    taken the moment the run finished; by the time anyone reads it the files may have
    been put back, or a later recovery may have resolved a rename the report called
    unfinished. `catalog_description` is `reporter.describe_session` - THE single
    renderer, shared with `pam undo`'s chooser and `pam status` - so all three say "put
    back" about the same session or none of them do.

    `whereabouts` is the disk half (M7 Task 4, closing the M6 review's condition 6):
    spec 7.0's external-interference states, re-derived from the journal and the folder
    every time somebody asks instead of printed once by recovery and then forgotten.

    NO DEFAULT for it, deliberately, for `SessionCounts.ops`' reason: a caller that could
    leave the disk half out would be constructing a session report whose relationship to
    the folder is unstated, which is the exact silence the field was added to end.
    """

    archive_root: Path
    session_id: str
    counts: repo.SessionCounts
    catalog_description: str
    stored_report_path: Path
    stored_report: str | None
    whereabouts: Whereabouts


def run_report_session(archive: Path | None, session_id: str) -> SessionReport:
    """One session's stored report plus what the catalog says about it now.

    An unknown id is a PamError listing the sessions pam does have (UX 1.4), built from
    `repo.sessions` rather than from counts derived here - the same source `pam undo`'s
    chooser reads, and the reason `_unknown_session_message` in `execute/undo.py` looks
    the way it does.
    """
    root, ws = _resolve(archive)
    conn = open_catalog_readonly(ws.db_path)
    try:
        # S13: three reads, ONE snapshot - a `pam undo` committing between the session's
        # counts and its governing rows would pair "not undone" with the undo's rows.
        with read_snapshot(conn):
            known = repo.sessions(conn)
            match = next((c for c in known if c.session_id == session_id), None)
            if match is None:
                raise PamError(_unknown_session_message(known, session_id, root))
            rows = repo.session_rows(conn, session_id)
            governing = repo.latest_resolved_rows(conn, session_id)
    finally:
        conn.close()
    path = ws.reports_dir / f"session-{session_id}.md"
    return SessionReport(
        archive_root=root,
        session_id=session_id,
        counts=match,
        catalog_description=describe_session(match),
        stored_report_path=path,
        stored_report=_read_stored(path),
        # The disk read happens after the catalog handle is closed, on purpose: it holds
        # no connection open across filesystem calls, and it needs none.
        whereabouts=probe(root, rows, governing),
    )


def _read_stored(path: Path) -> str | None:
    """The written report, or None if it is not there or cannot be read.

    A MISSING REPORT IS NOT AN ERROR. The report is written after the renames, best
    effort, and a run whose report failed to write is precisely a run somebody will
    later come looking for. Failing here would deny them the catalog half as well, which
    is the half that is still true.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _unknown_session_message(
    known: list[repo.SessionCounts], session_id: str, root: Path
) -> str:
    """UX 1.4: a refusal that does not say what to do next is a dead end.

    Deliberately the same shape as `execute/undo.py::_unknown_session_message` - the
    archive named, the sessions listed, one `Run:` line - because a user who mistypes an
    id at `pam undo` and then at `pam report session` should not meet two different
    programs. The last line differs, and only in the command it names.
    """
    if not known:
        return (
            f"pam has not applied anything to this folder yet, so there is no session"
            f" report to show:\n  {root}\nRun: pam plan, then pam apply <plan id>"
        )
    listed = "\n".join(f"  {c.session_id}   ({describe_session(c)})" for c in known)
    return (
        f"pam has no session {session_id} for this folder:\n  {root}\n"
        f"Sessions pam has here:\n{listed}\nRun: pam report session <session id>"
    )


def session_lines(report: SessionReport) -> list[str]:
    """The two current halves first, then the stored report verbatim under its heading.

    THE CURRENT HALVES LEAD because they are the ones that are true today. A user reading
    a six-month-old report needs to know before anything else whether it still describes
    the folder, and a stored report that says "384 renamed" above a catalog that says
    "already undone" would otherwise be read top-down and believed. What pam's RECORDS say
    comes first and what the FOLDER says second, in that order, because the second is a
    disagreement with the first and a disagreement has to follow the thing it is about.
    """
    lines = [
        f"Session {report.session_id} in {report.archive_root}",
        "",
        "What pam's records say about it now:",
        f"  {report.catalog_description}",
        "",
    ]
    lines.extend(where_lines(report.whereabouts, path_cap=PATH_CAP, files=_files))
    if report.stored_report is None:
        lines.extend(_missing_report_lines(report))
    else:
        lines.append(f"The report written at the time ({report.stored_report_path}):")
        lines.append("")
        lines.extend(report.stored_report.splitlines())
    lines.append("")
    lines.append(
        f"Next: pam verify --archive {report.archive_root}"
        "   (check pam's records against the folder)"
    )
    return lines


def _missing_report_lines(report: SessionReport) -> list[str]:
    """Why there is no written report, told truthfully for the session's OWN operation.

    HT-M6 finding H3. `reporter.write_session_report` has exactly ONE call site, in
    `apply.py`, so an undo session's file will never exist - and the single explanation
    this used to give ("can be missing if the run was interrupted") offers a failure as
    the reason for the designed case, to a user who has just watched their files go back
    correctly. UX 1.4 asks an empty state to say what happened and what it means; saying
    something happened that did not is worse than the dead end it was written to avoid.

    Branched on the OPERATION, taken from the session's journal rows through
    `repo.sessions` - never inferred from the counts, which cannot tell an apply from the
    undo that reversed it (that collision is what `SessionCounts.ops` was added for).
    A session holding both ops is catalog corruption rather than an undo, so the
    interrupted explanation is what it gets.
    """
    if report.counts.ops == (repo.OP_UNDO_RENAME,):
        return [
            "There is no written report for this session, and there should not be:",
            "  pam writes one for renaming runs, not for putting files back.",
            "  Everything above comes from pam's records and is up to date.",
        ]
    return [
        "The written report for this session is not in the workspace:",
        f"  {report.stored_report_path}",
        "  It is written at the end of a run and can be missing if the run was interrupted.",
        "  Everything above comes from pam's records and is up to date.",
    ]


# Bare `pam report`: the three subjects, one line each, then what to do. NOT a flag dump
# - that is `pam report --help` - for the same reason bare `pam` is not one (UX 1.4:
# never a dead end). A user who typed `pam report` wants to know what can be reported.
SUBJECTS = (
    ("skipped", "every file pam did not read, and why"),
    ("duplicates", "identical files, and photos that look alike"),
    ("session ID", "what one rename run did, and whether it still holds"),
)


def subjects_lines() -> list[str]:
    lines = ["pam can report on three things:", ""]
    lines.extend(f"  pam report {name}".ljust(28) + why for name, why in SUBJECTS)
    lines.append("")
    lines.append("Next: pam report skipped   (start here - it needs nothing else)")
    return lines


def _resolve(archive: Path | None) -> tuple[Path, Workspace]:
    """The archive and its workspace, or spec 3's refusal. Creates nothing.

    `workspace_if_exists`, never `workspace_for`: a report that made a workspace as a
    side effect would answer "0 files skipped" for a folder pam has never looked at,
    which is the same false all-clear that function's docstring exists to prevent.
    """
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    return root, ws


def _files(count: int) -> str:
    """ "1 file" / "2 files". A report that says "1 files" reads as a draft."""
    return f"{count:,} file" if count == 1 else f"{count:,} files"


def print_skipped(report: SkippedReport) -> None:
    for line in skipped_lines(report):
        click.echo(line)


def print_session(report: SessionReport) -> None:
    for line in session_lines(report):
        click.echo(line)


def print_subjects() -> None:
    for line in subjects_lines():
        click.echo(line)

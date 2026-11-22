"""`pam status` and the bare-`pam` guide - one question, two answers.

The question is *what does pam know about my archives, and what should I do next?*
`pam status` answers it in full (spec 12: "archive summary: counts, enrichment
coverage, plans"), and bare `pam` answers it in three lines (UX 3).

`pam forget` was written here and now lives in `pam/forget.py`. The cut is along WHO
WRITES: forget takes the workspace lock, opens a session log and changes PAM_HOME, and
the paragraph below about why THIS module does none of those things is easier to keep
true when the module that does them all is somewhere else. `pam report` (M6 Task 6) is
the third reader and cites the same paragraph.

ONE ENGINE, TWO VIEWS (M6 plan clarification 6). `collect_status` computes the state
and `next_steps` decides what to do about it; `status_lines` and `guide_lines` only
render what those two return. Bare `pam` computes nothing of its own, because two
implementations of "what state is this archive in" would disagree by M7 - and the guide
is the first thing a new user sees, so the one that drifts is the one nobody notices.

WHY `pam status` LISTS EVERY ARCHIVE INSTEAD OF RESOLVING ONE. Spec 3 rev 8 refuses to
auto-select while any known archive is unreachable, and `pam status` is precisely the
command a user runs to find out which folder is which. So with no `--archive` it reads
`known_archives()` directly rather than `resolve_archive(None)`, and reports the
unreachable ones as known-but-unreachable rather than dropping them - that is the state
`pam forget` exists for, and the two must be discoverable together. An explicit
`--archive` still goes through `resolve_archive`, which refuses a folder pam cannot see.

WHY `pam status` RUNS NO RECOVERY, HOLDS NO LOCK AND OPENS NO SESSION LOG. It is the
one archive-scoped-looking command that is not archive-scoped: with no `--archive` it
reports on N archives at once, and spec 7.2's recovery writes, branches on the disk and
must never be run against an archive pam cannot see (spec 7.0 J9) - which is half of
what this command exists to list. Running it N times, N of them possibly blind, would
make the summary the most mutating read-only command in the product. It would also
have to take N workspace locks, so `pam status` would refuse to answer while an apply
was running: exactly when a user most wants to ask. It therefore reads each catalog
through `connect_readonly` and writes nothing at all, and a state recovery would have
healed is reported honestly rather than repaired behind the user's back - `pam verify`
is the command spec 7.5 gives that job to, and it does run recovery first.

`pam forget` is the opposite case and takes both: it CHANGES PAM_HOME, it concerns
exactly one archive, and it must not race an apply into the workspace it is checking.
That is why it is a separate module.
"""

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import open_catalog_readonly, read_snapshot
from pam.errors import CatalogTooNewError
from pam.execute.reporter import describe_session
from pam.paths import (
    KnownArchive,
    known_archives,
    resolve_archive,
    workspace_if_exists,
)

# `files.status` values printed even when no file is in them. Spec 4's column comments
# name 'present' | 'missing' | 'changed', and pam writes only the first two - but a
# status line that omits `0 missing` reads as "missing is not a thing here" rather than
# as "nothing is missing". Any other value the catalog holds is printed as well, so a
# status this tuple has not heard of is surfaced rather than silently dropped.
FLOOR_STATUSES = ("present", "missing")

# The bare-`pam` banner (UX 3), and the ONE copy of it. It was written inline on the
# populated branch of `guide_lines`, so the first-run state - a brand-new user, with the
# least context anyone using pam ever has - was the one state that did not say nothing
# would be renamed (HT-M6 finding H5). A constant rather than the same literal twice,
# because two copies of one promise is how the two branches would come to word it
# differently. It belongs to the GUIDE and not to `_no_archives_lines`: that helper is
# shared with `pam status`, which is a report rather than a guide and prints no banner
# in any state.
GUIDE_BANNER = "pam - Photo Archive Manager. Nothing is renamed without your approval."


@dataclass(frozen=True)
class NextStep:
    """One line of the closing `Next:` block (UX 1.1: the next step is always printed).

    `text` is usually a command, but not always: an unreachable archive's second step is
    "connect the drive", which is a thing to do rather than a thing to type. Rendering
    prose here beats printing a command that cannot help (UX 1.4, never a dead end).
    """

    text: str
    why: str = ""


@dataclass(frozen=True)
class ArchiveStatus:
    """Everything both views know about one archive. Nothing here is computed twice."""

    root: Path
    reachable: bool
    scanned: bool
    files_by_media_type: dict[str, int] = field(default_factory=dict)
    files_by_status: dict[str, int] = field(default_factory=dict)
    placeholders: int = 0
    last_scanned_at: str | None = None
    enrichment: dict[str, int] = field(default_factory=dict)
    plans_by_status: dict[str, int] = field(default_factory=dict)
    open_plan_ids: tuple[int, ...] = ()
    latest_session: repo.SessionCounts | None = None
    # M14 Y1: the newest open plan's `(approved, total)`, the page's own numbers; None with no
    # open plan (and then no line is printed).
    open_plan_approved: tuple[int, int] | None = None
    # M15 Task 8 S2-F1: E1's sentence when a newer pam wrote this catalog, which was not read.
    refused: str | None = None

    @property
    def total_files(self) -> int:
        return sum(self.files_by_status.values())

    @property
    def undoable_session(self) -> repo.SessionCounts | None:
        """The most recent session if `pam undo` would still put files back, else None.

        THREE CONDITIONS, and the first is the M6 Task 5 ruling. An undo session is not
        undoable at all - `run_undo` refuses one by reading `op` - so a status line
        offering `pam undo` for it would send the user at a command that answers "pam
        cannot redo an undo". Then `done > undone`: a session every item of which is
        back needs nothing, and a PARTIAL undo (some items back, one refused) is still
        undoable, which is why this is a comparison and not `undone == 0`.
        """
        session = self.latest_session
        if session is None or session.ops != (repo.OP_RENAME,):
            return None
        return session if session.done > session.undone else None


@dataclass(frozen=True)
class StatusSummary:
    """Every archive `pam status` was asked about, in the order pam knows them."""

    archives: tuple[ArchiveStatus, ...] = ()

    @property
    def refused(self) -> bool:
        """Any folder whose catalog a newer pam wrote (E1): both views then exit 1."""
        return any(archive.refused is not None for archive in self.archives)


def collect_status(archive: Path | None = None) -> StatusSummary:
    """Read the state of one archive, or of every archive pam knows. Writes nothing.

    M15 Task 8 S2-F1 (the controller's ruling): with no `--archive`, a folder whose catalog E1
    refuses is reported by its path and E1's sentence, and every other folder as normal - "every
    reader refuses" is about not reading the newer catalog, not about hiding healthy ones. An
    explicit `--archive` keeps E1's plain refusal.
    """
    if archive is not None:
        return StatusSummary(
            archives=(_read_archive(KnownArchive(resolve_archive(archive), reachable=True)),)
        )
    return StatusSummary(archives=tuple(_read_or_refuse(a) for a in known_archives()))


def _read_or_refuse(known: KnownArchive) -> ArchiveStatus:
    try:
        return _read_archive(known)
    except CatalogTooNewError as newer:
        return ArchiveStatus(
            root=known.path, reachable=known.reachable, scanned=False, refused=str(newer)
        )


def _read_archive(known: KnownArchive) -> ArchiveStatus:
    """One archive's catalog, read through a connection that cannot write.

    An UNREACHABLE archive is read too, and that is deliberate: its workspace is on this
    machine even when its photos are not, so pam can still say how many files it has and
    when it last saw them. Reporting an unreachable archive as an empty one would tell
    the user pam had forgotten it, which is the claim spec 3 rev 8 exists to prevent.
    """
    ws = workspace_if_exists(known.path)
    if ws is None:
        return ArchiveStatus(root=known.path, reachable=known.reachable, scanned=False)
    conn = open_catalog_readonly(ws.db_path)
    try:
        # S13: eight reads, ONE snapshot - a `pam plan` committing between the plan counts and
        # the open plan ids once printed a state the catalog never held (M12 Q5). M14 Y1's
        # ninth, `_approved`, is in it too, and after the eight, which keep their order.
        with read_snapshot(conn):
            sessions = repo.sessions(conn)
            archive = ArchiveStatus(
                root=known.path,
                reachable=known.reachable,
                scanned=True,
                files_by_media_type=repo.media_type_counts(conn),
                files_by_status=repo.status_counts(conn),
                placeholders=repo.placeholder_count(conn),
                last_scanned_at=repo.last_scanned_at(conn),
                enrichment=repo.enrichment_coverage(conn),
                plans_by_status=repo.plan_status_counts(conn),
                open_plan_ids=tuple(repo.plans_in_status(conn, "open")),
                latest_session=sessions[-1] if sessions else None,
            )
            # M14 Y1: still inside the snapshot, so the count is the open plan's as listed above.
            if not archive.open_plan_ids:
                return archive
            approved = _approved(conn, archive.open_plan_ids[-1])
            return replace(archive, open_plan_approved=approved)
    finally:
        conn.close()


def _approved(conn: sqlite3.Connection, plan_id: int) -> tuple[int, int]:
    """`(approved, total)` for one plan - the K and M the review page's progress line shows.

    M14 amendment AM1: the PROPERTY is "the same numbers", not "the same function". One
    `GROUP BY` over `plan_items` (`repo.plan_item_status_counts`) - the page's `counts` reads
    the whole plan's rows through `repo.plan_rows` and measured 4.67 s / 383 MB at 100,000
    items to print two integers. The two can differ only where an item's `files` row is gone,
    which spec 7.5 check 2 reports. `total` is the sum over every status, so a status this
    function had not heard of is still counted, as the page's `len(items)` counts it.
    """
    by_status = repo.plan_item_status_counts(conn, plan_id)
    return by_status.get("approved", 0), sum(by_status.values())


# Design 12 rev 27 (P3): once `pam enrich faces` has covered any file, the closing block
# names the command that turns those faces into groups a person can name. Spelled here
# once; both views (status and the bare-`pam` guide) render it through `next_steps`.
FACES_CLUSTER_STEP = NextStep(
    "pam faces cluster", "group the faces pam found, then name them with pam faces label"
)


def next_steps(archive: ArchiveStatus) -> tuple[NextStep, ...]:
    """The sensible next commands for one archive (UX 3): the state machine, then P3's line.

    P3 (design 12 rev 27): when the faces coverage `pam status` already computes is
    non-zero, a scanned archive pam can see gains one `or:` line naming `pam faces
    cluster`. Read off that coverage and nothing else - whether clustering has already
    run would need a query in `repo/faces.py`, which I13 forbids - and no line at all on
    an archive where faces never ran.
    """
    steps = _state_steps(archive)
    if archive.reachable and archive.scanned and archive.enrichment.get("faces", 0) > 0:
        return (*steps, FACES_CLUSTER_STEP)
    return steps


def _state_steps(archive: ArchiveStatus) -> tuple[NextStep, ...]:
    """The state machine. FIRST MATCH WINS, and the order is the order in which a problem
    blocks the one below it: a folder pam cannot see cannot be scanned, an unscanned
    folder cannot be planned, an open plan is the thing to apply before making another,
    and a session that can still be put back is the thing to decide about before
    planning again.
    """
    if not archive.reachable:
        return (
            NextStep(f"pam forget {archive.root}", "if this folder is gone for good"),
            NextStep("connect the drive or network folder, then run pam status again"),
        )
    if not archive.scanned:
        return (NextStep(f"pam scan {archive.root}", "read this folder into the catalog"),)
    if archive.open_plan_ids:
        # UX 3's chain gained its middle link at M7: plan -> review -> apply. `pam
        # review` leads because looking at the names is the step before approving them,
        # and `pam apply` stays because HT-M6 verified the guide moves the user to it -
        # a user who already trusts the plan is not made to open a browser first.
        plan_id = archive.open_plan_ids[-1]
        return (
            NextStep(f"pam review {plan_id}", "see & approve the plan"),
            NextStep(f"pam apply {plan_id}", "renames only what you approve"),
        )
    undoable = archive.undoable_session
    if undoable is not None:
        return (
            NextStep(
                f"pam undo {undoable.session_id}", "put those files back under their old names"
            ),
            NextStep("pam scan", "pick up anything you have added since"),
        )
    # The same state `pam scan`'s closing block leaves the user in - scanned, nothing
    # planned, nothing to put back - so it carries the same pair. UX §3's chain has one
    # `or:` here and it is `pam enrich`; withholding it from this surface while `pam
    # scan` prints it would have the two commands describing one state differently,
    # which is exactly what rev 9 made the format normative to stop (M9 Task 12).
    return (
        NextStep("pam plan", "propose new names - renames nothing"),
        NextStep("pam enrich", "add AI understanding first"),
    )


def _local(timestamp: str | None) -> str:
    """A catalog timestamp as UX 2's local time, or the raw text if it will not parse.

    Every `now=` pam writes is `datetime.now().isoformat()`, which is already local -
    there is no conversion to do, only a shorter rendering. A value that does not parse
    is printed as it stands rather than swallowed: a summary is the wrong place to hide
    something unexpected in the catalog.
    """
    if timestamp is None:
        return "never"
    try:
        return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return timestamp


def _counted(counts: dict[str, int], floor: tuple[str, ...] = ()) -> str:
    """ "2 image, 1 video" - humanized numbers (UX 2), floor keys shown even at zero."""
    keys = list(floor) + sorted(k for k in counts if k not in floor)
    return ", ".join(f"{counts.get(key, 0):,} {key}" for key in keys) or "none"


def _files(count: int) -> str:
    """ "1 file" / "2 files". A summary that says "1 files" reads as a draft."""
    return f"{count:,} file" if count == 1 else f"{count:,} files"


def _archive_lines(archive: ArchiveStatus) -> list[str]:
    """One archive's block: the path first, then what pam knows, then what to do."""
    if archive.refused is not None:
        return _refused_lines(archive)
    lines = [str(archive.root)]
    if not archive.reachable:
        lines.append("  ! pam has scanned this folder, but cannot see it right now.")
    if not archive.scanned:
        lines.append("  pam has not read this folder yet.")
        return lines + step_lines(next_steps(archive))
    total = archive.total_files
    lines.append(f"  {_files(total)}: {_counted(archive.files_by_media_type)}")
    lines.append(
        f"  {_counted(archive.files_by_status, FLOOR_STATUSES)},"
        f" {archive.placeholders:,} cloud-only"
    )
    lines.append(f"  Last scanned {_local(archive.last_scanned_at)}")
    # Every enricher, including the zeros. Spec 12 asks for enrichment coverage and M9
    # is what will produce any; an absent row would read as "not applicable here" when
    # the truth is "not done yet", which is the distinction the user is deciding on.
    lines.append(f"  Enrichment coverage, of {_files(total)}:")
    lines.append("    " + "   ".join(f"{name} {n:,}" for name, n in archive.enrichment.items()))
    lines.append(f"  Plans: {_counted(archive.plans_by_status)}")
    lines.extend(_approved_lines(archive))
    lines.extend(_session_lines(archive))
    return lines + step_lines(next_steps(archive))


def _refused_lines(archive: ArchiveStatus) -> list[str]:
    """S2-F1: the folder's path on its own line, then E1's sentence unchanged. No next step:
    the sentence says what to do."""
    return [str(archive.root), f"  {archive.refused}"]


def _approved_lines(archive: ArchiveStatus) -> list[str]:
    """M14 Y1: `Plan 3: 12 of 95 names approved.` - the page's own progress words and numbers,
    so a reader can hold the two side by side. The words are the implementer's, pending the
    design model. No open plan, no line."""
    if archive.open_plan_approved is None:
        return []
    approved, total = archive.open_plan_approved
    plan_id = archive.open_plan_ids[-1]
    return [f"  Plan {plan_id}: {approved:,} of {total:,} names approved."]


def _session_lines(archive: ArchiveStatus) -> list[str]:
    """Spec 12's "the most recent session and whether it can still be undone".

    The description comes from `reporter.describe_session`, the same function `pam
    undo`'s chooser renders - so an undo session says "put back" in both places, and
    neither can start calling it a rename without the other doing so too.
    """
    session = archive.latest_session
    if session is None:
        return ["  No renames have been applied here yet."]
    ending = (
        " - it can still be undone."
        if archive.undoable_session is not None
        else " - there is nothing here left to undo."
    )
    return [f"  Most recent session {session.session_id}: {describe_session(session)}{ending}"]


def step_lines(steps: tuple[NextStep, ...]) -> list[str]:
    """UX 2/3's closing block: `Next:` then `or:`, each with its reason in brackets.

    THE renderer, not one of several (M8 amendment B2). The M8 review measured three
    different treatments of the closing block across four modules; this one is the only
    structured one and won, so it is public and `pam scan` and `pam plan` render through
    it rather than formatting their own. A second implementation of this three-line
    function would only be able to disagree with it.
    """
    lines = []
    for index, step in enumerate(steps):
        label = "Next:" if index == 0 else "  or:"
        reason = f"   ({step.why})" if step.why else ""
        lines.append(f"{label} {step.text}{reason}")
    return lines


def status_lines(summary: StatusSummary) -> list[str]:
    """The full report (spec 12), one block per archive, blank line between blocks."""
    if not summary.archives:
        return _no_archives_lines()
    lines: list[str] = []
    if len(summary.archives) > 1:
        lines.append(f"pam knows about {len(summary.archives):,} photo folders.")
        lines.append("")
    for index, archive in enumerate(summary.archives):
        if index:
            lines.append("")
        lines.extend(_archive_lines(archive))
    return lines


def guide_lines(summary: StatusSummary) -> list[str]:
    """Bare `pam` (UX 3): the state in one line per archive, then what to do about it.

    Deliberately NOT a flag dump - that is `pam --help`, which the last line names. It
    computes nothing: every fact below came out of `collect_status`, and the steps out
    of the same `next_steps` the full report renders.

    THE BANNER IS ON BOTH BRANCHES (HT-M6 finding H5). It used to be on this function's
    populated path alone, so the first-run state - the one state where the reader has
    never run pam at all - omitted the single line that says nothing is renamed without
    their approval. It is prepended HERE rather than inside `_no_archives_lines`, which
    `pam status` also renders: the sharing is about the state description, so that the
    two views cannot describe the state differently, and `pam status` is a report that
    carries no banner in any state.
    """
    if not summary.archives:
        return [GUIDE_BANNER, "", *_no_archives_lines()]
    lines = [GUIDE_BANNER, ""]
    for archive in summary.archives:
        if archive.refused is not None:
            lines.extend([*_refused_lines(archive), ""])
            continue
        lines.append(_guide_headline(archive))
        lines.extend(step_lines(next_steps(archive)))
        lines.append("")
    lines.append("Run pam status for the full picture, or pam --help for every command.")
    return lines


def _guide_headline(archive: ArchiveStatus) -> str:
    if not archive.reachable:
        return f"{archive.root}  ! pam cannot see this folder right now"
    if not archive.scanned:
        return f"{archive.root}  (not read yet)"
    return (
        f"{archive.root}  {_files(archive.total_files)},"
        f" last scanned {_local(archive.last_scanned_at)}"
    )


def _no_archives_lines() -> list[str]:
    """The first-run state (UX 3), shared by both views so they cannot say it differently."""
    return [
        "pam has not scanned any photo folder yet.",
        "",
        *step_lines(
            (NextStep("pam scan <your photo folder>", "reads and records, renames nothing"),)
        ),
    ]


def print_status(summary: StatusSummary) -> None:
    for line in status_lines(summary):
        click.echo(line)


def print_guide(summary: StatusSummary) -> None:
    for line in guide_lines(summary):
        click.echo(line)

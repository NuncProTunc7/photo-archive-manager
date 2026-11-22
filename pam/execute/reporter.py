"""The session report and every line UX 4 makes normative (spec 7.1 rev 7's decomposition).

COPY IS NORMATIVE IN THIS MODULE in a way it is nowhere else in pam. UX 4 calls
`pam apply` "the one dangerous moment", and each line of its block was chosen: the
archive path is second because every count below it is meaningless if the folder is
wrong (UX rev 4, finding F4); the deletion promise is stated in the negative because
that is the fear people actually have; the undo promise is INDEFINITE because the
journal has no expiry and pam must never state a limitation it does not have. Change a
line here and `tests/test_reporter.py` fails whole-line, on purpose.

WHY THE STRINGS ARE ASCII. Every user-facing string pam prints is ASCII, matching the
rest of the package (the only non-ASCII in `pam/` is `naming/sanitize.py`'s
transliteration table, which is data). UX 4 renders its dashes as em-dashes; the M5
plan renders the same block with hyphens, and hyphens are what ships - a Windows
console under a non-UTF-8 code page is not a place to discover an encoding error, and
this is the screen that must never fail to print.

WHY ELAPSED IS ONE DECIMAL EVERYWHERE, AGAINST UX 4's EXAMPLE COPY. UX 4 renders the
closing line as `in 24s`, and the four console sites below rendered `:.0f` until M7 Task
2 - so any run under half a second printed `in 0s`, which is the ORDINARY case on a small
archive and is visually indistinguishable from the `took 0.0s` bug M6 had just fixed. The
written report already used `:.1f`, so one session's one number was printed two ways.
One decimal is now the project-wide elapsed format: numbers over adjectives, and the
console must not disagree with the report about the same number (HT-M6 finding H6).

WHY IT OWNS `announce_interrupted`. It was written twice, verbatim, in `apply.py` and
`undo.py` (found at M5 Task 9, fixed here). It carries spec 13's unconditional log-path
promise for a run that never reached its summary - the case where the user most needs
the session id, because it is the argument to `pam undo`. Two copies of one user-facing
contract is the drift spec 7.1 rev 7's decomposition exists to prevent.

This module writes no catalog rows and moves no files. It renders, and it writes one
markdown file under the workspace's own reports directory.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from pam.catalog import repo

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    # `apply.py` and `undo.py` import this module at runtime, so it must not import
    # them back. Their summaries are needed only as type annotations, which is exactly
    # what TYPE_CHECKING is for: mypy sees the real types, the interpreter sees none.
    from pam.execute.apply import ApplySummary
    from pam.execute.recovery_report import InterruptedRun
    from pam.execute.undo import UndoSummary
    from pam.paths import Workspace

# The word, exactly. Case-sensitive and unstripped: "anything other than exactly
# `apply` cancels" is the ritual, and a confirmation that accepts `y` is one a user
# completes without reading. This is the only moment pam asks permission before
# renaming files it cannot replace.
CONFIRM_WORD = "apply"
CONFIRM_PROMPT = 'Type "apply" to proceed, anything else to cancel: '

# F1 (HT-M12, ruled 2026-10-08), R4/R5 and H1: the lines that say what apply will NOT
# rename. Spelled from the ruling; each appears only when its count is above zero.
# confirmation_lines, after the "renames approved" line, only when > 0:
NOT_APPROVED_LINE = "  {count:,} not approved - they will not be renamed."
# confirmation_lines, only when --all left Low items:
LOW_LEFT_LINE = (
    "  ! {count:,} low-confidence names were not accepted - approve them one at a time"
    " in pam review {plan_id}."
)
# apply_summary_lines, after the headline, only when not_approved > 0:
LEFT_LINE = (
    "  {approved:,} approved, {left:,} not approved and left as they are."
    " To name them later: pam plan"
)

# AM2 (M15 Task 2, adopted 2026-11-16), with N = FILES. Said by the console, Done, History and
# the session report, only when N > 0 - AM2's I10 exception, one count line. C6.5 (owner,
# 2026-11-16): singular for one file; and when a skipped unit has more than one member (files >
# units), TOGETHER follows, because N then counts files the person did not approve one by one.
CLOUD_ONLY_LINE = (
    "{count:,} files became cloud-only after they were approved and were not renamed."
    " Make them available offline and read the folder again."
)
CLOUD_ONLY_LINE_ONE = (
    "1 file became cloud-only after it was approved and was not renamed."
    " Make it available offline and read the folder again."
)
CLOUD_ONLY_TOGETHER = " A photo and the files that go with it are skipped together."
# The session report's data line: both counts, files and units (the controller's ruling). The
# review page's History reads the FILES back from it (`webui/confirm_api.py`), because a skipped
# unit has no journal row - so this wording is a format, pinned whole by the tests.
CLOUD_ONLY_REPORT_LINE = (
    "- {files:,} file(s) in {units:,} group(s) not renamed because they became cloud-only"
)

# E6 (M15 Task 7): every report pam writes from now on states its format in its header and ends
# with END_OF_REPORT, so a report cut short by a crash can be told from a finished one. A report
# WITHOUT the field was written by an older pam: it is final, never called truncated and never
# rewritten (the M14 writer had no end line, so the end line alone would condemn all of them).
REPORT_FORMAT = 2
REPORT_FORMAT_FIELD = "- Report format: "
END_OF_REPORT = "-- end of report --"

# UX 4's progress cadence. Never fired on a human test's library (a few hundred files); it is
# for the 98,210-file case the UX spec is written against.
PROGRESS_EVERY = 1000


@dataclass(frozen=True)
class Confirmation:
    """Everything UX 4's block states, gathered before anything at all is written.

    Built by `apply.py` from the preflight, so the numbers describe the plan pam is
    about to run rather than the plan as it was written - and `archive_root` is the
    path `resolve_archive` returned, carried through unchanged.
    """

    plan_id: int
    archive_root: Path
    approved: int
    proposed: int
    skipped_by_you: int
    low_confidence: int
    sync_provider: str | None
    # Frozen into `plans` at plan time by migration 0005. It cannot be derived here:
    # these files get no `plan_items` row, and "present files with no plan item"
    # would also catch placeholders and files that errored.
    already_conforming: int
    # F1 R5: the items left UNDECIDED, which this run will not rename. Defaulted so a
    # Confirmation built by hand before M13 still constructs.
    not_approved: int = 0
    # F1 R2/R4: how many Low-tier items `--all` left undecided (0 without `--all`).
    excluded_low: int = 0
    # M14 Task 6 (U14): `apply.actionable_digest` of the items this run will rename - what the
    # review page's confirmation nonce was issued for, recomputed here, inside the preflight.
    # The console prints none of it; defaulted so a Confirmation built by hand still constructs.
    digest: str = ""


def confirmation_lines(confirmation: Confirmation) -> list[str]:
    """UX 4's block, as lines. Separated from printing so it can be asserted whole.

    `approved` counts approved items only (F1 R5) - the typed `apply` confirms them and
    approves nothing more. "proposed" in the parenthetical still means "names pam
    proposed", as it always has; the items left undecided get their own line below it.

    "N already conforming" is read from the plan, not recomputed: those files get no
    `plan_items` row, so migration 0005 freezes the count at plan time or it is lost.
    It shipped MISSING from this line for one commit, which is the deviation the M5
    Task 10 review flagged rather than papered over - printing 0 would have been false.
    """
    lines = [
        f"Plan {confirmation.plan_id} - ready to apply",
        f"  {confirmation.archive_root}",
        f"  {confirmation.approved:,} renames approved"
        f"   ({confirmation.proposed:,} proposed,"
        f" {confirmation.skipped_by_you:,} skipped by you,"
        f" {confirmation.already_conforming:,} already conforming)",
    ]
    if confirmation.not_approved:
        lines.append(NOT_APPROVED_LINE.format(count=confirmation.not_approved))
    lines += [
        "  0 files will be deleted, moved, or modified - renames only, in place.",
        "  Every change is journaled. Undo restores exact original names.",
        "",
    ]
    if confirmation.excluded_low:
        lines.append(
            LOW_LEFT_LINE.format(count=confirmation.excluded_low, plan_id=confirmation.plan_id)
        )
    if confirmation.low_confidence:
        lines.append(
            f"  ! {confirmation.low_confidence:,} of these are low confidence"
            " - pam is unsure about the name."
        )
    if confirmation.sync_provider:
        lines.append(
            f"  ! {confirmation.sync_provider} detected: consider pausing sync while applying."
        )
    return lines


def confirm_apply(confirmation: Confirmation) -> bool:
    """Print UX 4's block and read the one word. True only for exactly `apply`.

    `input()` rather than `click.prompt` so the seam a test can hold is a stdlib
    builtin rather than a click internal - and so EOF is visible here rather than
    handled by a library. **A closed stdin CANCELS**: `pam apply < /dev/null` must not
    rename an archive, and a script that meant to apply can say so.

    The branching is unit-tested; the CONSOLE behaviour is not and cannot be. pytest
    has no console, so this function's real behaviour is HT-M5's to confirm (CLAUDE.md:
    console-only bugs pass green under automation - that is how the Windows `os.kill`
    bug survived a full suite).
    """
    for line in confirmation_lines(confirmation):
        click.echo(line)
    click.echo()
    click.echo(CONFIRM_PROMPT, nl=False)
    try:
        typed = input()
    except EOFError:
        click.echo()
        return False
    return typed == CONFIRM_WORD


def progress_line(before: int, after: int) -> str | None:
    """UX 4's per-1000 line, or None when this batch crossed no thousand.

    A pure function of two counts, so the cadence can be tested without renaming ten
    thousand files: an archive big enough to cross the boundary would cost the suite
    minutes to assert one string.
    """
    if after // PROGRESS_EVERY == before // PROGRESS_EVERY:
        return None
    reached = (after // PROGRESS_EVERY) * PROGRESS_EVERY
    return f"  {reached:,} renamed so far ..."


def announce_interrupted(session_id: str, log_path: Path | None) -> None:
    """spec 13's log path for a run that never reached its summary. ONE implementation.

    NOT a durability mechanism - a `finally` is exactly what a power cut skips. The
    session id is durable in the journal rows themselves and the log path was logged at
    the start of the run; this is so the user does not have to go looking for either
    after pam has just been interrupted mid-rename. `pam undo` takes the session id,
    which is why it is printed even when the run failed.
    """
    if session_id:
        click.echo(f"Session: {session_id}", err=True)
    if log_path is not None:
        click.echo(f"Log: {log_path}", err=True)


def describe_session(counts: repo.SessionCounts) -> str:
    """One session in the words a person needs. THE single implementation (M6 Task 5).

    Two callers ask the same question in two places - `pam undo`'s "which session did
    you mean?" chooser and `pam status`'s "the most recent session and whether it can
    still be undone" - and two renderings of one fact would answer them differently by
    the next milestone. This module already owns every other line those two commands
    share, so it owns this one.

    THE VERB COMES FROM `ops`, NEVER FROM THE COUNTS. Before that field existed, an
    undo session and the apply it reversed were byte-identical here (both `done=2,
    undone=2`), so both rendered as "2 renamed, already undone" - and for the undo that
    is false twice over: those files were put BACK, and an undo is not something a
    later undo can reverse. `run_undo` refuses one outright (`REDO_REFUSAL`) by reading
    `op`, so a chooser that offered it was contradicting the command it feeds.

    Every clause after the first is CONDITIONAL, so an ordinary session reads as one
    short fact rather than as four zeroes the user has to discount. The unresolved
    clause names the fix, because spec 7.2's recovery runs at the start of every
    archive-scoped command - so "run any pam command" is literally what clears it.
    """
    if counts.ops == (repo.OP_UNDO_RENAME,):
        parts = [f"{counts.done:,} put back"]
        if counts.failed:
            parts.append(f"{counts.failed:,} could not be put back")
        if counts.reverted:
            parts.append(f"{counts.reverted:,} re-applied mid-run")
        if counts.intent:
            parts.append(f"{counts.intent:,} unresolved - run any pam command")
        parts.append("an undo cannot itself be undone")
        return ", ".join(parts)
    parts = [f"{counts.done:,} renamed"]
    if counts.failed:
        parts.append(f"{counts.failed:,} failed")
    if counts.reverted:
        parts.append(f"{counts.reverted:,} put back mid-run")
    if counts.done and counts.undone >= counts.done:
        parts.append("already undone")
    elif counts.undone:
        parts.append(f"{counts.undone:,} already undone")
    if counts.intent:
        parts.append(f"{counts.intent:,} unresolved - run any pam command")
    return ", ".join(parts)


def cloud_only_lines(summary: "ApplySummary") -> tuple[str, ...]:
    """AM2's sentence for this run, or nothing when no unit was skipped (N > 0 only).

    ONE rendering for the console's closing block and the page's Done (`done_lines`, which
    `webui/rename_jobs.py` serves as the finished job's lines), so the two cannot word one count
    twice.
    """
    files = summary.cloud_only_files
    if not files:
        return ()
    line = CLOUD_ONLY_LINE_ONE if files == 1 else CLOUD_ONLY_LINE.format(count=files)
    return (line + (CLOUD_ONLY_TOGETHER if files > summary.cloud_only_units else ""),)


def done_lines(summary: "ApplySummary") -> tuple[str, ...]:
    """The page's Done lines for a finished rename: each failure's reason, then AM2's sentence."""
    return (*(reason for _, reason in summary.failures), *cloud_only_lines(summary))


def apply_summary_lines(summary: "ApplySummary") -> list[str]:
    """UX 4's closing block. Failures are in the HEADLINE, never buried below it."""
    if summary.failed:
        headline = (
            f"Done with problems. {summary.renamed:,} renamed,"
            f" {summary.failed:,} failed (details in report),"
            f" in {summary.elapsed_s:.1f}s."
        )
    else:
        headline = (
            f"Done. {summary.renamed:,} renamed, {summary.failed:,} failed,"
            f" in {summary.elapsed_s:.1f}s."
        )
    lines = [headline]
    if summary.not_approved:
        lines.append(LEFT_LINE.format(approved=summary.approved, left=summary.not_approved))
    lines.extend(f"  ! {line}" for line in cloud_only_lines(summary))
    lines += [
        # Reassurance is deliberate (UX 4): people worry about mistakes right after
        # applying. The promise is INDEFINITE - the journal has no expiry, and pam
        # never states a limitation it does not have.
        "  Every rename was journaled. Undo is available anytime - it does not",
        "  expire - as long as the files aren't renamed outside pam.",
    ]
    lines.extend(_attention_lines(summary.stranded))
    if summary.report_path is not None:
        lines.append(f"  Report: {summary.report_path}")
    if summary.session_id:
        lines.append(f"  Undo:   pam undo {summary.session_id}")
    return lines


def _attention_lines(stranded: list[str]) -> list[str]:
    """The one outcome that needs a human rather than a re-run (spec 7.3, spec 7.0).

    Named exactly and NEVER folded into the failure count: nothing was lost, the file
    is in the same folder under its new name while its group is not, and a count would
    tell the user something is wrong without telling them which of their photos it is.
    """
    if not stranded:
        return []
    lines = ["  ! Needs your attention - these files are under their new names"]
    lines.extend(f"      {rel_path}" for rel_path in stranded)
    return lines


def print_apply_summary(summary: "ApplySummary") -> None:
    """The closing block on stdout, then spec 13's log path on stderr."""
    for line in apply_summary_lines(summary):
        click.echo(line)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)


def undo_summary_lines(summary: "UndoSummary") -> list[str]:
    """`pam undo`'s closing block. UX 4 does not specify it; this mirrors apply's shape
    so the two commands do not read like different programs."""
    if summary.failed:
        headline = (
            f"Done with problems. {summary.restored:,} put back,"
            f" {summary.failed:,} failed, in {summary.elapsed_s:.1f}s."
        )
    else:
        headline = (
            f"Done. {summary.restored:,} put back, {summary.failed:,} failed,"
            f" in {summary.elapsed_s:.1f}s."
        )
    lines = [headline]
    if summary.already_undone:
        lines.append(
            f"  {len(summary.already_undone):,} file(s) were already back under their"
            " original names."
        )
    lines.extend(f"  ! {rel_path}: {reason}" for rel_path, reason in summary.failures)
    lines.extend(_attention_lines(summary.stranded))
    if summary.plan_status is not None:
        lines.append(f"  The plan is now {summary.plan_status}.")
    return lines


def print_undo_summary(summary: "UndoSummary") -> None:
    for line in undo_summary_lines(summary):
        click.echo(line)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)


def _count_lines(summary: "ApplySummary") -> list[str]:
    """The report's counts under "What happened". Each line after the first only when it says
    something: F1 R5's not-approved line only when there is any (a zero here is a fact nobody
    needs); AM2's line with both counts only when a unit was skipped; and C6.8(b) (owner,
    2026-11-16): no `- 0 file(s) not renamed` over AM2's line, which explains that count."""
    lines = [f"- {summary.renamed:,} file(s) renamed"]
    if summary.failed or not summary.cloud_only_files:
        lines.append(f"- {summary.failed:,} file(s) not renamed")
    if summary.not_approved:
        lines.append(f"- {summary.not_approved:,} file(s) not approved, left as they were")
    if summary.cloud_only_files:
        files, units = summary.cloud_only_files, summary.cloud_only_units
        lines.append(CLOUD_ONLY_REPORT_LINE.format(files=files, units=units))
    return lines


def write_session_report(
    reports_dir: Path, summary: "ApplySummary", logger: logging.Logger
) -> Path:
    """The session report: what this run did, in words that need no knowledge of pam.

    THE READER IS A STRANGER. This is the artifact a user forwards when something went
    wrong, so it is written for someone who has never read this codebase and may not be
    the person who ran the command. UX 8's rule holds harder here than on the screen -
    no internal vocabulary at all - and the M6 Task 6 readability pass is what turned
    the M5 minimum into this. Every heading is a sentence about the user's photos.

    EXTENDED, NEVER REPLACED (m5.md 2.7). Counts, every failure with its path and its
    reason, files a failed revert left stranded, what spec 7.2's recovery flagged and
    the undo command were all in the M5 version and are all still here. What M6 adds is
    the three facts a report cannot be read without months later: WHICH folder, WHEN,
    and which plan - none of which can be recovered from the file afterwards, because
    the workspace directory holding it is named by a one-way hash of the archive path
    (spec 3).

    Still deliberately a flat markdown file written in one pass. A report nobody can
    read because its generator crashed is worse than a plain one, and a report that
    cannot be written must never take the run down with it: the renames already happened
    and the journal already records them, so the failure is logged and the summary
    simply carries no report path.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = session_report_path(reports_dir, summary.session_id)
    lines = [
        f"# Renaming run {summary.session_id}",
        "",
        f"{REPORT_FORMAT_FIELD}{REPORT_FORMAT}",
        f"- Photo folder: {summary.archive_root}",
        f"- Started: {_local(summary.started_at)}",
        f"- Proposed names from: plan {summary.plan_id:,}",
        "",
        "## What happened",
        "",
        *_count_lines(summary),
        f"- took {summary.elapsed_s:.1f}s",
        "",
        *[f"{line}\n" for line in cloud_only_lines(summary)],
        "No file was deleted, moved out of the folder, or changed inside. pam renames"
        " in place and nothing else.",
        "",
    ]
    if summary.failures:
        lines += [
            "## Files pam did not rename",
            "",
            "These kept the names they already had. Nothing about them was changed.",
            "",
        ]
        lines += [f"- `{rel_path}` - {reason}" for rel_path, reason in summary.failures]
        lines += [""]
    if summary.stranded:
        lines += [
            "## Files needing your attention",
            "",
            "These are under their new names while the group they belong to is not."
            " Nothing was lost.",
            "",
        ]
        lines += [f"- `{rel_path}`" for rel_path in summary.stranded]
        lines += [""]
    if summary.recovery_notes:
        lines += [
            "## Left over from an interrupted run, needing your attention",
            "",
        ]
        lines += [f"- {note}" for note in summary.recovery_notes]
        lines += [""]
    lines += [
        "## Undo",
        "",
        "This puts every file above back under the name it had before. It does not expire.",
        "",
        f"    pam undo {summary.session_id}",
        "",
        END_OF_REPORT,
        "",
    ]
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logger.error("could not write the session report to %s: %s", path, exc)
        raise
    return path


def session_report_path(reports_dir: Path, session_id: str) -> Path:
    """Where a session's report lives: the ONE spelling the two writers share."""
    return reports_dir / f"session-{session_id}.md"


def write_recovery_report(ws: "Workspace", session: str, summary: "InterruptedRun") -> Path:
    """E6: the report of a renaming run that was interrupted, written by spec 7.2's recovery.

    The sibling of `write_session_report`, never a caller of it: an interrupted run has no
    `ApplySummary`, and inventing one would put numbers in the report that no run counted.
    `summary` is what the journal's final rows say (`recovery_report.interrupted_run`). Same
    path, same header shape, same end line, so `pam report session` and History read it as
    they read any other. Raises OSError; recovery logs it and carries on.
    """
    ws.reports_dir.mkdir(parents=True, exist_ok=True)
    path = session_report_path(ws.reports_dir, session)
    lines = [
        summary.title(session),
        "",
        f"{REPORT_FORMAT_FIELD}{REPORT_FORMAT}",
        f"- Photo folder: {ws.archive_root}",
        *summary.body_lines(session),
        END_OF_REPORT,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _local(timestamp: str) -> str:
    """A catalog timestamp as UX 2's local time, or the raw text if it will not parse.

    The same rendering `pam status` uses, for the same reason: every `now=` pam writes
    is already local, so there is nothing to convert and only a shorter form to print.
    A value that will not parse is printed as it stands - a report is the wrong place to
    hide something unexpected - and an empty one becomes "unknown" rather than a blank
    after a colon, which reads as a bug in the report rather than as a missing fact.
    """
    if not timestamp:
        return "unknown"
    try:
        return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return timestamp

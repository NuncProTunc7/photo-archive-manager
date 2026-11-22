"""pam scan's console: the progress line, the closing summary and the sync warning box (UX 2).

Cut out of `pam/scan/runner.py` by M14 Task 5a (ruling C5-1), a pure move: the runner was at its
290-line stop before Task 5 added the lock-held scan. `runner` imports `print_summary`,
`_progress` and `SKIPPED_POINTER` back, so every caller and test that names them there is
unchanged.
"""

import sys
from typing import TYPE_CHECKING

import click

from pam.status import NextStep, step_lines

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    # The summary's type only (the `events.py` / `PlanSummary` precedent): `runner` imports this
    # module, so a runtime import would be a cycle.
    from pam.scan.runner import ScanSummary

_PROGRESS_EVERY = 100

# UX 2's warning box ends by naming the command that explains what was not read. A
# module-level constant so a test can drive the EXACT string pam prints through the CLI
# and prove it resolves to a real command, rather than asserting a substring somebody
# typed twice.
SKIPPED_POINTER = "pam report skipped"


def _progress(done: int, total: int) -> None:
    """UX spec section 2: rewriting line on a TTY, plain append lines otherwise."""
    if total == 0:
        return
    line = f"Reading  {done:,} / {total:,} files  ({done * 100 // total}%)"
    if sys.stdout.isatty():
        click.echo("\r" + line, nl=False)
        if done == total:
            click.echo()
    elif done % _PROGRESS_EVERY == 0 or done == total:
        click.echo(line)


def print_summary(summary: "ScanSummary") -> None:
    click.echo("Scan complete.")
    click.echo(
        f"  {summary.total:,} files found  "
        f"({summary.images:,} images, {summary.videos:,} videos, "
        f"{summary.sidecars:,} sidecars, {summary.other:,} other)"
    )
    click.echo(
        f"  {summary.new:,} new - {summary.unchanged:,} unchanged - "
        f"{summary.changed:,} changed - {summary.missing:,} missing"
    )
    click.echo(f"  {summary.read:,} files read this scan - everything else was already known.")
    if summary.errors:
        # Finding 10: the '!' symbol UX spec section 2 requires - colour is never
        # the only signal, and `pam doctor` already gets this right.
        click.secho(
            f"  ! {len(summary.errors):,} files could not be read - "
            "skipped and listed in the log.",
            fg="yellow",
        )
    if summary.sync_provider:
        _warn_box(summary)
    click.echo()
    # UX §3 rev 8 (finding F3): the re-run reassurance names no command, so it
    # cannot honestly wear a `Next:` or `  or:` label - those label next steps.
    # It prints as a plain line ahead of the block; the block itself IS
    # `pam/status.py`'s renderer (M8 amendment B2) rather than a hand-rolled copy of
    # its shape, and `pam plan`'s reason is the one `pam/report.py` already gives for
    # this exact command - two surfaces naming `pam plan` must not describe it
    # differently.
    #
    # THE `  or:` SLOT IS FILLED AT LAST (M9 Task 12). UX §3 rev 8 recorded that
    # `or: pam enrich` "is M9's line and is not printed yet"; `pam enrich` exists as of
    # this commit, so the line names a command that runs today rather than a milestone
    # promise - which is the whole of the M4 standing rule it was withheld under. The
    # reason is UX §3's own parenthesis for this exact row, verbatim.
    click.echo("pam scan is safe to re-run anytime - only new or changed files are re-read.")
    steps = (
        NextStep("pam plan", "propose new names - renames nothing"),
        NextStep("pam enrich", "add AI understanding first"),
    )
    for line in step_lines(steps):
        click.echo(line)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)


def _warn_box(summary: "ScanSummary") -> None:
    """UX 2's boxed warning, including its normative last line.

    `Details: pam report skipped` shipped in UX 2's copy in M2 and was omitted here for
    four milestones, because the command it names did not exist until M6 Task 6. A
    pointer pam offers must exist (M4 finding 12), so it is added now that it resolves -
    and `tests/test_report.py` drives the exact string through the CLI rather than
    matching it as a substring, because a pointer that merely looks right is the defect.
    """
    lines = [f"This folder is inside {summary.sync_provider}."]
    if summary.placeholders:
        lines.append(
            f"{summary.placeholders:,} files are cloud-only placeholders and were not read."
        )
    lines.append("Renaming many files here may trigger a large re-sync.")
    lines.append(f"Details: {SKIPPED_POINTER}")
    width = max(len(line) for line in lines)
    click.secho("+- Warning " + "-" * (width - 8) + "+", fg="yellow")
    for line in lines:
        click.secho(f"| {line.ljust(width)} |", fg="yellow")
    click.secho("+" + "-" * (width + 2) + "+", fg="yellow")

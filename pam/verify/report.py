"""What `pam verify` prints (UX spec sections 1.4 and 2).

Split out of `pam/verify.py` at M6 Task 4, along the cut Task 3's spec review measured.
The same shape as `execute/reporter.py` and `naming/plan_report.py`: the rendering lives
apart from the thing it renders, and the dependency runs one way - this module imports
the check protocol from `checks.py` and the package imports these two functions, never
the other way round.

`VerifySummary` is imported under TYPE_CHECKING only, for the reason `execute/
reporter.py` documents: `pam/verify/__init__.py` imports this module at runtime, so
this module must not import it back. The attributes are read at runtime; only the
annotation needs the name.
"""

from typing import TYPE_CHECKING, NamedTuple

import click

from pam.verify.checks import STATUS_ERROR, STATUS_PASS, STATUS_WARN

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    from pam.verify import VerifySummary

# UX spec 2: symbols accompany colour, because colour is never the only signal (and is
# switched off entirely when stdout is not a TTY or NO_COLOR is set).
_SYMBOLS = {
    STATUS_PASS: ("ok", "green"),
    STATUS_WARN: ("!", "yellow"),
    STATUS_ERROR: ("x", "red"),
}


class ReportLine(NamedTuple):
    """One printed line and the colour it takes, or '' for the plain ones.

    UX spec 2: colour is never the only signal, so the `ok`/`!`/`x` symbol is already
    in `text` and the colour only reinforces it. Pairing them here rather than
    re-deriving the colour while printing means the lines a test reads are exactly the
    lines a user sees.
    """

    text: str
    colour: str


def verify_summary_lines(summary: "VerifySummary") -> list[ReportLine]:
    """The whole report: one line per check, the fixes below the ones that found
    something, then the headline (UX spec 2)."""
    lines = [
        ReportLine(f"Checking what pam has recorded for {summary.archive_root}", ""),
        ReportLine("", ""),
    ]
    for result in summary.results:
        mark, colour = _SYMBOLS[result.status]
        tail = "" if result.status == STATUS_PASS else f"  - {result.count:,} found"
        lines.append(ReportLine(f"  {mark:<2} {result.key:<2} {result.name}{tail}", colour))
        # A finding always says what to do next, and `_result` guarantees exactly one of
        # these is set - so a finding can never print an empty next step, and never two.
        if result.status != STATUS_PASS:
            if result.fix:
                lines.append(ReportLine(f"        Fix: {result.fix}", ""))
            elif result.note:
                lines.append(ReportLine(f"        {result.note}", ""))
    lines.append(ReportLine("", ""))
    checks = len(summary.results)
    if summary.errors or summary.warnings:
        lines.append(
            ReportLine(
                f"Done with problems. {checks:,} checks,"
                f" {len(summary.errors):,} error(s), {len(summary.warnings):,} warning(s).",
                "",
            )
        )
        lines.append(
            ReportLine("  pam changed nothing - verify only reads. The fixes above", "")
        )
        lines.append(ReportLine("  are the next step; your photographs are not affected.", ""))
    else:
        lines.append(
            ReportLine(f"Done. {checks:,} checks, everything pam recorded looks right.", "")
        )
        lines.append(ReportLine("  Nothing to do.", ""))
    # There used to be a `--deep is not available yet` line here, because the flag was
    # accepted and ignored and vision principle 9 forbids a check that did not run
    # reading as one that passed. M6 Task 4 built check 8, so the honest report is now
    # the results list itself: with `--deep` it carries check 8's own line, naming how
    # many files were re-read from disk; without it, it carries no line about check 8 at
    # all, so nothing claims bytes were compared. Nothing to add here.
    return lines


def print_verify_summary(summary: "VerifySummary") -> None:
    """The report on stdout, then spec 13's log path on stderr - unconditionally."""
    for line in verify_summary_lines(summary):
        click.secho(line.text, fg=line.colour or None)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)

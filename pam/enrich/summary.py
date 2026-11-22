"""`pam enrich`'s summary - the outcome records, the progress line and the closing block.

CUT OUT OF `pam/enrich/command.py` BY M13 TASK 3 (`m12.md` C8), which named the cut before any
edit: that module was at 296 executable lines and C4's refusal was about to land in it. The
command builds and runs enrichers; this module says what they did. The import runs one way,
command -> summary, and nothing here knows how an enricher is built.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from pam.enrich.base import EnrichSummary, Progress
from pam.status import NextStep, step_lines

# Q4 (design 12 rev 28): a refusal printed as `<name>: not run - <reason>` and its hint lines,
# with no `!` and no `Run: pam doctor` - and which still EXITS 1 (rev 17's enabled-but-
# unavailable state: the work asked for was not done). Q4 covers the PACK states only (an unset
# model_dir, a missing file, a hash mismatch), so the form is keyed on the sentence's opening
# words; a missing extra keeps the `!` form and its pointer to pam doctor. Spelled by hand.
NOT_RUN_REFUSALS: dict[str, str] = {"ocr": "[enrich.ocr] model_dir "}

# P13: what an enricher whose results are DECIDED AT PLAN TIME says instead of counting them.
# `ocr` stores readings; its dates do not exist until `pam plan` (P7). Spelled from the spec.
PLAN_TIME_RESULTS: dict[str, str] = {
    "ocr": "  Dates are read from that text when you run pam plan.",
}

# P3 (M12): for an enricher that writes a PER-FILE MARKER beside its results (design 4:
# `faces`), "results stored" counts bookkeeping a user cannot find. A human test read "N results
# stored" where N was the face rows PLUS one marker per file read. Name the RESULT kind
# instead. Spelled from the spec, never derived from `repo._FACT_KINDS` or the sink table.
# `ocr` is deliberately absent: its one kind IS the marker and its dates are decided at plan
# time (P7), so P13 gives it its own lines (Task 13).
RESULT_NOUNS: dict[str, tuple[str, str, str]] = {
    "faces": ("face", "face found", "faces found"),
}


@dataclass(frozen=True)
class EnricherOutcome:
    """What one enricher did, or why it did nothing.

    `refusal`, `disabled` and `result` are the THREE states and exactly one is set. A
    refusal is not a failure of the run - a missing Pillow is a sentence, per
    `available()`'s contract - but it IS a reason the user did not get what they asked
    for, so it reaches the exit code through `had_errors` below.

    `disabled` IS THE THIRD STATE AND IT IS AMENDMENT A2'S WHOLE POINT (design 8.1 rev
    17). An enricher whose `[enrich.<name>] enabled` key is `false` is not run, is
    reported in one line naming the key that turns it on, and EXITS 0: the user
    configured this, and a configuration is not a per-file error under design 13. Folding
    it into `refusal` would make `pam enrich` EXIT 1 ON A DEFAULT INSTALL, because
    `[enrich.caption] enabled` ships `false` - a machine where nothing is wrong reporting
    an error is the trap A2 exists for.
    """

    name: str
    result: EnrichSummary | None = None
    refusal: str = ""
    disabled: str = ""
    # P13 (M12 Task 13): a FOURTH state, a sibling of `disabled` - enabled, configured to read
    # nothing (`ocr` with no folders). The text after `not run - `, then its hint. Exit 0.
    idle: tuple[str, ...] = ()
    # N9 (design 12 rev 26): the `model_name model_version` the run LOADED, for the closing
    # block's `  model:` line. Empty when nothing was loaded, or for an enricher with no
    # receipt - `faces` and `ocr` are the two that have one.
    model: str = ""


@dataclass
class EnrichSummaryReport:
    """Every enricher this invocation ran, in the order it ran them."""

    archive_root: Path
    outcomes: list[EnricherOutcome] = field(default_factory=list)
    elapsed_s: float = 0.0
    log_path: Path | None = None

    @property
    def had_errors(self) -> bool:
        """Spec 13 rev 6: a command that recorded per-file errors exits 1, not 0.

        THREE THINGS COUNT, and the third is the one worth arguing about.
        `failed` is spec 13's per-file error exactly. A `refusal` is the work the user
        asked for not happening at all. And `facts_dropped` is pam refusing its OWN
        enricher's output - a file that was read, decoded and then had its result thrown
        away for failing spec 6.4's validation. Reporting exit 0 over a discarded result
        is the subset-as-whole failure vision principle 9 names; it is a louder defect
        than an unreadable file, not a quieter one.

        `facts_conflicted` deliberately does NOT count: it means the identity already
        holds that fact, which is what a re-run over unchanged bytes is supposed to look
        like. `disabled` does not count either, for a different reason: nothing was asked
        for, so nothing was denied (amendment A2).
        """
        return any(
            o.refusal or (o.result is not None and (o.result.failed or o.result.facts_dropped))
            for o in self.outcomes
        )


def progress(tick: Progress) -> None:
    """UX 2's progress line: rewriting on a TTY, plain append lines otherwise.

    THE ETA IS THE RUNNER'S, PRINTED AS GIVEN. M9 Task 7's stage-2 gate measured it at
    87.5 s displayed for a run that took 0.08 s, because `remaining_ms` multiplied the
    files left to WALK by the cost of a file that is ENRICHED and did not divide by
    `workers`. That was design 8.1 implemented literally; **design 8.1 rev 16 now states
    the formula** and `eta.py` implements it, so the overstatement is fixed where it was
    caused. It is still not a number for this printer to invent behind the runner's back.
    """
    if tick.total == 0:
        return
    line = (
        f"Enriching  {tick.walked:,} / {tick.total:,} files"
        f"  ({tick.walked * 100 // tick.total}%)  ETA {_eta(tick.eta_ms)}"
    )
    if sys.stdout.isatty():
        click.echo("\r" + line, nl=False)
        if tick.walked == tick.total:
            click.echo()
    else:
        click.echo(line)


def _eta(eta_ms: float) -> str:
    """UX 2: numbers are humanized. Under a minute reads in seconds, above it in minutes."""
    seconds = eta_ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.0f} min"


def enrich_lines(report: EnrichSummaryReport) -> list[str]:
    """The closing block. One paragraph per enricher, then the one `Next:` block."""
    lines = [f"Enrichment in {report.archive_root}", ""]
    for outcome in report.outcomes:
        lines.extend(_outcome_lines(outcome))
    lines.append(f"Done in {report.elapsed_s:.1f}s.")
    lines.append("")
    # UX 3's chain: what a user does after enriching is make names out of it. The reason
    # is the one `pam/report.py` and `pam scan` already give for `pam plan` - two
    # surfaces naming one command must not describe it differently (UX 3 rev 9).
    lines.extend(step_lines((NextStep("pam plan", "propose new names - renames nothing"),)))
    return lines


def _outcome_lines(outcome: EnricherOutcome) -> list[str]:
    """One enricher's paragraph: what it did, or the sentence saying why it did not.

    The disabled line carries NO `!` and NO `Run: pam doctor`. Design 8.1 rev 17: this is
    a configuration and not an error, and pointing a user at the diagnostic command for a
    setting they chose is UX 1.4's dead end. It names the key instead, which is the thing
    that would change the answer.
    """
    if outcome.disabled:
        return [
            f"{outcome.name}: not run - {outcome.disabled} is false in pam.toml.",
            f"  Set {outcome.disabled} = true to turn it on.",
            "",
        ]
    if outcome.idle:
        return [f"{outcome.name}: not run - {outcome.idle[0]}", *outcome.idle[1:], ""]
    pack = NOT_RUN_REFUSALS.get(outcome.name)
    if outcome.result is None and pack is not None and outcome.refusal.startswith(pack):
        first, *rest = outcome.refusal.splitlines()
        return [f"{outcome.name}: not run - {first}", *rest, ""]
    if outcome.result is None:
        return [
            f"! {outcome.name} did not run: {outcome.refusal}",
            "  Run: pam doctor   (it checks every dependency pam can use)",
            "",
        ]
    r = outcome.result
    head = f"{outcome.name}: {r.enriched:,} of {r.walked:,} files read"
    if (later := PLAN_TIME_RESULTS.get(outcome.name)) is not None:
        lines = [f"{head}.", later]
    else:
        lines = [f"{head}, {_stored(outcome.name, r)}."]
    lines.append(
        f"  skipped: {r.skipped_cached:,} already done, {r.skipped_wants:,} not applicable,"
        f" {r.skipped_duplicate:,} identical to another file,"
        f" {r.skipped_no_sha:,} never read from disk."
    )
    # Design 8.1 rev 34 (V1): a file the loader refused because it became cloud-only after
    # the last scan. Its own line, not `failed`: the fix is a scan, not the log.
    if r.cloud_only:
        lines.append(
            f"  ! {r.cloud_only:,} file(s) became cloud-only after the last scan and were not"
            " read. Run pam scan, then pam enrich again."
        )
    if outcome.model:
        lines.append(f"  model: {outcome.model}")
    if r.superseded:
        lines.append(f"  {r.superseded:,} older results replaced by this run.")
    if r.facts_conflicted:
        lines.append(
            f"  {r.facts_conflicted:,} results were already stored and kept as they are."
        )
    # Spec 13 rev 12: every summary line that mentions per-file errors names where they
    # are listed. `pam report skipped` is the scanner's register and holds none of these,
    # so the log is the honest pointer.
    if r.failed:
        lines.append(
            f"  ! {r.failed:,} files could not be read - skipped and listed in the log."
        )
    if r.facts_dropped:
        lines.append(
            f"  ! {r.facts_dropped:,} results were rejected as malformed and not stored"
            " - listed in the log."
        )
    lines.append("")
    return lines


def _stored(name: str, r: EnrichSummary) -> str:
    """P3: what the run stored, in the words a user can check against what they can find.

    An enricher in `RESULT_NOUNS` counts only its RESULT kind (`faces` -> its `face` rows,
    never the per-file markers); every other enricher keeps "N results stored".
    """
    if name not in RESULT_NOUNS:
        return f"{r.facts_written:,} results stored"
    kind, one, many = RESULT_NOUNS[name]
    found = r.facts_written_by_kind.get(kind, 0)
    return f"{found:,} {one if found == 1 else many}"


def print_enrich_summary(report: EnrichSummaryReport) -> None:
    """stdout for the report, stderr for the log path (spec 13, UX 2)."""
    for line in enrich_lines(report):
        click.echo(line)
    if report.log_path is not None:
        click.echo(f"Log: {report.log_path}", err=True)

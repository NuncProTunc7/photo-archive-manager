"""What `pam plan` prints (UX spec sections 2 and 8).

Split out of `planner.py` at M6 Task 1, along the cut the M5 review recorded
(`docs/implementation-notes/m5.md` section 2.5). The planner was the one module over
the rev-10 ~300-executable-line budget.

The dependency runs one way only: this module imports `PlanSummary` from the planner,
and the planner never imports this. `cli.py` is the single production importer, and it
already imports both.
"""

import click

from pam.naming.planner import PlanSummary
from pam.status import NextStep, step_lines


def _print_steps(*steps: NextStep) -> None:
    """UX 2/3's closing block, rendered by the ONE renderer (M8 amendment B2).

    This module printed `Next: <cmd> - <why>.` for four milestones - one of the three
    treatments the M8 review measured, and not the one the project has a type for. The
    reasons themselves are unchanged and are the same words `pam status` already uses
    for these two commands (`pam/status.py::next_steps`); only the punctuation moves.
    """
    for line in step_lines(steps):
        click.echo(line)


def _print_errors(summary: PlanSummary) -> None:
    """Finding 8 + spec 13 rev 6: unconditional, on the nothing-to-rename path too.

    A run that dropped forty units used to render as "Nothing to rename." with no
    error line at all - failure summarized as success (vision principle 9).
    Finding 10: the leading '!' is the symbol UX spec section 2 requires, because
    colour is never the only signal.
    """
    if summary.errors:
        click.secho(
            f"  ! {summary.error_files:,} files could not be given a name - see the log.",
            fg="yellow",
        )


def _print_events(summary: PlanSummary) -> None:
    """spec 6.3 rule 5b / spec 8.3 rev 14: `pam plan` is the command that groups.

    So `pam plan` is the command that reports it - NOT `pam status`, whose enrichment
    list is spec 8.1's five enrichers and does not contain events. The line appears on
    both exits, because grouping happens on both: a run that plans nothing still
    regrouped the archive, and a summary that mentioned it only when there were names
    to show would be silent about the only work that run did.

    Zero events prints nothing, matching every other count line here. In M8 every event
    label is empty (labels are geocoded, which is M9), so the count is all there is to
    say about them; the review nav renders an unlabelled event as its date span.
    """
    if summary.events:
        noun = "event" if summary.events == 1 else "events"
        click.echo(f"  Grouped into {summary.events:,} {noun} by date and place.")


def _print_blocked_by_missing(summary: PlanSummary) -> None:
    """C11 (m10.md): the skipped `{seq}` number, explained (UX 2/8, vision principle 9).

    NO `Fix:` AND NO `Run:` LINE, and that is a ruling rather than an omission. Spec 7.5
    rev 11: *a finding no pam command can clear names no `Fix:` line* - and nothing in v1.0
    clears a ghost row. `pam forget` is not the route: it drops an archive POINTER (spec 3)
    and clears no `files` row. Offering a command that does not do the job is worse than
    offering none, so the copy closes by saying plainly that there is nothing to run.

    It says what happened, why the names are held, and that nothing is wrong - no
    `sha256`, no `missing`, no `plan_items` (UX 2's plain-language rule). The names are
    listed in full rather than sampled: the whole point is to explain WHICH number was
    skipped, and a truncated list answers a different question.
    """
    if not summary.blocked_by_missing:
        return
    count = len(summary.blocked_by_missing)
    noun = "number was" if count == 1 else "numbers were"
    files = "a file that is" if count == 1 else "files that are"
    click.secho(
        f"  ! {count:,} {noun} skipped because pam's records still name {files} not on disk:",
        fg="yellow",
    )
    for rel_path in summary.blocked_by_missing:
        click.echo(f"      {rel_path}")
    click.echo(
        "    Those names are held so a rename cannot overwrite them. Nothing is wrong with your"
    )
    click.echo("    photos, and no pam command clears these records.")


def _print_log_path(summary: PlanSummary) -> None:
    """UX spec section 2: EVERY session ends with the log path on stderr."""
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)


def print_plan_summary(summary: PlanSummary) -> None:
    """UX spec section 2/8: plain language, counts, then one Next: hint."""
    if summary.plan_id is None:
        click.echo("Nothing to rename.")
        if summary.already_conforming:
            click.echo(
                f"  {summary.already_conforming:,} files already match the naming template."
            )
        if summary.skipped_placeholders:
            click.echo(
                f"  {summary.skipped_placeholders:,} cloud-only files were skipped "
                "(pam never downloads them to read a date)."
            )
        _print_events(summary)
        _print_errors(summary)
        click.echo()
        # `NextStep.text` "is usually a command, but not always" - here it is one, and
        # the trailing clause is a reason rather than part of it, so it belongs in `why`
        # rather than in the command a user might otherwise try to type verbatim.
        _print_steps(
            NextStep("pam scan <folder>", "if you have added photos since the last scan")
        )
        _print_log_path(summary)
        return
    click.echo(f"Plan {summary.plan_id} is ready - nothing has been renamed yet.")
    # Finding 10: PlanSummary.total counts the files pam can name, not every file
    # walked (which is what ScanSummary.total counts), so "375 of 400" right after
    # a scan that said 500 needs the qualifier to mean anything.
    # NO-OP ITEMS ARE NOT RENAMES AND THIS LINE MUST NOT COUNT THEM (spec 6.2 rule 7;
    # M10 Task 1, stage-2 review C2). spec 7.3 rev 16 puts a member whose name does not
    # change into the plan so a companion unit is never split, so `planned` - the number
    # of plan ITEMS - is no longer the number of files that will move. Before this,
    # `pam plan` said "2 files would be renamed" over a plan `pam apply` then correctly
    # described as "1 renames approved ... 1 already conforming": two pam surfaces
    # reporting different truths about the same plan, and THIS is the one the user reads
    # first. The apply side was widened when the no-op item was introduced; this was not.
    renaming = summary.planned - summary.noop_items
    conforming = summary.already_conforming + summary.noop_items
    noun = "file" if renaming == 1 else "files"
    click.echo(
        f"  {renaming:,} {noun} would be renamed, out of {summary.total:,} pam can name."
    )
    # The two populations are disjoint by construction and the sum is what UX 4's counts
    # line reconciles against: `already_conforming` counts files that got NO plan item
    # (the planner's wholly-conforming early return), and `noop_items` counts items whose
    # file will not move. A file cannot be in both, which is why they add rather than
    # overlap - and it is why the planner counts one and not the other (Task 1 Step 3).
    if conforming:
        click.echo(f"  {conforming:,} already match the template.")
    if summary.skipped_placeholders:
        click.echo(f"  {summary.skipped_placeholders:,} cloud-only files were skipped.")
    _print_events(summary)
    order = ("high", "medium", "low")
    breakdown = ", ".join(
        f"{summary.tiers[tier]:,} {tier}" for tier in order if tier in summary.tiers
    )
    if breakdown:
        click.echo(f"  Confidence: {breakdown}.")
    if summary.left_behind:
        click.secho(
            f"  ! {summary.left_behind:,} other files share a name with something being "
            "renamed and will NOT be renamed with it - listed in the log.",
            fg="yellow",
        )
    _print_blocked_by_missing(summary)
    _print_errors(summary)
    if summary.examples:
        click.echo("  For example:")
        for old, new in summary.examples:
            click.echo(f"    {old}  ->  {new}")
    click.echo()
    # UX spec 3's chain, completed at M7: plan -> review -> apply. `pam review` leads
    # because seeing the names is the step before approving them, and it is the one the
    # first-run journey draws; `pam apply` stays on the second line because a user who
    # already trusts the plan should not have to open a browser to run it.
    #
    # Standing rule (M4 review), still binding: the `Next:` line names the next command
    # that ACTUALLY EXISTS, never a milestone promise. This line pointed at `pam review,
    # coming in M7` once - a dead end dressed as guidance - and M4 replaced it with
    # `pam apply`. `pam review` ships in M7, so it is now a command and not a promise.
    _print_steps(
        NextStep(f"pam review {summary.plan_id}", "see & approve the plan"),
        NextStep(f"pam apply {summary.plan_id}", "renames only what you approve"),
    )
    _print_log_path(summary)

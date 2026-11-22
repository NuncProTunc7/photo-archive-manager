"""`pam verify` - the catalog integrity checker (spec 7.5). `git fsck` for the catalog.

Twelve checks, one line of output each, and **not one of them
changes anything**. The catalog is opened through `connect_readonly` (spec 7.5 "strictly
read-only"), so a repair added here would fail at runtime rather than quietly fix
something the user was only asking about. Spec 7.5 is explicit about the case that most
invites a repair - a companion unit left split across two stems is "reported for human
attention, never repaired by `pam verify`" - and UX spec 1.4 gives the alternative:
every check that finds something prints **the command that fixes it**, so a report is
never a dead end.

A PACKAGE SINCE M6 TASK 4, and the import path is unchanged. `pam/verify.py` was 379
executable lines against the rev-10 ~300 budget and check 8 was about to be added to it,
so it was cut along the line Task 3's spec review measured: `checks.py` holds the checks
and the protocol they obey, `report.py` holds the printing, and this file holds the
runner, the summary, the command - and `CHECKS`. Everything the module used to export is
re-exported below, so `cli.py` and every test import exactly what they imported before.

WHY THE EXIT CODES CONTRADICT SPEC 13, AND WHY THAT IS CORRECT. Spec 13 assigns exit 1
to an expected error and exit **2 to an unexpected exception with a traceback**. Spec
7.5 assigns this command **0 all-pass, 1 warnings only, 2 errors**, so `pam verify`
exits 2 for a finding it fully expected and reported calmly. That collision is
deliberate and spec 7.5 wins, for the same reason the M5 review left a `crash:` fault
at exit 1 (open question 5): the two cases are trivially distinguishable by **output**
- a checker prints a pass/fail report, a crash prints a traceback - and inventing a
novel exit code for this one command, to protect a distinction nothing consumes, costs
every script that wraps `pam verify` the one convention it can rely on. **Do not "fix"
this.** The numbers below are spec 7.5's, written out as constants so that a reader who
finds them surprising lands on this paragraph.

WHY THE RUNNER IS A LIST, AND WHY THERE ARE NOW TWO OF THEM. `CHECKS` is the checker
that always runs: the printer, the session log and the exit code all read that list, so
a check added in a later milestone cannot be implemented and then forgotten by the thing
that reports it. M6 Task 4's check 8 runs **only** under `--deep`, so it cannot go in
`CHECKS` - but a second hand-written list would lose exactly the property `CHECKS` was
built for. So `DEEP_CHECKS` sits beside it and `ALL_CHECKS` is their concatenation:
`run_verify` picks one of the two tuples and every consumer downstream still reads
whatever it was handed, unedited. The guard test asserts that every `check_*` function
in the package is in one of the two and in neither twice, so a check written and left
out of both still reports nothing at all - and is still caught.

WHY A CHECK NEVER RAISES. This is the one tool that must survive a corrupt catalog: a
checker that crashes on the state it exists to find tells the user nothing at all about
the other checks. So `run_checks` wraps every call, and an unexpected exception becomes
an `error` result naming the check that produced it. `SystemExit` and `KeyboardInterrupt`
are deliberately NOT caught - a Ctrl+C is not a finding.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pam.catalog.db import migrate, open_catalog, open_catalog_readonly, read_snapshot
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log
from pam.verify.checks import DEEP_SAMPLE_FLOOR as DEEP_SAMPLE_FLOOR
from pam.verify.checks import DEEP_SAMPLE_PERCENT as DEEP_SAMPLE_PERCENT
from pam.verify.checks import FIX_SCAN as FIX_SCAN
from pam.verify.checks import FIX_UNDO as FIX_UNDO
from pam.verify.checks import STATUS_ERROR as STATUS_ERROR
from pam.verify.checks import STATUS_PASS as STATUS_PASS
from pam.verify.checks import STATUS_WARN as STATUS_WARN
from pam.verify.checks import CheckContext as CheckContext
from pam.verify.checks import CheckFn as CheckFn
from pam.verify.checks import CheckResult as CheckResult
from pam.verify.checks import check_1_catalog_file as check_1_catalog_file
from pam.verify.checks import check_2a_orphaned_rows as check_2a_orphaned_rows
from pam.verify.checks import check_2b_inert_facts as check_2b_inert_facts
from pam.verify.checks import check_3_companion_groups as check_3_companion_groups
from pam.verify.checks import check_4a_intent_rows as check_4a_intent_rows
from pam.verify.checks import check_4b_applying_plans as check_4b_applying_plans
from pam.verify.checks import check_4c_item_status_agreement as check_4c_item_status_agreement
from pam.verify.checks import check_4d_stale_plan_status as check_4d_stale_plan_status
from pam.verify.checks import check_4e_split_units as check_4e_split_units
from pam.verify.checks import check_5_supersede_chains as check_5_supersede_chains
from pam.verify.checks import check_6_duplicate_paths as check_6_duplicate_paths
from pam.verify.checks import check_7_thumbnail_cache as check_7_thumbnail_cache
from pam.verify.checks import check_8_deep_rehash as check_8_deep_rehash
from pam.verify.checks import deep_sample as deep_sample
from pam.verify.report import ReportLine as ReportLine
from pam.verify.report import print_verify_summary as print_verify_summary
from pam.verify.report import verify_summary_lines as verify_summary_lines

# Spec 7.5's exit codes, overriding spec 13 - see the module docstring before changing
# any of these three numbers.
EXIT_ALL_PASS = 0
EXIT_WARNINGS = 1
EXIT_ERRORS = 2

# The checker that ALWAYS runs. The printer, the session log and the exit code all read
# whichever list they are handed, so a check written and not added to one of the two
# tuples below reports nothing at all - and one added here is reported by every consumer
# without any of them being edited.
CHECKS: tuple[CheckFn, ...] = (
    check_1_catalog_file,
    check_2a_orphaned_rows,
    check_2b_inert_facts,
    check_3_companion_groups,
    check_4a_intent_rows,
    check_4b_applying_plans,
    check_4c_item_status_agreement,
    check_4d_stale_plan_status,
    check_4e_split_units,
    check_5_supersede_chains,
    check_6_duplicate_paths,
    check_7_thumbnail_cache,
)

# The checks `--deep` ADDS to the twelve above. Separate rather than a flag on each
# check, because the difference is not what a check reports but whether it runs at all:
# check 8 opens files in the archive and re-reads their bytes, which is seconds of work
# per gigabyte, and spec 7.5 puts it behind the flag for that reason. A user who did not
# ask for it is never told it passed - it simply is not in the report.
DEEP_CHECKS: tuple[CheckFn, ...] = (check_8_deep_rehash,)

# What `--deep` runs: everything, in spec 7.5's order. Concatenation rather than a third
# hand-written list, so the two above stay the single place a check is registered.
ALL_CHECKS: tuple[CheckFn, ...] = CHECKS + DEEP_CHECKS


def run_checks(ctx: CheckContext, checks: tuple[CheckFn, ...] = CHECKS) -> list[CheckResult]:
    """Every check, in order, with none of them able to take the others down.

    A checker that crashes on a corrupt catalog is the one tool that must not: the
    checks after the crashed one are exactly what the user needs at that moment. So an
    unexpected exception becomes an `error` result naming the check it came from.

    `Exception`, never `BaseException`: a `crash:` fault (SystemExit) and a real Ctrl+C
    (KeyboardInterrupt) are not findings about the catalog and must keep unwinding.
    """
    results = []
    for check in checks:
        try:
            results.append(check(ctx))
        except Exception as e:
            results.append(
                CheckResult(
                    key="?",
                    name=f"{check.__name__} could not run",
                    status=STATUS_ERROR,
                    count=1,
                    details=(f"{type(e).__name__}: {e}",),
                    fix=FIX_SCAN,
                )
            )
    return results


@dataclass
class VerifySummary:
    """What one `pam verify` invocation found. Nothing here records a change."""

    archive_root: Path | None = None
    # Every check that RAN, and nothing about the ones that did not. `deep_requested`
    # used to live here so the report could say the re-hash was unavailable; M6 Task 4
    # built it, so the flag now decides which tuple runs and `results` records the
    # outcome. Vision principle 9 is served better by this: a `--deep` run carries a
    # check-8 line naming how many files it re-read, and a run without the flag carries
    # no line about check 8 at all, so nothing can read as a check that passed unrun.
    results: list[CheckResult] = field(default_factory=list)
    log_path: Path | None = None
    # Every file check 8 drew, in the order it drew them, and empty on a run without
    # `--deep` (HT-M6 finding H1). It is not a result and does not reach stdout: it is
    # what `--seed` promises made observable, written to the session log by
    # `_log_results` so that two runs at the same seed can be compared file by file.
    deep_sampled: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == STATUS_ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == STATUS_WARN]

    @property
    def exit_code(self) -> int:
        """Spec 7.5: 0 all-pass, 1 warnings only, 2 errors. See the module docstring."""
        if self.errors:
            return EXIT_ERRORS
        if self.warnings:
            return EXIT_WARNINGS
        return EXIT_ALL_PASS


def run_verify(
    archive: Path | None = None,
    *,
    deep: bool = False,
    seed: int | None = None,
    sample: float | None = None,
) -> VerifySummary:
    """Check one archive's catalog. Reads only; changes nothing (spec 7.5).

    `deep` adds spec 7.5's check 8, which re-reads a 1% sample of the archive's bytes.
    `seed` makes that sample reproducible and is inert without `deep`; None is the
    ordinary case and means "seeded from the OS", which is what "a random sample" means.
    `sample` (M13, H4) is the percentage `--deep` re-reads; None means the default 1 %.
    Without `deep` it is REFUSED, not ignored: a user who typed `--sample 100` believes
    every file was re-read, and a silent no-op would be a check that never ran reported
    as one that passed.

    THE CONNECTION SHAPE, and why there are two of them (plan clarification 4).
    `pam verify` is archive-scoped, so spec 7.2's recovery runs first, inside the
    workspace lock, exactly as it does for `scan`/`plan`/`apply`/`undo` - otherwise a
    crash left by an interrupted apply would be reported by every check as damage while
    the machinery that heals it sat one command away. But recovery WRITES, and a
    checker must not be able to. So the write connection exists for exactly as long as
    recovery needs it and is closed; the checks then run through a connection opened
    `mode=ro`, which SQLite itself refuses to let write.

    The lock spans both. Dropping it for the read half would let a concurrent `pam
    apply` move files underneath the checks, and the report would describe an archive
    that no longer exists.
    """
    if sample is not None and not deep:
        raise PamError(
            "--sample only means something with --deep: it is the share of files --deep"
            " re-hashes. Run: pam verify --deep --sample N"
        )
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")

    summary = VerifySummary(archive_root=root)
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="verify")
        summary.log_path = log_path
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                run_recovery(conn, root, logger)
                conn.commit()
            finally:
                conn.close()
            reader = open_catalog_readonly(ws.db_path)
            try:
                ctx = CheckContext(
                    conn=reader,
                    archive_root=root,
                    thumbs_dir=ws.thumbs_dir,
                    seed=seed,
                    sample_percent=DEEP_SAMPLE_PERCENT if sample is None else sample,
                )
                # S13: verify's protection is the workspace lock (no pam writer can commit
                # under it) AND one snapshot for every check's reads.
                with read_snapshot(reader):
                    summary.results = run_checks(ctx, ALL_CHECKS if deep else CHECKS)
                # What check 8 drew, carried out of the context it was recorded in. A
                # run without `--deep` never reaches check 8, so this stays empty and
                # the log says nothing about a sample - the same rule the report follows
                # for a check that did not run.
                summary.deep_sampled = ctx.deep_sampled
            finally:
                reader.close()
            _log_results(summary, logger)
        except BaseException:
            # Spec 13's log path is unconditional, and pam/cli.py's `except Exception`
            # prints a traceback with no `Log:` line. The same shape apply, undo, scan
            # and plan carry; `BaseException` because a SystemExit and a Ctrl+C are the
            # two cases where the user most needs to be told where the log is. The
            # empty session id is the truth: verify has a log, not an undoable session.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return summary


def _log_results(summary: VerifySummary, logger: logging.Logger) -> None:
    """Spec 7.5: "details go to the session log". Stdout gets one line per check.

    Every result is logged, passes included: "check 3 found nothing on 2026-08-07" is
    what makes a later "check 3 found four things" mean something.

    THE SAME REASONING PUTS THE `--deep` SAMPLE HERE (HT-M6 finding H1). Check 8 printed
    a count and nothing else, so on a passing run - which is the ordinary run - `--seed`
    changed nothing anybody could see, and its documented purpose is re-checking a
    finding reproducibly. The names of the files that were read go to the log for the
    same reason every passing check does: a run that names what it looked at is one a
    later run can be compared against. They stay OFF stdout deliberately: 1% of a
    100,000-file archive is a thousand lines, and spec 13 is where per-file detail lives.
    """
    for result in summary.results:
        logger.info(
            "verify check %s (%s): %s, %s finding(s)",
            result.key,
            result.name,
            result.status,
            result.count,
        )
        for detail in result.details:
            logger.warning("verify check %s: %s", result.key, detail)
    for rel_path in summary.deep_sampled:
        logger.info("deep re-read: %s", rel_path)

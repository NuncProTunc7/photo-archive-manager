"""The checks themselves, and the protocol every one of them obeys (spec 7.5).

Split out of `pam/verify.py` at M6 Task 4, which was 379 executable lines against the
rev-10 ~300 budget and was about to gain check 8. The cut is the one Task 3's spec
review measured: the checks here, the printing in `report.py`, and the runner, the
summary and the command in `__init__.py`. **The import path `pam.verify` is unchanged**
- everything below is re-exported from the package's front door, so `cli.py` and every
test import exactly what they imported before.

WHY THE PROTOCOL LIVES HERE RATHER THAN AT THE FRONT DOOR. `CheckResult`,
`CheckContext`, `_result` and the three status names are what a *check* is: every one
of them is consumed by the functions below and by nothing else in the package except
the printer. Keeping them beside the checks is also what makes the dependency run one
way - `checks.py` imports nothing from `pam.verify`, so the package's `__init__` can
import this module at the top of the file like any other. The re-exports mean a reader
still finds them at the front door.

SPEC 7.5 NUMBERS SEVEN CHECKS (EIGHT WITH `--deep`); THIS MODULE HAS TWELVE
FUNCTIONS. Check 2 and check 4 are each several independent clauses with different
statuses and different fixes - check 4 alone is four clauses plus J6's split units -
and a single result cannot carry two counts or two fix commands. They are split by spec
clause and keyed `2a`/`2b`, `4a`..`4e` so the mapping back to spec 7.5 is visible in the
output itself.
"""

import math
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from pam.catalog import repo
from pam.scan import walker
from pam.scan.hashing import sha256_file

# Spec 7.5's three outcomes. `warn` is what "reported as inert, not an error" and
# "informational" mean in a vocabulary that has to drive an exit code.
STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_ERROR = "error"

# M9 TASK 12, STEP 4 - `pam verify` AND ENRICHMENT, DECIDED AND RECORDED EITHER WAY.
# The plan required the decision to be written down whichever way it went. It went this
# way: **no new check, and no new fix command.**
#
# WHAT THE NEW TABLES ALREADY GET. `inferred_facts` is covered twice over and both
# halves became REACHABLE for the first time in M9, because nothing before it wrote a
# row: check 2b reports a fact whose sha256 matches no current file - spec 7.5's own
# "content-addressed rows whose file is absent", now produced by editing an enriched
# picture and re-scanning - and check 5 walks the `superseded_by` chains that
# `--refresh-older-than-model` writes. Check 1's `foreign_key_check` covers the rest.
# Both are driven over real `pam enrich` output in `tests/test_enrich_command.py`; until
# this task they stood guard over empty tables.
#
# WHAT `enrichment_runs` GETS: NOTHING, DELIBERATELY. It is the one table with no reader
# outside `catalog/repo/enrichment.py`, and the state that argued for a check is a run
# killed and never resumed - an `enrichment_runs` row whose `finished_at` stays NULL.
# It is not a finding, for the reason the M9 plan gives for refusing a split-unit check:
# **a check for a state that is not wrong can only ever be wrong itself.**
#   * An open run is not damage, it is spec 8.1's RESUME MECHANISM in its resting state.
#     `open_run` exists to find exactly this row and hand it to the next invocation.
#   * It is BOUNDED at one row per `(enricher, model_name, model_version)`: a second
#     `pam enrich` adopts the open run rather than calling `start_run`, so there is no
#     accumulation for a checker to find. Pinned by
#     `test_a_second_run_resumes_the_unfinished_one_rather_than_opening_another`.
#   * It is SELF-CLEARING: the next run of that enricher closes it.
#   * And it is undecidable from the catalog alone - "abandoned" and "about to be
#     resumed" are the same row - so a check would report every interrupted run for ever
#     with no way to tell them apart, which is the permanent-warn shape rev 11 named as
#     the thing that makes people switch a checker off.
# `test_an_unfinished_enrichment_run_is_not_a_pam_verify_finding` pins the silence, so a
# later milestone adding the check has to delete that test on purpose.
#
# THE INVISIBILITY IS REAL AND IS ANSWERED ELSEWHERE. Nothing outside the repo module
# reads `enrichment_runs`, and that stays true here. What a user can act on is surfaced
# where it matters: `pam enrich` resumes the run in front of them, and its progress line
# counts the files that are left rather than the archive.
#
# THE FIX COMMANDS. Every one of these names a command that exists TODAY - the standing
# rule from the M4 review (a `Next:` line naming a milestone promise is a dead end
# dressed as guidance). `pam enrich` now exists and STILL appears in none of them, but
# the reason has changed and is worth keeping straight: it is no longer that the command
# is unbuilt, it is that re-enriching would not clear any finding here. Nothing in pam
# deletes an `inferred_facts` row, so an inert fact survives every enrich; what makes it
# stop being inert is the file coming back, which is `pam scan`.
#
# `pam scan` carries most of them because it is the one command that rebuilds pam's
# picture of the archive from the archive itself: it re-reads every file, re-derives
# every companion group, and runs spec 7.2's recovery on the way in. It is an honest
# next step rather than a guaranteed repair, and the detail lines say which. What is
# never in doubt is the part the user actually worries about: the photographs are not
# involved in any of these findings - pam never writes to the archive except to rename,
# and every check here reads the catalog beside it.
FIX_SCAN = "pam scan"
FIX_UNDO = "pam undo {session_id}"


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict. Frozen: a printer must not be able to soften a finding.

    `count` and `details` are built together by `_result` so they cannot disagree - the
    same reason `ApplySummary.record_failure` is the single entry point for a failure.
    """

    key: str  # spec 7.5's number, with a clause letter where one check has several
    name: str  # one plain-language phrase, for the line on stdout
    status: str
    count: int
    details: tuple[str, ...] = ()
    fix: str = ""  # the command that fixes it - empty when nothing was found, OR when
    # no pam command can clear the finding (see `note`)
    # For a finding NO pam command can clear. UX 1.4 is "never a dead end", and a `Fix:`
    # line naming a command that does not help is a dead end dressed as guidance: the
    # user runs it, sees the identical report, and stops trusting the checker. Check 7
    # is the case - nothing in pam ever removes a thumbnail-cache entry - so it says the
    # finding is harmless instead of naming a command that provably will not clear it.
    # Exactly one of `fix` and `note` is set on a finding; `_result` enforces it.
    note: str = ""


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may read, and the one thing check 8 writes back.

    `thumbs_dir` is here because spec 7.5's check 7 is the one check whose subject is
    not in the catalog at all: the thumbnail cache is a directory of files under the
    workspace (spec 3), keyed by sha256, and what the check compares it against is.

    `archive_root` is check 8's, and check 8's alone. It is the only check that opens a
    file in the ARCHIVE rather than a row in the catalog beside it, and it needs the
    root to turn a `rel_path` into something it can read. The field was carried
    unconsumed through M6 Task 3 on the declared understanding that check 8 would be its
    consumer or the deletion gate would take it; this is that consumption.

    `seed` is check 8's too, and it is None on an ordinary run - `random.Random(None)`
    seeds itself from the OS, which is what spec 7.5's "a random 1% sample" means. A
    test, or a user re-checking a finding, passes `--seed` and gets the same sample.

    `deep_sampled` IS THE ONE THING WRITTEN BACK THROUGH THIS OBJECT, and it exists for
    HT-M6's finding H1: `--seed` promises a sample a person can reproduce, and on a
    PASSING run nothing said which files were in it - three runs at two different seeds
    printed three identical screens. The sample now reaches the session log, which spec
    13 makes the home of per-file detail (the console keeps the count: 1% of 100k files
    is a thousand lines).

    IT TRAVELS HERE RATHER THAN ON `CheckResult`, which is frozen and is a verdict: a
    count, a status and the findings. A list of files that were fine is a transcript, and
    a result that carried one would be a result the printer could read - one milestone
    from a report that names every healthy file it looked at. The runner reads this field
    after the checks are done and logs it; nothing else in the package reads it.

    The dataclass is still frozen, which stops a check REPLACING the field. It cannot
    stop a check appending, and nothing tries: check 8 is the only writer, and the guard
    that would make this structural does not exist. If a second check ever wants to
    report a transcript, give the context a second named list rather than sharing this
    one - "which files did --deep re-read" must keep exactly one possible answer.
    """

    conn: sqlite3.Connection
    archive_root: Path
    thumbs_dir: Path
    seed: int | None = None
    # Check 8's too (M13, H4): the share of files `--deep` re-reads, in percent.
    sample_percent: float = 1
    deep_sampled: list[str] = field(default_factory=list)


CheckFn = Callable[[CheckContext], CheckResult]


def _result(
    key: str,
    name: str,
    findings: list[str],
    *,
    status: str,
    fix: str = "",
    note: str = "",
) -> CheckResult:
    """The single constructor, so no check can report a count its details contradict.

    A finding carries EXACTLY ONE of `fix` and `note`, asserted here rather than left to
    each check: a finding with neither is the dead end UX 1.4 forbids, and one with both
    would print two competing next steps for the same line.
    """
    if not findings:
        return CheckResult(key=key, name=name, status=STATUS_PASS, count=0)
    assert bool(fix) != bool(note), (
        f"check {key} must give a finding either a fix command or a note saying none is"
        " needed, and never both"
    )
    return CheckResult(
        key=key,
        name=name,
        status=status,
        count=len(findings),
        details=tuple(findings),
        fix=fix,
        note=note,
    )


# --- check 1: the catalog file itself ------------------------------------------------


def check_1_catalog_file(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 1: `PRAGMA integrity_check` + `PRAGMA foreign_key_check`."""
    findings = repo.pragma_problems(ctx.conn)
    return _result("1", "the catalog file itself", findings, status=STATUS_ERROR, fix=FIX_SCAN)


# --- check 2: orphans ----------------------------------------------------------------


def check_2a_orphaned_rows(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 2, first half: `capture`/`file_events`/`plan_items` orphans."""
    findings = [
        f"a {table} row (key {key}) describes a file pam has no record of at all"
        for table, key in repo.orphan_rows(ctx.conn)
    ]
    return _result(
        "2a",
        "records pointing at files pam has no row for",
        findings,
        status=STATUS_ERROR,
        fix=FIX_SCAN,
    )


def check_2b_inert_facts(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 2, second half - **inert, not an error**, because the spec says so.

    `face_instances` and `inferred_facts` are keyed by content hash rather than by file
    id precisely so the work survives a file going away and coming back. An entry whose
    hash is not in the catalog today costs nothing and will be reused exactly as it
    stands if that file returns, so this is reported and never counted as damage.
    """
    findings = [
        f"a {table} row is kept for content pam cannot currently see ({sha256[:12]}...);"
        " nothing is wrong - it will be reused if that file comes back"
        for table, sha256 in repo.inert_fact_rows(ctx.conn)
    ]
    return _result(
        "2b",
        "AI results kept for files pam cannot see",
        findings,
        status=STATUS_WARN,
        fix=FIX_SCAN,
    )


# --- check 3: companion groups -------------------------------------------------------


def check_3_companion_groups(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 3: a group with a missing member or a missing primary.

    A companion group exists to bind at least two files under one stem (spec 5.4), so a
    group holding fewer than two has lost a member; and the primary - the member whose
    facts drive the name - must be one of them. Both are unreachable through `pam scan`,
    which clears and rebuilds every group from scratch, which is also why `pam scan` is
    the fix: it discards whatever is there and recomputes it from the disk.
    """
    findings = []
    for group in repo.companion_group_health(ctx.conn):
        if group.member_count < 2:
            findings.append(
                f"companion group {group.group_id} ({group.kind}) has"
                f" {group.member_count} member(s); a group binds at least two files"
            )
        if not group.primary_is_member:
            findings.append(
                f"companion group {group.group_id} ({group.kind}) names file"
                f" {group.primary_file_id} as the one its name comes from, but that file"
                " is not one of its members"
            )
    return _result("3", "companion groups", findings, status=STATUS_ERROR, fix=FIX_SCAN)


# --- check 4: the journal and the plans ----------------------------------------------


def check_4a_intent_rows(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 4, clause 1 (spec 7.0 J7): nothing may be left in `intent`.

    Spec 7.2's recovery resolves every `intent` row and runs at the start of every
    archive-scoped command - including this one, a few lines before these checks. So a
    row still here has survived a recovery pass.

    THIS CHECK HAS NO FIRE PATH THROUGH `pam verify`, AND THAT IS CORRECT. An earlier
    version of this docstring said the reportable state was reachable through spec 7.0
    J9, with the archive root not visible. It is not: `paths.resolve_archive` refuses
    an unreachable archive at the front door of every archive-scoped command, so the
    command never reaches recovery, let alone these checks. Corrected at the M6 Task 3
    spec review, which reproduced it rather than reading it.

    It stays, because spec 7.5's own parenthetical - "recovery should have resolved
    them" - makes this an assertion that recovery did its job, which is what a `git
    fsck` is for. It is a standing guard on J7, driven at the function level by its own
    test rather than end to end. `pam scan` is the fix because running it is what gives
    recovery another pass.
    """
    findings = [
        f"{row.old_rel_path} -> {row.new_rel_path} was journaled and never resolved"
        f" (session {row.session_id})"
        for row in repo.rows_in_state(ctx.conn, "intent")
    ]
    return _result("4a", "renames left mid-flight", findings, status=STATUS_ERROR, fix=FIX_SCAN)


def check_4b_applying_plans(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 4, clause 2 (spec 7.0 J10): no plan may be left `applying`.

    `applying` means a run is in progress, and recovery holds the workspace lock, so a
    plan it finds in that status belongs to a process that is gone and phase 3 gives it
    a terminal status. As with clause 1, a plan still here outlived a recovery pass, and
    as with clause 1 that state has no fire path through `pam verify` - `resolve_archive`
    forecloses J9 before recovery is reached. A standing guard on J10, driven at the
    function level. See `check_4a_intent_rows` for the full correction.
    """
    findings = [
        f"plan {plan_id} is still marked as being applied, but no run is in progress"
        for plan_id in repo.plans_in_status(ctx.conn, "applying")
    ]
    return _result("4b", "plans left mid-apply", findings, status=STATUS_ERROR, fix=FIX_SCAN)


# Spec 7.4 rev 9 point 2, as a table: the `plan_items.status` a resolution writes, by
# journal op and journal state. `reverted` and `failed` share a row because both mean
# "the file is where it was before this operation tried" - which for a `rename` is the
# original name (`failed`) and for an `undo_rename` is the applied name (`applied`).
#
# DELIBERATELY A SECOND, INDEPENDENT COPY of the maps `execute/journal_ops.py` writes
# from. This check exists to notice the catalog disagreeing with the protocol, and a
# checker that computed "what should be there" by importing the very map the writer
# used could not see the writer being wrong - it would agree with any bug in it,
# perfectly. That is the same ruling the M5 review made about the supersede oracle
# (m5.md open question 13): keep an independent second implementation. The cost is
# real and is accepted: if the two ever drift, this check reports every resolved item
# in the archive, which is loud rather than silent, and is the right way round.
_EXPECTED_ITEM_STATUS = {
    ("rename", "done"): "applied",
    ("rename", "failed"): "failed",
    ("rename", "reverted"): "failed",
    ("undo_rename", "done"): "undone",
    ("undo_rename", "failed"): "applied",
    ("undo_rename", "reverted"): "applied",
}


def check_4c_item_status_agreement(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 4, clause 3: item statuses that disagree with journal outcomes.

    The journal is the record of what pam did; `plan_items.status` is a summary of where
    each file now stands. They are written in one transaction (spec 7.0 J5), so a
    disagreement means something wrote one without the other - and it is the summary
    that a user, a report and M7's review UI all read.
    """
    findings = []
    for outcome in repo.item_outcomes(ctx.conn):
        expected = _EXPECTED_ITEM_STATUS.get((outcome.op, outcome.state))
        if expected is None:
            findings.append(
                f"{outcome.old_rel_path}: pam does not recognise the journal outcome"
                f" {outcome.op}/{outcome.state} recorded against it"
            )
        elif outcome.item_status != expected:
            findings.append(
                f"{outcome.old_rel_path}: pam's journal says {outcome.op}/{outcome.state},"
                f" which means {expected!r}, but the plan records {outcome.item_status!r}"
            )
    return _result(
        "4c",
        "what pam recorded against what it did",
        findings,
        status=STATUS_ERROR,
        fix=FIX_SCAN,
    )


# Spec 7.5 check 4's rev-10 clause names exactly these two plan statuses.
_TERMINAL_APPLIED_STATUSES = ("applied", "partially_applied")


def check_4d_stale_plan_status(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 4, clause 4 (rev 10): a plan `applied` whose files are all back.

    THE STATE (m5.md section 3.2). Undo writes the plan's status last, so there is a
    window after the final item resolution and before it. A crash there leaves every
    file back at its original name, every item `undone`, every journal row terminal -
    and the plan still reading `applied`. Nothing is lost and the disk is exactly where
    the user wants it; one status field is stale. Spec 7.2's recovery is deliberately
    inert here (J10 confines phase 3 to `applying`, which undo never sets), so the route
    out is the ordinary one and this check must name it: **a second `pam undo` of the
    same session**, which finds every item already `undone`, renames nothing, and writes
    the status.

    WHY THE PREDICATE ALSO REQUIRES AN `undone` ITEM, which spec 7.5's sentence does
    not say out loud. Read literally - "no plan in `applied`/`partially_applied` with no
    `applied` item left" - this fires on an ordinary, healthy outcome: an apply in which
    EVERY item failed leaves the plan `partially_applied` with zero `applied` items and
    zero `undone` ones (reproduced against an archive of real files before this was written). That
    plan is not stale, `pam undo` of its session would put nothing back, and reporting it
    would put a permanent false alarm on a correctly recorded failure. The word the spec
    uses is "left", which is the distinction: the items that WERE applied are no longer
    applied, and that is only true when at least one of them now reads `undone`.
    """
    findings = []
    # Every stale plan has its OWN session, so a single headline `Fix:` line cannot be
    # true of more than one of them. Only the headline reaches stdout - the per-plan
    # detail lines below go to the session log - so naming just the first would let a
    # user clear one plan and never learn the others exist. Found at the M6 Task 3 spec
    # review. Collected in order, de-duplicated, and rendered honestly below.
    commands: list[str] = []
    for plan in repo.plan_outcomes(ctx.conn, _TERMINAL_APPLIED_STATUSES):
        if plan.applied > 0 or plan.undone == 0:
            continue
        session_id = repo.rename_session_for_plan(ctx.conn, plan.plan_id)
        command = FIX_UNDO.format(session_id=session_id) if session_id else FIX_SCAN
        if command not in commands:
            commands.append(command)
        findings.append(
            f"plan {plan.plan_id} is still marked {plan.status}, but all"
            f" {plan.undone} of its files have been put back under their original"
            f" names. Nothing is wrong with your photos - run: {command}"
        )
    if len(commands) > 1:
        fix = (
            f"{commands[0]} - then re-run pam verify;"
            f" {len(commands):,} sessions each need their own undo"
        )
    else:
        fix = commands[0] if commands else ""
    return _result(
        "4d",
        "plans marked applied whose files are all back",
        findings,
        status=STATUS_ERROR,
        fix=fix,
    )


def check_4e_split_units(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 4, clause 5 (spec 7.0 J6): a companion group split across stems.

    A unit holding a `done` member beside a `failed` or `reverted` one has half a
    companion group under the new name and half under the old (vision S3). Spec 7.2's
    phase 2 repairs exactly this and runs before these checks, so anything reported here
    is a unit recovery has already looked at and could not honestly heal - something
    outside pam is holding the other name, or the file was edited since. **Reported for
    human attention, never repaired**: spec 7.5 says so, and this command could not
    repair it if it wanted to - the connection cannot write.
    """
    findings = [
        f"the files renamed together as {unit_key!r} in session {session_id} did not all"
        " end up under the same name; pam looked and could not put them back safely"
        for session_id, unit_key in repo.split_units(ctx.conn)
    ]
    return _result(
        "4e",
        "companion groups split across two names",
        findings,
        status=STATUS_ERROR,
        fix=FIX_SCAN,
    )


# --- checks 5-7 ----------------------------------------------------------------------


def check_5_supersede_chains(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 5: no cycles, and every `superseded_by` target exists.

    `inferred_facts.superseded_by` is written by re-enrichment (spec 8.1, M9), so on any
    archive pam has built so far this table is empty and the check stands guard rather
    than reporting. A cycle would make "the current version of this fact" unanswerable,
    and the walk below terminates on one rather than following it forever: every step
    either reaches a node it has not seen or stops.

    A cycle of N facts is reported N times, once from each member. That is deliberate -
    naming an arbitrary one of them as "the" cycle would hide the other N-1 rows a
    person has to look at.
    """
    edges = dict(repo.fact_supersede_edges(ctx.conn))
    known = repo.fact_ids(ctx.conn)
    findings = []
    for source, target in sorted(edges.items()):
        if target not in known:
            findings.append(
                f"AI result {source} says it was replaced by {target}, which is not there"
            )
    for source in sorted(edges):
        seen = {source}
        node = source
        while node in edges:
            node = edges[node]
            if node in seen:
                findings.append(
                    f"AI result {source} is replaced by a chain that leads back to itself"
                )
                break
            seen.add(node)
    return _result("5", "superseded AI results", findings, status=STATUS_ERROR, fix=FIX_SCAN)


def check_6_duplicate_paths(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 6: two catalog rows for one path, differing only in case.

    `files.rel_path` is UNIQUE, but SQLite's index is BINARY while Windows' filesystem
    is not, so two rows can legally describe one file that cannot exist twice on disk.
    That is catalog corruption, and it is reachable: it is why `journal_ops.py` carries
    a second layer of J11 protection.
    """
    findings = [
        f"pam has {len(paths)} records for one file, differing only in upper/lower case:"
        f" {', '.join(paths)}"
        for _key, paths in repo.duplicate_paths_nocase(ctx.conn)
    ]
    return _result(
        "6",
        "paths differing only in upper/lower case",
        findings,
        status=STATUS_ERROR,
        fix=FIX_SCAN,
    )


def _thumb_key(filename: str) -> str | None:
    """The sha256 a thumbnail filename is keyed by, or None if it is not keyed by one.

    Spec 3 says the cache is "keyed by sha256"; spec 8.2 REV 12 says the full key is
    sha256 + variant + extractor version, and amendment A2 makes "the filename begins
    with the sha256 and nothing else" normative BECAUSE of this function. Reading the
    leading 64 hex characters is therefore the whole key-reading rule, and everything
    after them is the rest of the key, which this check has no reason to parse.

    (Through M6 this docstring said the key was sha256 + size + orientation + generator
    version. That was rev 2's wording, written when M7 was going to GENERATE a 256px
    JPEG; A2 replaced generation with extraction and the key with the one above. The
    code never depended on either spelling - it reads the leading 64 characters - which
    is exactly how the sentence went on describing a key that does not exist.)
    """
    head = filename[:64].lower()
    if len(head) == 64 and all(c in "0123456789abcdef" for c in head):
        return head
    return None


def check_7_thumbnail_cache(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 7: cached thumbnails keyed to a hash pam does not know.

    **Informational, never an error** (spec 7.5's own word). A stale thumbnail wastes a
    few kilobytes under the workspace and nothing else; it is not in the archive, it is
    not a photograph, and M7 will regenerate whatever it needs. The cache is a directory
    of files rather than a table (spec 3), so this is the one check that reads the disk.

    THIS CHECK NAMES NO FIX COMMAND, and that is deliberate. It used to say `pam scan`,
    which provably does not help: nothing anywhere in `pam/` removes a thumbnail-cache
    entry, so the finding survived its own fix forever and the archive kept exiting 1
    with an instruction that changed nothing. Driven end to end at the M6 Task 3 spec
    review - verify, scan, verify, identical report. UX 1.4 is "never a dead end", and a
    command that does not work is a worse dead end than none: the user follows it, sees
    no change, and stops trusting the checker. It says the finding is harmless instead.

    Pending a spec amendment: 7.5 currently mandates a fix command for every finding
    without requiring that it help. The design model ratifies the wording; the behaviour
    is fixed here because shipping a checker that lies about its own remedy is worse
    than shipping ahead of the sentence that blesses it.
    """
    findings = []
    if ctx.thumbs_dir.is_dir():
        known = repo.known_sha256(ctx.conn)
        for path in sorted(ctx.thumbs_dir.rglob("*")):
            if not path.is_file():
                continue
            key = _thumb_key(path.name)
            if key is None or key not in known:
                findings.append(
                    f"the cached thumbnail {path.name} belongs to no file pam knows about"
                )
    return _result(
        "7",
        "the thumbnail cache",
        findings,
        status=STATUS_WARN,
        note=(
            "harmless - these are cached previews, not photographs, and no pam command"
            " removes them. Nothing to do."
        ),
    )


# --- check 8: the deep re-hash (`--deep` only) ---------------------------------------

# Spec 7.5 check 8: "a random 1% sample". A percentage and a floor, written as two
# constants because they are two decisions and only the first one is the spec's.
DEEP_SAMPLE_PERCENT = 1
# THE FLOOR, AND WHY IT IS 1 RATHER THAN 0. Before M13 the size was `n * 1 // 100`, and
# `50 * 1 // 100` is **0**. Under H4's ceiling no `0 < percent` reaches the floor for a
# non-empty archive, so today it binds only a direct caller passing `percent <= 0`; it is
# kept because H4 spells it (`max(1, ...)`), and a check that samples zero files
# passes every single time without opening anything. That is a check that never ran
# reported as a check that passed (vision principle 9), and it would be the ORDINARY
# case rather than an edge one: every archive under 100 files. So whenever there is
# anything at all to sample, at least one file is re-read. An archive with no hashed,
# present, non-placeholder file samples nothing, and the check's own name says "0
# file(s)" out loud rather than letting a green line imply bytes were compared.
DEEP_SAMPLE_FLOOR = 1


def deep_sample(
    candidates: list[repo.RehashCandidate],
    seed: int | None,
    percent: float = DEEP_SAMPLE_PERCENT,
) -> list[repo.RehashCandidate]:
    """The `percent` % of `candidates` check 8 will re-read, drawn reproducibly from `seed`.

    A named function rather than three lines inside the check, because the SIZE of this
    list is the property the check lives or dies by, and a test has to be able to assert
    it directly rather than infer it from an outcome. A sample that rounds to zero and a
    healthy archive produce the identical result.

    The size is `max(1, ceil(n * percent / 100))` (M13, H4). Exact rational arithmetic,
    not floats: `375 * 8.8 / 100` is 33.00000000000001 on this interpreter, whose ceiling
    is 34 rather than 33 - a file re-read that nobody asked for, and exactly the kind of thing that stops
    being right one refactor later. The float is read through its shortest decimal
    spelling, so `--sample 7` means seven percent.
    """
    if not candidates:
        return []
    exact = Fraction(str(percent)) * len(candidates) / 100
    size = max(DEEP_SAMPLE_FLOOR, math.ceil(exact))
    return random.Random(seed).sample(candidates, min(size, len(candidates)))


def check_8_deep_rehash(ctx: CheckContext) -> CheckResult:
    """Spec 7.5 check 8: re-hash a random sample (1% by default) and compare to the stored sha256.

    Bit rot, a sync client writing over a file, an editor saving in place - none of them
    change a filename, so nothing else in pam would ever notice. This is the only check
    that reads the ARCHIVE rather than the catalog beside it, and it reads it with the
    M2 streaming hasher (`scan.hashing.sha256_file`), never a second implementation.

    IT READS AND COMPARES; IT RECORDS NOTHING. `pam verify` is strictly read-only and
    `--deep` does not change that - the connection could not write if this wanted it to,
    and nothing here opens a file for anything but reading. `pam scan` is the fix, and
    it is a REAL repair here rather than the honest-next-step most fix lines in this
    module name: scan re-reads the file, records the bytes that are actually there now,
    and the finding is gone. What no command can do is give back the old bytes, which is
    why the detail line says the file CHANGED rather than that pam can restore it.

    A FILE THAT IS NO LONGER THERE IS NOT A FINDING OF THIS CHECK, and neither is one
    that cannot be opened. An unreadable file is not a modified file, and saying "the
    bytes changed" about bytes nobody managed to read would be a false alarm in the one
    check whose whole subject is trustworthiness. Spec 7.5 check 2 owns the catalog's
    relationship with files that have gone away, and `repo.rehash_candidates` has
    already excluded the `missing` rows and the cloud placeholders; the `OSError` here
    is for the file that went away between that query and this read.

    IT RECORDS WHAT IT SAMPLED, AND IT RECORDS IT BEFORE READING ANYTHING (HT-M6's H1).
    The line on stdout is a count, so on a passing run `--seed` was a promise with no
    observable consequence. The names go to `ctx.deep_sampled`, which `run_verify` writes
    to the session log. SAMPLED, not compared: a file that vanished between the query and
    the read is still a file this run drew, and a transcript that quietly dropped it would
    make the log disagree with the count beside it.
    """
    sample = deep_sample(repo.rehash_candidates(ctx.conn), ctx.seed, ctx.sample_percent)
    ctx.deep_sampled.extend(candidate.rel_path for candidate in sample)
    findings = []
    cloud_only = 0
    for candidate in sample:
        path = ctx.archive_root / candidate.rel_path
        # Design 7.5 check 8 rev 34 (V1): the candidates exclude the CATALOG's placeholders,
        # which is what the last scan saw. A file OneDrive freed since is skipped and counted
        # here, immediately before the read - reading it would hydrate it (vision R2). It
        # stays in `deep_sampled` (it was drawn), and a skip is not a finding.
        if walker.placeholder_now(path):
            cloud_only += 1
            continue
        try:
            found = sha256_file(path)
        except OSError:
            continue
        if found != candidate.sha256:
            findings.append(
                f"{candidate.rel_path} is not the file pam recorded: its contents have"
                " changed since pam last read it. The photograph on disk is whatever is"
                " there now - pam did not touch it - but pam's record of it is stale."
            )
    name = f"the bytes of {len(sample) - cloud_only:,} file(s) re-read from disk"
    if cloud_only:
        name += f"; {cloud_only:,} skipped because they became cloud-only after the last scan"
    return _result(
        "8",
        name,
        findings,
        status=STATUS_ERROR,
        fix=FIX_SCAN,
    )

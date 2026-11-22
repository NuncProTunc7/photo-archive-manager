"""`pam verify` - every check proven to FIRE, and proven to stay quiet (spec 7.5).

THE ACCEPTANCE CRITERION OF M6 TASK 3, and the reason it is written this way. A
checker's tests are written by reading the thing they check, which is the one place a
test most easily becomes a restatement of the code. Two rules keep that from happening
here:

1. **Every corruption is written with the SUITE'S OWN SQL** (`_raw` below), never
   through `pam/catalog/repo/integrity.py`. A fire-test that built its fixture with the
   production query would be asking that query to describe the state it is then asked to
   find - it would agree with any bug in it, perfectly. Same ruling as the supersede
   oracle (m5.md open question 13) and `tests/archives.py::open_plan`.
2. **Every archive is real**: built on a real temp filesystem, scanned by the real
   scanner through the fake exiftool, planned by the real planner. The corruption is
   then applied on top of a catalog pam itself wrote, so a check that only fires on
   hand-assembled rows cannot pass here.

The corrupting connection is a RAW `sqlite3.connect`, deliberately: `pam.catalog.db.
connect` sets `PRAGMA foreign_keys=ON`, which refuses exactly the orphan rows spec 7.5
check 2 exists to find. A checker has to be testable against states its own writer
cannot produce, because the states it exists to find are the ones nothing sane produced.

TWO CHECKS ARE DRIVEN END TO END RATHER THAN FROM HAND-BUILT ROWS, because for those
two a hand-built fixture would prove nothing:

  * **check 4d** (the rev-10 stale-status clause) runs the real crash from m5.md
    section 3.2 - a real apply, then a real undo crashed at `before_plan_finalize` - and
    asserts the state before checking it. Hand-writing "plan applied, items undone"
    would test that the check reads two columns, not that the state pam actually leaves
    is the state the check finds.
  * **check 5** builds a REAL cycle. A cycle-detection test that never builds a cycle
    proves nothing at all - the walk would pass it while looping forever on real input.
"""

import sqlite3

import pytest
from archives import approve_and_apply, companion_archive, planned

from pam import verify
from pam.catalog import repo
from pam.catalog.db import connect_readonly
from pam.execute.undo import run_undo
from pam.paths import workspace_if_exists

THREE = ("IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG")


# --- the harness ---------------------------------------------------------------------


def _ws(root):
    ws = workspace_if_exists(root)
    assert ws is not None, "the fixture archive must have been scanned"
    return ws


def _raw(root):
    """A connection that can write the corruption. THE SUITE'S OWN SQL, and no FK.

    Raw `sqlite3.connect` rather than `pam.catalog.db.connect` for two reasons, both
    load-bearing. Foreign keys are OFF by default here, and `connect()` turns them ON -
    so an orphan `capture` row, which is precisely what spec 7.5 check 2 exists to find,
    cannot be inserted through the production connection at all. And every statement
    written through this handle is the test's own, so no fire-test is circular.
    """
    conn = sqlite3.connect(_ws(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _context(root, *, seed=None):
    """A `CheckContext` shaped exactly as `run_verify` builds one: read-only.

    The checks are exercised through the same connection kind production gives them, so
    a check that only works against a writable handle fails here rather than in the
    field. The caller closes it.

    `seed` is check 8's and is None for every other check, exactly as production leaves
    it - a check that started reading it would be reading None everywhere else.
    """
    ws = _ws(root)
    return verify.CheckContext(
        conn=connect_readonly(ws.db_path),
        archive_root=root,
        thumbs_dir=ws.thumbs_dir,
        seed=seed,
    )


def _run(root, check, *, seed=None):
    """One check against one archive, through a read-only connection."""
    ctx = _context(root, seed=seed)
    try:
        return check(ctx)
    finally:
        ctx.conn.close()


def _healthy(tmp_path):
    """An archive of real files: three files, scanned and planned, nothing wrong with it."""
    root, _plan_id = planned(tmp_path, THREE)
    return root


def _assert_quiet(root, check, *, seed=None):
    """A check that found nothing says so in every field, not just in its status."""
    result = _run(root, check, seed=seed)
    assert result.status == verify.STATUS_PASS, result.details
    assert result.count == 0
    assert result.details == ()
    # An empty fix on a pass is what stops the printer offering a remedy for nothing;
    # `verify_summary_lines` only suppresses the Fix line when the status is `pass`.
    assert result.fix == ""
    assert result.note == "", "a check that found nothing has nothing to say about it"
    return result


def _assert_fires(root, check, *, status, count, key, seed=None):
    """A finding, with the three things a finding has to get right.

    The status, because `warn` and `error` are different exit codes and spec 7.5 says
    which checks are which; the count, because a check that reports "something is wrong"
    without saying how much is a check the user cannot act on; and a NEXT STEP, because
    UX 1.4 says a report is never a dead end.

    The next step is a fix command naming a REAL pam command - or, where no pam command
    can clear the finding, a note saying so. Never neither, and never both: `_result`
    enforces that, and `test_every_finding_offers_exactly_one_next_step` drives it across
    every check rather than trusting each one.
    """
    result = _run(root, check, seed=seed)
    assert result.key == key
    assert result.status == status, result
    assert result.count == count, result.details
    assert len(result.details) == count
    assert bool(result.fix) != bool(result.note), result
    if result.fix:
        _assert_fix_is_a_real_command(result.fix)
    return result


# Every command `pam --help` offers today. A fix line naming a command that does not
# exist - a milestone promise, or a typo'd `pam scann` - is a dead end dressed as
# guidance, and reading the group's own command table is the only way to notice one that
# stopped existing. This comment used to give `pam enrich` as the example of the first
# kind; M9 Task 12 built it, so the example is retired rather than the rule (and no
# check names it even now: see `verify/checks.py`'s Step 4 note for why).
def _assert_fix_is_a_real_command(fix: str) -> None:
    from pam.cli import cli

    assert fix, "a check that found something must name the command that fixes it"
    words = fix.split()
    assert words[0] == "pam", fix
    assert words[1] in cli.commands, f"{fix!r} names no pam command"


# --- check 1: the catalog file itself ------------------------------------------------


def test_check_1_fires_on_a_reference_the_database_itself_calls_broken(tmp_path):
    """`PRAGMA foreign_key_check`'s half, reached the only way it can be reached.

    The row is inserted with foreign keys OFF, which is what makes it possible at all -
    and is exactly the situation the check guards against: some other tool, an older
    pam, or a hand-edited catalog leaving a reference SQLite itself will not stand
    behind.
    """
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO capture (file_id, capture_dt_source, exif_json)"
            " VALUES (999001, 'exif', '{}')"
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(root, verify.check_1_catalog_file, status="error", count=1, key="1")
    assert "foreign_key_check" in result.details[0]
    assert "capture" in result.details[0]


def test_check_1_stays_quiet_on_a_healthy_archive(tmp_path):
    """Including the `ok` row `integrity_check` returns, which is the one row that is
    NOT a finding - reporting it would fail this check on every archive there is."""
    _assert_quiet(_healthy(tmp_path), verify.check_1_catalog_file)


class _PragmaStub:
    """A connection that answers check 1's two PRAGMAs with rows the test chooses.

    WHY A STUB HERE AND NOWHERE ELSE IN THIS FILE. Every other check is driven against a
    real corrupted catalog, and that is the right way round - a checker tested only
    against hand-assembled rows proves nothing about the states pam can actually be left
    in. `PRAGMA integrity_check` is the one finding that cannot be reached that way:
    producing genuine b-tree damage means writing bytes into the middle of a live SQLite
    file, which is flaky by construction and would test SQLite rather than pam.

    What is under test is not SQLite. It is the ONE LINE pam owns on this path -
    `if finding != "ok"` - which decides that exactly one of `integrity_check`'s possible
    answers is not a finding. A stub is what exercises that line in BOTH directions.
    """

    def __init__(self, integrity_rows, foreign_key_rows=()):
        self._rows = {
            "PRAGMA integrity_check": tuple(integrity_rows),
            "PRAGMA foreign_key_check": tuple(foreign_key_rows),
        }

    def execute(self, sql):
        assert sql in self._rows, f"check 1 ran a statement this stub does not answer: {sql}"
        return iter(self._rows[sql])


def _check_1_on(integrity_rows, tmp_path):
    ctx = verify.CheckContext(
        conn=_PragmaStub(integrity_rows), archive_root=tmp_path, thumbs_dir=tmp_path
    )
    return verify.check_1_catalog_file(ctx)


# What real `integrity_check` output looks like: one line per damaged page, none of them
# the word 'ok'.
_TORN = "*** in database main ***\nPage 4: btreeInitPage() returns error code 11"


def test_check_1_reports_what_integrity_check_calls_broken(tmp_path):
    """The HEADLINE half of spec 7.5 check 1, which the foreign-key fire-test cannot
    reach: `foreign_key_check` returns only problems, so it exercises no predicate at
    all. `integrity_check` returns a row on a HEALTHY database too, and pam's one line
    of judgement is which row that is."""
    result = _check_1_on([(_TORN,)], tmp_path)
    assert result.status == verify.STATUS_ERROR
    assert result.count == 1
    assert result.details == (f"integrity_check: {_TORN}",)
    _assert_fix_is_a_real_command(result.fix)


def test_check_1_says_nothing_about_the_ok_row_a_healthy_database_returns(tmp_path):
    """The other direction, in isolation. Without this pair the filter is exercised on
    one side only, and `if finding != "ok"` could be `if False` with nothing to say so -
    which is precisely what the mutation pass found before these tests existed."""
    result = _check_1_on([("ok",)], tmp_path)
    assert result.status == verify.STATUS_PASS
    assert result.count == 0
    assert result.details == ()


def test_check_1_reports_every_broken_row_not_only_the_first(tmp_path):
    """`integrity_check` returns one row per problem and pam reports all of them.

    The middle row is `'ok, but'`: only the row that is EXACTLY 'ok' is healthy, so a
    filter written as `startswith('ok')` or `'ok' not in finding` would drop a real
    finding while passing every other test here.
    """
    rows = [(_TORN,), ("ok, but 2 pages are unreferenced",), ("row 9 missing",)]
    result = _check_1_on(rows, tmp_path)
    assert result.count == 3, result.details
    assert result.details[1] == "integrity_check: ok, but 2 pages are unreferenced"


# --- check 2a: orphaned rows ---------------------------------------------------------


def test_check_2a_fires_for_every_one_of_the_three_tables_spec_7_5_names(tmp_path):
    """spec 7.5 check 2 names `capture`, `file_events` AND `plan_items`.

    All three in one test on purpose: the production side is a MAP of table to query,
    and a map is the shape where one entry silently going missing costs a whole table's
    coverage while the other two keep the check green. Three findings, one per table.
    """
    root, plan_id = planned(tmp_path, THREE)
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO capture (file_id, capture_dt_source, exif_json)"
            " VALUES (999001, 'exif', '{}')"
        )
        conn.execute("INSERT INTO file_events (file_id, event_id) VALUES (999002, 1)")
        conn.execute(
            "INSERT INTO plan_items (plan_id, file_id, old_rel_path, unit_key,"
            " generated_filename, new_filename, tokens_json, tier, trust_score,"
            " trust_reasons_json, status)"
            " VALUES (?, 999003, 'gone.JPG', 'gone', 'x.JPG', 'x.JPG', '{}', 'high',"
            " 100, '[]', 'proposed')",
            (plan_id,),
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_2a_orphaned_rows, status="error", count=3, key="2a"
    )
    reported = " ".join(result.details)
    for table in ("capture", "file_events", "plan_items"):
        assert table in reported, f"{table} orphan was not reported"


def test_check_2a_stays_quiet_on_a_healthy_archive(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_2a_orphaned_rows)


def test_check_2a_does_not_fire_for_a_file_marked_missing(tmp_path):
    """A `missing` file keeps its row and its facts so it can come back (spec 4).

    This is the false alarm the check is one word away from: reading "without a live
    `files` row" as "without a `present` one" would fire on every archive whose owner
    has moved a folder, and the fix line would tell them to re-scan something that is
    already correct.
    """
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        conn.execute("UPDATE files SET status = 'missing'")
        conn.commit()
    finally:
        conn.close()
    _assert_quiet(root, verify.check_2a_orphaned_rows)


# --- check 2b: inert AI results ------------------------------------------------------


def _insert_inert_facts(root, sha256):
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, model_name,"
            " model_version, generated_at) VALUES (?, 'caption', '{}', 'm', '1', 'now')",
            (sha256,),
        )
        conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " model_name, model_version) VALUES (?, '[]', X'00', 0.9, 'm', '1')",
            (sha256,),
        )
        conn.commit()
    finally:
        conn.close()


def test_check_2b_fires_as_a_warning_never_an_error(tmp_path):
    """spec 7.5 is explicit: "reported as inert, not an error".

    The status is the whole finding here. `error` and `warn` are different exit codes
    (2 and 1), so calling a cache entry for a file that may return an error would make
    `pam verify` fail a script over something that is not damage. The tables are empty
    until M9-M11, so the rows are hand-inserted, as the plan's Step 2 prescribes.
    """
    root = _healthy(tmp_path)
    _insert_inert_facts(root, "0" * 64)

    result = _assert_fires(root, verify.check_2b_inert_facts, status="warn", count=2, key="2b")
    assert result.status != verify.STATUS_ERROR
    reported = " ".join(result.details)
    for table in ("inferred_facts", "face_instances"):
        assert table in reported
    assert "nothing is wrong" in reported


def test_check_2b_stays_quiet_on_a_healthy_archive(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_2b_inert_facts)


def test_check_2b_does_not_fire_for_content_the_catalog_still_holds(tmp_path):
    """The other half of "inert": a fact keyed to a hash pam CAN see is just a fact."""
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        row = conn.execute("SELECT sha256 FROM files WHERE sha256 IS NOT NULL").fetchone()
        assert row is not None, "the scanner must have hashed something"
        known = row["sha256"]
    finally:
        conn.close()
    _insert_inert_facts(root, known)
    _assert_quiet(root, verify.check_2b_inert_facts)


# --- check 3: companion groups -------------------------------------------------------


def test_check_3_fires_when_a_group_has_lost_a_member(tmp_path):
    """A companion group binds at least two files under one stem (spec 5.4).

    Driven off a REAL group the scanner built, not an invented row: what is corrupted is
    one member's `files.companion_group_id`, which is where membership actually lives.
    """
    root, _plan_id = companion_archive(tmp_path)
    conn = _raw(root)
    try:
        conn.execute("UPDATE files SET companion_group_id = NULL WHERE rel_path = 'IMG_1.MOV'")
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_3_companion_groups, status="error", count=1, key="3"
    )
    assert "1 member(s)" in result.details[0]


def test_check_3_fires_when_the_primary_is_not_one_of_the_members(tmp_path):
    """The primary is the member whose facts drive the name (spec 5.4), so a primary
    outside the group means the group's stem comes from a file it does not contain."""
    root, _plan_id = companion_archive(tmp_path)
    conn = _raw(root)
    try:
        conn.execute("UPDATE companion_groups SET primary_file_id = 999001")
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_3_companion_groups, status="error", count=1, key="3"
    )
    assert "999001" in result.details[0]
    assert "not one of its members" in result.details[0]


def test_check_3_stays_quiet_on_a_healthy_companion_group(tmp_path):
    """The archive with a REAL group in it, so the quiet test has something to be quiet
    about - a check that never sees a group is quiet for the wrong reason."""
    root, _plan_id = companion_archive(tmp_path)
    conn = _raw(root)
    try:
        groups = conn.execute("SELECT COUNT(*) AS n FROM companion_groups").fetchone()["n"]
    finally:
        conn.close()
    assert groups == 1, "the fixture must actually contain a companion group"
    _assert_quiet(root, verify.check_3_companion_groups)


# --- check 4a: journal rows left in `intent` -----------------------------------------


def _first_item(root):
    """(plan_item_id, unit_key, old_rel_path) for one real plan item."""
    conn = _raw(root)
    try:
        row = conn.execute(
            "SELECT id, unit_key, old_rel_path FROM plan_items ORDER BY id LIMIT 1"
        ).fetchone()
        assert row is not None
        return row["id"], row["unit_key"], row["old_rel_path"]
    finally:
        conn.close()


def test_check_4a_fires_on_a_journaled_rename_nobody_resolved(tmp_path):
    """spec 7.0 J7 / spec 7.5 check 4 clause 1.

    The row is written by hand because pam cannot leave one: spec 7.2's recovery runs at
    the start of every archive-scoped command, `pam verify` included, so the only way an
    `intent` row survives to be reported is a recovery that could not run at all (J9,
    archive root not visible). Reaching that state through the filesystem would test J9,
    not this clause.
    """
    root = _healthy(tmp_path)
    item_id, _unit, old_rel_path = _first_item(root)
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO journal (session_id, plan_item_id, op, old_rel_path,"
            " new_rel_path, size, mtime_ns, state, intent_at)"
            " VALUES ('s-20260807-101010', ?, 'rename', ?, '2019-07-04_001.JPG', 10, 1,"
            " 'intent', 'now')",
            (item_id, old_rel_path),
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(root, verify.check_4a_intent_rows, status="error", count=1, key="4a")
    assert old_rel_path in result.details[0]
    assert "s-20260807-101010" in result.details[0]


def test_check_4a_stays_quiet_on_a_healthy_archive(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_4a_intent_rows)


def test_check_4a_stays_quiet_after_a_real_apply(tmp_path):
    """The realistic quiet case: an apply writes `intent` rows for every file and
    resolves every one of them. A check that fired here would fire on every archive
    anyone has ever renamed."""
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (3, 0), applied.failures
    _assert_quiet(root, verify.check_4a_intent_rows)


# --- check 4b: plans left `applying` -------------------------------------------------


def test_check_4b_fires_on_a_plan_left_mid_apply(tmp_path):
    """spec 7.0 J10 / spec 7.5 check 4 clause 2. Same reasoning as 4a: recovery's phase
    3 finalizes every `applying` plan and runs one function call before these checks,
    so the reportable state is one recovery could not reach."""
    root, plan_id = planned(tmp_path, THREE)
    conn = _raw(root)
    try:
        conn.execute("UPDATE plans SET status = 'applying' WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_4b_applying_plans, status="error", count=1, key="4b"
    )
    assert str(plan_id) in result.details[0]


def test_check_4b_stays_quiet_over_every_other_plan_status(tmp_path):
    """`applying` is the ONLY status this clause reports, and the quiet case has to
    include the statuses that look alike: `open` before an apply, `applied` after one,
    and `undone` after an undo. A clause widened by one word would fire on all three."""
    root, plan_id = planned(tmp_path, THREE)
    _assert_quiet(root, verify.check_4b_applying_plans)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    _assert_quiet(root, verify.check_4b_applying_plans)
    run_undo(applied.session_id, root)
    _assert_quiet(root, verify.check_4b_applying_plans)


# --- check 4c: item status against journal outcome -----------------------------------


def test_check_4c_fires_when_an_item_disagrees_with_what_the_journal_says(tmp_path):
    """spec 7.5 check 4 clause 3, driven off a REAL apply.

    The journal rows are pam's own, written by a real rename; only the summary column is
    moved. That is the defect the clause exists for - the journal is the record of what
    pam did, `plan_items.status` is what a user, a report and M7's review UI read, and
    they are written in one transaction (J5) precisely so they cannot part company.
    """
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (3, 0), applied.failures

    conn = _raw(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = 'failed' WHERE id ="
            " (SELECT MIN(id) FROM plan_items WHERE plan_id = ?)",
            (plan_id,),
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_4c_item_status_agreement, status="error", count=1, key="4c"
    )
    assert "rename/done" in result.details[0]
    assert "'applied'" in result.details[0]
    assert "'failed'" in result.details[0]


def test_check_4c_fires_on_a_journal_outcome_pam_does_not_recognise(tmp_path):
    """The other branch: an op/state pair the protocol has no rule for.

    Unreachable through pam - both vocabularies are closed and validated on the write
    path - which is exactly why the checker must not assume it. A pair it cannot map is
    reported rather than skipped, because skipping it would silently drop the item from
    a check whose whole job is to notice a disagreement.
    """
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures

    item_id, _unit, old_rel_path = _first_item(root)
    conn = _raw(root)
    try:
        # A LATER row than the apply's, so it is the newest resolution for that item.
        conn.execute(
            "INSERT INTO journal (session_id, plan_item_id, op, old_rel_path,"
            " new_rel_path, size, mtime_ns, state, intent_at, resolved_at)"
            " VALUES ('s-20260807-121212', ?, 'rename', ?, 'x.JPG', 10, 1,"
            " 'half_done', 'now', 'now')",
            (item_id, old_rel_path),
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_4c_item_status_agreement, status="error", count=1, key="4c"
    )
    assert "does not recognise" in result.details[0]
    assert "rename/half_done" in result.details[0]


def test_check_4c_stays_quiet_after_a_real_apply_and_a_real_undo(tmp_path):
    """Both maps exercised for real: `rename/done -> applied` from the apply and
    `undo_rename/done -> undone` from the undo, over the same three items.

    This is the quiet test that matters most, because `_EXPECTED_ITEM_STATUS` is a
    deliberate SECOND copy of the map `journal_ops.py` writes from. If the two ever
    drift, this test is where it shows up - loudly, on an archive where nothing is
    wrong at all.
    """
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    _assert_quiet(root, verify.check_4c_item_status_agreement)

    undone = run_undo(applied.session_id, root)
    assert (undone.restored, undone.failed) == (3, 0), undone.failures
    _assert_quiet(root, verify.check_4c_item_status_agreement)


# --- check 4d: the rev-10 stale plan status ------------------------------------------


def _plan_status(conn, plan_id):
    """The plan's status, read with the suite's own SQL."""
    row = conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return None if row is None else row["status"]


def test_check_4d_fires_on_the_real_state_a_crash_before_undos_finalize_leaves(
    tmp_path, set_fault
):
    """spec 7.5 check 4's rev-10 clause, driven END TO END from the real crash.

    THE SCENARIO IS m5.md section 3.2, the same one `test_faults_matrix.py::
    test_a_crash_before_undos_finalize_leaves_a_plan_a_second_undo_finishes`
    manufactures: a real apply, then a real undo crashed at `before_plan_finalize` - the
    window after the last item resolution and before undo writes the plan's status.
    Every file is back at its original name, every item reads `undone`, every journal row
    is terminal, and one status field is stale.

    Hand-writing "plan `applied`, items `undone`" would prove the check reads two
    columns. This proves it finds the state pam's own crash actually leaves, which is the
    only claim worth making about a checker.

    Recovery is asserted INERT before the check runs (J10 confines spec 7.2 phase 3 to
    `applying`, which undo never sets). That is what makes this a `pam verify` finding at
    all: `pam verify` runs recovery itself, so a clause reporting a state recovery
    cleared would report nothing on a real run.
    """
    import logging

    from archives import catalog, digests, item_statuses

    from pam.execute.recover import recover

    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (3, 0), applied.failures
    renamed = digests(root)

    set_fault("crash:before_plan_finalize:1")
    with pytest.raises(SystemExit):
        run_undo(applied.session_id, root)
    set_fault.next_invocation()

    # The state, asserted rather than assumed - this is the fixture.
    conn = catalog(root)
    try:
        assert set(item_statuses(conn, plan_id).values()) == {"undone"}
        assert _plan_status(conn, plan_id) == "applied", (
            "the plan status is the one thing the crash left behind"
        )
        assert not recover(conn, root, logging.getLogger("tests.verify")).did_anything
        assert _plan_status(conn, plan_id) == "applied", "recovery is inert here (J10)"
    finally:
        conn.close()
    assert digests(root) != renamed, "every file is back under its original name"

    result = _assert_fires(
        root, verify.check_4d_stale_plan_status, status="error", count=1, key="4d"
    )
    # The fix this clause exists to name: a second `pam undo` of the session that
    # RENAMED the files, never of an undo (spec 7.4 rev 9 point 1).
    assert result.fix == f"pam undo {applied.session_id}"
    assert "3 of its files have been put back" in result.details[0]
    assert "Nothing is wrong with your photos" in result.details[0]

    # And the fix WORKS, which is what stops the fix line being decoration.
    second = run_undo(applied.session_id, root)
    assert (second.restored, second.failed) == (0, 0), second.failures
    _assert_quiet(root, verify.check_4d_stale_plan_status)


def test_check_4d_names_every_stale_sessions_undo_not_only_the_first(tmp_path, set_fault):
    """Two stale plans from two sessions, and only ONE `Fix:` line reaches stdout.

    Each stale plan has its own rename session, so no single command is true of both.
    The per-plan detail lines are correct and complete, but they go to the SESSION LOG -
    stdout gets one Fix line per check. Naming only the first session therefore let a
    user run it, watch one plan clear, and never learn the second existed.

    Found at the M6 Task 3 spec review by building this state; the check previously kept
    `fix = fix or command`, which takes the first and holds it. `CheckResult` carries one
    fix per check, so the honest shape inside that protocol is to name the first AND say
    how many sessions need their own - not to redesign the protocol here.
    """
    from pam.naming.planner import run_plan

    root, first_plan = planned(tmp_path, THREE)
    first = approve_and_apply(first_plan, root)
    assert (first.renamed, first.failed) == (3, 0), first.failures

    set_fault("crash:before_plan_finalize:1")
    with pytest.raises(SystemExit):
        run_undo(first.session_id, root)
    set_fault.next_invocation()

    # A second stale plan, built the same way: the files are back under their original
    # names, so a fresh plan proposes the same renames again.
    second_plan = run_plan(root).plan_id
    assert second_plan is not None and second_plan != first_plan
    second = approve_and_apply(second_plan, root)
    assert (second.renamed, second.failed) == (3, 0), second.failures
    assert second.session_id != first.session_id

    set_fault("crash:before_plan_finalize:1")
    with pytest.raises(SystemExit):
        run_undo(second.session_id, root)
    set_fault.next_invocation()

    result = _assert_fires(
        root, verify.check_4d_stale_plan_status, status="error", count=2, key="4d"
    )
    # Both sessions are named in the detail lines that reach the log...
    details = "\n".join(result.details)
    assert f"pam undo {first.session_id}" in details
    assert f"pam undo {second.session_id}" in details
    # ...and the single stdout line does not pretend one command finishes the job.
    assert result.fix.startswith(f"pam undo {first.session_id}")
    assert "2 sessions" in result.fix, result.fix

    # Both fixes work, and the check only goes quiet once BOTH have run - which is what
    # stops the count and the plural being decoration.
    assert run_undo(first.session_id, root).failed == 0
    _assert_fires(root, verify.check_4d_stale_plan_status, status="error", count=1, key="4d")
    assert run_undo(second.session_id, root).failed == 0
    _assert_quiet(root, verify.check_4d_stale_plan_status)


def test_check_4d_stays_quiet_across_a_whole_apply_and_undo(tmp_path):
    """The three healthy points on the path: planned, applied, and cleanly undone.

    The last is the one that matters - a completed undo leaves every item `undone` and
    zero `applied`, which is the finding's shape exactly, and is not a finding because
    the plan status moved to `undone` with it.
    """
    root, plan_id = planned(tmp_path, THREE)
    _assert_quiet(root, verify.check_4d_stale_plan_status)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    _assert_quiet(root, verify.check_4d_stale_plan_status)
    run_undo(applied.session_id, root)
    _assert_quiet(root, verify.check_4d_stale_plan_status)


def test_check_4d_does_not_fire_on_a_plan_only_half_put_back(tmp_path):
    """The OTHER operand of the rev-10 clause's guard, and the other false alarm.

    `if plan.applied > 0 or plan.undone == 0: continue` is a compound condition, and a
    whole-guard test credits it with kills its individual operands do not have. This is
    the case only `plan.applied > 0` refuses: a partial undo leaves some items `undone`
    and some still `applied`, and spec 7.4 deliberately leaves the plan status alone -
    a plan whose files are partly restored is not undone. Reporting it as stale would
    tell the user to re-run an undo that already failed for a reason.

    Reached for real: one file's original name is taken back before the undo, so
    `perform_verified_rename` refuses that unit (spec 7.0 J4) and the rest go home.
    """
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (3, 0), applied.failures

    (root / "IMG_1.JPG").write_bytes(b"something else is here now")
    undone = run_undo(applied.session_id, root)
    assert (undone.restored, undone.failed) == (2, 1), undone.failures

    conn = _raw(root)
    try:
        assert _plan_status(conn, plan_id) == "applied", "a partial undo leaves it alone"
        statuses = [r["status"] for r in conn.execute("SELECT status FROM plan_items")]
        assert sorted(statuses) == ["applied", "undone", "undone"], statuses
    finally:
        conn.close()

    _assert_quiet(root, verify.check_4d_stale_plan_status)


def test_check_4d_does_not_fire_on_an_apply_in_which_every_item_failed(tmp_path):
    """THE FALSE ALARM spec 7.5's sentence invites, reached for real.

    Read literally - "no plan in `applied`/`partially_applied` with no `applied` item
    left" - the clause fires on an apply where every single item failed: the plan is
    `partially_applied`, there are zero `applied` items, and nothing at all is wrong.
    `pam undo` of that session would put nothing back, so the fix line would be a lie
    printed forever against a correctly recorded failure.

    Every target name is occupied before the apply, so all three items fail for the
    ordinary reason (spec 7.1's absence check) rather than by fiat.
    """
    root, plan_id = planned(tmp_path, THREE)
    conn = _raw(root)
    try:
        targets = [
            r["new_filename"] for r in conn.execute("SELECT new_filename FROM plan_items")
        ]
    finally:
        conn.close()
    assert len(targets) == 3
    for name in targets:
        (root / name).write_bytes(b"in the way")

    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (0, 3), applied.failures

    conn = _raw(root)
    try:
        assert _plan_status(conn, plan_id) == "partially_applied"
        statuses = {r["status"] for r in conn.execute("SELECT status FROM plan_items")}
        assert statuses == {"failed"}, statuses
    finally:
        conn.close()

    _assert_quiet(root, verify.check_4d_stale_plan_status)


# --- check 4e: a companion unit split across two names -------------------------------


def test_check_4e_fires_on_a_unit_holding_a_done_member_beside_a_reverted_one(
    tmp_path, allow_split_unit
):
    """spec 7.0 J6 / spec 7.5 check 4 clause 5 - half a companion group under each stem.

    Built on a REAL applied unit: the scanner grouped the two files, the planner froze
    the `unit_key`, and a real apply wrote both journal rows `done`. One row is then
    moved to `reverted`, which is what a crash inside spec 7.3's revert loop leaves when
    the repair afterwards cannot honestly finish - something outside pam holding the
    other name. pam cannot produce this state; the world can, and pam's answer is to
    report it, so the test holds the sanctioned J6 opt-out to be able to reach it at all.
    """
    root, plan_id = companion_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (2, 0), applied.failures

    conn = _raw(root)
    try:
        conn.execute(
            "UPDATE journal SET state = 'reverted' WHERE id = (SELECT MIN(id) FROM journal)"
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(root, verify.check_4e_split_units, status="error", count=1, key="4e")
    assert applied.session_id in result.details[0]
    assert "did not all end up under the same name" in result.details[0]


def test_check_4e_stays_quiet_on_a_unit_that_applied_and_undid_cleanly(tmp_path):
    """A whole unit through apply and undo: four journal rows, two sessions, no split.

    `intent` is deliberately not part of J6's test - a crash between members legitimately
    leaves `done` beside `intent` - so a clause widened to it would fire mid-apply on
    every archive there is. The J6 tripwire in conftest asserts the rest suite-wide.
    """
    root, plan_id = companion_archive(tmp_path)
    _assert_quiet(root, verify.check_4e_split_units)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    _assert_quiet(root, verify.check_4e_split_units)
    run_undo(applied.session_id, root)
    _assert_quiet(root, verify.check_4e_split_units)


# --- check 5: supersede chains -------------------------------------------------------


def _insert_facts(root, rows):
    """(id, superseded_by) pairs straight into `inferred_facts`, the suite's own SQL."""
    conn = _raw(root)
    try:
        for fact_id, superseded_by in rows:
            conn.execute(
                "INSERT INTO inferred_facts (id, sha256, kind, value_json, model_name,"
                " model_version, generated_at, superseded_by)"
                " VALUES (?, ?, 'caption', '{}', 'm', '1', 'now', ?)",
                (fact_id, f"{fact_id:064d}", superseded_by),
            )
        conn.commit()
    finally:
        conn.close()


def test_check_5_fires_on_a_real_cycle_and_terminates(tmp_path):
    """A REAL three-fact cycle: 1 -> 2 -> 3 -> 1.

    A cycle-detection test that never builds a cycle proves nothing - the walk would
    pass it and loop forever on real input. That this test RETURNS is half its value;
    the other half is that all three members are named, because telling a person one
    arbitrary row of a cycle hides the two others they have to look at.
    """
    root = _healthy(tmp_path)
    _insert_facts(root, [(1, 2), (2, 3), (3, 1)])

    result = _assert_fires(
        root, verify.check_5_supersede_chains, status="error", count=3, key="5"
    )
    for fact_id in (1, 2, 3):
        assert (
            f"AI result {fact_id} is replaced by a chain that leads back to itself"
            in result.details
        )


def test_check_5_fires_on_a_self_referencing_fact(tmp_path):
    """The one-node cycle, which is the case an off-by-one in the walk gets wrong: the
    first step already lands back on the node it started from."""
    root = _healthy(tmp_path)
    _insert_facts(root, [(1, 1)])
    result = _assert_fires(
        root, verify.check_5_supersede_chains, status="error", count=1, key="5"
    )
    assert "leads back to itself" in result.details[0]


def test_check_5_fires_on_a_superseded_by_target_that_is_not_there(tmp_path):
    """The other half of spec 7.5 check 5: "`superseded_by` targets exist"."""
    root = _healthy(tmp_path)
    _insert_facts(root, [(1, 99)])
    result = _assert_fires(
        root, verify.check_5_supersede_chains, status="error", count=1, key="5"
    )
    assert "says it was replaced by 99, which is not there" in result.details[0]


def test_check_5_stays_quiet_on_a_healthy_chain(tmp_path):
    """A legitimate supersede chain 1 -> 2 -> 3 with 3 current. This is what M9's
    re-enrichment writes on every ordinary run, so a check that fired here would fail
    every archive that had ever been re-enriched."""
    root = _healthy(tmp_path)
    _insert_facts(root, [(1, 2), (2, 3), (3, None)])
    _assert_quiet(root, verify.check_5_supersede_chains)


def test_check_5_stays_quiet_on_an_archive_with_no_facts_at_all(tmp_path):
    """Every archive pam can build today, since `superseded_by` is written by M9."""
    _assert_quiet(_healthy(tmp_path), verify.check_5_supersede_chains)


# --- check 6: case-only duplicate paths ----------------------------------------------


def test_check_6_fires_on_two_rows_for_one_windows_file(tmp_path):
    """spec 7.5 check 6. `files.rel_path` is UNIQUE, but SQLite's index is BINARY and the
    Windows filesystem is not, so these two rows are perfectly legal and describe a file
    that cannot exist twice. `journal_ops.py` carries a second layer of J11 protection
    because of this; the check reports the ones that got in anyway."""
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,"
            " status, first_seen_at, last_scanned_at)"
            " VALUES ('img_1.jpg', 'img_1.jpg', '.jpg', 'image', 1, 1, 'present',"
            " 'now', 'now')"
        )
        conn.commit()
    finally:
        conn.close()

    result = _assert_fires(
        root, verify.check_6_duplicate_paths, status="error", count=1, key="6"
    )
    assert "IMG_1.JPG" in result.details[0]
    assert "img_1.jpg" in result.details[0]
    assert "2 records" in result.details[0]


def test_check_6_stays_quiet_on_a_healthy_archive(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_6_duplicate_paths)


def test_check_6_stays_quiet_after_a_real_apply_renames_every_file(tmp_path):
    """The realistic near-miss: an apply rewrites `files.rel_path` for every row, and
    Almost every file of a phone library changes their extension's case doing it.
    A rename that left the old row behind would land exactly here."""
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    _assert_quiet(root, verify.check_6_duplicate_paths)


# --- check 7: the thumbnail cache ----------------------------------------------------


def test_check_7_fires_as_information_never_as_an_error(tmp_path):
    """spec 7.5 check 7's own word is "informational", and the status is the finding.

    A stale thumbnail wastes a few kilobytes under the workspace. It is not in the
    archive, it is not a photograph, and M7 regenerates whatever it needs - so calling it
    an error would give `pam verify` exit 2 over something nobody has to act on.
    """
    root = _healthy(tmp_path)
    thumbs = _ws(root).thumbs_dir
    thumbs.mkdir(parents=True, exist_ok=True)
    (thumbs / f"{'a' * 64}.jpg").write_bytes(b"stale")
    (thumbs / "not-keyed-by-a-hash.jpg").write_bytes(b"stale")

    result = _assert_fires(
        root, verify.check_7_thumbnail_cache, status="warn", count=2, key="7"
    )
    assert result.status != verify.STATUS_ERROR
    reported = " ".join(result.details)
    assert f"{'a' * 64}.jpg" in reported
    assert "not-keyed-by-a-hash.jpg" in reported

    # NO FIX COMMAND, deliberately - see the check's docstring. `pam scan` was named here
    # and provably does not help, so the finding survived its own fix forever.
    assert result.fix == "", "no pam command clears a thumbnail-cache entry"
    assert "Nothing to do" in result.note


def test_check_7s_fix_would_not_have_worked_which_is_why_it_names_none(tmp_path):
    """The reproduction, kept as a test: verify -> scan -> verify, identical finding.

    This is the evidence for check 7 naming no command, and it belongs in the suite
    rather than in a review transcript, because the day someone gives check 7 a `Fix:`
    line back is the day this test should fail. It asserts the PROPERTY that justified
    the change - a scan does not clear the entry - not merely the current wording.
    """
    from archives import FAKE_COMMAND

    from pam.scan.runner import run_scan

    root = _healthy(tmp_path)
    thumbs = _ws(root).thumbs_dir
    thumbs.mkdir(parents=True, exist_ok=True)
    stale = thumbs / f"{'a' * 64}.jpg"
    stale.write_bytes(b"stale")

    before = _run(root, verify.check_7_thumbnail_cache)
    assert before.count == 1

    run_scan(root, exiftool_command=FAKE_COMMAND)

    assert stale.exists(), "pam scan does not remove a thumbnail-cache entry"
    after = _run(root, verify.check_7_thumbnail_cache)
    assert (after.count, after.details) == (before.count, before.details), (
        "the finding is identical after the command that used to be named as its fix"
    )


def test_every_finding_offers_exactly_one_next_step(tmp_path):
    """UX 1.4 across the whole checker, driven from `ALL_CHECKS` rather than written out.

    A finding with neither a fix nor a note is a dead end; one with both prints two
    competing next steps for the same line. Driven over every check the runner can hand
    out, so a check added in M7 is covered without this test being edited - the same
    shape as the log-path convention test.

    `ALL_CHECKS`, NOT `CHECKS`, since M6 Task 4's review. This test claimed to cover "a
    check added in M6 Task 4", and it did not: check 8 lives in `DEEP_CHECKS`, and a
    driver reading only `CHECKS` walked past it. Measured by mutation - giving check 8 a
    `note` in place of its `fix` left this test green. A guard that names its own reach
    has to actually have it, so the deep check is made to FIRE here too and both branches
    below now see all thirteen.
    """
    root = _healthy(tmp_path)
    thumbs = _ws(root).thumbs_dir
    thumbs.mkdir(parents=True, exist_ok=True)
    (thumbs / f"{'b' * 64}.jpg").write_bytes(b"stale")
    _rewrite_outside_pam(root / _sample_at(root, SEED)[0].rel_path)

    ws = _ws(root)
    reader = connect_readonly(ws.db_path)
    try:
        ctx = verify.CheckContext(
            conn=reader, archive_root=root, thumbs_dir=ws.thumbs_dir, seed=SEED
        )
        results = verify.run_checks(ctx, verify.ALL_CHECKS)
    finally:
        reader.close()

    assert len(results) == len(verify.ALL_CHECKS)
    findings = [r for r in results if r.status != verify.STATUS_PASS]
    assert findings, "the planted thumbnail must make at least one check report"
    for result in results:
        if result.status == verify.STATUS_PASS:
            assert result.fix == "" and result.note == "", result
        else:
            assert bool(result.fix) != bool(result.note), result
            if result.fix:
                _assert_fix_is_a_real_command(result.fix)


def test_check_7_stays_quiet_for_a_thumbnail_of_a_file_pam_knows(tmp_path):
    """Both spellings spec 8.2 allows: the bare sha256, and the sha256 with the rest of
    the cache key after it. Reading the leading 64 hex characters serves both and does
    not commit M7 to either, so both are pinned here - as is the case-insensitivity,
    since nothing says which case M7 will write."""
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        known = conn.execute(
            "SELECT sha256 FROM files WHERE sha256 IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()["sha256"]
    finally:
        conn.close()
    thumbs = _ws(root).thumbs_dir
    thumbs.mkdir(parents=True, exist_ok=True)
    (thumbs / f"{known}.webp").write_bytes(b"fresh")
    (thumbs / f"{known.upper()}_512_1_v1.webp").write_bytes(b"fresh")
    _assert_quiet(root, verify.check_7_thumbnail_cache)


def test_check_7_stays_quiet_on_a_healthy_archive_with_an_empty_cache(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_7_thumbnail_cache)


# --- check 8: the deep re-hash (`--deep` only) ---------------------------------------

# One fixed seed for every check-8 test that needs a reproducible draw. A literal rather
# than a fixture: the point of `--seed` is that a person can type the same number and see
# the same sample, and a test that hid it behind a fixture would not be reading it back.
SEED = 1234


def _rewrite_outside_pam(path):
    """Rewrite a file's bytes behind pam's back - bit rot, a sync client, an editor.

    THE ONE HELPER, SHARED BY THE FIRE-TEST AND THE QUIET-TEST, which is the M233 lesson
    from M6 Task 3 written into this file: a corruption helper used only by a test that
    asserts silence will keep that test passing after the helper is blinded, because an
    untouched archive is quiet too. Both directions run through here, so a helper that
    stopped changing anything fails the fire-test loudly instead of weakening the quiet
    one silently.

    The assertion is part of the helper for the same reason - it is what makes "blinded"
    impossible rather than merely unlikely.
    """
    before = path.read_bytes()
    path.write_bytes(before + b"\nsomething other than pam wrote to this file")
    assert path.read_bytes() != before, "the corruption helper changed nothing"
    return path


def _sample_at(root, seed):
    """Exactly the files check 8 will re-read at this seed, drawn the way it draws them.

    Through the production query and the production sampler, deliberately: what this has
    to predict is not "some file in the archive" but *the* file check 8 is about to open.
    Any second implementation of the ordering or the draw would make the fire-test below
    corrupt a file the check never looks at, and it would fail for the wrong reason.
    """
    ctx = _context(root, seed=seed)
    try:
        drawn = verify.deep_sample(repo.rehash_candidates(ctx.conn), seed)
    finally:
        ctx.conn.close()
    assert drawn, "the fixture archive has nothing check 8 could sample"
    return drawn


def _fake_candidates(n):
    """`n` candidates with distinct paths and hashes. Nothing is read from disk here -
    `deep_sample` is pure, and the SIZE of its answer is what these tests are about."""
    return [repo.RehashCandidate(rel_path=f"{i:05d}.jpg", sha256=f"{i:064x}") for i in range(n)]


# --- check 8's sample size: the vacuity trap this check exists to avoid ---------------


@pytest.mark.parametrize(
    ("candidates", "expected"),
    [
        (500, 5),  # the number the M6 plan names: 1% of 500
        (200, 2),
        (100, 1),
        (99, 1),  # ceil(0.99) = 1 (it was 0 under the pre-M13 floor division)
        (50, 1),  # the plan's own counter-example: 1% of 50 rounds to ZERO
        (2, 1),
        (1, 1),  # never more than there are
        (0, 0),  # nothing to sample, and the check says "0 file(s)" out loud
    ],
)
def test_the_deep_sample_is_one_percent_with_a_floor_of_one(candidates, expected):
    """THE MOST IMPORTANT ASSERTION IN CHECK 8, and it is about a number, not an outcome.

    A check that samples zero files opens nothing, compares nothing and passes every
    single time - and it passes identically on a healthy archive and on an archive where
    every file has been overwritten. No outcome-based test can tell those two apart, so
    the size is asserted directly. Under the pre-M13 `n * 1 // 100`, `50 * 1 // 100` was 0,
    and every archive under 100 files was in that case: the ORDINARY archive, not an edge one.
    """
    assert len(verify.deep_sample(_fake_candidates(candidates), SEED)) == expected


def test_the_sample_percentage_and_the_floor_are_two_separate_decisions():
    """Only the percentage is spec 7.5's. The floor is pam's, so it is written down
    separately and pinned separately - a reader who changes one must see the other."""
    assert verify.DEEP_SAMPLE_PERCENT == 1
    assert verify.DEEP_SAMPLE_FLOOR == 1


def test_the_sample_is_drawn_from_the_candidates_and_holds_no_duplicates():
    """`sample`, not `choices`: re-reading one file five times is not a 1% sample, and it
    would report the same finding five times.

    SWEPT OVER 500 SEEDS, and the seeds are `range(500)` rather than random ones, so this
    test is exactly as deterministic as a one-seed version - it just asks the question
    500 times instead of once. It has to. Found by mutation at the M6 Task 4 review: with
    a single seed this assertion is decorative, because drawing 5 of 500 **with**
    replacement collides only about 2 % of the time and `SEED = 1234` is one of the 98 %.
    The mutant (`sample` -> `choices`) passed the whole suite. Over `range(500)` it
    fails, and the chance of a with-replacement draw slipping through all of them is
    about 4e-5.
    """
    candidates = _fake_candidates(500)
    for seed in range(500):
        drawn = verify.deep_sample(candidates, seed)
        assert len(drawn) == len({c.rel_path for c in drawn}), (
            f"seed {seed} drew the same file twice - that is a draw with replacement,"
            " not a 1% sample"
        )
        for candidate in drawn:
            assert candidate in candidates


def test_the_same_seed_draws_the_same_sample_twice():
    """What `--seed` promises: a finding can be re-checked, and a test is deterministic."""
    candidates = _fake_candidates(500)
    assert verify.deep_sample(candidates, SEED) == verify.deep_sample(candidates, SEED)


def test_different_seeds_generally_draw_different_samples():
    """The other half. Without this, `deep_sample` returning `candidates[:5]` and
    ignoring the seed entirely would pass every other test in this section."""
    candidates = _fake_candidates(500)
    draws = {
        tuple(c.rel_path for c in verify.deep_sample(candidates, seed)) for seed in range(20)
    }
    assert len(draws) > 1, "the seed changed nothing - the sample is not random at all"


def test_an_unseeded_sample_is_still_drawn(monkeypatch):
    """`seed=None` is the ordinary run: `random.Random(None)` seeds itself from the OS,
    which is what spec 7.5's "a random 1% sample" means. It must not mean "no sample"."""
    assert len(verify.deep_sample(_fake_candidates(500), None)) == 5


# --- check 8: fires, stays quiet, and reads only what it sampled ----------------------


def test_check_8_fires_for_a_file_whose_bytes_changed_outside_pam(tmp_path):
    """Bit rot, a sync client writing over a file, an editor saving in place. None of
    them change a filename, so nothing else in pam would ever notice.

    The file corrupted is the one the seeded draw actually selects, so this is a real
    end-to-end fire rather than a lucky one.
    """
    root = _healthy(tmp_path)
    drawn = _sample_at(root, SEED)
    assert len(drawn) == 1, "the fixture is small, so the floor governs the sample"
    _rewrite_outside_pam(root / drawn[0].rel_path)

    result = _assert_fires(
        root, verify.check_8_deep_rehash, status="error", count=1, key="8", seed=SEED
    )
    assert drawn[0].rel_path in result.details[0]
    assert "changed since pam last read it" in result.details[0]
    # Controller ruling 2: check 8's fix is `pam scan`, and it is a REAL repair - scan
    # re-reads the file and records the bytes that are there now.
    assert result.fix == "pam scan"
    assert result.note == ""


def test_check_8_stays_quiet_on_a_healthy_archive(tmp_path):
    _assert_quiet(_healthy(tmp_path), verify.check_8_deep_rehash, seed=SEED)


def test_check_8_reads_only_the_files_it_sampled(tmp_path):
    """A file rewritten OUTSIDE the draw is not reported, and that is correct - spec 7.5
    check 8 is a 1% sample, not a full re-hash.

    This is also where the shared corruption helper earns its keep. The same
    `_rewrite_outside_pam` drives the fire-test above, so a helper that quietly stopped
    changing bytes could not leave this test passing against an untouched archive while
    the fire-test still failed - it would fail there first, and loudly.
    """
    root = _healthy(tmp_path)
    drawn = _sample_at(root, SEED)
    sampled = {candidate.rel_path for candidate in drawn}
    ctx = _context(root, seed=SEED)
    try:
        untouched = [c for c in repo.rehash_candidates(ctx.conn) if c.rel_path not in sampled]
    finally:
        ctx.conn.close()
    assert untouched, "the fixture must hold a file outside the sample"

    _rewrite_outside_pam(root / untouched[0].rel_path)
    _assert_quiet(root, verify.check_8_deep_rehash, seed=SEED)


def test_a_file_that_vanished_between_the_query_and_the_read_is_not_a_check_8_finding(
    tmp_path,
):
    """Spec 7.5 check 2 owns files that have gone away; check 8 owns files that changed.

    "The bytes changed" about bytes nobody managed to read would be a false alarm in the
    one check whose whole subject is trustworthiness - and it would fire on an archive on
    a drive that was unplugged, which is not damage at all.
    """
    root = _healthy(tmp_path)
    drawn = _sample_at(root, SEED)
    (root / drawn[0].rel_path).unlink()
    _assert_quiet(root, verify.check_8_deep_rehash, seed=SEED)


def test_a_non_oserror_from_the_hasher_is_never_swallowed_by_check_8(tmp_path, monkeypatch):
    """`except OSError`, and never `except Exception`. Found by mutation at the M6 Task 4
    review: widening that clause passed all 1089 tests.

    The narrow clause exists for ONE case - the file that went away between the query and
    the read - and the narrowness is the whole of it. Widened, a hasher that raised
    anything at all would be skipped in silence and check 8 would print "the bytes of N
    file(s) re-read from disk" in green having compared none of them: a check that did
    not run reading as a check that passed (vision principle 9), in the check most able
    to hide it.

    Both halves are driven. Out of the check the exception propagates; through
    `run_checks` it becomes a keyed `error` result naming check 8, which is the honest
    report - "this check could not run", never a pass.
    """
    root = _healthy(tmp_path)

    def _explode(path):
        raise ValueError("the hasher is broken")

    monkeypatch.setattr(verify.checks, "sha256_file", _explode)
    with pytest.raises(ValueError, match="the hasher is broken"):
        _run(root, verify.check_8_deep_rehash, seed=SEED)

    ctx = _context(root, seed=SEED)
    try:
        (result,) = verify.run_checks(ctx, (verify.check_8_deep_rehash,))
    finally:
        ctx.conn.close()
    assert result.status == verify.STATUS_ERROR, result
    assert "check_8_deep_rehash could not run" in result.name


def test_check_8_names_how_many_files_it_re_read_even_when_it_found_nothing(tmp_path):
    """A green line reading "the bytes of 1 file(s) re-read from disk" says something a
    bare "pass" does not: that bytes were actually compared, and how many."""
    result = _run(_healthy(tmp_path), verify.check_8_deep_rehash, seed=SEED)
    assert result.name == "the bytes of 1 file(s) re-read from disk"
    assert result.status == verify.STATUS_PASS


def test_check_8_uses_the_M2_streaming_hasher_and_not_a_second_one(tmp_path, monkeypatch):
    """One hasher in the project (`pam.scan.hashing.sha256_file`). A second
    implementation would compare a hash pam computes one way against a hash pam computed
    another, and would disagree with the scanner about a healthy file."""
    calls = []
    real = verify.checks.sha256_file

    def _record(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(verify.checks, "sha256_file", _record)
    _assert_quiet(_healthy(tmp_path), verify.check_8_deep_rehash, seed=SEED)
    assert len(calls) == 1, "check 8 did not read its sample through the M2 hasher"


def test_check_8_never_samples_a_placeholder_a_missing_file_or_an_unhashed_one(tmp_path):
    """`rehash_candidates`' three filters, each of which keeps check 8 from reporting a
    file it had no business reading.

    A cloud placeholder's bytes are not on this machine (spec 5.2), so reading one asks
    OneDrive to hydrate it - a read-only check pulling gigabytes down a metered line. A
    `missing` file has nothing to read. An unhashed file has nothing to compare against.
    Written with the suite's own SQL, so this is not the production query describing the
    state it is then asked to exclude.
    """
    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        before = {c.rel_path for c in repo.rehash_candidates(ctx.conn)}
    finally:
        ctx.conn.close()
    assert len(before) >= 3, before

    ordered = sorted(before)
    conn = _raw(root)
    try:
        conn.execute("UPDATE files SET placeholder = 1 WHERE rel_path = ?", (ordered[0],))
        conn.execute("UPDATE files SET status = 'missing' WHERE rel_path = ?", (ordered[1],))
        conn.execute("UPDATE files SET sha256 = NULL WHERE rel_path = ?", (ordered[2],))
        conn.commit()
    finally:
        conn.close()

    ctx = _context(root)
    try:
        after = {c.rel_path for c in repo.rehash_candidates(ctx.conn)}
    finally:
        ctx.conn.close()
    assert after == before - set(ordered[:3])


def test_the_rehash_candidates_come_back_in_rel_path_order(tmp_path):
    """`ORDER BY rel_path`, which is what makes `--seed` a promise SQLite keeps.

    `random.Random(seed).sample` draws from the SEQUENCE it is handed, so two runs that
    return the same rows in a different order draw a different sample from the same
    seed - and `--seed` exists so a user can re-check a finding. Found by mutation at the
    M6 Task 4 review: dropping the clause passed the whole suite, because SQLite happens
    to scan a small table in rowid order and the fixture's rowid order is already its
    `rel_path` order.

    So this test makes the two orders DISAGREE: rows inserted last that sort first. Ids
    follow insertion order, which is exactly what a re-scan of a moved archive does not
    preserve - the reason the ordering key is `rel_path` and not `id`.
    """
    root = _healthy(tmp_path)
    conn = _raw(root)
    try:
        for n, name in enumerate(("AAA_2.jpg", "AAA_1.jpg")):
            conn.execute(
                "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,"
                " sha256, status, first_seen_at, last_scanned_at)"
                " VALUES (?, ?, '.jpg', 'image', 1, 1, ?, 'present', 'now', 'now')",
                (name, name, f"{n:064x}"),
            )
        conn.commit()
    finally:
        conn.close()

    ctx = _context(root)
    try:
        paths = [candidate.rel_path for candidate in repo.rehash_candidates(ctx.conn)]
    finally:
        ctx.conn.close()
    assert paths[:2] == ["AAA_1.jpg", "AAA_2.jpg"], "inserted last, and they must sort first"
    assert paths == sorted(paths), paths


# --- check 8 is `--deep`'s alone -----------------------------------------------------


def test_check_8_is_in_the_deep_list_and_never_in_the_always_run_one():
    """The shape of the wiring, asserted rather than described. `CHECKS` is what always
    runs; `DEEP_CHECKS` is what `--deep` adds; `ALL_CHECKS` is their concatenation, so
    there is no third hand-written list to fall out of step with either."""
    assert verify.check_8_deep_rehash not in verify.CHECKS
    assert verify.DEEP_CHECKS == (verify.check_8_deep_rehash,)
    assert verify.ALL_CHECKS == verify.CHECKS + verify.DEEP_CHECKS


def test_without_deep_no_file_in_the_archive_is_ever_opened(tmp_path, monkeypatch):
    """Stronger than "check 8 is not in the results": the hasher itself is made to
    explode, so a plain `pam verify` that touched one archive file would fail here.

    The exit code is asserted too, because `run_checks` turns an exception into a calm
    `error` result keyed `?` - without this line a check 8 that ran and blew up would be
    swallowed into a finding and the key assertion alone would not see it.
    """

    def _forbidden(path):
        raise AssertionError(f"pam verify read {path} without --deep")

    monkeypatch.setattr(verify.checks, "sha256_file", _forbidden)
    summary = verify.run_verify(_healthy(tmp_path))

    assert summary.exit_code == 0, summary.results
    assert "8" not in {r.key for r in summary.results}
    assert len(summary.results) == len(verify.CHECKS)


def test_run_verify_with_deep_runs_the_thirteenth_check(tmp_path):
    summary = verify.run_verify(_healthy(tmp_path), deep=True, seed=SEED)
    assert summary.exit_code == 0, summary.results
    assert len(summary.results) == len(verify.ALL_CHECKS)
    assert summary.results[-1].key == "8"


def test_deep_is_still_strictly_read_only(tmp_path):
    """Controller ruling 3, and spec 7.5's headline property under the one flag that
    opens files in the archive. The archive's bytes and the catalog's bytes are both
    compared before and after - a checker that repaired something it found would be a
    second mutation path, and vision principle 1 allows exactly one."""
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    from archives import digests

    db_path = _ws(root).db_path
    before_db = db_path.read_bytes()
    before_disk = digests(root)

    summary = verify.run_verify(root, deep=True, seed=SEED)

    assert summary.exit_code == 0, summary.results
    assert db_path.read_bytes() == before_db, "verify --deep wrote to the catalog"
    assert digests(root) == before_disk, "verify --deep touched the archive"


# --- the runner: a check may never take the others down ------------------------------


def _boom(ctx):
    raise ValueError("the catalog is stranger than this check expected")


def test_a_check_that_raises_becomes_an_error_result_naming_it(tmp_path):
    """The one tool that must survive a corrupt catalog is the one that inspects it.

    Injected through `run_checks`' check-list parameter, which exists for exactly this:
    a real check made to raise on demand would need a hook in production code, and a
    test that monkeypatched one of the twelve would prove the patch, not the wrapper.
    """
    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        results = verify.run_checks(ctx, (_boom,))
    finally:
        ctx.conn.close()

    assert len(results) == 1
    assert results[0].status == verify.STATUS_ERROR
    assert "_boom" in results[0].name, "the result must name the check that produced it"
    assert results[0].count == 1
    assert results[0].details == (
        "ValueError: the catalog is stranger than this check expected",
    )
    _assert_fix_is_a_real_command(results[0].fix)


def test_a_raising_check_does_not_stop_the_checks_after_it(tmp_path):
    """The eleven checks after the crashed one are exactly what the user needs at that
    moment. A runner that let the first exception out would report nothing about them."""
    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        results = verify.run_checks(
            ctx, (verify.check_1_catalog_file, _boom, verify.check_6_duplicate_paths)
        )
    finally:
        ctx.conn.close()

    assert [r.key for r in results] == ["1", "?", "6"]
    assert [r.status for r in results] == ["pass", "error", "pass"]


@pytest.mark.parametrize("blow_up", [SystemExit(3), KeyboardInterrupt()])
def test_the_runner_never_swallows_a_ctrl_c_or_a_crash_fault(tmp_path, blow_up):
    """`Exception`, never `BaseException`. A `crash:` fault (SystemExit) and a real
    Ctrl+C are not findings about the catalog, and turning either into a calm `error`
    line would make an interrupted run look like a completed one with a problem."""

    def _interrupt(ctx):
        raise blow_up

    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        with pytest.raises(type(blow_up)):
            verify.run_checks(ctx, (_interrupt,))
    finally:
        ctx.conn.close()


def test_every_check_in_the_module_is_in_one_of_the_runners_lists():
    """`CHECKS` plus `DEEP_CHECKS` is the whole checker - the printer, the session log
    and the exit code all read whichever they are handed - so a check written and left
    out of both reports nothing at all.

    Derived from the module rather than written down twice: a hand-maintained expected
    list would be updated in the same edit that added the check, which is precisely the
    edit this guard exists to catch. Splitting the runner in two at M6 Task 4 could have
    lost that property; instead the guard now spans both tuples, and asserts no check is
    in both - a check registered twice would be run twice and reported twice.
    """
    defined = {
        name
        for name in dir(verify)
        if name.startswith("check_") and callable(getattr(verify, name))
    }
    assert {fn.__name__ for fn in verify.ALL_CHECKS} == defined
    assert len(verify.ALL_CHECKS) == len(defined), "a check is registered in both tuples"
    # And in spec 7.5's order, so the report reads down the spec.
    assert [fn.__name__ for fn in verify.ALL_CHECKS] == sorted(defined, key=_check_sort_key)


def _check_sort_key(name):
    """`check_4c_...` sorts after `check_4b_...` and before `check_5_...`.

    Numerically on the spec's number and then on the clause letter, because plain
    string order puts `check_10` before `check_2` - which is the sort M6 Task 4's
    check 8 would be the first to notice.
    """
    key = name.split("_")[1]
    return (int("".join(c for c in key if c.isdigit())), "".join(c for c in key if c.isalpha()))


# --- exit codes (spec 7.5, deliberately overriding spec 13) --------------------------


def _summary(*statuses):
    return verify.VerifySummary(
        results=[
            verify.CheckResult(key=str(i), name="x", status=status, count=1)
            for i, status in enumerate(statuses)
        ]
    )


def test_exit_code_is_zero_when_everything_passes():
    assert _summary("pass", "pass").exit_code == 0
    assert verify.VerifySummary().exit_code == 0


def test_exit_code_is_one_for_warnings_only():
    """The inert-facts and thumbnail-cache findings live here. Spec 7.5 gives them 1,
    not 2, which is the whole reason those two checks carry `warn` rather than `error`."""
    assert _summary("pass", "warn").exit_code == 1


def test_exit_code_is_two_when_anything_is_an_error():
    """2 even when warnings outnumber errors: the worst finding decides."""
    assert _summary("warn", "warn", "error").exit_code == 2
    assert _summary("error").exit_code == 2


def test_the_exit_codes_are_spec_7_5s_numbers():
    """Written out as constants so a reader who finds them surprising lands on the
    module docstring rather than on spec 13, which assigns 2 to a traceback."""
    assert (verify.EXIT_ALL_PASS, verify.EXIT_WARNINGS, verify.EXIT_ERRORS) == (0, 1, 2)


# --- the whole command ---------------------------------------------------------------


def test_run_verify_on_a_healthy_archive_passes_every_check(tmp_path):
    root = _healthy(tmp_path)
    summary = verify.run_verify(root)
    assert summary.exit_code == 0
    assert len(summary.results) == len(verify.CHECKS)
    assert [r.status for r in summary.results] == ["pass"] * len(verify.CHECKS)
    assert summary.archive_root == root
    assert summary.log_path is not None and summary.log_path.exists()


def test_run_verify_refuses_a_folder_pam_has_never_scanned(tmp_path):
    """UX 1.4: a refusal that does not say what to do next is a dead end."""
    from pam.errors import PamError

    unscanned = tmp_path / "not-an-archive"
    unscanned.mkdir()
    with pytest.raises(PamError) as caught:
        verify.run_verify(unscanned)
    assert "pam scan" in str(caught.value)


def test_run_verify_changes_nothing_it_looks_at(tmp_path):
    """Spec 7.5's headline property. The catalog's bytes are compared before and after,
    which is stronger than "the checks did not call a writer": it also covers the
    read-only connection being handed something that could write."""
    root, plan_id = planned(tmp_path, THREE)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0, applied.failures
    from archives import digests

    db_path = _ws(root).db_path
    before_db = db_path.read_bytes()
    before_disk = digests(root)

    verify.run_verify(root)

    assert db_path.read_bytes() == before_db, "verify wrote to the catalog"
    assert digests(root) == before_disk, "verify touched the archive"


def test_run_verify_reports_the_findings_a_corrupt_catalog_holds(tmp_path):
    """End to end through the real command: recovery runs, the read-only connection is
    opened, and both a warning and an error survive to the summary with the exit code
    spec 7.5 gives them."""
    root = _healthy(tmp_path)
    _insert_inert_facts(root, "0" * 64)  # warn
    conn = _raw(root)
    try:
        conn.execute("UPDATE companion_groups SET primary_file_id = 999001")
        conn.execute(
            "INSERT INTO companion_groups (id, primary_file_id, kind)"
            " VALUES (77, 999001, 'live_photo')"
        )
        conn.commit()
    finally:
        conn.close()

    summary = verify.run_verify(root)
    assert summary.exit_code == 2
    assert [r.key for r in summary.errors] == ["3"]
    assert [r.key for r in summary.warnings] == ["2b"]


def test_run_verify_writes_every_check_to_the_session_log_including_the_passes(tmp_path):
    """Spec 7.5: "details go to the session log". Passes too - "check 3 found nothing on
    2026-08-07" is what makes a later "check 3 found four things" mean anything."""
    root = _healthy(tmp_path)
    _insert_inert_facts(root, "0" * 64)
    summary = verify.run_verify(root)
    assert summary.log_path is not None
    logged = summary.log_path.read_text(encoding="utf-8")
    for check in verify.CHECKS:
        key = check.__name__.split("_")[1]
        assert f"verify check {key} " in logged, f"check {key} was not logged"
    assert "nothing is wrong - it will be reused" in logged, "the detail lines are missing"


# --- the report and the CLI ----------------------------------------------------------


def _cli_verify(root, *extra):
    """The real command through click's runner, with stderr kept SEPARATE.

    Separate because the one thing spec 13 requires unconditionally - the `Log:` line -
    goes to stderr, and a runner that merged the streams would let a `Log:` line printed
    on stdout pass every assertion here. Click 8.2 made separation the default and
    dropped the `mix_stderr` flag that used to request it.
    """
    from click.testing import CliRunner

    from pam.cli import cli

    return CliRunner().invoke(cli, ["verify", "--archive", str(root), *extra])


def test_the_cli_passes_deep_and_seed_through_to_run_verify(tmp_path, monkeypatch):
    """Both flags reach the core, asserted deterministically.

    `test_the_cli_exits_two_when_deep_finds_a_file_whose_bytes_changed` does drive the
    seed end to end, but it cannot be the only witness: its archive holds two candidates
    and check 8 samples one, so a CLI that dropped `--seed` fails it only **half the
    time**. Measured at the M6 Task 4 review - the same coin flip let the equivalent
    mutation one level down (`run_verify` dropping the seed) through the entire suite.
    A flaky kill is not a kill.
    """
    import pam.cli

    seen = {}

    def _capture(archive, *, deep=False, seed=None, sample=None):
        seen.update(archive=archive, deep=deep, seed=seed, sample=sample)
        return verify.VerifySummary(archive_root=archive)

    monkeypatch.setattr(pam.cli, "run_verify", _capture)
    result = _cli_verify(_healthy(tmp_path), "--deep", "--seed", str(SEED))

    assert result.exit_code == 0, result.output
    assert (seen["deep"], seen["seed"]) == (True, SEED), seen
    assert seen["sample"] is None, "no --sample given must reach run_verify as None"


def test_the_cli_exits_zero_and_prints_the_log_line_on_a_healthy_archive(tmp_path):
    """Spec 13's `Log:` line is UNCONDITIONAL - on stderr, on the run where nothing at
    all was wrong, which is the run where it is easiest to forget."""
    result = _cli_verify(_healthy(tmp_path))
    assert result.exit_code == 0, result.output
    assert "everything pam recorded looks right" in result.stdout
    assert "Nothing to do." in result.stdout
    assert result.stderr.startswith("Log: ") or "\nLog: " in result.stderr


def test_the_cli_exits_one_for_warnings_only(tmp_path):
    """Spec 7.5's middle code, reached through a real inert-facts finding."""
    root = _healthy(tmp_path)
    _insert_inert_facts(root, "0" * 64)
    result = _cli_verify(root)
    assert result.exit_code == 1, result.output
    assert "Done with problems." in result.stdout
    assert "1 error(s), 1 warning(s)" not in result.stdout
    assert "0 error(s), 1 warning(s)" in result.stdout
    assert "Log: " in result.stderr


def _corrupt_beyond_recovery(root):
    """A case-only duplicate `files` row - check 6's finding, and one spec 7.2's
    recovery does not touch.

    THE CORRUPTION HAS TO OUTLIVE RECOVERY, which is not a detail: `pam verify` runs
    recovery before its checks, so a plan set to `applying` by hand is FINALIZED by
    phase 3 and check 4b correctly reports nothing (pinned by
    `test_verify_runs_recovery_first_so_a_healable_state_is_healed_not_reported`).
    An end-to-end error test needs damage that is genuinely damage.
    """
    conn = _raw(root)
    try:
        conn.execute(
            "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,"
            " status, first_seen_at, last_scanned_at)"
            " VALUES ('img_1.jpg', 'img_1.jpg', '.jpg', 'image', 1, 1, 'present',"
            " 'now', 'now')"
        )
        conn.commit()
    finally:
        conn.close()


def test_verify_runs_recovery_first_so_a_healable_state_is_healed_not_reported(tmp_path):
    """Plan clarification 4, end to end: `pam verify` is archive-scoped, so spec 7.2's
    recovery runs before its checks - through a WRITE connection, inside the lock.

    A plan left `applying` is exactly what recovery's phase 3 finalizes (J10). Without
    recovery wired in, check 4b would report it as damage while the machinery that heals
    it sat one command away; with recovery wired in, `pam verify` reports a clean archive
    and says so. The one line of stderr recovery prints is the witness that it ran.
    """
    root, plan_id = planned(tmp_path, THREE)
    conn = _raw(root)
    try:
        conn.execute("UPDATE plans SET status = 'applying' WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()

    result = _cli_verify(root)
    assert result.exit_code == 0, result.output
    assert "interrupted plan(s) closed" in result.stderr, "recovery did not run"

    conn = _raw(root)
    try:
        assert _plan_status(conn, plan_id) != "applying"
    finally:
        conn.close()


def test_the_cli_exits_two_for_an_error(tmp_path):
    root = _healthy(tmp_path)
    _corrupt_beyond_recovery(root)
    result = _cli_verify(root)
    assert result.exit_code == 2, result.output
    assert "1 error(s)" in result.stdout
    assert "Fix: pam scan" in result.stdout
    assert "Log: " in result.stderr


def test_the_report_carries_a_symbol_for_every_status_not_only_a_colour(tmp_path):
    """UX 2: colour is never the only signal, and it is switched off entirely when
    stdout is not a TTY. The `ok` / `!` / `x` marks carry the report on their own."""
    root = _healthy(tmp_path)
    _insert_inert_facts(root, "0" * 64)  # a `!`
    _corrupt_beyond_recovery(root)  # an `x`

    summary = verify.run_verify(root)
    lines = verify.verify_summary_lines(summary)
    rendered = [line.text for line in lines]
    assert any(line.strip().startswith("ok ") for line in rendered)
    assert any(line.strip().startswith("! ") for line in rendered)
    assert any(line.strip().startswith("x ") for line in rendered)
    # Every non-pass line offers its fix; no pass line does.
    fixes = [line for line in rendered if "Fix:" in line]
    assert len(fixes) == len(summary.errors) + len(summary.warnings)


def test_a_passing_check_is_never_offered_a_fix(tmp_path):
    """A remedy printed under a check that found nothing is the report telling a user
    to act on an archive that is fine."""
    summary = verify.run_verify(_healthy(tmp_path))
    assert "Fix:" not in "\n".join(line.text for line in verify.verify_summary_lines(summary))


def test_deep_re_reads_files_from_disk_and_the_report_says_how_many(tmp_path):
    """Vision principle 9, now that the re-hash exists. The report used to carry a line
    saying `--deep is not available yet`, because the flag was accepted and ignored;
    M6 Task 4 built check 8 and deleted it. What `--deep` asserts here is true: a
    thirteenth line, naming the number of files whose bytes were actually compared."""
    result = _cli_verify(_healthy(tmp_path), "--deep", "--seed", str(SEED))
    assert result.exit_code == 0, result.output
    assert "--deep is not available yet" not in result.stdout
    assert "8  the bytes of 1 file(s) re-read from disk" in result.stdout
    assert "13 checks" in result.stdout


def test_the_cli_exits_two_when_deep_finds_a_file_whose_bytes_changed(tmp_path):
    """The whole command, seeded so the sample is the file the test corrupted: spec
    7.5's error code, the finding on stdout, and a fix line naming a real command."""
    root = _healthy(tmp_path)
    drawn = _sample_at(root, SEED)
    _rewrite_outside_pam(root / drawn[0].rel_path)

    result = _cli_verify(root, "--deep", "--seed", str(SEED))
    assert result.exit_code == 2, result.output
    assert "8  the bytes of 1 file(s) re-read from disk  - 1 found" in result.stdout
    assert "Fix: pam scan" in result.stdout
    assert "Log: " in result.stderr
    # Spec 7.5: "details go to the session log", and the name of the file whose bytes
    # changed is a detail. Read back from the log so the finding is proven to reach a
    # person somewhere, not merely to have been counted on stdout.
    logged = verify.run_verify(root, deep=True, seed=SEED).log_path.read_text(encoding="utf-8")
    assert drawn[0].rel_path in logged
    assert "changed since pam last read it" in logged


def _logged_sample(summary):
    """The files one run's session log says `--deep` re-read, in the order logged.

    Parsed out of the log text rather than read off the summary, because the summary is
    what production hands the logger: a test that read `summary.deep_sampled` and called
    it "what the log says" would pass with the logging call deleted.
    """
    assert summary.log_path is not None
    return [
        line.split("deep re-read: ", 1)[1]
        for line in summary.log_path.read_text(encoding="utf-8").splitlines()
        if "deep re-read: " in line
    ]


def test_a_passing_deep_run_names_the_files_it_sampled_in_the_log(tmp_path):
    """HT-M6 finding H1, and the passing run is the whole point of it.

    The owner ran `--deep` three times - seed 42 twice, then unseeded - and got three
    identical screens, because check 8 printed a count and nothing else. `--seed` is
    documented as the way to re-check a finding reproducibly, and on a healthy archive
    there was nothing to compare between two runs at all. So the sample reaches the
    session log, where spec 13 puts per-file detail, on a run where every check PASSES.

    The count on stdout is tied to the length of that list here rather than asserted
    separately: the line a user reads and the transcript they can open must not be able
    to disagree about how many files were re-read.
    """
    root = _healthy(tmp_path)
    drawn = [candidate.rel_path for candidate in _sample_at(root, SEED)]

    summary = verify.run_verify(root, deep=True, seed=SEED)

    assert summary.exit_code == verify.EXIT_ALL_PASS, summary.results
    # STRAIGHT OFF THE DISK, not through `_logged_sample` - the M7 Task 2 review's
    # finding (mutation M550). That helper is the ONLY thing in the suite that reads the
    # log for this, so re-pointing it at `summary.deep_sampled` - the very field it says
    # it is not reading - turns all four of its callers into tautologies, and the logging
    # call in `_log_results` can then be deleted with the whole suite green. Measured:
    # 1259 passed with `--deep` writing no transcript at all. One assertion that does not
    # go through the helper is what stops a single blinded oracle taking H1 with it.
    assert f"deep re-read: {drawn[0]}" in summary.log_path.read_text(encoding="utf-8")
    logged = _logged_sample(summary)
    assert logged == drawn
    assert summary.deep_sampled == drawn
    for rel_path in logged:
        assert (root / rel_path).is_file(), "the log named a file that is not there"
    (check_8,) = [result for result in summary.results if result.key == "8"]
    assert check_8.name == f"the bytes of {len(logged):,} file(s) re-read from disk"


def test_two_runs_at_the_same_seed_log_the_same_sample(tmp_path):
    """What `--seed` is for, now that a person can see it: the same number re-reads the
    same files, and the log is where they read that back.

    AN UNSEEDED RUN IS DELIBERATELY NOT ASSERTED AGAINST THESE (M353's lesson). This
    fixture has three candidates and check 8 draws one, so an unseeded draw matches the
    seeded one a third of the time - an inequality here would be a coin flip dressed as
    a test, which is worse than no test at all because it makes the suite flaky rather
    than wrong. `test_different_seeds_generally_draw_different_samples` covers that half
    deterministically, over `deep_sample` itself.
    """
    root = _healthy(tmp_path)

    first = _logged_sample(verify.run_verify(root, deep=True, seed=SEED))
    second = _logged_sample(verify.run_verify(root, deep=True, seed=SEED))

    assert first, "neither run logged a sample at all"
    assert first == second


def test_a_file_that_vanished_before_the_read_is_still_named_in_the_transcript(tmp_path):
    """The M7 Task 2 review's finding (mutation M546): SAMPLED, not compared.

    Check 8 records what it DREW, before it opens anything, and the count on stdout is
    `len(sample)` for the same reason. Move the recording inside the loop - so only the
    files that were successfully read are named - and every test above still passes,
    while a run in which one sampled file went away between the query and the read prints
    `the bytes of 1 file(s) re-read from disk` above a transcript naming none of them.
    That is the screen-versus-log disagreement H1 exists to close, reappearing in the
    fix for H1.

    The vanished file is not a FINDING - that is settled, and
    `test_a_file_that_vanished_between_the_query_and_the_read_is_not_a_check_8_finding`
    owns it. What is asserted here is that it is still in the transcript, and that the
    count beside the transcript still describes it.
    """
    root = _healthy(tmp_path)
    drawn = [candidate.rel_path for candidate in _sample_at(root, SEED)]
    (root / drawn[0]).unlink()

    summary = verify.run_verify(root, deep=True, seed=SEED)

    logged = _logged_sample(summary)
    assert logged == drawn
    (check_8,) = [result for result in summary.results if result.key == "8"]
    assert check_8.status == verify.STATUS_PASS, check_8.details
    assert check_8.name == f"the bytes of {len(logged):,} file(s) re-read from disk"


def test_a_run_without_deep_logs_no_sample(tmp_path):
    """A check that did not run says nothing - the same rule the report follows for
    check 8's line on stdout. A log naming files "re-read from disk" on a run that
    opened none of them would be the vacuity vision principle 9 forbids."""
    summary = verify.run_verify(_healthy(tmp_path))

    assert summary.deep_sampled == []
    assert _logged_sample(summary) == []


def test_without_deep_the_report_says_nothing_about_it(tmp_path):
    """Twelve checks and no mention of the sample - not "0 files re-read", which would
    read as a re-hash that found nothing. A check that did not run says nothing."""
    result = _cli_verify(_healthy(tmp_path))
    assert "--deep" not in result.stdout
    assert "re-read from disk" not in result.stdout
    assert "12 checks" in result.stdout


def test_the_summary_names_the_archive_it_checked(tmp_path):
    """pam can know about several archives (spec 3), so a report that does not say
    which one it read is a report the user cannot file."""
    root = _healthy(tmp_path)
    summary = verify.run_verify(root)
    assert str(root) in verify.verify_summary_lines(summary)[0].text


def test_run_verify_opens_the_connection_it_checks_through_read_only(tmp_path, monkeypatch):
    """The read-only guarantee asserted about the connection `run_verify` ACTUALLY builds.

    THE GAP THIS CLOSES, found by mutation. The test below proves that a
    `connect_readonly` handle refuses a write - but it opens that handle **itself**, so
    it says nothing about which kind `run_verify` hands the checks. And
    `test_run_verify_changes_nothing_it_looks_at` compares the catalog's bytes, which
    passes for as long as the twelve checks happen not to write. Between them, swapping
    `connect_readonly` for `connect` inside `run_verify` passed the ENTIRE suite.

    That turns plan clarification 4 back into a promise about the checks - the thing it
    exists to stop being. So the probe is taken from inside the run, on the real context,
    while the connection is still open; `probed` is asserted afterwards because a
    monkeypatch that never fires would make this test pass by doing nothing.
    """
    probed = []
    real_run_checks = verify.run_checks

    def _probe_then_run(ctx, *args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ctx.conn.execute("UPDATE plans SET status = 'applying'")
        probed.append(ctx.conn)
        return real_run_checks(ctx, *args, **kwargs)

    monkeypatch.setattr(verify, "run_checks", _probe_then_run)
    summary = verify.run_verify(_healthy(tmp_path))

    assert probed, "run_checks was never reached - the probe proved nothing"
    assert summary.exit_code == 0, summary.results


def test_run_verify_hands_the_seed_it_was_given_to_the_checks(tmp_path, monkeypatch):
    """`--seed` reaches the check, or it is a flag that does nothing.

    THE GAP THIS CLOSES, found by mutation at the M6 Task 4 review. Dropping the seed on
    its way into `CheckContext` passed the whole suite: the only test that could have
    noticed is the CLI exit-2 test, whose archive holds two candidates and samples one,
    so an unseeded draw picks the corrupted file **half the time**. A mutant caught by a
    coin flip is worse than one caught by nothing - it makes the suite flaky rather than
    wrong.

    So the seed is read off the REAL context from inside the run, the same shape as the
    read-only probe above, and both directions are driven: a seeded run carries the
    number, an unseeded one carries None (which `random.Random` turns into an OS seed,
    and which is what spec 7.5's "a random 1% sample" means on an ordinary run).
    """
    seen = []
    real_run_checks = verify.run_checks

    def _capture(ctx, *args, **kwargs):
        seen.append(ctx.seed)
        return real_run_checks(ctx, *args, **kwargs)

    monkeypatch.setattr(verify, "run_checks", _capture)
    root = _healthy(tmp_path)
    verify.run_verify(root, deep=True, seed=SEED)
    verify.run_verify(root, deep=True)

    assert seen == [SEED, None], seen


def test_the_checker_cannot_write_through_the_connection_it_is_given(tmp_path):
    """Plan clarification 4 made "strictly read-only" structural rather than a promise.
    A checker with a writable handle is one edit away from repairing something it was
    asked only to report - and spec 7.5's one explicit instruction is that the
    split-unit case is never repaired."""
    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ctx.conn.execute("UPDATE plans SET status = 'applying'")
    finally:
        ctx.conn.close()


# --- M13 H4: `--deep --sample PERCENT` ------------------------------------------------
# Expectations are spelled out here from the amendment (size = max(1, ceil(n * p / 100))),
# not computed from the production symbol. The (n, percent) pairs are chosen so that
# ceil, floor and the floor-of-one each give a DIFFERENT answer somewhere.


# A module constant, not an inline list, so its EXTENT can be pinned (Task 11 review, M2545):
# pytest SKIPS an empty parametrize, and these rows are the only killers of ceil -> floor and
# of the exact arithmetic. `test_the_h4_size_table_keeps_every_row_that_tells_formulas_apart`
# is its floor.
_H4_SIZE_ROWS = [
    (3, 50, 2),  # 1.5 -> ceil 2; floor would say 1
    (7, 50, 4),  # 3.5 -> 4; floor 3
    (10, 25, 3),  # 2.5 -> 3; floor 2
    (100, 7, 7),  # exact, and an exact float too: 7 * 100 / 100 is 7.0
    # THE FLOAT TRAP, which the row above cannot express (Task 11 review, M2543): in
    # floats, 8.8 * 375 / 100 is 33.00000000000001 and 64.4 * 250 / 100 is
    # 161.00000000000003, whose ceilings re-read a file nobody asked for. Exact: 33, 161.
    (375, 8.8, 33),
    (250, 64.4, 161),
    (1000, 0.1, 1),  # exactly 1
    (1000, 0.15, 2),  # 1.5 -> 2
    (5, 0.01, 1),  # 0.0005 -> ceil 1 (and the floor of one agrees)
    (3, 100, 3),  # everything
    (500, 1, 5),  # the default, unchanged
    (101, 100, 101),
]


@pytest.mark.parametrize(("n", "percent", "expected"), _H4_SIZE_ROWS)
def test_the_sample_size_is_the_ceiling_of_the_percentage_with_a_floor_of_one(
    n, percent, expected
):
    assert len(verify.deep_sample(_fake_candidates(n), SEED, percent)) == expected


def test_the_h4_size_table_keeps_every_row_that_tells_formulas_apart():
    """The table's FLOOR (Task 11 review, M2545). Each row named here is one on which a wrong
    formula gives a different count - floor for ceil, floats for exact rationals - so losing
    any of them, or the whole list, silently re-admits that formula."""
    assert len(_H4_SIZE_ROWS) == 12
    for row in [(3, 50, 2), (7, 50, 4), (10, 25, 3), (1000, 0.15, 2)]:  # ceil vs floor
        assert row in _H4_SIZE_ROWS, row
    for row in [(375, 8.8, 33), (250, 64.4, 161), (1000, 0.1, 1)]:  # exact vs float
        assert row in _H4_SIZE_ROWS, row


def test_the_floor_of_one_holds_for_a_percentage_that_rounds_to_zero():
    """Under H4's ceil the floor of one is UNREACHABLE for any percent > 0: exact arithmetic
    makes every positive share of a non-empty list at least 1 (Task 11 review: deleting the
    floor, M2530, is green and recorded INVALID). So this pins the constant, the tiny-share
    answer and the empty list - it does not, and cannot, exercise the floor itself."""
    assert verify.DEEP_SAMPLE_FLOOR == 1
    assert len(verify.deep_sample(_fake_candidates(1), SEED, 0.001)) == 1
    assert verify.deep_sample([], SEED, 100) == []


def test_sample_100_re_hashes_every_present_file_exactly_once(tmp_path, monkeypatch):
    """The hasher is SPIED: called n times, once per present candidate - not merely an
    outcome that looks the same."""
    root = _healthy(tmp_path)
    ctx = _context(root, seed=SEED)
    try:
        n = len(repo.rehash_candidates(ctx.conn))
    finally:
        ctx.conn.close()
    assert n == 3

    calls = []
    real = verify.checks.sha256_file

    def _record(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(verify.checks, "sha256_file", _record)
    summary = verify.run_verify(root, deep=True, seed=SEED, sample=100)
    assert summary.exit_code == verify.EXIT_ALL_PASS, summary.results
    assert len(calls) == 3
    assert len(set(calls)) == 3
    assert len(summary.deep_sampled) == 3


def test_the_default_deep_run_still_samples_one_file_of_three(tmp_path, monkeypatch):
    root = _healthy(tmp_path)
    calls = []
    real = verify.checks.sha256_file
    monkeypatch.setattr(verify.checks, "sha256_file", lambda p: calls.append(p) or real(p))
    verify.run_verify(root, deep=True, seed=SEED)
    assert len(calls) == 1


def test_a_corrupted_file_is_found_at_sample_100_whatever_the_seed(tmp_path):
    root = _healthy(tmp_path)
    ctx = _context(root)
    try:
        victim = repo.rehash_candidates(ctx.conn)[-1]
    finally:
        ctx.conn.close()
    _rewrite_outside_pam(root / victim.rel_path)
    for seed in (1, 2, 3):
        summary = verify.run_verify(root, deep=True, seed=seed, sample=100)
        (check_8,) = [r for r in summary.results if r.key == "8"]
        assert check_8.status == verify.STATUS_ERROR
        assert victim.rel_path in check_8.details[0]


def test_sample_without_deep_is_refused_before_anything_is_read(tmp_path, monkeypatch, capsys):
    """Exit 1 and ONE clear line (spec 13) - and the refusal comes first: no archive is
    resolved, no log opened."""
    from pam.cli import main

    root = _healthy(tmp_path)
    called = []
    monkeypatch.setattr(verify, "resolve_archive", lambda a: called.append(a) or root)
    assert main(["verify", "--archive", str(root), "--sample", "5"]) == 1
    err = capsys.readouterr()
    text = err.out + err.err
    assert (
        "--sample only means something with --deep: it is the share of files --deep re-hashes."
        " Run: pam verify --deep --sample N"
    ) in text
    assert called == [], "the refusal must precede archive resolution"
    with pytest.raises(verify.PamError):
        verify.run_verify(root, sample=5)


@pytest.mark.parametrize("bad", ["0", "-1", "100.5", "abc"])
def test_sample_outside_zero_exclusive_to_100_is_a_usage_error(tmp_path, bad):
    from pam.cli import main

    root = _healthy(tmp_path)
    assert main(["verify", "--archive", str(root), "--deep", "--sample", bad]) != 0


def test_sample_nan_is_a_usage_error_and_check_8_never_runs(tmp_path, capsys):
    """`0 < PERCENT <= 100` (H4) excludes nan. Measured at `c03dbd7`: `--deep --sample nan`
    passes the range, takes the lock, runs every check, and check 8 reports `could not run`
    with `Fix: pam scan` - a remedy for nothing - and exit 2, spec 13's code for a bug."""
    from pam.cli import main

    root = _healthy(tmp_path)
    code = main(["verify", "--archive", str(root), "--deep", "--sample", "nan"])
    text = "".join(capsys.readouterr())
    assert "could not run" not in text, text
    assert "Log:" not in text, "a usage error must stop before any check runs"
    assert code != 0


def test_the_cli_passes_sample_through_to_the_check(tmp_path, monkeypatch):
    from pam.cli import main

    root = _healthy(tmp_path)
    calls = []
    real = verify.checks.sha256_file
    monkeypatch.setattr(verify.checks, "sha256_file", lambda p: calls.append(p) or real(p))
    assert main(["verify", "--archive", str(root), "--deep", "--sample", "100"]) == 0
    assert len(calls) == 3

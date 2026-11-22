"""S13 (M13 Task 4, `m12.md` C1): every lock-free reader reports a state that EXISTED.

`pam status` is covered by its M12 construction (`test_m12_safety_constructions.py`, Q5), whose
strict xfail this task turned into a plain test. Here: the other readers.

MEASURED FIRST (m13-progress.md, Task 4): `pam report` has three subjects (`skipped`,
`duplicates`, `session` - read off `pam/cli.py`). `skipped` and `duplicates` make ONE catalog
read each, so no interleaving can mix two states; they are wrapped anyway (S13 says every
lock-free read). `session` makes THREE - the session's counts, its rows, and the governing rows
from any session - and a `pam undo` committing between the first and the third paired the
apply's "nothing undone" counts with the undo's governing rows: a state the catalog never held.
`pam verify` holds the workspace lock, so a pam writer started during its read half is REFUSED
(below); its checks also read inside one snapshot.

The oracle for `session` is the catalog read QUIESCENTLY, before the writer and after it, through
the suite's own read-only connection - never the report's own output.
"""

import threading

import pytest
from archives import approve_and_apply, planned

import pam.report as report_mod
import pam.report_duplicates as duplicates_mod
import pam.status as status_mod
import pam.verify as verify_mod
from pam.catalog import repo
from pam.catalog.db import connect, connect_readonly, read_snapshot
from pam.cli import main
from pam.execute.undo import run_undo
from pam.paths import workspace_if_exists

BARRIER_S = 60  # a deadline for a barrier that should open at once, never a wait for an effect
LOCKED = "Another pam command is already running against this archive."

# `pam report session`'s catalog reads, spelled by hand from `run_report_session` (S13's three).
SESSION_READS = ("sessions", "session_rows", "latest_resolved_rows")
assert len(SESSION_READS) == len(set(SESSION_READS)) == 3


def _applied(tmp_path):
    """Two files, planned, approved and applied: (root, session_id)."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    summary = approve_and_apply(plan_id, root, confirm=None)
    assert summary.session_id
    return root, summary.session_id


def _session_state(root, session_id):
    """(the session's counts, its rows, the governing rows) as the catalog holds them NOW."""
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect_readonly(ws.db_path)
    try:
        counts = next(c for c in repo.sessions(conn) if c.session_id == session_id)
        return (
            counts,
            repo.session_rows(conn, session_id),
            repo.latest_resolved_rows(conn, session_id),
        )
    finally:
        conn.close()


def _across(monkeypatch, pause_after, reader_fn, writer_fn):
    """Run `reader_fn` in a thread, stop it right after its `pause_after` repo read, run
    `writer_fn` to completion here, then release it. Returns the reader's result."""
    reached, release = threading.Event(), threading.Event()
    result = {}

    def read():
        result["value"] = reader_fn()

    reader = threading.Thread(target=read, daemon=True)
    original = getattr(repo, pause_after)

    def paused(*args, **kwargs):
        value = original(*args, **kwargs)
        if threading.current_thread() is reader and not reached.is_set():
            reached.set()
            assert release.wait(BARRIER_S), "the writer never released the reader"
        return value

    monkeypatch.setattr(repo, pause_after, paused)
    reader.start()
    try:
        assert reached.wait(BARRIER_S), f"the reader never made its {pause_after} read"
        writer_fn()
        assert reader.is_alive() and not release.is_set()
    finally:
        release.set()
        reader.join(BARRIER_S)
    assert not reader.is_alive()
    assert "value" in result, "the reader raised instead of returning"
    return result["value"]


@pytest.mark.parametrize("pause_after", SESSION_READS)
def test_report_session_read_across_an_undo_reports_before_or_after(
    tmp_path, monkeypatch, pause_after
):
    root, session_id = _applied(tmp_path)
    before = _session_state(root, session_id)
    seen = {}
    real_probe = report_mod.probe

    def spy_probe(probe_root, rows, governing):
        seen["rows"], seen["governing"] = rows, governing
        return real_probe(probe_root, rows, governing)

    monkeypatch.setattr(report_mod, "probe", spy_probe)

    def undo():
        assert not run_undo(session_id, root).had_errors

    report = _across(
        monkeypatch,
        pause_after,
        lambda: report_mod.run_report_session(root, session_id),
        undo,
    )
    after = _session_state(root, session_id)
    # The barrier's own assertion (plan mutation 5): the writer REALLY committed between the
    # reader's reads, and it changed every part the report reads - else this test is vacuous.
    assert before[0].undone == 0 and after[0].undone == 2
    assert before[2] != after[2]

    reading = (report.counts, seen["rows"], seen["governing"])
    assert reading in (before, after), (
        "pam report session paired catalog reads from before and after a pam undo"
    )


@pytest.mark.parametrize(
    ("reader", "read"),
    [
        ("status", "sessions"),
        ("report skipped", "report_facts"),
        ("report duplicates", "duplicate_groups"),
        ("report duplicates", "phash_by_sha256"),
        ("report duplicates", "all_files"),
        ("report session", "sessions"),
        ("report session", "session_rows"),
        ("report session", "latest_resolved_rows"),
    ],
)
def test_every_lock_free_read_runs_inside_one_transaction(tmp_path, monkeypatch, reader, read):
    """The wrap itself, reader by reader: each repo read the reader makes runs with its
    connection INSIDE a transaction - i.e. inside `read_snapshot`. One-SELECT readers cannot be
    caught by a concurrency construction, so this is the guard on their wrap (plan mutation 3)."""
    root, session_id = _applied(tmp_path)
    flags = []
    original = getattr(repo, read)

    def spy(conn, *args, **kwargs):
        flags.append(conn.in_transaction)
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(repo, read, spy)
    run = {
        "status": lambda: status_mod.collect_status(root),
        "report skipped": lambda: report_mod.run_report_skipped(root),
        "report duplicates": lambda: duplicates_mod.run_report_duplicates(root),
        "report session": lambda: report_mod.run_report_session(root, session_id),
    }[reader]
    run()
    assert flags == [True], f"{reader}'s {read} read ran outside a snapshot: {flags}"


def test_verify_runs_every_check_inside_one_snapshot(tmp_path, monkeypatch):
    root, _ = _applied(tmp_path)
    flags = []
    original = verify_mod.run_checks

    def spy(ctx, checks):
        flags.append(ctx.conn.in_transaction)
        return original(ctx, checks)

    monkeypatch.setattr(verify_mod, "run_checks", spy)
    verify_mod.run_verify(root)
    assert flags == [True]


def test_a_writer_started_during_verifys_read_half_is_refused_the_lock(
    tmp_path, monkeypatch, capsys
):
    """`pam verify` is protected by the workspace lock AND the snapshot: a `pam plan` started
    while verify is reading is refused with the lock's own sentence, writes nothing, and
    verify's result is the before-state's. After verify ends the same `pam plan` succeeds,
    which proves it was the lock that refused it. Planned, not applied: so a `pam plan` has
    something to write (it supersedes plan 1 and opens plan 2)."""
    root, _ = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    ws = workspace_if_exists(root)
    assert ws is not None

    def plans():
        conn = connect_readonly(ws.db_path)
        try:
            return conn.execute("SELECT id, status FROM plans ORDER BY id").fetchall()
        finally:
            conn.close()

    before = [tuple(r) for r in plans()]
    reached, release = threading.Event(), threading.Event()
    original = verify_mod.run_checks
    result = {}

    def paused(ctx, checks):
        reached.set()
        assert release.wait(BARRIER_S), "the writer never released verify"
        return original(ctx, checks)

    monkeypatch.setattr(verify_mod, "run_checks", paused)
    reader = threading.Thread(
        target=lambda: result.setdefault("summary", verify_mod.run_verify(root)), daemon=True
    )
    reader.start()
    try:
        assert reached.wait(BARRIER_S), "pam verify never reached its checks"
        capsys.readouterr()
        assert main(["plan", "--archive", str(root)]) == 1
        assert LOCKED in capsys.readouterr().err
        assert [tuple(r) for r in plans()] == before
    finally:
        release.set()
        reader.join(BARRIER_S)
    assert not reader.is_alive()
    assert result["summary"].exit_code == 0

    assert main(["plan", "--archive", str(root)]) == 0
    assert [tuple(r) for r in plans()] == [(1, "superseded"), (2, "open")]


def test_read_snapshot_holds_one_state_and_ends_its_transaction(tmp_path):
    """The helper alone, against a real WAL catalog: inside the block a commit by another
    connection is invisible; after it, the connection is out of its transaction and sees it."""
    root, _ = _applied(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    reader = connect_readonly(ws.db_path)
    writer = connect(ws.db_path)
    try:
        count = "SELECT COUNT(*) FROM plans"
        with read_snapshot(reader):
            first = reader.execute(count).fetchone()[0]
            writer.execute(
                "INSERT INTO plans (created_at, status, template, config_json, app_version,"
                " schema_version) SELECT created_at, 'superseded', template, config_json,"
                " app_version, schema_version FROM plans LIMIT 1"
            )
            writer.commit()
            assert reader.execute(count).fetchone()[0] == first
            assert reader.in_transaction
        assert not reader.in_transaction
        assert reader.execute(count).fetchone()[0] == first + 1
    finally:
        reader.close()
        writer.close()

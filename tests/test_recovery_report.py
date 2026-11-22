"""E6 (M15 Task 7, design 7.2, `m14.md` C9): recovery writes the report of an interrupted run.

Every crash here is a real one reached the way `test_recover.py` reaches them - an archive of real files,
scanned, planned and applied under `PAM_FAULT` - and every expectation is SPELLED BY HAND from
the journal and the disk the scenario leaves, never read out of `recovery_report` itself (an
oracle built from the definition it checks agrees with any definition).

THE M14 FIXTURES (`tests/fixtures/m14_reports/`) WERE WRITTEN BY `v0.1-m14`'s OWN WRITER, not
typed: `git archive v0.1-m14 pam | tar -x -C <dir>`, then
`PYTHONPATH=<dir> python tests/fixtures/m14_reports/make_m14_reports.py.txt tests/fixtures/m14_reports`
(the script asserts it imported the tagged tree). `.gitattributes` keeps their bytes as written.
"""

import errno
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from archives import (
    FAKE_COMMAND,
    approve_and_apply,
    approve_ids,
    archive_names,
    catalog,
    companion_archive,
    journal_pairs,
    planned,
    put,
)

from pam.catalog import repo
from pam.cli import main
from pam.errors import EXIT_OK
from pam.execute import recover as recover_module
from pam.execute import recovery_report, reporter
from pam.execute.apply import run_apply
from pam.execute.recover import recover
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

LOGGER = logging.getLogger("tests.recovery_report")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "m14_reports"
M14_REPORTS = sorted(FIXTURES.glob("session-*.md"))
# The end line and the header field, spelled from the brief rather than read off `reporter`.
END = "-- end of report --"
FORMAT = "- Report format: 2"
STARTED = re.compile(r"- Started: \d{4}-\d\d-\d\d \d\d:\d\d")
STOPPED = re.compile(
    r"- This run stopped part-way\. pam recovered this session at \d{4}-\d\d-\d\d \d\d:\d\d"
    r" and wrote this report\."
)
# F1 (Task 7 Stage 2 ruling): a session THIS recovery pass did not resolve, heal or finalize is not
# called interrupted - pam cannot tell a deleted report from one never written. Spelled by hand.
FOUND = re.compile(
    r"- pam found no complete report for this session and wrote this one at"
    r" \d{4}-\d\d-\d\d \d\d:\d\d, from the catalog as it stood then\."
)
NO_DAMAGE = (
    "No file was deleted, moved out of the folder, or changed inside. pam renames in place and"
    " nothing else."
)


def test_the_m14_fixtures_are_there():
    """The legacy controls below are parametrized over this list; an empty one would SKIP."""
    assert len(M14_REPORTS) == 2


def recover_now(root):
    conn = catalog(root)
    try:
        return recover(conn, root, LOGGER)
    finally:
        conn.close()


def reports_dir(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    return ws.reports_dir


def report_path(root, session_id):
    return reports_dir(root) / f"session-{session_id}.md"


def only_session(root):
    conn = catalog(root)
    try:
        (counts,) = repo.sessions(conn)
        return counts.session_id
    finally:
        conn.close()


def report_lines(root, session_id):
    """The report as lines, with its two clock readings checked and replaced by a marker."""
    lines = report_path(root, session_id).read_text(encoding="utf-8").split("\n")
    out = []
    for line in lines:
        if STARTED.fullmatch(line):
            out.append("<started>")
        elif STOPPED.fullmatch(line):
            out.append("<stopped>")
        elif FOUND.fullmatch(line):
            out.append("<found>")
        else:
            out.append(line)
    return out


def crash(root, plan_id, set_fault, spec):
    set_fault.arm(spec)
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()


def expected(root, session, plan_id, counts, sections, undo=False, found=False):
    """The whole report, line by line, spelled from what the scenario left. `found`: a session
    this recovery pass did not touch (F1) - the ordinary title and the neutral line."""
    lines = [
        f"# Renaming run {session}" if found else f"# Renaming run {session} (interrupted)",
        "",
        FORMAT,
        f"- Photo folder: {root.resolve()}",
        "<started>",
        f"- Proposed names from: plan {plan_id}",
        "<found>" if found else "<stopped>",
        "",
        "## What happened",
        "",
    ]
    renamed, put_back, not_renamed, unresolved = counts
    lines += [
        f"- {renamed} file(s) renamed",
        f"- {put_back} file(s) put back under their original names",
        f"- {not_renamed} file(s) not renamed",
        f"- {unresolved} file(s) unresolved",
        "",
        NO_DAMAGE,
        "",
    ]
    lines += sections
    if undo:
        lines += [
            "## Undo",
            "",
            "This puts every file renamed above back under the name it had before.",
            "",
            f"    pam undo {session}",
            "",
        ]
    return [*lines, END, ""]


# --- a crash at each boundary: one complete, correct report on the next command -------------


def test_a_rename_the_crash_interrupted_after_it_happened_is_reported_as_renamed(
    tmp_path, set_fault
):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    assert not report_path(root, session).exists()

    summary = recover_now(root)

    assert summary.reported == [session]
    assert archive_names(root) == ["2019-07-04_001.JPG"]
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (1, 0, 0, 0),
        ["## Files renamed", "", "- `IMG_1.JPG` -> `2019-07-04_001.JPG`", ""],
        undo=True,
    )


def test_a_rename_the_crash_stopped_before_it_happened_is_reported_as_not_renamed(
    tmp_path, set_fault
):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_intent:1")
    session = only_session(root)

    recover_now(root)

    assert archive_names(root) == ["IMG_1.JPG"]
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (0, 0, 1, 0),
        ["## Files pam did not rename", "", "- `IMG_1.JPG`", ""],
    )


MID_UNIT = ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP")


def mid_unit_expectation(root, session, plan_id, found=False):
    """`crash:mid_companion_unit:1` over three members, after ALL THREE phases: phase 1 fails
    the .MOV and .XMP, phase 2 puts the .JPG back. Spelled from test_recover's account.
    `found`: the pass that wrote the report was not the one that recovered the session (F1)."""
    return expected(
        root,
        session,
        plan_id,
        (0, 1, 2, 0),
        [
            "## Files put back under their original names",
            "",
            "- `IMG_1.JPG`",
            "",
            "## Files pam did not rename",
            "",
            "- `IMG_1.MOV`",
            "- `IMG_1.XMP`",
            "",
        ],
        found=found,
    )


def test_a_unit_put_back_by_phase_two_is_reported_put_back(tmp_path, set_fault):
    root, plan_id = companion_archive(tmp_path, MID_UNIT)
    crash(root, plan_id, set_fault, "crash:mid_companion_unit:1")
    session = only_session(root)

    recover_now(root)

    assert archive_names(root) == list(MID_UNIT)
    assert report_lines(root, session) == mid_unit_expectation(root, session, plan_id)


@pytest.mark.parametrize(
    "phase", ["_resolve_intent_rows", "_heal_split_units", "_finalize_applying_plans"]
)
def test_a_crash_after_any_phase_still_ends_in_one_complete_correct_report(
    tmp_path, set_fault, monkeypatch, phase
):
    """No report is written between the phases (gate blocker 4): a pass killed after any of
    them leaves none, and the next pass writes the one that describes the FINAL state.

    F1 (Task 7 Stage 2 ruling): killed after phase 1 or 2, the next pass still heals or finalizes
    this session, so the report says it stopped part-way. Killed after phase 3, the killed pass
    did all of recovery's work and the next one does none, so its report is the neutral one."""
    root, plan_id = companion_archive(tmp_path, MID_UNIT)
    crash(root, plan_id, set_fault, "crash:mid_companion_unit:1")
    session = only_session(root)
    real = getattr(recover_module, phase)

    def then_die(*args, **kwargs):
        real(*args, **kwargs)
        raise SystemExit("killed after a phase")

    with monkeypatch.context() as patch:
        patch.setattr(recover_module, phase, then_die)
        with pytest.raises(SystemExit):
            recover_now(root)
    assert not report_path(root, session).exists()

    recover_now(root)

    assert report_lines(root, session) == mid_unit_expectation(
        root, session, plan_id, found=phase == "_finalize_applying_plans"
    )


def test_a_crash_after_the_journal_is_final_and_before_the_report_is_retried(
    tmp_path, set_fault, monkeypatch
):
    """Discovery is NOT "the sessions this pass resolved": the second pass has no `intent`
    row and no split unit left, and must still find the session that has no report. Re-pointed
    by F1 (Task 7 Stage 2 ruling): the second pass touched nothing, so the report it writes is
    the neutral one, not "stopped part-way"."""
    root, plan_id = companion_archive(tmp_path, MID_UNIT)
    crash(root, plan_id, set_fault, "crash:mid_companion_unit:1")
    session = only_session(root)

    def die(*args, **kwargs):
        raise SystemExit("killed before the report")

    with monkeypatch.context() as patch:
        patch.setattr(recover_module, "report_interrupted_runs", die)
        with pytest.raises(SystemExit):
            recover_now(root)
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.split_units(conn) == []
    finally:
        conn.close()
    assert not report_path(root, session).exists()

    second = recover_now(root)

    assert not second.did_anything  # nothing left to recover ...
    assert second.reported == [session]  # ... and the report is written anyway
    assert report_lines(root, session) == mid_unit_expectation(
        root, session, plan_id, found=True
    )


def test_a_report_a_crash_cut_short_is_written_again_whole(tmp_path, set_fault, monkeypatch):
    """Re-pointed by F1: the pass that recovered the session died writing its report, so the pass
    that rewrites it did no recovery work and writes the neutral line."""
    root, plan_id = companion_archive(tmp_path, MID_UNIT)
    crash(root, plan_id, set_fault, "crash:mid_companion_unit:1")
    session = only_session(root)
    real_write = Path.write_text

    def half_then_die(self, data, *args, **kwargs):
        real_write(self, data[: len(data) // 2], *args, **kwargs)
        raise SystemExit("killed mid-report")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", half_then_die)
        with pytest.raises(SystemExit):
            recover_now(root)
    cut = report_path(root, session).read_text(encoding="utf-8")
    assert FORMAT in cut
    assert END not in cut

    assert recover_now(root).reported == [session]

    assert report_lines(root, session) == mid_unit_expectation(
        root, session, plan_id, found=True
    )


# --- what the report says -----------------------------------------------------------------


def test_a_split_unit_that_cannot_heal_yet_is_reported_unresolved_and_named(
    tmp_path, set_fault, allow_split_unit
):
    """Something sits on the .JPG's old name, so phase 2 cannot put it back: the file is
    under its new name in a split group. That is `unresolved`, never `renamed`."""
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    crash(root, plan_id, set_fault, "crash:mid_unit_revert:1")
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    session = only_session(root)

    assert recover_now(root).stranded == ["2019-07-04_001.JPG"]

    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()
    assert (root / "2019-07-04_001.JPG").exists()
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (0, 0, 1, 1),
        [
            "## Files pam did not rename",
            "",
            "- `IMG_1.MOV`",
            "",
            "## Files unresolved",
            "",
            "These are under their new names while the group they belong to is not."
            " Nothing was lost. pam puts them back the next time it runs and can.",
            "",
            "- `2019-07-04_001.JPG`",
            "",
        ],
    )


def test_a_failed_item_with_no_journal_row_is_named_not_renamed_with_no_reason(
    tmp_path, set_fault
):
    """S2-F1: an item apply refused before its intent (AM2's cloud-only skip is one) is
    `failed` with no journal row and no recorded reason. It is listed; no cause is claimed."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    conn = catalog(root)
    try:
        items = {item.old_rel_path: item.id for item in repo.plan_items(conn, plan_id)}
        repo.set_plan_item_status(conn, items["IMG_2.JPG"], "failed")
        conn.commit()
    finally:
        conn.close()
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)

    recover_now(root)

    text = report_path(root, session).read_text(encoding="utf-8")
    assert "cloud" not in text.lower()
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (1, 0, 1, 0),
        [
            "## Files renamed",
            "",
            "- `IMG_1.JPG` -> `2019-07-04_001.JPG`",
            "",
            "## Files pam did not rename",
            "",
            "- `IMG_2.JPG`",
            "",
        ],
        undo=True,
    )


# --- what recovery never does -------------------------------------------------------------


def test_a_session_with_no_journal_rows_gets_no_report(tmp_path, set_fault):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:before_intent_commit:1")

    summary = recover_now(root)

    assert summary.reported == []
    assert list(reports_dir(root).glob("session-*.md")) == []


def test_an_ordinary_apply_writes_a_complete_report_and_recovery_leaves_it_alone(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    path = report_path(root, applied.session_id)
    text = path.read_text(encoding="utf-8")
    assert text.split("\n")[2] == FORMAT
    assert text.endswith(f"\n{END}\n")
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    assert recover_now(root).reported == []

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_an_undo_session_gets_no_report(tmp_path):
    """pam never writes a report for putting files back (HT-M6 H3), so a missing one says
    nothing about an interruption; recovery must not invent an "interrupted" report for it."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    undone = run_undo(applied.session_id, root)

    assert recover_now(root).reported == []
    assert not report_path(root, undone.session_id).exists()


@pytest.mark.parametrize("fixture", M14_REPORTS, ids=lambda p: p.name)
def test_an_m14_report_of_an_interrupted_run_stays_byte_identical(tmp_path, set_fault, fixture):
    """Legacy is final: an M14 report has no format field and no end line, and is never
    taken for a truncated one - even standing where an interrupted session's report goes."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    path = report_path(root, only_session(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fixture.read_bytes())
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    for _ in range(2):
        assert recover_now(root).reported == []

    assert path.read_bytes() == fixture.read_bytes()
    assert path.stat().st_mtime_ns == 1_700_000_000_000_000_000


@pytest.mark.parametrize("fixture", M14_REPORTS, ids=lambda p: p.name)
def test_an_m14_report_of_a_finished_run_stays_byte_identical(tmp_path, fixture):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    path = report_path(root, approve_and_apply(plan_id, root).session_id)
    path.write_bytes(fixture.read_bytes())
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    assert recover_now(root).reported == []

    assert path.read_bytes() == fixture.read_bytes()
    assert path.stat().st_mtime_ns == 1_700_000_000_000_000_000


def test_a_complete_recovery_report_is_never_rewritten(tmp_path, set_fault, allow_split_unit):
    """Not even when the state it described has moved on: the unit heals on a later pass and
    the report written before that stays exactly as it was (bytes and mtime)."""
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    crash(root, plan_id, set_fault, "crash:mid_unit_revert:1")
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    session = only_session(root)
    recover_now(root)
    path = report_path(root, session)
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    (root / "IMG_1.JPG").unlink()
    assert recover_now(root).reverted == ["IMG_1.JPG"]

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_access_denied_on_the_reports_folder_does_not_stop_recovery_or_the_command(
    tmp_path, set_fault, monkeypatch
):
    """The report is the only thing lost: the journal converges and `pam plan` runs to exit 0.
    The report is written by the first command after the folder is writable again."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    real_write = Path.write_text
    denied = []

    def deny_reports(self, *args, **kwargs):
        if self.parent.name == "reports":
            denied.append(self.name)
            raise PermissionError(13, "Access is denied", str(self))
        return real_write(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", deny_reports)
        code = main(["plan", "--archive", str(root)])
    assert code == EXIT_OK
    assert denied == [f"session-{session}.md"]
    assert not report_path(root, session).exists()
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.plan_status(conn, plan_id) == "applied"
    finally:
        conn.close()

    assert recover_now(root).reported == [session]
    assert report_lines(root, session)[-2:] == [END, ""]


# --- `report_is_final`, the publication rule -----------------------------------------------


def test_report_is_final_spelled_case_by_case(tmp_path):
    missing = tmp_path / "missing.md"
    complete = tmp_path / "complete.md"
    complete.write_text(
        f"# t\n\n{FORMAT}\n- x\n\n## What happened\n\n{END}\n", encoding="utf-8"
    )
    cut = tmp_path / "cut.md"
    cut.write_text(f"# t\n\n{FORMAT}\n- x\n\n## What happened\n\n- 1 file(s)", encoding="utf-8")
    cut_mid_end = tmp_path / "cut_mid_end.md"
    cut_mid_end.write_text(f"# t\n\n{FORMAT}\n\n-- end of", encoding="utf-8")
    # The field only counts in the HEADER: a body line that happens to read like it does not
    # make a legacy report "new".
    late_field = tmp_path / "late_field.md"
    late_field.write_text(f"# t\n\n- x\n\n## Files\n\n{FORMAT}\n", encoding="utf-8")
    a_directory = tmp_path / "a_directory.md"
    a_directory.mkdir()

    assert recovery_report.report_is_final(missing) is False
    assert recovery_report.report_is_final(complete) is True
    assert recovery_report.report_is_final(cut) is False
    assert recovery_report.report_is_final(cut_mid_end) is False
    assert recovery_report.report_is_final(late_field) is True
    assert recovery_report.report_is_final(a_directory) is True
    for fixture in M14_REPORTS:
        assert recovery_report.report_is_final(fixture) is True


def test_the_ordinary_writer_and_the_recovery_writer_share_one_path_spelling(tmp_path):
    assert reporter.session_report_path(tmp_path, "s-20261116-101010") == (
        tmp_path / "session-s-20261116-101010.md"
    )


# --- Stage 1 review (M15 Task 7): an older pam's journal, and a report no pam wrote -----------


def test_a_session_an_older_pam_recovered_and_the_user_undid_is_reported_put_back(
    tmp_path, set_fault, monkeypatch
):
    """Q10 (AM5): an M14 workspace holds a crashed run M14 recovered WITHOUT a report, which the
    user then undid. Spec 7.0 J2 keeps the rename row `done` for ever, so the row alone says
    "renamed"; the file is back under its original name, and the report must say so - and must
    not offer `pam undo` for a run already undone. Re-pointed by F1: the older pam did the
    recovering, so this pam's report is the neutral one."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    with monkeypatch.context() as older_pam:
        older_pam.setattr(recover_module, "report_interrupted_runs", lambda *args: [])
        recover_now(root)
        run_undo(session, root)
    assert archive_names(root) == ["IMG_1.JPG"]
    assert not report_path(root, session).exists()

    assert recover_now(root).reported == [session]

    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (0, 1, 0, 0),
        ["## Files put back under their original names", "", "- `IMG_1.JPG`", ""],
        found=True,
    )


@pytest.mark.parametrize("content", [b"", b"\x00" * 300], ids=["empty", "nul-filled"])
def test_a_report_a_power_cut_left_empty_is_written_again(tmp_path, set_fault, content):
    """The commonest shape of a report a power cut interrupted is a file with no bytes in it (or
    NULs): no pam ever wrote that, so it is not legacy and not final."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    path = report_path(root, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    assert recover_now(root).reported == [session]

    assert report_lines(root, session)[-2:] == [END, ""]
    assert recovery_report.report_is_final(path) is True


def test_a_session_with_a_row_still_in_intent_is_not_reported(tmp_path, set_fault):
    """M3812: a session is reported only once it is OVER. The one `intent` row recovery cannot
    resolve - its plan item is gone (catalog corruption, made with the suite's own SQL, as in
    `test_recover.py`) - keeps its session open, and an "interrupted" report would be premature."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_intent:1")
    session = only_session(root)
    conn = catalog(root)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM plan_items")
        conn.commit()
        summary = recover(conn, root, LOGGER)
        assert [row.state for row in repo.session_rows(conn, session)] == ["intent"]
    finally:
        conn.close()

    assert summary.needs_attention == ["IMG_1.JPG"]
    assert summary.reported == []
    assert not report_path(root, session).exists()


def test_a_disk_full_while_writing_the_report_does_not_stop_the_command(
    tmp_path, set_fault, monkeypatch
):
    """M3813: "any write error is logged and swallowed" - not only access denied."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    real_write = Path.write_text
    refused = []

    def disk_full(self, *args, **kwargs):
        if self.parent.name == "reports":
            refused.append(self.name)
            raise OSError(errno.ENOSPC, "There is not enough space on the disk", str(self))
        return real_write(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", disk_full)
        code = main(["plan", "--archive", str(root)])
    assert code == EXIT_OK
    assert refused == [f"session-{session}.md"]
    assert recover_now(root).reported == [session]


# Ruling (a)'s statuses that are NEVER listed. A module tuple, pinned by its own control below:
# emptied, the parametrize would SKIP its two nodes - the sole killers of ruling (a) (M3839).
NEVER_DECIDED = ("proposed", "skipped")


@pytest.mark.parametrize("never_decided", NEVER_DECIDED)
def test_only_this_plans_unrenamed_items_without_a_journal_row_are_listed(
    tmp_path, set_fault, never_decided
):
    """M3815 / M3820, then the controller's ruling (a): the report lists, as not renamed with no
    reason, every item OF THE SESSION'S PLAN that ends with no journal row and was going to be
    renamed - a `failed` one (S2-F1) and an `approved` one the crash never reached (the plan is
    terminal after phase 3, so this plan will never rename it). A `proposed` or `skipped` item
    was never going to be renamed and is not listed, and an earlier plan's refusals are not this
    run's: plan 1's `IMG_3.JPG` failed with no row, plan 2 superseded it, and plan 2's run
    renamed `IMG_1.JPG` and stopped before the approved `IMG_2.JPG`."""
    root, first = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    conn = catalog(root)
    try:
        items = {item.old_rel_path: item.id for item in repo.plan_items(conn, first)}
        repo.set_plan_item_status(conn, items["IMG_3.JPG"], "failed")
        conn.commit()
    finally:
        conn.close()
    second = run_plan(root).plan_id
    assert second is not None and second != first
    conn = catalog(root)
    try:
        items = {item.old_rel_path: item.id for item in repo.plan_items(conn, second)}
        approve_ids(conn, [items["IMG_1.JPG"], items["IMG_2.JPG"]])
        repo.set_plan_item_status(conn, items["IMG_3.JPG"], never_decided)
        conn.commit()
    finally:
        conn.close()
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        run_apply(second, root)
    set_fault.next_invocation()
    session = only_session(root)

    recover_now(root)

    # The ruling's premise, read from the real crash: phase 3 made the plan terminal and left
    # the unreached item `approved` with no journal row.
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, second) == "partially_applied"
        statuses = {item.old_rel_path: item.status for item in repo.plan_items(conn, second)}
        assert statuses == {
            "IMG_1.JPG": "applied",
            "IMG_2.JPG": "approved",
            "IMG_3.JPG": never_decided,
        }
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()
    assert archive_names(root) == ["2019-07-04_001.JPG", "IMG_2.JPG", "IMG_3.JPG"]
    assert report_lines(root, session) == expected(
        root,
        session,
        second,
        (1, 0, 1, 0),
        [
            "## Files renamed",
            "",
            "- `IMG_1.JPG` -> `2019-07-04_001.JPG`",
            "",
            "## Files pam did not rename",
            "",
            "- `IMG_2.JPG`",
            "",
        ],
        undo=True,
    )


def section(root, session, heading):
    """The entries under one `## ` heading of the session's report, as written."""
    lines = report_path(root, session).read_text(encoding="utf-8").split("\n")
    if heading not in lines:
        return []
    out = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("## ") or line == END:
            break
        if line:
            out.append(line)
    return out


def test_a_later_plans_items_are_never_listed_in_an_earlier_sessions_report(
    tmp_path, set_fault, monkeypatch
):
    """Stage 2 (M3835): only the session's OWN plan counts - also a plan made AFTER it. The report
    can be written late (the first try hit a full disk, so `pam scan` and `pam plan` ran first),
    and by then a later plan holds approved items with no journal row of its own."""
    root, first = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    crash(root, first, set_fault, "crash:after_rename:1")
    session = only_session(root)
    real_write = Path.write_text

    def disk_full(self, *args, **kwargs):
        if self.parent.name == "reports":
            raise OSError(errno.ENOSPC, "There is not enough space on the disk", str(self))
        return real_write(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", disk_full)
        put(root, "IMG_9.JPG")
        run_scan(root, exiftool_command=FAKE_COMMAND)
        later = run_plan(root).plan_id
    assert not report_path(root, session).exists()
    assert later is not None and later > first
    conn = catalog(root)
    try:
        later_items = {item.old_rel_path: item.id for item in repo.plan_items(conn, later)}
        assert "IMG_9.JPG" in later_items
        approve_ids(conn, list(later_items.values()))
        conn.commit()
    finally:
        conn.close()

    assert recover_now(root).reported == [session]

    assert section(root, session, "## Files pam did not rename") == ["- `IMG_2.JPG`"]


def test_the_recovered_at_line_names_the_time_recovery_wrote_it_not_the_start(
    tmp_path, set_fault, monkeypatch
):
    """Stage 2 (M3838): ruling (b)'s line, spelled whole, with both clocks pinned apart. The
    module regex `STOPPED` admits any time, so it cannot tell `recovered_at` from `started_at`."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    conn = catalog(root)
    try:
        conn.execute(
            "UPDATE journal SET intent_at = '2019-01-02T03:04:05' WHERE session_id = ?",
            (session,),
        )
        conn.commit()
    finally:
        conn.close()

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 5, 6, 7, 8, 9)

    monkeypatch.setattr(recovery_report, "datetime", Later)
    recover_now(root)

    lines = report_path(root, session).read_text(encoding="utf-8").split("\n")
    assert "- Started: 2019-01-02 03:04" in lines
    assert (
        "- This run stopped part-way. pam recovered this session at 2030-05-06 07:08 and wrote"
        " this report."
    ) in lines


def test_the_never_decided_statuses_are_written_down():
    """Stage 2 (M3839): the parametrize above would SKIP, not fail, if its list were emptied."""
    assert NEVER_DECIDED == ("proposed", "skipped")


# --- Stage 2 review (M15 Task 7), F1 and F2: "interrupted" only for what THIS pass recovered ------
#
# The controller's ruling: discovery cannot tell a report the user deleted from one never written,
# so the interrupted title and line are kept for a session THIS recovery pass resolved (phase 1),
# healed (phase 2) or finalized (phase 3). Every other session gets the ordinary title and the
# neutral line (`FOUND`). The body below that line is unchanged.

RENAMED_IMG_1 = ["## Files renamed", "", "- `IMG_1.JPG` -> `2019-07-04_001.JPG`", ""]


def second_plan(root, name="IMG_2.JPG", date="2020:08:05 09:15:00"):
    """A new file in the folder, scanned and planned: the id of the plan that now holds it."""
    put(root, name, date)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    return plan_id


def test_a_deleted_report_of_a_finished_run_is_not_called_interrupted(tmp_path):
    """F1 as the reviewer built it: an ordinary apply finished, its report was deleted, and the
    next command must not say the run "stopped part-way"."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    session = approve_and_apply(plan_id, root).session_id
    report_path(root, session).unlink()

    summary = recover_now(root)

    assert not summary.did_anything
    assert summary.reported == [session]
    text = report_path(root, session).read_text(encoding="utf-8")
    assert "interrupted" not in text
    assert "stopped part-way" not in text
    assert report_lines(root, session) == expected(
        root, session, plan_id, (1, 0, 0, 0), RENAMED_IMG_1, undo=True, found=True
    )


def test_a_deleted_reports_folder_brings_every_report_back_neutral(tmp_path):
    """F1's wider shape: deleting `reports/` brings back every rename session's report, and none
    of them may claim an interruption that never happened."""
    root, first = planned(tmp_path, ["IMG_1.JPG"])
    one = approve_and_apply(first, root).session_id
    two = approve_and_apply(second_plan(root), root).session_id
    for path in reports_dir(root).iterdir():
        path.unlink()
    reports_dir(root).rmdir()

    assert sorted(recover_now(root).reported) == sorted([one, two])

    for session in (one, two):
        lines = report_lines(root, session)
        assert lines[0] == f"# Renaming run {session}"
        assert lines.count("<found>") == 1
        assert "<stopped>" not in lines


def test_only_the_session_this_pass_recovered_is_called_interrupted(tmp_path, set_fault):
    """Both rules in one pass: the session this pass resolved stopped part-way; the older one,
    whose report the user deleted, gets the neutral line. Spelled by hand from the two runs."""
    root, first = planned(tmp_path, ["IMG_1.JPG"])
    older = approve_and_apply(first, root).session_id
    second = second_plan(root)
    crash(root, second, set_fault, "crash:after_rename:1")
    report_path(root, older).unlink()  # after the crash: the crashed apply's own recovery ran
    conn = catalog(root)
    try:
        crashed = next(c.session_id for c in repo.sessions(conn) if c.session_id != older)
    finally:
        conn.close()

    summary = recover_now(root)

    assert summary.adopted == ["2020-08-05_001.JPG"]
    assert sorted(summary.reported) == sorted([older, crashed])
    assert report_lines(root, older) == expected(
        root, older, first, (1, 0, 0, 0), RENAMED_IMG_1, undo=True, found=True
    )
    assert report_lines(root, crashed) == expected(
        root,
        crashed,
        second,
        (1, 0, 0, 0),
        ["## Files renamed", "", "- `IMG_2.JPG` -> `2020-08-05_001.JPG`", ""],
        undo=True,
    )


def test_a_session_only_phase_three_finalized_stopped_part_way(tmp_path, set_fault):
    """The plan operand alone: every rename was done and resolved, so phases 1 and 2 have nothing
    to do; only phase 3 closes the plan the crash left `applying`. That is still recovery work."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:before_plan_finalize:1")
    session = only_session(root)

    summary = recover_now(root)

    assert (summary.adopted, summary.failed, summary.reverted) == ([], [], [])
    assert summary.finalized == [(plan_id, "applied")]
    assert report_lines(root, session) == expected(
        root, session, plan_id, (1, 0, 0, 0), RENAMED_IMG_1, undo=True
    )


def test_a_session_only_phase_two_healed_stopped_part_way(
    tmp_path, set_fault, monkeypatch, allow_split_unit
):
    """The heal operand alone: the first pass resolved and finalized everything but could not put
    the .JPG back (its old name was taken) and died before the report. The next pass does ONE
    thing - phase 2 puts the .JPG back - and that is recovery work, so the report it writes says
    the run stopped part-way."""
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    crash(root, plan_id, set_fault, "crash:mid_unit_revert:1")
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    session = only_session(root)

    def die(*args, **kwargs):
        raise SystemExit("killed before the report")

    with monkeypatch.context() as patch:
        patch.setattr(recover_module, "report_interrupted_runs", die)
        with pytest.raises(SystemExit):
            recover_now(root)
    assert not report_path(root, session).exists()
    (root / "IMG_1.JPG").unlink()

    summary = recover_now(root)

    assert (summary.adopted, summary.failed, summary.finalized) == ([], [], [])
    assert summary.reverted == ["IMG_1.JPG"]
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (0, 1, 1, 0),
        [
            "## Files put back under their original names",
            "",
            "- `IMG_1.JPG`",
            "",
            "## Files pam did not rename",
            "",
            "- `IMG_1.MOV`",
            "",
        ],
    )


def test_a_session_an_older_pam_recovered_then_followed_by_another_is_reported_neutral(
    tmp_path, set_fault, monkeypatch
):
    """F2, the reviewer's Q10 variant (c): an older pam recovered session 2 without a report and
    the user carried on - a new file, a new plan, session 3 renamed it. This pam writes session
    2's report late, from the catalog as it stands; the neutral line says exactly that, and the
    title does not call it interrupted. Session 3's report is left as it was."""
    root, first = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    crash(root, first, set_fault, "crash:after_rename:1")
    session = only_session(root)
    with monkeypatch.context() as older_pam:
        older_pam.setattr(recover_module, "report_interrupted_runs", lambda *args: [])
        recover_now(root)
        later = approve_and_apply(second_plan(root, "IMG_3.JPG", "2021:09:06 11:02:03"), root)
    assert not report_path(root, session).exists()
    after = report_path(root, later.session_id)
    before = (after.read_bytes(), after.stat().st_mtime_ns)

    assert recover_now(root).reported == [session]

    assert report_lines(root, session) == expected(
        root,
        session,
        first,
        (1, 0, 1, 0),
        [
            *RENAMED_IMG_1,
            "## Files pam did not rename",
            "",
            "- `IMG_2.JPG`",
            "",
        ],
        undo=True,
        found=True,
    )
    assert (after.read_bytes(), after.stat().st_mtime_ns) == before


def test_the_neutral_line_names_the_time_it_was_written(tmp_path, monkeypatch):
    """F1's line, spelled whole with the clock pinned (the `FOUND` regex admits any time)."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    session = approve_and_apply(plan_id, root).session_id
    report_path(root, session).unlink()

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 5, 6, 7, 8, 9)

    monkeypatch.setattr(recovery_report, "datetime", Later)
    recover_now(root)

    lines = report_path(root, session).read_text(encoding="utf-8").split("\n")
    assert (
        "- pam found no complete report for this session and wrote this one at 2030-05-06 07:08,"
        " from the catalog as it stood then."
    ) in lines


# --- Task 7 follow-up review (M3850, M3852): each phase's operand alone, as the ruling spells it ---


def test_a_session_only_phase_one_resolved_stopped_part_way(tmp_path, set_fault):
    """Review M3850: phase 1's own contribution could be deleted with the suite green, because in
    every state pam reaches, the plan of a row phase 1 resolves is still `applying` and phase 3
    covers it in the same pass. BY HAND, the plan is closed first (`partially_applied`), so phase
    1's resolution of the `intent` row is this pass's ONLY recovery work - and the ruling says a
    session phase 1 resolved stopped part-way."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    crash(root, plan_id, set_fault, "crash:after_rename:1")
    session = only_session(root)
    conn = catalog(root)
    try:
        conn.execute("UPDATE plans SET status = 'partially_applied' WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()

    summary = recover_now(root)

    assert summary.finalized == []
    assert summary.adopted == ["2019-07-04_001.JPG"]
    assert report_lines(root, session) == expected(
        root, session, plan_id, (1, 0, 0, 0), RENAMED_IMG_1, undo=True
    )


def test_a_pass_that_could_not_put_a_member_back_is_not_called_interrupted(
    tmp_path, set_fault, monkeypatch, allow_split_unit
):
    """Review M3852: phase 2's operand is a member PUT BACK, not one pam tried and could not. The
    first pass resolved and finalized everything, could not put the .JPG back (its old name is
    taken) and died before the report. The next pass still cannot: it strands the .JPG again and
    does no recovery work, so the report it writes is the neutral one."""
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    crash(root, plan_id, set_fault, "crash:mid_unit_revert:1")
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    session = only_session(root)

    def die(*args, **kwargs):
        raise SystemExit("killed before the report")

    with monkeypatch.context() as patch:
        patch.setattr(recover_module, "report_interrupted_runs", die)
        with pytest.raises(SystemExit):
            recover_now(root)

    summary = recover_now(root)

    assert (summary.adopted, summary.failed, summary.finalized, summary.reverted) == (
        [],
        [],
        [],
        [],
    )
    assert summary.stranded == ["2019-07-04_001.JPG"]
    assert report_lines(root, session) == expected(
        root,
        session,
        plan_id,
        (0, 0, 1, 1),
        [
            "## Files pam did not rename",
            "",
            "- `IMG_1.MOV`",
            "",
            "## Files unresolved",
            "",
            "These are under their new names while the group they belong to is not. Nothing was"
            " lost. pam puts them back the next time it runs and can.",
            "",
            "- `2019-07-04_001.JPG`",
            "",
        ],
        found=True,
    )

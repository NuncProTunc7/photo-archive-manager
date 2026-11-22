"""M15's construction hunt (plan Task 15, the controller's Task 15A rulings): BUILD the violation.

WHY THIS FILE EXISTS. A property no single module owns is invisible to a mutation pass scoped to one
task's files (CLAUDE.md, "the mutation gate"). M15 made pam a program that is INSTALLED, UPDATED and
REPLACED: a workspace now outlives the binary that wrote it (AM5's tenth question), an installer and
`pam update` now run beside a pam that is open (E11), a catalog now carries a ledger outside itself
(E12), and a unit can go cloud-only between approval and Rename (C2). Each question below is taken
against what M15 changed, and the violation is BUILT end to end, driving the real commands
(`pam.cli.main`, the real `pam start` in a subprocess, a real `pam apply` waiting at its prompt, a
compiled test build of the real installer), and watching the one dangerous call -
`perform_verified_rename` - with a spy (M697).

WHERE A QUESTION IS ALREADY ANSWERED BY A DETERMINISTIC TEST, IT IS CITED, NOT REBUILT (the
controller's ruling 3). `CITED` names each such test by module and name; the collection check below
imports every cited module and fails if a cited test is gone or renamed, so a citation cannot rot
into a claim (M1670's lesson: an instrument nobody registers stops being measured).

THE QUESTIONS (`docs/implementation-notes/m14.md` section 4, plus AM5's Q10 and the plan's Q5 for
distribution):

  Q1  Which filter dimensions are not unit-uniform? M15's new one is CLOUD STATE (C2): one member of
      a unit can go cloud-only alone. CITED: Task 2 probes the unit's COMPLETE frozen membership,
      no-op members included, through both routes.
  Q2  Which members of a unit does each command not see? The no-op member (`actionable()` and
      `_units_of` exclude it). CITED: Task 2's no-op fixture; Task 7's recovery report lists a
      plan's approved items the crash never reached.
  Q3  Does every write path still resolve through `_widen_to_units` over the whole plan? M15 added
      no plan write route (the M14 hunt's live route table is still exact - CITED); its new path is
      `pam update`, built here: offered, confirmed, downloaded and launched, it changes no byte of a
      catalog and no line of the ledger.
  Q4  Which faces does an unnamed-cluster operation see? The faces write code changed only in how it
      opens the catalog (E1). CITED: M14's construction through `pam start` (it runs today's code),
      and Task 8's refusal row for each faces consumer.
  Q5  What does a write in one command do to an artefact another produced, or a reader still open?
      CITED: Task 7 - an M14-format report is never rewritten. And the plan's Q5 for distribution,
      built here: `pam update` while a real `pam start` serves; the installer while a real
      `pam apply` waits at its prompt; and (CITED) a pam started while the installer copies.
  Q6  Does anything turn an undecided item into a rename? Built: the retry route after a cloud-only
      skip (scan -> plan) renames nothing until the person approves again; and Q10's open plan: an
      approval an OLDER pam recorded is honoured, and what it left undecided is not renamed.
  Q7  Which request reaches a rename without a person? M15's new dangerous action is the update's
      installer launch. CITED: only the typed word `update` launches it; a closed stdin cancels;
      M14's ten requests against the page (they run today's server).
  Q8  What does pam decide from the catalog's copy of a fact the OS can change? C2 IS this question's
      M15 answer. CITED: the real `FILE_ATTRIBUTE_OFFLINE`, set after approval, through both routes.
  Q9  Does any path to the verified rename list a flat folder more than once per run? CITED: M14's
      AST enumeration of every call to the rename and its eight paths (today's apply, C2 included).
  Q10 What does this pam do with a catalog, plan, journal or report an OLDER pam wrote? Built from
      REAL older workspaces: `tests/fixtures/old_workspaces/`, written by `v0.1-m13` and `v0.1-m14`'s
      own code (`tools/make_old_workspaces.py`): an open plan opened, applied and undone; an
      interrupted journal (an `intent` row with no resolution) recovered and reported with the older
      report left byte for byte; this pam's genuine forward migration of the `v0.1-m13` catalog
      refused by an older reader that has E1; the pre-1.0 downgrade gap stated in the README.

EVERY EXPECTATION IS SPELLED FROM THE SPEC, THE PLAN OR THE FIXTURE, never read back from the
production call it checks: names, statuses and migration names are written out here, the journal and
the plans are read with the suite's own SQL, and the disk is hashed here.
"""

import hashlib
import importlib
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_apply_cloud_only_now as c2
import test_f12_unit_membership as f12
import test_m14_safety_constructions as m14
import test_update_command as upd
import test_webui_rename_stage2 as stage2
from archives import FAKE_COMMAND, put
from tls_server import TlsServer

from pam import run_registry
from pam.catalog import db as db_mod
from pam.catalog import ledger
from pam.cli import main
from pam.naming.planner import run_plan
from pam.paths import record_archive_root, workspace_for
from pam.scan.runner import run_scan
from pam.update.command import UpdateError

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
spy = m14.spy
cloud = c2.cloud
watch = c2.watch
VERIFY_CLEAN = "Done. 12 checks, everything pam recorded looks right."

# --- Q10: the workspaces an OLDER pam wrote ---------------------------------------------------

OLD = TESTS / "fixtures" / "old_workspaces"
# AM5: the two tags before this milestone, spelled by hand (never globbed from the fixture folder).
OLDER_TAGS = ("v0.1-m13", "v0.1-m14")
# Every migration a released pam shipped, spelled from design 4 (rev 34) and the tags: `v0.1-m13`
# shipped the first seven, `v0.1-m14` added 0008 (M14 Task 0C, OD-6). M15 adds none.
RELEASED = (
    "0001_init.sql",
    "0002_scanner.sql",
    "0003_video_reread.sql",
    "0004_plan_unit_key.sql",
    "0005_plan_already_conforming.sql",
    "0006_event_label_source.sql",
    "0007_face_review_state.sql",
    "0008_files_rel_path_nocase.sql",
)
OLDER_SCHEMA = {"v0.1-m13": RELEASED[:7], "v0.1-m14": RELEASED}
NEWEST = "0008_files_rel_path_nocase.sql"
# The fixture's archive (the generator's DATES) and the default template's names, spelled.
ORIGINAL = ("A.JPG", "B.JPG", "P.JPG", "P.NEF")
NEW = {
    "A.JPG": "2018-01-01_001.JPG",
    "B.JPG": "2020-08-05_001.JPG",
    "P.JPG": "2019-07-04_001.JPG",
    "P.NEF": "2019-07-04_001.NEF",
}
# E1's sentence (design 4 / 7.5, M15 Task 8), spelled here.
NEWER = (
    "This catalog was written by a newer pam (it has update {update}, which this pam does not"
    " know). Install that version again, or a newer one."
)
# E6's report lines (M15 Task 7), spelled from the brief and the rulings.
REPORT_FORMAT = "- Report format: 2"
REPORT_END = "-- end of report --"


def _lay_down(tmp_path: Path, tag: str, case: str) -> tuple[Path, object]:
    """The older pam's archive (every file's text and mtime) and its workspace, at a new path."""
    spec = json.loads((OLD / tag / case / "archive.json").read_text(encoding="utf-8"))
    assert (spec["tag"], spec["case"]) == (tag, case)
    root = tmp_path / "photos"
    root.mkdir()
    for rel, entry in spec["files"].items():
        path = root / rel
        path.write_bytes(entry["text"].encode("utf-8"))
        os.utime(path, ns=(entry["mtime_ns"], entry["mtime_ns"]))
    ws = workspace_for(root)
    shutil.copyfile(OLD / tag / case / "catalog.db", ws.db_path)
    reports = OLD / tag / case / "reports"
    if reports.is_dir():
        for report in reports.glob("*.md"):
            shutil.copyfile(report, ws.reports_dir / report.name)
    record_archive_root(ws)
    return root, ws


def _sql(ws, query: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(ws.db_path)
    try:
        return [tuple(row) for row in conn.execute(query, args)]
    finally:
        conn.close()


def _migrations(ws) -> tuple[str, ...]:
    return tuple(
        r[0] for r in _sql(ws, "SELECT filename FROM schema_migrations ORDER BY filename")
    )


def _statuses(ws, plan_id: int) -> dict[str, str]:
    rows = _sql(ws, "SELECT old_rel_path, status FROM plan_items WHERE plan_id = ?", (plan_id,))
    return dict(rows)


def _journal(ws) -> list[tuple]:
    return _sql(
        ws, "SELECT session_id, old_rel_path, new_rel_path, state FROM journal ORDER BY id"
    )


def _disk(root: Path) -> dict[str, str]:
    """rel_path -> sha256 of every file. The fixture's files hold their ORIGINAL name as text, so
    the map also says where each one is now."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _photos(disk: dict[str, str]) -> set[str]:
    """The photographs in a `_disk` map, so a test can pin it against names spelled from the
    fixture: an empty or partial map is then a failure, never two empty maps agreeing."""
    return {rel for rel in disk if not rel.endswith(".tags.json")}


def _media(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if not p.name.endswith(".tags.json")}


def _typed(monkeypatch, word: str = "apply") -> None:
    monkeypatch.setattr("builtins.input", lambda *args: word)


def _cli(capsys, *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_q10_the_fixtures_are_the_older_pams_and_hold_what_the_hunt_needs():
    """The fixtures' own floor, spelled from the generator's two cases: each tag's catalog carries
    exactly that tag's migrations, the open plan holds one approval, the interrupted journal ends in
    an `intent` row with no resolution."""
    for tag in OLDER_TAGS:
        for case in ("open", "interrupted"):
            assert (OLD / tag / case / "catalog.db").is_file(), (tag, case)
            # `immutable=1`: a read-only open of a WAL catalog would otherwise create `-wal` and
            # `-shm` files beside the committed fixture.
            fixture = (OLD / tag / case / "catalog.db").as_posix()
            conn = sqlite3.connect(f"file:{fixture}?mode=ro&immutable=1", uri=True)
            try:
                names = tuple(
                    r[0]
                    for r in conn.execute("SELECT filename FROM schema_migrations ORDER BY 1")
                )
                journal = list(
                    conn.execute("SELECT old_rel_path, state FROM journal ORDER BY id")
                )
                approved = list(
                    conn.execute(
                        "SELECT old_rel_path FROM plan_items WHERE plan_id = 1"
                        " AND status IN ('approved', 'applied')"
                    )
                )
            finally:
                conn.close()
            assert names == OLDER_SCHEMA[tag], (tag, case, names)
            assert approved == [("A.JPG",)], (tag, case)
            expected = [] if case == "open" else [("A.JPG", "done"), ("B.JPG", "intent")]
            assert journal == expected, (tag, case, journal)
        assert (OLD / tag / "interrupted" / "reports").is_dir()
    assert sorted(OLD.rglob("catalog.db-*")) == [], "a test wrote beside the committed fixtures"


@pytest.mark.parametrize("tag", OLDER_TAGS)
def test_q10_an_open_plan_an_older_pam_wrote_renames_only_its_approval_and_undoes(
    tmp_path, capsys, monkeypatch, spy, tag
):
    """Q10 x Q6. The older pam scanned, planned and recorded ONE approval (A.JPG); this pam reads
    the workspace (`pam status`, lock-free, migrating nothing), applies that plan - renaming A.JPG
    and nothing it left undecided - migrating the `v0.1-m13` catalog forward on the way (0008, a
    ledger line first: E12), undoes it to the exact original, and `pam verify` is clean."""
    root, ws = _lay_down(tmp_path, tag, "open")
    before = _disk(root)
    assert _photos(before) == set(ORIGINAL)  # the oracle's floor (M5010a)
    assert _migrations(ws) == OLDER_SCHEMA[tag]

    code, out, err = _cli(capsys, "status", "--archive", str(root))
    assert code == 0, err
    assert _migrations(ws) == OLDER_SCHEMA[tag]  # a reader migrates nothing

    _typed(monkeypatch)
    code, out, err = _cli(capsys, "apply", "1", "--archive", str(root))
    assert code == 0, out + err
    assert spy == [("A.JPG", NEW["A.JPG"])]
    assert _statuses(ws, 1) == {
        "A.JPG": "applied",
        "B.JPG": "proposed",
        "P.JPG": "proposed",
        "P.NEF": "proposed",
    }
    assert _migrations(ws) == RELEASED
    assert ledger.read_level() == NEWEST
    assert _media(root) == {NEW["A.JPG"], "B.JPG", "P.JPG", "P.NEF"}

    session = f12._session_id(out)
    code, out, err = _cli(capsys, "undo", session, "--archive", str(root))
    assert code == 0, out + err
    assert spy[1:] == [(NEW["A.JPG"], "A.JPG")]
    assert _disk(root) == before
    code, out, err = _cli(capsys, "verify", "--archive", str(root))
    assert code == 0 and VERIFY_CLEAN in out, out + err


@pytest.mark.parametrize("tag", OLDER_TAGS)
def test_q10_an_interrupted_run_an_older_pam_left_is_recovered_reported_and_renames_nothing(
    tmp_path, capsys, monkeypatch, spy, tag
):
    """Q10 x Q5 x Q6. The older pam finished one session (A.JPG, with its own report) and stopped
    the next after committing B.JPG's `intent` row (`PAM_FAULT=crash:after_intent:1`). This pam's
    next archive-scoped command recovers first (design 7.2): the row is resolved without a rename
    (B.JPG never moved), the plan is no longer `applying`, the approved items the crash never
    reached are NOT renamed by recovery, a recovery report in E6's format names the session - and
    the older pam's own report of the finished session stays byte for byte (it has no
    `report_format` field, so it is final and never rewritten: Task 7's trap)."""
    root, ws = _lay_down(tmp_path, tag, "interrupted")
    journal = _journal(ws)
    first, second = journal[0][0], journal[1][0]
    assert journal == [
        (first, "A.JPG", NEW["A.JPG"], "done"),
        (second, "B.JPG", NEW["B.JPG"], "intent"),
    ]
    older_report = ws.reports_dir / f"session-{first}.md"
    older_bytes = older_report.read_bytes()
    assert REPORT_FORMAT not in older_bytes.decode("utf-8")
    before = _disk(root)
    assert _photos(before) == {NEW["A.JPG"], "B.JPG", "P.JPG", "P.NEF"}  # floor (M5010a)

    code, out, err = _cli(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    assert spy == []  # recovery put nothing back and renamed nothing: B.JPG never moved
    assert _disk(root) == before
    states = [row[3] for row in _journal(ws)]
    assert states[0] == "done" and states[1] != "intent", states
    plan_two = _sql(ws, "SELECT status FROM plans WHERE id = 2")[0][0]
    assert plan_two not in ("applying", "open", "applied"), plan_two
    assert _media(root) == {NEW["A.JPG"], "B.JPG", "P.JPG", "P.NEF"}

    recovered = (ws.reports_dir / f"session-{second}.md").read_text(encoding="utf-8")
    lines = recovered.splitlines()
    assert lines[0] == f"# Renaming run {second} (interrupted)", lines[:3]
    assert REPORT_FORMAT in lines
    assert lines[-1] == REPORT_END
    assert "- 0 file(s) renamed" in lines
    assert "- 3 file(s) not renamed" in lines  # B.JPG, and the pair the crash never reached
    assert older_report.read_bytes() == older_bytes

    code, out, err = _cli(capsys, "undo", first, "--archive", str(root))
    assert code == 0, out + err
    assert spy == [(NEW["A.JPG"], "A.JPG")]
    assert _media(root) == set(ORIGINAL)
    assert older_report.read_bytes() == older_bytes
    code, out, err = _cli(capsys, "verify", "--archive", str(root))
    assert code == 0 and VERIFY_CLEAN in out, out + err


def test_q10_this_pams_forward_migration_is_refused_by_an_older_reader_and_nothing_changes(
    tmp_path, capsys, monkeypatch, spy
):
    """The other direction. This pam opens the `v0.1-m13` catalog with a writing command and
    migrates it forward for real (0008 applied by `migrate`, the ledger line reserved first). Then
    an OLDER pam that has E1 - this pam's code with `v0.1-m13`'s seven migrations, spelled above -
    opens it through every kind of command: each refuses with E1's sentence naming 0008, exit 1,
    and no byte of the catalog file or of the ledger changes, and nothing is renamed. (Released pams
    before 1.0 have no E1: that is the downgrade gap the README states, below.)"""
    root, ws = _lay_down(tmp_path, "v0.1-m13", "open")
    code, out, err = _cli(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    assert _migrations(ws) == RELEASED
    assert ledger.read_level() == NEWEST

    older = tmp_path / "v0.1-m13-migrations"
    older.mkdir()
    for name in OLDER_SCHEMA["v0.1-m13"]:
        shutil.copyfile(db_mod._MIGRATIONS_DIR / name, older / name)
    monkeypatch.setattr(db_mod, "_MIGRATIONS_DIR", older)
    _typed(monkeypatch)
    catalog_bytes = ws.db_path.read_bytes()
    ledger_bytes = ledger.ledger_path().read_bytes()
    refusal = "x " + NEWER.format(update="0008_files_rel_path_nocase")
    commands = (
        ["status"],
        ["plan"],
        ["verify"],
        ["apply", "2"],
        ["undo", "s-0"],
        ["report", "skipped"],
    )
    for argv in commands:
        code, out, err = _cli(capsys, *argv, "--archive", str(root))
        # A writing command names its session log first (design 9.2); the refusal is the last line.
        assert (code, err.strip().splitlines()[-1]) == (1, refusal), (argv, code, out, err)
    assert ws.db_path.read_bytes() == catalog_bytes
    assert ledger.ledger_path().read_bytes() == ledger_bytes
    assert spy == []


def test_q10_the_readme_states_the_pre_1_0_downgrade_gap():
    """Plan Task 15: "the pre-M15 downgrade gap stated in the README" (F-W1). A pam built before
    1.0 does not check the catalog's updates, so it opens a catalog a newer pam wrote without
    refusing it. Spelled from the plan's F-W1, not from the README: the public README must say,
    in one paragraph, that a pam OLDER THAN 1.0 opens a catalog a NEWER pam wrote WITHOUT REFUSING."""
    # In this repository the public README is `public/README.md`; the export writes it at the root
    # (`tools/export_public.py`), so in the public repository it is `README.md`.
    readme = REPO / "public" / "README.md"
    if not (REPO / "public").is_dir():
        readme = REPO / "README.md"
    text = readme.read_text(encoding="utf-8")
    paragraphs = [p for p in text.split("\n\n") if "older than 1.0" in p]
    assert len(paragraphs) == 1, "the README states the pre-1.0 downgrade gap in one paragraph"
    paragraph = " ".join(paragraphs[0].split())
    assert "newer pam" in paragraph and "without refusing" in paragraph, paragraph


# --- Q3: M15's new path, `pam update`, writes no catalog --------------------------------------


@pytest.fixture
def github():
    with TlsServer() as server:
        yield upd.FakeGitHub(server)


def _planned(tmp_path: Path) -> Path:
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in c2.DATES.items():
        put(root, name, date)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert run_plan(root).plan_id == 1
    return root


def _workspace_bytes(root: Path) -> dict[str, bytes]:
    ws = workspace_for(root)
    return {
        p.relative_to(ws.root).as_posix(): p.read_bytes()
        for p in sorted(ws.root.rglob("*"))
        if p.is_file() and p.parent.name != "logs"
    }


def test_q3_a_confirmed_pam_update_changes_no_catalog_byte_and_no_ledger_line(
    tmp_path, github, spy
):
    """`pam update` - offered, confirmed, downloaded, checked and launched (the launch a spy) -
    beside a workspace with an open plan: no byte of the workspace (catalog, reports, pam.toml)
    and no byte of the ledger changes, and nothing is renamed. "It makes no change to your
    catalogs" (E2) measured on the files, not read off the sentence."""
    root = _planned(tmp_path)
    ledger_before = ledger.ledger_path().read_bytes()
    workspace_before = _workspace_bytes(root)
    run = upd.Run(github, tmp_path)
    github.serve_latest(github.publish("1.1.0"))
    assert run() == 0
    assert len(run.launched) == 1 and run.confirmed == 1, run.lines
    assert _workspace_bytes(root) == workspace_before
    assert ledger.ledger_path().read_bytes() == ledger_before
    assert spy == []


# --- Q5 for distribution ----------------------------------------------------------------------


def test_q5_pam_update_while_a_real_pam_start_serves_is_refused_before_any_request(
    tmp_path, github
):
    """`pam start` in its own process (registered in E11's run registry), serving. `pam update`
    with the REAL exclusion (the run registry, not a stub) refuses before any lookup - nothing
    fetched, nothing launched - and the review server keeps serving. Once it is stopped, the same
    update proceeds to its offer."""
    root = _planned(tmp_path)
    run = upd.Run(github, tmp_path)
    del run.kwargs["another_pam_running"]  # the production default: the real registry
    github.serve_latest(github.publish("1.1.0"))
    server = stage2._start(stage2._env(tmp_path), "start", str(root))
    try:
        assert run_registry.another_pam_running()
        with pytest.raises(UpdateError) as refused:
            run(check=True)
        assert str(refused.value) == (
            "pam cannot update now: another pam is running. Close it (and stop pam start), then"
            " run pam update again. Nothing was changed."
        )
        assert github.server.lookups == [] and run.nothing_downloaded_or_launched()
        assert server.get("api/ping")[0] == 200  # the open review server is untouched
    finally:
        server.close()
    assert not run_registry.another_pam_running()
    assert run(check=True) == 0
    assert any("1.1.0" in line for line in run.lines), run.lines
    assert run.nothing_downloaded_or_launched()


APPLY_CHILD = "import sys; from pam.entry import main; sys.exit(main(sys.argv[1:]))"
REFUSED_AT_START = (
    1  # Inno Setup 6.7.4: InitializeSetup refused (tests/test_installer_behaviour)
)


@pytest.mark.inno
def test_q5_the_installer_while_pam_apply_waits_at_its_prompt_is_refused_and_apply_goes_on(
    tmp_path,
):
    """A REAL `pam apply` (the console entry, `pam.entry.main`) waits at its typed confirmation,
    registered in a test install root. A compiled test build of the real installer is refused at
    its start (E11) and installs nothing. Nothing is renamed while the prompt waits; the person
    then types `apply` and the run renames exactly the one approved file; the installer, run
    again, installs."""
    sys.path.insert(0, str(REPO / "tools" / "installer"))
    import harness
    import test_installer_behaviour as inst_tests
    from inno_stack import iscc_path

    before_env = harness.real_environment()
    root = _planned(tmp_path)
    ws = workspace_for(root)
    conn = sqlite3.connect(ws.db_path)
    conn.execute(
        "UPDATE plan_items SET status='approved' WHERE plan_id=1 AND old_rel_path='A.JPG'"
    )
    conn.commit()
    conn.close()
    disk = _disk(root)
    assert _photos(disk) == set(ORIGINAL)  # floor (M5010a)
    inst = harness.TestInstall(tmp_path / "w", iscc_path())
    try:
        exe = inst_tests.installer(inst)
        env = dict(os.environ, PAM_RUN_REGISTRY=str(inst.root), PYTHONIOENCODING="utf-8")
        child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [stage2.PYTHON, "-c", APPLY_CHILD, "apply", "1", "--archive", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        try:
            deadline = time.monotonic() + 60
            while not run_registry.others_running(root=inst.root):
                assert child.poll() is None, child.communicate()
                assert time.monotonic() < deadline, "pam apply never registered"
                time.sleep(0.05)
            assert inst.run(exe, "/LOG=" + str(inst.work / "setup.log")) == REFUSED_AT_START
            assert not inst.installed()
            assert "pam refused: pam is running" in inst_tests.log_text(inst.work / "setup.log")
            assert _disk(root) == disk  # the prompt still waits; nothing renamed
            out, err = child.communicate("apply\n", timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(30)
        assert child.returncode == 0, out + err
        assert _media(root) == {NEW["A.JPG"], "B.JPG", "P.JPG", "P.NEF"}
        assert inst.run(exe) == 0
        assert inst.installed()
    finally:
        assert inst.cleanup(), f"the test's uninstall key {inst.key} is still there"
    assert harness.real_environment() == before_env


# --- Q6: the retry route after a cloud-only skip ----------------------------------------------


@c2.windows_only
def test_q6_the_retry_route_after_a_cloud_only_skip_renames_nothing_without_a_new_approval(
    tmp_path, capsys, monkeypatch, cloud, watch
):
    """C2 x F1. A pair skipped because P.NEF went cloud-only after approval (its items `failed`,
    the plan `partially_applied`). P.NEF is kept on the device again. Re-applying the old plan is
    refused (it is terminal); the retry route AM2 names - read again, plan again - gives a plan in
    which the pair is UNDECIDED, and applying it before anyone approves is refused. Nothing of the
    pair is renamed and no intent row is written until the person approves again."""
    root, plan_id = c2._archive(tmp_path)
    first = c2._rename(
        "cli", root, plan_id, lambda: cloud.free(root / "P.NEF"), capsys, monkeypatch
    )
    assert first.renamed == 2
    cloud.keep(root / "P.NEF")
    watch.renames.clear()
    watch.intents.clear()

    _typed(monkeypatch)
    code, out, err = _cli(capsys, "apply", str(plan_id), "--archive", str(root))
    assert code != 0, out
    run_scan(root, exiftool_command=FAKE_COMMAND)
    again = run_plan(root).plan_id
    assert again is not None and again != plan_id
    ws = workspace_for(root)
    pair = {k: v for k, v in _statuses(ws, again).items() if k in c2.PAIR}
    assert pair == {"P.JPG": "proposed", "P.NEF": "proposed"}
    code, out, err = _cli(capsys, "apply", str(again), "--archive", str(root))
    assert code != 0, out
    assert watch.renames == [] and watch.intents == []
    assert {"P.JPG", "P.NEF"} <= _media(root)


# --- the citations ----------------------------------------------------------------------------
# Each question's tests in OTHER modules that already build it against today's code, by module
# and name. Checked at collection: a cited test that is gone or renamed, that names a helper rather
# than a test, or that is cited twice under one question fails the run here (M5100, M5101).

CITED = {
    "Q1": (
        "test_apply_cloud_only_now::test_a_mixed_plan_renames_the_rest_and_skips_the_unit_whole",
        "test_apply_cloud_only_now::test_a_unit_whose_only_cloud_only_member_is_its_no_op_is_skipped_whole",
        "test_m14_safety_constructions::test_q1_except_ids_naming_one_member_is_widened_back_and_named_overridden",
    ),
    "Q2": (
        "test_apply_cloud_only_now::test_a_unit_whose_only_cloud_only_member_is_its_no_op_is_skipped_whole",
        "test_recovery_report::test_only_this_plans_unrenamed_items_without_a_journal_row_are_listed",
        "test_m14_safety_constructions::test_q2_the_members_the_page_never_showed_are_counted_confirmed_and_renamed",
    ),
    "Q3": (
        "test_m14_safety_constructions::test_q3_the_live_route_table_of_pam_start_is_exactly_the_hand_written_one",
        "test_m14_safety_constructions::test_q3_every_plan_write_route_of_pam_start_writes_the_whole_unit_from_one_member",
        "test_update_command::test_check_names_both_versions_and_downloads_nothing",
    ),
    "Q4": (
        "test_m14_safety_constructions::test_q4_label_and_dismiss_through_pam_start_write_exactly_the_faces_the_queue_offered",
        "test_catalog_compat::test_every_consumer_refuses_a_newer_catalog_and_writes_nothing",
    ),
    "Q5": (
        "test_recovery_report::test_an_m14_report_of_an_interrupted_run_stays_byte_identical",
        "test_installer_behaviour::test_setup_holds_setup_lock_from_its_start_and_a_pam_started_then_refuses",
        "test_installer_behaviour::test_uninstall_refuses_while_a_pam_runs_and_removes_nothing",
        "test_run_registry::test_the_race_a_registered_pam_is_seen_by_setup_then_refuses",
        "test_update_command::test_a_pam_started_while_the_prompt_waits_is_refused_before_the_download",
    ),
    "Q6": (
        "test_m14_safety_constructions::test_q6_recovery_after_a_page_rename_dies_renames_no_undecided_item",
    ),
    "Q7": (
        "test_update_command::test_only_the_word_update_confirms",
        "test_update_command::test_a_closed_stdin_cancels",
        "test_update_command::test_a_cancelled_prompt_downloads_and_launches_nothing",
        "test_m14_safety_constructions::test_q7_no_request_without_a_person_at_the_page_reaches_a_rename",
    ),
    "Q8": (
        "test_apply_cloud_only_now::test_a_mixed_plan_renames_the_rest_and_skips_the_unit_whole",
        "test_apply_cloud_only_now::test_an_all_cloud_only_plan_renames_nothing",
        "test_apply_cloud_only_now::test_the_retry_route_renames_the_unit_once_it_is_local_again",
        "test_apply_cloud_only_now::test_undo_puts_back_a_file_that_became_cloud_only_after_the_rename",
    ),
    "Q9": (
        "test_m14_safety_constructions::test_q9_every_call_on_the_way_to_the_verified_rename_is_listed_and_passes_the_runs_names",
        "test_m14_safety_constructions::test_q9_each_path_to_the_verified_rename_lists_a_flat_folder_once_per_run",
    ),
    "Q10": (
        "test_catalog_compat::test_a_real_forward_schema_is_refused_by_the_shipped_set",
        "test_update_command::test_a_release_below_the_compatibility_ledger_is_refused",
        "test_installer_behaviour::test_the_ledger_decides_whether_this_payload_may_replace_pam",
        "test_installer_behaviour::test_the_ledger_also_guards_an_upgrade_and_leaves_the_previous_pam_whole",
        "test_schema_ledger::test_a_copied_already_migrated_catalog_is_registered_on_first_open",
    ),
}

# This module's own constructions, by question. Every test here is in exactly one list.
HUNT = {
    "Q3": ("test_q3_a_confirmed_pam_update_changes_no_catalog_byte_and_no_ledger_line",),
    "Q5": (
        "test_q5_pam_update_while_a_real_pam_start_serves_is_refused_before_any_request",
        "test_q5_the_installer_while_pam_apply_waits_at_its_prompt_is_refused_and_apply_goes_on",
    ),
    "Q6": (
        "test_q6_the_retry_route_after_a_cloud_only_skip_renames_nothing_without_a_new_approval",
    ),
    "Q10": (
        "test_q10_the_fixtures_are_the_older_pams_and_hold_what_the_hunt_needs",
        "test_q10_an_open_plan_an_older_pam_wrote_renames_only_its_approval_and_undoes",
        "test_q10_an_interrupted_run_an_older_pam_left_is_recovered_reported_and_renames_nothing",
        "test_q10_this_pams_forward_migration_is_refused_by_an_older_reader_and_nothing_changes",
        "test_q10_the_readme_states_the_pre_1_0_downgrade_gap",
    ),
}
# The floors, spelled from what each question builds or cites (Task 15A rulings), never len().
QUESTIONS = tuple(f"Q{n}" for n in range(1, 11))
FLOORS = {
    "Q1": 3,
    "Q2": 3,
    "Q3": 4,
    "Q4": 2,
    "Q5": 7,
    "Q6": 2,
    "Q7": 4,
    "Q8": 4,
    "Q9": 2,
    "Q10": 10,
}
FLOORS_TEST = "test_the_hunt_holds_every_question_at_its_floor"


def _count(question: str) -> int:
    """DISTINCT tests: a citation listed twice counts once (M5101)."""
    return len(set(CITED.get(question, ()))) + len(set(HUNT.get(question, ())))


def _missing_citations(cited: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """Every citation that does not name a TEST: gone or renamed; a helper (no `test_` prefix, or
    not a function DEFINED in the cited module - M5100: `callable` alone let a citation rot into
    `_archive` and still count); or listed twice under one question (M5101)."""
    missing = []
    for question, names in (CITED if cited is None else cited).items():
        if len(set(names)) != len(names):
            missing.append(f"{question}: a citation is listed twice")
        for name in names:
            module_name, test_name = name.split("::")
            module = importlib.import_module(module_name)
            found = getattr(module, test_name, None)
            if not (
                test_name.startswith("test_")
                and inspect.isfunction(found)
                and found.__module__ == module.__name__
            ):
                missing.append(name)
    return missing


assert set(CITED) | set(HUNT) == set(QUESTIONS) == set(FLOORS)
for _question in QUESTIONS:
    assert _count(_question) >= FLOORS[_question], (_question, _count(_question))
assert _missing_citations() == [], _missing_citations()
_DEFINED = {name for name in list(globals()) if name.startswith("test_")}
_LISTED = {name for names in HUNT.values() for name in names}
assert _LISTED | {FLOORS_TEST} == _DEFINED | {FLOORS_TEST}, sorted(_LISTED ^ _DEFINED)


def test_the_hunt_holds_every_question_at_its_floor():
    """The collection-time checks again at run time, in both directions: every test here is in
    exactly one question, every citation still names a test, every question meets its floor."""
    module = sys.modules[__name__]
    defined = {name for name in vars(module) if name.startswith("test_")} - {FLOORS_TEST}
    listed = [name for names in HUNT.values() for name in names]
    assert len(listed) == len(set(listed)), "a test listed under two questions"
    assert set(listed) == defined, sorted(set(listed) ^ defined)
    assert {q: _count(q) >= FLOORS[q] for q in QUESTIONS} == {q: True for q in QUESTIONS}
    assert _missing_citations() == []
    assert len(OLDER_TAGS) == 2 and len(RELEASED) == 8
    # The checker's own negative controls (M5106: disarmed, it passed green), spelled here and never
    # taken from CITED: a name that is gone, a helper, and one test cited twice are each reported.
    bogus = {
        "gone": ("test_update_command::test_no_such_test_m5106",),
        "helper": ("test_apply_cloud_only_now::_archive",),
        "twice": ("test_update_command::test_a_closed_stdin_cancels",) * 2,
    }
    for key, names in bogus.items():
        assert _missing_citations({key: names}) != [], key

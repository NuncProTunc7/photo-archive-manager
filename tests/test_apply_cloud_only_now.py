"""M15 Task 2 (C2 / AM2): a unit with any member cloud-only NOW is skipped whole at rename time.

THE RULING (`docs/superpowers/specs/2026-11-16-m14-review-amendments.md`, AM2; `m14.md` 2.3,
Q8-D1). Immediately before a unit's first rename, `pam apply` and the page's rename check EVERY
member of the unit with `walker.placeholder_now` - the unit's COMPLETE frozen membership, no-op
members included (`PlanPreflight.actionable()` and `apply._units_of` both exclude no-ops). A unit
with any member cloud-only now gets no journal row and no rename, its items are `failed`, and the
run counts files and units. The console, Done, History and the session report say AM2's sentence
when N > 0. `pam undo` does NOT skip: putting back a name pam gave is its promise.

THE CONSTRUCTION is Task 0B's: the REAL `FILE_ATTRIBUTE_OFFLINE` set with `SetFileAttributesW`
after the person approved (inside the CLI's typed confirmation, or between the page's Confirm and
its POST) - what OneDrive's "Free up space" leaves. Windows only; CI runs Windows.

WATCH THE DANGEROUS CALLS (M697). Every case spies on `perform_verified_rename` AND on the
journal's intent insert (`repo.insert_intents`), and asserts neither saw a member of a skipped
unit - not only that the disk is unchanged. A read spy (Task 0B's hooks) asserts the probe never
read a skipped file's content: reading a placeholder hydrates it (vision R2).

EVERY EXPECTATION IS SPELLED HERE: the names, the sentence and the report line are written out from
the fixture and AM2, never read back from `pam.execute`.

EACH CASE RUNS THROUGH BOTH ROUTES (`ROUTES`): the console's `pam apply` and the page's rename job
through `pam start`'s real, bound server.
"""

import ctypes
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import test_f12_unit_membership as f12
import test_live_placeholder as live
import test_m14_safety_constructions as m14
from archives import FAKE_COMMAND, approve_archive, catalog, put

from pam.catalog import repo
from pam.cli import main
from pam.execute import unit as unit_mod
from pam.execute.apply import ApplySummary
from pam.execute.reporter import apply_summary_lines, write_session_report
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan import walker
from pam.scan.runner import run_scan
from pam.webui import confirm_api

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="FILE_ATTRIBUTE_OFFLINE is a Windows file attribute"
)

# Task 0B's read spy, as the M14 hunt installs it (its control is that module's Q8 case).
reads = m14.reads

# AM2's sentence, VERBATIM from the amendment, with N = FILES.
AM2 = (
    "{n} files became cloud-only after they were approved and were not renamed."
    " Make them available offline and read the folder again."
)
# C6.5 (the maintainer's ruling, 2026-11-16): one file is singular, and when a skipped unit has more
# than one member the sentence goes on with TOGETHER - spelled from the ruling, never imported.
AM2_ONE = (
    "1 file became cloud-only after it was approved and was not renamed."
    " Make it available offline and read the folder again."
)
TOGETHER = " A photo and the files that go with it are skipped together."
# The session report's data line (the controller's ruling: both counts, files and units).
REPORT_COUNTS = (
    "- {files} file(s) in {units} group(s) not renamed because they became cloud-only"
)

# The two routes, by hand and asserted non-empty at collection (M743).
ROUTES = ("cli", "page")
assert len(ROUTES) == 2

# The fixture: two singles and a RAW+JPEG pair (one unit), one date each, so every name is the
# default template's `<date>_001.<EXT>`.
DATES = {
    "A.JPG": "2018:01:01 10:00:00",
    "B.JPG": "2020:08:05 09:15:00",
    "P.NEF": "2019:07:04 18:23:11",
    "P.JPG": "2019:07:04 18:23:11",
}
NEW = {
    "A.JPG": "2018-01-01_001.JPG",
    "B.JPG": "2020-08-05_001.JPG",
    "P.NEF": "2019-07-04_001.NEF",
    "P.JPG": "2019-07-04_001.JPG",
}
PAIR = ("P.JPG", "P.NEF")


# --- the instruments ----------------------------------------------------------------------------


@dataclass
class Watch:
    """What reached the two dangerous calls: (old, new) renames and intent rows' old paths."""

    renames: list[tuple[str, str]]
    intents: list[str]


@pytest.fixture()
def watch(monkeypatch) -> Watch:
    seen = Watch(renames=[], intents=[])
    real_rename = unit_mod.perform_verified_rename
    real_insert = repo.insert_intents

    def renaming(*args, **kwargs):
        seen.renames.append((kwargs["old_rel"], kwargs["new_rel"]))
        return real_rename(*args, **kwargs)

    def inserting(conn, session_id, op, entries, **kwargs):
        seen.intents.extend(entry.old_rel_path for entry in entries)
        return real_insert(conn, session_id, op, entries, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", renaming)
    monkeypatch.setattr(repo, "insert_intents", inserting)
    return seen


@pytest.fixture()
def os_opens(monkeypatch) -> list[Path]:
    """Every path handed to `os.open` - the one Python read primitive Task 0B's `reads` spy
    (`builtins.open`, `io.open`) does not hook. A probe that read a byte through it would hydrate
    a placeholder with every read-spy assertion green (M3546, S1-F2)."""
    seen: list[Path] = []
    real_open = os.open

    def opening(path, *args, **kwargs):
        seen.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", opening)
    return seen


class Cloud:
    """The REAL FILE_ATTRIBUTE_OFFLINE, set and cleared; every file restored at teardown."""

    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        self._kernel32.GetFileAttributesW.restype = ctypes.c_uint32
        self._kernel32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        self._kernel32.SetFileAttributesW.restype = ctypes.c_int
        self._before: dict[str, int] = {}

    def free(self, path: Path) -> None:
        """OneDrive's "Free up space": the file is cloud-only now."""
        before = self._kernel32.GetFileAttributesW(str(path))
        assert before != live.INVALID_FILE_ATTRIBUTES, ctypes.get_last_error()
        self._before.setdefault(str(path), before)
        offline = (before & ~live.FILE_ATTRIBUTE_NORMAL) | live.FILE_ATTRIBUTE_OFFLINE
        assert self._kernel32.SetFileAttributesW(str(path), offline)
        assert walker.is_placeholder(os.stat(path))

    def keep(self, path: Path) -> None:
        """ "Always keep on this device": the file is local again."""
        attrs = self._kernel32.GetFileAttributesW(str(path))
        assert self._kernel32.SetFileAttributesW(
            str(path), attrs & ~live.FILE_ATTRIBUTE_OFFLINE
        )
        assert not walker.is_placeholder(os.stat(path))

    def restore(self) -> None:
        for path, before in self._before.items():
            if os.path.exists(path):
                self._kernel32.SetFileAttributesW(path, before)


@pytest.fixture()
def cloud() -> Iterator[Cloud]:
    keeper = Cloud()
    try:
        yield keeper
    finally:
        keeper.restore()


# --- the fixtures -------------------------------------------------------------------------------


def _archive(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in DATES.items():
        put(root, name, date)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    items = m14._items(root, plan_id)
    assert {rel: row["new_filename"] for rel, row in items.items()} == NEW
    assert items["P.NEF"]["unit_key"] == items["P.JPG"]["unit_key"]
    assert len({row["unit_key"] for row in items.values()}) == 3
    return root, plan_id


def _no_op_archive(tmp_path: Path) -> tuple[Path, int]:
    """F12's partial unit: `2019-07-04_001.NEF` is a NO-OP item (it already has its name) and
    `2019-07-04_001.acr_.png` is an ordinary rename, one unit (`scan.ignore_stem_suffixes`)."""
    root = tmp_path / "photos"
    f12._partial_unit(root)
    plan_id = f12._open_plan_id(root)
    items = m14._items(root, plan_id)
    assert {rel: row["new_filename"] for rel, row in items.items()} == {
        "2019-07-04_001.NEF": "2019-07-04_001.NEF",
        "2019-07-04_001.acr_.png": "2019-07-04_001.png",
    }
    assert len({row["unit_key"] for row in items.values()}) == 1
    return root, plan_id


# --- the two routes -----------------------------------------------------------------------------


@dataclass
class Run:
    """What one rename said: the counts it reported, its session and its report's text."""

    renamed: int
    session: str
    words: str  # the console's stdout, or the finished job's lines
    report: str
    units: int | None = None  # the page's result carries it; the console prints only files
    files: int | None = None


def _report(root: Path, session: str) -> str:
    ws = workspace_if_exists(root)
    assert ws is not None
    return (ws.reports_dir / f"session-{session}.md").read_text(encoding="utf-8")


def _rename(
    route: str, root: Path, plan_id: int, after_confirm: Callable[[], None], capsys, monkeypatch
) -> Run:
    """Approve every undecided item, then rename through `route`; `after_confirm` runs after the
    person confirmed and before the first rename (the moment AM2 is about)."""
    approve_archive(root, plan_id)
    if route == "cli":

        def typed() -> str:
            after_confirm()
            return "apply"

        monkeypatch.setattr("builtins.input", typed)
        capsys.readouterr()
        code = main(["apply", str(plan_id), "--archive", str(root)])
        out = capsys.readouterr().out
        assert code == 0, out
        session = f12._session_id(out)
        renamed = int(out.split("renamed")[0].split()[-1])
        return Run(renamed=renamed, session=session, words=out, report=_report(root, session))
    with m14.served(root) as server:
        facts = m14._ok(server, f"plan/{plan_id}/confirmation")
        after_confirm()
        m14._ok(
            server,
            f"plan/{plan_id}/apply",
            "POST",
            {"nonce": facts["nonce"], "folder_confirmed": True},
        )
        job = m14.await_state(server, "done", "failed", "cancelled")
    assert (job["kind"], job["state"]) == ("rename", "done"), job
    session = job["session"]
    return Run(
        renamed=int(job["result"]["renamed"]),
        session=session,
        words="\n".join(job["lines"]),
        report=_report(root, session),
        units=int(job["result"]["cloud_only_units"]),
        files=int(job["result"]["cloud_only"]),
    )


def _history(root: Path) -> list[dict]:
    """History after a restart: a NEW server bound to the folder, `GET sessions`."""
    with m14.served(root) as server:
        return m14._ok(server, "sessions")["sessions"]


def _journal(root: Path) -> list[str]:
    conn = catalog(root)
    try:
        return sorted(row[0] for row in conn.execute("SELECT old_rel_path FROM journal"))
    finally:
        conn.close()


def _counts(run: Run, route: str, files: int, units: int) -> None:
    """Both counts, read through the route's own words and through the report, spelled here.
    Every case skips a unit of more than one member, so C6.5's TOGETHER follows the sentence."""
    assert files > units
    assert AM2.format(n=files) + TOGETHER in run.words, run.words
    assert AM2.format(n=files) + TOGETHER in run.report, run.report
    assert REPORT_COUNTS.format(files=files, units=units) in run.report.splitlines(), run.report
    if route == "page":
        assert (run.files, run.units) == (files, units)


# --- the cases ----------------------------------------------------------------------------------


@windows_only
@pytest.mark.parametrize("route", ROUTES)
def test_a_mixed_plan_renames_the_rest_and_skips_the_unit_whole(
    tmp_path, capsys, monkeypatch, cloud, watch, reads, os_opens, route
):
    """P.NEF goes cloud-only after the person confirmed. A and B are renamed; the RAW+JPEG unit
    is skipped WHOLE - P.JPG, still local, is not renamed either - and counted as 2 files / 1
    unit. No journal row and no rename for either member; neither file's content is read; the
    plan ends `partially_applied` with P's items `failed`; History, after a restart, reads 2."""
    root, plan_id = _archive(tmp_path)
    m14._from_now(reads)
    run = _rename(route, root, plan_id, lambda: cloud.free(root / "P.NEF"), capsys, monkeypatch)

    assert run.renamed == 2
    _counts(run, route, files=2, units=1)
    assert sorted(watch.renames) == sorted([("A.JPG", NEW["A.JPG"]), ("B.JPG", NEW["B.JPG"])])
    assert sorted(watch.intents) == ["A.JPG", "B.JPG"]
    assert _journal(root) == ["A.JPG", "B.JPG"]
    for name in PAIR:
        assert (root / name).is_file(), name
        reads.assert_never_read(root / name)
        assert root / name not in os_opens, name
    # The os.open spy's own reach, in the same case: an open it must see.
    os.close(os.open(root / NEW["A.JPG"], os.O_RDONLY))
    assert os_opens[-1] == root / NEW["A.JPG"]
    assert m14._plan_status(root, plan_id) == "partially_applied"
    assert m14._statuses(root, plan_id) == {
        "A.JPG": "applied",
        "B.JPG": "applied",
        "P.JPG": "failed",
        "P.NEF": "failed",
    }
    history = _history(root)
    assert [
        (r["session_id"], r["renamed"], r["cloud_only"], r["cloud_only_units"]) for r in history
    ] == [(run.session, 2, 2, 1)]


@windows_only
@pytest.mark.parametrize("route", ROUTES)
def test_an_all_cloud_only_plan_renames_nothing(
    tmp_path, capsys, monkeypatch, cloud, watch, reads, route
):
    """Every unit has a member cloud-only now: nothing is renamed, nothing is journalled, all
    four files and three units are counted, the plan is `partially_applied` (design 7.2: nothing
    attempted is never `applied`). The session has no journal row, so History - which lists the
    journal's sessions - has no row for it; its report carries both counts."""
    root, plan_id = _archive(tmp_path)

    def free_one_of_each() -> None:
        for name in ("A.JPG", "B.JPG", "P.JPG"):
            cloud.free(root / name)

    m14._from_now(reads)
    run = _rename(route, root, plan_id, free_one_of_each, capsys, monkeypatch)

    assert run.renamed == 0
    _counts(run, route, files=4, units=3)
    assert watch.renames == [] and watch.intents == []
    assert _journal(root) == []
    for name in DATES:
        assert (root / name).is_file(), name
        reads.assert_never_read(root / name)
    assert m14._plan_status(root, plan_id) == "partially_applied"
    assert set(m14._statuses(root, plan_id).values()) == {"failed"}
    assert _history(root) == []


@windows_only
@pytest.mark.parametrize("route", ROUTES)
def test_a_unit_whose_only_cloud_only_member_is_its_no_op_is_skipped_whole(
    tmp_path, capsys, monkeypatch, cloud, watch, reads, route
):
    """THE NO-OP FIXTURE. Only `2019-07-04_001.NEF` - the member whose item is a no-op, which
    `actionable()` and `_units_of` never hand to the loop - goes cloud-only after confirmation.
    The probe reads the unit's COMPLETE membership, so the png is not renamed either: no intent,
    no rename, counted as 2 files / 1 unit. The no-op item keeps J12's `applied` (resolved at
    the preflight, before the probe); the png's item is `failed`."""
    root, plan_id = _no_op_archive(tmp_path)
    nef, png = "2019-07-04_001.NEF", "2019-07-04_001.acr_.png"
    m14._from_now(reads)
    run = _rename(route, root, plan_id, lambda: cloud.free(root / nef), capsys, monkeypatch)

    assert run.renamed == 0
    _counts(run, route, files=2, units=1)
    assert watch.renames == [] and watch.intents == []
    assert _journal(root) == []
    assert f12._names(root) == {nef, png}
    reads.assert_never_read(root / nef)
    assert m14._statuses(root, plan_id) == {nef: "applied", png: "failed"}
    assert m14._plan_status(root, plan_id) == "partially_applied"


@windows_only
@pytest.mark.parametrize("route", ROUTES)
def test_the_retry_route_renames_the_unit_once_it_is_local_again(
    tmp_path, capsys, monkeypatch, cloud, watch, route
):
    """AM2's sentence names the route: make the files available offline, read the folder again,
    review, rename. After the skip, P.NEF is kept on the device, the folder is read again (the
    console's scan, or the page's read job), a new plan is made and approved, and the pair is
    renamed - whole."""
    root, plan_id = _archive(tmp_path)
    first = _rename(
        route, root, plan_id, lambda: cloud.free(root / "P.NEF"), capsys, monkeypatch
    )
    _counts(first, route, files=2, units=1)
    cloud.keep(root / "P.NEF")
    watch.renames.clear()

    if route == "cli":
        run_scan(root, exiftool_command=FAKE_COMMAND)
        again = run_plan(root).plan_id
        assert again is not None
        second = _rename(route, root, again, lambda: None, capsys, monkeypatch)
    else:
        with m14.served(root) as server:
            again = m14._read_job(server)
            m14._approve(server, root, again, "P.JPG", decided=2)
            job = m14._page_apply(server, again)
        assert (job["state"], job["result"]["renamed"], job["result"]["cloud_only"]) == (
            "done",
            2,
            0,
        ), job
        second = None
    assert sorted(watch.renames) == sorted((name, NEW[name]) for name in PAIR)
    assert m14._plan_status(root, again) == "applied"
    if second is not None:
        assert second.renamed == 2
        assert "cloud-only" not in second.words
    assert f12._names(root) == set(NEW.values())


@windows_only
@pytest.mark.parametrize("route", ROUTES)
def test_undo_puts_back_a_file_that_became_cloud_only_after_the_rename(
    tmp_path, capsys, monkeypatch, cloud, watch, route
):
    """`pam undo` does NOT skip (AM2): the files were renamed while local, and stranding one
    under pam's name is the worse outcome. A.JPG's new name goes cloud-only after the rename;
    the undo (the console's, or the page's) puts all four back."""
    root, plan_id = _archive(tmp_path)
    run = _rename(route, root, plan_id, lambda: None, capsys, monkeypatch)
    assert run.renamed == 4
    cloud.free(root / NEW["A.JPG"])
    watch.renames.clear()

    if route == "cli":
        capsys.readouterr()
        code = main(["undo", run.session, "--archive", str(root)])
        assert code == 0, capsys.readouterr()
    else:
        with m14.served(root) as server:
            undone = m14._page_undo(server, run.session)
        assert (undone["state"], undone["result"]["restored"]) == ("done", 4), undone
    assert sorted(watch.renames) == sorted((NEW[name], name) for name in DATES)
    assert f12._names(root) == set(DATES)


def test_the_cases_drive_both_routes():
    """The floor (M743): every case above is parametrized over BOTH routes, spelled here."""
    cases = [
        test_a_mixed_plan_renames_the_rest_and_skips_the_unit_whole,
        test_an_all_cloud_only_plan_renames_nothing,
        test_a_unit_whose_only_cloud_only_member_is_its_no_op_is_skipped_whole,
        test_the_retry_route_renames_the_unit_once_it_is_local_again,
        test_undo_puts_back_a_file_that_became_cloud_only_after_the_rename,
    ]
    for case in cases:
        marks = [m for m in case.pytestmark if m.name == "parametrize"]
        assert any(m.name == "skipif" for m in case.pytestmark), case.__name__
        assert [tuple(m.args[1]) for m in marks] == [("cli", "page")], case.__name__


# --- the words, without a filesystem attribute (these run everywhere) ---------------------------


def _summary(files: int, units: int) -> ApplySummary:
    return ApplySummary(
        session_id="s-20261116-101500",
        renamed=3,
        cloud_only_files=files,
        cloud_only_units=units,
        archive_root=Path("C:/Users/user/Pictures"),
        plan_id=7,
        started_at="2026-11-16T10:15:00",
    )


def test_the_console_says_am2s_sentence_only_when_a_unit_was_skipped():
    """N > 0 only (AM2's I10 exception: one new count line, never a zero)."""
    said = apply_summary_lines(_summary(1234, 600))
    assert "  ! " + AM2.format(n="1,234") + TOGETHER in said
    assert not any("cloud-only" in line for line in apply_summary_lines(_summary(0, 0)))


def test_the_report_carries_both_counts_and_history_reads_the_files_back(tmp_path):
    """The report's count line, spelled here, and the page's SECOND reading of it
    (`confirm_api._cloud_only`, which may not import `pam.execute`) agree - thousands
    separator included. No line, and no count, when nothing was skipped."""
    import logging

    logger = logging.getLogger("pam.tests.cloud_only_now")
    skipped = write_session_report(tmp_path / "a", _summary(1234, 600), logger)
    text = skipped.read_text(encoding="utf-8")
    assert REPORT_COUNTS.format(files="1,234", units=600) in text.splitlines()
    assert AM2.format(n="1,234") + TOGETHER in text.splitlines()
    assert confirm_api._cloud_only(skipped) == (1234, 600)

    clean = write_session_report(tmp_path / "b", _summary(0, 0), logger)
    assert "cloud-only" not in clean.read_text(encoding="utf-8")
    assert confirm_api._cloud_only(clean) == (0, 0)
    assert confirm_api._cloud_only(tmp_path / "missing.md") == (0, 0)
    assert confirm_api._cloud_only(None) == (0, 0)


def test_history_reads_both_counts_back_past_a_thousand_units(tmp_path):
    """C6.5: History reads the UNITS back from the report's count line too (`cloud_only_units`,
    for TOGETHER). The report writes both counts with a thousands separator, so the page's second
    reading must accept one in BOTH groups - a units group without `,` stopped matching the whole
    line and History said nothing at all for a run that skipped 1,000 units or more (M3906)."""
    import logging

    logger = logging.getLogger("pam.tests.cloud_only_now")
    report = write_session_report(tmp_path, _summary(2_345, 1_200), logger)
    lines = report.read_text(encoding="utf-8").splitlines()
    assert REPORT_COUNTS.format(files="2,345", units="1,200") in lines, lines
    assert confirm_api._cloud_only(report) == (2345, 1200)


def test_one_file_is_singular_and_together_follows_only_a_unit_of_several(tmp_path):
    """C6.5 (owner, 2026-11-16): `1 file became cloud-only after it was approved ...` for one;
    the plural otherwise; TOGETHER only when a skipped unit has more than one member (files >
    units). The console, Done's lines and the report say the same sentence."""
    import logging

    from pam.execute.reporter import done_lines

    logger = logging.getLogger("pam.tests.cloud_only_now")
    expected = {
        (1, 1): AM2_ONE,
        (3, 3): AM2.format(n=3),
        (2, 1): AM2.format(n=2) + TOGETHER,
        (1_001, 1_000): AM2.format(n="1,001") + TOGETHER,
    }
    for n, ((files, units), sentence) in enumerate(expected.items()):
        summary = _summary(files, units)
        assert "  ! " + sentence in apply_summary_lines(summary), (files, units)
        assert done_lines(summary) == (sentence,), (files, units)
        report = write_session_report(tmp_path / str(n), summary, logger)
        assert sentence in report.read_text(encoding="utf-8").splitlines(), (files, units)
    assert not any(TOGETHER.strip() in line for line in apply_summary_lines(_summary(3, 3)))


def test_the_report_drops_a_zero_not_renamed_line_over_the_cloud_only_line(tmp_path):
    """C6.8(b) (owner, 2026-11-16): the report's `- 0 file(s) not renamed` is omitted when the
    cloud-only line is drawn (that line explains the count); a non-zero count stays, and with no
    cloud-only line the zero stays as before."""
    import logging

    logger = logging.getLogger("pam.tests.cloud_only_now")

    def counts(summary: ApplySummary, name: str) -> list[str]:
        text = write_session_report(tmp_path / name, summary, logger).read_text(
            encoding="utf-8"
        )
        return [line for line in text.splitlines() if line.startswith("- ")]

    skipped = counts(_summary(2, 1), "skipped")
    assert "- 0 file(s) not renamed" not in skipped
    assert not [line for line in skipped if line.endswith("file(s) not renamed")]
    assert REPORT_COUNTS.format(files=2, units=1) in skipped
    failed = _summary(2, 1)
    failed.failed = 1
    failed.failures = [("C.JPG", "C.JPG could not be renamed: it is open in another program.")]
    assert "- 1 file(s) not renamed" in counts(failed, "failed")
    assert "- 0 file(s) not renamed" in counts(_summary(0, 0), "clean")

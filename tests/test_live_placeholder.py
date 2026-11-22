"""M14 Task 0B (M13 review C1, amendments V1 + X1): vision R2 against the LIVE filesystem.

THE DEFECT (`m13.md` 2.1, R1, built with the real attribute). `pam verify --deep`, every pixel
enricher and the thumbnail extractor decided "is this a cloud placeholder?" from
`files.placeholder` - what the LAST SCAN saw. OneDrive can free a file after that, and reading
it then hydrates it. The scanner itself has the same gap between its walk and its reads (X1).
Each reader now calls `walker.placeholder_now` immediately before the read.

THE CONSTRUCTION. Two real JPEGs; `SetFileAttributesW(b.jpg, attrs | FILE_ATTRIBUTE_OFFLINE)`
after the scan (or, for the scanner, after its walk) - which is what a Files On-Demand
dehydration leaves - restored in a `finally`. Windows only: the attribute is a Windows file
attribute, and CI runs Windows.

THE ORACLE IS THE READ ITSELF, NEVER THE OUTCOME (M697). A spy records every path handed to
FOUR hooks:
  - `builtins.open` - the call `PIL.Image.open` makes;
  - `io.open` - the call `Path.open` makes (`sha256_file`). Patching `builtins.open` does NOT
    intercept it: the M13 probe's first version patched only `builtins.open` and saw verify
    read nothing, which was false (`m13.md` 2.1's instrument lesson);
  - `pam.scan.exiftool.extract_binary` - the thumbnail and the RAW preview are read by an
    exiftool SUBPROCESS, which no `open` spy sees;
  - `ExifTool.extract` - the scanner's stay-open exiftool, likewise.
Every construction asserts no read of `b.jpg` on ANY hook, AND the control: the hook its reader
reads through DID record `a.jpg`. A spy that records nothing passes the first assertion by
construction - the M13 probe never showed its control, so its "discriminates" was unproven.
`test_the_spy_sees_a_local_file_on_every_hook` is the control for all four hooks at once, and
runs everywhere (it needs no attribute).
"""

import builtins
import contextlib
import ctypes
import io
import os
import sqlite3
import sys
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest
from archives import raw_with_preview_bytes

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.cli import main
from pam.enrich import COMMIT_BATCH, InferredFact, run_enricher
from pam.enrich.base import CloudOnlyNowError, EnrichSummary
from pam.enrich.summary import EnricherOutcome, EnrichSummaryReport, enrich_lines
from pam.naming.planner import run_plan
from pam.paths import workspace_for
from pam.scan import exiftool as exiftool_mod
from pam.scan import walker
from pam.scan.exiftool import ExifTool
from pam.webui import run_review

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="FILE_ATTRIBUTE_OFFLINE and SetFileAttributesW are Windows file attributes",
)

# Spelled here from the Win32 documentation, never imported from `walker` (an oracle built
# from the definition it checks).
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

# The four hooks, written by hand and asserted non-empty, so a list emptied by an edit cannot
# make every per-hook assertion below vanish with it.
HOOKS = ("builtins.open", "io.open", "extract_binary", "ExifTool.extract")
assert len(HOOKS) == 4

# The copy, verbatim from the M14 plan (Task 0B Step 3) and the existing scan line.
CHECK_8_LINE = (
    "the bytes of 1 file(s) re-read from disk;"
    " 1 skipped because they became cloud-only after the last scan"
)
ENRICH_LINE = (
    "  ! 1 file(s) became cloud-only after the last scan and were not read."
    " Run pam scan, then pam enrich again."
)
SCAN_LINE = "1 files are cloud-only placeholders and were not read."


@pytest.fixture(autouse=True)
def _never_a_real_browser(monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class Spy:
    """Every path handed to each hook, normalised, keyed by hook name."""

    def __init__(self) -> None:
        self.seen: dict[str, list[str]] = {hook: [] for hook in HOOKS}
        self._lock = threading.Lock()

    def record(self, hook: str, path) -> None:
        if isinstance(path, (str, os.PathLike)):
            with self._lock:
                self.seen[hook].append(_key(path))

    def saw(self, hook: str, path) -> bool:
        return _key(path) in self.seen[hook]

    def assert_never_read(self, path) -> None:
        for hook in HOOKS:
            assert not self.saw(hook, path), (
                f"{Path(path).name} was read through {hook} after it became cloud-only;"
                " reading a placeholder hydrates it (vision R2)"
            )

    def assert_read(self, hooks, path) -> None:
        """THE CONTROL: the spy SAW the local file on each hook its reader reads through."""
        assert hooks, "a control over no hook controls nothing"
        for hook in hooks:
            assert self.saw(hook, path), (
                f"the spy recorded no {hook} read of {Path(path).name}, a file that WAS read"
                " - it cannot see, so its silence about the placeholder means nothing"
            )


def _reads(args, kwargs) -> bool:
    """Does this `open` call read the file? `r` or `+` (an update reads too); a pure write -
    the fixtures writing the JPEGs - is not a read of archive content."""
    mode = args[0] if args else kwargs.get("mode", "r")
    return "r" in mode or "+" in mode


@pytest.fixture()
def spy(monkeypatch):
    watch = Spy()
    real_builtin_open = builtins.open
    real_io_open = io.open
    real_extract_binary = exiftool_mod.extract_binary
    real_extract = ExifTool.extract

    def builtin_open(file, *args, **kwargs):
        if _reads(args, kwargs):
            watch.record("builtins.open", file)
        return real_builtin_open(file, *args, **kwargs)

    def io_open(file, *args, **kwargs):
        if _reads(args, kwargs):
            watch.record("io.open", file)
        return real_io_open(file, *args, **kwargs)

    def extract_binary(source, attempts):
        watch.record("extract_binary", source)
        return real_extract_binary(source, attempts)

    def extract(self, paths, media_type="image"):
        for path in paths:
            watch.record("ExifTool.extract", path)
        return real_extract(self, paths, media_type=media_type)

    monkeypatch.setattr(builtins, "open", builtin_open)
    monkeypatch.setattr(io, "open", io_open)
    monkeypatch.setattr(exiftool_mod, "extract_binary", extract_binary)
    monkeypatch.setattr(ExifTool, "extract", extract)
    return watch


@contextlib.contextmanager
def _offline_attributes():
    """`set_offline(path)` sets FILE_ATTRIBUTE_OFFLINE; every one is restored on exit, in a
    `finally`. A context manager rather than the fixture's own body so the restore can be
    tested (M2713): a teardown that runs after the test cannot be asserted by the test."""
    restore: list[tuple[str, int]] = []
    kernel32 = (
        ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
    )

    def set_offline(path: Path) -> None:
        assert kernel32 is not None
        get = kernel32.GetFileAttributesW
        get.argtypes, get.restype = [ctypes.c_wchar_p], ctypes.c_uint32
        put = kernel32.SetFileAttributesW
        put.argtypes, put.restype = [ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int
        before = get(str(path))
        assert before != INVALID_FILE_ATTRIBUTES, ctypes.get_last_error()
        restore.append((str(path), before))
        assert put(str(path), (before & ~FILE_ATTRIBUTE_NORMAL) | FILE_ATTRIBUTE_OFFLINE)
        # The construction holds: the live attributes now say placeholder.
        assert walker.is_placeholder(os.stat(path))

    try:
        yield set_offline
    finally:
        for path, before in reversed(restore):
            try:
                kernel32.SetFileAttributesW(path, before)
            except Exception:  # pragma: no cover - restoring must not mask the test's failure
                pass


@pytest.fixture()
def offline():
    """`offline(path)` sets FILE_ATTRIBUTE_OFFLINE; every one is restored at teardown."""
    with _offline_attributes() as set_offline:
        yield set_offline


def _jpeg(path: Path, colour) -> None:
    image_module = pytest.importorskip("PIL.Image")
    image_module.new("RGB", (64, 48), colour).save(path, "JPEG")


def _two_jpegs(tmp_path) -> tuple[Path, Path, Path]:
    root = tmp_path / "photos"
    root.mkdir()
    a, b = root / "a.jpg", root / "b.jpg"
    _jpeg(a, (200, 30, 30))
    _jpeg(b, (30, 30, 200))
    return root, a, b


def _pam(capsys, *args):
    capsys.readouterr()
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def _rows(root) -> dict[str, sqlite3.Row]:
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, rel_path, placeholder, sha256 FROM files").fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: row for row in rows}


def _facts_for(root, sha256: str) -> int:
    conn = connect(workspace_for(root).db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM inferred_facts WHERE sha256 = ?", (sha256,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def _scanned(capsys, root) -> None:
    code, out, err = _pam(capsys, "scan", str(root))
    assert code == 0, (out, err)


def _thumb_statuses(root, ids) -> list[int]:
    """GET `/api/thumb/{id}?variant=grid` for each id through the server `pam review` starts."""
    plan = run_plan(root)
    assert plan.plan_id is not None
    statuses: list[int] = []

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for file_id in ids:
                conn = HTTPConnection(server.host, server.port, timeout=30)
                try:
                    conn.request("GET", f"/t/{server.token}/api/thumb/{file_id}?variant=grid")
                    response = conn.getresponse()
                    response.read()
                    statuses.append(response.status)
                finally:
                    conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=30)

    run_review(plan.plan_id, root, serve=serve, opener=lambda url: True)
    return statuses


# ============================================================================ the choke point


def test_placeholder_now_reads_the_live_attributes_and_an_oserror_is_false(
    tmp_path, monkeypatch
):
    """Step 1: `is_placeholder(os.stat(path))`, and an `OSError` answers False so the read
    that follows fails exactly as it does today."""
    local = tmp_path / "x.jpg"
    local.write_bytes(b"x")
    assert walker.placeholder_now(local) is False
    assert walker.placeholder_now(tmp_path / "gone.jpg") is False
    real_stat = os.stat
    with monkeypatch.context() as m:
        m.setattr(
            walker.os,
            "stat",
            lambda p, *a, **k: (
                SimpleNamespace(st_file_attributes=FILE_ATTRIBUTE_OFFLINE)
                if Path(p) == local
                else real_stat(p, *a, **k)
            ),
        )
        assert walker.placeholder_now(local) is True


def test_the_enrich_line_is_the_plans_copy_and_absent_at_zero(tmp_path):
    """Step 3's `pam enrich` line, verbatim after the skipped line - and not printed at 0."""

    def summary(cloud_only):
        return EnrichSummary(
            run_id=1,
            total=2,
            walked=2,
            enriched=1,
            skipped_cached=0,
            skipped_wants=0,
            skipped_duplicate=0,
            skipped_no_sha=0,
            failed=0,
            facts_written=1,
            facts_dropped=0,
            facts_conflicted=0,
            superseded=0,
            cursor_file_id=2,
            cloud_only=cloud_only,
        )

    def lines(cloud_only):
        report = EnrichSummaryReport(archive_root=tmp_path)
        report.outcomes.append(EnricherOutcome(name="phash", result=summary(cloud_only)))
        return enrich_lines(report)

    shown = lines(1)
    skipped = next(i for i, line in enumerate(shown) if line.startswith("  skipped: "))
    assert shown[skipped + 1] == ENRICH_LINE
    assert not any("cloud-only" in line for line in lines(0))
    # A skip is not an error: nothing is wrong with the file (exit 0).
    report = EnrichSummaryReport(archive_root=tmp_path)
    report.outcomes.append(EnricherOutcome(name="phash", result=summary(1)))
    assert report.had_errors is False


# ============================================================================ the control


def test_the_spy_sees_a_local_file_on_every_hook(tmp_path, capsys, spy):
    """THE INSTRUMENT'S OWN CONTROL, for all four hooks at once (no attribute needed).

    Every reader runs over two LOCAL files, and each hook must record them: the scan reads
    through `ExifTool.extract` and `io.open` (the hash), `pam verify --deep` through `io.open`,
    `pam enrich phash` through `builtins.open` (Pillow), and the thumbnail through
    `extract_binary`. A hook that records nothing would pass every "never read" assertion in
    this module; this is the test that says it cannot.
    """
    root, a, b = _two_jpegs(tmp_path)
    _scanned(capsys, root)
    code, out, err = _pam(capsys, "verify", "--archive", str(root), "--deep", "--sample", "100")
    assert code == 0, (out, err)
    code, out, err = _pam(capsys, "enrich", "phash", "--archive", str(root))
    assert code == 0, (out, err)
    rows = _rows(root)
    assert _thumb_statuses(root, [rows["a.jpg"]["id"], rows["b.jpg"]["id"]]) == [404, 404]
    for path in (a, b):
        spy.assert_read(HOOKS, path)
    # THE HELPERS ARE THE INSTRUMENT, and each must FAIL when it should (M2692, M2693): every
    # control in this module delegates to `assert_read`, and every "never read" assertion to
    # `assert_never_read`, so a helper that stopped asserting would leave the module green over
    # a spy that cannot see, or over a reader that hydrates a file and leaves no trace.
    with pytest.raises(AssertionError):
        spy.assert_read(HOOKS, tmp_path / "never-read.jpg")
    with pytest.raises(AssertionError):
        spy.assert_never_read(a)


@windows_only
def test_the_offline_attribute_is_restored_even_when_the_test_fails(tmp_path):
    """The construction's `finally` (M2713): a test that fails must not leave a file it made
    cloud-only behind it, any more than one that passes."""
    target = tmp_path / "x.jpg"
    target.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="the test failed"):
        with _offline_attributes() as set_offline:
            set_offline(target)
            raise RuntimeError("the test failed")
    assert not walker.is_placeholder(os.stat(target)), "the OFFLINE attribute was left set"


# ============================================================================ Step 4


@windows_only
def test_verify_deep_skips_and_counts_a_file_that_became_cloud_only(
    tmp_path, capsys, spy, offline
):
    """Check 8: the live placeholder is not hashed, is counted, and the check stays `ok`."""
    root, a, b = _two_jpegs(tmp_path)
    _scanned(capsys, root)
    offline(b)
    spy.seen = {hook: [] for hook in HOOKS}
    code, out, err = _pam(capsys, "verify", "--archive", str(root), "--deep", "--sample", "100")
    assert code == 0, (out, err)
    assert f"  ok 8  {CHECK_8_LINE}" in out.splitlines(), out
    spy.assert_never_read(b)
    spy.assert_read(("io.open",), a)
    assert walker.is_placeholder(os.stat(b)), "the file must still be cloud-only afterwards"


@windows_only
def test_enrich_phash_skips_counts_and_stores_nothing_for_a_cloud_only_file(
    tmp_path, capsys, spy, offline
):
    """The loader's non-RAW branch: `CloudOnlyNowError`, counted, NOTHING stored for b."""
    root, a, b = _two_jpegs(tmp_path)
    _scanned(capsys, root)
    offline(b)
    spy.seen = {hook: [] for hook in HOOKS}
    code, out, err = _pam(capsys, "enrich", "phash", "--archive", str(root))
    assert code == 0, (out, err)
    assert ENRICH_LINE in out.splitlines(), out
    assert "phash: 1 of 2 files read" in out, out
    spy.assert_never_read(b)
    spy.assert_read(("builtins.open",), a)
    rows = _rows(root)
    assert _facts_for(root, rows["a.jpg"]["sha256"]) == 1
    assert _facts_for(root, rows["b.jpg"]["sha256"]) == 0, (
        "a fact was stored for a file pam did not read; it would never be retried"
    )


@windows_only
def test_a_thumbnail_request_never_reads_a_file_that_became_cloud_only(
    tmp_path, capsys, spy, offline
):
    """The thumbnail extractor answers as for a catalog placeholder: 404, and NO cache entry
    of either kind for b - while a's extraction ran and left its negative marker."""
    root, a, b = _two_jpegs(tmp_path)
    _scanned(capsys, root)
    rows = _rows(root)
    offline(b)
    spy.seen = {hook: [] for hook in HOOKS}
    assert _thumb_statuses(root, [rows["a.jpg"]["id"], rows["b.jpg"]["id"]]) == [404, 404]
    spy.assert_never_read(b)
    spy.assert_read(("extract_binary",), a)
    cached = [p.name for p in workspace_for(root).thumbs_dir.glob("*")]
    assert any(name.startswith(rows["a.jpg"]["sha256"]) for name in cached), cached
    assert not any(name.startswith(rows["b.jpg"]["sha256"]) for name in cached), cached


# ============================================================================ Step 5


@windows_only
def test_the_raw_branch_never_extracts_a_preview_from_a_cloud_only_raw(
    tmp_path, capsys, spy, offline
):
    """The loader's RAW branch reads through `extract_binary`, which no `open` spy sees."""
    root = tmp_path / "photos"
    root.mkdir()
    a, b = root / "a.NEF", root / "b.NEF"
    a.write_bytes(raw_with_preview_bytes("a.NEF"))
    b.write_bytes(raw_with_preview_bytes("b.NEF"))
    _scanned(capsys, root)
    offline(b)
    spy.seen = {hook: [] for hook in HOOKS}
    code, out, err = _pam(capsys, "enrich", "phash", "--archive", str(root))
    assert code == 0, (out, err)
    assert ENRICH_LINE in out.splitlines(), out
    spy.assert_never_read(b)
    spy.assert_read(("extract_binary",), a)
    rows = _rows(root)
    assert _facts_for(root, rows["a.NEF"]["sha256"]) == 1
    assert _facts_for(root, rows["b.NEF"]["sha256"]) == 0


# ============================================================================ Step 6 (X1)


@windows_only
def test_the_scanner_does_not_read_a_file_that_became_cloud_only_after_its_walk(
    tmp_path, capsys, spy, offline, monkeypatch
):
    """X1: both files NEW; b goes cloud-only AFTER the walk returns. It is recorded as the
    placeholder it now is (sha256 NULL), never read, counted by the existing line, and kept
    out of the plan."""
    root, a, b = _two_jpegs(tmp_path)
    real_walk = walker.walk

    def walk_then_dehydrate(archive_root, config):
        found = real_walk(archive_root, config)
        offline(b)
        return found

    monkeypatch.setattr(walker, "walk", walk_then_dehydrate)
    monkeypatch.setattr("pam.scan.walker.detect_sync_root", lambda p: "OneDrive")
    spy.seen = {hook: [] for hook in HOOKS}
    code, out, err = _pam(capsys, "scan", str(root))
    assert code == 0, (out, err)
    # (i) no read of b on any of the four hooks; (ii) the control: a on the scanner's own two.
    spy.assert_never_read(b)
    spy.assert_read(("ExifTool.extract", "io.open"), a)
    # (iii) the row, and no plan item.
    rows = _rows(root)
    assert rows["b.jpg"]["placeholder"] == 1 and rows["b.jpg"]["sha256"] is None
    assert rows["a.jpg"]["placeholder"] == 0 and rows["a.jpg"]["sha256"] is not None
    # (iv) the existing count line, verbatim, and the read count that does not claim b.
    assert any(line.startswith(f"| {SCAN_LINE}") for line in out.splitlines()), out
    assert "  1 files read this scan - everything else was already known." in out, out
    plan = run_plan(root)
    assert plan.plan_id is not None
    conn = connect(workspace_for(root).db_path)
    try:
        planned = {
            r[0]
            for r in conn.execute(
                "SELECT old_rel_path FROM plan_items WHERE plan_id = ?", (plan.plan_id,)
            )
        }
    finally:
        conn.close()
    assert "a.jpg" in planned
    assert "b.jpg" not in planned, "a cloud-only file was given a plan item"


# ============================================================================ the runner


class _CloudOnlyFirst:
    """A phash-shaped fake whose loader would refuse one named file as cloud-only NOW."""

    name = "phash"
    model_name = "fake-model"
    model_version = "1.0"

    def __init__(self, cloud_only: str) -> None:
        self.cloud_only = cloud_only
        self.calls: list[str] = []

    def available(self):
        return True, "the fake is always available"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return file.media_type == "image"

    def enrich(self, file, image):
        self.calls.append(file.rel_path)
        if file.rel_path == self.cloud_only:
            raise CloudOnlyNowError(f"{file.rel_path} became cloud-only after the last scan")
        return [InferredFact(kind="phash", value={"hash": file.sha256})]


def test_a_cloud_only_files_local_twin_in_a_later_batch_is_still_enriched(tmp_path):
    """The runner releases a cloud-only file's sha256, as it does a failed one's (M2700).

    `in_flight` refuses a byte-identical twin at dispatch. A file skipped because it became
    cloud-only stored nothing, so if its sha stayed in flight a LOCAL twin walked in a later
    commit batch would be refused as a duplicate, and content that IS readable would go
    un-enriched for the run. Asserted on the CALL, not on `skipped_duplicate` alone (the
    failure branch's sibling: `test_enrich_gate::test_a_duplicate_in_a_LATER_batch…`).
    """
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)

    def add(name: str, sha: str) -> None:
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="jpg",
            media_type="image",
            size=10,
            mtime_ns=1,
            placeholder=False,
            now="2026-10-30T10:00:00",
        )
        repo.set_sha256(conn, file_id, sha)

    add("A_cloud.jpg", "sha-shared")
    for i in range(COMMIT_BATCH + 10):
        add(f"pad{i:03d}.jpg", f"sha-pad{i:03d}")
    add("B_twin.jpg", "sha-shared")
    conn.commit()
    enricher = _CloudOnlyFirst("A_cloud.jpg")
    try:
        summary = run_enricher(conn, enricher, lambda f: None, workers=1)
        stored = conn.execute(
            "SELECT COUNT(*) FROM inferred_facts WHERE sha256 = 'sha-shared'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert summary.cloud_only == 1 and summary.failed == 0
    assert "B_twin.jpg" in enricher.calls, "the cloud-only file's sha was never released"
    assert summary.skipped_duplicate == 0
    assert stored == 1, "the local twin's fact covers the content"

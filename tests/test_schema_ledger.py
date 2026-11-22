"""E12, the compatibility ledger (M15 Task 9; plan rev 3, second gate blocker 2).

An append-only file outside every PAM_HOME. Its value is the max over the lines that are migration
filenames. `db.migrate` RESERVES the newest pending migration (fsynced) before its first
`executescript` and refuses to migrate when it cannot; `db.check_compatible` REGISTERS every
compatible catalog's newest migration on open. A missing ledger is no constraint; an unreadable
one is refused.

THE EXPECTED LEVELS ARE SPELLED HERE (`NEWEST_SHIPPED`), never read from the migrations folder.
"""

import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pam.catalog import db, ledger
from pam.errors import CatalogTooNewError, LedgerError, PamError

REPO_ROOT = Path(__file__).resolve().parent.parent

# The newest migration this pam ships (schema 0008), spelled from the spec, not globbed.
NEWEST_SHIPPED = "0008_files_rel_path_nocase.sql"
FIRST_SHIPPED = "0001_init.sql"
SHIPPED_COUNT = 8


def _lines(path: Path) -> list[str]:
    return path.read_bytes().decode("ascii").splitlines() if path.exists() else []


def _applied(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        has = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()[0]
        if not has:
            return []
        return [row[0] for row in conn.execute("SELECT filename FROM schema_migrations")]
    finally:
        conn.close()


def _migrate(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        db.migrate(conn)
    finally:
        conn.close()


# --- where the ledger is ------------------------------------------------------------


def test_under_pytest_the_ledger_is_this_tests_private_file(isolated_schema_ledger, tmp_path):
    assert ledger.ledger_path() == isolated_schema_ledger
    assert tmp_path in isolated_schema_ledger.parents
    assert os.environ[ledger.LEDGER_ENV] == str(isolated_schema_ledger)


def test_the_windows_default_is_localappdata_pam_install(monkeypatch, tmp_path):
    monkeypatch.delenv(ledger.LEDGER_ENV)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert ledger.ledger_path() == tmp_path / "local" / "pam-install" / "schema-ledger"


def test_without_localappdata_it_is_under_the_platform_default_home(monkeypatch, tmp_path):
    monkeypatch.delenv(ledger.LEDGER_ENV)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert ledger.ledger_path() == tmp_path / "home" / ".pam" / "schema-ledger"


def test_a_custom_pam_home_does_not_move_the_ledger(monkeypatch, tmp_path):
    """The installer reads one fixed file; a custom PAM_HOME must not move pam's writes."""
    monkeypatch.delenv(ledger.LEDGER_ENV)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    before = ledger.ledger_path()
    monkeypatch.setenv("PAM_HOME", str(tmp_path / "elsewhere"))
    assert ledger.ledger_path() == before
    assert (tmp_path / "elsewhere") not in before.parents


def test_a_catalog_under_a_custom_pam_home_registers_in_the_one_ledger(
    monkeypatch, tmp_path, isolated_schema_ledger
):
    monkeypatch.setenv("PAM_HOME", str(tmp_path / "custom-home"))
    workspace = tmp_path / "custom-home" / "archives" / "x"
    workspace.mkdir(parents=True)
    _migrate(workspace / "catalog.db")
    assert ledger.read_level() == NEWEST_SHIPPED
    assert not list((tmp_path / "custom-home").rglob("schema-ledger"))


@pytest.fixture(scope="module")
def ledger_seen_by_a_module_fixture():
    """What a MODULE-scoped fixture sees: they run before every function-scoped fixture."""
    return ledger.ledger_path()


def test_a_module_scoped_fixture_is_isolated_too(ledger_seen_by_a_module_fixture, tmp_path):
    """Eleven module-scoped fixtures migrate a catalog in their own setup; before the session
    layer they wrote to the developer's real ledger (M15 Task 9's first full run)."""
    assert os.environ.get(ledger.LEDGER_ENV)
    basetemp = tmp_path.parent.parent
    assert basetemp in ledger_seen_by_a_module_fixture.parents


# --- the guard on the REAL ledger (M15 Task 9 review, F1) ---------------------------


def test_the_guard_watches_the_spec_s_default_files(real_schema_ledgers, tmp_path):
    """Design 4 rev 35's two default files, spelled here; never the per-test override."""
    expected = [Path.home() / ".pam" / "schema-ledger"]
    if os.environ.get("LOCALAPPDATA"):
        expected.append(Path(os.environ["LOCALAPPDATA"]) / "pam-install" / "schema-ledger")
    assert sorted(real_schema_ledgers) == sorted(expected)
    basetemp = tmp_path.parent.parent
    assert not any(basetemp in path.parents for path in real_schema_ledgers)


def test_the_guard_sees_a_created_and_an_appended_file_and_nothing_else(tmp_path):
    import conftest

    created, appended, untouched = tmp_path / "c", tmp_path / "a", tmp_path / "u"
    appended.write_bytes(b"0001_a.sql\n")
    untouched.write_bytes(b"0001_a.sql\n")
    paths = (created, appended, untouched)
    before = conftest.schema_ledger_states(paths)
    assert conftest.schema_ledger_escapes(before, conftest.schema_ledger_states(paths)) == []
    ledger.reserve("0002_b.sql", created)
    ledger.reserve("0002_b.sql", appended)
    escaped = conftest.schema_ledger_escapes(before, conftest.schema_ledger_states(paths))
    assert escaped == [str(created), str(appended)]


_ESCAPES = """
import os, subprocess, sys
from pam.catalog import ledger

def test_0_control():
    ledger.register("0001_a.sql")

def test_a_monkeypatch_escape(monkeypatch):
    monkeypatch.delenv(ledger.LEDGER_ENV)
    ledger.register("0002_b.sql")

def test_c_subprocess_escape():
    env = {k: v for k, v in os.environ.items() if k != ledger.LEDGER_ENV}
    code = "from pam.catalog import ledger; ledger.register('0003_c.sql')"
    subprocess.run([sys.executable, "-c", code], env=env, check=True)

def test_d_empty_override(monkeypatch):
    monkeypatch.setenv(ledger.LEDGER_ENV, "")
    ledger.register("0004_d.sql")
"""


def errored_tests(output: str) -> set[str]:
    """The names of the child run's ERRORed tests, from its short summary. pytest 9.1 writes
    `ERROR <path>/test_escapes.py::name`; pytest 9.2 (the hosted runner, 2026-11-21) writes
    `ERROR ::name` for this child, so both forms are read."""
    return {
        line.rsplit("::", 1)[-1].split(" ")[0]
        for line in output.splitlines()
        if line.startswith("ERROR ")
        and ("test_escapes.py::" in line or line.startswith("ERROR ::"))
    }


def test_the_child_summary_parser_reads_both_pytest_forms():
    output = "\n".join(
        [
            "ERROR ../run/test_escapes.py::test_a_one - AssertionError: x",
            "ERROR ::test_b_two - AssertionError: y",
            "ERROR elsewhere.py::test_c_not_ours - AssertionError: z",
            "FAILED ::test_d_failed_not_errored",
        ]
    )
    assert errored_tests(output) == {"test_a_one", "test_b_two"}


def _run_child_pytest(tmp_path, source):
    """A child pytest that loads THIS conftest (`-p conftest`) with LOCALAPPDATA and the home
    folder pointed at scratch folders, so its 'real' ledger is a scratch file. Returns the run's
    output, the ERRORed test names and the lines the scratch 'real' ledger holds."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "test_escapes.py").write_text(source, encoding="utf-8")
    local, home = tmp_path / "local", tmp_path / "home"
    local.mkdir()
    home.mkdir()
    env = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PAM_FACES_REQUIRED",
        "PAM_OCR_REQUIRED",
        "PAM_INNO_REQUIRED",
    ):
        env.pop(name, None)
    env.update(LOCALAPPDATA=str(local), USERPROFILE=str(home), HOME=str(home))
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rE",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
        ]
        + [str(tmp_path / "run" / "test_escapes.py")],
        cwd=REPO_ROOT / "tests",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    output = (result.stdout + result.stderr).replace("\\", "/")
    assert result.returncode == 1, output[-3000:]
    errored = errored_tests(output)
    real = local / "pam-install" / "schema-ledger"
    written = real.read_text(encoding="ascii").split() if real.exists() else []
    return output, errored, written


def test_an_escape_by_monkeypatch_subprocess_or_empty_override_fails_the_run(tmp_path):
    """F1, built: before the file guard, all three of these wrote the real ledger GREEN."""
    output, errored, written = _run_child_pytest(tmp_path, _ESCAPES)
    assert errored == {
        "test_a_monkeypatch_escape",
        "test_c_subprocess_escape",
        "test_d_empty_override",
    }
    assert "this test wrote the developer's REAL schema ledger" in output
    assert written == ["0002_b.sql", "0003_c.sql", "0004_d.sql"]


_MODULE_ESCAPE = """
import os, subprocess, sys
import pytest
from pam.catalog import ledger

@pytest.fixture(scope="module")
def escaped_before_any_test():
    env = {k: v for k, v in os.environ.items() if k != ledger.LEDGER_ENV}
    code = "from pam.catalog import ledger; ledger.register('0005_e.sql')"
    subprocess.run([sys.executable, "-c", code], env=env, check=True)

def test_uses_the_module_fixture(escaped_before_any_test):
    pass

def test_a_later_clean_test():
    ledger.register("0001_a.sql")
"""


def test_an_escape_in_a_module_scoped_fixture_fails_the_session(tmp_path):
    """Review M4133 (Task 9): a module-scoped fixture runs BEFORE the per-test guard's 'before'
    reading, so only the SESSION guard can see its write. The ERROR is that guard's own
    sentence, at the session's last teardown - neither test's own guard fires."""
    output, errored, written = _run_child_pytest(tmp_path, _MODULE_ESCAPE)
    assert "the suite wrote the developer's REAL schema ledger" in output
    assert "this test wrote the developer's REAL schema ledger" not in output
    assert errored == {"test_a_later_clean_test"}
    assert written == ["0005_e.sql"]


# --- reading ------------------------------------------------------------------------


def test_a_missing_ledger_is_no_constraint(isolated_schema_ledger):
    assert not isolated_schema_ledger.exists()
    assert ledger.read_level() is None


def test_an_empty_ledger_is_no_constraint(isolated_schema_ledger):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"")
    assert ledger.read_level() is None


def test_the_level_is_the_max_over_the_lines_not_the_last_line(isolated_schema_ledger):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0003_c.sql\n0009_i.sql\n0002_b.sql\n")
    assert ledger.read_level() == "0009_i.sql"


def test_garbage_lines_are_ignored(isolated_schema_ledger):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(
        b"0004_d.sql\n"
        b"9999\n"  # no name
        b"9999_X.sql\n"  # uppercase
        b"9999_x.txt\n"  # not .sql
        b" 9999_x.sql\n"  # leading space
        b"9999_x.sql trailing\n"
        b"\xff\xfe9999_x.sql\n"  # not ASCII
        b"../9999_x.sql\n"
        b"\n\n"
    )
    assert ledger.read_level() == "0004_d.sql"


def test_a_truncated_last_line_is_ignored(isolated_schema_ledger):
    """A write cut short by a crash leaves a partial name; it must not count."""
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0005_e.sql\n0009_fut")
    assert ledger.read_level() == "0005_e.sql"


def test_a_crlf_line_still_counts(isolated_schema_ledger):
    """An editor that saved CRLF must not LOWER the ledger: the conservative direction."""
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0002_b.sql\r\n0007_g.sql\r\n")
    assert ledger.read_level() == "0007_g.sql"


def test_only_garbage_is_no_constraint(isolated_schema_ledger):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"hello\nworld")
    assert ledger.read_level() is None


def test_an_unreadable_ledger_is_refused_with_a_sentence(isolated_schema_ledger):
    """A directory where the file should be: the OS refuses the read (access denied on
    Windows, EISDIR elsewhere). Unknown is refused, never read as no constraint."""
    isolated_schema_ledger.mkdir(parents=True)
    with pytest.raises(LedgerError) as raised:
        ledger.read_level()
    assert str(isolated_schema_ledger) in str(raised.value)
    assert "pam cannot read" in str(raised.value)
    assert isinstance(raised.value, PamError)


def test_an_access_denied_read_is_refused(isolated_schema_ledger, monkeypatch):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0001_a.sql\n")

    def denied(self):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(LedgerError, match="Access is denied"):
        ledger.read_level()


# --- writing ------------------------------------------------------------------------


def test_reserve_creates_the_folder_and_appends_one_line(isolated_schema_ledger):
    assert not isolated_schema_ledger.parent.exists()
    ledger.reserve("0003_c.sql")
    assert _lines(isolated_schema_ledger) == ["0003_c.sql"]
    ledger.reserve("0002_b.sql")
    assert _lines(isolated_schema_ledger) == ["0003_c.sql", "0002_b.sql"]


def test_each_append_is_one_write_under_64_bytes_then_one_fsync(monkeypatch):
    """Watch the CALLS: one `write` (no read-modify-write) and an fsync of that descriptor."""
    calls: list[tuple[str, int, int]] = []
    real_write, real_fsync = os.write, os.fsync

    def spy_write(fd, data):
        calls.append(("write", fd, len(data)))
        return real_write(fd, data)

    def spy_fsync(fd):
        calls.append(("fsync", fd, 0))
        return real_fsync(fd)

    monkeypatch.setattr(os, "write", spy_write)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    ledger.reserve(NEWEST_SHIPPED)
    assert [kind for kind, _fd, _n in calls] == ["write", "fsync"]
    assert calls[0][1] == calls[1][1]
    assert 0 < calls[0][2] < 64


def test_a_short_write_is_a_failed_reservation(monkeypatch):
    monkeypatch.setattr(os, "write", lambda fd, data: len(data) - 1)
    with pytest.raises(LedgerError, match="short write"):
        ledger.reserve("0003_c.sql")


def test_a_name_that_is_not_a_migration_is_never_written(isolated_schema_ledger):
    for bad in (
        "0003_c.sql\n0009_x.sql",
        "0003_c.sql\n",
        "9_x.sql",
        "0003_" + "a" * 60 + ".sql",
    ):
        with pytest.raises(ValueError):
            ledger.reserve(bad)
    assert not isolated_schema_ledger.exists()


def test_an_unwritable_ledger_makes_reserve_refuse_naming_the_path(isolated_schema_ledger):
    isolated_schema_ledger.mkdir(parents=True)
    with pytest.raises(LedgerError) as raised:
        ledger.reserve("0003_c.sql")
    assert str(isolated_schema_ledger) in str(raised.value)
    assert "has not updated the catalog" in str(raised.value)


def test_register_appends_only_when_higher(isolated_schema_ledger):
    ledger.register("0004_d.sql")
    ledger.register("0004_d.sql")
    ledger.register("0002_b.sql")
    ledger.register("0006_f.sql")
    assert _lines(isolated_schema_ledger) == ["0004_d.sql", "0006_f.sql"]


def test_register_on_an_unwritable_ledger_logs_and_does_not_raise(
    isolated_schema_ledger, caplog
):
    isolated_schema_ledger.mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="pam.ledger"):
        ledger.register("0003_c.sql")
    assert "could not register catalog update 0003_c.sql" in caplog.text


def test_register_on_an_unreadable_ledger_writes_nothing(
    isolated_schema_ledger, monkeypatch, caplog
):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0001_a.sql\n")
    appended = []
    monkeypatch.setattr(ledger, "_append", lambda path, name: appended.append(name))
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError(5, "I/O"))
    )
    with caplog.at_level(logging.WARNING, logger="pam.ledger"):
        ledger.register("0009_i.sql")
    assert appended == []
    assert "could not register" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="the FILE_APPEND_DATA handle is Windows'")
def test_the_windows_handle_appends_wherever_its_file_pointer_is(isolated_schema_ledger):
    """M4113 (Task 9 review): with GENERIC_WRITE in place of FILE_APPEND_DATA the two-process
    test passed 5 / 5 and the full suite was green - at 2 x 200 the CRT's seek-then-write race is
    rarely lost. This asks the HANDLE directly: rewind its file pointer to 0 and write through
    WriteFile, beneath the C runtime. A handle without FILE_WRITE_DATA appends anyway; a handle
    that has it overwrites the first line."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0001_a.sql\n")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.WriteFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_char_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    fd = ledger._open_append(isolated_schema_ledger)
    try:
        handle = msvcrt.get_osfhandle(fd)
        assert kernel32.SetFilePointerEx(handle, 0, None, 0), ctypes.get_last_error()
        written = wintypes.DWORD()
        data = b"0002_b.sql\n"
        assert kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None), (
            ctypes.get_last_error()
        )
    finally:
        os.close(fd)
    assert isolated_schema_ledger.read_bytes() == b"0001_a.sql\n0002_b.sql\n"


_APPENDER = """
import sys
from pathlib import Path
from pam.catalog import ledger
tag = sys.argv[1]
for i in range(200):
    ledger.reserve(f"{i:04d}_{tag}.sql", Path(sys.argv[2]))
"""


def test_two_processes_appending_at_once_lose_no_line(isolated_schema_ledger):
    """200 appends from each of two processes: every line arrives whole, and the max is right."""
    isolated_schema_ledger.parent.mkdir(parents=True)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _APPENDER, tag, str(isolated_schema_ledger)], cwd=REPO_ROOT
        )
        for tag in ("aa", "bb")
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    lines = _lines(isolated_schema_ledger)
    assert len(lines) == 400
    expected = {f"{i:04d}_{tag}.sql" for i in range(200) for tag in ("aa", "bb")}
    assert set(lines) == expected
    assert ledger.read_level() == "0199_bb.sql"


# --- reservation, inside `db.migrate` -----------------------------------------------


def test_migrating_a_new_catalog_reserves_the_newest_shipped_migration(
    tmp_path, isolated_schema_ledger
):
    _migrate(tmp_path / "catalog.db")
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]
    assert len(_applied(tmp_path / "catalog.db")) == SHIPPED_COUNT


class _SpyConnection(sqlite3.Connection):
    """Records the ledger's level at every `executescript`, i.e. before each migration's SQL."""

    levels_at_script: list[str | None] = []

    def executescript(self, sql_script):
        type(self).levels_at_script.append(ledger.read_level())
        return super().executescript(sql_script)


def test_the_reservation_is_durable_before_the_first_executescript(tmp_path):
    _SpyConnection.levels_at_script = []
    conn = sqlite3.connect(tmp_path / "catalog.db", factory=_SpyConnection)
    conn.row_factory = sqlite3.Row
    try:
        db.migrate(conn)
    finally:
        conn.close()
    assert _SpyConnection.levels_at_script == [NEWEST_SHIPPED] * SHIPPED_COUNT


def test_a_reservation_that_cannot_be_written_migrates_nothing(
    tmp_path, isolated_schema_ledger
):
    isolated_schema_ledger.mkdir(parents=True)  # a folder where the file goes: unwritable
    _SpyConnection.levels_at_script = []
    conn = sqlite3.connect(tmp_path / "catalog.db", factory=_SpyConnection)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(LedgerError) as raised:
            db.migrate(conn)
    finally:
        conn.close()
    assert str(isolated_schema_ledger) in str(raised.value)
    assert _SpyConnection.levels_at_script == []
    assert _applied(tmp_path / "catalog.db") == []


class _CountingConnection(sqlite3.Connection):
    """Counts `executescript` calls and reads NOTHING: the spy above reads the ledger, so on an
    unreadable ledger it raises by itself and cannot tell a late reservation from an early one."""

    scripts = 0

    def executescript(self, sql_script):
        type(self).scripts += 1
        return super().executescript(sql_script)


def test_a_failed_append_to_a_readable_ledger_runs_no_migration_sql(
    tmp_path, isolated_schema_ledger, monkeypatch
):
    """M4105 (Task 9 review): with the reservation moved to just before the LAST migration,
    the test above stayed green - its own spy raised first. Here the ledger stays readable and
    only the append fails, so the one thing that can stop the SQL is the reservation."""
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0001_init.sql\n")

    def refused(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", refused)
    _CountingConnection.scripts = 0
    conn = sqlite3.connect(tmp_path / "catalog.db", factory=_CountingConnection)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(LedgerError, match="No space left"):
            db.migrate(conn)
    finally:
        conn.close()
    assert _CountingConnection.scripts == 0
    assert _applied(tmp_path / "catalog.db") == []
    assert ledger.read_level() == "0001_init.sql"


def test_nothing_pending_reserves_nothing(tmp_path, isolated_schema_ledger):
    _migrate(tmp_path / "catalog.db")
    _migrate(tmp_path / "catalog.db")
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]


def test_an_upgrade_reserves_the_newest_pending_not_the_first(tmp_path, isolated_schema_ledger):
    older = tmp_path / "older"
    older.mkdir()
    shipped = REPO_ROOT / "pam" / "catalog" / "migrations"
    for name in sorted(p.name for p in shipped.glob("*.sql"))[:3]:
        (older / name).write_bytes((shipped / name).read_bytes())
    conn = db.connect(tmp_path / "catalog.db")
    try:
        db.migrate(conn, older)
        assert _lines(isolated_schema_ledger) == ["0003_video_reread.sql"]
        db.migrate(conn)
    finally:
        conn.close()
    assert _lines(isolated_schema_ledger) == ["0003_video_reread.sql", NEWEST_SHIPPED]


# --- the three interruption boundaries, through the fault harness -------------------


def test_a_crash_before_the_reservation_leaves_both_untouched(
    tmp_path, isolated_schema_ledger, set_fault
):
    set_fault("crash:before_ledger_reservation:1")
    with pytest.raises(SystemExit):
        _migrate(tmp_path / "catalog.db")
    assert set_fault.hits("before_ledger_reservation") == 1
    assert not isolated_schema_ledger.exists()
    assert _applied(tmp_path / "catalog.db") == []
    set_fault.next_invocation()
    _migrate(tmp_path / "catalog.db")
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]
    assert len(_applied(tmp_path / "catalog.db")) == SHIPPED_COUNT


def test_a_crash_after_the_reservation_leaves_the_ledger_higher_than_the_catalog(
    tmp_path, isolated_schema_ledger, set_fault
):
    """The conservative false positive: the ledger says 0008, the catalog has nothing."""
    set_fault("crash:after_ledger_reservation:1")
    with pytest.raises(SystemExit):
        _migrate(tmp_path / "catalog.db")
    assert set_fault.hits("after_ledger_reservation") == 1
    assert ledger.read_level() == NEWEST_SHIPPED
    assert _applied(tmp_path / "catalog.db") == []
    set_fault.next_invocation()
    _migrate(tmp_path / "catalog.db")
    assert ledger.read_level() == NEWEST_SHIPPED
    assert len(_applied(tmp_path / "catalog.db")) == SHIPPED_COUNT


def test_a_crash_after_one_migrations_commit_keeps_the_reservation(
    tmp_path, isolated_schema_ledger, set_fault
):
    set_fault("crash:after_migration_commit:1")
    with pytest.raises(SystemExit):
        _migrate(tmp_path / "catalog.db")
    assert _applied(tmp_path / "catalog.db") == [FIRST_SHIPPED]
    assert ledger.read_level() == NEWEST_SHIPPED
    set_fault.next_invocation()
    _migrate(tmp_path / "catalog.db")
    assert len(_applied(tmp_path / "catalog.db")) == SHIPPED_COUNT
    assert ledger.read_level() == NEWEST_SHIPPED


def test_a_crash_after_the_last_migrations_commit_leaves_ledger_and_catalog_equal(
    tmp_path, isolated_schema_ledger, set_fault
):
    set_fault(f"crash:after_migration_commit:{SHIPPED_COUNT}")
    with pytest.raises(SystemExit):
        _migrate(tmp_path / "catalog.db")
    assert set_fault.hits("after_migration_commit") == SHIPPED_COUNT
    assert max(_applied(tmp_path / "catalog.db")) == NEWEST_SHIPPED
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]


def test_a_crash_in_a_real_process_after_the_reservation_is_seen_by_the_next_one(
    tmp_path, isolated_schema_ledger, set_fault
):
    """A genuine process boundary (spec 9.3): the child dies after fsync, before any SQL."""
    script = (
        "import sys; from pathlib import Path; from pam.catalog import db;"
        " c = db.connect(Path(sys.argv[1])); db.migrate(c)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "catalog.db")],
        cwd=REPO_ROOT,
        env=set_fault.fault_env("crash:after_ledger_reservation:1"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "PAM_FAULT: crashed at after_ledger_reservation hit 1" in proc.stderr
    assert ledger.read_level() == NEWEST_SHIPPED
    assert _applied(tmp_path / "catalog.db") == []


# --- registration, inside `db.check_compatible` -------------------------------------


def test_a_copied_already_migrated_catalog_is_registered_on_first_open(tmp_path, monkeypatch):
    """Migrated on 'another PC' (another ledger), copied here, opened read-only: registered."""
    elsewhere = tmp_path / "other-pc-ledger"
    monkeypatch.setenv(ledger.LEDGER_ENV, str(elsewhere))
    _migrate(tmp_path / "catalog.db")
    monkeypatch.undo()
    here = ledger.ledger_path()
    assert not here.exists()
    conn = db.open_catalog_readonly(tmp_path / "catalog.db")
    conn.close()
    assert _lines(here) == [NEWEST_SHIPPED]


def test_every_kind_of_open_registers(tmp_path, monkeypatch, isolated_schema_ledger):
    elsewhere = tmp_path / "other-pc-ledger"
    monkeypatch.setenv(ledger.LEDGER_ENV, str(elsewhere))
    _migrate(tmp_path / "catalog.db")
    monkeypatch.undo()
    db.ensure_compatible(tmp_path / "catalog.db")
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]
    isolated_schema_ledger.unlink()
    db.open_catalog(tmp_path / "catalog.db").close()
    assert _lines(isolated_schema_ledger) == [NEWEST_SHIPPED]


def test_registration_does_not_lower_or_repeat(tmp_path, isolated_schema_ledger):
    isolated_schema_ledger.parent.mkdir(parents=True)
    isolated_schema_ledger.write_bytes(b"0009_from_a_newer_pam.sql\n")
    _migrate(tmp_path / "catalog.db")  # the reservation appends; registration must not
    db.open_catalog_readonly(tmp_path / "catalog.db").close()
    db.open_catalog_readonly(tmp_path / "catalog.db").close()
    assert _lines(isolated_schema_ledger) == ["0009_from_a_newer_pam.sql", NEWEST_SHIPPED]
    assert ledger.read_level() == "0009_from_a_newer_pam.sql"


def test_a_failed_registration_does_not_stop_the_open(tmp_path, monkeypatch, caplog):
    elsewhere = tmp_path / "other-pc-ledger"
    monkeypatch.setenv(ledger.LEDGER_ENV, str(elsewhere))
    _migrate(tmp_path / "catalog.db")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setenv(ledger.LEDGER_ENV, str(blocked))  # a folder: unreadable and unwritable
    with caplog.at_level(logging.WARNING, logger="pam.ledger"):
        conn = db.open_catalog_readonly(tmp_path / "catalog.db")
    conn.close()
    assert "could not register" in caplog.text


def test_a_refused_catalog_is_not_registered(tmp_path, isolated_schema_ledger):
    _migrate(tmp_path / "catalog.db")
    raw = sqlite3.connect(tmp_path / "catalog.db")
    raw.execute(
        "INSERT INTO schema_migrations (filename, sha256, applied_at)"
        " VALUES ('0009_future.sql', 'x', 'now')"
    )
    raw.commit()
    raw.close()
    with pytest.raises(CatalogTooNewError):
        db.open_catalog_readonly(tmp_path / "catalog.db")
    assert ledger.read_level() == NEWEST_SHIPPED


def test_a_catalog_nothing_has_migrated_registers_nothing(tmp_path, isolated_schema_ledger):
    sqlite3.connect(tmp_path / "catalog.db").close()
    db.open_catalog_readonly(tmp_path / "catalog.db").close()
    assert not isolated_schema_ledger.exists()


def test_a_catalog_whose_first_migration_never_committed_opens_and_registers_nothing(
    tmp_path, isolated_schema_ledger, set_fault
):
    """A crash before the reservation leaves `schema_migrations` CREATED and EMPTY: the open
    after it must neither crash (`max()` of nothing) nor register anything."""
    set_fault("crash:before_ledger_reservation:1")
    with pytest.raises(SystemExit):
        _migrate(tmp_path / "catalog.db")
    set_fault.next_invocation()
    assert _applied(tmp_path / "catalog.db") == []
    raw = sqlite3.connect(tmp_path / "catalog.db")
    assert raw.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    raw.close()
    db.ensure_compatible(tmp_path / "catalog.db")
    db.open_catalog_readonly(tmp_path / "catalog.db").close()
    assert not isolated_schema_ledger.exists()

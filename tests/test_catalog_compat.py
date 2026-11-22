"""E1 (M15 Task 8): a catalog a NEWER pam migrated is refused by every reader of it.

From M15 a workspace can outlive the binary that wrote it. Before this task `db.migrate` ignored
an applied migration it did not ship, and six read-only readers never called `migrate` at all, so
an older pam opened a newer pam's catalog silently (plan F-W1).

THE SHAPE. `db.check_compatible(conn)` compares `schema_migrations`' filenames with this binary's
files BY NAME and never writes. Two choke points call it: `db.open_catalog` (the check on a
READ-ONLY handle, then `connect`, which sets WAL - itself a write, so the order is the point) and
`db.open_catalog_readonly`. The webui opens per request with a plain `connect`, and those sites
are reached only through a bind that went through `open_catalog` under the workspace lock it
still holds (`BOUND` below): no migration can run under that lock, so the schema the bind checked
is the schema every request reads.

WHAT EACH PART CAN SEE, so nobody assumes more:
- `SITES` is the census: every catalog-opening call in `pam/`, spelled by hand. A new reader that
  is not in it fails `test_every_catalog_open_in_pam_is_in_the_census`.
- The census cannot see a bypass at a site it already lists (the site still calls something,
  just not the check). So every consumer has its OWN refusal row in `CONSUMERS`, driven through
  its real entry point, and each row asserts the sentence, no recovery, no writable connection
  and byte-identical catalog files - in WAL and in rollback-journal mode.
"""

import ast
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, planned

import pam.catalog.db as db
import pam.execute.recover as recover_mod
from pam.catalog.db import NEWER_CATALOG, check_compatible, connect, connect_readonly, migrate
from pam.cli import main
from pam.errors import CatalogTooNewError, MigrationError
from pam.paths import pointer_for, workspace_if_exists

REPO_ROOT = Path(__file__).resolve().parent.parent
PAM_DIR = REPO_ROOT / "pam"
SHIPPED = REPO_ROOT / "pam" / "catalog" / "migrations"
FUTURE = "9999_future.sql"
SIDECARS = ("", "-wal", "-shm", "-journal")
JOURNAL_MODES = ("wal", "delete")

# --- the census: every catalog-opening call site in pam/, BY HAND (M15 Task 8) -------------------
# (module, enclosing function's qualified name, the callee). `CHECKED` callees are the two choke
# points; `BOUND` is the webui's per-request `connect`, which only a checked bind can reach.
CHECKED = ("open_catalog", "open_catalog_readonly")
BOUND = "connect"
SITES = (
    ("pam/enrich/command.py", "run_enrich", "open_catalog"),
    ("pam/execute/apply.py", "run_apply", "open_catalog"),
    ("pam/execute/undo.py", "run_undo", "open_catalog"),
    ("pam/faces/__init__.py", "run_cluster", "open_catalog"),
    ("pam/faces/calibrate.py", "run_calibrate", "open_catalog_readonly"),
    ("pam/faces/export.py", "run_export", "open_catalog"),
    ("pam/faces/purge.py", "run_purge", "open_catalog"),
    ("pam/forget.py", "_refuse_if_mid_repair", "open_catalog_readonly"),
    ("pam/naming/planner.py", "plan_locked", "open_catalog"),
    ("pam/report.py", "run_report_skipped", "open_catalog_readonly"),
    ("pam/report.py", "run_report_session", "open_catalog_readonly"),
    ("pam/report_duplicates.py", "run_report_duplicates", "open_catalog_readonly"),
    ("pam/scan/runner.py", "scan_locked", "open_catalog"),
    ("pam/status.py", "_read_archive", "open_catalog_readonly"),
    ("pam/verify/__init__.py", "run_verify", "open_catalog"),
    ("pam/verify/__init__.py", "run_verify", "open_catalog_readonly"),
    ("pam/webui/__init__.py", "run_review", "open_catalog"),
    ("pam/webui/__init__.py", "_read_counts", "open_catalog"),
    ("pam/webui/__init__.py", "run_faces_label", "open_catalog"),
    ("pam/webui/__init__.py", "_read_people_counts", "open_catalog"),
    ("pam/webui/launcher.py", "bind", "open_catalog"),
    # BOUND: reached only after `launcher.bind`, `run_review` or `run_faces_label` checked.
    ("pam/webui/job_api.py", "_read", "connect"),
    ("pam/webui/jobs.py", "JobRegistry.heal", "connect"),
    ("pam/webui/rename_jobs.py", "renaming.body", "connect"),
    ("pam/webui/rename_jobs.py", "undoing.body", "connect"),
    ("pam/webui/server.py", "ReviewServer.connection", "connect"),
)
OPENERS = frozenset({"connect", "connect_readonly", "open_catalog", "open_catalog_readonly"})


def _catalog_opens() -> Counter:
    """Every call to a catalog opener in `pam/` outside `db.py`, as (module, qualname, callee).

    By NAME (`connect(...)`) and by attribute on `db` or `sqlite3` (`sqlite3.connect(...)` is a
    catalog open that skips both choke points). An import of an opener under another name would
    hide a call from this walk, so `_aliased_openers` refuses that separately."""
    found: Counter = Counter()
    for path in sorted(PAM_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "pam/catalog/db.py":
            continue

        def walk(node: ast.AST, stack: list[str], rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(child, [*stack, child.name])
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Name) and func.id in OPENERS:
                        found[(rel, ".".join(stack), func.id)] += 1
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr in OPENERS
                        and _dotted(func.value) in OPENER_RECEIVERS
                    ):
                        found[(rel, ".".join(stack), f"{_dotted(func.value)}.{func.attr}")] += 1
                walk(child, stack)

        walk(ast.parse(path.read_text(encoding="utf-8")), [])
    return found


# Receivers a catalog open is spelled through; `import pam.catalog.db` (unaliased) is the third.
OPENER_RECEIVERS = frozenset({"db", "sqlite3", "pam.catalog.db"})
OPENER_MODULES = ("sqlite3", "pam.catalog.db")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _aliased_openers() -> list[str]:
    """Every import that binds an opener, or a module that holds one, under ANOTHER name: the
    one shape `_catalog_opens` cannot follow (Task 8 review, F2: `import sqlite3 as sq` and then
    `sq.connect(...)` was a catalog open the census did not see, M4014)."""
    hits = []
    for path in sorted(PAM_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a for a in node.names if a.name in OPENER_MODULES]
            elif isinstance(node, ast.ImportFrom) and node.module in OPENER_MODULES:
                names = list(node.names)
            elif isinstance(node, ast.ImportFrom) and node.module == "pam.catalog":
                names = [a for a in node.names if a.name == "db"]
            else:
                continue
            hits += [f"{path.name}: {a.name} as {a.asname}" for a in names if a.asname]
    return hits


def test_every_catalog_open_in_pam_is_in_the_census():
    """A reader added later, anywhere in `pam/`, fails here until it is classified by hand."""
    assert _catalog_opens() == Counter(SITES)
    assert _aliased_openers() == []


def test_the_census_classifies_every_site_and_bound_sites_are_the_webuis():
    assert len(SITES) == 26  # spelled at 60b4875 + Task 8; a change is a deliberate edit here
    for module, _, callee in SITES:
        assert callee in CHECKED or (callee == BOUND and module.startswith("pam/webui/")), (
            module
        )
    bound = [site for site in SITES if site[2] == BOUND]
    assert len(bound) == 5


def test_the_census_walk_sees_a_raw_open_it_was_not_told_about(tmp_path, monkeypatch):
    """The walker's own reach: a module that calls `sqlite3.connect` and one that calls an
    opener by bare name are both found. Without this the census could pass by finding nothing."""
    fake = tmp_path / "pam"
    (fake / "catalog").mkdir(parents=True)
    (fake / "catalog" / "db.py").write_text("def connect(p):\n    return p\n")
    (fake / "a.py").write_text("import sqlite3\ndef f(p):\n    return sqlite3.connect(p)\n")
    (fake / "b.py").write_text(
        "from pam.catalog.db import connect\nclass K:\n    def g(self, p):\n        connect(p)\n"
    )
    (fake / "c.py").write_text(
        "import pam.catalog.db\ndef h(p):\n    pam.catalog.db.connect(p)\n"
    )
    (fake / "d.py").write_text("def s(sock):\n    sock.connect(('a', 1))\n")
    monkeypatch.setattr(f"{__name__}.REPO_ROOT", tmp_path)
    monkeypatch.setattr(f"{__name__}.PAM_DIR", fake)
    assert _catalog_opens() == Counter(
        {
            ("pam/a.py", "f", "sqlite3.connect"): 1,
            ("pam/b.py", "K.g", "connect"): 1,
            ("pam/c.py", "h", "pam.catalog.db.connect"): 1,
        }
    )


def test_the_alias_guard_sees_every_way_to_rename_an_opener(tmp_path, monkeypatch):
    """F2 (Task 8 review, M4013/M4014): the guard's own reach, spelled by hand. Each of the
    first four files hides a catalog open from the census walk; the last one's imports hide
    nothing and must not be flagged."""
    fake = tmp_path / "pam"
    fake.mkdir()
    (fake / "a.py").write_text("import sqlite3 as sq\n")
    (fake / "b.py").write_text("from pam.catalog import db as d\n")
    (fake / "c.py").write_text("from sqlite3 import connect as c\n")
    (fake / "d.py").write_text("from pam.catalog.db import connect as c\n")
    (fake / "e.py").write_text(
        "import sqlite3\nimport pam.catalog.db\nfrom sqlite3 import Connection\n"
        "from pam.catalog import db, repo as r\nfrom pam.catalog.db import connect\n"
    )
    monkeypatch.setattr(f"{__name__}.PAM_DIR", fake)
    assert _aliased_openers() == [
        "a.py: sqlite3 as sq",
        "b.py: db as d",
        "c.py: connect as c",
        "d.py: connect as c",
    ]


# --- check_compatible itself ------------------------------------------------------------------


def _fresh_catalog(path: Path, migrations_dir: Path | None = None) -> None:
    conn = connect(path)
    migrate(conn, migrations_dir)
    conn.close()


def _add_row(path: Path, filename: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO schema_migrations (filename, sha256, applied_at) VALUES (?, 'x', 'now')",
        (filename,),
    )
    conn.commit()
    conn.close()


def _check(path: Path) -> None:
    conn = connect_readonly(path)
    try:
        check_compatible(conn)
    finally:
        conn.close()


def test_a_fully_known_catalog_is_compatible(tmp_path):
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    _check(path)


def test_a_catalog_with_no_migrations_table_is_compatible(tmp_path):
    path = tmp_path / "catalog.db"
    sqlite3.connect(path).close()
    _check(path)


def test_an_unknown_update_is_refused_with_e1s_sentence(tmp_path):
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    _add_row(path, FUTURE)
    with pytest.raises(CatalogTooNewError) as refused:
        _check(path)
    assert str(refused.value) == (
        "This catalog was written by a newer pam (it has update 9999_future, which this pam"
        " does not know). Install that version again, or a newer one."
    )


def test_the_refusal_is_an_ordinary_pam_error_exit_one(tmp_path, capsys):
    root, ws = _newer_archive(tmp_path, "wal")
    code = main(["status", "--archive", str(root)])
    err = capsys.readouterr().err
    assert code == 1
    assert err.strip() == "x " + NEWER_CATALOG.format(update="9999_future")


def test_it_compares_by_name_not_by_count(tmp_path):
    """One shipped update missing and one unknown present: the COUNT is the shipped count."""
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    conn = sqlite3.connect(path)
    newest = conn.execute("SELECT MAX(filename) FROM schema_migrations").fetchone()[0]
    conn.execute("DELETE FROM schema_migrations WHERE filename = ?", (newest,))
    conn.commit()
    conn.close()
    _add_row(path, FUTURE)
    assert len(list(SHIPPED.glob("*.sql"))) == sum(1 for _ in _rows(path))
    with pytest.raises(CatalogTooNewError, match="9999_future"):
        _check(path)


def test_it_reads_every_row_not_only_the_newest(tmp_path):
    """An unknown update that sorts BELOW every shipped one (a back-port) is still unknown."""
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    _add_row(path, "0003_zz_backport.sql")
    assert max(_rows(path)) != "0003_zz_backport.sql"
    with pytest.raises(CatalogTooNewError, match="update 0003_zz_backport,"):
        _check(path)


def test_the_sentence_names_the_newest_unknown_update(tmp_path):
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    _add_row(path, "0003_zz_backport.sql")
    _add_row(path, "0010_later.sql")
    with pytest.raises(CatalogTooNewError, match="update 0010_later,"):
        _check(path)


def test_check_compatible_writes_nothing(tmp_path):
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    conn = connect(path)
    try:
        check_compatible(conn)
        assert conn.total_changes == 0
        assert not conn.in_transaction
    finally:
        conn.close()


def test_migrate_itself_refuses_a_newer_catalog_before_applying_anything(tmp_path):
    path = tmp_path / "catalog.db"
    _fresh_catalog(path)
    _add_row(path, FUTURE)
    conn = connect(path)
    try:
        with pytest.raises(CatalogTooNewError, match="9999_future"):
            migrate(conn)
    finally:
        conn.close()


def test_a_changed_checksum_keeps_its_existing_message(tmp_path):
    copied = tmp_path / "migrations"
    shutil.copytree(SHIPPED, copied)
    path = tmp_path / "catalog.db"
    _fresh_catalog(path, copied)
    first = sorted(copied.glob("*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    conn = connect(path)
    try:
        with pytest.raises(MigrationError) as refused:
            migrate(conn, copied)
    finally:
        conn.close()
    assert not isinstance(refused.value, CatalogTooNewError)
    assert str(refused.value) == (
        f"Migration {first.name} has changed since it was applied. The catalog schema cannot"
        " be trusted - restore the original migration file or rebuild the catalog with a"
        " fresh scan."
    )


def test_a_real_forward_schema_is_refused_by_the_shipped_set(tmp_path):
    """Not a fabricated row: a genuine `0009_test.sql` applied by `migrate` from a copy of the
    migrations folder, and then a real command of THIS pam opens the catalog."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    ws = workspace_if_exists(root)
    assert ws is not None
    forward = tmp_path / "migrations"
    shutil.copytree(SHIPPED, forward)
    (forward / "0009_test.sql").write_text(
        "CREATE TABLE future_feature (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    _fresh_catalog(ws.db_path, forward)
    assert "0009_test.sql" in _rows(ws.db_path)
    from pam.naming.planner import run_plan

    with pytest.raises(CatalogTooNewError) as refused:
        run_plan(root)
    assert str(refused.value) == NEWER_CATALOG.format(update="0009_test")


# --- Q10, the other direction: an OLDER pam's catalog still opens (Task 8 review, F1) ----------
# Every migration filename a released pam has written into a catalog, spelled by hand from the
# `v0.1-m14` tag - NOT read from `SHIPPED`, which is what it checks. Under E1 a shipped migration
# renamed or deleted makes every catalog those releases wrote name an update "this pam does not
# know", refused with a FALSE "written by a newer pam" sentence (M4012: renaming 0008 left the
# whole suite green). Append a name when a release ships a migration; never edit one.
RELEASED_MIGRATIONS = (
    "0001_init.sql",
    "0002_scanner.sql",
    "0003_video_reread.sql",
    "0004_plan_unit_key.sql",
    "0005_plan_already_conforming.sql",
    "0006_event_label_source.sql",
    "0007_face_review_state.sql",
    "0008_files_rel_path_nocase.sql",
)


def test_every_released_migration_name_is_still_shipped():
    assert len(RELEASED_MIGRATIONS) >= 8  # the floor: v0.1-m14's eight
    shipped = {path.name for path in SHIPPED.glob("*.sql")}
    assert [name for name in RELEASED_MIGRATIONS if name not in shipped] == []


def test_a_catalog_a_released_pam_wrote_is_compatible(tmp_path):
    """The table is written by hand with the released names, not by `migrate` from SHIPPED."""
    path = tmp_path / "catalog.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE,"
        " sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO schema_migrations (filename, sha256, applied_at) VALUES (?, 'x', 'then')",
        [(name,) for name in RELEASED_MIGRATIONS],
    )
    conn.commit()
    conn.close()
    _check(path)


def _rows(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT filename FROM schema_migrations")]
    finally:
        conn.close()


# --- every consumer refuses: one row each, WAL and rollback-journal ------------------------------


def _newer_archive(tmp_path: Path, mode: str) -> tuple[Path, object]:
    """A scanned and planned archive whose catalog a newer pam has migrated (`9999_future.sql`).

    In WAL mode the catalog is left as every lock-free reader (`pam status`) leaves it: one
    read-only open creates an empty `-wal` and the wal-index `-shm` beside a WAL catalog that has
    none, and nothing a read-only handle can do removes them. That is SQLite's, not the check's;
    from that state on, a read-only open changes no byte of any of the four files (measured)."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = sqlite3.connect(ws.db_path)
    if mode == "delete":
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute(
        "INSERT INTO schema_migrations (filename, sha256, applied_at) VALUES (?, 'x', 'now')",
        (FUTURE,),
    )
    conn.commit()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == mode
    conn.close()
    if mode == "wal":
        reader = connect_readonly(ws.db_path)
        reader.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        reader.close()
    return root, ws


def _files(ws) -> dict[str, bytes | None]:
    out = {}
    for suffix in SIDECARS:
        path = Path(f"{ws.db_path}{suffix}")
        out[suffix] = path.read_bytes() if path.exists() else None
    return out


@pytest.fixture
def spies(monkeypatch):
    """Recovery and writable connections, watched where every consumer reaches them."""
    seen: dict[str, list] = {"recover": [], "connect": []}
    real_recover, real_connect = recover_mod.recover, db.connect

    def recover(*args, **kwargs):
        seen["recover"].append(args)
        return real_recover(*args, **kwargs)

    def connect_spy(path):
        seen["connect"].append(path)
        return real_connect(path)

    monkeypatch.setattr(recover_mod, "recover", recover)
    monkeypatch.setattr(db, "connect", connect_spy)
    return seen


def _cli(*argv):
    def run(root, ws, monkeypatch):
        code = main([part.format(root=root) for part in argv])
        if code == 0:
            return None
        return code

    return run


def _review(root, ws, monkeypatch):
    from pam.webui import run_review

    run_review(None, root, serve=lambda server: None, opener=lambda url: True)


def _faces_label(root, ws, monkeypatch):
    from pam.webui import run_faces_label

    run_faces_label(root, serve=lambda server: None, opener=lambda url: True)


def _faces_calibrate(root, ws, monkeypatch):
    from pam.enrich import faces_model
    from pam.faces.calibrate import run_calibrate

    monkeypatch.setattr(faces_model, "import_runtime", lambda: (None, None))
    run_calibrate(root)


def _faces_cluster(root, ws, monkeypatch):
    from pam.faces import run_cluster

    ws.config_path.write_text("[enrich.faces]\ncluster_epsilon = 0.35\n", encoding="utf-8")
    run_cluster(root, announce=lambda line: None)


def _scan(root, ws, monkeypatch):
    from pam.scan.runner import run_scan

    run_scan(root, exiftool_command=FAKE_COMMAND)


def _start_bind_route(root, ws, monkeypatch):
    """`POST /api/bind`'s function: a refusal is an `ApiError` 409, and the server stays unbound."""
    from pam.webui import launcher
    from pam.webui.api import ApiError

    server = launcher.start_server(launcher._start_logger())
    try:
        with pytest.raises(ApiError) as refused:
            launcher.bind(server, root)
        assert refused.value.status == 409
        assert server._workspace is None
        assert ws.lock_path.exists() is False
        raise CatalogTooNewError(refused.value.message)
    finally:
        server.server_close()


def _start_cli(root, ws, monkeypatch):
    """`pam start PATH`: the bind's refusal reaches the console, exit 1, and nothing is served."""
    from pam.webui import launcher

    served: list = []
    monkeypatch.setattr(launcher.StartServer, "serve_forever", lambda *a, **k: served.append(1))
    monkeypatch.setattr("webbrowser.open", lambda url: served.append(url) or True)
    code = main(["start", str(root)])
    assert served == []
    return code


# (row id, driver). A driver either raises the refusal or returns the CLI's exit code. Every
# CHECKED site in `SITES` has a row here or in `AFTER_A_CHECKED_OPEN` (spelled by hand).
CONSUMERS = (
    ("scan", _scan),
    ("plan", _cli("plan", "--archive", "{root}")),
    ("apply", _cli("apply", "1", "--archive", "{root}")),
    ("undo", _cli("undo", "s-20260101-000000", "--archive", "{root}")),
    ("enrich", _cli("enrich", "--archive", "{root}")),
    ("verify", _cli("verify", "--archive", "{root}")),
    ("status", _cli("status", "--archive", "{root}")),
    ("report skipped", _cli("report", "skipped", "--archive", "{root}")),
    ("report session", _cli("report", "session", "s-20260101-000000", "--archive", "{root}")),
    ("report duplicates", _cli("report", "duplicates", "--archive", "{root}")),
    ("forget", _cli("forget", "{root}")),
    ("faces cluster", _faces_cluster),
    ("faces export", _cli("faces", "export", "--archive", "{root}")),
    ("faces purge", _cli("faces", "purge", "--archive", "{root}")),
    ("faces calibrate", _faces_calibrate),
    ("review", _review),
    ("faces label", _faces_label),
    ("start bind", _start_bind_route),
    ("pam start PATH", _start_cli),
)
CONSUMER_IDS = [row[0] for row in CONSUMERS]


def test_every_checked_site_has_its_own_refusal_row():
    """Spelled by hand from `SITES`: which row drives which checked site."""
    drives = {
        "pam/enrich/command.py:run_enrich": "enrich",
        "pam/execute/apply.py:run_apply": "apply",
        "pam/execute/undo.py:run_undo": "undo",
        "pam/faces/__init__.py:run_cluster": "faces cluster",
        "pam/faces/calibrate.py:run_calibrate": "faces calibrate",
        "pam/faces/export.py:run_export": "faces export",
        "pam/faces/purge.py:run_purge": "faces purge",
        "pam/forget.py:_refuse_if_mid_repair": "forget",
        "pam/naming/planner.py:plan_locked": "plan",
        "pam/report.py:run_report_skipped": "report skipped",
        "pam/report.py:run_report_session": "report session",
        "pam/report_duplicates.py:run_report_duplicates": "report duplicates",
        "pam/scan/runner.py:scan_locked": "scan",
        "pam/status.py:_read_archive": "status",
        "pam/verify/__init__.py:run_verify": "verify",  # and the reader: AFTER_A_CHECKED_OPEN
        "pam/webui/__init__.py:run_review": "review",
        "pam/webui/__init__.py:_read_counts": "after: review's closing counts",
        "pam/webui/__init__.py:run_faces_label": "faces label",
        "pam/webui/__init__.py:_read_people_counts": "after: faces label's closing counts",
        "pam/webui/launcher.py:bind": "start bind",
    }
    checked = {f"{m}:{q}" for m, q, callee in SITES if callee in CHECKED}
    assert set(drives) == checked
    rows = set(CONSUMER_IDS) | {f"after: {name}" for name in AFTER_IDS}
    assert set(drives.values()) <= rows
    assert len(CONSUMERS) == 19


@pytest.mark.parametrize("mode", JOURNAL_MODES)
@pytest.mark.parametrize("consumer", CONSUMERS, ids=CONSUMER_IDS)
def test_every_consumer_refuses_a_newer_catalog_and_writes_nothing(
    tmp_path, capsys, monkeypatch, spies, consumer, mode
):
    """The sentence, no recovery, no writable connection, and the catalog's four files
    byte-identical - before and after, in WAL and in rollback-journal mode."""
    _, drive = consumer
    root, ws = _newer_archive(tmp_path, mode)
    spies["recover"].clear()  # building the archive scanned and planned; only the run counts
    spies["connect"].clear()
    before = _files(ws)
    pointer_before = pointer_for(root)
    sentence = NEWER_CATALOG.format(update="9999_future")

    try:
        code = drive(root, ws, monkeypatch)
    except CatalogTooNewError as refused:
        assert str(refused) == sentence
    else:
        assert code == 1
        assert "x " + sentence in capsys.readouterr().err

    assert spies["recover"] == []
    assert spies["connect"] == []
    assert _files(ws) == before
    assert pointer_for(root) == pointer_before


# --- the sites only reachable AFTER a checked open (the closing counts, verify's reader) ---------
# A newer pam cannot migrate under the lock these commands hold, so the state is built by hand:
# the newer row is added between the first checked open and the second.


def _review_counts(root, ws, monkeypatch):
    from pam.webui import run_review

    run_review(
        None, root, serve=lambda server: _add_row(ws.db_path, FUTURE), opener=lambda u: True
    )


def _people_counts(root, ws, monkeypatch):
    from pam.webui import run_faces_label

    run_faces_label(
        root, serve=lambda server: _add_row(ws.db_path, FUTURE), opener=lambda u: True
    )


def _verify_reader(root, ws, monkeypatch):
    import pam.verify as verify_mod

    real = verify_mod.run_recovery

    def recover_then_newer(conn, archive_root, logger):
        summary = real(conn, archive_root, logger)
        conn.execute(
            "INSERT INTO schema_migrations (filename, sha256, applied_at) VALUES (?, 'x', 'now')",
            (FUTURE,),
        )
        conn.commit()
        return summary

    monkeypatch.setattr(verify_mod, "run_recovery", recover_then_newer)
    verify_mod.run_verify(root)


AFTER_A_CHECKED_OPEN = (
    ("review's closing counts", _review_counts),
    ("faces label's closing counts", _people_counts),
    ("verify's reader", _verify_reader),
)
AFTER_IDS = [row[0] for row in AFTER_A_CHECKED_OPEN]


@pytest.mark.parametrize("site", AFTER_A_CHECKED_OPEN, ids=AFTER_IDS)
def test_a_second_open_in_the_same_command_is_checked_too(tmp_path, monkeypatch, site):
    _, drive = site
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    ws = workspace_if_exists(root)
    with pytest.raises(CatalogTooNewError, match="update 9999_future,"):
        drive(root, ws, monkeypatch)


# --- placement: the check runs on a read-only handle BEFORE any writable connection -------------


def test_the_check_runs_read_only_before_the_first_writable_connection(tmp_path, monkeypatch):
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    calls: list[str] = []
    real_ro, real_rw, real_check = db.connect_readonly, db.connect, db.check_compatible

    def ro(path):
        calls.append("readonly")
        return real_ro(path)

    def rw(path):
        calls.append("connect")
        return real_rw(path)

    def check(conn, migrations_dir=None):
        calls.append("check")
        return real_check(conn, migrations_dir)

    monkeypatch.setattr(db, "connect_readonly", ro)
    monkeypatch.setattr(db, "connect", rw)
    monkeypatch.setattr(db, "check_compatible", check)
    from pam.naming.planner import run_plan

    run_plan(root)
    # `migrate` checks again on the writable handle; that is the second layer, not the first.
    assert calls[:3] == ["readonly", "check", "connect"]


def test_a_fully_known_catalog_opens_through_every_kind_of_consumer(tmp_path, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    assert main(["status", "--archive", str(root)]) == 0
    assert main(["verify", "--archive", str(root)]) == 0
    assert main(["plan", "--archive", str(root)]) == 0
    assert "newer pam" not in capsys.readouterr().err


# --- S2-F1: `pam status` with no --archive names each newer folder, keeps the healthy ones -------
# The controller's ruling (Task 8 S2): E1's sentence stays verbatim; status reports every other
# known folder as normal and, for each newer catalog, prints the folder's path followed by E1's
# sentence; exit 1 if any folder was refused. The sentence is SPELLED here, never read from
# `NEWER_CATALOG`: a test that formats the constant passes any rewording of it.
SENTENCE = (
    "This catalog was written by a newer pam (it has update 9999_future, which this pam does not"
    " know). Install that version again, or a newer one."
)


def _mark_newer(root: Path) -> None:
    """`_newer_archive`'s WAL state, on an archive that already exists."""
    ws = workspace_if_exists(root)
    assert ws is not None
    _add_row(ws.db_path, FUTURE)
    reader = connect_readonly(ws.db_path)
    reader.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    reader.close()


def _folders(tmp_path: Path, count: int) -> list[Path]:
    """`count` scanned and planned folders, in the order `pam status` lists them."""
    from pam.paths import known_archives

    for index in range(count):
        parent = tmp_path / f"f{index}"
        parent.mkdir()
        planned(parent, ["IMG_1.JPG"])
    order = [known.path for known in known_archives()]
    assert len(order) == count
    return order


def _block(capsys, *argv: str) -> tuple[int, list[str]]:
    capsys.readouterr()
    code = main(list(argv))
    return code, capsys.readouterr().out.splitlines()


def _ledger_text() -> str:
    import os

    from pam.catalog.ledger import LEDGER_ENV

    path = Path(os.environ[LEDGER_ENV])
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _contains_run(lines: list[str], run: list[str]) -> bool:
    return any(lines[i : i + len(run)] == run for i in range(len(lines) - len(run) + 1))


# Independently written: the newer folder's position in the listing (S2-F1's two fixtures).
POSITIONS = ("first", "last")
assert POSITIONS


@pytest.mark.parametrize("position", POSITIONS)
def test_status_names_the_newer_folder_and_still_reports_the_healthy_one(
    tmp_path, capsys, position
):
    order = _folders(tmp_path, 2)
    newer, healthy = (order[0], order[1]) if position == "first" else (order[1], order[0])
    code, healthy_block = _block(capsys, "status", "--archive", str(healthy))
    assert code == 0
    _mark_newer(newer)
    ws = workspace_if_exists(newer)
    before = _files(ws)

    code, lines = _block(capsys, "status")

    assert code == 1
    assert lines[0] == "pam knows about 2 photo folders."
    assert _contains_run(lines, healthy_block)  # the healthy folder exactly as on its own
    assert sum(line.strip().startswith("Last scanned") for line in lines) == 1
    at = lines.index(str(newer))
    assert lines[at + 1].strip() == SENTENCE
    assert _files(ws) == before
    assert "9999_future" not in _ledger_text()


def test_status_with_every_folder_healthy_is_unchanged_and_exits_zero(tmp_path, capsys):
    order = _folders(tmp_path, 2)
    blocks = [_block(capsys, "status", "--archive", str(root))[1] for root in order]

    code, lines = _block(capsys, "status")

    assert code == 0
    assert lines == ["pam knows about 2 photo folders.", "", *blocks[0], "", *blocks[1]]
    assert "newer pam" not in "\n".join(lines)


def test_status_with_only_a_newer_folder_names_it_and_exits_one(tmp_path, capsys):
    (newer,) = _folders(tmp_path, 1)
    _mark_newer(newer)
    ws = workspace_if_exists(newer)
    before = _files(ws)

    code, lines = _block(capsys, "status")

    assert code == 1
    assert [line.strip() for line in lines] == [str(newer), SENTENCE]
    assert _files(ws) == before
    assert "9999_future" not in _ledger_text()


def test_bare_pam_names_the_newer_folder_and_still_guides_the_healthy_one(tmp_path, capsys):
    """Bare `pam` renders the same `collect_status`: the same per-folder rule, the same exit."""
    newer, healthy = _folders(tmp_path, 2)
    _mark_newer(newer)

    code, lines = _block(capsys)

    assert code == 1
    at = lines.index(str(newer))
    assert lines[at + 1].strip() == SENTENCE
    assert any(line.startswith(f"{healthy}  2 files, last scanned") for line in lines)


# --- the reviewer's closing tests (M4177, M4179) ---------------------------------------------------


def test_bare_pam_shows_a_newer_folder_as_its_path_and_sentence_and_nothing_else(
    tmp_path, capsys
):
    """M4177: without `continue` the refused folder ALSO got a guide headline and next steps, and
    the first test passed (it only reads the line after the path). The block ends at the blank
    line: path, sentence, blank - nothing about reading or scanning a folder pam cannot read."""
    newer, healthy = _folders(tmp_path, 2)
    _mark_newer(newer)

    code, lines = _block(capsys)

    assert code == 1
    at = lines.index(str(newer))
    assert [line.strip() for line in lines[at : at + 3]] == [str(newer), SENTENCE, ""]
    assert sum(str(newer) in line for line in lines) == 1  # no second, headline mention


def test_a_folder_failing_for_another_reason_is_not_dressed_as_a_newer_catalog(
    tmp_path, capsys, monkeypatch
):
    """M4179: only E1's refusal is reported per folder. Any other failure keeps its own path
    (an error, nothing printed as a status); widening the `except` to `Exception` would print
    it as a folder pam is merely too old for."""
    import pam.status as status_mod

    _folders(tmp_path, 1)

    def broken(_known):
        raise sqlite3.DatabaseError("boom-not-e1")

    monkeypatch.setattr(status_mod, "_read_archive", broken)
    capsys.readouterr()
    try:
        main(["status"])
    except sqlite3.DatabaseError:
        pass
    out = capsys.readouterr().out
    assert "boom-not-e1" not in out
    assert "newer pam" not in out

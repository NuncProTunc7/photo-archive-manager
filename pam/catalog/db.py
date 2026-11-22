"""Catalog connection factory and checksummed, transactional migration runner."""

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from pam import faults
from pam.catalog import ledger
from pam.errors import CatalogTooNewError, MigrationError

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Migration filenames are constrained so they can be safely inlined into SQL below. The grammar
# lives with the compatibility ledger (E12), which reads names other programs wrote.
_MIGRATION_NAME = ledger.MIGRATION_NAME


def shipped_migrations() -> tuple[str, ...]:
    """Every migration filename this pam ships, sorted. `pam update` (M15 Task 12) names the ones
    an offered release ships and this pam does not."""
    return tuple(sorted(path.name for path in _MIGRATIONS_DIR.glob("*.sql")))


# E1 (M15 Task 8, design 4 / 7.5), verbatim. `{update}` is the filename without `.sql`.
NEWER_CATALOG = (
    "This catalog was written by a newer pam (it has update {update}, which this pam does not"
    " know). Install that version again, or a newer one."
)


def check_compatible(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    """E1: refuse a catalog whose `schema_migrations` names a file this pam does not ship.

    Pure SQL over `schema_migrations`, and it never writes the catalog - it runs on a read-only
    handle (the one file it may append to is the ledger, below).
    BY NAME, EVERY ROW: a count passes a catalog that lost one update and gained another, and
    the newest row alone passes an unknown update that sorts below a known one (a back-port).
    The sentence names the newest unknown update. No table at all is a catalog nothing has
    migrated yet: nothing in it is unknown.

    E12's REGISTRATION: a compatible catalog's newest applied update is passed to the
    compatibility ledger, which appends it only when it is higher than what it holds - so a
    catalog copied or restored from elsewhere is recorded the first time this pam opens it.
    The ledger is a file outside the catalog; a failure there is logged, never raised.
    """
    mig_dir = migrations_dir or _MIGRATIONS_DIR
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()[0]
    if not has_table:
        return
    shipped = {path.name for path in mig_dir.glob("*.sql")}
    applied = [row[0] for row in conn.execute("SELECT filename FROM schema_migrations")]
    unknown = sorted(name for name in applied if name not in shipped)
    if unknown:
        update = unknown[-1].removesuffix(".sql")
        raise CatalogTooNewError(NEWER_CATALOG.format(update=update))
    if applied:
        ledger.register(max(applied))


def ensure_compatible(db_path: Path, migrations_dir: Path | None = None) -> None:
    """`check_compatible` on a READ-ONLY handle, opened and closed here (E1, rev 3).

    Before any writable connection, because `connect` sets `journal_mode=WAL` and `migrate`
    creates its table: both are writes, and a refused catalog must be byte-identical. A
    catalog file that does not exist yet (a first scan) has nothing in it to refuse.
    """
    if not db_path.exists():
        return
    conn = connect_readonly(db_path)
    try:
        check_compatible(conn, migrations_dir)
    finally:
        conn.close()


def open_catalog(db_path: Path) -> sqlite3.Connection:
    """THE CHOKE POINT for a writable catalog connection (E1): the read-only check, then
    `connect`. Every archive-scoped command opens its catalog here, BEFORE recovery and before
    any archive access; `tests/test_catalog_compat.py`'s census holds every call site."""
    ensure_compatible(db_path)
    return connect(db_path)


def open_catalog_readonly(db_path: Path) -> sqlite3.Connection:
    """THE CHOKE POINT for a read-only catalog connection (E1): `connect_readonly`, checked."""
    conn = connect_readonly(db_path)
    try:
        check_compatible(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """A connection that CANNOT write. `pam verify`'s (spec 7.5, plan clarification 4).

    Spec 7.5 says the checker is "strictly read-only", and the plan's clarification 4
    makes that structural rather than a promise: "a checker must not be able to write".
    A checker with a writable handle is one edit away from repairing something it was
    asked only to report, and the one thing spec 7.5 says about the split-unit case is
    that `pam verify` never repairs it.

    Two layers, because they fail differently and both are cheap. `mode=ro` is refused
    by SQLite at the file level and survives any PRAGMA a later caller issues;
    `query_only` is a connection setting that also refuses a write to a TEMP table,
    which `mode=ro` permits. Neither is load-bearing alone.

    No `journal_mode=WAL` here, unlike `connect()`: setting the journal mode is itself
    a write to the database header and fails on a read-only handle. The mode is already
    WAL - it is a persistent property of the file, set the first time pam opened it.

    A URI rather than a plain path because `mode=ro` has no other spelling. `as_uri()`
    percent-encodes, so a workspace under a folder with spaces in its name (every
    Windows user profile that has "OneDrive - Something" in it) opens correctly.
    """
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextlib.contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """S13: every read inside this block sees ONE committed state of the catalog.

    WAL gives a reader a snapshot at its first read inside a transaction and keeps it until
    the transaction ends; outside one, every statement takes a fresh snapshot, which is how
    `pam status` printed a plan count from before a `pam plan` and a plan list from after.
    ROLLBACK, not COMMIT, at the end: the connection is read-only (`connect_readonly`), so
    there is nothing to commit, and saying so is clearer than a COMMIT that does nothing.
    Keep the block SHORT - a held snapshot blocks a WAL checkpoint, which is what `pam faces
    purge`'s compaction measures (N7b)."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("ROLLBACK")


def schema_version(conn: sqlite3.Connection) -> int:
    """Number of applied migrations (0 on a fresh database)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row[0] == 0:
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    """Apply pending migrations in filename order; verify checksums of applied ones.

    E12's RESERVATION: every name and checksum is checked FIRST, then - only when something is
    pending - the newest pending filename is appended to the compatibility ledger and fsynced,
    BEFORE the first `executescript`. A reservation that cannot be written raises `LedgerError`
    and nothing is migrated (fail closed). A crash between the reservation and a migration's
    COMMIT leaves the ledger higher than the catalog, which is the conservative direction.
    """
    mig_dir = migrations_dir or _MIGRATIONS_DIR
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
             id INTEGER PRIMARY KEY,
             filename TEXT NOT NULL UNIQUE,
             sha256 TEXT NOT NULL,
             applied_at TEXT NOT NULL
           )"""
    )
    conn.commit()
    # E1: before applying anything. Every caller has already checked on a read-only handle
    # (`open_catalog`); this is the layer that survives a caller that did not.
    check_compatible(conn, mig_dir)
    applied = {
        row["filename"]: row["sha256"]
        for row in conn.execute("SELECT filename, sha256 FROM schema_migrations")
    }
    pending: list[tuple[str, str, str]] = []
    for path in sorted(mig_dir.glob("*.sql")):
        if not _MIGRATION_NAME.match(path.name):
            raise MigrationError(
                f"Migration filename {path.name} is invalid: expected NNNN_name.sql "
                "with lowercase letters, digits, and underscores only."
            )
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if path.name in applied:
            if applied[path.name] != digest:
                raise MigrationError(
                    f"Migration {path.name} has changed since it was applied. "
                    "The catalog schema cannot be trusted - restore the original "
                    "migration file or rebuild the catalog with a fresh scan."
                )
            continue
        pending.append((path.name, sql, digest))
    if not pending:
        return
    faults.maybe_fault("before_ledger_reservation")
    ledger.reserve(pending[-1][0])
    faults.maybe_fault("after_ledger_reservation")
    for name, sql, digest in pending:
        # Atomicity subtlety: sqlite3.executescript() issues an implicit COMMIT
        # before running, so wrapping it in a Python-side transaction does NOT
        # work. Instead the transaction lives inside the script text itself:
        # DDL + the bookkeeping row commit together or roll back together.
        # Consequently migration files must NEVER contain their own BEGIN/COMMIT.
        # (name and digest are safe to inline: filename regex-validated
        # above, digest is hex.)
        wrapped = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (filename, sha256, applied_at) "
            f"VALUES ('{name}', '{digest}', datetime('now'));\n"
            "COMMIT;"
        )
        try:
            conn.executescript(wrapped)
        except sqlite3.Error as e:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")  # no transaction left open - nothing to roll back
            raise MigrationError(f"Migration {name} failed and was rolled back: {e}") from None
        faults.maybe_fault("after_migration_commit")


# What `pam faces purge` prints after "Compaction: FAILED - " when the checkpoint was
# blocked. It names the likely cause, because the user can act on it and pam cannot: pam's
# read-only commands take no lock, and a program that is not pam takes none of pam's.
CHECKPOINT_BLOCKED = (
    "another program is reading pam's catalog (for example pam status, pam report or pam"
    " verify in another window), so the deleted records are still in its write-ahead log."
    " Close it, then run pam faces purge again"
)


def compact(conn: sqlite3.Connection) -> None:
    """Checkpoint the write-ahead log, then `VACUUM`. A19's PHYSICAL half.

    HERE AND NOT IN `pam/faces/purge.py`, and the reason is the invariant rather than
    convenience. `tests/test_repo.py::test_no_sql_outside_repo_and_migration_runner` puts
    every `conn.execute` in this module or in `catalog/repo/`, and it is right to: these
    two statements are DATABASE-level maintenance over no table at all, which is this
    module's subject and not any repo module's. A command that spelled them itself would
    be the first `.execute(` outside the catalog layer, and the guard would have had to
    learn an exception for a rule that has held since M2.

    THE CHECKPOINT COMES FIRST AND IS NOT OPTIONAL. `connect` runs in WAL mode, so rows
    deleted a moment ago are still sitting in `catalog.db-wal` until a checkpoint moves
    them; a `VACUUM` alone would rewrite the main file and leave them there. `TRUNCATE`
    rather than `PASSIVE` because it also resets the WAL file to zero length, which is the
    state A19's oracle can read.

    NEITHER STATEMENT MAY RUN INSIDE A TRANSACTION, so the caller commits first - which is
    also why A19 makes this a separate act with a separate outcome rather than a sixth
    step of the deletion it follows.

    A BLOCKED CHECKPOINT DOES NOT RAISE (design 10 rev 26, N7b; the M11 review's R1). SQLite
    answers `(busy, log, checkpointed)` and carries on, so success must be READ, never
    inferred from the absence of an exception. Measured: with a read-only handle holding a
    snapshot - what `pam status`, `pam report` and `pam verify` open, without the workspace
    lock and by design - the checkpoint returned `(1, 3, 0)`, `VACUUM` then succeeded, and
    the purged name was still in `catalog.db-wal` while pam reported "compacted".

    `busy` ALONE, AND THAT IS MEASURED, NOT ASSUMED. A reader whose snapshot PRE-DATES the
    deletion gives `(1, 3, 0)`; a reader that opened AFTER it gives `(1, 3, 3)` - every frame
    checkpointed, the truncation blocked, and the file still 12 KB with the name in it. So a
    check on `log != checkpointed` would pass the second case, and under TRUNCATE a non-busy
    answer is always complete (`(0, 0, 0)`, or `(0, -1, -1)` on a database not in WAL mode),
    so that operand could never fire on its own either. `busy` is raised here as the
    `sqlite3.Error` the caller already reports as a failed compaction with its safe retry.
    """
    busy, _log, _checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if busy:
        raise sqlite3.OperationalError(CHECKPOINT_BLOCKED)
    conn.execute("VACUUM")


def freelist_count(conn: sqlite3.Connection) -> int:
    """Pages the database holds and is not using. A19's compaction ORACLE.

    "The correctness oracle for compaction is `PRAGMA freelist_count` and WAL state -
    NEVER file size, which is nondeterministic and can pass without proving the promised
    result." A successful `VACUUM` leaves this at 0; a large delete without one leaves it
    above 0. It is a reading rather than an assertion so that the report can carry the
    number and a test can read it back.
    """
    return int(conn.execute("PRAGMA freelist_count").fetchone()[0])

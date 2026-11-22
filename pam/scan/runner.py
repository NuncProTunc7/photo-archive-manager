"""pam scan orchestration: walk -> diff -> hash -> extract -> companions -> summary."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog, schema_version
from pam.config import Config, load_config
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.paths import record_archive_root, workspace_for
from pam.scan import companions, exiftool, objective, walker
from pam.scan.console import SKIPPED_POINTER as SKIPPED_POINTER
from pam.scan.console import _progress
from pam.scan.console import print_summary as print_summary
from pam.scan.exiftool import ExifTool
from pam.scan.hashing import sha256_file
from pam.sessionlog import close_session_log, open_session_log

# M14 Task 5a (C5-1): the console (progress, summary, warning box) is `console.py`'s. Its names
# are imported back above, so every caller (`cli.py`) and test that names them here is unchanged.

_BATCH = 100


@dataclass
class ScanSummary:
    total: int = 0
    images: int = 0
    videos: int = 0
    sidecars: int = 0
    other: int = 0
    new: int = 0
    unchanged: int = 0
    changed: int = 0
    missing: int = 0
    read: int = 0  # files actually opened this scan (hashed + extracted)
    placeholders: int = 0
    sync_provider: str | None = None
    errors: list[str] = field(default_factory=list)
    log_path: Path | None = None
    # M14 Task 5 (C5-2): True when `should_stop` ended the read early. `read` then counts the
    # files actually read, and the next scan reads exactly the files this one did not (P1).
    # Grouping and its commit STILL ran (C5-4 as amended 2026-11-13, review R1's F1): every
    # walked file is grouped with its stem-mates, read or not. A stopped read never plans.
    stopped: bool = False

    @property
    def had_errors(self) -> bool:
        """spec 13 rev 6: a command that recorded per-file errors exits 1, not 0.

        This changes pam scan, which has exited 0 with errors since M2. Deliberate:
        scan, plan and (from M5) apply must not disagree about what an error means.
        """
        return bool(self.errors)


def run_scan(archive_root: Path, exiftool_command: list[str] | None = None) -> ScanSummary:
    """Scan one archive folder. Read-only towards the archive, always."""
    root = archive_root.expanduser()
    if not root.is_dir():
        raise PamError(
            f"{archive_root} is not a folder pam can scan. Check the path and try again."
        )
    if exiftool_command is None:
        exiftool_command = exiftool.default_command()  # refuse early (spec 5.3)
    root = root.resolve()
    ws = workspace_for(root)
    config = load_config(ws.config_path)
    with WorkspaceLock(ws.lock_path, ws) as held:
        # UNDER the lock, always. Two concurrent scans of one archive used to reach
        # this write unserialized and race on the pointer's temp file, and the
        # exception surfaced BEFORE WorkspaceLock could produce its friendly
        # "another pam is running" - so the second scan died with exit code 2 and a
        # traceback instead. Nothing above depends on the pointer, and writing it
        # touches no database, so it simply belongs inside.
        record_archive_root(ws)
        logger, log_path = open_session_log(ws.logs_dir, suffix="scan")
        try:
            summary = scan_locked(held, root, config, exiftool_command, logger)
        except BaseException:
            # spec 13's log path is UNCONDITIONAL - "every session (any command)" -
            # and pam/cli.py's `except Exception` prints a traceback with no `Log:`
            # line at all. M5 closed this for apply and undo; scan and plan were the
            # remainder (M5 open question 6), and this is it.
            #
            # `except BaseException`, not `except Exception`: a `crash:` fault raises
            # SystemExit and a real Ctrl+C raises KeyboardInterrupt, both outside the
            # Exception hierarchy, and those are precisely the two cases where the user
            # needs the log. M5's finding F2 was this exact narrowing, found by mutation.
            #
            # ONE implementation, in `execute/reporter.py`. It was written twice
            # verbatim in apply.py and undo.py before M5 Task 9 consolidated it; a third
            # copy here would undo that. The empty session id is honest rather than
            # incidental: a scan has a log, not an undoable session, and
            # `announce_interrupted` prints only what it is given.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    summary.log_path = log_path
    return summary


def scan_locked(
    held: HeldWorkspace,
    root: Path,
    config: Config,
    exiftool_command: list[str],
    logger: logging.Logger,
    *,
    progress: Callable[[int, int], None] = _progress,
    should_stop: Callable[[], bool] | None = None,
) -> ScanSummary:
    """The scan's body, under a lock the caller proves it holds (M14 Task 5, Step 1).

    `root` is resolved. `held` must name `workspace_for(root)` and still be held, or this
    raises AssertionError before touching anything. The caller owns the session log.
    `progress(done, total)` and `should_stop()` are C5-2's: see `_scan`.
    """
    ws = held.require(workspace_for(root))
    conn = open_catalog(ws.db_path)
    try:
        migrate(conn)
        logger.info("scan start: root=%s schema=%s", root, schema_version(conn))
        # spec 7.2: BEFORE THE WALK, not merely before the summary. A scan that
        # runs first records a completed-but-unresolved rename as one new file
        # and one missing file, and recovery's adoption branch then collides
        # with the `files` row the scan just created (reproduced, 2026-08-06).
        # Inside the lock, which is what makes a plan found in `applying`
        # provably the property of a dead process.
        run_recovery(conn, root, logger)
        return _scan(
            conn,
            root,
            config,
            exiftool_command,
            logger,
            progress=progress,
            should_stop=should_stop,
        )
    finally:
        conn.close()


def _scan(
    conn: Connection,
    root: Path,
    config: Config,
    command: list[str],
    logger: logging.Logger,
    *,
    progress: Callable[[int, int], None] = _progress,
    should_stop: Callable[[], bool] | None = None,
) -> ScanSummary:
    summary = ScanSummary()
    summary.sync_provider = walker.detect_sync_root(root)
    disk, walk_errors = walker.walk(root, config)
    for err in walk_errors:
        logger.error("walk: %s", err)
    summary.errors.extend(walk_errors)

    summary.total = len(disk)
    for f in disk:
        if f.media_type == "image":
            summary.images += 1
        elif f.media_type == "video":
            summary.videos += 1
        elif f.media_type == "sidecar":
            summary.sidecars += 1
        else:
            summary.other += 1
        if f.placeholder:
            summary.placeholders += 1

    diff = walker.diff_catalog(disk, repo.all_files(conn))
    summary.new = len(diff.new)
    summary.unchanged = len(diff.unchanged)
    summary.changed = len(diff.changed)
    summary.missing = len(diff.missing)
    now = datetime.now().isoformat(timespec="seconds")

    repo.touch_files(conn, [row.id for _, row in diff.unchanged], now)
    repo.mark_missing(conn, [row.id for row in diff.missing])
    # spec 5.1 rev 6 supersede, trigger 1 of 2: file changes. This goes in the SAME
    # transaction as the writes that justify it, because _scan has four commits and
    # the spec says "before the scan commits" - singular. Recording new/changed/
    # missing durably while the supersede waits ~90 lines for the last commit leaves
    # a window where a crash converges the archive to a permanently wrong steady
    # state: on the next scan every one of those files is unchanged and complete
    # (walker.diff_catalog), so this trigger is False forever after and the stale
    # plan survives. Superseding BEFORE the upserts is deliberate: a plan superseded
    # by a scan that then crashed costs a re-plan; a stale plan that survives is the
    # failure this whole mechanism exists to prevent.
    files_changed = bool(summary.new or summary.changed or summary.missing)
    if files_changed:
        _log_supersede(logger, repo.supersede_open_plans(conn), "files_changed")
    conn.commit()

    to_process = diff.new + [f for f, _ in diff.changed]
    ids: dict[str, int] = {}
    for f in to_process:
        ids[f.rel_path] = _upsert(conn, f, now, placeholder=f.placeholder)
    for f, _row in diff.changed:
        # Clear the stale hash first: a crash mid-rehash must never leave an
        # old sha256 describing new bytes.
        repo.set_sha256(conn, ids[f.rel_path], None)
    conn.commit()

    to_read = [
        f for f in to_process if f.media_type in walker.READ_MEDIA_TYPES and not f.placeholder
    ]
    summary.read = len(to_read)
    # C5-4 as amended 2026-11-13 (review R1's F1, vision S3): a stop ends the READING, never the
    # scan. Grouping needs only the walked paths, and every walked file's row was written above,
    # before any was read, so the grouping below runs after a stop too and an unread file groups
    # with its stem-mates. Returning before it left a Live Photo pair ungrouped, and `pam plan`
    # then planned its members as two units. `stopped_at` is the files done at the stop, or None.
    stopped_at = (
        _read(conn, root, command, logger, to_read, ids, now, summary, progress, should_stop)
        if to_read
        else None
    )

    companion_paths = [
        f.rel_path for f in disk if f.media_type in companions.GROUPED_MEDIA_TYPES
    ]
    current = repo.all_files(conn)
    # spec 5.1 rev 6: read the STORED grouping before clearing it, so the grouping
    # this scan computed can be compared against it below.
    stored_grouping = repo.companion_membership(conn)

    # Decide every group first, then write. The two loops used to be one; splitting
    # them is what makes the membership comparison possible - the grouping about to
    # be stored has to exist as a value before clear_companion_groups destroys the
    # old one. Deciding is `companions.decide_groups`, the ONE definition `pam plan`'s
    # Z2 check calls too (M14 Task 5h).
    fresh = companions.decide_groups(
        companion_paths, current, config.sidecar_exts, config.ignore_stem_suffixes
    )
    fresh_grouping = {(g.kind, frozenset(g.member_rel_paths)) for g in fresh}

    repo.clear_companion_groups(conn)
    for g in fresh:
        member_ids = [current[m].id for m in g.member_rel_paths]
        repo.create_companion_group(conn, current[g.primary_rel_path].id, g.kind, member_ids)

    # spec 5.1/6.3.5 rev 6 supersede, trigger 2 of 2: grouping changes. An open plan
    # is a statement about a catalog state, so a scan that alters that state
    # invalidates the statement - reconciling a stale plan against a moved catalog
    # would be guesswork. This trigger is not hypothetical: grouping depends on
    # scan.sidecar_exts and scan.ignore_stem_suffixes, so editing pam.toml alone
    # re-groups files with no file changing on disk (measured on a real-world
    # library - the open plan would have split a raw_jpeg pair across two stems,
    # vision S3).
    # It stays HERE, and only here, because this is the transaction that carries the
    # group writes above; trigger 1 (files_changed) was made durable ~90 lines
    # earlier alongside its own writes and must not be re-tested in this condition.
    # Compare MEMBERSHIP, never group ids: clear_companion_groups above deletes and
    # recreates every group on every scan. Applied plans are never touched - the
    # repo SQL is an allowlist on status = 'open'.
    grouping_changed = fresh_grouping != stored_grouping
    if grouping_changed:
        _log_supersede(logger, repo.supersede_open_plans(conn), "grouping_changed")
    conn.commit()
    if stopped_at is not None:
        return _stopped(summary, logger, stopped_at, len(to_read))  # never "scan done"
    logger.info(
        "scan done: %d files (%d new, %d changed, %d missing, %d read, %d errors)",
        summary.total,
        summary.new,
        summary.changed,
        summary.missing,
        summary.read,
        len(summary.errors),
    )
    return summary


def _read(
    conn: Connection,
    root: Path,
    command: list[str],
    logger: logging.Logger,
    to_read: list[walker.DiskFile],
    ids: dict[str, int],
    now: str,
    summary: ScanSummary,
    progress: Callable[[int, int], None],
    should_stop: Callable[[], bool] | None,
) -> int | None:
    """Read `to_read`, one commit per file (P1). The files done when `should_stop` ended the read,
    or None when every file was read.

    C5-2: the stop is asked BETWEEN files, after the file's commit and before the next hash, so
    nothing is read after it. Returning leaves the `with ExifTool` block normally; the caller
    then groups (C5-4 as amended) and only then reports the scan `stopped`.
    """
    # design 5.1, M13 (S9, H8; M12 review R1): within a workspace a name pam wrote is not
    # evidence. Read ONCE per scan, after spec 7.2's recovery has resolved every `intent` row.
    # The writer that makes this matter is the operating system: an mtime-only touch (a sync
    # client, a restore) re-reads a renamed file, which would otherwise take pam's own
    # proposal - an OCR or mtime date - back as a `filename` date.
    written = repo.pre_pam_names(conn)
    done = 0
    # A batch carries one frozen arg list (spec 5.3 rev 4), so batches never
    # mix media types: group first, then window each group. Do NOT filter a
    # mixed window instead - a straddling batch would silently drop files.
    read_groups = [
        [f for f in to_read if f.media_type == media_type]
        for media_type in walker.READ_MEDIA_TYPES
    ]
    with ExifTool(command=command) as et:
        for media_type, read_group in zip(walker.READ_MEDIA_TYPES, read_groups, strict=True):
            for start in range(0, len(read_group), _BATCH):
                batch = []
                # Design 5.1/5.2 rev 34 (X1): the walk decided `placeholder` up to
                # minutes ago. Immediately before this batch is read, each file's LIVE
                # attributes are checked; one OneDrive freed since is recorded as the
                # placeholder it now is (sha256 stays NULL) and is not read. The next
                # scan's `diff_catalog` compares the placeholder shape, so it is read
                # once it is local again. The window left - one batch - is accepted.
                for f in read_group[start : start + _BATCH]:
                    if walker.placeholder_now(root / f.rel_path):
                        _upsert(conn, f, now, placeholder=True)
                        conn.commit()
                        summary.placeholders += 1
                        summary.read -= 1
                        logger.info("became cloud-only, not read: %s", f.rel_path)
                        done += 1
                        progress(done, len(to_read))
                        # C5-2: between files - this one's row is committed above.
                        if should_stop is not None and should_stop():
                            return done
                    else:
                        batch.append(f)
                abs_paths = [root / f.rel_path for f in batch]
                tags_by_path = et.extract(abs_paths, media_type=media_type)
                for f, abs_path in zip(batch, abs_paths, strict=True):
                    file_id = ids[f.rel_path]
                    try:
                        repo.set_sha256(conn, file_id, sha256_file(abs_path))
                    except OSError as e:
                        summary.errors.append(f"{f.rel_path}: {e}")
                        logger.error("hash failed: %s: %s", f.rel_path, e)
                    tags = tags_by_path.get(str(abs_path).replace("\\", "/"))
                    if tags is None:
                        summary.errors.append(
                            f"{f.rel_path}: exiftool could not read this file"
                        )
                        logger.error("exiftool failed: %s", f.rel_path)
                    else:
                        _read_exiftools_own_report(summary, logger, f.rel_path, tags)
                    evidence = repo.name_evidence(written.get(file_id), f.rel_path, f.size)
                    repo.upsert_capture(
                        conn,
                        file_id,
                        objective.build_capture(
                            tags,
                            f.filename,
                            f.mtime_ns,
                            f.media_type,
                            evidence_filename=evidence,
                        ),
                    )
                    conn.commit()  # per file: a crash loses at most one file (P1)
                    done += 1
                    progress(done, len(to_read))
                    # C5-2: between files, after this file's commit, before the next hash.
                    if should_stop is not None and should_stop():
                        return done
    return None


def _stopped(
    summary: ScanSummary, logger: logging.Logger, done: int, total: int
) -> ScanSummary:
    """Report a scan whose read `should_stop` ended, AFTER its grouping has committed (C5-4 as
    amended 2026-11-13). Nothing more is read or written here.

    `done` files are behind the per-file commit; `read` drops the `total - done` never reached.
    """
    summary.read -= total - done
    summary.stopped = True
    logger.info("scan stopped: %d of %d read", done, total)
    return summary


def _upsert(conn: Connection, f: walker.DiskFile, now: str, *, placeholder: bool) -> int:
    """`repo.upsert_file` for one walked file, with `placeholder` given by the caller."""
    return repo.upsert_file(
        conn,
        rel_path=f.rel_path,
        filename=f.filename,
        ext=f.ext,
        media_type=f.media_type,
        size=f.size,
        mtime_ns=f.mtime_ns,
        placeholder=placeholder,
        now=now,
    )


def _read_exiftools_own_report(
    summary: ScanSummary,
    logger: logging.Logger,
    rel_path: str,
    tags: dict[str, object],
) -> None:
    """exiftool answered - now read what it said ABOUT the answer (M7 Task 5).

    A RECORD CAME BACK, so this is not the `tags is None` case above: exiftool produced
    output for this path and may have said in it that it could make nothing of the file.
    Until M7 nobody looked, so an empty or unidentifiable file sailed into the catalog,
    fell to `file_mtime` at the lowest trust, and pam said nothing at all - while UX 1.4
    promises the user "3 photos couldn't be read (corrupt files) - they were skipped and
    are listed in the report".

    `Error` -> `summary.errors`, the SAME list the walk errors and the hashing
    `OSError`s already use, so spec 13 rev 6's exit-1, the '!' summary line and the log
    entry all come from the one place that already produces them. `Warning` -> the
    session log at debug level and nowhere else, because a warning is a partial read
    (measured: about half of a real-world library's capture rows carry one, all videos, all dated).
    The reasoning for the split is in `objective.read_error` / `read_warning`.

    NOTHING ELSE ABOUT THE FILE CHANGES. It is still inventoried, still hashed, and its
    capture row is still written by the caller two lines below - "skipped" is a
    classification `pam report skipped` applies, not a status in the catalog.
    """
    error = objective.read_error(tags)
    if error is not None:
        summary.errors.append(f"{rel_path}: {error}")
        logger.error("exiftool error: %s: %s", rel_path, error)
    warning = objective.read_warning(tags)
    if warning is not None:
        # Not `elif`: a record can carry both, and the log is where a reader goes to
        # find out what exiftool actually saw. Debug, so the console never shows it.
        logger.debug("exiftool warning: %s: %s", rel_path, warning)


def _log_supersede(logger: logging.Logger, superseded: int, trigger: str) -> None:
    """Session log only (never console): which trigger fired, and how many plans."""
    if superseded:
        logger.info("superseded %d open plan(s): trigger=%s", superseded, trigger)

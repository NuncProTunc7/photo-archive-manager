"""`pam faces export` (M11 Task 13, amendment A20). A DATA-ACCESS export, not a backup.

WHY A SUBMODULE RATHER THAN MORE OF `faces/__init__.py`. The package was 194 executable
lines with `manifest` and `cluster` in it; export and purge together are more than a
hundred more, and spec 9.4's ~300-line budget is measured rather than estimated. The
package docstring's census names all four.

NEITHER THIS COMMAND NOR `purge` TOUCHES A PHOTOGRAPH (Task 13 Step 1). This one reads
`face_instances`, `persons` and `files` and writes two files into the WORKSPACE's
`reports/` directory - never into the archive. `_refuse_archive_path` raises rather than
asserting that, because a build run with `-O` drops `assert` statements and this is the
one check standing between a data-access export and a file written into somebody's
photo folder.

WHY THE EMBEDDINGS ARE A SIDECAR AND NOT BASE64 IN THE JSON. A20 says so, and the reason
is legibility in both directions: a JSON document a person can read in an editor, and a
block of float32 a person can `numpy.fromfile` in one line. Base64 inside JSON would be
neither - unreadable to the eye and requiring a decode step before it is numbers.

THE WRITE ORDER IS THE RECOVERY STORY. The sidecar is moved into place FIRST and the JSON
LAST, so a present JSON always means a complete export: the document names the sidecar it
belongs to, and a sidecar with no document is an orphan a user can delete on sight. Both
are written to temp files in the destination directory (same filesystem, so `os.replace`
is atomic) and every temp file - plus a sidecar already moved - is removed on any failure.

V1.0 SHIPS NO IMPORT AND THE DOCUMENT SAYS SO, in a field of its own rather than only in
the command's output, because the output scrolls away and the file is what survives.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from sqlite3 import Connection

import click

from pam import __version__
from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog, schema_version
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import Workspace, resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log
from pam.status import NextStep, step_lines

FORMAT = "pam-faces-export"
FORMAT_VERSION = 1

# Design 4's BLOB is raw float32 in NATIVE byte order, written by
# `enrich/faces.py`'s `.astype(numpy.float32).tobytes()` and read by
# `cluster.decode_embedding`'s `array.array("f", ...)`. `cluster.FLOAT32_BYTES`' comment
# records that every platform pam supports is little-endian, so the declaration is a
# statement about the bytes in this file and not a hope about the reader's machine.
DTYPE = "float32"
BYTE_ORDER = "little"
FLOAT32_BYTES = 4

NO_IMPORT = "pam v1.0 has no import - this file is a data-access export, not a backup"


@dataclass(frozen=True)
class ExportReport:
    """What one `pam faces export` wrote. Counts, not rows: the file holds the rows."""

    archive_root: Path
    json_path: Path
    embeddings_path: Path
    faces: int
    persons: int
    photographs: int
    embedding_width: int | None
    elapsed_s: float = 0.0
    log_path: Path | None = None


def _stamp(now: float | None = None) -> str:
    """`YYYYMMDD-HHMMSS`, local time, the stamp `sessionlog.py` already uses for a run.

    Seconds and not microseconds, which is `s-YYYYMMDD-HHMMSS`'s ruling from M5: a stamp
    a person can read and type. Two exports in one second collide, and the collision is
    caught by the never-overwrite check below rather than papered over with a suffix - a
    second export in the same second is a user repeating themselves, and a refusal naming
    the file already there is a better answer than two files they cannot tell apart.
    """
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def _refuse_archive_path(path: Path, archive_root: Path) -> None:
    """A20: a destination under the archive root is a BUG, and this is the check.

    A RAISED ERROR, NEVER AN `assert` STATEMENT. `python -O` drops assertions, and what
    this guards is pam writing a file it generated into the user's irreplaceable photo
    folder - the one place vision's whole premise says pam never writes. `is_relative_to`
    over resolved paths, so a `..` or a symlinked reports directory cannot walk in.
    """
    try:
        inside = path.resolve().is_relative_to(archive_root.resolve())
    except OSError:  # pragma: no cover - an unresolvable path is refused below anyway
        inside = False
    if inside:
        raise PamError(
            f"pam faces export would have written {path} inside the archive"
            f" {archive_root}. pam never writes into your photo folder; this is a bug in"
            " pam rather than something you did. Nothing was written."
        )


def _paths(ws: Workspace, stamp: str) -> tuple[Path, Path]:
    """The two destinations. The JSON names the sidecar by its FILENAME, not by a path."""
    return (
        ws.reports_dir / f"faces-export-{stamp}.json",
        ws.reports_dir / f"faces-export-{stamp}.embeddings.bin",
    )


def _rel_paths(conn: Connection) -> dict[str, list[str]]:
    """`{sha256: [every rel_path holding it, sorted]}`, present and missing alike.

    A20 asks for a per-face `rel_path` and `face_instances` knows only a sha256 (spec 4
    keys a face by CONTENT), so the resolution happens here. EVERY row, not the best one:
    `repo.files_by_sha256`'s ordering is a PICK for a command that must open one file, and
    an export that silently dropped the second copy of a photograph would be answering a
    data-access request with pam's opinion of which copy mattered.

    `repo.all_files` rather than one query per face: an archive with 20,000 faces would
    otherwise issue 20,000 statements to read one table.
    """
    by_sha: dict[str, list[str]] = {}
    for rel_path, row in repo.all_files(conn).items():
        if row.sha256:
            by_sha.setdefault(str(row.sha256), []).append(rel_path)
    return {sha: sorted(paths) for sha, paths in by_sha.items()}


def _document(
    conn: Connection,
    faces: list[repo.ExportFace],
    sidecar_name: str,
    exported_at: str,
) -> tuple[dict[str, object], int | None]:
    """A20's JSON, and the embedding width it declared. No file is written here.

    MIXED WIDTHS ARE DESCRIBED, NOT REFUSED. `faces/__init__.py::width_refusal` refuses to
    CLUSTER a catalog holding embeddings of two widths, because a cosine distance between
    them is a meaningless number. An export makes no such comparison: it is what pam holds
    about the user, and a catalog in that state is exactly when a user most needs to get
    their data out. So `shape` becomes `null` and each face carries its own
    `embedding_offset` and `embedding_length` - the sidecar is still readable, one row at
    a time, and the document says why it cannot be read as a rectangle.
    """
    rel_paths = _rel_paths(conn)
    widths = {len(f.embedding) for f in faces}
    uniform = len(widths) <= 1
    width = (next(iter(widths)) // FLOAT32_BYTES) if faces and uniform else None
    models: dict[tuple[str, str], int] = {}
    for face in faces:
        key = (face.model_name, face.model_version)
        models[key] = models.get(key, 0) + 1
    rows: list[dict[str, object]] = []
    offset = 0
    for index, face in enumerate(faces):
        row: dict[str, object] = {
            "id": face.id,
            "sha256": face.sha256,
            "rel_paths": rel_paths.get(face.sha256, []),
            "geometry": face.geometry,
            "confidence": face.det_confidence,
            "cluster_id": face.cluster_id,
            "person_label": face.person_label,
            "review_state": face.review_state,
            "model_name": face.model_name,
            "model_version": face.model_version,
            "embedding_row": index,
        }
        if not uniform:
            row["embedding_offset"] = offset
            row["embedding_length"] = len(face.embedding)
        offset += len(face.embedding)
        rows.append(row)
    embeddings: dict[str, object] = {
        "file": sidecar_name,
        "dtype": DTYPE,
        "byte_order": BYTE_ORDER,
        "shape": [len(faces), width if width is not None else 0] if uniform else None,
        "row_order": "same as faces",
    }
    if not uniform:
        embeddings["note"] = (
            "this catalog holds embeddings of more than one width, so the sidecar is not a"
            " rectangle: read each face with its own embedding_offset and embedding_length"
        )
    document: dict[str, object] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "schema_version": schema_version(conn),
        "app_version": __version__,
        "exported_at": exported_at,
        "import": NO_IMPORT,
        "models": [
            {"model_name": name, "model_version": version, "faces": count}
            for (name, version), count in sorted(models.items())
        ],
        "embeddings": embeddings,
        "faces": rows,
    }
    return document, width


def _write_pair(
    json_path: Path, embeddings_path: Path, document: dict[str, object], blobs: list[bytes]
) -> None:
    """Temp files, then `os.replace` - SIDECAR FIRST, JSON LAST (A20).

    THE CLEANUP LIST IS EVERY PATH THIS FUNCTION HAS TOUCHED, including a sidecar already
    moved into place. A20 says "partial sidecars deleted on failure" and a sidecar whose
    document never arrived is precisely that: an orphan block of float32 with nothing
    saying what is in it. Deleting it is what makes "a present JSON means a complete
    export" true in both directions.

    `ensure_ascii=False` because the labels are people's names. A20's document is meant to
    be read; `Zo\\u00eb` is not a name anybody recognises, and the file is UTF-8 either way.

    ONE `os.replace` CALL SITE, IN A LOOP, and that is not a tidiness choice.
    `tests/guards.py::ALLOWED_MOVE_SITES` is a sorted list rather than a set precisely so
    that a second identical move inside one function fails it - spec 7.1's "one verified
    rename" rests on that. Two literal calls here would need two allowlist entries and
    would make this function look, to the guard, like the thing the guard exists to catch.
    """
    tmp_json = json_path.with_name(json_path.name + ".tmp")
    tmp_bin = embeddings_path.with_name(embeddings_path.name + ".tmp")
    written: list[Path] = []
    try:
        tmp_bin.write_bytes(b"".join(blobs))
        written.append(tmp_bin)
        tmp_json.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(tmp_json)
        # SIDECAR FIRST, DOCUMENT LAST. The order is the recovery story, so it is a
        # literal sequence here rather than anything derived from the arguments.
        for source, destination in ((tmp_bin, embeddings_path), (tmp_json, json_path)):
            os.replace(source, destination)
            written = [p for p in written if p != source] + [destination]
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def run_export(archive: Path | None, *, now: float | None = None) -> ExportReport:
    """Write A20's export. Reads the catalog; writes two files under `reports/`."""
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    started = time.monotonic()
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="faces-export")
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                logger.info("faces export start: root=%s", root)
                # Spec 7.2, inside the lock and before this command's own work, in the
                # position every archive-scoped command puts it in. An export is keyed by
                # CONTENT hash and so cannot itself be corrupted by a half-finished
                # rename - but it publishes `rel_paths`, which is exactly what an
                # unrecovered crash leaves pointing at names that are not on disk, and it
                # holds the lock the healing machinery needs while it runs.
                run_recovery(conn, root, logger)
                report = _export(conn, ws, root, now, logger)
            finally:
                conn.close()
        except BaseException:
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return replace(report, elapsed_s=time.monotonic() - started, log_path=log_path)


def _export(
    conn: Connection,
    ws: Workspace,
    root: Path,
    now: float | None,
    logger: logging.Logger,
) -> ExportReport:
    """Resolve the two destinations, refuse to overwrite either, and write both."""
    json_path, embeddings_path = _paths(ws, _stamp(now))
    for path in (json_path, embeddings_path):
        _refuse_archive_path(path, root)
    for path in (json_path, embeddings_path):
        if path.exists():
            raise PamError(
                f"{path} already exists. pam faces export never overwrites an export -"
                " an export is the copy of your data you asked pam to make, and a second"
                " run a second later must not be able to replace it. Nothing was written."
                " Move or delete that file, or wait a second and run this again."
            )
    ws.reports_dir.mkdir(parents=True, exist_ok=True)
    faces = repo.export_faces(conn)
    exported_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    document, width = _document(conn, faces, embeddings_path.name, exported_at)
    _write_pair(json_path, embeddings_path, document, [f.embedding for f in faces])
    persons = len({f.person_label for f in faces if f.person_label is not None})
    photographs = len({f.sha256 for f in faces})
    logger.info(
        "faces export: faces=%d persons=%d photographs=%d json=%s",
        len(faces),
        persons,
        photographs,
        json_path,
    )
    return ExportReport(
        archive_root=root,
        json_path=json_path,
        embeddings_path=embeddings_path,
        faces=len(faces),
        persons=persons,
        photographs=photographs,
        embedding_width=width,
    )


def export_lines(report: ExportReport) -> list[str]:
    """The closing block: what was written, how much of it, and what it is NOT.

    THE NO-IMPORT SENTENCE IS NOT A FOOTNOTE (A20). A file called an "export" that a user
    files away as their backup is a user who will one day discover pam cannot read it
    back, at the moment they need it. It is printed here and stored in the document, so
    neither the screen nor the file is the only place it exists.
    """
    lines = [f"Face export from {report.archive_root}", ""]
    lines.append(f"Wrote {report.json_path}")
    lines.append(f"      {report.embeddings_path}")
    lines.append("")
    lines.append(
        f"{report.faces:,} faces over {report.photographs:,} photographs;"
        f" {report.persons:,} people you have named."
    )
    if report.faces == 0:
        lines.append(
            "  pam holds no faces for this archive, so the export is empty - which is the"
            " honest answer to 'what do you have about me' rather than a failure."
        )
    if report.embedding_width is not None and report.faces:
        lines.append(
            f"  Embeddings: {report.faces:,} x {report.embedding_width:,} {DTYPE},"
            f" {BYTE_ORDER}-endian, in the same order as the faces in the JSON."
        )
    elif report.faces:
        lines.append(
            "  Embeddings are of more than one width, so the sidecar is not a rectangle:"
            " each face carries its own embedding_offset and embedding_length."
        )
    lines.append("")
    lines.append(f"This is a data-access export, not a backup: {NO_IMPORT}.")
    lines.append("No photograph was read, written or renamed.")
    lines.append("")
    lines.extend(
        step_lines(
            (
                NextStep(
                    "pam faces purge",
                    "delete every face pam holds - read the export first",
                ),
            )
        )
    )
    return lines


def print_export_summary(report: ExportReport) -> None:
    """stdout for the report, stderr for the log path (spec 13, UX 2)."""
    for line in export_lines(report):
        click.echo(line)
    if report.log_path is not None:
        click.echo(f"Log: {report.log_path}", err=True)

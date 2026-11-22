"""The `pam faces` command family. Four members after M11 Task 13.

THE CENSUS, so nobody has to go looking: `manifest` and `cluster` are in this module;
`export` (A20) is in `export.py` and `purge` (A19) is in `purge.py`; `label` is a web
page and lives in `pam/webui/`. M13 Task 14 adds a sixth, `calibrate` (H7), in
`calibrate.py`: a lock-free reader that sweeps `cluster_epsilon`. Task 13's two are submodules rather than more of this
file because together they are more than a hundred executable lines and this module was
already at 194 against spec 9.4's ~300 budget - measured, not estimated. They are one
import away and are wired from `cli.py` exactly as `cluster` is.


WHY A PACKAGE FOR ONE COMMAND. `pam faces cluster`, `pam faces export` and
`pam faces purge` land in Tasks 9 and 13, and every one of them is archive-scoped work
with a report of its own - the shape `pam/report.py` and `pam/verify.py` already have.
Putting the first one in `cli.py` would mean moving it later, and `cli.py` is spec 12's
"argument parsing and exit-code mapping only".

`manifest` is the exception to that archive-scoping and deliberately so (A22): a model
pack is a folder of files on disk and has nothing to do with any catalog, which is the
same reason `pam doctor` does not validate one.

`cluster` (Task 9) is the archive-scoped one the paragraph above predicted. It reads
`face_instances`, groups unnamed detections with `pam/enrich/cluster.py`'s DBSCAN and
writes `cluster_id`. It opens no photograph, loads no model and changes no filename -
design 8.1 puts clustering in a command of its own precisely because it is corpus-wide
rather than per-file, and the arithmetic it needs is over vectors the enricher already
stored.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from sqlite3 import Connection

import click

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.config import Config, load_config
from pam.enrich import cluster, faces_model
from pam.enrich.cluster import ScaleEstimate
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log
from pam.status import NextStep, step_lines


def run_manifest(directory: Path) -> Path:
    """Write `pack.toml` for the pack in `directory`. Raises `FacesPackError` (exit 1)."""
    return faces_model.write_manifest(directory)


def print_manifest(target: Path) -> None:
    """UX 2: say what was written, and what it is now safe to do next."""
    click.echo(f"Wrote {target}")
    ok, sentence = faces_model.describe(target.parent)
    click.echo(f"  {'ok' if ok else 'x'} {sentence}")
    if ok:
        click.echo(
            f"\nNext: set [enrich.faces] model_dir = {str(target.parent)!r} in pam.toml,"
            " then run: pam enrich faces"
        )


@dataclass(frozen=True)
class ClusterReport:
    """What one `pam faces cluster` run did. Nothing here is computed twice.

    `singletons` is design clarification 1's number and is reported separately from the
    cluster count because it answers a different question: not "how many people did pam
    find" but "how many faces will I never be asked about". A face seen once is noise
    under `min_samples = 2` and is never offered for labeling, and a user who is not told
    that will go looking for it on the labeling page.
    """

    archive_root: Path
    epsilon: float
    floor: float
    scale: ScaleEstimate
    considered: int
    clusters: int
    singletons: int
    already_named: int
    elapsed_s: float = 0.0
    log_path: Path | None = None


def epsilon_refusal(config: Config) -> str | None:
    """A12: with no measured epsilon there is no clustering, and the key is NAMED.

    THE VALUE IS NOT NORMATIVE UNTIL THE EVIDENCE EXISTS, which is design 11 rev 22's own
    sentence. The `0.35` this project carried from rev 2 to rev 21 was never measured for
    any embedding stack, and M11's spike measured only the UPPER anchor - three different
    real people in one photograph at cosine 0.4678 / 0.7188 / 0.6543. The lower bound
    needs SAME-PERSON pairs across photographs; those need the maintainer's labels; and the
    page that collects them is Task 10, which does not exist yet. Guessing the number
    instead would merge two people into one name in a filename, which vision R6 calls the
    most personally embarrassing failure there is.

    The sentence names the key and, since M13 (H7), the command that measures a value on
    the user's own named faces (`pam faces calibrate`). It deliberately does NOT suggest a
    value itself: a
    "try 0.4" inside a refusal is the guess this refusal exists to prevent, arriving in
    the voice of the product rather than the voice of the user.
    """
    if config.faces_cluster_epsilon is not None:
        return None
    return (
        "pam has no value for [enrich.faces] cluster_epsilon, so it will not cluster."
        " That setting is the cosine distance below which two faces are treated as the"
        " same person. It is a property of the face model rather than of pam, and no"
        " measured value ships: a guessed one puts two different people under a single"
        " name in a filename. Set [enrich.faces] cluster_epsilon in pam.toml to a value"
        " measured on your own photographs, then run this command again."
        " Run pam faces calibrate to see which value works for your photos."
    )


def generation_refusal(conn: Connection) -> str | None:
    """A6, clustering's half: one archive, one face-model generation.

    `pam enrich faces` refuses to ADD a second generation (`enrich/sink.py`'s
    `generation_refusal`); this refuses to CLUSTER a catalog that already holds two, which
    is a different moment and a different query - the enricher compares the catalog against
    the installed pack, and clustering has no pack to compare against. Cosine distances
    BETWEEN two embedding spaces are meaningless numbers, and meaningless numbers cluster
    into confident, wrong people rather than into an error anybody can see.
    """
    generations = repo.model_generations(conn)
    if len(generations) < 2:
        return None
    held = "; ".join(
        f"{g.model_name} {g.model_version} ({g.faces:,} faces)" for g in generations
    )
    return (
        f"this archive holds faces from {len(generations)} different model generations:"
        f" {held}. Distances between two embedding spaces are meaningless, so clustering"
        " them would group people at random. Run: pam faces export (to keep the names you"
        " have already given), then pam faces purge, then pam enrich faces to detect again"
        " with one model."
    )


def width_refusal(
    faces: Sequence[repo.FaceRow] | Sequence[repo.CalibrationFace],
) -> str | None:
    """A6's second half, which is about the ROWS and not about their labels.

    "`pam faces cluster` refuses the same state and ADDITIONALLY refuses embeddings of
    differing width." Two generations that happen to share a `model_version` string - a
    hand-edited catalog, an import, a digest collision - pass the check above and still
    hold 128-value and 512-value vectors that cannot be compared at all. This is measured
    off `len(embedding)`, which needs no decode and cannot be fooled by metadata.
    `pam faces calibrate` (M13 Task 14) asks the same question of its own rows.
    """
    widths = sorted({len(face.embedding) // cluster.FLOAT32_BYTES for face in faces})
    if len(widths) < 2:
        return None
    listed = ", ".join(f"{width:,} values" for width in widths)
    return (
        f"this archive holds face embeddings of {len(widths)} different widths ({listed}),"
        " which cannot be compared with each other. Run: pam faces export, then pam faces"
        " purge, then pam enrich faces to detect again with one model."
    )


def run_cluster(archive: Path | None, *, announce: Callable[[str], None]) -> ClusterReport:
    """Group unnamed faces into clusters. Touches no photograph and no filename.

    `announce` is not a logging seam and is not optional: A21 requires the face count, the
    estimated time and memory and the tested ceiling to be published BEFORE the work
    starts, and a summary printed afterwards cannot do that. `cli.py` passes `click.echo`;
    a test passes a list's `append` and reads what the user would have seen.
    """
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    config = load_config(ws.config_path)
    # BEFORE THE LOCK AND BEFORE THE CATALOG. A refusal that depends only on pam.toml has
    # no business taking a workspace lock, opening a database or running recovery first -
    # and putting it first is also what makes it the sentence a user with no epsilon
    # actually meets, rather than one about model generations.
    unset = epsilon_refusal(config)
    if unset:
        raise PamError(unset)
    epsilon = config.faces_cluster_epsilon
    assert epsilon is not None  # narrowed by `epsilon_refusal`; mypy cannot see through it
    floor = config.tiers["faces"].floor
    started = time.monotonic()
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="faces-cluster")
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                logger.info(
                    "faces cluster start: root=%s epsilon=%s floor=%s", root, epsilon, floor
                )
                # Spec 7.2, inside the lock and before this command's own work, in the
                # position every other archive-scoped command puts it in. Clustering is
                # keyed by CONTENT hash and cannot itself be corrupted by a half-finished
                # rename - but it holds the workspace lock while it runs, and a command
                # that holds the lock and leaves a crash unhealed is exactly the argument
                # `pam verify` lost: the machinery that heals it then sits one command
                # away from a user who has just been told everything is fine.
                run_recovery(conn, root, logger)
                report = _cluster(conn, root, epsilon, floor, announce, logger)
            finally:
                conn.close()
        except BaseException:
            # Spec 13's log path is unconditional and `cli.py` prints no traceback. One
            # implementation, in `execute/reporter.py`; the empty session id is the truth,
            # because a clustering pass has a log and not an undoable session.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return replace(report, elapsed_s=time.monotonic() - started, log_path=log_path)


def _cluster(
    conn: Connection,
    root: Path,
    epsilon: float,
    floor: float,
    announce: Callable[[str], None],
    logger: logging.Logger,
) -> ClusterReport:
    """A6's two refusals, A21's preamble and ceiling, the pass, and ONE transaction."""
    mixed = generation_refusal(conn)
    if mixed:
        raise PamError(mixed)
    faces = repo.faces_for_clustering(conn, floor)
    ragged = width_refusal(faces)
    if ragged:
        raise PamError(ragged)
    dim = len(faces[0].embedding) // cluster.FLOAT32_BYTES if faces else cluster.DEFAULT_DIM
    scale = cluster.estimate(len(faces), dim)
    too_big = cluster.refusal(scale)
    if too_big:
        raise PamError(too_big)
    for line in cluster.lines(scale):
        announce(line)
    labels = cluster.dbscan(
        {face.id: cluster.decode_embedding(face.embedding) for face in faces},
        epsilon=epsilon,
        min_samples=cluster.MIN_SAMPLES,
    )
    # COLLISION-FREE ALLOCATION, and it is not an optimisation. `dbscan` numbers from 1;
    # the faces this pass did not look at keep ids an earlier pass gave them, numbered
    # from 1 as well. See `repo.retained_cluster_ids` for what happens without the offset:
    # a person the user has named ends up sharing a cluster id with a group of strangers,
    # which is vision R6's failure arriving with no user action at all.
    offset = max(repo.retained_cluster_ids(conn, floor), default=0)
    assignments: list[tuple[int, int | None]] = [
        (face_id, None if number is None else number + offset)
        for face_id, number in labels.items()
    ]
    # ONE TRANSACTION. `set_cluster` issues a single `executemany` and does not commit;
    # the commit is here, so an interruption anywhere in this function leaves every
    # cluster id exactly as it was. A renumbering applied halfway is a catalog in which
    # some faces are numbered by this pass and some by the last one, and nothing
    # downstream could tell which.
    repo.set_cluster(conn, assignments)
    conn.commit()
    formed = {number for number in labels.values() if number is not None}
    singletons = sum(1 for number in labels.values() if number is None)
    named = sum(person.faces for person in repo.persons_all(conn))
    logger.info(
        "faces cluster: considered=%d clusters=%d singletons=%d named=%d offset=%d",
        len(faces),
        len(formed),
        singletons,
        named,
        offset,
    )
    return ClusterReport(
        archive_root=root,
        epsilon=epsilon,
        floor=floor,
        scale=scale,
        considered=len(faces),
        clusters=len(formed),
        singletons=singletons,
        already_named=named,
    )


def cluster_lines(report: ClusterReport) -> list[str]:
    """The closing block: faces considered, groups formed, singletons, and the untouched.

    THE SINGLETON SENTENCE IS DESIGN CLARIFICATION 1'S, and it says WHY rather than only
    how many: "a face seen once is noise and is never offered for labeling - `pam faces
    cluster` says so rather than leaving a user to infer it". A user who reads "402 faces
    considered, 31 groups formed" and nothing else will go hunting on the labeling page
    for the ones that are not there.

    THE "LEFT ALONE" SENTENCE IS I3'S. A pass that re-decided a name a human had already
    given would not be a correction anybody could rely on, so named faces are excluded -
    and being excluded silently is what makes a user think their names were lost.
    """
    lines = [f"Face clustering in {report.archive_root}", ""]
    lines.append(
        f"{report.considered:,} faces considered"
        f" (unnamed, not hidden, confidence at or above {report.floor:g}):"
        f" {report.clusters:,} groups formed."
    )
    if report.singletons:
        lines.append(
            f"  {report.singletons:,} of them appear only once and were left ungrouped;"
            " pam does not offer a face it has seen once for naming."
        )
    if report.already_named:
        lines.append(
            f"  {report.already_named:,} faces you have already named were left alone."
        )
    lines.append("")
    lines.append(f"Done in {report.elapsed_s:.1f}s.")
    lines.append("")
    lines.extend(
        step_lines(
            (NextStep("pam faces label", "name the groups pam found - renames nothing"),)
        )
    )
    return lines


def print_cluster_summary(report: ClusterReport) -> None:
    """stdout for the report, stderr for the log path (spec 13, UX 2)."""
    for line in cluster_lines(report):
        click.echo(line)
    if report.log_path is not None:
        click.echo(f"Log: {report.log_path}", err=True)

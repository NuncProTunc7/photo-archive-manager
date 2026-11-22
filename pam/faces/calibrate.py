"""`pam faces calibrate` (M13 Task 14, C9, amendment H7): which `cluster_epsilon` suits YOUR faces.

WHAT IT IS FOR. Design 11 leaves `[enrich.faces] cluster_epsilon` unset and `pam faces cluster`
refuses to run without it, because a guessed value puts two different people under one name in a
filename (vision R6). Design 11 also prescribes how the value is chosen: "from same-person and
different-person distance distributions over the maintainer's own labels, choosing the value with the
fewest CROSS-PERSON MERGES". M12 built that method as a maintainer's tool over a `pam faces export`
(`tools/epsilon_sweep.py`, P4); this command is the same method offered to the user over their
own catalog, and the tool is gone - every option it had is here.

IT NEVER WRITES `pam.toml`, AND IT WRITES NOTHING ELSE EITHER. It prints a table and the line a
user may add themselves. Choosing the value stays the user's act: a command that wrote it would
be pam choosing, on the evidence of names the user may not have finished checking. It is a
LOCK-FREE READER with `pam status`'s shape - a read-only connection, every read inside one
snapshot (S13), no workspace lock, no recovery, no session log - so it answers while another
command holds the lock, and cannot heal or change anything while it does.

THE GROUND TRUTH MUST BE CLEAN, AND THE COMMAND MAKES THE USER SAY HOW (the M12 gate's I4). A face
is left out for the FIRST of these reasons that applies, in this order:

  1. its confidence is below the floor (`--floor`, else `[tiers] faces.floor`; inclusive, as
     `repo.faces_for_clustering` reads it);
  2. its `review_state` is set (a person already ruled on it - "not a face", and so on);
  3. its label is one of `--exclude-label` (a person known not to be one identity);
  4. one of its paths matches an `--exclude-path` glob (`fnmatch`, case-sensitive, `*` crosses
     `/`).

What was left out is printed FIRST, by reason, so a sweep over the wrong ground truth says so.

WHAT IT PRINTS, per design 11's method: the pack identity; named faces per person; the CLOSEST
CROSS-PERSON PAIR (below that cosine distance no epsilon can merge two named people DIRECTLY);
and for epsilon 0.250 to 0.500 in steps of 0.025, running `pam.enrich.cluster.dbscan` ITSELF
(never a re-implementation) with `cluster.MIN_SAMPLES` over EVERY remaining face, named or not:

  groups       clusters (DBSCAN's non-noise labels);
  singletons   faces left alone (DBSCAN's noise);
  merges       groups holding two or more names;
  indirect     of those, merges that exist ONLY THROUGH UNNAMED FACES - re-clustering the same
               faces at the same epsilon with every unnamed face removed, no resulting group
               holds two of that merge's names;
  split        persons whose named faces lie in two or more groups;
  fragmented   named faces left as singletons, total, and per person beneath the row.

Then ONE sentence: the largest value with no merged people and the line to add, or why there is
none. "Largest" is read along the table from the smallest value and stops at the first row with a
merge: a value above a merging one is never offered, even if the rows after it came back clean.

THE COUNTING AND THE EXCLUSION ARE PURE FUNCTIONS OVER PLAIN MAPPINGS (`exclude`, `count`,
`closest_cross_pair`, `largest_clean`), so a test reaches them with a partition written by hand;
only `sweep` calls `dbscan`, which imports numpy itself, INSIDE the function - importing this
module costs `pam --version` nothing.
"""

import fnmatch
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import open_catalog_readonly, read_snapshot
from pam.config import load_config
from pam.enrich import cluster, faces_model
from pam.errors import PamError
from pam.faces import generation_refusal, width_refusal
from pam.paths import resolve_archive, workspace_if_exists

# P4: "for epsilon from 0.25 to 0.50 in steps of 0.025". Eleven values, rounded so that
# 0.25 + 3 * 0.025 prints and compares as 0.325 rather than 0.32500000000000007. ASCENDING, and
# `largest_clean` depends on it.
EPSILONS = tuple(round(0.25 + step * 0.025, 3) for step in range(11))

# The four exclusion reasons, in the order they are TESTED and PRINTED. A face that meets two
# is counted under the first, so the four counts add up to the number excluded.
BELOW_FLOOR = "below --floor"
REVIEWED = "review_state set"
EXCLUDED_LABEL = "--exclude-label"
EXCLUDED_PATH = "--exclude-path"
REASONS = (BELOW_FLOOR, REVIEWED, EXCLUDED_LABEL, EXCLUDED_PATH)

# H7's sentences. The first two are the plan's (M13 Task 14 Step 2), verbatim.
RECOMMENDATION = (
    "The largest value with no merged people is {e}. To use it, add  cluster_epsilon = {e}"
    "  under [enrich.faces] in {path}."
)
FEWER_THAN_TWO = (
    "pam needs at least two people you have named, with several photos each, to suggest a"
    " value. Name some faces first (pam faces label), then run this again."
)
NONE_CLEAN = (
    "Every value pam tried, from {low} to {high}, put two people you have named in one group,"
    " so pam has no value to suggest. Check the names listed under each row"
    " (pam faces label), then run this again."
)


@dataclass(frozen=True)
class Face:
    """One detection, reduced to what the sweep reads."""

    id: int
    confidence: float
    label: str | None
    review_state: str | None
    rel_paths: tuple[str, ...]


@dataclass(frozen=True)
class CrossPair:
    """The closest pair of named faces that carry DIFFERENT names."""

    distance: float
    label_a: str
    label_b: str
    face_a: int
    face_b: int


@dataclass(frozen=True)
class Merge:
    """One group holding two or more names."""

    group: int
    labels: tuple[str, ...]
    size: int
    indirect: bool


@dataclass(frozen=True)
class Counts:
    """One epsilon's row. `fragmented` maps a person to their faces left as singletons."""

    groups: int
    singletons: int
    merges: tuple[Merge, ...]
    split: dict[str, int]
    fragmented: dict[str, int]

    @property
    def indirect(self) -> int:
        return sum(1 for m in self.merges if m.indirect)


@dataclass(frozen=True)
class Calibration:
    """What one `pam faces calibrate` printed. Nothing was written to produce it."""

    archive_root: Path
    config_path: Path
    lines: tuple[str, ...]


def exclude(
    faces: Sequence[Face],
    *,
    floor: float,
    labels: Sequence[str] = (),
    paths: Sequence[str] = (),
) -> tuple[list[Face], dict[str, list[int]]]:
    """The faces kept, and every excluded face's id under the FIRST reason it met.

    Every reason is a key of the returned map, empty or not, so a printout can say "0".
    """
    excluded: dict[str, list[int]] = {reason: [] for reason in REASONS}
    kept: list[Face] = []
    for face in faces:
        if face.confidence < floor:
            excluded[BELOW_FLOOR].append(face.id)
        elif face.review_state is not None:
            excluded[REVIEWED].append(face.id)
        elif face.label is not None and face.label in labels:
            excluded[EXCLUDED_LABEL].append(face.id)
        elif any(fnmatch.fnmatchcase(p, glob) for p in face.rel_paths for glob in paths):
            excluded[EXCLUDED_PATH].append(face.id)
        else:
            kept.append(face)
    return kept, excluded


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in vector))
    return tuple(x / norm for x in vector) if norm > 0 else tuple(0.0 for _ in vector)


def closest_cross_pair(
    vectors: Mapping[int, Sequence[float]], labels: Mapping[int, str | None]
) -> CrossPair | None:
    """The smallest cosine distance between two named faces of DIFFERENT persons.

    Cosine distance is `1 - dot` over unit vectors - `cluster.dbscan`'s own definition - and
    a tie keeps the pair with the lowest ids. None when fewer than two persons remain.
    """
    labelled = sorted(i for i in vectors if labels.get(i) is not None)
    units = {i: _unit(vectors[i]) for i in labelled}
    best: CrossPair | None = None
    for index, a in enumerate(labelled):
        for b in labelled[index + 1 :]:
            label_a, label_b = str(labels[a]), str(labels[b])
            if label_a == label_b:
                continue
            distance = 1.0 - sum(x * y for x, y in zip(units[a], units[b], strict=True))
            if best is None or distance < best.distance:
                best = CrossPair(distance, label_a, label_b, a, b)
    return best


def count(
    partition: Mapping[int, int | None],
    labelled_partition: Mapping[int, int | None],
    labels: Mapping[int, str | None],
) -> Counts:
    """One epsilon's counts, from two DBSCAN partitions and the names. Pure.

    `partition` clusters every remaining face; `labelled_partition` clusters the NAMED faces
    alone at the same epsilon, and is what tells a direct merge from an indirect one: a merge
    is DIRECT when some group of the named-only run, inside this group, holds two distinct
    names - the persons meet without an unnamed face between them.
    """
    members: dict[int, list[int]] = {}
    for face, group in partition.items():
        if group is not None:
            members.setdefault(group, []).append(face)
    merges: list[Merge] = []
    for group in sorted(members):
        held = sorted({str(labels[f]) for f in members[group] if labels.get(f) is not None})
        if len(held) < 2:
            continue
        inner: dict[int, set[str]] = {}
        for face in members[group]:
            sub = labelled_partition.get(face)
            if sub is not None and labels.get(face) is not None:
                inner.setdefault(sub, set()).add(str(labels[face]))
        direct = any(len(names) >= 2 for names in inner.values())
        merges.append(Merge(group, tuple(held), len(members[group]), indirect=not direct))
    groups_of: dict[str, set[int]] = {}
    fragmented: dict[str, int] = {}
    for face, group in partition.items():
        label = labels.get(face)
        if label is None:
            continue
        if group is None:
            fragmented[label] = fragmented.get(label, 0) + 1
        else:
            groups_of.setdefault(label, set()).add(group)
    return Counts(
        groups=len(members),
        singletons=sum(1 for group in partition.values() if group is None),
        merges=tuple(merges),
        split={name: len(g) for name, g in sorted(groups_of.items()) if len(g) >= 2},
        fragmented=dict(sorted(fragmented.items())),
    )


def sweep(
    vectors: Mapping[int, Sequence[float]],
    labels: Mapping[int, str | None],
    epsilons: Sequence[float] = EPSILONS,
) -> list[tuple[float, Counts]]:
    """`cluster.dbscan` itself at every epsilon, over all faces and over the named ones."""
    labelled = {i: v for i, v in vectors.items() if labels.get(i) is not None}
    rows = []
    for epsilon in epsilons:
        full = cluster.dbscan(vectors, epsilon=epsilon, min_samples=cluster.MIN_SAMPLES)
        sub = cluster.dbscan(labelled, epsilon=epsilon, min_samples=cluster.MIN_SAMPLES)
        rows.append((epsilon, count(full, sub, labels)))
    return rows


def largest_clean(rows: Sequence[tuple[float, Counts]]) -> float | None:
    """The last epsilon before the first row with a merge, or None if the first row has one.

    Read from the smallest value up and STOPPED at the first merge, so a value above a merging
    one is never offered (see the module docstring). `rows` must be in ascending epsilon order,
    which is `EPSILONS`' order and `sweep`'s.
    """
    best: float | None = None
    for epsilon, counts in rows:
        if counts.merges:
            break
        best = epsilon
    return best


def recommendation(
    rows: Sequence[tuple[float, Counts]], persons: int, config_path: Path
) -> str:
    """H7's one closing sentence."""
    if persons < 2:
        return FEWER_THAN_TWO
    value = largest_clean(rows)
    if value is None:
        return NONE_CLEAN.format(low=f"{EPSILONS[0]:.3f}", high=f"{EPSILONS[-1]:.3f}")
    return RECOMMENDATION.format(e=f"{value:.3f}", path=config_path)


def report_lines(
    packs: Sequence[tuple[str, str]],
    faces: Sequence[Face],
    vectors: Mapping[int, Sequence[float]],
    *,
    floor: float,
    labels: Sequence[str],
    paths: Sequence[str],
    config_path: Path,
) -> list[str]:
    """Everything the command prints, exclusions first and the recommendation last."""
    kept, excluded = exclude(faces, floor=floor, labels=labels, paths=paths)
    gone = sum(len(ids) for ids in excluded.values())
    lines = [f"Excluded {gone:,} of {len(faces):,} faces:"]
    detail = {
        BELOW_FLOOR: f"below --floor {floor}",
        REVIEWED: "review_state set",
        EXCLUDED_LABEL: f"--exclude-label {', '.join(labels) or '(none)'}",
        EXCLUDED_PATH: f"--exclude-path {', '.join(paths) or '(none)'}",
    }
    lines.extend(f"  {detail[reason]}: {len(excluded[reason]):,}" for reason in REASONS)
    lines.extend(f"Pack: {name} {version}" for name, version in packs)
    label_of = {f.id: f.label for f in kept}
    kept_vectors = {f.id: vectors[f.id] for f in kept}
    per_person: dict[str, int] = {}
    for face in kept:
        if face.label is not None:
            per_person[face.label] = per_person.get(face.label, 0) + 1
    lines.append(f"Considered: {len(kept):,} faces, {sum(per_person.values()):,} labelled.")
    lines.extend(f"  {name}: {n:,}" for name, n in sorted(per_person.items()))
    pair = closest_cross_pair(kept_vectors, label_of)
    if pair is None:
        lines.append("Closest cross-person pair: none - fewer than two labelled persons.")
    else:
        lines.append(
            f"Closest cross-person pair: {pair.distance:.4f} ({pair.label_a} / {pair.label_b},"
            f" faces {pair.face_a} and {pair.face_b}) - below it no epsilon can merge two"
            " labelled people directly."
        )
    lines.append("epsilon  groups  singletons  merges  indirect  split  fragmented")
    rows = sweep(kept_vectors, label_of)
    for epsilon, row in rows:
        lines.append(
            f"{epsilon:7.3f}  {row.groups:6d}  {row.singletons:10d}  {len(row.merges):6d}"
            f"  {row.indirect:8d}  {len(row.split):5d}  {sum(row.fragmented.values()):10d}"
        )
        for merge in row.merges:
            how = "indirect" if merge.indirect else "direct"
            lines.append(
                f"    merged ({how}): {' + '.join(merge.labels)} in one group of {merge.size}"
            )
        for name, groups in row.split.items():
            lines.append(f"    split: {name} across {groups} groups")
        for name, alone in row.fragmented.items():
            lines.append(f"    left alone: {name} {alone} of {per_person[name]}")
    lines.append("")
    lines.append(recommendation(rows, len(per_person), config_path))
    return lines


def run_calibrate(
    archive: Path | None,
    floor: float | None = None,
    *,
    exclude_label: Sequence[str] = (),
    exclude_path: Sequence[str] = (),
) -> Calibration:
    """Read the catalog once, sweep, and return the lines. Writes nothing, anywhere."""
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    # H7: "Needs [faces]". The extra's own refusal - `pam enrich faces`' sentence - and exit 1
    # (FacesUnavailableError is a PamError). The sweep itself needs only numpy, which
    # `cluster.dbscan` imports inside the function; nothing here loads a model.
    faces_model.import_runtime()
    config = load_config(ws.config_path)
    threshold = config.tiers["faces"].floor if floor is None else floor
    conn = open_catalog_readonly(ws.db_path)
    try:
        # S13: a lock-free reader makes every read inside ONE snapshot.
        with read_snapshot(conn):
            mixed = generation_refusal(conn)
            rows = repo.labelled_face_embeddings(conn)
    finally:
        conn.close()
    if mixed:
        raise PamError(mixed)
    ragged = width_refusal(rows)
    if ragged:
        raise PamError(ragged)
    faces = [
        Face(r.id, r.det_confidence, r.person_label, r.review_state, r.rel_paths) for r in rows
    ]
    vectors = {r.id: cluster.decode_embedding(r.embedding) for r in rows}
    packs = sorted({(r.model_name, r.model_version) for r in rows})
    lines = report_lines(
        packs,
        faces,
        vectors,
        floor=threshold,
        labels=tuple(exclude_label),
        paths=tuple(exclude_path),
        config_path=ws.config_path,
    )
    return Calibration(archive_root=root, config_path=ws.config_path, lines=tuple(lines))


def print_calibration(report: Calibration) -> None:
    """stdout only: a lock-free reader keeps no session log, like `pam status`."""
    click.echo(f"Face calibration for {report.archive_root}")
    click.echo("")
    for line in report.lines:
        click.echo(line)

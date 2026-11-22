"""Catalog SQL for `face_instances` and `persons`. All SQL lives under catalog/repo/.

M11 Task 8 Step 5. `face_instances` is spec 4's second CONTENT-ADDRESSED table - keyed by
`sha256`, not by `file_id` - so two copies of one photograph share one set of detections
and a rename cannot orphan them. `persons` is the user-truth side: a row exists only
because somebody typed a name.

WHY THESE TWO TABLES ARE ONE MODULE AND NOT TWO. `persons` holds nothing a face does not
point at: `face_instances.person_id` is the only reference to it in the schema, every
write to one is made in the same breath as a write to the other (labeling a cluster
creates the person and moves the faces; merging a person moves faces and deletes the row),
and a `persons.py` of five statements would exist only to make `faces.py` reach into it.
That is the same argument `events.py` makes for `events` + `file_events` and
`companions.py` for `companion_groups` + `files.companion_group_id`, and it keeps
`guards.ALLOWED_REPO_REACHES` at five pairs.

WHAT IS NOT HERE, AND IT IS NOT AN OVERSIGHT (reported at the task boundary, Task 8).
Step 5 of the plan names eighteen queries for this module and two of them cannot live
here without a SIXTH cross-table reach, which the same step forbids:

  * `people_by_file_id` (Task 12 Step 1) is keyed by `files.id` by its name and its
    purpose - the planner holds file ids, not sha256 - so it is `face_instances` JOIN
    `persons` JOIN `files`. `files` is foreign to this module. It is NOT built here.
    Task 12 chose the composed form (owner ruling, 2026-09-17): `people_by_sha256` below
    is keyed by content and each consumer maps its files onto it, so no reach was added.
  * `plan_items_with_people_tokens` (A19, Task 13) reads `plan_items.tokens_json` and
    NOTHING ELSE - no face, no person, because A19 is explicit that the inventory must be
    "independent of label text". It is SQL over one table that this module does not own,
    so it is filed with that table, in `plan_items.py`, which is where the house rule
    ("one module per table") puts it. The public name is unchanged: callers say
    `repo.plan_items_with_people_tokens(conn)` either way.

THE FLOOR IS A PARAMETER, NEVER A CONSTANT HERE (A11). `[tiers] faces.floor` is applied
when detections are READ, so every read that a human or a clustering pass will see takes
`floor` and filters on it. Storage keeps every structurally valid detection with its
confidence, which is what makes raising and lowering the threshold both reversible. A
default value for `floor` in this module would be a second place the threshold lives, and
the one that got read when a caller forgot.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

# The non-NULL vocabulary of `face_instances.review_state` (migration 0007, A13). NULL is
# the fourth state and is spelled `None`, never a member here: it is the absence of a
# review, and a tuple that named it would invite `review_state = 'none'` in a URL.
#
# A face in EITHER of these states is excluded from clustering and from `{people}`, and is
# never offered for labeling. `not_a_face` is a false positive the user rejected;
# `dismissed` is UX 6's "Hide" for an unnamed cluster - a real face nobody wants named.
# They are two different corrections and both must be reversible (I3), which is why this is
# a durable column and not a filter applied at display time.
REVIEW_STATES = ("not_a_face", "dismissed")


class _Unset:
    """The sentinel `update_person` needs, because `None` is a legal new value.

    `filename_label` and `person_id` are nullable columns whose NULL is meaningful -
    "no filename override" and "not assigned" - so a partial update cannot use `None`
    to mean "leave this alone". A class rather than `object()` so mypy can narrow it.
    """


UNSET = _Unset()


@dataclass(frozen=True)
class FaceRow:
    """One detection, as clustering and the labeling page read it.

    `geometry` is A13's parsed document, not the raw `bbox_json` text: every consumer
    wants the box and the source size, and a second `json.loads` at each call site is a
    second place the parse can be got wrong.
    """

    id: int
    sha256: str
    geometry: dict[str, Any]
    embedding: bytes
    det_confidence: float
    cluster_id: int | None
    person_id: int | None
    review_state: str | None


@dataclass(frozen=True)
class ClusterSummary:
    """One cluster as the labeling page lists it (spec 8.2's `/faces/clusters`).

    `contents` counts DISTINCT sha256 and is deliberately not called "photos": spec 4
    keys this table by content, so two identical files in the archive are one content.
    """

    cluster_id: int
    faces: int
    contents: int
    person_id: int | None
    person_label: str | None


@dataclass(frozen=True)
class PersonLabel:
    """One person as `{people}` and people search read them (M11 Task 12, A14)."""

    id: int
    label: str
    filename_label: str | None

    @property
    def filename_value(self) -> str:
        """A14: `filename_label` when set and non-empty, else `label`.

        One property rather than a `label if not filename_label else ...` at each call
        site, because `{people}` and the review UI must show the same string and two
        spellings of one rule is one of them being wrong (M8's M922/M924). `PersonRow`
        EXTENDS this type rather than repeating the property, so there is one spelling by
        construction and not by care.
        """
        override = self.filename_label
        # A14 says the value written IS `filename_label` - so the raw string is returned,
        # not a stripped copy of it. `.strip()` decides only whether the override counts
        # as SET: an all-whitespace override is a user who cleared the field, and a token
        # of three spaces is not a name.
        return override if override is not None and override.strip() else self.label


@dataclass(frozen=True)
class PersonRow(PersonLabel):
    """One `persons` row with its face and content counts (spec 8.2's `/faces/persons`).

    A `PersonLabel` plus the columns only Manage People reads. The field order is the one
    this type has always had (`id, label, filename_label, hidden, faces, contents`), so a
    caller constructing it by position or by keyword sees no change.
    """

    hidden: bool
    faces: int
    contents: int


@dataclass(frozen=True)
class ModelGeneration:
    """One `(model_name, model_version)` present in `face_instances`, with its face count.

    A6's refusal reads this: one archive, one face-model generation. The COUNT is here so
    the refusal sentence can say how much work a purge would throw away, which is the
    number a user needs to decide.
    """

    model_name: str
    model_version: str
    faces: int


@dataclass(frozen=True)
class ExportFace:
    """One row of A20's export, minus the parts this module does not own.

    `rel_path` is NOT here. A20 asks the export document for a per-face `rel_path` and
    that is `files`, foreign to this module (see the module docstring); the command
    assembling the export resolves it from `sha256`. What is here is everything
    `face_instances` and `persons` know.
    """

    id: int
    sha256: str
    geometry: dict[str, Any]
    embedding: bytes
    det_confidence: float
    cluster_id: int | None
    person_label: str | None
    review_state: str | None
    model_name: str
    model_version: str


def _geometry(raw: object) -> dict[str, Any]:
    """A13's geometry document, parsed, or `{}` if this row does not hold one.

    Skipped rather than raised on, for the reason `enrichment._live_fact_strings` gives:
    the sink validates every detection before insert (spec 6.4), so a malformed document
    cannot come from pam - it can come from a hand-edited catalog, and a crop endpoint
    that refuses one face is better than a labeling page that will not open at all.
    """
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _face(row: sqlite3.Row) -> FaceRow:
    """One `face_instances` row as a `FaceRow`. The shared read, not a shared query."""
    return FaceRow(
        id=int(row["id"]),
        sha256=str(row["sha256"]),
        geometry=_geometry(row["bbox_json"]),
        embedding=bytes(row["embedding"]),
        det_confidence=float(row["det_confidence"]),
        cluster_id=None if row["cluster_id"] is None else int(row["cluster_id"]),
        person_id=None if row["person_id"] is None else int(row["person_id"]),
        review_state=None if row["review_state"] is None else str(row["review_state"]),
    )


_FACE_COLUMNS = (
    "id, sha256, bbox_json, embedding, det_confidence, cluster_id, person_id, review_state"
)


def insert_face(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    bbox_json: str,
    embedding: bytes,
    det_confidence: float,
    model_name: str,
    model_version: str,
) -> int:
    """One detection. `cluster_id`, `person_id` and `review_state` are NULL on insert.

    NULL on all three deliberately: a detection is not clustered until a clustering pass
    runs, is not a person until a human says so (I2), and has not been reviewed until a
    human reviews it. An enricher that could write any of them would be an enricher that
    could name a file.

    NO FLOOR (A11). Every structurally valid detection is stored with its confidence, so
    raising `[tiers] faces.floor` leaves nothing stranded and lowering it recovers what a
    higher setting hid. `insert_face` takes no threshold and must never grow one.
    """
    cur = conn.execute(
        "INSERT INTO face_instances"
        " (sha256, bbox_json, embedding, det_confidence, model_name, model_version)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sha256, bbox_json, embedding, det_confidence, model_name, model_version),
    )
    return int(cur.lastrowid or 0)


def faces_for_clustering(conn: sqlite3.Connection, floor: float) -> list[FaceRow]:
    """Every face a clustering pass may see: unlabelled, unreviewed, at or above `floor`.

    THE THREE FILTERS ARE THREE DIFFERENT PROMISES, not one predicate spelled three ways.
    `person_id IS NULL` keeps a pass from re-deciding a name a human already gave (I3:
    every correction is reversible, and a pass that overwrote one would not be).
    `review_state IS NULL` is A13: a rejected or dismissed face is excluded from
    clustering. `det_confidence >= floor` is A11's read-time threshold.

    Ordered by id so a re-run over an unchanged catalog produces the same clusters -
    "whichever row SQLite returned first" is not a rule (spec 6.2 rule 6's determinism,
    which clustering inherits the moment its output reaches a filename).
    """
    rows = conn.execute(
        f"SELECT {_FACE_COLUMNS} FROM face_instances"
        " WHERE person_id IS NULL AND review_state IS NULL AND det_confidence >= ?"
        " ORDER BY id",
        (floor,),
    ).fetchall()
    return [_face(r) for r in rows]


def retained_cluster_ids(conn: sqlite3.Connection, floor: float) -> list[int]:
    """Cluster ids held by faces a clustering pass may NOT touch. The complement of
    `faces_for_clustering`, restricted to rows that already carry a cluster id.

    WHY A CLUSTERING PASS NEEDS THIS, AND IT IS NOT AN OPTIMISATION. `pam faces cluster`
    renumbers the faces it considers from 1 upwards. Every face it does NOT consider keeps
    the cluster id it already had - a named person's faces, a dismissed face, a detection
    below the floor - and those ids come from an EARLIER pass that numbered from 1 as well.
    So a second run can hand cluster 1 to a group of strangers while a person the user
    named is still sitting in cluster 1, and `cluster_faces(1)` then returns both. That is
    a named person merged with strangers, which is vision R6's failure with no user action
    at all. The caller allocates above `max(...)` of this list; design 8.2 asks for
    "deterministic, collision-free cluster-id allocation" of the split for the same reason.

    THE PREDICATE IS THE NEGATION OF `faces_for_clustering`'s AND MUST STAY THAT WAY. The
    two are written out separately rather than shared, because SQLite has no way to say
    "the complement of that query" and a helper returning a WHERE fragment would be one
    string two callers trusted differently.
    `test_the_two_reads_partition_the_table` asserts the partition from the outside, over a
    catalog holding a row of every shape, which is what makes the duplication safe.
    """
    rows = conn.execute(
        "SELECT DISTINCT cluster_id FROM face_instances WHERE cluster_id IS NOT NULL"
        " AND NOT (person_id IS NULL AND review_state IS NULL AND det_confidence >= ?)"
        " ORDER BY cluster_id",
        (floor,),
    ).fetchall()
    return [int(r["cluster_id"]) for r in rows]


def max_cluster_id(conn: sqlite3.Connection) -> int:
    """The largest `cluster_id` held by ANY row, or 0 (spec 8.2's split allocation).

    NO FLOOR AND NO `review_state` FILTER, and that is the whole function. A split hands its
    groups fresh ids above this number; `retained_cluster_ids(floor)` would not do, because
    it leaves out every OFFERABLE face in other clusters, and any floor-filtered maximum
    would let a later change of `[tiers] faces.floor` bring back a below-floor face that
    already holds the id just handed out - two groups of strangers under one id.
    """
    row = conn.execute("SELECT MAX(cluster_id) AS top FROM face_instances").fetchone()
    return 0 if row is None or row["top"] is None else int(row["top"])


def cluster_faces(conn: sqlite3.Connection, cluster_id: int, floor: float) -> list[FaceRow]:
    """One cluster's UNNAMED faces, at or above `floor`, not reviewed away (A11, A13).

    `floor` again, and for the reason A11 gives: the labeling page and the clustering
    pass must be looking at the same set, or a user is asked about a face the algorithm
    never grouped.

    UNNAMED, because every caller is an unnamed-cluster operation (the queue's sample,
    expand, label, Ignore, split) and a face taken off a person KEEPS its `cluster_id`.
    Without `person_id IS NULL` the queue offered that face as a "1 face" group while this
    read and `set_person` saw the whole cluster, so saving a name there silently moved the
    named person's faces too (S2-F1, Task 11 Stage 2, on real photographs).
    """
    rows = conn.execute(
        f"SELECT {_FACE_COLUMNS} FROM face_instances WHERE cluster_id = ?"
        " AND person_id IS NULL AND review_state IS NULL AND det_confidence >= ?"
        " ORDER BY det_confidence DESC, id",
        (cluster_id, floor),
    ).fetchall()
    return [_face(r) for r in rows]


def face_by_id(conn: sqlite3.Connection, face_id: int) -> FaceRow | None:
    """One detection by its id, or None (spec 8.2's `/faces/crop/{face_id}`).

    NO FLOOR AND NO `review_state` FILTER, and both omissions are deliberate. This is the
    row lookup behind a crop, and every surface that can ASK for a crop has already
    decided the face belongs on it: the labeling page lists faces through `cluster_faces`,
    which applies A11's floor, and `/faces/ignored` lists exactly the faces the floor-and-
    state filters exclude - so a crop endpoint that re-applied either would leave the
    Ignored Faces page unable to draw the thing a user is there to restore (vision R6).

    `None` is a 404 with a sentence, not amendment A3's silent one: a request that reaches
    a handler has proved it carries the session token.
    """
    row = conn.execute(
        f"SELECT {_FACE_COLUMNS} FROM face_instances WHERE id = ?", (face_id,)
    ).fetchone()
    return None if row is None else _face(row)


def person_faces(conn: sqlite3.Connection, person_id: int) -> list[FaceRow]:
    """One named person's faces with no `review_state` (Manage People; owner ruling 4).

    NO FLOOR, and the omission is the reason this is not `cluster_faces` by person. A
    below-floor detection that is ASSIGNED to a person is still that person's, and a user
    correcting a mislabel may need to move exactly that face; a floor here would hide it
    from the only page that can repair it. The caller flags it instead. A reviewed-away
    face is excluded because it is listed - and restored - on Ignored faces.

    Ordered like `cluster_faces`: highest confidence first, then id, so the order is a rule.
    """
    rows = conn.execute(
        f"SELECT {_FACE_COLUMNS} FROM face_instances"
        " WHERE person_id = ? AND review_state IS NULL"
        " ORDER BY det_confidence DESC, id",
        (person_id,),
    ).fetchall()
    return [_face(r) for r in rows]


def ignored_faces(conn: sqlite3.Connection) -> list[FaceRow]:
    """Every face in a non-NULL `review_state` (spec 8.2's `/faces/ignored`).

    NO FLOOR. This is the page that exists so dismissing is not a one-way door (vision
    R6), and a face hidden by a threshold change would be unrestorable from the one
    surface built to restore it.
    """
    rows = conn.execute(
        f"SELECT {_FACE_COLUMNS} FROM face_instances"
        " WHERE review_state IS NOT NULL ORDER BY review_state, id"
    ).fetchall()
    return [_face(r) for r in rows]


def clusters_summary(conn: sqlite3.Connection, floor: float) -> list[ClusterSummary]:
    """Every cluster with its face and content counts and the person it names, if any.

    Faces with no `cluster_id` are absent: a cluster is what a clustering pass grouped,
    and an unclustered face belongs to no row here. Reviewed-away faces are excluded, so
    a cluster whose every face was dismissed disappears rather than showing a count of 0.
    """
    rows = conn.execute(
        "SELECT a.cluster_id AS cluster_id, COUNT(*) AS faces,"
        " COUNT(DISTINCT a.sha256) AS contents, MIN(a.person_id) AS person_id,"
        " p.label AS person_label"
        " FROM face_instances a LEFT JOIN persons p ON p.id = a.person_id"
        " WHERE a.cluster_id IS NOT NULL AND a.review_state IS NULL"
        " AND a.det_confidence >= ?"
        " GROUP BY a.cluster_id, p.label ORDER BY faces DESC, a.cluster_id",
        (floor,),
    ).fetchall()
    return [
        ClusterSummary(
            cluster_id=int(r["cluster_id"]),
            faces=int(r["faces"]),
            contents=int(r["contents"]),
            person_id=None if r["person_id"] is None else int(r["person_id"]),
            person_label=None if r["person_label"] is None else str(r["person_label"]),
        )
        for r in rows
    ]


def set_cluster(conn: sqlite3.Connection, assignments: list[tuple[int, int | None]]) -> None:
    """`(face id, cluster id)` pairs, in one statement. `None` un-clusters a face.

    `executemany` rather than a loop of `execute`, because a clustering pass reassigns
    every face it touched and spec 8.2's split says "the whole reassignment in ONE
    transaction". This function does not commit; the caller owns the transaction, which
    is what lets a split be atomic across a delete and an insert too.
    """
    conn.executemany(
        "UPDATE face_instances SET cluster_id = ? WHERE id = ?",
        [(cluster_id, face_id) for face_id, cluster_id in assignments],
    )


def set_person(
    conn: sqlite3.Connection, cluster_id: int, person_id: int | None, floor: float
) -> int:
    """Attach EVERY face in a cluster to a person (spec 8.2's `/faces/cluster/{id}/label`).

    Returns the number of faces touched, because spec 8.2 says the response reports it:
    "this is the action a user is most likely to regret".

    "EVERY FACE" MEANS EVERY FACE `cluster_faces` SHOWS, so the floor is the same one, and
    `>=` like it, and a face ALREADY NAMED is never re-named here (S2-F1). A11 applies the floor at read time, so raising it leaves below-floor
    detections inside clusters; a write with no floor named those too, disagreed with the
    page's count, and made `label` refuse the cluster for ever (Task 10's defect, closed in
    Task 11). Writing them instead would name a face the user never saw (vision R6).

    A reviewed-away face is NOT attached. It is excluded from the cluster everywhere else
    (`cluster_faces`, `clusters_summary`), so naming it here would put a person on a face
    the user has already said is not one - and `{people}` reads `review_state IS NULL`,
    so the name would be stored and then never used.
    """
    cur = conn.execute(
        "UPDATE face_instances SET person_id = ? WHERE cluster_id = ?"
        " AND person_id IS NULL AND review_state IS NULL AND det_confidence >= ?",
        (person_id, cluster_id, floor),
    )
    return int(cur.rowcount)


def assign_faces(conn: sqlite3.Connection, face_ids: list[int], person_id: int | None) -> int:
    """Move named faces to a person, or unassign them (spec 8.2's `/faces/faces/assign`).

    A DIFFERENT OPERATION FROM `set_person`, not a bulk spelling of it. `set_person`
    names a whole cluster and is what the labeling flow does; this names individual faces
    a user picked out of one, which is how a mis-clustered face is corrected without
    splitting anything. Collapsing the two would make "label this cluster" reachable with
    a hand-built id list and lose the `review_state` rule that belongs to the cluster
    spelling alone.

    `person_id=None` unassigns, which is I3: every correction is reversible.
    """
    if not face_ids:
        return 0
    cur = conn.executemany(
        "UPDATE face_instances SET person_id = ? WHERE id = ?",
        [(person_id, face_id) for face_id in _distinct(face_ids)],
    )
    return int(cur.rowcount)


def set_review_state(
    conn: sqlite3.Connection, face_ids: list[int], review_state: str | None
) -> int:
    """Mark faces `not_a_face` or `dismissed`, or CLEAR either (spec 8.2, A13).

    The vocabulary is checked HERE rather than by a CHECK constraint, following
    `journal.OPS` and `plan_items.ITEM_STATUSES`: no migration in this catalog uses a
    CHECK, and a typo'd state reaching the column would silently exclude a face from
    clustering and from `{people}` for ever with nothing reporting it.
    """
    if review_state is not None and review_state not in REVIEW_STATES:
        raise ValueError(f"{review_state!r} is not a review state: {REVIEW_STATES} or None")
    if not face_ids:
        return 0
    cur = conn.executemany(
        "UPDATE face_instances SET review_state = ? WHERE id = ?",
        [(review_state, face_id) for face_id in _distinct(face_ids)],
    )
    return int(cur.rowcount)


def _distinct(face_ids: list[int]) -> list[int]:
    """The ids once each, for a write bound ONE ID PER STATEMENT (design 2 rev 32, L1).

    A face list can be a whole cluster's (`dismiss`), so it grows with the archive and one
    `IN (?, ...)` can cross SQLite's bound-variable limit. `executemany` binds two variables
    per statement and sums `rowcount` across them; de-duplicating first keeps the count the
    single `IN` returned, which counted a repeated id once.
    """
    return sorted(set(face_ids))


def persons_all(conn: sqlite3.Connection) -> list[PersonRow]:
    """Every person with face and content counts (spec 8.2's `/faces/persons`).

    `contents` is DISTINCT sha256, NOT a file count. Spec 8.2 asks for "face and file
    counts" and the file count needs `files`, foreign to this module (see the module
    docstring); the two differ only when the archive holds duplicate copies of one
    photograph, and the caller that wants files can widen this by sha256.

    COUNTS ARE OF FACES NOT REVIEWED AWAY - the faces `person/{id}` lists and `{people}`
    reads (S2-F2, Task 11 Stage 2): a person whose one face was marked not-a-face read
    "1 face" with nothing to correct, and merge's confirmation is built from these.

    A LEFT JOIN, so a person whose every face was reassigned still appears with 0. That
    is the row a user needs in order to delete or merge them; an INNER JOIN would make an
    emptied person vanish from the only page that can clean them up.
    """
    rows = conn.execute(
        "SELECT p.id AS id, p.label AS label, p.filename_label AS filename_label,"
        " p.hidden AS hidden, COUNT(a.id) AS faces,"
        " COUNT(DISTINCT a.sha256) AS contents"
        " FROM persons p LEFT JOIN face_instances a"
        " ON a.person_id = p.id AND a.review_state IS NULL"
        " GROUP BY p.id ORDER BY p.label"
    ).fetchall()
    return [
        PersonRow(
            id=int(r["id"]),
            label=str(r["label"]),
            filename_label=None if r["filename_label"] is None else str(r["filename_label"]),
            hidden=bool(r["hidden"]),
            faces=int(r["faces"]),
            contents=int(r["contents"]),
        )
        for r in rows
    ]


def people_by_sha256(
    conn: sqlite3.Connection, floor: float
) -> dict[str, tuple[PersonLabel, ...]]:
    """`{sha256: people}` - who `{people}` names, and who people search finds (Task 12).

    KEYED BY CONTENT, NOT BY FILE, and that is the maintainer's ruling of 2026-09-17 rather than
    a convenience: a file-keyed read needs `files`, foreign to this module, and would be the
    package's sixth cross-table reach. The planner and `review.plan_rows` both already hold
    each file's sha256 and compose the two answers themselves.

    FOUR FILTERS, EACH A PROMISE. The inner join means only a face a HUMAN attached to a
    person counts - an unnamed cluster names nothing (I2, vision R6). `hidden = 0` is spec
    4's "hidden persons never appear in {people}". `review_state IS NULL` keeps a rejected
    or dismissed face out (A13). `det_confidence >= floor` is A11's read-time threshold,
    `>=` like every other read here. DISTINCT makes a person with two faces in one photo
    one entry.

    ORDERED BY THE VALUE THAT REACHES THE FILENAME (A14): `(casefold, value, id)`, so the
    order is alphabetical, independent of how a label was capitalised, and total.
    """
    found: dict[str, list[PersonLabel]] = {}
    for r in conn.execute(
        "SELECT DISTINCT a.sha256, p.id, p.label, p.filename_label"
        " FROM face_instances a JOIN persons p ON p.id = a.person_id"
        " WHERE p.hidden = 0 AND a.review_state IS NULL AND a.det_confidence >= ?",
        (floor,),
    ):
        found.setdefault(r[0], []).append(PersonLabel(r[1], r[2], r[3]))
    for people in found.values():
        people.sort(key=lambda p: (p.filename_value.casefold(), p.filename_value, p.id))
    return {sha: tuple(people) for sha, people in found.items()}


def upsert_person(conn: sqlite3.Connection, label: str) -> int:
    """The id of the person with this label, creating the row if there is none.

    CASE-INSENSITIVE, which spec 8.2 states in as many words: "so `Emma` and `emma` do
    not become two people". `persons.label` is UNIQUE but SQLite's default collation is
    case-SENSITIVE, so the constraint alone would happily hold both - the match has to be
    `COLLATE NOCASE` here, and an existing row's spelling wins over the one just typed.

    No `ON CONFLICT`: this project forbids `ON CONFLICT REPLACE`, and a lookup-then-insert
    inside the caller's transaction is exactly as safe with a single writer.
    """
    row = conn.execute(
        "SELECT id FROM persons WHERE label = ? COLLATE NOCASE", (label,)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute("INSERT INTO persons (label) VALUES (?)", (label,))
    return int(cur.lastrowid or 0)


def update_person(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    label: str | _Unset = UNSET,
    filename_label: str | None | _Unset = UNSET,
    hidden: bool | _Unset = UNSET,
) -> None:
    """Rename, set the filename override, or hide/unhide (spec 8.2's `/faces/person/{id}`).

    Each argument defaults to `UNSET` rather than to `None` because `filename_label`'s
    `None` is a real value - "clear the override" - and a partial update that could not
    express it would make an override one-way, which vision R6 forbids.
    """
    sets: list[str] = []
    args: list[Any] = []
    if not isinstance(label, _Unset):
        sets.append("label = ?")
        args.append(label)
    if not isinstance(filename_label, _Unset):
        sets.append("filename_label = ?")
        args.append(filename_label)
    if not isinstance(hidden, _Unset):
        sets.append("hidden = ?")
        args.append(1 if hidden else 0)
    if not sets:
        return
    args.append(person_id)
    conn.execute(f"UPDATE persons SET {', '.join(sets)} WHERE id = ?", tuple(args))


def merge_person(conn: sqlite3.Connection, person_id: int, into: int) -> int:
    """Move every face to `into` and delete the emptied row (spec 8.2's `/merge`).

    IT MOVES FACES; IT DOES NOT RENAME, which is spec 8.2's own sentence and follows from
    `persons.label` being UNIQUE: merging towards an existing label cannot make a second
    row carry it. Returns the number of faces moved.

    Merging a person with themselves is refused rather than silently doing nothing: it
    would delete the row every face had just been "moved" to, taking the person and their
    label with it.

    (The parameter is named `into` and the prose avoids the two words "in" and "to" run
    together on purpose: `tests/guards.py` extracts table names out of this module's
    STRING LITERALS by regex, and `INTO <word>` in a docstring reads to it as SQL.)
    """
    if person_id == into:
        raise ValueError(f"cannot merge person {person_id} with themselves")
    cur = conn.execute(
        "UPDATE face_instances SET person_id = ? WHERE person_id = ?", (into, person_id)
    )
    conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
    return int(cur.rowcount)


def model_generations(conn: sqlite3.Connection) -> list[ModelGeneration]:
    """Every `(model_name, model_version)` in `face_instances`, with its face count (A6).

    A6's refusal is "one archive, one face-model generation", so what this answers is
    "how many generations are here" - and the answer to a well-behaved archive is one row
    or none. The count rides along because the refusal sentence has to tell the user what
    a purge would cost them.
    """
    rows = conn.execute(
        "SELECT model_name, model_version, COUNT(*) AS faces FROM face_instances"
        " GROUP BY model_name, model_version ORDER BY model_name, model_version"
    ).fetchall()
    return [
        ModelGeneration(
            model_name=str(r["model_name"]),
            model_version=str(r["model_version"]),
            faces=int(r["faces"]),
        )
        for r in rows
    ]


def export_faces(conn: sqlite3.Connection) -> list[ExportFace]:
    """Every face with its person's label, for A20's data-access export.

    NO FLOOR AND NO `review_state` FILTER. A20 is a data-access export: it is what pam
    holds about the user, and a threshold or a rejection is pam's reading of it rather
    than a reason to withhold a row. `review_state` is exported as a field instead, so
    the document says what pam thought without deciding it for the reader.
    """
    rows = conn.execute(
        "SELECT a.id AS id, a.sha256 AS sha256, a.bbox_json AS bbox_json,"
        " a.embedding AS embedding, a.det_confidence AS det_confidence,"
        " a.cluster_id AS cluster_id, a.review_state AS review_state,"
        " a.model_name AS model_name, a.model_version AS model_version,"
        " p.label AS person_label"
        " FROM face_instances a LEFT JOIN persons p ON p.id = a.person_id"
        " ORDER BY a.id"
    ).fetchall()
    return [
        ExportFace(
            id=int(r["id"]),
            sha256=str(r["sha256"]),
            geometry=_geometry(r["bbox_json"]),
            embedding=bytes(r["embedding"]),
            det_confidence=float(r["det_confidence"]),
            cluster_id=None if r["cluster_id"] is None else int(r["cluster_id"]),
            person_label=None if r["person_label"] is None else str(r["person_label"]),
            review_state=None if r["review_state"] is None else str(r["review_state"]),
            model_name=str(r["model_name"]),
            model_version=str(r["model_version"]),
        )
        for r in rows
    ]


def purge_faces(conn: sqlite3.Connection) -> tuple[int, int]:
    """Delete every face and every person. Returns `(faces, persons)` deleted (A19).

    THE TWO TABLES THIS MODULE OWNS, AND NOT THE WHOLE OF A19. A19's purge also removes
    the `faces` marker facts and the `faces` `enrichment_runs` row - both in
    `inferred_facts`/`enrichment_runs`, which `enrichment.py` owns - and the cached crops,
    which are files. It says all of it happens "in one transaction", so the COMMAND
    composes those calls and commits once; a single function here would have had to reach
    into two foreign tables to do it.

    FACES FIRST, THEN PERSONS. `face_instances.person_id` REFERENCES `persons(id)` and
    `catalog/db.py` turns `PRAGMA foreign_keys=ON`, so emptying `persons` while a face
    still points at one is refused by SQLite. The order is a constraint, not a style.

    No commit. The caller owns the transaction, which is the whole point of A19's
    one-transaction sentence.
    """
    faces = int(conn.execute("DELETE FROM face_instances").rowcount)
    persons = int(conn.execute("DELETE FROM persons").rowcount)
    return faces, persons

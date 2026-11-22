"""Spec 8.2's `/faces/*` READ routes, plus `cluster/{id}/label` (M11 Task 10, A18).

WHAT IS HERE AND WHAT IS TASK 11's. This module ships the routes the labeling page of
UX 6 is built out of - list the unnamed clusters biggest first, expand one, draw a face,
autocomplete a name - and the ONE write that page performs, `label`. A18's remaining
write routes (`faces/state`, `faces/assign`, `person/{id}`, `person/{id}/merge`,
`cluster/{id}/dismiss`, `cluster/{id}/split`) belong to Task 11, which names them as its
own Step 1 and owns split's preview and undo in its Step 4; `/faces/ignored` goes with
them, because the surface that exists so dismissing is not a one-way door is Task 11
Step 3 and a route with no reader is a route nobody has checked. Task 11 Step 1 put all
but split in `pam/webui/faces_manage_api.py`, a FOURTH table merged beside this one,
because the rows would have taken this file to the ~300-line budget (I16).

THE THIRD TABLE, AND IT IS ROWS RATHER THAN BRANCHES. `ReviewServer.__init__` merges
`api.HANDLERS`, `write_api.WRITE_HANDLERS` and `FACES_HANDLERS` in one place; nothing in
`server._api` or `server._match_route` knows this module exists. It is a separate table
for the reason the second one is: everything here needs `ApiError`, `ApiRequest` and
`RawResponse` from `api.py`, so `api.py` cannot import it back.

A FACE IS KEYED BY CONTENT, NOT BY FILE, and every response here carries the consequence.
Spec 4 keys `face_instances` by `sha256`, so a face does not know which PATH it was seen
at - an archive holding one photograph twice holds it once as far as faces are concerned.
`repo.files_by_sha256` is what turns a face back into a file, and it returns a LIST
because both copies are real; `webui/crop.py` picks the one that is actually there. The
counts spec 8.2 calls "photographs" are therefore DISTINCT CONTENTS, which is the honest
number: a user who duplicated a folder has not got twice as many photographs of Emma.

A11's FLOOR IS APPLIED HERE BECAUSE IT IS APPLIED AT READ TIME. `[tiers] faces` floor is
never written into the catalog - that is the whole amendment, so raising and lowering it
are both reversible - so every read of a cluster passes `server.config.tiers["faces"].
floor` down, and the labeling page and the clustering pass are looking at the same set.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.faces import FaceRow
from pam.webui import crop
from pam.webui.api import (
    ApiRequest,
    RawResponse,
    _bad_request,
    _bounded_int,
    _conflict,
    _not_found,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# Spec 8.2: "up to FACE_SAMPLE {face_id, file_id} chosen for detection quality and spread,
# never row order". Five is UX 6's wireframe, which draws five face thumbnails above the
# name field.
FACE_SAMPLE = 5

# Paging for `/faces/clusters`. Smaller than the review grid's because a cluster row is a
# heavier thing to render than a card and the page shows one cluster at a time anyway;
# the list exists for the progress line and the nav, not for a contact sheet.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Spec 8.2 / A19: pam has no crop cache, and may not have one until `pam faces purge` can
# inventory and delete it. Until then every crop says so, in the one header that stops a
# browser keeping a copy pam cannot account for.
NO_STORE = (("Cache-Control", "no-store"),)


def clusters(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """UX 6's queue: the UNNAMED clusters, BIGGEST FIRST, with the progress counts.

    LARGEST FIRST IS THE PRODUCT DECISION, not a default sort (UX 6: "highest naming
    payoff first"). `repo.clusters_summary` orders by face count descending already and
    this endpoint does not re-sort; it filters to the unnamed ones and pages.

    THE TWO COUNTS ARE ABOUT THE WHOLE ARCHIVE AND NOT ABOUT THE PAGE. `named` and
    `remaining` are what UX 6's progress line reads ("7 named - 16 remaining"), so they
    are computed over every cluster before the slice, and paging through the queue does
    not make the progress bar move on its own.
    """
    offset = _bounded_int(request, "offset", 0, maximum=2**31)
    limit = _bounded_int(request, "limit", DEFAULT_LIMIT, maximum=MAX_LIMIT)
    floor = _floor(server)
    with server.connection() as conn:
        summaries = repo.clusters_summary(conn, floor)
        unnamed = [row for row in summaries if row.person_id is None]
        page = unnamed[offset : offset + limit]
        rows = [
            {
                "cluster_id": row.cluster_id,
                "faces": row.faces,
                "photographs": row.contents,
                "sample": _sample(conn, row.cluster_id, floor),
            }
            for row in page
        ]
        hidden = hidden_below_floor(conn, floor)
    return {
        "clusters": rows,
        "offset": offset,
        "limit": limit,
        "total": len(unnamed),
        **_progress(summaries),
        "hidden_below_floor": hidden,
        # The done state's F5 sentence names the threshold (P2); the page has no other
        # way to know it, because A11's floor is configuration the server reads.
        "floor": floor,
    }


def progress(conn: Any, floor: float) -> dict[str, int]:
    """UX 6's "N named - M remaining", for a WRITE's response (P2, M12 Task 5).

    HT-M11 Leg B: the counter did not move after a save, because the page moved it by its
    own arithmetic and a write can change counts arithmetic cannot see (a merge, a move, a
    restore into a named person). Every write the page makes returns these two numbers,
    read AFTER the write inside its own transaction, and the page shows them as given.
    Same definition as `clusters`, because it is the same function over the same rows.
    """
    return _progress(repo.clusters_summary(conn, floor))


def _progress(summaries: list[Any]) -> dict[str, int]:
    return {
        "named": sum(1 for row in summaries if row.person_id is not None),
        "remaining": sum(1 for row in summaries if row.person_id is None),
    }


def hidden_below_floor(conn: Any, floor: float) -> int:
    """F5 (P2): unnamed groups whose unreviewed faces ALL sit below the floor.

    `repo.clusters_summary` called twice - at the floor and at 0.0 - and the UNNAMED
    group ids present in the second and absent from the first counted. No new SQL and
    no edit to `repo/faces.py` (I13). Unnamed on both sides, because the queue is the
    unnamed groups: a cluster with named faces above the floor and an unnamed remainder
    only below it is a group the queue is not showing, and it counts.
    """
    shown = {r.cluster_id for r in repo.clusters_summary(conn, floor) if r.person_id is None}
    every = {r.cluster_id for r in repo.clusters_summary(conn, 0.0) if r.person_id is None}
    return len(every - shown)


def cluster(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Every face in one cluster, "so a user can expand before assigning one name to all".

    An EMPTY cluster is a 404 rather than an empty list, and the difference is the one a
    page must show differently: `repo.cluster_faces` excludes reviewed-away faces and
    faces below A11's floor, so a cluster whose every face has been ignored - or whose
    every face fell below a raised floor - is not a cluster with no members, it is a
    cluster that is no longer there to name.
    """
    cluster_id = _cluster_id(request)
    floor = _floor(server)
    with server.connection() as conn:
        faces = unnamed_or_refuse(conn, cluster_id, floor)
        return {
            "cluster_id": cluster_id,
            "faces": [_face_json(conn, face) for face in faces],
            "photographs": len({face.sha256 for face in faces}),
        }


def persons(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """UX 6's autocomplete: every person pam knows, with their counts.

    `photographs` IS DISTINCT CONTENT, NOT A FILE COUNT, and the field is named for what
    it is. Spec 8.2 asks for "face and file counts"; `repo.persons_all`'s docstring
    records why it cannot answer the second - `files` is foreign to that module and
    reaching it would cost a sixth entry in `guards.ALLOWED_REPO_REACHES` - and the two
    differ only where the archive holds duplicate copies of one photograph. Serving the
    number under the name of the number it is beats serving it under the other one.

    HIDDEN PEOPLE ARE INCLUDED AND FLAGGED. They contribute nothing to `{people}` (A14),
    but they are people pam knows, and a name that autocompleted to a NEW person because
    the existing one was hidden is exactly the `Emma`/`emma` duplicate this endpoint
    exists to prevent.
    """
    with server.connection() as conn:
        rows = repo.persons_all(conn)
    return {
        "persons": [
            {
                "id": row.id,
                "label": row.label,
                "filename_label": row.filename_label,
                "filename_value": row.filename_value,
                "hidden": row.hidden,
                "faces": row.faces,
                "photographs": row.contents,
            }
            for row in rows
        ]
    }


def face_crop(server: "ReviewServer", request: ApiRequest) -> RawResponse:
    """THE FACE, cut from the shared decoder's raster (spec 8.2, A18, invariant I4).

    THE WORK HAPPENS OUTSIDE THE DB LOCK, which is `api.thumb`'s rule and matters more
    here: `server.connection()` holds the single lock for its whole block, and a decode is
    hundreds of milliseconds on a full-size photograph. The block does the two row lookups
    and nothing else; the decode and the cut happen after it has closed.

    EVERY UNAVAILABLE STATE IS A 404 WITH A SENTENCE. A face pam does not know, a
    photograph that has moved, a cloud placeholder pam will not hydrate (vision R2), a
    geometry document that does not match the raster (I4), a file that will not decode -
    the page draws the same placeholder card for all of them and the sentence is what the
    user is shown when they ask why. Amendment A3's SILENT empty 404 is a different thing
    and is not this: a request that reached this handler proved it carries the token.
    """
    face_id = _face_id(request)
    with server.connection() as conn:
        face = repo.face_by_id(conn, face_id)
        if face is None:
            raise _not_found(f"pam does not know a face with id {face_id}")
        candidates = repo.files_by_sha256(conn, face.sha256)
        # Read with the rows, inside the lock (R1-F1), never after it.
        root = server.workspace.archive_root
    try:
        source = crop.source_file(candidates, root)
        rendered = crop.render(root, source, face.geometry)
    except crop.CropUnavailable as unavailable:
        raise _not_found(str(unavailable)) from None
    return RawResponse(crop.JPEG_TYPE, rendered.body, NO_STORE)


def label(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Attach EVERY face in a cluster to a person (spec 8.2's `cluster/{id}/label`).

    CASE-INSENSITIVE ON THE LABEL, which spec 8.2 states in as many words - "so `Emma` and
    `emma` do not become two people". `repo.upsert_person` is where that lives; an existing
    row's spelling wins over the one just typed, so the archive keeps one name per person
    rather than whichever capitalisation was typed last.

    IT REPORTS WHAT IT CHANGED, in faces AND in photographs (spec 6.2 rule 7, vision R6):
    "this is the action a user is most likely to regret". The two numbers are different and
    both are needed - forty faces across three photographs and forty faces across forty
    photographs are very different mistakes to have just made, and the page (Step 7) shows
    the sentence built from them.

    THE COUNTS ARE READ BEFORE THE WRITE AND THE WRITE'S OWN NUMBER IS ASSERTED AGAINST
    THEM. `repo.set_person` returns its `rowcount`, and `repo.cluster_faces` is the list
    the page was looking at; if the two disagree the response would be reporting a
    different action from the one that happened, so the disagreement is a 409 rather than
    a number nobody can act on.
    """
    cluster_id = _cluster_id(request)
    name = _person_name(request)
    floor = _floor(server)
    with server.connection() as conn:
        faces = unnamed_or_refuse(conn, cluster_id, floor)
        photographs = len({face.sha256 for face in faces})
        person_id = repo.upsert_person(conn, name)
        touched = repo.set_person(conn, cluster_id, person_id, floor)
        if touched != len(faces):
            raise _conflict(
                f"pam was about to name {len(faces)} faces and found {touched};"
                " nothing was changed. Reload the page."
            )
        stored = [row for row in repo.persons_all(conn) if row.id == person_id]
        counts = progress(conn, floor)
        conn.commit()
    return {
        **counts,
        "cluster_id": cluster_id,
        "person_id": person_id,
        "label": stored[0].label if stored else name,
        "faces": touched,
        "photographs": photographs,
        # Exactly the faces written (the count check above makes it so): Task 11 Step 5's
        # undo posts these back to `faces/faces/assign` with `person: null`.
        "face_ids": sorted(face.id for face in faces),
    }


def unnamed_or_refuse(conn: Any, cluster_id: int, floor: float) -> list[FaceRow]:
    """The UNNAMED faces an unnamed-cluster operation acts on, or a refusal saying why not.

    S2-F1 (Task 11 Stage 2): a cluster can hold named AND unnamed faces - a face taken off
    a person keeps its `cluster_id` - and the queue offers the unnamed remainder as its own
    group. Label, Ignore and split act on exactly that remainder. A cluster with NO unnamed
    face left but named ones is a stale page's request: a 409 naming the people, never a
    wholesale re-name. Neither: a 404.
    """
    faces = repo.cluster_faces(conn, cluster_id, floor)
    if faces:
        return faces
    labels = sorted(
        {
            row.person_label
            for row in repo.clusters_summary(conn, floor)
            if row.cluster_id == cluster_id and row.person_label is not None
        }
    )
    if labels:
        raise _conflict(
            f"pam changed nothing. This group is already named {', '.join(labels)};"
            " change that from Manage People instead."
        )
    raise _not_found(f"pam has no unnamed cluster with id {cluster_id}")


def _floor(server: "ReviewServer") -> float:
    """`[tiers] faces` floor, read from the server's configuration (A11)."""
    return server.config.tiers["faces"].floor


def _cluster_id(request: ApiRequest) -> int:
    """The `{id}` out of the path. Not a whole number -> 404: no such cluster exists."""
    raw = request.params["id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know a cluster with id {raw!r}")
    return int(raw)


def _face_id(request: ApiRequest) -> int:
    """The `{face_id}` out of the path, which is `face_instances.id`."""
    raw = request.params["face_id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know a face with id {raw!r}")
    return int(raw)


def _person_name(request: ApiRequest) -> str:
    """The `person` field of a `label` body: a non-empty name, stripped of its edges.

    A 400 NAMING THE FIELD, because UX 1.4 forbids a refusal that does not say which
    control is wrong. An all-whitespace name is the same refusal as an absent one: a
    person whose label is three spaces is a row nothing can ever match again, and
    `persons.label` is UNIQUE, so there would be exactly one of them for the whole
    archive.
    """
    value = request.body.get("person")
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("person", "a name is needed, and it cannot be blank")
    return value.strip()


def _sample(conn: Any, cluster_id: int, floor: float) -> list[dict[str, Any]]:
    """Up to `FACE_SAMPLE` faces from one cluster: DETECTION QUALITY AND SPREAD (A18).

    NEVER ROW ORDER, which is spec 8.2's own wording and is the whole of this function's
    reason to exist. `repo.cluster_faces` returns highest confidence first, so quality is
    the ordering; SPREAD is the second pass - one face per distinct photograph before any
    photograph contributes a second - so a cluster of forty faces from one group shot does
    not show the user the same face five times and ask who it is.

    The fill-up afterwards is what makes the answer `FACE_SAMPLE` faces rather than
    "however many distinct photographs there happen to be": a cluster of five faces in two
    photographs still gets five thumbnails, just with the spread ones first.
    """
    faces = repo.cluster_faces(conn, cluster_id, floor)
    seen: set[str] = set()
    spread: list[FaceRow] = []
    for face in faces:
        if face.sha256 not in seen:
            seen.add(face.sha256)
            spread.append(face)
    picked = {face.id for face in spread}
    chosen = spread + [face for face in faces if face.id not in picked]
    return [_face_json(conn, face) for face in chosen[:FACE_SAMPLE]]


def _face_json(conn: Any, face: FaceRow) -> dict[str, Any]:
    """One face as the page reads it: what to draw, and which photograph it is in.

    `file_id` is spec 8.2's field and is NULLABLE here, which the spec's one-line entry
    does not say and the catalog forces: a face is keyed by content, and the content may
    have no `files` row pam can currently point at. The crop endpoint does not need it -
    it is keyed by `face_id` - so a null `file_id` costs the page a link and not a face.

    THE EMBEDDING IS NEVER SERVED. It is 512 bytes of float32 per face, the browser has no
    use for it, and a response that carried it would put the archive's biometric vectors
    on a socket to satisfy a contact sheet.
    """
    candidates = repo.files_by_sha256(conn, face.sha256)
    return {
        "face_id": face.id,
        "file_id": candidates[0].id if candidates else None,
        "filename": candidates[0].filename if candidates else None,
        "confidence": face.det_confidence,
    }


# The `/faces/*` rows `ReviewServer` merges in, beside `api.HANDLERS` and
# `write_api.WRITE_HANDLERS`. A task adds ROWS; Task 11's correction routes are rows in
# `faces_manage_api.FACES_MANAGE_HANDLERS`, and nothing in the router changed for them.
FACES_HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("GET", "faces/clusters"): clusters,
    ("GET", "faces/cluster/{id}"): cluster,
    ("GET", "faces/crop/{face_id}"): face_crop,
    ("GET", "faces/persons"): persons,
    ("POST", "faces/cluster/{id}/label"): label,
}

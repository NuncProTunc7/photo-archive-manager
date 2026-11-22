"""Spec 8.2's `/faces/*` CORRECTION routes (M11 Task 11 Step 1, amendment A18).

`faces/state`, `faces/assign`, `person/{id}`, `person/{id}/merge`, `cluster/{id}/dismiss`
and `/faces/ignored`; plus `GET faces/person/{id}`, the read Manage People needs (the
maintainer's ruling 4, Task 11 Step 2). Split is NOT here: it owns a preview and an undo
(Task 11 Step 4).

A FOURTH TABLE AND NOT MORE ROWS IN `faces_api`, FOR THE FILE BUDGET (I16) ALONE.
`faces_api.py` stood at 155 executable lines and these six routes would have taken it to
the ~300 ceiling. The mechanism is unchanged: `ReviewServer.__init__` merges this table
beside the other three in one place, and nothing in the router knows the module exists.
Helpers are imported FROM `faces_api`, never the reverse.

EVERY WRITE SAYS WHAT IT CHANGED (spec 6.2 rule 7): how many faces, how many photographs
(DISTINCT CONTENT, `faces_api`'s module docstring gives why) and how many people. Every
write the naming page's counter follows also returns `named` and `remaining`, read after
the write (`faces_api.progress`, P2) - never left to the page's arithmetic. EVERY
WRITE IS ONE TRANSACTION with one explicit `conn.commit()`, and every refusal is raised
before that commit, so `server.connection()` closing the handle rolls it back.

A BATCH WITH ONE UNKNOWN FACE ID CHANGES NOTHING, following `decide`'s precedent (spec
8.2's whole-batch refusal): a 409 naming the ids, raised before the first write.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.faces import UNSET, FaceRow, PersonRow
from pam.webui.api import ApiRequest, _bad_request, _conflict, _not_found
from pam.webui.faces_api import (
    _cluster_id,
    _face_json,
    _floor,
    _person_name,
    progress,
    unnamed_or_refuse,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# A18's vocabulary for `faces/state`, spelled here as the WIRE contract. `None` clears.
STATES = ("not_a_face", "dismissed")


def ignored(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Every dismissed face and not-a-face detection, with a count per state (A18).

    NO FLOOR, for the reason `repo.ignored_faces` records: this is the page that makes
    dismissing reversible, and a face a threshold hid would be unrestorable from it.
    Crops are drawn through the existing `faces/crop/{face_id}` route.
    """
    with server.connection() as conn:
        faces = repo.ignored_faces(conn)
        listed = [
            {**_face_json(conn, face), "review_state": face.review_state} for face in faces
        ]
    counts = {state: sum(1 for face in faces if face.review_state == state) for state in STATES}
    return {"faces": listed, "counts": counts}


def person(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """One named person and their faces - Manage People's expansion (owner ruling 4).

    A ROUTE A18's LIST DOES NOT NAME, added by the maintainer's ruling because every per-face
    correction takes `face_ids` and no A18 route listed a person's faces (`/faces/persons`
    carries counts; a person's faces can span clusters). The design model amends spec 8.2's
    text at the close.

    NO FLOOR ON THE LIST, EVERY FACE FLAGGED AGAINST IT: an assigned below-floor face is
    still this person's and may be exactly the one to correct. HIDDEN PEOPLE ARE SERVED -
    this is where they are unhidden.
    """
    person_id = _path_person_id(request)
    floor = _floor(server)
    with server.connection() as conn:
        row = _person(conn, person_id)
        faces = [
            {**_face_json(conn, face), "below_floor": face.det_confidence < floor}
            for face in repo.person_faces(conn, person_id)
        ]
    return {
        "person": {**_person_json(row), "faces": row.faces, "photographs": row.contents},
        "faces": faces,
    }


def set_state(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Mark faces `not_a_face` or `dismissed`, or CLEAR either with `null` (A13, I3).

    `person_labels` SAYS WHERE THE FACES GO (P2's O8). A restored face returns to the
    person it was already attached to - HT-M11 watched one go straight back to a named
    person while the page promised "its group" - so the response names the distinct
    labels of the faces' `person_id`s, `null` standing for a face with no person, and the
    page chooses its sentence from that. A review state never changes `person_id`.
    """
    face_ids = _face_ids(request)
    if "review_state" not in request.body:
        raise _bad_request("review_state", "must be not_a_face, dismissed or null")
    state = request.body["review_state"]
    if state is not None and (not isinstance(state, str) or state not in STATES):
        raise _bad_request("review_state", "must be not_a_face, dismissed or null")
    with server.connection() as conn:
        faces = _known_faces(conn, face_ids)
        touched = repo.set_review_state(conn, face_ids, state)
        labels = {row.id: row.label for row in repo.persons_all(conn)}
        counts = progress(conn, _floor(server))
        conn.commit()
    named = sorted({labels[f.person_id] for f in faces if f.person_id in labels})
    unnamed: list[str | None] = [None] if any(f.person_id is None for f in faces) else []
    return {
        "review_state": state,
        **_changed(faces, touched),
        "person_labels": [*named, *unnamed],
        **counts,
    }


def assign(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Move faces to a person, matched case-insensitively, or unassign them with `null`."""
    face_ids = _face_ids(request)
    name = None if request.body.get("person", "") is None else _person_name(request)
    with server.connection() as conn:
        faces = _known_faces(conn, face_ids)
        person_id = None if name is None else repo.upsert_person(conn, name)
        touched = repo.assign_faces(conn, face_ids, person_id)
        stored = None if person_id is None else _person(conn, person_id)
        counts = progress(conn, _floor(server))
        conn.commit()
    changed = _changed(faces, touched, extra_person=person_id)
    return {
        "person_id": person_id,
        "label": None if stored is None else stored.label,
        **changed,
        **counts,
    }


def update_person(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Rename, set or clear the filename override, hide or unhide ONE named person.

    A RENAME ONTO ANOTHER PERSON'S LABEL IS REFUSED, NEVER MERGED. `persons.label` is
    UNIQUE but SQLite compares it case-SENSITIVELY, while pam matches names with
    `COLLATE NOCASE` (`repo.upsert_person`). So `emma` onto an existing `Emma` would land
    as a second person the autocomplete can no longer tell apart; the user is told to
    merge, which is the operation that says what it destroys.
    """
    person_id = _path_person_id(request)
    fields = {name for name in ("label", "filename_label", "hidden") if name in request.body}
    if not fields:
        raise _bad_request("body", "send at least one of label, filename_label or hidden")
    label: str | Any = UNSET
    filename_label: str | None | Any = UNSET
    hidden: bool | Any = UNSET
    if "label" in fields:
        raw = request.body["label"]
        if not isinstance(raw, str) or not raw.strip():
            raise _bad_request("label", "a name is needed, and it cannot be blank")
        label = raw.strip()
    if "filename_label" in fields:
        filename_label = request.body["filename_label"]
        if filename_label is not None and not isinstance(filename_label, str):
            raise _bad_request("filename_label", "must be text, or null to clear it")
    if "hidden" in fields:
        hidden = request.body["hidden"]
        if not isinstance(hidden, bool):
            raise _bad_request("hidden", "must be true or false")
    with server.connection() as conn:
        current = _person(conn, person_id)
        if isinstance(label, str):
            _refuse_label_collision(conn, person_id, label)
        repo.update_person(
            conn, person_id, label=label, filename_label=filename_label, hidden=hidden
        )
        stored = _person(conn, person_id)
        conn.commit()
    return {
        "person": _person_json(stored),
        "faces": current.faces,
        "photographs": current.contents,
        "people": 1,
    }


def merge(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """Move every face of one person onto another and delete the emptied row.

    MERGE MOVES FACES; IT DOES NOT RENAME (spec 8.2). The surviving person keeps their own
    label. It is destructive - one person stops existing - so the response names BOTH
    people and says so in a sentence the page can show verbatim.
    """
    person_id = _path_person_id(request)
    into = request.body.get("into")
    if isinstance(into, bool) or not isinstance(into, int):
        raise _bad_request("into", "must be the id of the person to merge into")
    if into == person_id:
        raise _bad_request("into", "a person cannot be merged into themselves")
    with server.connection() as conn:
        source = _person(conn, person_id)
        target = _person(conn, into)
        moved = repo.merge_person(conn, person_id, into)
        counts = progress(conn, _floor(server))
        conn.commit()
    return {
        **counts,
        "merged": {"id": source.id, "label": source.label},
        "into": {"id": target.id, "label": target.label},
        "faces": moved,
        "photographs": source.contents,
        "people": 2,
        "message": (
            f"Moved {moved} faces from {source.label} to {target.label}."
            f" {source.label} no longer exists as a separate person."
        ),
    }


def dismiss(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """UX 6's `Ignore` for an unnamed cluster: `review_state = 'dismissed'` on its faces.

    THE FACES WRITTEN ARE EXACTLY THE FACES THE PAGE SHOWED - `repo.cluster_faces` at A11's
    floor - written BY ID. A below-floor detection in the same cluster was never offered,
    so it is not dismissed, and the count reported is the count written. (`label`'s
    `set_person` applies the same floor since Task 11 closed Task 10's defect; this one
    writes by id so the refusal of a named cluster can read the rows first.)
    """
    cluster_id = _cluster_id(request)
    floor = _floor(server)
    with server.connection() as conn:
        # Spec 8.2: `Ignore` is for an UNNAMED cluster, so it acts on the unnamed faces
        # alone and a wholly named cluster is refused by name (S2-F1).
        faces = unnamed_or_refuse(conn, cluster_id, floor)
        touched = repo.set_review_state(conn, [face.id for face in faces], "dismissed")
        counts = progress(conn, floor)
        conn.commit()
    return {"cluster_id": cluster_id, **_changed(faces, touched), **counts}


def _face_ids(request: ApiRequest) -> list[int]:
    """`face_ids`: a non-empty list of whole numbers, de-duplicated in order. `True` is
    an `int` in Python and not a face id, so it is refused by name (`_item_ids`' trap)."""
    value = request.body.get("face_ids")
    if not isinstance(value, list) or not value:
        raise _bad_request("face_ids", "must be a non-empty list of face ids")
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, int):
            raise _bad_request("face_ids", f"{entry!r} is not a face id")
    return list(dict.fromkeys(value))


def _known_faces(conn: Any, face_ids: list[int]) -> list[FaceRow]:
    """Every face in the batch, or a 409 that names the unknown ids and writes nothing."""
    found = {face_id: repo.face_by_id(conn, face_id) for face_id in face_ids}
    unknown = [str(face_id) for face_id, face in found.items() if face is None]
    if unknown:
        raise _conflict(
            "pam changed nothing. It does not know these faces: "
            + ", ".join(unknown)
            + ". Reload the page."
        )
    return [face for face in found.values() if face is not None]


def _changed(
    faces: list[FaceRow], touched: int, extra_person: int | None = None
) -> dict[str, int]:
    """Spec 6.2 rule 7's three numbers: faces written, distinct contents, people affected."""
    people = {face.person_id for face in faces} | {extra_person}
    return {
        "faces": touched,
        "photographs": len({face.sha256 for face in faces}),
        "people": len(people - {None}),
    }


def _path_person_id(request: ApiRequest) -> int:
    raw = request.params["id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know a person with id {raw!r}")
    return int(raw)


def _person(conn: Any, person_id: int) -> PersonRow:
    for row in repo.persons_all(conn):
        if row.id == person_id:
            return row
    raise _not_found(f"pam does not know a person with id {person_id}")


def _nocase(text: str) -> str:
    """SQLite's `NOCASE`: ASCII letters only, which is what `upsert_person` matches with."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def _refuse_label_collision(conn: Any, person_id: int, label: str) -> None:
    for row in repo.persons_all(conn):
        if row.id != person_id and _nocase(row.label) == _nocase(label):
            raise _conflict(
                f"pam changed nothing. {row.label} is already a person; to make these"
                f" faces {row.label}'s, merge the two people instead of renaming."
            )


def _person_json(row: PersonRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "filename_label": row.filename_label,
        "filename_value": row.filename_value,
        "hidden": row.hidden,
    }


# Merged by `ReviewServer.__init__` beside `FACES_HANDLERS`, exactly as that table is.
FACES_MANAGE_HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("GET", "faces/ignored"): ignored,
    ("GET", "faces/person/{id}"): person,
    ("POST", "faces/faces/state"): set_state,
    ("POST", "faces/faces/assign"): assign,
    ("POST", "faces/person/{id}"): update_person,
    ("POST", "faces/person/{id}/merge"): merge,
    ("POST", "faces/cluster/{id}/dismiss"): dismiss,
}

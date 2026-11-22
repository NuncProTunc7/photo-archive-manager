"""Spec 8.2's `POST /faces/cluster/{id}/split` - UX 6's "Not the same person" (M11 Task 11
Step 4, amendment A18).

ONE ROUTE, THREE ACTIONS, because A18 lists one route. The body's `action` is `preview`
(re-cluster and PROPOSE), `confirm` (apply exactly what was previewed) or `undo` (put the
most recent confirmed split back). A FIFTH TABLE, merged beside the others in
`ReviewServer.__init__`, for the file budget (I16) alone.

WHICH EPSILON - THE MAINTAINER'S RULING OF 2026-09-12, "TIGHTER FIXED STEP". Spec 8.2 says the
response carries "the epsilon used" and no normative document says which one. Re-running
DBSCAN at the configured `cluster_epsilon` over a cluster that value produced mostly
reproduces the cluster, so the preview steps DOWN from it by a fixed factor a bounded number
of times, and stops at the first epsilon that yields at least two groups. If none does it
SAYS SO (A18's own sentence) and hands out no token: one group plus noise is not a split.

WHERE THE UNDO LIVES - THE MAINTAINER'S RULING, "SERVER MEMORY". I5 forbids a migration beyond
0007, so the prior membership is held on the running server (`SplitMemory`) and is lost
when it stops. One undo of the MOST RECENT split, not a stack. Both the previews and the
undo are read and written only inside `server.connection()`'s block: that lock is the one
lock, and a preview confirmed from two tabs at once must not apply twice.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.faces import FaceRow
from pam.enrich import cluster as dbscan_module
from pam.webui.api import ApiRequest, _bad_request, _conflict
from pam.webui.faces_api import _cluster_id, _floor, unnamed_or_refuse

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# The maintainer's "tighter fixed step" (2026-09-12); the two numbers are the controller's choice
# under that ruling. Four steps of 0.75 reach 0.32 of the configured epsilon - well below the
# value that grouped these faces, without descending to where every face is noise.
SPLIT_FACTOR = 0.75
SPLIT_MAX_STEPS = 4

ACTIONS = ("preview", "confirm", "undo")
TOKEN_BYTES = 8


@dataclass(frozen=True)
class Preview:
    """What a preview proposed, kept so a confirm applies exactly that and nothing else."""

    cluster_id: int
    face_ids: frozenset[int]
    groups: tuple[tuple[int, ...], ...]
    noise: tuple[int, ...]


@dataclass(frozen=True)
class Undo:
    """The most recent confirmed split: each face's prior id and the id the split gave it."""

    token: str
    cluster_id: int
    prior: dict[int, int | None]
    given: dict[int, int | None]


@dataclass
class SplitMemory:
    """Server memory for split (the maintainer's ruling). Touched only under the DB lock."""

    previews: dict[str, Preview] = field(default_factory=dict)
    undo: Undo | None = None


def split(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    action = request.body.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise _bad_request("action", "must be preview, confirm or undo")
    cluster_id = _cluster_id(request)
    if action == "preview":
        return _preview(server, cluster_id)
    token = request.body.get("token")
    if action == "confirm":
        return _confirm(server, cluster_id, token)
    return _undo(server, cluster_id, token)


def _preview(server: "ReviewServer", cluster_id: int) -> dict[str, Any]:
    configured = server.config.faces_cluster_epsilon
    if configured is None:
        raise _conflict(
            "pam changed nothing. It has no value for [enrich.faces] cluster_epsilon, so it"
            " cannot re-cluster these faces. Set it in pam.toml."
        )
    with server.connection() as conn:
        faces = _offered(server, conn, cluster_id)
        # A6's second half, as `pam faces cluster` applies it: vectors of different widths
        # cannot be compared, and DBSCAN over them is a 500 with numpy's words in it.
        if len({len(face.embedding) // dbscan_module.FLOAT32_BYTES for face in faces}) > 1:
            raise _conflict(
                "pam changed nothing. These faces hold embeddings of different widths, which"
                " cannot be compared with each other. Run: pam faces export, then pam faces"
                " purge, then pam enrich faces to detect again with one model."
            )
        vectors = {face.id: dbscan_module.decode_embedding(face.embedding) for face in faces}
        epsilon = configured
        groups: list[list[int]] = []
        noise: list[int] = []
        for _ in range(SPLIT_MAX_STEPS):
            epsilon *= SPLIT_FACTOR
            labels = dbscan_module.dbscan(
                vectors, epsilon=epsilon, min_samples=dbscan_module.MIN_SAMPLES
            )
            groups, noise = _partition(labels)
            if len(groups) >= 2:
                break
        by_id = {face.id: face for face in faces}
        if len(groups) < 2:
            return {
                "cluster_id": cluster_id,
                "separated": False,
                "epsilon": epsilon,
                "groups": [],
                "unclustered": _counted([], by_id),
                "token": None,
                "message": (
                    f"pam could not separate these {len(faces)} faces, even at a tighter"
                    " setting. It has not invented a split; nothing was changed."
                ),
            }
        token = secrets.token_hex(TOKEN_BYTES)
        memory = server.split_memory
        # One live preview per cluster: previewing again replaces the token it was given.
        for stale in [t for t, p in memory.previews.items() if p.cluster_id == cluster_id]:
            del memory.previews[stale]
        memory.previews[token] = Preview(
            cluster_id, frozenset(by_id), tuple(map(tuple, groups)), tuple(noise)
        )
    return {
        "cluster_id": cluster_id,
        "separated": True,
        "epsilon": epsilon,
        "groups": [_counted(group, by_id) for group in groups],
        "unclustered": _counted(noise, by_id),
        "token": token,
        "message": (
            f"pam can split these {len(faces)} faces into {len(groups)} groups;"
            f" {len(noise)} would be left out of any group."
        ),
    }


def _confirm(server: "ReviewServer", cluster_id: int, token: object) -> dict[str, Any]:
    with server.connection() as conn:
        memory = server.split_memory
        key = token if isinstance(token, str) else ""
        preview = memory.previews.get(key)
        if preview is None or preview.cluster_id != cluster_id:
            raise _conflict(
                "pam changed nothing. There is no preview of this split to confirm;"
                " ask for the proposal again first."
            )
        faces = _offered(server, conn, cluster_id)
        if frozenset(face.id for face in faces) != preview.face_ids:
            raise _conflict(
                "pam changed nothing. This group has changed since the proposal was made."
                " Reload the page."
            )
        top = repo.max_cluster_id(conn)
        given: dict[int, int | None] = dict.fromkeys(preview.noise)
        new_ids = []
        for offset, group in enumerate(preview.groups, start=1):
            new_ids.append(top + offset)
            given.update(dict.fromkeys(group, top + offset))
        repo.set_cluster(conn, sorted(given.items()))
        conn.commit()
        del memory.previews[key]
        undo = Undo(
            secrets.token_hex(TOKEN_BYTES),
            cluster_id,
            {face.id: face.cluster_id for face in faces},
            given,
        )
        memory.undo = undo
    return {
        "cluster_id": cluster_id,
        "cluster_ids": new_ids,
        "faces": len(faces),
        "photographs": len({face.sha256 for face in faces}),
        "people": 0,
        "unclustered": len(preview.noise),
        "undo": undo.token,
    }


def _undo(server: "ReviewServer", cluster_id: int, token: object) -> dict[str, Any]:
    with server.connection() as conn:
        memory = server.split_memory
        undo = memory.undo
        if undo is None or token != undo.token or undo.cluster_id != cluster_id:
            raise _conflict(
                "pam changed nothing. Only the most recent split can be undone, and only"
                " once, while pam faces label is still running."
            )
        faces = [repo.face_by_id(conn, face_id) for face_id in undo.given]
        if any(face is None or face.cluster_id != undo.given[face.id] for face in faces):
            raise _conflict(
                "pam changed nothing. Some of these faces have been moved since the split,"
                " so putting it back would undo that change too. Reload the page."
            )
        repo.set_cluster(conn, sorted(undo.prior.items()))
        conn.commit()
        memory.undo = None
    present = [face for face in faces if face is not None]
    return {
        "cluster_id": cluster_id,
        "faces": len(present),
        "photographs": len({face.sha256 for face in present}),
        "people": 0,
    }


def _offered(server: "ReviewServer", conn: Any, cluster_id: int) -> list[FaceRow]:
    """The UNNAMED faces the labeling page shows; a wholly named cluster is a 409 naming
    them, and nothing at all is a 404 (S2-F1: `faces_api.unnamed_or_refuse`)."""
    return unnamed_or_refuse(conn, cluster_id, _floor(server))


def _partition(labels: dict[int, int | None]) -> tuple[list[list[int]], list[int]]:
    """DBSCAN's labels as groups of face ids, in ascending order of each group's SMALLEST
    id - the order a confirm allocates fresh ids in - plus the noise faces."""
    groups: dict[int, list[int]] = {}
    for face_id in sorted(labels):
        number = labels[face_id]
        if number is not None:
            groups.setdefault(number, []).append(face_id)
    noise = sorted(face_id for face_id, number in labels.items() if number is None)
    return sorted(groups.values(), key=lambda group: group[0]), noise


def _counted(face_ids: list[int], by_id: dict[int, FaceRow]) -> dict[str, Any]:
    return {
        "face_ids": face_ids,
        "faces": len(face_ids),
        "photographs": len({by_id[face_id].sha256 for face_id in face_ids}),
    }


# Merged by `ReviewServer.__init__` beside `FACES_MANAGE_HANDLERS`, exactly as that table is.
FACES_SPLIT_HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("POST", "faces/cluster/{id}/split"): split,
}

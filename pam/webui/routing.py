"""The review server's route matcher, U10's unbound refusal, and `api/ping` (spec 8.2).

M14 Task 6a (review R1-F7): MOVED VERBATIM out of `webui/server.py`, which stood at 296 executable
lines against the ~300 budget with Task 6b's Content-Type / Origin / Host checks still to land in
it. A pure move: no behaviour changed and no name changed. `server.py` imports all three back (and
`CHOOSE_A_FOLDER`, which `_choose_a_folder` spells and `webui/launcher.py` imports from
`server`), so every importer of `server` is unchanged.
"""

from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.webui.api import CONFLICT_STATUS, ApiError, ApiRequest

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# M14 Task 4 Step 2 (U10): what every archive route answers while no folder is chosen.
CHOOSE_A_FOLDER = "Choose a folder first."


def _match_route(template: str, segments: list[str]) -> dict[str, str] | None:
    """`plan/{id}/items` against `['plan', '3', 'items']` -> `{'id': '3'}`, else None.

    Segment COUNT first, so `plan/{id}` and `plan/{id}/items` can never both match. A
    `{name}` segment captures whatever is there, INCLUDING a non-numeric id: the handler
    turns that into 404 "pam does not know a plan with id 'x'", which is a sentence, where
    a router that refused to match would give the silent empty 404 that spec 8.2 reserves
    for requests without the token.
    """
    parts = template.split("/")
    if len(parts) != len(segments):
        return None
    params: dict[str, str] = {}
    for part, segment in zip(parts, segments, strict=True):
        if part.startswith("{") and part.endswith("}"):
            params[part[1:-1]] = segment
        elif part != segment:
            return None
    return params


def _choose_a_folder() -> ApiError:
    return ApiError(CONFLICT_STATUS, CHOOSE_A_FOLDER)


def _api_ping(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """The one endpoint Task 7 ships, so routing is testable before the real API exists.

    It reads the plan's status back out of the catalog rather than reporting what the
    server was constructed with, which makes it a live proof of the per-request
    connection model on a worker thread - the thing `check_same_thread` would refuse if
    a connection were shared from the main thread.

    THE NO-PLAN BRANCH IS EXPLICIT AND THAT IS SPEC 8.2 REV 22 IN AS MANY WORDS: "a type
    change is not a test". `pam faces label` serves this same server with `plan_id = None`
    (A18 - a command whose whole job is corpus-wide people labeling must not refuse to
    start because no plan is open), and `repo.plan_status(conn, None)` DOES NOT RAISE:
    `WHERE id = NULL` matches nothing in SQLite and returns `None`. So the wrong version of
    this function is quiet - it reports `{"plan_id": null, "status": null}`, which is the
    right answer arrived at by a lookup that could never have produced any other, and which
    would go on being right if the query changed underneath it. The branch below means the
    `null` is a statement rather than a coincidence, and the catalog is not asked a question
    about a plan that does not exist.
    """
    # M14 Task 5d (W23): `folder` names the bound archive root, None when unbound.
    folder = str(server.workspace.archive_root) if server.bound else None
    # M14 Task 6 Stage 2, S2-F2: recovery's line from the last bind (U12). `pam start PATH` binds
    # before the page exists and the page boots from this ping, never posting `bind`, so without
    # it the line reached stderr only. A plain `ReviewServer` (`pam faces label`) never binds.
    recovery = getattr(server, "recovery", None)
    if server.plan_id is None:
        return {
            "plan_id": None,
            "status": None,
            "binding": server.binding,
            "folder": folder,
            "recovery": recovery,
        }
    with server.connection() as conn:
        status = repo.plan_status(conn, server.plan_id)
    return {
        "plan_id": server.plan_id,
        "status": status,
        "binding": server.binding,
        "folder": folder,
        "recovery": recovery,
    }

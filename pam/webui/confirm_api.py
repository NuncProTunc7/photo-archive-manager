"""`pam start`'s confirmation, rename and undo routes (design 8.2 U6, U14, U18; M14 Task 6b).

    GET  /api/plan/{id}/confirmation  -> U6's facts + a nonce
    POST /api/plan/{id}/apply         {nonce, folder_confirmed: true} -> the RENAME job
    GET  /api/session/{id}            -> one session's counts, `undoable` + an undo nonce
    POST /api/session/{id}/undo       {nonce} -> the UNDO job
    GET  /api/sessions                -> History: one row per rename session, newest first (Task 7)
    GET  /api/session/{id}/report     -> the session's report, as text (Task 7)

Registered by `StartServer` (`webui/launcher.py`), beside the job routes: the page renames through
a job. Since M14 Task 6c (ruling B1) `pam review` serves that same server, bound, so both commands
serve these routes (design 8.2 "Renaming and undoing from the page").

A SECOND TRIGGER, NOT A SECOND IMPLEMENTATION (U5, U19). The two POSTs start a job whose body is
`apply_locked` / `undo_locked` - the console's own bodies, in `webui/rename_jobs.py`, the one webui
module that may import them. Nothing here moves a file, journals a rename or decides what is renamed.

U14's THREE CONDITIONS on the two routes that move files, each beside the token gate (which is still
the authorization):

1. **Content-Type, Origin and Host** (`refused_before_parse`), checked by the router BEFORE the
   body is parsed: anything but `application/json`, an `Origin` that is not this server's own, or
   a `Host` that is not `127.0.0.1:<port>` is the token gate's empty 404.
2. **`folder_confirmed` is the JSON literal `true`** - `1`, `"true"` and a missing field are not.
3. **The nonce** the confirmation issued: the one most recently issued for that plan (a new
   confirmation replaces it), unused, and MARKED USED BEFORE THE JOB STARTS, so a replay - during
   the job or after it - renames nothing. The nonce is bound to `(plan id, archive root, digest)`;
   the job's confirmation (`apply_locked`'s `confirm`, after recovery and the preflight) compares
   the root AND the digest it recomputes. A False is a cancelled apply: nothing is written and the
   job ends `cancelled` with U14's sentence. The undo is the same shape: its job recomputes the
   session's digest under the held lock before `undo_locked` runs.

The nonces live in server memory (`Nonces`; `SplitMemory`'s precedent) and are not forgotten at a
change of folder: a nonce from another folder is refused by the ROOT comparison inside the job.

HISTORY (M14 Task 7, W22, X2) is two READS of the bound archive, bound-only and binding-checked like
`GET session/{id}` (the router's rule for every route not in `UNBOUND_ROUTES`, and `connection()`'s
second check inside the DB lock). A row's `Undo this rename` is Done's: `GET session/{id}` for the
nonce, then `POST session/{id}/undo`. No moving route is added. The REPORT is named by the session
id ALONE, validated against the shape `repo.next_session_id` writes and required to name a session
the journal knows; the path is `reports/session-<id>.md`, as `write_session_report` and `pam report
session` derive it - never a request path joined onto a directory (the `STATIC_FILES` rule).
"""

import re
import secrets
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pam.catalog import repo
from pam.errors import PamError
from pam.webui import jobs, rename_jobs
from pam.webui.api import (
    BAD_REQUEST_STATUS,
    CONFLICT_STATUS,
    NOT_FOUND_STATUS,
    ApiError,
    ApiRequest,
    RawResponse,
    _plan_id,
)
from pam.webui.decide_api import _refuse_unknown_names

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.launcher import StartServer
    from pam.webui.server import ReviewServer

# U6, word for word: the button is disabled until the box is ticked; a request without it says so.
TICK_FIRST = 'Tick "This is the right folder" first.'
# U14's sentence, defined beside the job that says it (`rename_jobs.CHANGED`).
CHANGED = rename_jobs.CHANGED

# The two routes that move files (U14).
APPLY_ROUTE = ("POST", "plan/{id}/apply")
UNDO_ROUTE = ("POST", "session/{id}/undo")
MOVING_ROUTES = frozenset({APPLY_ROUTE, UNDO_ROUTE})
APPLY_FIELDS = ("nonce", "folder_confirmed")
UNDO_FIELDS = ("nonce",)
JSON_MEDIA_TYPE = "application/json"
NONCE_BYTES = 16

# Task 7: a session id as `repo.next_session_id` writes it - `s-YYYYMMDD-HHMMSS`, and the `-N` a
# second run inside one second takes. ASCII digits only (`\d` would take any script's). Nothing
# that does not match names a report, so `..`, a separator or an empty id never reaches a path.
SESSION_ID = re.compile(
    r"s-([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})(?:-[0-9]+)?"
)
# The implementer's sentences, pending the design model.
NO_SUCH_SESSION = "pam has no rename session by that name in this folder."
NO_REPORT = "pam has no report for this session: it was never written, or it was deleted."
REPORT_TYPE = "text/plain; charset=utf-8"
REPORT_HEADERS = (("X-Content-Type-Options", "nosniff"),)
# AM2 (M15 Task 2): the session report's count line as `execute/reporter.py`'s
# `CLOUD_ONLY_REPORT_LINE` writes it - a SECOND, independent reading of that format (this module
# may not import `pam.execute`; `tests/test_apply_cloud_only_now.py` pins the two together). A
# skipped unit has no journal row, so History's count can come only from the report. Both counts
# are read (C6.5: History says TOGETHER when files > units, as the console and Done do).
CLOUD_ONLY_COUNT = re.compile(
    r"^- ([0-9,]+) file\(s\) in ([0-9,]+) group\(s\) not renamed because they became cloud-only$",
    re.MULTILINE,
)


def refused_before_parse(
    server: "ReviewServer", route: tuple[str, str], headers: Message
) -> bool:
    """U14 conditions 2-3: True when a moving route's request must be the empty 404.

    Called by the router after the route matched and BEFORE the body is parsed. Every other
    route answers False and is untouched.
    """
    if route not in MOVING_ROUTES:
        return False
    media = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    own = f"{server.host}:{server.port}"
    origin = headers.get("Origin")
    return (
        media != JSON_MEDIA_TYPE
        or (origin is not None and origin != f"http://{own}")
        or headers.get("Host") != own
    )


@dataclass
class _Issued:
    nonce: str
    root: Path
    digest: str
    used: bool = False


class Nonces:
    """The confirmation nonces one server issued: the newest per plan or session (U14)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._issued: dict[tuple[str, str], _Issued] = {}

    def issue(self, key: tuple[str, str], root: Path, digest: str) -> str:
        """A fresh nonce for `key`, REPLACING any earlier one."""
        nonce = secrets.token_hex(NONCE_BYTES)
        with self._lock:
            self._issued[key] = _Issued(nonce=nonce, root=root, digest=digest)
        return nonce

    def take(self, key: tuple[str, str], nonce: object) -> _Issued | None:
        """The issue `nonce` names, marked USED now; None unless it is current and unused."""
        if not isinstance(nonce, str):
            return None
        with self._lock:
            issued = self._issued.get(key)
            if issued is None or issued.used:
                return None
            if not secrets.compare_digest(issued.nonce.encode(), nonce.encode("utf-8")):
                return None
            issued.used = True
            return issued


def api_confirmation(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`GET /api/plan/{id}/confirmation`: U6's facts, computed now by the read-only preflight, and
    a nonce bound to `(plan id, archive root, digest)`. A plan `pam apply` would refuse is a 409
    with that sentence, and no nonce is issued."""
    start = cast("StartServer", server)
    plan_id = _plan_id(request)
    root = start.workspace.archive_root
    with start.connection() as conn:
        try:
            facts = rename_jobs.confirmation(conn, root, plan_id)
        except PamError as refusal:
            raise ApiError(CONFLICT_STATUS, str(refusal)) from None
        nonce = start.nonces.issue(("plan", str(plan_id)), root, facts.digest)
    return {
        "archive_root": str(facts.archive_root),
        "renames": facts.approved,
        "not_approved": facts.not_approved,
        "skipped_by_you": facts.skipped_by_you,
        "already_conforming": facts.already_conforming,
        "low_confidence": facts.low_confidence,
        "sync_provider": facts.sync_provider,
        "nonce": nonce,
    }


def api_apply(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/plan/{id}/apply {nonce, folder_confirmed: true}`: start the RENAME job."""
    start = cast("StartServer", server)
    plan_id = _plan_id(request)
    issued = _take(start, request.body, APPLY_FIELDS, ("plan", str(plan_id)))
    root = start.workspace.archive_root

    def page_confirm(facts: Any) -> bool:
        # U14: the folder AND the batch the nonce was issued for, recomputed in the preflight.
        return bool(facts.archive_root == issued.root and facts.digest == issued.digest)

    body = rename_jobs.renaming(root, plan_id, page_confirm, start.log_path)
    return {"job": start.jobs.start(jobs.RENAME, body)}


def api_session(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`GET /api/session/{id}`: the session's counts, whether it can be undone, an undo nonce.
    An unknown session is a 404 and an undo session a 409, each with the CLI's sentence."""
    start = cast("StartServer", server)
    session_id = request.params["id"]
    root = start.workspace.archive_root
    with start.connection() as conn:
        try:
            facts = rename_jobs.session_facts(conn, root, session_id)
        except PamError as refusal:
            status = NOT_FOUND_STATUS if not _known(conn, session_id) else CONFLICT_STATUS
            raise ApiError(status, str(refusal)) from None
        nonce = start.nonces.issue(("session", session_id), root, facts.digest)
    counts = facts.counts
    return {
        "session_id": session_id,
        "done": counts.done,
        "failed": counts.failed,
        "reverted": counts.reverted,
        "intent": counts.intent,
        "undone": counts.undone,
        "undoable": facts.undoable,
        "nonce": nonce,
    }


def api_undo(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/session/{id}/undo {nonce}`: start the UNDO job."""
    start = cast("StartServer", server)
    session_id = request.params["id"]
    issued = _take(start, request.body, UNDO_FIELDS, ("session", session_id))

    def undo_confirm(root: Path, digest: str) -> bool:
        return root == issued.root and digest == issued.digest

    body = rename_jobs.undoing(start.workspace.archive_root, session_id, undo_confirm)
    return {"job": start.jobs.start(jobs.UNDO, body)}


def _take(
    server: "StartServer",
    body: Mapping[str, Any],
    fields: tuple[str, ...],
    key: tuple[str, str],
) -> _Issued:
    """The body's fields checked, then the nonce taken - marked used here, before any job."""
    _refuse_unknown_names(body, fields)
    if "folder_confirmed" in fields and body.get("folder_confirmed") is not True:
        raise ApiError(BAD_REQUEST_STATUS, TICK_FIRST)
    issued = server.nonces.take(key, body.get("nonce"))
    if issued is None:
        raise ApiError(CONFLICT_STATUS, CHANGED)
    return issued


def _known(conn: sqlite3.Connection, session_id: str) -> bool:
    return bool(repo.session_rows(conn, session_id))


def api_sessions(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`GET /api/sessions`: History's rows (W22), newest first - one per RENAME session.

    An undo session is not a row: W22's `124 renamed · undone` is the apply row's state. Each row:
    `time`, the id's own clock (local: `pam apply` takes it from `datetime.now()`); `renamed`, the
    session's `done` rows; `undone`, every renamed file put back (its plan items `undone`, from
    the journal's join - never the plan's status); `undoable`, design 7.4's rule applied to THIS
    row (`done > undone`, X2), not only to the newest; `report`, whether its report file exists;
    `cloud_only`, AM2's count of files not renamed because they became cloud-only, read from the
    report because a skipped unit has no journal row (0 without a report, or without the line);
    `cloud_only_units`, the units those files make up, from the same line (C6.5).
    """
    start = cast("StartServer", server)
    with start.connection() as conn:
        known = repo.sessions(conn)
        reports = start.workspace.reports_dir
    rows = [_history_row(c, reports) for c in reversed(known) if c.ops == (repo.OP_RENAME,)]
    return {"sessions": rows}


def _history_row(counts: repo.SessionCounts, reports: Path) -> dict[str, Any]:
    path = _report_path(reports, counts.session_id)
    cloud_only, cloud_only_units = _cloud_only(path)
    return {
        "session_id": counts.session_id,
        "time": _session_time(counts.session_id),
        "renamed": counts.done,
        "undone": counts.done > 0 and counts.undone >= counts.done,
        "undoable": counts.done > counts.undone,
        "report": path is not None and path.is_file(),
        "cloud_only": cloud_only,
        "cloud_only_units": cloud_only_units,
    }


def _cloud_only(path: Path | None) -> tuple[int, int]:
    """AM2's (files, units), from the session's report: (0, 0) when there is none, or no line."""
    try:
        text = path.read_text(encoding="utf-8") if path is not None else ""
    except OSError:
        return (0, 0)
    found = CLOUD_ONLY_COUNT.search(text)
    if found is None:
        return (0, 0)
    return (int(found.group(1).replace(",", "")), int(found.group(2).replace(",", "")))


def _session_time(session_id: str) -> str | None:
    """`s-20261114-143205` -> `2026-11-14T14:32:05`, the LOCAL wall clock the id was written in."""
    shape = SESSION_ID.fullmatch(session_id)
    if shape is None:
        return None
    year, month, day, hour, minute, second = shape.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"


def _report_path(reports: Path, session_id: str) -> Path | None:
    """The report's path from the id alone, or None for an id of any other shape."""
    if SESSION_ID.fullmatch(session_id) is None:
        return None
    return reports / f"session-{session_id}.md"


def api_session_report(server: "ReviewServer", request: ApiRequest) -> RawResponse:
    """`GET /api/session/{id}/report`: the `.md` the rename wrote, as plain text (B4, W22).

    A malformed id and an id the journal does not know are both 404 with one sentence, before any
    path exists; a known session with no report file is 404 with another. Read inside the DB lock,
    so a change of folder between the check and the read is refused by `connection()`.
    """
    start = cast("StartServer", server)
    session_id = request.params["id"]
    with start.connection() as conn:
        path = _report_path(start.workspace.reports_dir, session_id)
        if path is None or not _known(conn, session_id):
            raise ApiError(NOT_FOUND_STATUS, NO_SUCH_SESSION)
        try:
            body = path.read_bytes()
        except OSError:
            raise ApiError(NOT_FOUND_STATUS, NO_REPORT) from None
    return RawResponse(REPORT_TYPE, body, REPORT_HEADERS)


CONFIRM_HANDLERS = {
    ("GET", "plan/{id}/confirmation"): api_confirmation,
    APPLY_ROUTE: api_apply,
    ("GET", "session/{id}"): api_session,
    UNDO_ROUTE: api_undo,
    ("GET", "sessions"): api_sessions,
    ("GET", "session/{id}/report"): api_session_report,
}

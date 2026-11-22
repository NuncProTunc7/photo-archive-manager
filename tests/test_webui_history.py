"""M14 Task 7: History and the session report (design 8.2, UX 5 W22, U14, X2; B4).

`GET /api/sessions` is History's rows: one per RENAME session, newest first, each with its time,
its `renamed` count, whether it was undone, whether it can STILL be undone (design 7.4's rule,
applied to every row - X2) and whether its report exists. `GET /api/session/{id}/report` is the
report the rename wrote, as plain text, named by the session id alone. Undo from History is Done's
undo: `GET session/{id}` for the nonce, `POST session/{id}/undo` - no new moving route.

EVERY EXPECTATION IS SPELLED FROM THE SPEC, NEVER READ OFF THE PRODUCTION PREDICATE (CLAUDE.md: an
oracle built from the definition it checks). The listing fixtures write their journal rows and
plan-item statuses BY HAND, in SQL, so the rule each row is held to - an apply session with
`done > undone` can still be undone; one whose every renamed file is back is `· undone`; an undo
session is not a row - is stated here, in the fixture, and nowhere borrowed from
`status.undoable_session`, `repo.sessions` or `confirm_api`. The undo from History is driven
through the real routes and watched at THE verified rename (M697).
"""

import pathlib

import pytest
from archives import FAKE_COMMAND, archive_names, catalog
from test_webui_jobs import await_state
from test_webui_rename import (
    HEIC,
    MOV,
    NEW,
    ORIGINAL,
    A,
    B,
    C,
    _applied,
    _bound,
    call,
    spy,
    started,
)

__all__ = ["spy", "started"]  # the two fixtures, imported for pytest

# The implementer's sentences (Task 7), pending the design model - spelled here, never imported.
NO_SUCH_SESSION = "pam has no rename session by that name in this folder."
NO_REPORT = "pam has no report for this session: it was never written, or it was deleted."
CHOOSE_A_FOLDER = "Choose a folder first."
REPORT_TYPE = "text/plain; charset=utf-8"

# Session ids as `pam apply` writes them: `s-YYYYMMDD-HHMMSS`, local time. Chosen so string order
# and time order agree, and so the second run inside one second carries the `-2` suffix.
OLDER = "s-20261113-091500"
NEWER = "s-20261114-143205"
NEWER_UNDO = "s-20261114-150000"
SAME_SECOND = "s-20261114-143205-2"


def _write(root, rows, statuses=(), plan_status=None):
    """Journal rows `(session, item, op, state)` and plan-item statuses `(item, status)`, by hand."""
    conn = catalog(root)
    try:
        for session, item, op, state in rows:
            conn.execute(
                "INSERT INTO journal (session_id, plan_item_id, op, old_rel_path, new_rel_path,"
                " size, mtime_ns, state, intent_at, resolved_at)"
                " VALUES (?, ?, ?, 'old', 'new', 1, 1, ?, '2026-11-14T10:00:00',"
                " '2026-11-14T10:00:01')",
                (session, item, op, state),
            )
        for item, status in statuses:
            conn.execute("UPDATE plan_items SET status = ? WHERE id = ?", (status, item))
        if plan_status is not None:
            conn.execute("UPDATE plans SET status = ? WHERE id = 1", (plan_status,))
        conn.commit()
    finally:
        conn.close()


def _sessions(server) -> list[dict]:
    reply = call(server, "sessions")
    assert reply.status == 200, reply
    return reply.json()["sessions"]


def _by_id(rows) -> dict[str, dict]:
    return {row["session_id"]: row for row in rows}


# --- the listing: the rule, spelled in the fixture (T7 2-6) ---------------------------------


def test_an_older_session_stays_undoable_after_the_newer_is_undone(tmp_path, started, spy):
    """X2, T7 (4): two apply sessions, the newer put back. The OLDER row still offers Undo; the
    newer is `undone` and offers none. The undo session is not a row (T7 2, 5). The plan's status
    stays `applied` while the newer session is fully undone: the flag is the journal's (T7 3)."""
    root = _bound(started, tmp_path)
    _write(
        root,
        [
            (OLDER, A, "rename", "done"),
            (OLDER, B, "rename", "done"),
            (NEWER, C, "rename", "done"),
            (NEWER, HEIC, "rename", "done"),
            (NEWER, MOV, "rename", "done"),
            (NEWER_UNDO, MOV, "undo_rename", "done"),
            (NEWER_UNDO, HEIC, "undo_rename", "done"),
            (NEWER_UNDO, C, "undo_rename", "done"),
        ],
        [(A, "applied"), (B, "applied"), (C, "undone"), (HEIC, "undone"), (MOV, "undone")],
        plan_status="applied",
    )

    rows = _sessions(started)

    assert [row["session_id"] for row in rows] == [NEWER, OLDER]
    assert rows == [
        {
            "session_id": NEWER,
            "time": "2026-11-14T14:32:05",
            "renamed": 3,
            "undone": True,
            "undoable": False,
            "report": False,
            # AM2 (M15 Task 2): no report, so no count - 0, never absent.
            "cloud_only": 0,
            "cloud_only_units": 0,
        },
        {
            "session_id": OLDER,
            "time": "2026-11-13T09:15:00",
            "renamed": 2,
            "undone": False,
            "undoable": True,
            "report": False,
            "cloud_only": 0,
            "cloud_only_units": 0,
        },
    ]
    assert spy == []


def test_the_undo_session_is_never_a_row(tmp_path, started, spy):
    """T7 (2, 5): one apply and the undo that put it back - ONE row, the apply's, `undone`."""
    root = _bound(started, tmp_path)
    _write(
        root,
        [(OLDER, A, "rename", "done"), (NEWER_UNDO, A, "undo_rename", "done")],
        [(A, "undone")],
    )

    rows = _sessions(started)

    assert [(r["session_id"], r["undone"], r["undoable"]) for r in rows] == [
        (OLDER, True, False)
    ]


def test_a_partially_undone_session_stays_undoable(tmp_path, started, spy):
    """T7 (6, 3): two files renamed; the undo put one back and was refused on the other. One file
    is still renamed, so the row is NOT `undone` and CAN still be undone (2 done > 1 undone) -
    even though the plan's status (written here to disagree) says `undone`."""
    root = _bound(started, tmp_path)
    _write(
        root,
        [
            (OLDER, A, "rename", "done"),
            (OLDER, B, "rename", "done"),
            (NEWER_UNDO, B, "undo_rename", "done"),
            (NEWER_UNDO, A, "undo_rename", "failed"),
        ],
        [(A, "applied"), (B, "undone")],
        plan_status="undone",
    )
    # The fixture checks ITSELF (Task 7 review, M3405): with the statuses unwritten this row would
    # read "renamed, never undone" and pass for the wrong reason. One file is back, one is not.
    conn = catalog(root)
    try:
        written = conn.execute(
            "SELECT id, status FROM plan_items WHERE id IN (?, ?) ORDER BY id", (A, B)
        ).fetchall()
    finally:
        conn.close()
    assert sorted((int(r["id"]), r["status"]) for r in written) == sorted(
        [(A, "applied"), (B, "undone")]
    )

    (row,) = _sessions(started)

    assert (row["session_id"], row["renamed"], row["undone"], row["undoable"]) == (
        OLDER,
        2,
        False,
        True,
    )


def test_a_session_that_renamed_nothing_is_neither_undone_nor_undoable(tmp_path, started, spy):
    """Every rename refused: `0 renamed`, nothing to put back, and no `· undone` claim."""
    root = _bound(started, tmp_path)
    _write(root, [(OLDER, A, "rename", "failed")], [(A, "failed")])

    (row,) = _sessions(started)

    assert (row["renamed"], row["undone"], row["undoable"]) == (0, False, False)


def test_the_rows_are_newest_first_and_a_same_second_run_follows_its_twin(
    tmp_path, started, spy
):
    root = _bound(started, tmp_path)
    _write(
        root,
        [
            (NEWER, C, "rename", "done"),
            (OLDER, A, "rename", "done"),
            (SAME_SECOND, B, "rename", "done"),
        ],
    )

    rows = _sessions(started)

    assert [r["session_id"] for r in rows] == [SAME_SECOND, NEWER, OLDER]
    # The suffix is not a time: the twin's clock is its first run's second.
    assert rows[0]["time"] == "2026-11-14T14:32:05"


def test_an_archive_with_no_sessions_lists_none(tmp_path, started, spy):
    _bound(started, tmp_path)
    assert call(started, "sessions").json() == {"sessions": []}


def test_an_unbound_server_lists_nothing(started):
    reply = call(started, "sessions")
    assert reply.status != 200 and reply.json() == {"error": CHOOSE_A_FOLDER}


def test_a_row_says_whether_its_report_exists(tmp_path, started, spy):
    root = _bound(started, tmp_path)
    _write(root, [(OLDER, A, "rename", "done"), (NEWER, B, "rename", "done")])
    # The report's path, spelled from design 8.2 / `pam report session`: reports/session-<id>.md.
    (started.workspace.root / "reports").mkdir(exist_ok=True)
    (started.workspace.root / "reports" / f"session-{NEWER}.md").write_text(
        "x", encoding="utf-8"
    )

    assert {r["session_id"]: r["report"] for r in _sessions(started)} == {
        NEWER: True,
        OLDER: False,
    }


# --- the report (T7-R4, T7 1) ---------------------------------------------------------------


def test_the_report_is_the_written_file_as_plain_text(tmp_path, started, spy, reads):
    root = _bound(started, tmp_path)
    session = _applied(started, root, (A,))["session"]
    written = started.workspace.root / "reports" / f"session-{session}.md"
    assert written.is_file()
    del reads[:]

    reply = call(started, f"session/{session}/report")

    # The `reads` spy's POSITIVE control (Task 7 review, M3406): it records the one read the route
    # makes, so its `reads == []` elsewhere cannot pass because the spy has gone blind.
    assert [p.resolve() for p in reads] == [written.resolve()]

    assert reply.status == 200
    assert reply.body == written.read_bytes()
    assert session in reply.body.decode("utf-8")
    assert _by_id(_sessions(started))[session]["report"] is True


def test_the_report_is_served_as_text_plain_utf8(tmp_path, started, spy):
    from http.client import HTTPConnection

    root = _bound(started, tmp_path)
    session = _applied(started, root, (A,))["session"]
    conn = HTTPConnection(started.host, started.port, timeout=30)
    try:
        conn.request("GET", f"/t/{started.token}/api/session/{session}/report")
        response = conn.getresponse()
        response.read()
        assert response.status == 200
        assert response.getheader("Content-Type") == REPORT_TYPE
        assert response.getheader("X-Content-Type-Options") == "nosniff"
    finally:
        conn.close()


@pytest.fixture
def reads(monkeypatch):
    """Every path any `Path.read_bytes` call reads while a test runs."""
    seen: list[pathlib.Path] = []
    real = pathlib.Path.read_bytes

    def watching(self):
        seen.append(self)
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", watching)
    return seen


BAD_IDS = (
    "..",
    "%2E%2E",
    "s-2026",
    "s-20261114-143205.md",
    "S-20261114-143205",
    "s-20261114-1432051",
    "session-s-20261114-143205",
)
assert len(BAD_IDS) >= 7


def test_a_session_id_with_dotdot_is_refused(tmp_path, started, spy, reads):
    """T7 (1): `..` is refused with the sentence, and NO file is read - least of all one outside
    the reports directory. A file is planted beside the reports directory to be tempting."""
    root = _bound(started, tmp_path)
    _write(root, [(OLDER, A, "rename", "done")])
    (started.workspace.root / "reports").mkdir(exist_ok=True)
    (started.workspace.root / "session-...md").write_text("outside", encoding="utf-8")
    (started.workspace.root / "..md").write_text("outside", encoding="utf-8")
    del reads[:]

    reply = call(started, "session/../report")

    assert (reply.status, reply.json()) == (404, {"error": NO_SUCH_SESSION})
    reports = (started.workspace.root / "reports").resolve()
    assert [p for p in reads if p.resolve().parent != reports] == []
    assert reads == []


# Ids that must name NO path at all (Task 7 review, M3383) - written for this test, independent of
# BAD_IDS: a traversal by either separator, an absolute path, a suffix that is not a number, and a
# very long one. A loosened shape check would let each reach a path, leaving the route's
# known-session check alone behind the module's "never reaches a path" sentence.
NO_PATH_IDS = (
    "..",
    "s-20261114-143205\\..\\..\\outside",
    "s-20261114-143205/../../outside",
    "C:\\Windows\\win.ini",
    "/etc/passwd",
    "s-20261114-143205-2.md",
    "s-20261114-143205-x",
    "s-20261114-143205-" + "9" * 300 + "x",
)
assert len(NO_PATH_IDS) >= 8


@pytest.mark.parametrize("bad", NO_PATH_IDS)
def test_an_id_of_another_shape_names_no_report_path(tmp_path, bad):
    from pam.webui.confirm_api import _report_path

    assert _report_path(tmp_path / "reports", bad) is None


def test_an_id_of_the_written_shape_names_its_report_inside_the_reports_directory(tmp_path):
    """The `-N` a second run inside one second takes is part of the shape (`next_session_id`)."""
    from pam.webui.confirm_api import _report_path

    reports = tmp_path / "reports"
    for session in (OLDER, SAME_SECOND, "s-20261114-143205-12"):
        assert _report_path(reports, session) == reports / f"session-{session}.md"


@pytest.mark.parametrize("bad", BAD_IDS)
def test_a_malformed_session_id_is_refused_before_any_read(tmp_path, started, spy, reads, bad):
    _bound(started, tmp_path)
    del reads[:]
    reply = call(started, f"session/{bad}/report")
    assert (reply.status, reply.json()) == (404, {"error": NO_SUCH_SESSION})
    assert reads == []


def test_an_id_in_another_script_s_digits_is_refused_before_any_read(
    tmp_path, started, spy, reads
):
    r"""`\d` would take Arabic-Indic digits; the shape is ASCII. HTTP cannot carry the raw id
    (the router never unquotes), so the handler is called with it directly."""
    from pam.webui.api import ApiError, ApiRequest
    from pam.webui.confirm_api import api_session_report

    _bound(started, tmp_path)
    del reads[:]
    request = ApiRequest(params={"id": "s-٢٠٢٦١١١٤-143205"}, query={}, body={})
    with pytest.raises(ApiError) as refused:
        api_session_report(started, request)
    assert (refused.value.status, refused.value.message) == (404, NO_SUCH_SESSION)
    assert reads == []


def test_a_well_formed_id_no_session_has_is_refused(tmp_path, started, spy, reads):
    _bound(started, tmp_path)
    # A report file with no session behind it: the journal, not the directory, says what exists.
    (started.workspace.root / "reports").mkdir(exist_ok=True)
    (started.workspace.root / "reports" / f"session-{OLDER}.md").write_text(
        "x", encoding="utf-8"
    )
    del reads[:]
    reply = call(started, f"session/{OLDER}/report")
    assert (reply.status, reply.json()) == (404, {"error": NO_SUCH_SESSION})
    assert reads == []


def test_a_session_with_no_report_gets_a_plain_refusal(tmp_path, started, spy):
    """A session written before reports existed, or whose report was deleted: a sentence, not a
    500."""
    root = _bound(started, tmp_path)
    _write(root, [(OLDER, A, "rename", "done")])
    reply = call(started, f"session/{OLDER}/report")
    assert (reply.status, reply.json()) == (404, {"error": NO_REPORT})


def test_the_report_route_never_renames(tmp_path, started, spy):
    root = _bound(started, tmp_path)
    session = _applied(started, root, (A,))["session"]
    del spy[:]
    assert call(started, f"session/{session}/report").status == 200
    assert call(started, "sessions").status == 200
    assert spy == []
    assert archive_names(root) == sorted([NEW["A.JPG"], *ORIGINAL[1:]])


# --- undo from History: Done's routes, the files back (X2) ----------------------------------


def _plan2_item(root, name) -> int:
    conn = catalog(root)
    try:
        row = conn.execute(
            "SELECT id FROM plan_items WHERE plan_id = 2 AND old_rel_path = ?", (name,)
        ).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def test_undo_from_history_renames_the_files_back(tmp_path, started, spy):
    """Apply plan 1 (A), read again, apply plan 2 (B), undo plan 2's session from Done; then the
    OLDER row still offers Undo, and its session id drives Done's own undo: A comes back, and
    only A is renamed by it."""
    root = _bound(started, tmp_path)
    first = _applied(started, root, (A,))["session"]
    started.exiftool_command = FAKE_COMMAND
    assert call(started, "job/read", "POST", {}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    b = _plan2_item(root, "B.JPG")
    assert call(started, "plan/2/decide", "POST", {"action": "approve", "item_ids": [b]}).status
    nonce = call(started, "plan/2/confirmation").json()["nonce"]
    body = {"nonce": nonce, "folder_confirmed": True}
    assert call(started, "plan/2/apply", "POST", body).status == 200
    second = await_state(started, "done", "failed")["session"]
    assert second not in (None, first)
    undo_nonce = call(started, f"session/{second}").json()["nonce"]
    assert call(started, f"session/{second}/undo", "POST", {"nonce": undo_nonce}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    assert archive_names(root) == sorted([NEW["A.JPG"], *ORIGINAL[1:]])

    rows = _sessions(started)
    assert [(r["session_id"], r["undone"], r["undoable"]) for r in rows] == [
        (second, True, False),
        (first, False, True),
    ]
    del spy[:]
    older = rows[1]["session_id"]
    nonce = call(started, f"session/{older}").json()["nonce"]
    assert call(started, f"session/{older}/undo", "POST", {"nonce": nonce}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"

    assert spy == [(NEW["A.JPG"], "A.JPG")]
    assert archive_names(root) == ORIGINAL
    assert [(r["undone"], r["undoable"]) for r in _sessions(started)] == [
        (True, False),
        (True, False),
    ]

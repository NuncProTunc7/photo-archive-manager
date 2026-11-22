"""M14 Task 6b: confirm, rename and undo from the page (design 8.2 U6, U11, U14, U18, U19).

The page is a second TRIGGER of `pam apply` / `pam undo`, never a second implementation: its two
POST routes start a job whose body is `apply_locked` / `undo_locked`, under the server's held lock.

WATCH THE DANGEROUS CALL ITSELF (CLAUDE.md, M697). Every refusal below asserts that the one
verified rename, `perform_verified_rename` (its only call site is `execute/unit.py`), was CALLED
ZERO TIMES - not only that the names on disk are unchanged. A refusal that renamed and then
reverted would leave the disk and the catalog looking untouched (the plan's mutation 15).

EVERY EXPECTATION IS SPELLED BY HAND: the plan items, the new names, the counts and every sentence
are written out from the fixture and from U6 / U11 / U14 / the CLI's copy - never read back from
the code under test. Every wait is a barrier with a deadline, never a sleep.
"""

import json
import os
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection
from typing import NamedTuple

import pytest
from archives import FAKE_COMMAND, archive_names, catalog, put
from test_webui_jobs import DEADLINE, Blocked, await_state

from pam.execute import unit as unit_mod
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan
from pam.webui import launcher, rename_jobs
from pam.webui.launcher import StartServer, start_server

# --- the fixture, by hand -------------------------------------------------------------------
#
# Four units: three singles and a Live Photo pair. One date each (the fake exiftool reads it), so
# every proposed name is the default template's `<date>_001.<EXT>`. Plan item ids follow the
# planner's rel_path order: A, B, C, then the pair (one unit, keyed by its primary, the HEIC).
FILES = {
    "A.JPG": "2020:08:05 09:15:00",
    "B.JPG": "2021:09:06 11:02:03",
    "C.JPG": "2022:10:07 12:00:00",
    "IMG_1.HEIC": "2019:07:04 18:23:11",
    "IMG_1.MOV": "2019:07:04 18:23:11",
}
A, B, C, HEIC, MOV = 1, 2, 3, 4, 5
NEW = {
    "A.JPG": "2020-08-05_001.JPG",
    "B.JPG": "2021-09-06_001.JPG",
    "C.JPG": "2022-10-07_001.JPG",
    "IMG_1.HEIC": "2019-07-04_001.HEIC",
    "IMG_1.MOV": "2019-07-04_001.MOV",
}
ORIGINAL = ["A.JPG", "B.JPG", "C.JPG", "IMG_1.HEIC", "IMG_1.MOV"]
PLAN = 1

# U14, U6, U11 and the CLI's copy, word for word - spelled here, never imported.
CHANGED = "Something changed since you looked. Check the names again."
TICK_FIRST = 'Tick "This is the right folder" first.'
BUSY_READING = "pam is busy reading your photos. Try again when it finishes."
BUSY_RENAMING = "pam is busy renaming. Try again when it finishes."
NOTHING_APPROVED = (
    "Nothing in plan 1 is approved. Run pam review 1 to approve names,"
    " or pam apply 1 --all to accept everything except low-confidence names."
)
SUPERSEDED = "Plan 1 was superseded by a newer plan. Run: pam plan"
STALE_PEOPLE = (
    "{old} was left alone: pam can no longer confirm the people named in its new"
    " name - a person may have been hidden, renamed, removed or merged since this plan was"
    " made - so pam will not write that name. Run: pam plan"
)
REDO = (
    "Session {sid} is itself an undo, and pam cannot redo one.\n"
    "The files are already under their original names. To rename them again, run:"
    " pam plan, then pam apply."
)
NOTHING_TO_UNDO = (
    "pam has not applied anything to this folder yet, so there is nothing to undo:\n"
    "  {root}\nRun: pam plan, then pam apply <plan id>"
)
# recovery's own sentence after `crash:after_intent:1` (`recover._announcement`), spelled.
RECOVERED = "pam finished tidying up after an interrupted run: 1 rename(s) not started."
STOPPED_WHILE_RENAMING = (
    "pam stopped while renaming. Start pam again and it will put things right first."
)


class Reply(NamedTuple):
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body)


def _quiet():
    import logging

    logger = logging.getLogger("pam.tests.webui_rename")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def started() -> Iterator[StartServer]:
    """`pam start`'s server, unbound and serving. Teardown stops it and releases its lock."""
    server = start_server(_quiet())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=DEADLINE)


@pytest.fixture
def spy(monkeypatch):
    """Every (old, new) handed to THE verified rename: the CALL is watched (M697)."""
    calls: list[tuple[str, str]] = []
    real = unit_mod.perform_verified_rename

    def watching(*args, **kwargs):
        calls.append((kwargs["old_rel"], kwargs["new_rel"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", watching)
    return calls


def call(server, path, method="GET", body=None, *, raw=None, headers=None) -> Reply:
    """One request. A POST carries JSON, the binding and the server's own Origin unless a test
    overrides a header (`None` drops it)."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    sent = {}
    if method == "POST":
        sent = {
            "Content-Type": "application/json",
            "X-Pam-Binding": str(server.binding),
            "Origin": f"http://127.0.0.1:{server.port}",
        }
    for name, value in (headers or {}).items():
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    conn = HTTPConnection(server.host, server.port, timeout=DEADLINE)
    try:
        conn.request(method, f"/t/{server.token}/api/{path}", body=data, headers=sent)
        response = conn.getresponse()
        return Reply(response.status, response.read())
    finally:
        conn.close()


def _archive(base):
    """FILES on disk, scanned and planned: plan 1, every item undecided."""
    root = base / "photos"
    root.mkdir(parents=True)
    for name, date in FILES.items():
        put(root, name, date)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert run_plan(root).plan_id == PLAN
    return root


def _bound(server, base):
    root = _archive(base)
    launcher.bind(server, root)
    return root


def _decide(server, ids, action="approve"):
    reply = call(server, f"plan/{PLAN}/decide", "POST", {"action": action, "item_ids": ids})
    assert reply.status == 200, reply


def _confirmation(server) -> dict:
    reply = call(server, f"plan/{PLAN}/confirmation")
    assert reply.status == 200, reply
    return reply.json()


def _apply(server, nonce, **headers) -> Reply:
    body = {"nonce": nonce, "folder_confirmed": True}
    return call(server, f"plan/{PLAN}/apply", "POST", body, headers=headers)


def _job_number(server) -> int:
    return server.jobs._count


def _renamed(*names):
    return [(name, NEW[name]) for name in names]


def _after(root, *renamed):
    return sorted(NEW[n] if n in renamed else n for n in ORIGINAL)


def _plan_status(root):
    conn = catalog(root)
    try:
        return conn.execute("SELECT status FROM plans WHERE id = ?", (PLAN,)).fetchone()[0]
    finally:
        conn.close()


def _applied(server, root, ids=(A,)):
    """Approve `ids` on the page, confirm and apply; the finished job's snapshot."""
    _decide(server, list(ids))
    reply = _apply(server, _confirmation(server)["nonce"])
    assert reply.status == 200, reply
    return await_state(server, "done", "failed", "cancelled")


# --- the confirmation (Step 3, U6) ------------------------------------------------------------


def test_the_confirmation_states_u6_s_facts_and_a_new_one_replaces_the_nonce(
    tmp_path, started, spy
):
    """U6's facts from the read-only preflight: 3 files get new names (A and the pair), 2 left
    undecided (B, C), 0 skipped, 0 already conforming, 0 low, no sync folder. A second
    confirmation REPLACES the first nonce: the first is refused, the second renames."""
    root = _bound(started, tmp_path)
    _decide(started, [A, HEIC])
    first = _confirmation(started)
    second = _confirmation(started)
    nonce = second.pop("nonce")
    assert len(nonce) == 32 and nonce != first["nonce"]
    assert second == {
        "archive_root": str(root.resolve()),
        "renames": 3,
        "not_approved": 2,
        "skipped_by_you": 0,
        "already_conforming": 0,
        "low_confidence": 0,
        "sync_provider": None,
    }
    assert _plan_status(root) == "open"  # the confirmation wrote nothing

    stale = _apply(started, first["nonce"])
    assert (stale.status, stale.json()) == (409, {"error": CHANGED})
    assert spy == [] and _job_number(started) == 0

    assert _apply(started, nonce).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    assert sorted(spy) == sorted(_renamed("A.JPG", "IMG_1.HEIC", "IMG_1.MOV"))


def test_a_plan_with_nothing_approved_has_no_confirmation(tmp_path, started, spy):
    root = _bound(started, tmp_path)
    reply = call(started, f"plan/{PLAN}/confirmation")
    assert (reply.status, reply.json()) == (409, {"error": NOTHING_APPROVED})
    assert spy == [] and archive_names(root) == ORIGINAL


# --- the apply route (Step 4, U14) and its mutations ------------------------------------------


def test_the_page_renames_exactly_what_the_page_approved(tmp_path, started, spy):
    """M13 F1's page twin (mutation 9). Approve A through `decide` and the pair through
    `decide-selection`, leave B and C undecided: the confirmation says 3, and the one verified
    rename sees exactly those three files, nothing else."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    selection = {"selection": {"view": {}, "ids": [HEIC]}, "action": "approve"}
    assert call(started, f"plan/{PLAN}/decide-selection", "POST", selection).status == 200
    facts = _confirmation(started)
    assert (facts["renames"], facts["not_approved"]) == (3, 2)

    assert _apply(started, facts["nonce"]).status == 200
    done = await_state(started, "done", "failed")

    assert done["state"] == "done" and done["lines"] == []
    assert sorted(spy) == sorted(_renamed("A.JPG", "IMG_1.HEIC", "IMG_1.MOV"))
    assert archive_names(root) == _after(root, "A.JPG", "IMG_1.HEIC", "IMG_1.MOV")


def test_a_page_rename_reports_its_progress_per_unit_and_its_own_counts(
    tmp_path, started, spy, monkeypatch
):
    """The controller's rulings R6-1 and R6-2 (M14 Task 6), which the Done screen (W20, U22) and
    the progress line (`Renaming… 3 of 3`) stand on. Approve A (a single) and the HEIC+MOV pair:
    two units, three files, two left undecided (B, C). The job reports progress ONCE PER UNIT, of
    the 3 actionable files, ending at 3 of 3; its `result` is the run's own summary - 3 renamed,
    0 failed, 2 not approved - with the seconds it took. Every number is spelled from the fixture."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    selection = {"selection": {"view": {}, "ids": [HEIC]}, "action": "approve"}
    assert call(started, f"plan/{PLAN}/decide-selection", "POST", selection).status == 200
    seen: list[tuple[int, int]] = []
    real = started.jobs._progress
    monkeypatch.setattr(
        started.jobs,
        "_progress",
        lambda done, total: (seen.append((done, total)), real(done, total)),
    )
    assert _apply(started, _confirmation(started)["nonce"]).status == 200
    done = await_state(started, "done", "failed")

    assert len(seen) == 2, f"one report per unit, two units: {seen}"
    assert seen[-1] == (3, 3) and all(total == 3 for _, total in seen)
    assert seen[0][0] in (1, 2) and seen[0][0] < seen[1][0]
    assert (done["done"], done["total"]) == (3, 3)
    result = dict(done["result"])
    elapsed = result.pop("elapsed_s")
    # AM2 (M15 Task 2): no unit was cloud-only, so both of its counts are 0, never absent.
    assert result == {
        "renamed": 3,
        "failed": 0,
        "not_approved": 2,
        "cloud_only": 0,
        "cloud_only_units": 0,
    }
    assert isinstance(elapsed, float) and elapsed >= 0.0
    assert archive_names(root) == _after(root, "A.JPG", "IMG_1.HEIC", "IMG_1.MOV")


def test_a_changed_decision_after_confirm_renames_nothing(tmp_path, started, spy):
    """Mutations 1, 2 (digest operand) and 8. One more row approved between the confirmation and
    the button: the job's preflight recomputes the digest, the apply is CANCELLED - nothing
    written, U14's sentence - and the verified rename is never called."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    _decide(started, [B])

    assert _apply(started, nonce).status == 200
    ended = await_state(started, "done", "failed", "cancelled")

    assert (ended["state"], ended["lines"], ended["session"]) == ("cancelled", [CHANGED], None)
    assert spy == []
    assert archive_names(root) == ORIGINAL and _plan_status(root) == "open"


def test_a_nonce_for_another_folder_is_refused(tmp_path, started, spy):
    """Mutation 2 (root operand). Two folders built identically - the same plan id, the same
    items, the same new names, so the same digest. Confirm on A, change folder to B, press the
    button with A's nonce: the job's root is B's, the nonce's is A's, and nothing is renamed."""
    a = _bound(started, tmp_path / "a")
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    b = _bound(started, tmp_path / "b")
    _decide(started, [A])  # B's plan approved exactly as A's was: only the folder differs

    assert _apply(started, nonce).status == 200
    ended = await_state(started, "done", "failed", "cancelled")

    assert (ended["state"], ended["lines"]) == ("cancelled", [CHANGED])
    assert spy == []
    assert archive_names(a) == ORIGINAL and archive_names(b) == ORIGINAL


def test_a_replayed_apply_renames_nothing(tmp_path, started, spy):
    """Mutation 3. The nonce is single-use: the replay is a 409 and starts no job."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    assert _apply(started, nonce).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    assert spy == _renamed("A.JPG")

    replay = _apply(started, nonce)

    assert (replay.status, replay.json()) == (409, {"error": CHANGED})
    assert _job_number(started) == 1
    assert spy == _renamed("A.JPG")
    assert archive_names(root) == _after(root, "A.JPG")


def test_a_replay_during_the_job_renames_nothing(tmp_path, started, spy, monkeypatch):
    """Mutation 4. A barrier holds the job at its very start; the replay arrives then, and again
    after the job has ended. Neither starts a job, and the rename sees A once."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    entered, release = threading.Event(), threading.Event()
    real_renaming = rename_jobs.renaming

    def held_at_start(*args):
        body = real_renaming(*args)

        def waiting(*a):
            entered.set()
            assert release.wait(DEADLINE), "the test never released the job"
            return body(*a)

        return waiting

    monkeypatch.setattr(rename_jobs, "renaming", held_at_start)
    assert _apply(started, nonce).status == 200
    assert entered.wait(DEADLINE)

    during = _apply(started, nonce)
    release.set()
    assert await_state(started, "done", "failed")["state"] == "done"
    after = _apply(started, nonce)

    assert (during.status, during.json()) == (409, {"error": BUSY_RENAMING})
    assert (after.status, after.json()) == (409, {"error": CHANGED})
    assert _job_number(started) == 1
    assert spy == _renamed("A.JPG")
    assert archive_names(root) == _after(root, "A.JPG")


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
# Written by hand from U14 ("must be the JSON literal true"), NOT from the code: each is a value
# a careless truthiness check would accept, or no value at all.
NOT_THE_LITERAL_TRUE = [1, "true", "yes", MISSING]
assert NOT_THE_LITERAL_TRUE, "an empty list would make the parametrized control vanish"


@pytest.mark.parametrize("value", NOT_THE_LITERAL_TRUE)
def test_folder_confirmed_must_be_the_literal_true(tmp_path, started, spy, value):
    """Mutation 5. Refused before the nonce is taken: the same nonce then renames."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    body = {"nonce": nonce} if value is MISSING else {"nonce": nonce, "folder_confirmed": value}

    reply = call(started, f"plan/{PLAN}/apply", "POST", body)

    assert (reply.status, reply.json()) == (400, {"error": TICK_FIRST})
    assert spy == [] and _job_number(started) == 0
    assert archive_names(root) == ORIGINAL
    assert _apply(started, nonce).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    assert spy == _renamed("A.JPG")


def _ready(server, tmp_path, route):
    """A request to `route` with every U14 condition right: (root, path, body). For `undo`, A has
    been renamed by a page apply first."""
    root = _bound(server, tmp_path)
    if route == "apply":
        _decide(server, [A])
        nonce = _confirmation(server)["nonce"]
        return root, f"plan/{PLAN}/apply", {"nonce": nonce, "folder_confirmed": True}
    session = _applied(server, root)["session"]
    reply = call(server, f"session/{session}")
    assert reply.status == 200, reply
    return root, f"session/{session}/undo", {"nonce": reply.json()["nonce"]}


MOVING = ["apply", "undo"]
assert MOVING


def _refused_then_served(server, spy, path, body, **headers):
    """The bad request is the empty 404, calls nothing and starts nothing; the same nonce, sent
    right, is then served - so the refusal came before the handler."""
    before, jobs_before = list(spy), _job_number(server)
    reply = call(server, path, "POST", body, headers=headers)
    assert (reply.status, reply.body) == (404, b"")
    assert spy == before and _job_number(server) == jobs_before
    assert call(server, path, "POST", body).status == 200
    assert await_state(server, "done", "failed")["state"] == "done"


@pytest.mark.parametrize("route", MOVING)
def test_a_form_post_is_the_empty_404(tmp_path, started, spy, route):
    """Mutation 6 (Content-Type operand). Everything else right: the nonce, the binding, Origin
    and Host. The body is even valid JSON; only the declared type is a form's."""
    _root, path, body = _ready(started, tmp_path, route)
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    _refused_then_served(started, spy, path, body, **form)


@pytest.mark.parametrize("route", MOVING)
def test_a_foreign_origin_is_the_empty_404(tmp_path, started, spy, route):
    """Mutation 6 (Origin operand). A page in another tab sends its own Origin."""
    _root, path, body = _ready(started, tmp_path, route)
    _refused_then_served(started, spy, path, body, Origin="http://example.invalid")


@pytest.mark.parametrize("route", MOVING)
def test_a_rebound_host_is_the_empty_404(tmp_path, started, spy, route):
    """Mutation 6 (Host operand). DNS rebinding: a name that resolves to 127.0.0.1."""
    _root, path, body = _ready(started, tmp_path, route)
    _refused_then_served(
        started, spy, path, body, Host=f"rebound.invalid:{started.port}", Origin=None
    )


def test_get_never_renames(tmp_path, started, spy):
    """Mutation 7. A GET on either moving route - nonce and all in the query - is the empty 404."""
    root, path, body = _ready(started, tmp_path, "apply")
    query = f"{path}?nonce={body['nonce']}&folder_confirmed=true"
    assert call(started, query) == Reply(404, b"")
    assert call(started, path) == Reply(404, b"")
    assert spy == [] and _job_number(started) == 0
    assert archive_names(root) == ORIGINAL


def test_get_never_undoes(tmp_path, started, spy):
    root, path, body = _ready(started, tmp_path, "undo")
    before = list(spy)
    assert call(started, f"{path}?nonce={body['nonce']}") == Reply(404, b"")
    assert spy == before and _job_number(started) == 1
    assert archive_names(root) == _after(root, "A.JPG")


def test_a_refused_apply_ends_the_job_failed_with_the_refusal_s_sentence(
    tmp_path, started, spy
):
    """Nothing approved any more when the button is pressed: `validate_plan`'s whole-plan
    refusal, before the confirmation, so the job ends `failed` with `pam apply`'s sentence."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    _decide(started, [A], "reset")

    assert _apply(started, nonce).status == 200
    ended = await_state(started, "done", "failed", "cancelled")

    assert (ended["state"], ended["lines"]) == ("failed", [NOTHING_APPROVED])
    assert spy == [] and archive_names(root) == ORIGINAL


def test_an_apply_of_a_plan_a_read_superseded_ends_failed_with_the_sentence(
    tmp_path, started, spy
):
    root = _bound(started, tmp_path)
    started.exiftool_command = FAKE_COMMAND
    _decide(started, [A])
    nonce = _confirmation(started)["nonce"]
    assert call(started, "job/read", "POST", {}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"

    assert _apply(started, nonce).status == 200
    ended = await_state(started, "failed")

    assert ended["lines"] == [SUPERSEDED]
    assert spy == [] and archive_names(root) == ORIGINAL


def test_a_j13_refusal_reaches_the_job_s_lines_and_renames_nothing(tmp_path, started, spy):
    """J13, per item: a `tokens_json` pam cannot read is a name it cannot confirm. The refusal is
    the preflight's, so the confirmation already counts 0 renames and the job ends `done` with
    `pam apply`'s sentence as its line."""
    root = _bound(started, tmp_path)
    _decide(started, [A])
    conn = catalog(root)
    try:
        conn.execute("UPDATE plan_items SET tokens_json = '{not json' WHERE id = ?", (A,))
        conn.commit()
    finally:
        conn.close()
    facts = _confirmation(started)
    assert facts["renames"] == 0

    assert _apply(started, facts["nonce"]).status == 200
    ended = await_state(started, "done", "failed")

    assert (ended["state"], ended["lines"]) == ("done", [STALE_PEOPLE.format(old="A.JPG")])
    # R6-2 where the counts DISAGREE (Stage 1 review, M3300/M3301): A was approved and refused,
    # so 0 renamed, 1 failed, and B, C and the pair (4 items) never approved; nothing actionable,
    # so no progress was reported (0 of 0). Spelled from the fixture.
    result = dict(ended["result"])
    result.pop("elapsed_s")
    assert result == {
        "renamed": 0,
        "failed": 1,
        "not_approved": 4,
        "cloud_only": 0,
        "cloud_only_units": 0,
    }
    assert (ended["done"], ended["total"]) == (0, 0)
    assert spy == [] and archive_names(root) == ORIGINAL


# --- the busy refusal (U11, mutation 13) -------------------------------------------------------


def test_rename_is_refused_while_reading(tmp_path, started, spy):
    """While a read runs, both moving routes are U11's 409 and take no nonce; while a rename
    runs, a read is refused too."""
    root, path, body = _ready(started, tmp_path, "apply")
    reading = Blocked()
    reading.start(started, "read")

    refused = call(started, path, "POST", body)
    reading.release.set()
    assert await_state(started, "done")["state"] == "done"

    assert (refused.status, refused.json()) == (409, {"error": BUSY_READING})
    assert spy == []
    assert archive_names(root) == ORIGINAL

    renaming = Blocked()
    renaming.start(started, "rename")
    read = call(started, "job/read", "POST", {})
    renaming.release.set()
    await_state(started, "done")
    assert (read.status, read.json()) == (409, {"error": BUSY_RENAMING})

    assert call(started, path, "POST", body).status == 200, "the nonce was not spent by a 409"
    assert await_state(started, "done", "failed")["state"] == "done"
    assert spy == _renamed("A.JPG")


def test_undo_is_refused_while_reading(tmp_path, started, spy):
    root, path, body = _ready(started, tmp_path, "undo")
    before = list(spy)
    reading = Blocked()
    reading.start(started, "read")
    refused = call(started, path, "POST", body)
    reading.release.set()
    await_state(started, "done")
    assert (refused.status, refused.json()) == (409, {"error": BUSY_READING})
    assert spy == before
    assert archive_names(root) == _after(root, "A.JPG")
    # The apply twin's last check, for undo (Stage 1 review, M3293): the 409 came BEFORE the
    # handler, so the undo's nonce was not spent - the same request then puts A back.
    assert call(started, path, "POST", body).status == 200, "the nonce was not spent by a 409"
    assert await_state(started, "done", "failed")["state"] == "done"
    assert archive_names(root) == ORIGINAL


# --- undo (Step 5) ---------------------------------------------------------------------------


def test_a_page_undo_after_a_page_apply_puts_every_name_back(
    tmp_path, started, spy, monkeypatch
):
    root = _bound(started, tmp_path)
    applied = _applied(started, root, (A, HEIC))
    assert archive_names(root) == _after(root, "A.JPG", "IMG_1.HEIC", "IMG_1.MOV")
    session = applied["session"]
    assert session.startswith("s-")

    facts = call(started, f"session/{session}").json()
    nonce = facts.pop("nonce")
    assert facts == {
        "session_id": session,
        "done": 3,
        "failed": 0,
        "reverted": 0,
        "intent": 0,
        "undone": 0,
        "undoable": True,
    }
    del spy[:]
    seen: list[tuple[int, int]] = []
    real = started.jobs._progress
    monkeypatch.setattr(
        started.jobs,
        "_progress",
        lambda done, total: (seen.append((done, total)), real(done, total)),
    )
    assert call(started, f"session/{session}/undo", "POST", {"nonce": nonce}).status == 200
    undone = await_state(started, "done", "failed")
    # R6-1 per unit (Stage 1 review, M3297): the undo goes back in REVERSE order, so the pair
    # (2 rows) first and then A - each report "of 3", the session's 3 done rows.
    assert seen == [(2, 3), (3, 3)]

    assert undone["state"] == "done" and undone["kind"] == "undo"
    # R6-1 / R6-2: the undo reports its progress (3 of the session's 3 done rows) and its counts.
    assert (undone["done"], undone["total"]) == (3, 3)
    assert undone["result"] == {"restored": 3, "failed": 0, "already_undone": 0}
    assert undone["session"] not in (None, session)
    assert sorted(spy) == sorted([(NEW[n], n) for n in ("A.JPG", "IMG_1.HEIC", "IMG_1.MOV")])
    assert archive_names(root) == ORIGINAL
    after = call(started, f"session/{session}").json()
    assert (after["undone"], after["undoable"]) == (3, False)


def test_a_replayed_undo_renames_nothing(tmp_path, started, spy):
    root, path, body = _ready(started, tmp_path, "undo")
    assert call(started, path, "POST", body).status == 200
    await_state(started, "done", "failed")
    before = list(spy)

    replay = call(started, path, "POST", body)

    assert (replay.status, replay.json()) == (409, {"error": CHANGED})
    assert spy == before and _job_number(started) == 2
    assert archive_names(root) == ORIGINAL


def test_an_unknown_session_renames_nothing(tmp_path, started, spy):
    root = _bound(started, tmp_path)
    reply = call(started, "session/s-20000101-000000")
    assert (reply.status, reply.json()) == (
        404,
        {"error": NOTHING_TO_UNDO.format(root=root.resolve())},
    )
    undo = call(started, "session/s-20000101-000000/undo", "POST", {"nonce": "0" * 32})
    assert (undo.status, undo.json()) == (409, {"error": CHANGED})
    assert spy == [] and _job_number(started) == 0
    assert archive_names(root) == ORIGINAL


def test_an_already_undone_session_renames_nothing(tmp_path, started, spy):
    root, path, body = _ready(started, tmp_path, "undo")
    assert call(started, path, "POST", body).status == 200
    first = await_state(started, "done", "failed")
    session = path.split("/")[1]
    before = list(spy)

    again = call(started, f"session/{session}").json()
    assert (again["undone"], again["undoable"]) == (1, False)
    assert call(started, path, "POST", {"nonce": again["nonce"]}).status == 200
    ended = await_state(started, "done", "failed")

    assert ended["state"] == "done" and ended["lines"] == []
    # R6-2 (Stage 1 review, M3302): the second undo restored nothing - its one row was already
    # undone. U22's "exactly as it was before" depends on this field.
    assert ended["result"] == {"restored": 0, "failed": 0, "already_undone": 1}
    assert spy == before
    assert archive_names(root) == ORIGINAL
    redo = call(started, f"session/{first['session']}")
    assert (redo.status, redo.json()) == (409, {"error": REDO.format(sid=first["session"])})


def test_an_undo_after_the_session_changed_renames_nothing(tmp_path, started, spy):
    """The undo's nonce is bound to the session's rows as they were: a second confirmation's undo
    runs first, and the first nonce - now stale, though unused - is CANCELLED in the job."""
    root, path, body = _ready(started, tmp_path, "undo")
    session = path.split("/")[1]
    real = started.nonces._issued[("session", session)]
    assert call(started, path, "POST", body).status == 200
    await_state(started, "done", "failed")
    before = list(spy)
    real.used = False  # the first nonce, reopened by hand: only the digest can refuse it now

    assert call(started, path, "POST", body).status == 200
    ended = await_state(started, "done", "failed", "cancelled")

    assert (ended["state"], ended["lines"]) == ("cancelled", [CHANGED])
    assert spy == before
    assert archive_names(root) == ORIGINAL


# --- job death and Ctrl+C (Steps 7 and 8) -----------------------------------------------------


def test_a_page_apply_that_dies_leaves_no_plan_applying(tmp_path, started, spy, set_fault):
    """Step 7, mutation 10. `crash:after_intent:1` fires in the JOB THREAD: the plan is
    `applying`. The next request joins the thread and recovers (J10), the plan gets its terminal
    status, and the job reports U12's recovery line."""
    root = _bound(started, tmp_path)
    _decide(started, [A, B])
    nonce = _confirmation(started)["nonce"]
    set_fault.arm("crash:after_intent:1")

    assert _apply(started, nonce).status == 200
    dead = await_state(started, "failed")
    assert set_fault.hits("after_intent") == 1
    os.environ.pop("PAM_FAULT")  # disarmed: the fault stays armed inside one process otherwise
    set_fault.next_invocation()
    assert dead["recovery"] is None
    assert _plan_status(root) == "applying"

    healed = call(started, "job").json()

    assert (healed["state"], healed["recovery"]) == ("failed", RECOVERED)
    assert _plan_status(root) == "partially_applied"
    assert spy == [] and archive_names(root) == ORIGINAL


# F3's two deaths inside the report write: a process killed, and a real OSError (a full disk).
# Named, so emptying them fails `test_the_report_write_deaths_are_written_down` rather than
# SKIPPING the only test that pins F3 (review M3864, the M3839 shape).
REPORT_WRITE_DEATHS = {
    "killed": lambda: SystemExit("killed writing the report"),
    "disk-full": lambda: OSError(28, "No space left on device"),
}


def test_the_report_write_deaths_are_written_down():
    """Review M3864: an empty parametrize SKIPS, so the list F3's test runs over is pinned here -
    read off the test's OWN mark, so emptying the decorator is caught as well as the dict."""
    (mark,) = test_a_page_rename_that_dies_writing_its_report_still_names_its_session.pytestmark
    assert (mark.name, mark.args[0], list(mark.args[1])) == (
        "parametrize",
        "death_name",
        ["disk-full", "killed"],
    )
    assert isinstance(REPORT_WRITE_DEATHS["killed"](), SystemExit)
    assert isinstance(REPORT_WRITE_DEATHS["disk-full"](), OSError)


@pytest.mark.parametrize("death_name", sorted(REPORT_WRITE_DEATHS))
def test_a_page_rename_that_dies_writing_its_report_still_names_its_session(
    tmp_path, started, spy, monkeypatch, death_name
):
    """Task 7 Stage 2 F3: the rename finished (A and the pair renamed) and the job died INSIDE
    `write_session_report`. The snapshot still names the session that renamed the files, and the
    server says that session is undoable - which is what the page offers Undo on (S2-F1)."""
    root = _bound(started, tmp_path)
    _decide(started, [A, HEIC])
    nonce = _confirmation(started)["nonce"]
    entered: list[str] = []

    def dying(reports_dir, summary, logger):
        entered.append(summary.session_id)
        raise REPORT_WRITE_DEATHS[death_name]()

    monkeypatch.setattr(rename_jobs.apply, "write_session_report", dying)
    assert _apply(started, nonce).status == 200
    dead = await_state(started, "failed")

    (session,) = entered
    assert session.startswith("s-")
    assert archive_names(root) == _after(root, "A.JPG", "IMG_1.HEIC", "IMG_1.MOV")
    assert sorted(spy) == sorted(_renamed("A.JPG", "IMG_1.HEIC", "IMG_1.MOV"))
    assert dead["session"] == session
    assert call(started, "job").json()["session"] == session
    facts = call(started, f"session/{session}").json()
    assert (facts["done"], facts["undone"], facts["undoable"]) == (3, 0, True)


def test_ctrl_c_during_a_page_rename_is_a_crash_and_says_so(tmp_path, monkeypatch, capsys):
    """Step 8 (U11). The REAL serve loop and a real Ctrl+C while the page's rename is moving
    files: no closing block, U11's sentence, exit 1 - and NO wait for the job and NO release of
    the lock (the daemon thread would die with the process). The test then lets the job finish
    and closes the server itself."""
    from pam.cli import main

    root = _archive(tmp_path)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    entered, release = threading.Event(), threading.Event()
    real = unit_mod.perform_verified_rename

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(DEADLINE), "the test never released the rename"
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", blocking)
    real_serve = launcher.StartServer.serve_forever
    servers: list[StartServer] = []

    def interrupted(server, *args, **kwargs):
        servers.append(server)
        thread = threading.Thread(target=real_serve, args=(server,), daemon=True)
        thread.start()
        _decide(server, [A])
        assert _apply(server, _confirmation(server)["nonce"]).status == 200
        assert entered.wait(DEADLINE), "the rename never started"
        server.shutdown()
        thread.join(timeout=DEADLINE)
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher.StartServer, "serve_forever", interrupted)
    code = main(["start", str(root)])
    out = capsys.readouterr().out.splitlines()
    server = servers[0]
    try:
        assert code == 1
        assert out[-1] == STOPPED_WHILE_RENAMING
        assert "Next: pam start   (pick up where you left off)" not in out
        assert server.jobs.busy_with() == "renaming", "the job was waited for"
        assert server.held is not None and server.held.held, "the lock was released"
    finally:
        release.set()
        deadline = time.monotonic() + DEADLINE
        while server.jobs.busy_with() is not None and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        server.server_close()


# --- `pam review` serves the same server, bound (M14 Task 6c, ruling B1; U5, U10) -------------

# W17, word for word - spelled here, never imported.
W17 = (
    "Review names and rename them in your browser. Nothing is renamed until you confirm there."
)


def _lock_file(root):
    from pam.paths import workspace_if_exists

    return workspace_if_exists(root).lock_path


def test_pam_review_serves_the_bound_start_server_and_the_page_renames_on_it(
    tmp_path, monkeypatch, capsys, spy
):
    """B1: `pam review PLAN_ID` serves `pam start`'s server bound to the plan `_resolve_plan`
    chose, under `run_review`'s own lock - and a page rename runs on it. It cannot change
    folder (no bind route; `rebindable` False, so a write with no binding header is served),
    and the lock is released when it stops, exactly as before M14."""
    from pam.cli import main

    root = _archive(tmp_path)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    seen = {}

    def serve(server):
        seen["server"] = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            seen["held"] = server.held is not None and server.held.held
            seen["lock_held"] = _lock_file(root).exists()
            seen["bind"] = call(server, "bind", "POST", {"path": str(root)}).status
            no_header = call(
                server,
                f"plan/{PLAN}/decide",
                "POST",
                {"action": "approve", "item_ids": [A]},
                headers={"X-Pam-Binding": None},
            )
            seen["no_header"] = no_header.status
            seen["job"] = _applied(server, root, ids=(A,))
        finally:
            server.shutdown()
            thread.join(timeout=DEADLINE)

    monkeypatch.setattr("pam.webui.serve_until_interrupted", serve)
    code = main(["review", str(PLAN), "--archive", str(root)])
    out = capsys.readouterr().out.splitlines()
    server = seen["server"]
    assert code == 0
    assert isinstance(server, StartServer)
    assert server.rebindable is False
    assert ("POST", "bind") not in server.api_handlers
    assert seen["bind"] == 404
    assert seen["no_header"] == 200
    assert seen["held"] and seen["lock_held"]
    assert seen["job"]["state"] == "done"
    assert seen["job"]["result"]["renamed"] == 1
    assert spy == _renamed("A.JPG")
    assert archive_names(root) == _after(root, "A.JPG")
    assert out[out.index(f"Review at {server.url}") + 1] == W17
    assert STOPPED_WHILE_RENAMING not in out
    assert not _lock_file(root).exists(), "pam review's lock outlived it"


def test_ctrl_c_during_a_page_rename_under_pam_review_is_a_crash_and_says_so(
    tmp_path, monkeypatch, capsys
):
    """Step 8 (U11) for `run_review` (B1): no closing block, U11's sentence, exit 1, the job not
    waited for and the lock NOT released (the daemon thread would die with the process)."""
    from pam.cli import main

    root = _archive(tmp_path)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    entered, release = threading.Event(), threading.Event()
    real = unit_mod.perform_verified_rename

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(DEADLINE), "the test never released the rename"
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", blocking)
    real_serve = launcher.StartServer.serve_forever
    servers: list[StartServer] = []

    def interrupted(server, *args, **kwargs):
        servers.append(server)
        thread = threading.Thread(target=real_serve, args=(server,), daemon=True)
        thread.start()
        _decide(server, [A])
        assert _apply(server, _confirmation(server)["nonce"]).status == 200
        assert entered.wait(DEADLINE), "the rename never started"
        server.shutdown()
        thread.join(timeout=DEADLINE)
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher.StartServer, "serve_forever", interrupted)
    code = main(["review", "--archive", str(root)])
    captured = capsys.readouterr()
    out = captured.out.splitlines()
    server = servers[0]
    try:
        assert code == 1
        assert out[-1] == STOPPED_WHILE_RENAMING
        assert not [line for line in out if line.startswith("Plan 1:")], "a closing block"
        assert captured.err.strip().splitlines()[-1].startswith("Log: ")
        assert server.jobs.busy_with() == "renaming", "the job was waited for"
        assert server.held is not None and server.held.held, "the lock was released"
        assert _lock_file(root).exists()
    finally:
        release.set()
        deadline = time.monotonic() + DEADLINE
        while server.jobs.busy_with() is not None and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        held = server.held
        server.server_close()
        held._lock.__exit__(None, None, None)  # what the process's end would have done


# --- W16's footer N (M14 Task 6c, ruling B2) -------------------------------------------------


def test_the_footer_n_is_the_confirm_screen_s_renames_and_not_the_approved_items(
    tmp_path, started, spy
):
    """B2: `/list`'s top-level `renames` is the number Confirm shows, by Confirm's own count: an
    approved NO-OP member (J12) is approved and is not a rename. Nothing approved -> 0 (W16's
    `Approve names to rename them`). Spelled by hand: A and the Live Photo pair approved (3
    items), the pair's MOV already has its proposed name, so 2 files get new names."""
    root = _bound(started, tmp_path)
    assert call(started, f"plan/{PLAN}/list").json()["renames"] == 0
    conn = catalog(root)
    try:
        conn.execute(
            "UPDATE plan_items SET new_filename = 'IMG_1.MOV' WHERE plan_id = ? AND id = ?",
            (PLAN, MOV),
        )
        conn.commit()
    finally:
        conn.close()
    _decide(started, [A, HEIC])
    listed = call(started, f"plan/{PLAN}/list?status=proposed").json()
    assert listed["renames"] == 2
    assert _confirmation(started)["renames"] == 2
    assert spy == []


# --- the Stage 1 server review's closures (M14 Task 6; docs/mutations/m14.md) -----------------

# R1-F1's sentence, word for word - spelled here, never imported.
FOLDER_CHANGED = "pam is working on another folder now. Reload the page."
# Written by hand: what a page loaded on ANOTHER folder sends, and what a client that is no page
# at all sends (no header) - both refused on `pam start`'s server, which can change folder.
STALE_BINDINGS = ["another", "none"]
assert STALE_BINDINGS, "an empty list would make the parametrized control vanish"


@pytest.mark.parametrize("sent", STALE_BINDINGS)
@pytest.mark.parametrize("route", MOVING)
def test_a_moving_route_from_a_page_on_another_folder_is_refused_and_spends_nothing(
    tmp_path, started, spy, route, sent
):
    """R1-F1 on the two routes that move files (Stage 1 review, M3294 / M3295). Every U14
    condition right except the binding: the 409 FOLDER_CHANGED, before the handler - no verified
    rename called, no job started, the nonce NOT spent (the same request with the binding the
    page was loaded on is then served)."""
    _root, path, body = _ready(started, tmp_path, route)
    before, jobs_before = list(spy), _job_number(started)
    header = str(started.binding + 1) if sent == "another" else None

    reply = call(started, path, "POST", body, headers={"X-Pam-Binding": header})

    assert (reply.status, reply.json()) == (409, {"error": FOLDER_CHANGED})
    assert spy == before and _job_number(started) == jobs_before
    assert call(started, path, "POST", body).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"


# U19 BY NAME (6b concern 1; Stage 1 review M3290). The guard in `tests/guards.py` pairs MODULES,
# so the door module (`rename_jobs.py` since M15 Task 4's cut, `jobs.py` before it) could assemble
# a second apply body from `apply.execute_unit`, `apply._record_preflight` and
# `apply._finalize_plan` and pass it. Written by hand from U19 ("exactly the
# lock-held entry points `apply_locked` and `undo_locked`") plus the helpers Task 6b's report
# lists (the confirmation's facts, the run's summary and clock, the session report, the CLI's
# undo refusals) - every one of them a reader or a record, none a rename. A name added here is a
# U19 ratification for the M14 close, not a test edit.
JOBS_EXECUTE_NAMES = {
    ("apply", "apply_locked"),
    ("apply", "confirmation_for"),
    ("apply", "ApplySummary"),
    ("apply", "_Clock"),
    ("apply", "write_session_report"),
    # M15 Task 2 (AM2): the finished rename's Done lines - each failure's reason, then AM2's
    # sentence (`reporter.done_lines`). A renderer, not a rename; for the M15 close to ratify.
    ("apply", "done_lines"),
    ("undo", "undo_locked"),
    ("undo", "undoable_rows"),
    ("undo", "UndoSummary"),
}
assert JOBS_EXECUTE_NAMES


def _execute_reaches(source: str) -> tuple[set[tuple[str, str]], list[str], list[str]]:
    """(`apply.X` / `undo.X` attribute reaches, `from pam.execute.apply|undo import ...` modules,
    `jobs.apply` / `rename_jobs.undo` reaches - through a webui module's namespace) in `source`."""
    import ast

    tree = ast.parse(source)
    names = set()
    froms = []
    through_jobs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in ("apply", "undo"):
                names.add((node.value.id, node.attr))
            if node.value.id in ("jobs", "rename_jobs") and node.attr in ("apply", "undo"):
                through_jobs.append(node.attr)
        if isinstance(node, ast.ImportFrom) and node.module in (
            "pam.execute.apply",
            "pam.execute.undo",
        ):
            froms.append(node.module)
    return names, froms, through_jobs


def test_jobs_reaches_apply_and_undo_only_by_the_names_u19_lists():
    from pathlib import Path

    import pam.webui

    webui = Path(pam.webui.__file__).parent
    names, froms, _ = _execute_reaches((webui / "rename_jobs.py").read_text(encoding="utf-8"))
    assert names == JOBS_EXECUTE_NAMES
    assert froms == [], "a name imported from apply/undo would escape the attribute check"
    for path in sorted(webui.glob("*.py")):
        _, _, through_jobs = _execute_reaches(path.read_text(encoding="utf-8"))
        assert through_jobs == [], f"{path.name} reaches pam.execute through jobs' namespace"


def test_the_execute_reach_walker_sees_a_second_apply_body_in_each_spelling():
    """Negative controls for the walker above: M3290's shape, a from-import and a reach through
    `jobs`' namespace are each found."""
    second = "apply._record_preflight(c, p, s, l)\napply.execute_unit(c)\n"
    assert _execute_reaches(second)[0] == {
        ("apply", "_record_preflight"),
        ("apply", "execute_unit"),
    }
    assert _execute_reaches("from pam.execute.apply import execute_unit\n")[1] == [
        "pam.execute.apply"
    ]
    assert _execute_reaches("jobs.apply.apply_locked()\n")[2] == ["apply"]
    assert _execute_reaches("rename_jobs.undo.undo_locked()\n")[2] == ["undo"]


def _verified_rename_calls(source: str) -> int:
    import ast

    count = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            count += name == "perform_verified_rename"
    return count


def test_the_verified_rename_is_called_only_in_execute_unit():
    """U19 / design 7.1: `perform_verified_rename` has exactly ONE call site, `execute/unit.py`
    (its forward and its backward rename - two calls, counted by hand). `ALLOWED_MOVE_SITES`
    counts `os.rename`, so a second caller of the verified rename - a second rename loop, the
    page's or anyone's - passed it (Stage 1 review, M3314)."""
    from pathlib import Path

    import pam

    package = Path(pam.__file__).parent
    sites = {
        path.relative_to(package).as_posix(): n
        for path in sorted(package.rglob("*.py"))
        if (n := _verified_rename_calls(path.read_text(encoding="utf-8")))
    }
    assert sites == {"execute/unit.py": 2}
    assert (
        _verified_rename_calls("x.perform_verified_rename(a=1)\nperform_verified_rename()") == 2
    )


@pytest.mark.parametrize("route", MOVING)
def test_a_moving_post_with_no_origin_is_served(tmp_path, started, spy, route):
    """U14 3: `Origin` is checked WHEN THE BROWSER SENDS ONE - a client that sends none (a
    script, the Stage 2 drive) is not refused for it. Before this, only the U11 busy sweep sent a
    moving POST without Origin (Stage 1 review, M3284)."""
    _root, path, body = _ready(started, tmp_path, route)
    before = list(spy)
    assert call(started, path, "POST", body, headers={"Origin": None}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    assert len(spy) > len(before)


# --- the controller's fixes for the Stage 1 server review (S1-F1, S1-F7) ----------------------


def _review_with(tmp_path, monkeypatch, capsys, work):
    """`pam review 1` with `work(server, root)` run against its served page: the exit code, the
    stdout lines and what `work` returned."""
    from pam.cli import main

    root = _archive(tmp_path)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    seen = {}

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            seen["work"] = work(server, root)
        finally:
            server.shutdown()
            thread.join(timeout=DEADLINE)

    monkeypatch.setattr("pam.webui.serve_until_interrupted", serve)
    code = main(["review", str(PLAN), "--archive", str(root)])
    return code, capsys.readouterr().out.splitlines(), seen["work"]


def test_pam_review_closes_on_the_session_after_a_page_rename(
    tmp_path, monkeypatch, capsys, spy
):
    """S1-F1: after the page renamed, the closing block says what the plan IS and names the
    session's two commands - never "still to review" and never a `pam review` that would refuse.
    Every line spelled by hand."""
    code, out, job = _review_with(
        tmp_path, monkeypatch, capsys, lambda server, root: _applied(server, root, (A,))
    )
    session = job["session"]
    assert code == 0
    assert spy == _renamed("A.JPG")
    # 1 of the plan's 5 items renamed: the plan is `partially_applied`, in `_plan_refusal`'s words.
    assert out[-3:] == [
        "Plan 1 is already applied in part.",
        f"Next: pam report session {session}   (see what was renamed)",
        f"  or: pam undo {session}   (put those files back under their old names)",
    ]
    assert not [line for line in out if "still to review" in line]


def _split_session(root, item, session, *, undone=False):
    """By hand (C6.4): move `item`'s journal row into a second rename session, newer by id, and
    optionally mark its plan item put back - `pam apply` refuses a plan that is not `open`, so no
    command produces a plan renamed in two sessions; the closing block is spelled for it all the
    same (the maintainer's C6.4 ruling)."""
    conn = catalog(root)
    try:
        conn.execute(
            "UPDATE journal SET session_id = ? WHERE plan_item_id = ?", (session, item)
        )
        if undone:
            conn.execute("UPDATE plan_items SET status = 'undone' WHERE id = ?", (item,))
        conn.commit()
    finally:
        conn.close()


def test_pam_review_names_every_session_of_a_plan_renamed_in_two(
    tmp_path, monkeypatch, capsys, spy
):
    """C6.4 (owner, 2026-11-16), the first form: `Plan N is ... (2 sessions).`, one report step
    per session, oldest first, and one undo step - for the NEWEST session that can still be
    undone. Every line spelled by hand."""
    second = "s-20991231-235959"

    def two_sessions(server, root):
        first = _applied(server, root, (A, B))["session"]
        _split_session(root, B, second)
        return first

    code, out, first = _review_with(tmp_path, monkeypatch, capsys, two_sessions)
    assert code == 0
    assert out[-4:] == [
        "Plan 1 is already applied in part (2 sessions).",
        f"Next: pam report session {first}   (see what was renamed)",
        f"  or: pam report session {second}   (see what was renamed)",
        f"  or: pam undo {second}   (put those files back under their old names)",
    ]


def test_pam_review_offers_undo_for_the_newest_session_that_can_still_be_undone(
    tmp_path, monkeypatch, capsys, spy
):
    """C6.4: the newer session's file is already back, so the undo step names the OLDER one -
    never the newest session merely for being newest. Spelled by hand."""
    second = "s-20991231-235959"

    def newer_put_back(server, root):
        first = _applied(server, root, (A, B))["session"]
        _split_session(root, B, second, undone=True)
        return first

    code, out, first = _review_with(tmp_path, monkeypatch, capsys, newer_put_back)
    assert code == 0
    assert out[-4:] == [
        "Plan 1 is already applied in part (2 sessions).",
        f"Next: pam report session {first}   (see what was renamed)",
        f"  or: pam report session {second}   (see what was renamed)",
        f"  or: pam undo {first}   (put those files back under their old names)",
    ]


def test_pam_review_closes_without_undo_after_a_page_undo(tmp_path, monkeypatch, capsys, spy):
    """S1-F1: renamed and put back on the page - the plan is `undone`, and nothing is left to
    put back, so the only step is the report."""

    def apply_then_undo(server, root):
        session = _applied(server, root, (A,))["session"]
        nonce = call(server, f"session/{session}").json()["nonce"]
        assert call(server, f"session/{session}/undo", "POST", {"nonce": nonce}).status == 200
        assert await_state(server, "done", "failed")["state"] == "done"
        return session

    code, out, session = _review_with(tmp_path, monkeypatch, capsys, apply_then_undo)
    assert code == 0
    assert out[-2:] == [
        "Plan 1 is applied and then undone.",
        f"Next: pam report session {session}   (see what was renamed)",
    ]


def test_pam_review_still_closes_on_its_counts_when_nothing_was_renamed(
    tmp_path, monkeypatch, capsys, spy
):
    """S1-F1's other side: an open plan keeps the counts block, unchanged."""
    code, out, _ = _review_with(
        tmp_path, monkeypatch, capsys, lambda server, root: _decide(server, [A])
    )
    assert code == 0
    assert spy == []
    assert out[-2:] == [
        "Plan 1: 1 approved, 0 skipped, 4 still to review.",
        "Next: pam apply 1   (renames only what you approve)",
    ]


def test_pam_review_closes_on_the_new_plan_after_a_page_read(
    tmp_path, monkeypatch, capsys, spy
):
    """S1-F1: a page read (`POST job/read`) scans and plans again, superseding plan 1 - the
    closing names plan 2, which `pam review` would serve, and never a `pam review 1` it refuses."""

    def read_again(server, root):
        reply = call(server, "job/read", "POST", {})
        assert reply.status == 200, reply
        assert await_state(server, "done", "failed")["state"] == "done"

    code, out, _ = _review_with(tmp_path, monkeypatch, capsys, read_again)
    assert code == 0
    assert spy == []
    assert out[-2:] == [
        "Plan 1 is superseded - a newer scan or plan replaced it.",
        "Next: pam review 2   (see & approve the plan)",
    ]


def test_pam_review_closes_on_pam_plan_when_no_plan_is_open(tmp_path, monkeypatch, capsys, spy):
    """S1-F1: plan 1 superseded with no plan open after it (as a read stopped before planning
    leaves it) - the closing names `pam plan`, since `pam review` would refuse."""

    def supersede(server, root):
        conn = catalog(root)
        try:
            conn.execute("UPDATE plans SET status = 'superseded' WHERE id = ?", (PLAN,))
            conn.commit()
        finally:
            conn.close()

    code, out, _ = _review_with(tmp_path, monkeypatch, capsys, supersede)
    assert code == 0
    assert out[-2:] == [
        "Plan 1 is superseded - a newer scan or plan replaced it.",
        "Next: pam plan   (propose new names - renames nothing)",
    ]


@pytest.fixture
def preflights(monkeypatch):
    """Every preflight `/list`'s footer count runs (S1-F7): `rename_jobs.confirmation`, watched."""
    calls: list[int] = []
    real = rename_jobs.confirmation

    def watching(conn, root, plan_id):
        calls.append(plan_id)
        return real(conn, root, plan_id)

    monkeypatch.setattr(rename_jobs, "confirmation", watching)
    return calls


def _footer(server) -> int:
    reply = call(server, f"plan/{PLAN}/list")
    assert reply.status == 200, reply
    return reply.json()["renames"]


def test_the_footer_count_is_not_recomputed_until_something_writes(
    tmp_path, started, spy, preflights
):
    """S1-F7: two `/list` reads with nothing written between run ONE preflight; a POST - served
    or refused - runs the next one, and the count is the fresh one."""
    _bound(started, tmp_path)
    assert (_footer(started), _footer(started)) == (0, 0)
    assert preflights == [PLAN]
    _decide(started, [A])
    assert _footer(started) == 1
    assert preflights == [PLAN, PLAN]
    refused = call(started, f"plan/{PLAN}/decide", "POST", {"action": "approve"})
    assert refused.status == 400, refused
    assert _footer(started) == 1
    assert preflights == [PLAN, PLAN, PLAN]


def test_the_footer_count_is_recomputed_when_a_job_ends(tmp_path, started, spy, preflights):
    """S1-F7: a job writes on its own thread with no POST after it, so the job's state is part of
    the memo's key. Here the job is started directly (no POST) and approves A while it runs."""
    from test_webui_jobs import Blocked

    root = _bound(started, tmp_path)
    blocked = Blocked()
    blocked.start(started)
    assert _footer(started) == 0
    conn = catalog(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = 'approved' WHERE plan_id = ? AND id = ?", (PLAN, A)
        )
        conn.commit()
    finally:
        conn.close()
    blocked.release.set()
    assert await_state(started, "done")["state"] == "done"
    assert _footer(started) == 1
    assert preflights == [PLAN, PLAN]
    assert spy == []


# --- the Stage 1 page review's closures of the controller's fixes (M3320, M3321, M3325) --------


def test_pam_review_closes_on_pam_plan_after_a_page_apply_that_renamed_nothing(
    tmp_path, monkeypatch, capsys, spy
):
    """S1-F1's session operand (M3321): J13 refuses the one approved item, so the plan ends
    `partially_applied` with NO rename session in the journal (`recover.py`: "including the case
    where NOTHING was attempted"). The closing must not name `pam report session None` or a
    `pam undo None`: it is `_plan_refusal`'s `pam plan`. Spelled by hand."""

    def refused(server, root):
        _decide(server, [A])
        conn = catalog(root)
        try:
            conn.execute("UPDATE plan_items SET tokens_json = '{not json' WHERE id = ?", (A,))
            conn.commit()
        finally:
            conn.close()
        assert _apply(server, _confirmation(server)["nonce"]).status == 200
        assert await_state(server, "done", "failed")["state"] == "done"

    code, out, _ = _review_with(tmp_path, monkeypatch, capsys, refused)
    assert code == 0
    assert spy == []
    assert out[-2:] == [
        "Plan 1 is already applied in part.",
        "Next: pam plan   (propose new names - renames nothing)",
    ]
    assert not [line for line in out if "None" in line]


def test_pam_review_offers_no_undo_after_a_page_apply_that_died_before_renaming(
    tmp_path, monkeypatch, capsys, spy, set_fault
):
    """S1-F1 after a job death (built for M3320): the rename job dies after its first intent row.
    `server_close` heals it (recovery: 1 rename not started), so the plan is `partially_applied`
    at the close - `applying` is not reachable there - and its rename session renamed NOTHING.
    `_closed_plan_lines` says it offers undo "while its files are still renamed"; here none is,
    so the closing is the report alone. Spelled by hand."""

    def died(server, root):
        _decide(server, [A, B])
        nonce = _confirmation(server)["nonce"]
        set_fault.arm("crash:after_intent:1")
        assert _apply(server, nonce).status == 200
        assert await_state(server, "failed")["state"] == "failed"
        os.environ.pop("PAM_FAULT")
        set_fault.next_invocation()
        assert _plan_status(root) == "applying"
        return root

    code, out, root = _review_with(tmp_path, monkeypatch, capsys, died)
    conn = catalog(root)
    try:
        sessions = conn.execute("SELECT DISTINCT session_id FROM journal").fetchall()
        done = conn.execute("SELECT COUNT(*) FROM journal WHERE state = 'done'").fetchone()[0]
    finally:
        conn.close()
    (session,) = sessions[0]
    assert code == 0
    assert spy == [] and len(sessions) == 1 and done == 0
    assert out[-2:] == [
        "Plan 1 is already applied in part.",
        f"Next: pam report session {session}   (see what was renamed)",
    ]


def test_pam_review_offers_no_undo_for_its_empty_session_while_an_earlier_one_renamed(
    tmp_path, monkeypatch, capsys, spy, set_fault
):
    """S1P-F1's session operand (M3368, the controller's mutation of its own fix): an earlier
    plan's session renamed A by `pam apply` and could still be undone; this plan's page job dies
    after its first intent, so ITS session renamed nothing. The closing names this plan's
    session, so it offers `pam undo` only for that one - never because some other session in the
    archive could be undone. Spelled by hand."""
    from pam.cli import main

    root = _archive(tmp_path)
    conn = catalog(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = 'approved' WHERE plan_id = 1 AND id = ?", (A,)
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", "1", "--archive", str(root)]) == 0
    assert run_plan(root).plan_id == 2
    capsys.readouterr()
    conn = catalog(root)
    try:
        (b_item,) = conn.execute(
            "SELECT id FROM plan_items WHERE plan_id = 2 AND old_rel_path = 'B.JPG'"
        ).fetchone()
    finally:
        conn.close()
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = {"action": "approve", "item_ids": [b_item]}
            assert call(server, "plan/2/decide", "POST", body).status == 200
            nonce = call(server, "plan/2/confirmation").json()["nonce"]
            set_fault.arm("crash:after_intent:1")
            body = {"nonce": nonce, "folder_confirmed": True}
            assert call(server, "plan/2/apply", "POST", body).status == 200
            assert await_state(server, "failed")["state"] == "failed"
            os.environ.pop("PAM_FAULT")
            set_fault.next_invocation()
        finally:
            server.shutdown()
            thread.join(timeout=DEADLINE)

    monkeypatch.setattr("pam.webui.serve_until_interrupted", serve)
    code = main(["review", "2", "--archive", str(root)])
    out = capsys.readouterr().out.splitlines()
    conn = catalog(root)
    try:
        sessions = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM journal")]
    finally:
        conn.close()
    assert code == 0
    assert spy == _renamed("A.JPG"), "the dying page job renamed a file"
    assert len(sessions) == 2
    newest = max(sessions)
    assert out[-2:] == [
        "Plan 2 is already applied in part.",
        f"Next: pam report session {newest}   (see what was renamed)",
    ]


def test_the_footer_count_is_the_plan_s_own_with_no_write_between(tmp_path, started, spy):
    """S1-F7's plan-id operand (M3325): a page read supersedes plan 1 and plans 2; one name is
    approved on plan 2. A stale tab's `/list` for plan 1 read straight after plan 2's - nothing
    written between - is plan 1's own count (0: a superseded plan renames nothing), never plan
    2's memo."""
    root = _bound(started, tmp_path)
    assert call(started, "job/read", "POST", {}).status == 200
    assert await_state(started, "done", "failed")["state"] == "done"
    conn = catalog(root)
    try:
        (a2,) = conn.execute(
            "SELECT id FROM plan_items WHERE plan_id = 2 AND old_rel_path = 'A.JPG'"
        ).fetchone()
    finally:
        conn.close()
    reply = call(started, "plan/2/decide", "POST", {"action": "approve", "item_ids": [a2]})
    assert reply.status == 200, reply

    fresh = call(started, "plan/2/list")
    stale = call(started, f"plan/{PLAN}/list")

    assert (fresh.status, stale.status) == (200, 200)
    assert (fresh.json()["renames"], stale.json()["renames"]) == (1, 0)
    assert spy == []

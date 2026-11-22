"""F12: a plan's unit is what `pam scan` grouped, not what the plan holds items for.

THE BREACH THIS MODULE CLOSES (design spec 7.3 rev 16, vision S3). With
`scan.ignore_stem_suffixes` non-empty a companion unit's members have different on-disk
stems, so spec 6.3 rule 3's `already_conforming` test - which was made per MEMBER - could
be true for one member and false for another. The conforming member got no plan item at
all, and all three of the layers that contain spec 7.3 reason over the plan's ITEMS: the
preflight (`validator._item_refusals`), spec 8.2's endpoints (`write_state._widen_to_units`)
and the review page. A name the user typed then split the group across two stems, `pam
verify` reported a clean 12/12, and the next `pam scan` made it permanent.

`v0.1-m9` was tagged with it knowingly. The construction that asserted the split lives in
`tests/test_m9_safety_constructions.py` and now asserts the PROPERTY instead.

WHAT THIS MODULE PINS THAT THE TINY LIBRARY CANNOT.
`tests/test_tiny_library.py::test_plan_proposes_exactly_these_names` is the acceptance
criterion - no proposed name and no item count may move under the default configuration -
and it asserts `already_conforming == 0`, which means the tiny library holds no conforming
file at all and the branch changed here NEVER FIRES THERE. Green over it is necessary and
not sufficient. So this module builds the three cases by hand and asserts item by item.

THE EXPECTATIONS ARE SPELLED FROM THE SPEC, never read back from the planner. Every
filename below is written out; nothing here asks `pam plan` what it produced and then
agrees with it.
"""

import json
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path

import pytest
from archives import (
    FACES_FLOOR,
    FAKE_COMMAND,
    approve_and_apply,
    approve_archive,
    catalog,
    open_plan,
)

from pam.catalog import repo
from pam.cli import main
from pam.errors import EXIT_EXPECTED
from pam.paths import workspace_for
from pam.scan.runner import run_scan
from pam.webui import run_review

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
PICTURE = (LIBRARY / "phash_pattern_base.png").read_bytes()

# No `{event-or-scene}` and no `{place}`: the name must be a pure function of the date and
# the sequence number, or the expectations below could not be written out by hand.
DATE_ONLY_TEMPLATE = "{date}_{seq}"
DATE = "2019:07:04 18:23:11"

# The editor-suffix config the owner asked for and the one thing that makes a unit's
# members' on-disk stems differ (design spec 5.4, `scan.ignore_stem_suffixes`).
ACR_CONFIG = (
    f'[naming]\ntemplate = "{DATE_ONLY_TEMPLATE}"\n\n[scan]\nignore_stem_suffixes = [".acr_"]\n'
)

# THE FLOOR UNDER THIS MODULE'S OWN INVENTORY (M1670, and Tasks 1 and 2's F5/F1 before it).
# Every row here is a control measured to be the SOLE thing standing behind a property, so
# that deleting it is a deliberate edit to a registry rather than a silently smaller suite.
LOAD_BEARING_CONTROLS = {
    "test_an_external_rename_of_a_no_op_member_is_refused_with_a_route": (
        "the ONLY assertion in the repository that observes the COMPANION'S NAME ON DISK "
        "after a moved no-op - vision S3 for M10's C1. Measured: M1703b (the preflight "
        "row sees only `actionable()`, the arithmetic row sees only a count) and M1707 "
        "(deleting it leaves the suite green at 2976)"
    ),
    "test_a_no_op_item_outside_the_actionable_statuses_is_never_resolved_to_applied": (
        "`noops()`'s first operand; the mutation it pins (M1419) survived a whole suite"
    ),
    "test_a_refused_no_op_item_keeps_its_refusal_and_is_not_resolved": (
        "`noops()`'s second operand; its mutation (M1420) also survived a whole suite, "
        "and NO assertion about the archive can see the damage it does"
    ),
    "test_a_partially_conforming_unit_gets_an_item_for_every_member": (
        "F12 itself - spec 7.3 rev 16's membership rule, the reason this module exists"
    ),
    "test_the_state_after_one_apply_and_an_editor_export_keeps_its_unit_whole": (
        "the steady state of an archive that has been through pam once, which is what "
        "made F12 an ordinary configuration rather than an exotic one"
    ),
    "test_apply_resolves_the_no_op_item_without_a_journal_row_or_a_rename": (
        "all four clauses of spec 7.0 J12 on an archive of real files, in one place"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own inventory (reviewer's F3, on M1670's precedent)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )


def _put(root: Path, name: str, salt: int, *, date: str | None = DATE) -> None:
    """One decodable picture plus the sidecar `tests/fake_exiftool.py` reads its tags from.

    The last byte varies per file so every file has its own sha256 - PNG's trailing bytes
    are the IEND CRC, which Pillow does not verify.
    """
    (root / name).write_bytes(PICTURE[:-1] + bytes([salt]))
    tags: dict[str, object] = {"PamTestSidecar": name}
    if date is not None:
        tags["DateTimeOriginal"] = date
    (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")


def _configure(root: Path, body: str) -> None:
    config_path = workspace_for(root).config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body, encoding="utf-8")


def _names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if not p.name.endswith(".tags.json")}


def _stems(names) -> set[str]:
    return {Path(name).stem for name in names}


def _plan_state(root: Path) -> list[tuple[str, str]]:
    """The OPEN plan's items, as (old_filename, new_filename) pairs.

    THE SUITE'S OWN SQL, not `repo.plan_rows`: an oracle built from the production query
    agrees with a broken one, perfectly. Scoped to the open plan by a join on `plans`
    rather than by a plan id handed in, because a workspace that has applied a plan holds
    its superseded rows too and they are not what any assertion here is about. `[]` when
    there is no open plan at all - `pam plan` writes NO `plans` row when it has nothing to
    rename, which is itself part of what the wholly-conforming cases below assert.
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT pi.old_rel_path, pi.new_filename FROM plan_items pi"
            " JOIN plans pl ON pl.id = pi.plan_id WHERE pl.status = 'open'"
            " ORDER BY pi.old_rel_path"
        ).fetchall()
    finally:
        conn.close()
    return [(Path(r["old_rel_path"]).name, r["new_filename"]) for r in rows]


def _plan_count(root: Path) -> int:
    conn = catalog(root)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0])
    finally:
        conn.close()


def _item_statuses(root: Path) -> dict[str, str]:
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT old_rel_path, status FROM plan_items").fetchall()
    finally:
        conn.close()
    return {Path(row["old_rel_path"]).name: row["status"] for row in rows}


def _journalled(root: Path) -> set[str]:
    """The files pam wrote a journal row for. spec 7.0 J12: never a no-op item's."""
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT old_rel_path FROM journal").fetchall()
    finally:
        conn.close()
    return {Path(row["old_rel_path"]).name for row in rows}


def _plan_status(root: Path, plan_id: int) -> str:
    conn = catalog(root)
    try:
        row = conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()
    finally:
        conn.close()
    return str(row[0])


def _open_plan_id(root: Path) -> int:
    conn = catalog(root)
    try:
        return open_plan(conn).id
    finally:
        conn.close()


def _apply(root: Path, plan_id: int, monkeypatch) -> int:
    # F1 (M13): `pam apply` renames only approved items - approve every undecided one with
    # the suite's own SQL (a skip stays a skip), then drive the real command.
    approve_archive(root, plan_id)
    monkeypatch.setattr("builtins.input", lambda: "apply")
    return main(["apply", str(plan_id), "--archive", str(root)])


def _session_id(out: str) -> str:
    """The session id `pam apply` printed, read off its own `Undo:` line."""
    lines = [line for line in out.splitlines() if "pam undo " in line]
    assert lines, out
    return lines[0].split("pam undo ")[1].strip()


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """`run_review` opens a browser. A suite that opens real tabs is a broken suite."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def _request(server, method, path, body=None):
    """One request to the live review server, through the token prefix - the only way in."""
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request(
            method,
            f"/t/{server.token}/api{path}",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, (method, path, response.status, raw)
        return json.loads(raw)
    finally:
        conn.close()


def _drive(plan_id: int, root: Path, work):
    """Run `work(server)` against the server `pam review` really starts, then stop it."""
    result: dict[str, object] = {}

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result["value"] = work(server)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    run_review(plan_id, root, serve=serve)
    return result["value"]


# --- Step 8: the three cases, item by item --------------------------------------------


def test_a_wholly_conforming_unit_still_gets_no_items_at_all(tmp_path, capsys):
    """spec 6.3 rule 3 is unchanged where it was always right: no member is renamed.

    The membership rule of spec 7.3 rev 16 fires only "whenever ANY member of that unit is
    non-conforming". Both members here already carry the stem the planner will choose, so
    the unit is wholly conforming, both files are counted as already conforming and the
    plan holds nothing.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.NEF", 0)
    _put(root, "2019-07-04_001.png", 1)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    out = capsys.readouterr().out

    assert _plan_state(root) == []
    assert _plan_count(root) == 0
    assert "2 files already match the naming template." in out


def test_a_lone_conforming_file_still_gets_no_item(tmp_path, capsys):
    """A unit of one that already conforms. The membership rule cannot reach it."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.NEF", 0)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    out = capsys.readouterr().out

    assert _plan_state(root) == []
    assert _plan_count(root) == 0
    assert "1 files already match the naming template." in out


def test_a_partially_conforming_unit_gets_an_item_for_every_member(tmp_path, capsys):
    """THE MEMBERSHIP RULE (spec 7.3 rev 16), spelled out from the spec.

    > A planner that would omit a member because it is already conforming must emit an
    > item for it anyway whenever *any* member of that unit is non-conforming.

    Two members, one stem apiece: `2019-07-04_001.NEF` already carries the planner's stem
    and `2019-07-04_001.acr_.png` does not. Both get an item; exactly one of them proposes
    the name its file already has, which spec 7.0 J12 calls a no-op item.
    `already_conforming` counts files that got NO item, so it is 0 - a no-op item's file is
    in the plan, and counting it in both would make UX 4's counts line stop reconciling.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.NEF", 0)
    _put(root, "2019-07-04_001.acr_.png", 1)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    assert _plan_state(root) == [
        ("2019-07-04_001.NEF", "2019-07-04_001.NEF"),
        ("2019-07-04_001.acr_.png", "2019-07-04_001.png"),
    ]
    # `already_conforming` counts files that got NO item, and this unit's conforming
    # member got one - so the plan's frozen figure stays 0 and UX 4's counts line
    # reconciles. The confirmation's own handling of it is asserted further down.
    assert "0 files already match" not in capsys.readouterr().out


# --- Step 2: the steady state that needs no hand-rename -------------------------------


def test_the_state_after_one_apply_and_an_editor_export_keeps_its_unit_whole(
    tmp_path, monkeypatch, capsys
):
    """THE ROUTE THAT NEEDS NO HAND-RENAME, driven end to end through the real commands.

    Apply a plan over a lone RAW; let the editor export `<pam stem>.acr_.png` beside it -
    the exact convention `scan.ignore_stem_suffixes` exists for; re-scan; re-plan. Before
    the fix the second plan held ONE of the unit's two members (measured: the items were
    `[('2019-07-04_001.acr_.png', '2019-07-04_001.png')]` while `2019-07-04_001.NEF` sat
    beside it on disk with no item at all), and one name typed in the review UI split the
    group.

    That is the steady state of an archive that has been through pam once, which is why
    this is the construction and not the hand-typed one.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "DSC_3579.NEF", 0)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    assert _apply(root, _open_plan_id(root), monkeypatch) == 0
    capsys.readouterr()
    assert _names(root) == {"2019-07-04_001.NEF"}

    # The editor exports beside it.
    _put(root, "2019-07-04_001.acr_.png", 1)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    # The plan now holds the WHOLE unit, one member of which is a no-op.
    assert _plan_state(root) == [
        ("2019-07-04_001.NEF", "2019-07-04_001.NEF"),
        ("2019-07-04_001.acr_.png", "2019-07-04_001.png"),
    ]

    # A name the user types moves every member, which is the point of the rule.
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        rows = repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
    finally:
        conn.close()
    png = next(row for row in rows if row.old_filename.endswith(".acr_.png"))

    def work(server):
        _request(server, "POST", f"/item/{png.id}/rename", {"new_filename": "Holiday.png"})
        return _request(server, "POST", f"/plan/{plan_id}/decide-filter", {"action": "approve"})

    assert _drive(plan_id, root, work)["decided"] == 2
    assert _apply(root, plan_id, monkeypatch) == 0
    capsys.readouterr()

    # VISION S3: one stem across the unit, which is what spec 7.3's closing sentence
    # promises can never fail.
    assert _names(root) == {"Holiday.NEF", "Holiday.png"}
    assert len(_stems(_names(root))) == 1


# --- Step 4 + Step 6: what apply does with a no-op item, and every consumer of status ---


def _partial_unit(root: Path) -> None:
    """The archive every consumer test below drives: one no-op item, one real rename."""
    root.mkdir()
    _put(root, "2019-07-04_001.NEF", 0)
    _put(root, "2019-07-04_001.acr_.png", 1)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0


def test_apply_resolves_the_no_op_item_without_a_journal_row_or_a_rename(
    tmp_path, monkeypatch, capsys
):
    """spec 7.0 J12, all four of its clauses, on an archive of real files.

    Resolved at preflight; NO journal row is ever written for it; no `os.rename` is
    attempted; and it contributes to neither the renamed nor the failed count.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    assert _apply(root, plan_id, monkeypatch) == 0
    out = capsys.readouterr().out

    # Neither renamed nor failed: exactly one file moved.
    assert "1 renamed, 0 failed" in out
    assert _names(root) == {"2019-07-04_001.NEF", "2019-07-04_001.png"}
    # The no-op item has no journal row. The other one does.
    assert _journalled(root) == {"2019-07-04_001.acr_.png"}
    # Terminal status `applied`, and spec 4's six values gain no seventh.
    assert _item_statuses(root) == {
        "2019-07-04_001.NEF": "applied",
        "2019-07-04_001.acr_.png": "applied",
    }


def test_the_confirmation_counts_a_no_op_as_already_conforming_and_not_as_a_rename(
    tmp_path, capsys
):
    """Step 5 / UX 4: the user types `apply` against a count, so the count must be true.

    "2 renames approved" over a plan that will move one file is a misleading number at the
    one dangerous moment. The phrase UX 4 already carries - "already conforming" - is
    literally true of a no-op item's file, so no new copy is invented and no line is added.
    """
    from pam.execute.reporter import confirmation_lines

    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)

    seen: list[str] = []

    def refuse(confirmation):
        seen.extend(confirmation_lines(confirmation))
        return False

    approve_and_apply(plan_id, root, confirm=refuse)
    block = "\n".join(seen)
    assert "1 renames approved" in block
    assert "2 proposed, 0 skipped by you, 1 already conforming" in block


def test_a_refused_no_op_leaves_the_already_conforming_count_and_joins_the_failures(
    tmp_path, monkeypatch, capsys
):
    """M10 condition C1, Step 7: a refused no-op is in NEITHER partition.

    `_confirmation` computes `already_conforming` as the plan-time figure plus
    `len(preflight.noops())`, and `noops()` excludes a refused item by construction - so
    the refused member LEAVES that count rather than being reported as a file already
    carrying its name, which it is not. It joins the failure list instead, through
    `_record_preflight`'s ordinary refusal path.

    THE NUMBERS ARE SPELLED OUT FROM THE SPEC, not read back from the run: two items, both
    refused, so `0 renames approved`, `2 proposed`, `0 skipped by you`, `0 already
    conforming`, and `0 renamed, 2 failed`. Every one of them moved when the fix landed -
    the same construction previously read 1 / 2 / 0 / 1 and `1 renamed, 0 failed`.
    """
    from pam.execute.reporter import confirmation_lines

    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    (root / "2019-07-04_001.NEF").rename(root / "EXTERNAL.NEF")

    seen: list[str] = []

    def refuse(confirmation):
        seen.extend(confirmation_lines(confirmation))
        return False

    approve_and_apply(plan_id, root, confirm=refuse)
    block = "\n".join(seen)
    assert "0 renames approved" in block
    assert "2 proposed, 0 skipped by you, 0 already conforming" in block

    # And the same two items are the failure list once the run is not cancelled.
    capsys.readouterr()
    assert _apply(root, plan_id, monkeypatch) == EXIT_EXPECTED
    assert "0 renamed, 2 failed" in capsys.readouterr().out


def test_a_plan_holding_a_no_op_item_still_reads_applied(tmp_path, monkeypatch, capsys):
    """Consumer 1: `pam apply`'s finalizer (`recover.derive_plan_status`).

    An `applied` item is neither `failed` nor unattempted, so the plan reads `applied` and
    not `partially_applied`. Spelled from spec 7.1's own sentence - "plan status `applied`
    (all) or `partially_applied`" - not read back from `derive_plan_status`.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    assert _apply(root, plan_id, monkeypatch) == 0
    capsys.readouterr()
    assert _plan_status(root, plan_id) == "applied"


def test_undo_puts_the_plan_back_to_undone_even_though_a_no_op_item_stays_applied(
    tmp_path, monkeypatch, capsys
):
    """Consumers 2 and 3: undo's plan finalizer, and its per-unit status check.

    spec 7.4 rev 9 point 3 says `undone` when no item of that plan is `applied` any more,
    and spec 7.0 J12 says a no-op item's terminal status must not prevent that: the plan is
    `undone` when nothing of it is still applied ON DISK, and a file that never moved was
    never applied to anything. Read literally, the no-op item would keep this plan reading
    `applied` FOREVER - no second `pam undo` could ever clear it.

    THE PER-UNIT CHECK IS PROVED, NOT ASSUMED. `undo._undo_unit` refuses a unit whose item
    statuses are not exactly `{"applied"}`, and its units come from the session's JOURNAL
    rows. A no-op item has none, so it can never appear there - and the proof is that this
    undo SUCCEEDS rather than refusing the unit, which is what a no-op member reaching that
    set would cause.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    assert _apply(root, plan_id, monkeypatch) == 0
    session_id = _session_id(capsys.readouterr().out)

    assert main(["undo", session_id, "--archive", str(root)]) == 0
    out = capsys.readouterr().out
    assert "1 put back" in out
    assert _names(root) == {"2019-07-04_001.NEF", "2019-07-04_001.acr_.png"}
    assert _item_statuses(root) == {
        "2019-07-04_001.NEF": "applied",
        "2019-07-04_001.acr_.png": "undone",
    }
    assert _plan_status(root, plan_id) == "undone"


def test_verify_stays_clean_over_a_no_op_item_and_over_its_undo(tmp_path, monkeypatch, capsys):
    """Consumers 4 and 5: `pam verify` checks 4c and 4d.

    CHECK 4c IS RIGHT AS IT STANDS and is deliberately not changed. It walks
    `repo.item_outcomes`, which JOINS the journal, and asks whether an item's status agrees
    with the journal outcome recorded against it. A no-op item has no journal row (J12), so
    it is in no outcome row and there is nothing for it to disagree with - which is correct
    rather than a hole: the check exists to notice pam writing one of the pair without the
    other, and for a no-op item pam deliberately writes neither.

    CHECK 4d NEEDED A REPAIR, made in `repo.plan_outcomes`. It skips any plan with an
    `applied` item, so an `applied`-forever no-op item would have silently disarmed the
    stale-plan-status check for every plan holding one. See the test below.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    assert _apply(root, plan_id, monkeypatch) == 0
    session_id = _session_id(capsys.readouterr().out)

    assert main(["verify", "--archive", str(root)]) == 0
    assert "12 checks, everything pam recorded looks right" in capsys.readouterr().out

    assert main(["undo", session_id, "--archive", str(root)]) == 0
    capsys.readouterr()
    assert main(["verify", "--archive", str(root)]) == 0
    assert "12 checks, everything pam recorded looks right" in capsys.readouterr().out
    assert _plan_status(root, plan_id) == "undone"


def test_check_4d_still_reports_a_stale_plan_status_when_a_no_op_item_is_present(
    tmp_path, monkeypatch, capsys
):
    """Check 4d's live wire, driven through spec 9.3's `before_plan_finalize` fault.

    THE STATE (spec 7.4 rev 10): a crash between undo's last item resolution and its plan
    status write leaves every file back, every item `undone` - and the plan still reading
    `applied`. Recovery is deliberately inert there. Check 4d is the only thing that
    reports it, and before the `plan_outcomes` repair this run returned 12 of 12 and said
    nothing, because the no-op item made `applied > 0` and the check skipped the plan.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    assert _apply(root, plan_id, monkeypatch) == 0
    session_id = _session_id(capsys.readouterr().out)

    # `crash:` raises SystemExit, which pam/cli.py catches and turns into EXIT_EXPECTED -
    # the process stays alive and the exit code is the ordinary "pam stopped on purpose".
    monkeypatch.setenv("PAM_FAULT", "crash:before_plan_finalize:1")
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_EXPECTED
    monkeypatch.delenv("PAM_FAULT")
    capsys.readouterr()
    assert _plan_status(root, plan_id) == "applied"

    assert main(["verify", "--archive", str(root)]) == 2
    out = capsys.readouterr().out
    assert "plans marked applied whose files are all back" in out
    assert f"pam undo {session_id}" in out


# --- Step 7: the review UI (A11) and the case-only disclosure (B1) ---------------------


def test_the_no_op_card_is_served_decidable_and_counted(tmp_path, capsys):
    """UX 5 rev 11's no-op card, through the real read endpoint.

    The predicate is a SERVED field, because nothing in this project lints, types or
    executes `app.js`. The card shows the unit's NORMAL trust display (tier and score are
    computed once per unit and copied to every member), it is decidable, and it is counted
    - deciding it IS deciding the unit.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)

    def work(server):
        return _request(server, "GET", f"/plan/{plan_id}/items")

    payload = _drive(plan_id, root, work)
    cards = {card["old_filename"]: card for card in payload["items"]}
    assert set(cards) == {"2019-07-04_001.NEF", "2019-07-04_001.acr_.png"}
    assert cards["2019-07-04_001.NEF"]["noop"] is True
    assert cards["2019-07-04_001.acr_.png"]["noop"] is False
    # Normal trust display and no vocabulary of its own: same tier, same score, ordinary
    # undecided status - and it is inside the page's own counts.
    assert cards["2019-07-04_001.NEF"]["tier"] == cards["2019-07-04_001.acr_.png"]["tier"]
    assert (
        cards["2019-07-04_001.NEF"]["trust_score"]
        == cards["2019-07-04_001.acr_.png"]["trust_score"]
    )
    assert cards["2019-07-04_001.NEF"]["status"] == "proposed"
    assert payload["total"] == 2


def test_editing_the_unit_stem_takes_the_no_op_sentence_away(tmp_path, capsys):
    """UX 5 rev 11: "if the user edits the unit's stem, the sentence goes away".

    The rename response is what the page repaints from, so the predicate has to come back
    on it - a response carrying the new names and a stale `noop` would leave the sentence
    on a card that is now really being renamed (design spec 7.0 J12: whether an item is a
    no-op is computed from its CURRENT `new_filename`, never frozen at plan time).
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        rows = repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
    finally:
        conn.close()
    nef = next(row for row in rows if row.old_filename.endswith(".NEF"))

    def work(server):
        return _request(
            server, "POST", f"/item/{nef.id}/rename", {"new_filename": "Holiday.NEF"}
        )

    members = {m["old_filename"]: m for m in _drive(plan_id, root, work)["members"]}
    assert members["2019-07-04_001.NEF"]["new_filename"] == "Holiday.NEF"
    assert members["2019-07-04_001.NEF"]["noop"] is False
    assert members["2019-07-04_001.acr_.png"]["noop"] is False


def test_a_case_only_result_is_stored_disclosed_and_then_not_renamed(
    tmp_path, monkeypatch, capsys
):
    """AMENDMENT B1, both halves, from the outside through the real endpoint.

    design spec 8.2 rev 18: v1.0 does not change a name in case alone. The rename SUCCEEDS
    and the name is stored; what spec 6.2 rule 7 requires is that the response NAME the
    file this applies to and say it will not be renamed. Silently storing the name and
    quietly not renaming the file is the failure B1 exists to close.

    THE SECOND HALF IS ASSERTED ON DISK, after a real `pam apply`: the case-only member is
    unchanged, character for character, while its sibling really moves.
    """
    root = tmp_path / "photos"
    root.mkdir()
    # A partially conforming unit whose conforming member is `Holiday.NEF`, so a typed
    # `holiday` differs from its on-disk name in case alone.
    _put(root, "Holiday.NEF", 0, date=None)
    _put(root, "Holiday.acr_.png", 1, date=None)
    _configure(
        root, '[naming]\ntemplate = "{orig}"\n\n[scan]\nignore_stem_suffixes = [".acr_"]\n'
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        rows = repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
    finally:
        conn.close()
    nef = next(row for row in rows if row.old_filename == "Holiday.NEF")

    def work(server):
        result = _request(
            server, "POST", f"/item/{nef.id}/rename", {"new_filename": "holiday.NEF"}
        )
        _request(server, "POST", f"/plan/{plan_id}/decide-filter", {"action": "approve"})
        return result

    response = _drive(plan_id, root, work)
    members = {m["old_filename"]: m for m in response["members"]}
    # Half one: the name IS stored, for every member of the unit.
    assert members["Holiday.NEF"]["new_filename"] == "holiday.NEF"
    assert members["Holiday.acr_.png"]["new_filename"] == "holiday.png"
    # Half two: the response names the file and says it will not be renamed - and names
    # only that file, never the sibling that really moves.
    assert "holiday.NEF will not be renamed" in response["note"]
    assert "capitalisation" in response["note"]
    assert "holiday.png" not in response["note"]

    assert _apply(root, plan_id, monkeypatch) == 0
    assert "1 renamed, 0 failed" in capsys.readouterr().out
    # THE FILE ON DISK IS UNCHANGED - not `holiday.NEF`, and not gone.
    assert _names(root) == {"Holiday.NEF", "holiday.png"}


def test_an_ordinary_case_only_stem_re_edit_is_still_a_real_rename(tmp_path, capsys):
    """B1's boundary, which spec 8.2 rev 18 draws explicitly and which is easy to cross.

    "This does not touch the case-only STEM edit the collision bullet calls legal."
    Changing `Holiday` to `holiday` in the editor, over a file whose name on disk is
    `DSC_0001.NEF`, is an ordinary re-edit and a real rename. B1 compares the RESULTING
    name against the file's CURRENT name, which is a different pair of strings - a
    disclosure fired here would be false.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "DSC_0001.NEF", 0)
    _configure(root, ACR_CONFIG)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        item_id = repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()

    def work(server):
        _request(server, "POST", f"/item/{item_id}/rename", {"new_filename": "Holiday.NEF"})
        return _request(
            server, "POST", f"/item/{item_id}/rename", {"new_filename": "holiday.NEF"}
        )

    response = _drive(plan_id, root, work)
    assert response["members"][0]["new_filename"] == "holiday.NEF"
    assert response["note"] == ""
    assert response["members"][0]["noop"] is False


# --- The stage-1 review's four findings, closed in the same task ----------------------
#
# M1419, M1420, M1429 and M1434 were SURVIVORS: four behaviours the code has, that the
# code's own docstrings claim, and that no test in the suite could see. CLAUDE.md - "a
# mutation no test catches is a finding, closed in the same task". Each test below is
# named for the sentence it pins, and each was verified by RE-RUNNING its mutation,
# never by observing that a test now exists.


def _set_item_status(root: Path, filename: str, status: str) -> int:
    """Force one item's status, and return its id. The suite's own SQL, deliberately.

    pam has no CLI route to skip a SINGLE item - the review UI writes decisions per UNIT
    (spec 8.2 rev 15, amendment A1), which is the containment F12's fix rests on - so the
    only way to build a unit whose members' statuses disagree is to write one directly.
    That is the point rather than a shortcut: `noops()`'s status operand exists for states
    the ordinary routes do not produce, and a test that could only reach it through those
    routes could not reach it at all.
    """
    conn = catalog(root)
    try:
        row = conn.execute(
            "SELECT id FROM plan_items WHERE old_rel_path LIKE ?", (f"%{filename}",)
        ).fetchone()
        assert row is not None, filename
        conn.execute("UPDATE plan_items SET status = ? WHERE id = ?", (status, row["id"]))
        conn.commit()
        return int(row["id"])
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["skipped", "undone", "failed"])
def test_a_no_op_item_outside_the_actionable_statuses_is_never_resolved_to_applied(
    tmp_path, monkeypatch, capsys, status
):
    """`noops()`'s FIRST operand: only an ACTIONABLE item is resolved (spec 7.0 J12).

    THE MUTATION THIS PINS (M1419) SURVIVED THE WHOLE SUITE. Neutralising
    `item.status in ACTIONABLE_STATUSES` inside `noops()` makes apply resolve a no-op item
    in ANY status: a `skipped` item is silently flipped to `applied` - pam overriding a
    decision the user made - and an `undone` one is flipped back, which is exactly the
    state undo's finalizer and `pam verify` check 4d were repaired for in this task.

    THE THREE STATUSES ARE SPELLED FROM SPEC 4'S ENUM, never read back from
    `ACTIONABLE_STATUSES`: an oracle built from the constant it checks agrees with a
    broken one, perfectly. These are the decided-or-terminal values a no-op item can hold
    when apply runs, and none of them is a state apply may write over.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    _set_item_status(root, "2019-07-04_001.NEF", status)

    _apply(root, plan_id, monkeypatch)
    capsys.readouterr()

    assert _item_statuses(root)["2019-07-04_001.NEF"] == status
    # And it is still not journaled and still not renamed, whatever else happened.
    assert "2019-07-04_001.NEF" not in _journalled(root)
    assert "2019-07-04_001.NEF" in _names(root)


def test_a_refused_no_op_item_keeps_its_refusal_and_is_not_resolved(
    tmp_path, monkeypatch, capsys
):
    """`noops()`'s SECOND operand: a REFUSED item is not resolved (spec 7.3).

    THE MUTATION THIS PINS (M1420) SURVIVED THE WHOLE SUITE. `noops()`'s docstring says
    "A refused item is NOT here", and the no-op loop runs AFTER the refusal loop - so with
    the `item.id not in self.refusals` operand gone, apply overwrites the refusal's own
    record with `applied` for that member. The unit is still correctly contained, so
    nothing moves on disk and NO ASSERTION ABOUT THE ARCHIVE CAN SEE IT: the damage is
    that the plan then claims a member was applied when its whole unit was refused.

    The construction skips the member that really moves, so spec 7.3's containment refuses
    its no-op sibling - the only route by which a no-op item is refused at all.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    _set_item_status(root, "2019-07-04_001.acr_.png", "skipped")

    _apply(root, plan_id, monkeypatch)
    capsys.readouterr()

    # `failed` - the refusal's OWN record, written by the refusal loop. NOT `applied`,
    # which is what the no-op loop would overwrite it with if it ran on a refused item.
    assert _item_statuses(root)["2019-07-04_001.NEF"] == "failed"
    assert _journalled(root) == set()
    assert _names(root) == {"2019-07-04_001.NEF", "2019-07-04_001.acr_.png"}


def test_the_served_card_calls_a_case_only_name_a_no_op_after_a_page_reload(tmp_path, capsys):
    """`ReviewItem.noop` folds case, unlike `edited` directly above it (UX 5 rev 11).

    THE MUTATION THIS PINS (M1429) SURVIVED THE WHOLE SUITE. Every other served-`noop`
    assertion uses either an exactly-equal pair or a pair differing in more than case, so
    making `noop` byte-exact - the same comparison `edited` uses - changed nothing the
    suite could see. Under that mutant the PAGE and the PREFLIGHT disagree about whether
    the file will move: `validator.is_noop` still resolves it without a rename, while the
    card says nothing about it.

    READ BACK THROUGH `GET /plan/{id}/items`, NOT off the rename response, and that is the
    half of B1 the rename response cannot carry: the note is a property of one request,
    and the card is rebuilt from `/items` on the next reload. If the predicate did not
    survive that trip, the sentence would be the only thing that ever said so.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "Holiday.NEF", 0, date=None)
    _put(root, "Holiday.acr_.png", 1, date=None)
    _configure(
        root, '[naming]\ntemplate = "{orig}"\n\n[scan]\nignore_stem_suffixes = [".acr_"]\n'
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        nef = next(
            row
            for row in repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
            if row.old_filename == "Holiday.NEF"
        )
    finally:
        conn.close()

    def work(server):
        _request(server, "POST", f"/item/{nef.id}/rename", {"new_filename": "holiday.NEF"})
        return _request(server, "GET", f"/plan/{plan_id}/items")

    cards = {c["old_filename"]: c for c in _drive(plan_id, root, work)["items"]}
    # `holiday.NEF` over `Holiday.NEF`: differs in case alone, so it will not move...
    assert cards["Holiday.NEF"]["noop"] is True
    # ...and it IS an edit, because a person typed it. Two questions, two answers.
    assert cards["Holiday.NEF"]["edited"] is True
    # The sibling really moves, so it is neither a no-op nor confused with one.
    assert cards["Holiday.acr_.png"]["noop"] is False


def test_a_name_byte_identical_to_the_file_on_disk_is_not_disclosed(tmp_path, capsys):
    """B1's "case-insensitively - but NOT EXACTLY" (design spec 8.2 rev 18).

    THE MUTATION THIS PINS (M1434) SURVIVED THE WHOLE SUITE. No test performed a rename
    whose resulting name was byte-identical to a member's on-disk name, so dropping
    `and resulting != current` from `not_renamed` changed nothing the suite could see.
    Under that mutant a user who retypes the name their file already has is told the name
    "differs from the one on disk only in capitalisation" - which is FALSE of that file,
    on the one surface spec 6.2 rule 7 exists to keep truthful.

    The item is still a no-op at preflight either way (spec 7.0 J12): this governs the
    SENTENCE and not the behaviour, which is why both are asserted.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)
    conn = catalog(root)
    try:
        nef = next(
            row
            for row in repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
            if row.old_filename == "2019-07-04_001.NEF"
        )
    finally:
        conn.close()

    def work(server):
        # The stem the file already carries, character for character.
        return _request(
            server,
            "POST",
            f"/item/{nef.id}/rename",
            {"new_filename": "2019-07-04_001.NEF"},
        )

    response = _drive(plan_id, root, work)
    members = {m["old_filename"]: m for m in response["members"]}
    assert members["2019-07-04_001.NEF"]["new_filename"] == "2019-07-04_001.NEF"
    # NO sentence: nothing differs, so there is nothing to disclose.
    assert response["note"] == ""
    # But it is still a no-op, and the page still says so.
    assert members["2019-07-04_001.NEF"]["noop"] is True


# --- The stage-2 review's three conditions, closed by the controller inline ------------
#
# Stage 2 drove the real commands and found three things stage 1's mutation pass could
# not: C1, a FALSE SENTENCE shown to real users; C2, two pam surfaces reporting different
# truths about the same plan; C3, a docstring "this cannot happen" whose counter-example
# stage 2 built and ran. C4 is a spec amendment and is escalated to the owner, not closed
# here - the executor has no amendment authority.


def test_the_no_op_card_needs_a_unit_and_the_card_carries_what_that_gate_reads(
    tmp_path, capsys
):
    """C1: UX 5 rev 11's card is for A MEMBER OF A COMPANION UNIT, and both halves say so.

    THE SENTENCE SHIPPED WITHOUT ITS UNIT GATE AND WAS FALSE ON ITS FIRST NON-GROUP INPUT.
    `ReviewItem.noop` is unit-blind and case-insensitive, so a case-only name typed over a
    LONE file made it true with no group anywhere: the card claimed the file "already has
    the right name" (it has `Holiday.jpg`; the stored proposal is `holiday.jpg`) and that
    pam "renames a group together" (there is no group) - while B1's note directly below it
    correctly explained the very difference the first sentence denied.

    THE GATE ITSELF LIVES IN `app.js`, WHICH NOTHING IN THIS PROJECT EXECUTES, so this test
    cannot assert the rendering - and saying so is the point rather than an excuse (M1436).
    What it CAN do, and what makes the gate more than a hope, is pin the two response
    fields the gate reads: `noop` and `unit_size`, on the same card, with the values that
    make the sentence true for a real unit member and false for a lone file. If either
    field stops being served the gate silently reads `undefined` and the sentence vanishes
    or returns; this test is what makes that a red suite. The RENDERING is registered for
    HT-M10, where a human reads the card.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)

    def work(server):
        return _request(server, "GET", f"/plan/{plan_id}/items")

    cards = {c["old_filename"]: c for c in _drive(plan_id, root, work)["items"]}
    noop_card = cards["2019-07-04_001.NEF"]
    # The gate's two operands, both served, both on the card that renders the sentence.
    assert noop_card["noop"] is True
    assert noop_card["unit_size"] == 2

    # And the lone case the gate exists to exclude: a case-only rename over a file in no
    # group at all. `noop` is true - it really will not be renamed - and `unit_size` is 1,
    # so the group sentence must not render. B1's note is what speaks for this file.
    lone = tmp_path / "lone"
    lone.mkdir()
    _put(lone, "Holiday.jpg", 0)
    # NOT the `{orig}` template: under it this file is already conforming, gets no plan
    # item at all, and there is nothing to type a name into. The lone no-op is reachable
    # only through a TYPED case-only name, which is B1's case and the one C1 got wrong.
    _configure(lone, '[naming]\ntemplate = "{date}_{seq}"\n')
    run_scan(lone, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(lone)]) == 0
    capsys.readouterr()
    lone_plan = _open_plan_id(lone)
    conn = catalog(lone)
    try:
        item_id = repo.plan_rows(conn, lone_plan, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()

    def lone_work(server):
        _request(server, "POST", f"/item/{item_id}/rename", {"new_filename": "holiday.jpg"})
        return _request(server, "GET", f"/plan/{lone_plan}/items")

    lone_card = _drive(lone_plan, lone, lone_work)["items"][0]
    assert lone_card["noop"] is True
    assert lone_card["unit_size"] == 1


def test_pam_plan_does_not_call_a_no_op_item_a_file_that_would_be_renamed(
    tmp_path, capsys, monkeypatch
):
    """C2: `pam plan` and `pam apply` must describe the same plan the same way (rule 7).

    THE PLAN SURFACE SAID "2 files would be renamed" OVER A PLAN THAT RENAMES ONE. spec
    7.3 rev 16 puts a member whose name does not change into the plan, so the count of
    plan ITEMS stopped being the count of files that will move. `pam apply` was widened
    for this when the no-op item was introduced and correctly says
    "1 renames approved ... 1 already conforming"; `pam plan` was not, and it is the
    surface the user reads FIRST.

    THE EXPECTATIONS ARE SPELLED OUT AS LITERAL SENTENCES, not composed from
    `summary.planned` or `summary.noop_items`: an oracle built from the definition it
    checks agrees with a broken one perfectly, and this project has paid for that lesson
    more than once. Both halves are asserted - the number that must fall AND the number
    that must rise - because a fix that simply stopped printing the second line would
    satisfy a test that only checked the first.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    out = capsys.readouterr().out

    # ONE file moves, not two - and the singular noun goes with it.
    assert "1 file would be renamed, out of 2 pam can name." in out
    assert "2 files would be renamed" not in out
    # The no-op member is counted where it belongs instead.
    assert "1 already match the template." in out

    # ...and `pam apply` describes the SAME plan with the SAME two numbers, which is the
    # whole of what this condition is about.
    plan_id = _open_plan_id(root)
    _apply(root, plan_id, monkeypatch)
    applied = capsys.readouterr().out
    assert "1 renames approved" in applied
    assert "1 already conforming" in applied


def test_an_external_rename_of_a_no_op_member_is_refused_with_a_route(
    tmp_path, monkeypatch, capsys
):
    """M10 condition C1, spec 7.0 J12: the no-op item goes behind the existence check.

    THIS TEST USED TO ASSERT THE BUG, AND IT WENT RED THE MOMENT THE FIX LANDED - which is
    exactly what it was written for. Its construction is unchanged: stage 2 built this
    counter-example to a docstring that claimed it could not happen. `is_noop` compares
    against `old_rel_path`, where apply BELIEVES the file is, and the docstring argued that
    was safe because spec 7.1 step 1 and `execute_unit` check the disk afterwards. Neither
    runs for a no-op item: `actionable()` filters it out before both. So `pam apply`
    reported a clean "1 renamed, 0 failed", recorded `applied` for a path that is not
    there, and `pam verify` returned 12/12.

    `validator._moved_no_op_refusals` closes it with one `stat`, merged into the refusals
    BEFORE `blocked_units` is computed - so spec 7.3's containment refuses the companion
    too and the unit is not split (vision S3). Nothing is renamed and nothing is lost: the
    user is told where pam looked, and sent to `pam scan` because it is the CATALOG that is
    stale, not the plan.
    """
    root = tmp_path / "photos"
    _partial_unit(root)
    capsys.readouterr()
    plan_id = _open_plan_id(root)

    # The user renames the no-op member outside pam, after the plan was written.
    (root / "2019-07-04_001.NEF").rename(root / "EXTERNAL.NEF")

    # Per-file errors exit 1 (spec 13, the M4 review's amendment).
    assert _apply(root, plan_id, monkeypatch) == EXIT_EXPECTED
    out = capsys.readouterr().out
    assert "0 renamed, 2 failed" in out

    # NOTHING MOVED. The companion was refused with its member, so the unit is whole.
    assert _names(root) == {"EXTERNAL.NEF", "2019-07-04_001.acr_.png"}
    # Both members `failed`, and pam records `applied` for no path that is not on disk.
    assert _item_statuses(root) == {
        "2019-07-04_001.NEF": "failed",
        "2019-07-04_001.acr_.png": "failed",
    }
    # J1: a refusal writes no journal row, because no file was going to be moved.
    assert _journalled(root) == set()

    report = _report_text(root, out)
    assert "is not where pam last saw it" in report
    assert "Run: pam scan" in report
    # spec 7.3's containment, bought with no new sentence of its own.
    assert "pam renames a group together or not at all" in report


def _report_text(root: Path, out: str) -> str:
    """The session report `pam apply` wrote, read off the `Report:` line it printed."""
    lines = [line for line in out.splitlines() if line.strip().startswith("Report:")]
    assert lines, out
    return Path(lines[0].split("Report:", 1)[1].strip()).read_text(encoding="utf-8")

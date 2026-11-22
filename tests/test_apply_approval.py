"""F1 (HT-M12, ruled 2026-10-08): `pam apply` renames ONLY what was approved. M13 Task 1.

The owner approved 5 cards, typed `apply`, and 399 files were renamed: apply flipped every
`proposed` item to `approved` itself (M5 clarification 2). The ruling
(`docs/superpowers/specs/2026-10-08-ht-m12-f1-approval-amendment.md`, R1-R6):

  - R1 apply renames only `approved` items; the typed `apply` confirms and no longer approves;
  - R3 a companion unit is decided as a unit;
  - R4 a plan with nothing approved is refused; `--all` accepts every undecided name except
    low-confidence ones (and never a skip, M13-D1);
  - R5 the confirmation counts approved items only.

HOW THESE TESTS ARE BUILT, and why:

  - THE DANGEROUS CALL IS WATCHED, NOT ONLY THE OUTCOME (M697). A spy on
    `pam.execute.unit.perform_verified_rename` records every rename pam ATTEMPTED; an outcome
    check alone cannot tell "never renamed it" from "renamed it and put it back".
  - EVERY EXPECTATION IS SPELLED BY HAND FROM THE FIXTURE AND THE RULING - the file names, the
    counts, the sentences. Nothing is derived from a production selection or constant.
  - The approvals are written by the suite's OWN SQL (`tests/archives.py::approve_ids`), never
    through the production approve path.
"""

import json

import pytest
from archives import FAKE_COMMAND, approve_ids, catalog, digests, put

from pam.errors import PamError
from pam.execute import reporter
from pam.execute import unit as unit_mod
from pam.execute.apply import run_apply
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

# The fixture's dates, one per unit, so every proposed name is predictable and distinct.
LIVE = "2019:07:04 18:23:11"  # IMG_1.HEIC + IMG_1.MOV + IMG_1.AAE -> 2019-07-04_001.*
B_DATE = "2020:08:05 09:15:00"
C_DATE = "2021:09:06 11:02:03"
D_DATE = "2022:10:07 12:00:00"

THE_UNIT = ["IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE"]


def _six_files_four_units(tmp_path):
    """IMG_1.HEIC + IMG_1.MOV + IMG_1.AAE (one Live-Photo unit of three - `.aae` is a default
    sidecar, so it groups) and three singles B.JPG, C.JPG, D.JPG, all dated, so none is Low."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in THE_UNIT:
        put(root, name, LIVE)
    put(root, "B.JPG", B_DATE)
    put(root, "C.JPG", C_DATE)
    put(root, "D.JPG", D_DATE)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    conn = catalog(root)
    # The fixture's own shape, checked before anything else relies on it.
    rows = conn.execute(
        "SELECT old_rel_path, unit_key, tier, status FROM plan_items WHERE plan_id = ?",
        (plan_id,),
    ).fetchall()
    assert sorted(r["old_rel_path"] for r in rows) == sorted(
        [*THE_UNIT, "B.JPG", "C.JPG", "D.JPG"]
    )
    assert len({r["unit_key"] for r in rows}) == 4
    assert {r["tier"] for r in rows} <= {"high", "medium"}
    assert {r["status"] for r in rows} == {"proposed"}
    return root, conn, plan_id


def _ids_for(conn, plan_id, names):
    """The test's own SQL: the plan_items ids of these old_rel_paths, in the order given."""
    by_name = dict(
        conn.execute(
            "SELECT old_rel_path, id FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    )
    return [by_name[name] for name in names]


def _set_status(conn, plan_id, names, status):
    """The test's own SQL, for a state the review page would write (or never could)."""
    conn.executemany(
        "UPDATE plan_items SET status = ? WHERE plan_id = ? AND old_rel_path = ?",
        [(status, plan_id, name) for name in names],
    )
    conn.commit()


def _statuses_by_name(conn, plan_id, names):
    """The SET of statuses these items carry now, read with the test's own SQL."""
    marks = ",".join("?" for _ in names)
    return {
        row[0]
        for row in conn.execute(
            f"SELECT status FROM plan_items WHERE plan_id = ? AND old_rel_path IN ({marks})",
            (plan_id, *names),
        ).fetchall()
    }


def _all_statuses(conn, plan_id):
    return dict(
        conn.execute(
            "SELECT old_rel_path, status FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    )


def _plan_status(conn, plan_id):
    return conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()[0]


def _old_new(args, kwargs):
    """`perform_verified_rename`'s REAL signature is keyword-only (`pam/execute/rename.py`):
    `archive_root`, `old_rel`, `new_rel`, `expect_size`, `expect_mtime_ns`, `logger`.
    Every call is recorded - forward AND revert - so the assertion is about the exact set."""
    assert args == (), "perform_verified_rename takes keyword arguments only"
    return kwargs["old_rel"], kwargs["new_rel"]


@pytest.fixture()
def spy(monkeypatch):
    """Every (old_rel, new_rel) pam asked the one verified rename to perform."""
    calls = []
    real = unit_mod.perform_verified_rename

    def watching(*args, **kwargs):
        calls.append(_old_new(args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", watching)
    return calls


def _media(found):
    """A `digests` map with the fake exiftool's `.tags.json` sidecars left out."""
    return {sha: path for sha, path in found.items() if not path.endswith(".tags.json")}


# --- R1: exactly the approved items, and the call itself is watched ------------------


def test_apply_renames_exactly_the_approved_items_and_calls_rename_for_nothing_else(
    tmp_path, spy
):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approved = _ids_for(conn, plan_id, ["IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE", "B.JPG"])
    approve_ids(conn, approved)  # 4 of 6, spelled by hand
    before = _media(digests(root))

    summary = run_apply(plan_id, root)

    after = _media(digests(root))
    assert summary.renamed == 4
    assert summary.not_approved == 2
    assert summary.failed == 0
    # The CALL: every rename pam attempted, forward and back - exactly these four, forward.
    assert sorted(spy) == sorted(
        [
            ("IMG_1.HEIC", "2019-07-04_001.HEIC"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
            ("IMG_1.AAE", "2019-07-04_001.AAE"),
            ("B.JPG", "2020-08-05_001.JPG"),
        ]
    )
    # The bytes: the same six contents before and after (a digest is content, not a name).
    assert set(before) == set(after)
    assert len(after) == 6
    # The names: the two unapproved files kept theirs; the four approved did not.
    assert set(before.values()) & set(after.values()) == {"C.JPG", "D.JPG"}
    # The DB half - apply must not have APPROVED what it did not rename (the old flip):
    assert _statuses_by_name(conn, plan_id, ["C.JPG", "D.JPG"]) == {"proposed"}
    # M13-D2: a deliberately partial apply leaves the plan partially applied.
    assert _plan_status(conn, plan_id) == "partially_applied"


def test_an_untouched_plan_is_refused_and_nothing_moves(tmp_path, spy):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    before = digests(root)

    with pytest.raises(PamError) as refused:
        run_apply(plan_id, root)

    assert str(refused.value) == (
        f"Nothing in plan {plan_id} is approved. Run pam review {plan_id} to approve names,"
        f" or pam apply {plan_id} --all to accept everything except low-confidence names."
    )
    assert digests(root) == before
    assert spy == []
    assert _plan_status(conn, plan_id) == "open"
    assert set(_all_statuses(conn, plan_id).values()) == {"proposed"}


def test_a_plan_whose_only_approved_item_is_a_no_op_is_not_refused(tmp_path, spy):
    """The refusal's ONE operand is "is any item approved", not "is any item a rename": a
    plan whose only approved item already has its proposed name is applied (the no-op is
    resolved, J12), not refused - and nothing is renamed."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    # Make B.JPG's item a no-op by giving it the name it already has, then approve only it.
    conn.execute(
        "UPDATE plan_items SET new_filename = 'B.JPG', generated_filename = 'B.JPG'"
        " WHERE plan_id = ? AND old_rel_path = 'B.JPG'",
        (plan_id,),
    )
    conn.commit()
    approve_ids(conn, _ids_for(conn, plan_id, ["B.JPG"]))
    before = digests(root)

    summary = run_apply(plan_id, root)

    assert summary.renamed == 0
    assert summary.failed == 0
    assert spy == []
    assert digests(root) == before
    assert _statuses_by_name(conn, plan_id, ["B.JPG"]) == {"applied"}


def test_a_plan_with_nothing_approved_and_one_skip_is_refused(tmp_path, spy):
    """The other half of the operand pair: a skip is a decision, but not an approval."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    _set_status(conn, plan_id, ["D.JPG"], "skipped")
    before = digests(root)

    with pytest.raises(PamError, match=f"Nothing in plan {plan_id} is approved"):
        run_apply(plan_id, root)

    assert digests(root) == before
    assert spy == []
    assert _all_statuses(conn, plan_id)["D.JPG"] == "skipped"


def test_some_approved_reports_what_it_left(tmp_path):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approve_ids(
        conn, _ids_for(conn, plan_id, ["IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE", "B.JPG"])
    )

    summary = run_apply(plan_id, root)

    lines = reporter.apply_summary_lines(summary)
    assert (
        "  4 approved, 2 not approved and left as they are. To name them later: pam plan"
        in (lines)
    )
    # After the headline, never before it (UX 4: failures and facts below the headline).
    assert (
        lines.index(
            "  4 approved, 2 not approved and left as they are. To name them later: pam plan"
        )
        == 1
    )


def test_a_fully_approved_plan_prints_no_left_line(tmp_path):
    """The LEFT line is conditional: a plan with nothing left over must not say "0 not
    approved" - every clause after the headline is a fact the user needs, or absent."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approve_ids(conn, _ids_for(conn, plan_id, [*THE_UNIT, "B.JPG", "C.JPG", "D.JPG"]))

    summary = run_apply(plan_id, root)

    assert summary.not_approved == 0
    assert not any("not approved" in line for line in reporter.apply_summary_lines(summary))


# --- R4 / M13-D1: --all -------------------------------------------------------------


def _undated(root, name):
    """A file with no date anywhere (spec 6.3.4: `undated_NNN`, tier `low`). The sidecar
    carries only `PamTestSidecar`, so its bytes are unique and `digests` can tell it apart."""
    (root / name).write_bytes(f"the only copy of {name}, which has no date".encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({"PamTestSidecar": name}), encoding="utf-8"
    )


def _with_low_and_skip(tmp_path):
    """Two undecided high units (IMG_1's three, B.JPG), one undecided LOW unit of two
    (E.JPG + E.MOV with no date anywhere - spec 6.3.4), and one SKIPPED unit (C.JPG)."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in THE_UNIT:
        put(root, name, LIVE)
    put(root, "B.JPG", B_DATE)
    put(root, "C.JPG", C_DATE)
    for name in ("E.JPG", "E.MOV"):
        _undated(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    conn = catalog(root)
    tiers = dict(
        conn.execute(
            "SELECT old_rel_path, tier FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    )
    assert tiers["E.JPG"] == tiers["E.MOV"] == "low", tiers
    assert {tiers[n] for n in [*THE_UNIT, "B.JPG", "C.JPG"]} <= {"high", "medium"}, tiers
    units = conn.execute(
        "SELECT COUNT(DISTINCT unit_key) FROM plan_items WHERE plan_id = ?", (plan_id,)
    ).fetchone()[0]
    assert units == 4  # IMG_1 (3), B (1), C (1), E (2)
    _set_status(conn, plan_id, ["C.JPG"], "skipped")
    return root, conn, plan_id


def test_all_accepts_everything_but_low_and_never_a_skip(tmp_path, spy):
    root, conn, plan_id = _with_low_and_skip(tmp_path)
    before = _media(digests(root))

    summary = run_apply(plan_id, root, accept_all=True)

    after = _media(digests(root))
    assert sorted(old for old, _ in spy) == sorted([*THE_UNIT, "B.JPG"])
    assert summary.renamed == 4
    assert set(before) == set(after)
    assert set(before.values()) & set(after.values()) == {"C.JPG", "E.JPG", "E.MOV"}
    # The Low unit's size, and nothing for the skip: it is decided, not left undecided.
    assert summary.not_approved == 2
    statuses = _all_statuses(conn, plan_id)
    assert statuses["C.JPG"] == "skipped"
    assert statuses["E.JPG"] == statuses["E.MOV"] == "proposed"
    assert {statuses[n] for n in [*THE_UNIT, "B.JPG"]} == {"applied"}


def test_all_names_the_low_count_it_left_at_the_confirmation(tmp_path):
    root, conn, plan_id = _with_low_and_skip(tmp_path)
    seen = []

    def capture(confirmation):
        seen.append(confirmation)
        return False

    run_apply(plan_id, root, confirm=capture, accept_all=True)

    (confirmation,) = seen
    assert confirmation.approved == 4
    assert confirmation.not_approved == 2
    assert confirmation.excluded_low == 2
    lines = reporter.confirmation_lines(confirmation)
    assert (
        f"  ! 2 low-confidence names were not accepted - approve them one at a time"
        f" in pam review {plan_id}." in lines
    )


def test_all_over_a_plan_with_nothing_acceptable_is_refused(tmp_path, spy):
    """Every undecided unit is Low -> the Low branch of the refusal, nothing written."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("E.JPG", "E.MOV", "F.JPG"):
        _undated(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    conn = catalog(root)
    assert set(_all_statuses(conn, plan_id)) == {"E.JPG", "E.MOV", "F.JPG"}
    before = digests(root)

    with pytest.raises(PamError) as refused:
        run_apply(plan_id, root, accept_all=True)

    assert str(refused.value) == (
        f"Nothing in plan {plan_id} can be accepted with --all: every name you have not"
        " decided is low-confidence (3 of them) or belongs with a file you skipped."
        f" Run pam review {plan_id} to approve them one at a time."
    )
    assert digests(root) == before
    assert spy == []
    assert set(_all_statuses(conn, plan_id).values()) == {"proposed"}
    assert _plan_status(conn, plan_id) == "open"


def test_all_over_a_plan_whose_every_item_is_skipped_says_so(tmp_path, spy):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    _set_status(conn, plan_id, [*THE_UNIT, "B.JPG", "C.JPG", "D.JPG"], "skipped")
    before = digests(root)

    with pytest.raises(PamError) as refused:
        run_apply(plan_id, root, accept_all=True)

    assert str(refused.value) == (
        f"Nothing in plan {plan_id} is left to accept: you have skipped every name in it."
        f" Run pam review {plan_id} to change that, or pam plan for a new plan."
    )
    assert digests(root) == before
    assert spy == []


def test_all_never_unskips_a_member_of_a_mixed_unit(tmp_path, spy):
    """M13-D1 end to end (M2367): a unit with ONE member skipped and the others undecided is
    left exactly as it is by `--all` - the skip is never overwritten with an approval and the
    unit is never renamed. `test_accept.py`'s table guards the pure selection; this guards the
    WRITE (`_record_preflight` would otherwise approve over the user's skip and apply rename
    the whole unit, the skipped file included). Built by the suite's own SQL: the page widens
    every decision to the unit and cannot produce it, but an older pam or a crash can."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    _set_status(conn, plan_id, ["IMG_1.MOV"], "skipped")

    summary = run_apply(plan_id, root, accept_all=True)

    assert sorted(old for old, _ in spy) == ["B.JPG", "C.JPG", "D.JPG"]
    assert summary.renamed == 3
    assert summary.failures == []
    statuses = _all_statuses(conn, plan_id)
    assert statuses["IMG_1.MOV"] == "skipped"
    assert statuses["IMG_1.HEIC"] == statuses["IMG_1.AAE"] == "proposed"


def test_cancelling_all_writes_no_approval(tmp_path, spy):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    before = digests(root)

    summary = run_apply(plan_id, root, confirm=lambda _c: False, accept_all=True)

    assert summary.cancelled
    assert set(_all_statuses(conn, plan_id).values()) == {"proposed"}
    assert _plan_status(conn, plan_id) == "open"
    assert digests(root) == before
    assert spy == []


# --- R3: a unit is decided as a unit ------------------------------------------------


def test_a_unit_half_approved_is_left_whole(tmp_path, spy):
    """A state the page cannot produce (it widens every decision to the unit), built by the
    suite's own SQL: the HEIC approved, its MOV and AAE undecided."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approve_ids(conn, _ids_for(conn, plan_id, ["IMG_1.HEIC", "B.JPG"]))
    before = _media(digests(root))

    summary = run_apply(plan_id, root)

    assert summary.failures == [
        (
            "IMG_1.HEIC",
            "IMG_1.HEIC was left alone: another file it is grouped with was not approved, and"
            " pam renames a group together or not at all. Run: pam plan, then approve or skip"
            " the whole group together.",
        )
    ]
    assert spy == [("B.JPG", "2020-08-05_001.JPG")]
    after = _media(digests(root))
    assert set(before.values()) & set(after.values()) == {*THE_UNIT, "C.JPG", "D.JPG"}


def test_skipped_beats_undecided_in_one_unit(tmp_path, spy):
    """Precedence blocked > skipped > undecided. A two-member unit cannot express it - the
    approved member is the only candidate - so the unit of three carries both statuses."""
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approve_ids(conn, _ids_for(conn, plan_id, ["IMG_1.HEIC", "B.JPG"]))
    _set_status(conn, plan_id, ["IMG_1.MOV"], "skipped")

    summary = run_apply(plan_id, root)

    assert summary.failures == [
        (
            "IMG_1.HEIC",
            "IMG_1.HEIC was left alone: another file it is grouped with was skipped, and"
            " pam renames a group together or not at all. Run: pam plan, then approve or skip"
            " the whole group together.",
        )
    ]
    assert spy == [("B.JPG", "2020-08-05_001.JPG")]


# --- R5: the confirmation says what will happen -------------------------------------


def test_the_confirmation_counts_approved_only(tmp_path):
    root, conn, plan_id = _six_files_four_units(tmp_path)
    approve_ids(
        conn, _ids_for(conn, plan_id, ["IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE", "B.JPG"])
    )
    statuses_before = _all_statuses(conn, plan_id)
    seen = []

    def capture(confirmation):
        seen.append(confirmation)
        return False

    summary = run_apply(plan_id, root, confirm=capture)

    (confirmation,) = seen
    assert confirmation.approved == 4
    assert confirmation.not_approved == 2
    assert confirmation.excluded_low == 0
    lines = reporter.confirmation_lines(confirmation)
    assert lines[2].startswith("  4 renames approved   (6 proposed, 0 skipped by you,")
    assert lines[3] == "  2 not approved - they will not be renamed."
    assert not any("low-confidence names were not accepted" in line for line in lines)
    # Cancelling writes nothing.
    assert summary.cancelled
    assert _all_statuses(conn, plan_id) == statuses_before


def test_all_calls_the_one_accept_rule_with_no_extra_hold(tmp_path, monkeypatch):
    """M14 OD-1: "`pam apply --all` is unchanged" - the CLI passes NO `also_hold`, so it holds
    back Low and nothing else (M13's R4). Watched at the call, beside test_accept's default."""
    from pam.naming import accept

    root, _conn, plan_id = _with_low_and_skip(tmp_path)
    seen: list[dict[str, object]] = []
    real = accept.accept_all

    def spy(items, *args, **kwargs):
        seen.append({"args": args, "kwargs": kwargs})
        return real(items, *args, **kwargs)

    monkeypatch.setattr(accept, "accept_all", spy)
    run_apply(plan_id, root, confirm=lambda _c: False, accept_all=True)
    assert seen == [{"args": (), "kwargs": {}}]

"""M14 Task 5h (Z2): `pam plan` refuses over a grouping the catalog does not match (vision S3).

Record: `docs/superpowers/specs/2026-11-13-m14-r1-f1-amendment.md`, section Z2. Review R1's F1
route (c): a Ctrl+C, or any crash, during a console `pam scan` between the reads and the grouping
commit leaves a catalog whose companion units are not grouped. The next `pam plan` made each
member its own unit and `pam apply` renamed a Live Photo's photo and video apart, for good. The
same plan was built after a `pam.toml` edit to `scan.sidecar_exts` or `scan.ignore_stem_suffixes`
that no re-scan had followed. Z2: before it plans, `plan_locked` refuses - after 7.2's recovery and
before any plan row - while the stored grouping differs from 5.4's over the `present` rows under
the CURRENT config.

EVERY EXPECTATION BELOW IS SPELLED BY HAND FROM 5.4 AND FROM THE RECORD (CLAUDE.md: an oracle
built from the definition it checks is a survivor waiting to happen). Nothing here calls
`companions.decide_groups`, `group_companions` or `stem_key`, and nothing imports the production
sentence: the sentence is written out, letter for letter, from the record.
"""

import json
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, approve_and_apply, catalog, put

from pam.catalog import repo
from pam.cli import main
from pam.errors import PamError
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

LIVE_PHOTO_PAIR = ("IMG_1.JPG", "IMG_1.MOV")


def refusal(root: Path) -> str:
    """Z2's sentence, copied from the record by hand. ROOT is the archive root."""
    return (
        f"pam's companion groups are out of date - the last scan of {root} did not finish,"
        f" or pam.toml changed since. Run: pam scan {root}"
    )


def _archive(tmp_path, names) -> Path:
    root = (tmp_path / "photos").resolve()
    root.mkdir()
    for name in names:
        put(root, name)
    return root


def _configure(root: Path, body: str) -> None:
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(body, encoding="utf-8")


def _plans(root) -> list[tuple[int, str]]:
    """Every plan row as (id, status), by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        return [(r["id"], r["status"]) for r in conn.execute("SELECT id, status FROM plans")]
    finally:
        conn.close()


def _units_of(root, plan_id) -> dict[str, str]:
    """old rel_path -> its plan item's `unit_key`, by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        return {
            r["old_rel_path"]: r["unit_key"]
            for r in conn.execute(
                "SELECT old_rel_path, unit_key FROM plan_items WHERE plan_id = ?", (plan_id,)
            )
        }
    finally:
        conn.close()


def _refused(root) -> None:
    """`pam plan` is refused with Z2's sentence; returns nothing, raises if it planned."""
    with pytest.raises(PamError) as refused:
        run_plan(root)
    assert str(refused.value) == refusal(root)


def _interrupted_scan(root, monkeypatch) -> None:
    """A REAL `run_scan`, interrupted between its reads and its grouping commit (route (c)).

    No fault seam sits there, so `repo.clear_companion_groups` - the grouping block's first
    write, after every read has committed - raises `KeyboardInterrupt`, a real Ctrl+C's own
    exception. The record names this seam.
    """

    def ctrl_c(conn):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(repo, "clear_companion_groups", ctrl_c)
        with pytest.raises(KeyboardInterrupt):
            run_scan(root, exiftool_command=FAKE_COMMAND)


# --- route (c) ----------------------------------------------------------------------------------


def test_a_scan_interrupted_before_its_grouping_commit_is_refused_until_a_scan_finishes(
    tmp_path, monkeypatch
):
    """R1-F1 route (c), end to end. The pair is read for the first time and the scan dies after
    the reads: both rows are `present` and neither is grouped. 5.4 binds `IMG_1.JPG` and
    `IMG_1.MOV` (one stem, a display file and a live video) into one `live_photo` unit, so the
    plan is refused and writes no plan row. A complete scan then clears it, and the plan holds
    the pair in ONE unit."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    _interrupted_scan(root, monkeypatch)
    conn = catalog(root)
    try:
        rows = {
            r["rel_path"]: (r["status"], r["companion_group_id"])
            for r in conn.execute(
                "SELECT rel_path, status, companion_group_id FROM files"
                " WHERE rel_path NOT LIKE '%.tags.json'"
            )
        }
    finally:
        conn.close()
    assert rows == {"IMG_1.JPG": ("present", None), "IMG_1.MOV": ("present", None)}

    _refused(root)
    assert _plans(root) == []

    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)

    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == sorted(LIVE_PHOTO_PAIR), units
    assert len(set(units.values())) == 1, units


def test_the_refusal_exits_1_through_the_cli_and_prints_the_sentence(
    tmp_path, monkeypatch, capsys
):
    """design 12: a `PamError`, exit 1, the sentence on the console."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    _interrupted_scan(root, monkeypatch)
    capsys.readouterr()

    assert main(["plan", "--archive", str(root)]) == 1

    captured = capsys.readouterr()
    assert refusal(root) in captured.out + captured.err
    assert _plans(root) == []


# --- the pam.toml edits ----------------------------------------------------------------------


def test_removing_an_ignored_stem_suffix_without_a_rescan_is_refused(tmp_path):
    """`DSC_1.NEF` and `DSC_1.acr_.jpg` group as `raw_jpeg` only while `.acr_` is ignored (5.4).
    The key is then removed from `pam.toml` and no scan follows: by 5.4 under the CURRENT config
    the two stems differ and nothing groups, while the catalog still stores the pair - refused.
    A re-scan stores the current grouping (none) and clears it; the plan then gives each file its
    own unit."""
    root = _archive(tmp_path, ())
    put(root, "DSC_1.NEF")
    put(root, "DSC_1.acr_.jpg")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _configure(root, '[scan]\nignore_stem_suffixes = [".acr_"]\n')
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == {
            ("raw_jpeg", frozenset({"DSC_1.NEF", "DSC_1.acr_.jpg"}))
        }
    finally:
        conn.close()

    _configure(root, "")
    _refused(root)
    assert _plans(root) == []

    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == ["DSC_1.NEF", "DSC_1.acr_.jpg"], units
    assert len(set(units.values())) == 2, units


def test_an_ignored_stem_suffix_under_a_complete_scan_is_not_refused(tmp_path):
    """The other half of the `.acr_` case: while `.acr_` IS ignored and the last scan finished,
    the check groups `DSC_1.NEF` with `DSC_1.acr_.jpg` exactly as the scan stored them (5.4), so
    it plans, and the plan holds the pair in one unit. A check that ignored the configured
    suffixes would refuse here (measured: the test above cannot see that, because there the
    suffix is gone)."""
    root = _archive(tmp_path, ())
    put(root, "DSC_1.NEF")
    put(root, "DSC_1.acr_.jpg")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _configure(root, '[scan]\nignore_stem_suffixes = [".acr_"]\n')
    run_scan(root, exiftool_command=FAKE_COMMAND)

    plan = run_plan(root)  # not refused

    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == ["DSC_1.NEF", "DSC_1.acr_.jpg"], units
    assert len(set(units.values())) == 1, units


def test_adding_a_sidecar_extension_without_a_rescan_is_refused(tmp_path):
    """The record's binding bullet: media type is classified under the CURRENT config. The last
    scan typed `IMG_1.LRV` as `other` (`lrv` is not in the default `sidecar_exts`), so it stored
    no group. `lrv` is then added to `scan.sidecar_exts`: by 5.4 a sidecar sharing a display
    file's stem makes a `sidecar` group, so the stored grouping (none) is stale. A check that read
    the stored `files.media_type` would see `other` and plan. A re-scan clears it, and the plan
    holds the photo and its `.LRV` in one unit."""
    root = _archive(tmp_path, ("IMG_1.JPG", "IMG_1.LRV"))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == set()
        types = {
            r["rel_path"]: r["media_type"]
            for r in conn.execute(
                "SELECT rel_path, media_type FROM files WHERE rel_path NOT LIKE '%.tags.json'"
            )
        }
    finally:
        conn.close()
    assert types == {"IMG_1.JPG": "image", "IMG_1.LRV": "other"}

    _configure(root, '[scan]\nsidecar_exts = ["xmp", "aae", "lrv"]\n')
    _refused(root)
    assert _plans(root) == []

    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == ["IMG_1.JPG", "IMG_1.LRV"], units
    assert len(set(units.values())) == 1, units


def test_an_open_plan_survives_the_refusal_unchanged(tmp_path):
    """The record: "An open plan is left as it was." The refusal is before rule 5's supersede, so
    the plan made over the old grouping stays `open` and no second plan row is written."""
    root = _archive(tmp_path, ("IMG_1.JPG", "IMG_1.LRV"))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    first = run_plan(root)
    assert first.plan_id is not None
    before = _units_of(root, first.plan_id)

    _configure(root, '[scan]\nsidecar_exts = ["xmp", "aae", "lrv"]\n')
    _refused(root)

    assert _plans(root) == [(first.plan_id, "open")]
    assert _units_of(root, first.plan_id) == before


def test_removing_mov_from_video_exts_without_a_rescan_is_refused(tmp_path):
    """The check offers grouping only the files of an image, video or sidecar media type, as the
    scan does. `video_exts = ["mp4"]` makes `IMG_1.MOV` an `other` file, which the next walk would
    never offer to 5.4, so the stored `live_photo` pair is stale - refused. (5.4's own extension
    table still lists `mov`: only the media-type filter sees this edit.)"""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _configure(root, '[scan]\nvideo_exts = ["mp4"]\n')

    _refused(root)
    assert _plans(root) == []


def test_a_stored_kind_that_is_not_5_4s_is_refused(tmp_path):
    """Kind is half of the comparison (the record: "membership and kind"). The pair's members are
    right but its stored kind says `raw_jpeg` - a JPG and a MOV are a `live_photo` by 5.4 - as an
    older pam's kind rule could have left it. The suite's own SQL writes that kind."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        conn.execute("UPDATE companion_groups SET kind = 'raw_jpeg'")
        conn.commit()
    finally:
        conn.close()

    _refused(root)
    assert _plans(root) == []


def test_a_member_replaced_under_an_interrupted_scan_is_refused(tmp_path, monkeypatch):
    """Membership is the other half: the stored KINDS can agree while the members do not. The
    pair `IMG_1.JPG` + `IMG_1.MOV` is grouped; the video is then replaced by `IMG_1.MP4` and the
    re-scan dies before its grouping commit. The catalog marked `IMG_1.MOV` `missing` and still
    stores it in a `live_photo` group; 5.4 over the present files gives `live_photo` too, of
    `IMG_1.JPG` + `IMG_1.MP4`. Refused; a complete scan clears it, and the plan holds the photo
    and the new video in one unit."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "IMG_1.MOV").unlink()
    (root / "IMG_1.MOV.tags.json").unlink()
    put(root, "IMG_1.MP4")
    _interrupted_scan(root, monkeypatch)

    _refused(root)
    assert _plans(root) == []

    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == ["IMG_1.JPG", "IMG_1.MP4"], units
    assert len(set(units.values())) == 1, units


# --- recovery first ------------------------------------------------------------------------


def test_the_check_runs_after_recovery_and_plans_by_the_recovered_paths(tmp_path, set_fault):
    """A Live Photo pair's apply dies BETWEEN its members (`mid_companion_unit`): one member is
    renamed and committed, so its row names `2019-07-04_001.*` while the other still names
    `IMG_1.*` and the stored group holds both. 5.4 over those two paths groups nothing (two
    stems), so a check that ran BEFORE 7.2's recovery would refuse. Recovery puts the renamed
    member back (7.3's unit consistency), the grouping is 5.4's again, and the plan is NOT
    refused: it names the files by the recovered paths, in one unit."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    applied = run_plan(root)
    assert applied.plan_id is not None
    set_fault.arm("crash:mid_companion_unit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(applied.plan_id, root)
    set_fault.next_invocation()
    conn = catalog(root)
    try:
        crashed = sorted(r["rel_path"] for r in conn.execute("SELECT rel_path FROM files"))
    finally:
        conn.close()
    assert len([p for p in crashed if p.startswith("2019-07-04_001.")]) == 1, crashed

    plan = run_plan(root)

    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == sorted(LIVE_PHOTO_PAIR), units
    assert len(set(units.values())) == 1, units


# --- no false refusal ----------------------------------------------------------------------


def _put_tags(root: Path, name: str, tags: dict[str, str]) -> None:
    (root / name).write_bytes(f"the only copy of {name}".encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({**tags, "PamTestSidecar": name}), encoding="utf-8"
    )


def test_no_refusal_after_an_apply_or_an_undo_of_two_same_day_events(tmp_path):
    """5f-F1's construction (Task 5g): three morning photos and three evening videos on one day,
    one folder, the default template, so `{seq}` numbers both events from 001. Before Z3 the apply
    gave a photo and a video one stem, and 5.4 over the renamed paths would group what the catalog
    does not - a FALSE refusal right after an ordinary apply. With Z3 nothing shares a key: the
    plan after the apply, and after its undo, is not refused."""
    root = _archive(tmp_path, ())
    for i, minute in enumerate(("00", "05", "10"), start=1):
        _put_tags(root, f"IMG_{i}.JPG", {"DateTimeOriginal": f"2019:07:04 09:{minute}:00"})
        _put_tags(root, f"VID_{i}.MOV", {"CreationDate": f"2019:07:04 18:{minute}:00-07:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    first = run_plan(root)
    assert first.plan_id is not None
    applied = approve_and_apply(first.plan_id, root)
    assert sorted(p.name for p in root.glob("2019-07-04_*")) == [
        "2019-07-04_001.JPG",
        "2019-07-04_002.JPG",
        "2019-07-04_003.JPG",
        "2019-07-04_004.MOV",
        "2019-07-04_005.MOV",
        "2019-07-04_006.MOV",
    ]

    run_plan(root)  # not refused

    run_undo(applied.session_id, root)
    again = run_plan(root)  # not refused
    assert again.plan_id is not None


def test_a_placeholder_member_under_a_complete_scan_is_not_refused(tmp_path, monkeypatch):
    """A cloud placeholder is walked and stays `present` (`placeholder = 1`), so the last scan
    grouped it with its stem-mate. The check counts it too, and plans."""
    root = _archive(tmp_path, ("IMG_1.JPG",))
    put(root, "IMG_1.MOV", content=b"1234567")
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: st.st_size == 7)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.placeholders == 1
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == {
            ("live_photo", frozenset({"IMG_1.JPG", "IMG_1.MOV"}))
        }
    finally:
        conn.close()

    run_plan(root)  # not refused


def test_a_missing_member_under_a_complete_scan_is_not_refused(tmp_path):
    """The pair is scanned, then the video is deleted outside pam and the archive scanned again:
    its row is `missing` and the photo stands alone. A `missing` row is not on disk and the last
    scan did not group it, so the check leaves it out, and plans."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "IMG_1.MOV").unlink()
    (root / "IMG_1.MOV.tags.json").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == set()
        status = conn.execute(
            "SELECT status FROM files WHERE rel_path = 'IMG_1.MOV'"
        ).fetchone()["status"]
    finally:
        conn.close()
    assert status == "missing"

    plan = run_plan(root)  # not refused
    assert plan.plan_id is not None


# --- review closures (Task 5h review: M3259, M3264, M3265) ----------------------------------------


def test_a_refused_plan_never_reaches_the_plan_writer(tmp_path, monkeypatch):
    """The record: the refusal comes "before any plan row is written", and an open plan is left
    as it was. A test that re-reads the rows cannot tell "never wrote" from "wrote, then rolled
    back" (CLAUDE.md, M697; review M3259: the refusal moved after `insert_plan_items` and before
    the commit left every row-reading test green). So the three writers are WATCHED: after the
    refusal not one was called. The negative control - the same spies on the plan after a
    complete scan - sees all three, so the spies are live."""
    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    _interrupted_scan(root, monkeypatch)
    calls: list[str] = []
    for name in ("supersede_open_plans", "create_plan", "insert_plan_items"):
        real = getattr(repo, name)

        def spy(*args, _name=name, _real=real, **kwargs):
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(repo, name, spy)

    _refused(root)
    assert calls == []

    run_scan(root, exiftool_command=FAKE_COMMAND)  # the scan supersedes through the spy too
    calls.clear()
    run_plan(root)
    assert calls == ["supersede_open_plans", "create_plan", "insert_plan_items"]


def test_scan_and_plan_each_use_the_one_shared_grouping_and_nothing_else_decides_it(
    tmp_path, monkeypatch
):
    """The record: "One definition. The 'decide every group' step is ONE function, called by both
    `runner.py`'s grouping block and this check. ... A third copy is forbidden." An import is not a
    wiring, and an identical private copy changes no outcome (review M3264, M3265: both survived
    the whole suite). So `companions.decide_groups` is replaced by a stand-in that decides NO
    group: a scan then stores none for a Live Photo pair (the runner stores ITS result), and a plan
    after a complete scan of the pair is refused (the check compares ITS result). And no module in
    `pam/` other than `scan/companions.py` calls `group_companions`, the grouping it wraps."""
    import ast

    from pam.scan import companions

    root = _archive(tmp_path, LIVE_PHOTO_PAIR)
    asked: list[str] = []

    def no_groups(rel_paths, known, sidecar_exts, ignore_stem_suffixes):
        asked.append("decide")
        return []

    with monkeypatch.context() as patch:
        patch.setattr(companions, "decide_groups", no_groups)
        run_scan(root, exiftool_command=FAKE_COMMAND)
    assert asked == ["decide"]
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == set()
    finally:
        conn.close()

    run_scan(root, exiftool_command=FAKE_COMMAND)  # the real grouping: the pair, stored
    with monkeypatch.context() as patch:
        patch.setattr(companions, "decide_groups", no_groups)
        _refused(root)
    assert asked == ["decide", "decide"]

    package = Path(__file__).resolve().parents[1] / "pam"
    callers = set()
    for module in package.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "group_companions":
                    callers.add(module.relative_to(package).as_posix())
    assert callers == {"scan/companions.py"}


def test_decide_groups_keeps_only_known_members_and_drops_a_group_it_cannot_anchor():
    """`companions.decide_groups`' own contract, spelled by hand from its docstring (review
    M3261-M3263: no caller reaches it, because both offer only catalog paths, so nothing pinned
    it). `known` is the set of paths the catalog holds a row for:
    - `a/`: the `.AAE` is unknown, so the Live Photo keeps its photo and video only;
    - `b/`: the primary (the HEIC, a display file) is unknown - dropped, though two members are
      known;
    - `c/`: only the JPEG is known - one known member is no group, dropped."""
    from pam.scan.companions import CompanionGroup, decide_groups

    paths = [
        "a/IMG_1.AAE", "a/IMG_1.JPG", "a/IMG_1.MOV",
        "b/IMG_2.AAE", "b/IMG_2.HEIC", "b/IMG_2.MOV",
        "c/DSC_3.JPG", "c/DSC_3.NEF",
    ]  # fmt: skip
    known = {"a/IMG_1.JPG", "a/IMG_1.MOV", "b/IMG_2.AAE", "b/IMG_2.MOV", "c/DSC_3.JPG"}

    assert decide_groups(paths, known, ("xmp", "aae"), ()) == [
        CompanionGroup("live_photo", "a/IMG_1.JPG", ("a/IMG_1.JPG", "a/IMG_1.MOV"))
    ]

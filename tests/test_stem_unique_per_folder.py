"""M14 Task 5g (Z3): a stem is unique per folder - no two units share a grouping key.

Finding 5f-F1 (Task 5f's implementer, reproduced by the controller): under the default template
`{date}_{event-or-scene}_{people}_{seq}`, an archive with no labels and no enrichment renders
`{date}_{seq}`, and `{seq}` is scoped per (event, date) (design 6.1 rev 14) - so two same-day
events both number from `001`. Design 6.3 rule 2 made each FILENAME unique, never each stem, so a
morning photo became `2019-07-04_001.JPG` and an unrelated evening video `2019-07-04_001.MOV`, and
the next ordinary `pam scan` bound them by 5.4 into a `live_photo` unit: pam mis-associated two
files through its own names, which vision S3 forbids.

Z3 (adopted by the owner 2026-11-13; record `docs/superpowers/specs/2026-11-13-m14-r1-f1-amendment.md`)
amends rule 2: a proposed stem must not share a 5.4 grouping key with any file outside the unit in
its directory - a catalog file, or a name another unit of the plan already holds. The unit's own
current names stay free to it. A key collision continues the search exactly as a name collision
does (6.3.2: append `{seq}` at the unit's number, then the seeded increment).

EVERY EXPECTED NAME BELOW IS SPELLED BY HAND FROM THE SPEC (CLAUDE.md: an oracle built from the
definition it checks is a survivor waiting to happen). Nothing here calls `companions.stem_key` or
`group_companions` to decide what the answer should be.
"""

import json

from archives import FAKE_COMMAND, approve_and_apply, archive_names, catalog

from pam.catalog import repo
from pam.naming import collision
from pam.naming.collision import StemKeys, assign_unique_stem, reserve, taken_names
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

ROOT = "C:\\archive"


def _put(root, name, tags):
    """One archive file plus the sidecar `tests/fake_exiftool.py` reads its tags from."""
    (root / name).write_bytes(f"the only copy of {name}".encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({**tags, "PamTestSidecar": name}), encoding="utf-8"
    )


def _proposed(root, plan_id):
    """old filename -> proposed filename, read with the suite's OWN SQL."""
    conn = catalog(root)
    try:
        found = conn.execute(
            "SELECT old_rel_path, generated_filename FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        )
        return {str(r[0]): str(r[1]) for r in found}
    finally:
        conn.close()


def _two_same_day_events(tmp_path, folder=""):
    """5f-F1's archive: three morning photos and three evening videos, one day, one folder.

    09:00-09:10 and 18:00-18:10 are nine hours apart, beyond the default 4-hour `[events]
    gap_hours` (design 8.3), so they are two events; with no label, scene or people the default
    template renders `{date}_{seq}` for both (design 6.1). `folder` puts the six files one
    folder below the archive root.
    """
    root = tmp_path / "photos"
    where = root / folder if folder else root
    where.mkdir(parents=True)
    for i, minute in enumerate(("00", "05", "10"), start=1):
        _put(where, f"IMG_{i}.JPG", {"DateTimeOriginal": f"2019:07:04 09:{minute}:00"})
    for i, minute in enumerate(("00", "05", "10"), start=1):
        _put(where, f"VID_{i}.MOV", {"CreationDate": f"2019:07:04 18:{minute}:00-07:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


# Spelled from the spec, not computed. Units are named in primary rel_path order
# (`IMG_*` before `VID_*`). The photos' event numbers 1..3 (6.1 rev 14: `{seq}` per (event,
# date), ordered by capture time) and nothing holds their keys, so they keep 001..003. Each video's
# own event also numbers 1..3, but the key `2019-07-04_001` is now held by IMG_1's reserved name:
# rule 2 rev 15's search starts from the unit's number and takes the smallest HIGHER number whose
# key is free. VID_1 (seq 1): 2 and 3 are held by the photos -> 4. VID_2 (seq 2): 3 is a photo's,
# 4 is VID_1's -> 5. VID_3 (seq 3): 4 and 5 are the videos' -> 6. The record says the same:
# "In 5f-F1's example the videos become `_004`..`_006`."
F1_EXPECTED = {
    "IMG_1.JPG": "2019-07-04_001.JPG",
    "IMG_2.JPG": "2019-07-04_002.JPG",
    "IMG_3.JPG": "2019-07-04_003.JPG",
    "VID_1.MOV": "2019-07-04_004.MOV",
    "VID_2.MOV": "2019-07-04_005.MOV",
    "VID_3.MOV": "2019-07-04_006.MOV",
}


def test_two_same_day_events_get_different_stems_and_a_rescan_binds_nothing(tmp_path):
    """5f-F1 end to end: plan, apply, re-scan. Before Z3 the videos took `_001`..`_003` and the
    re-scan stored three `live_photo` pairs of unrelated files."""
    root = _two_same_day_events(tmp_path)
    summary = run_plan(root)
    assert summary.plan_id is not None
    assert _proposed(root, summary.plan_id) == F1_EXPECTED

    approve_and_apply(summary.plan_id, root)
    assert archive_names(root) == sorted(F1_EXPECTED.values())

    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == set(), "pam's own names bound unrelated files"
    finally:
        conn.close()


def test_two_same_day_events_one_folder_down_get_different_stems(tmp_path):
    """The Task 5g review (M3219): the planner reserves an assigned name's key UNDER ITS FOLDER
    (`reserve(..., rel_dir=)`). In the flat archive above the folder is the root, `""`, which was
    also the parameter's default until Q1 made it required - so dropping the argument was
    invisible there. One folder
    down, a key reserved under the root would leave the videos free to take the photos' stems.
    The names are F1_EXPECTED's (the folder does not enter the stem), with the folder in front."""
    root = _two_same_day_events(tmp_path, folder="2019")
    summary = run_plan(root)
    assert summary.plan_id is not None
    assert _proposed(root, summary.plan_id) == {
        "2019/IMG_1.JPG": "2019-07-04_001.JPG",
        "2019/IMG_2.JPG": "2019-07-04_002.JPG",
        "2019/IMG_3.JPG": "2019-07-04_003.JPG",
        "2019/VID_1.MOV": "2019-07-04_004.MOV",
        "2019/VID_2.MOV": "2019-07-04_005.MOV",
        "2019/VID_3.MOV": "2019-07-04_006.MOV",
    }


def test_a_missing_files_row_holds_its_key_against_a_new_name(tmp_path):
    """The Task 5g review (M3216), ruling concern 3: a catalog row whose file has gone
    (`missing`) holds its stem key exactly as it holds its NAME (`taken_names`, every catalog
    row). A missing file can come back - a cloud restore, a drive plugged in again - and the
    next scan would then bind it to a new name on its stem (5.4 groups what is on disk).

    `2019-07-04_001.MOV` is scanned, then deleted outside pam and scanned again, so its row is
    `missing`. `IMG_1.JPG` is the only unit on 2019-07-04, so its `{seq}` is 1, and rule 2's
    search moves it to 2 because the missing row holds `2019-07-04_001`."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "IMG_1.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
    _put(root, "2019-07-04_001.MOV", {"CreationDate": "2020:01:01 12:00:00-07:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "2019-07-04_001.MOV").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        status = conn.execute(
            "SELECT status FROM files WHERE rel_path = ?", ("2019-07-04_001.MOV",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "missing", "the construction needs a missing row"
    summary = run_plan(root)
    assert summary.plan_id is not None
    assert _proposed(root, summary.plan_id) == {"IMG_1.JPG": "2019-07-04_002.JPG"}


def test_an_ignored_editor_suffix_holds_its_stem_key_against_a_new_name(tmp_path):
    """With `scan.ignore_stem_suffixes = [".acr_"]`, `2019-07-04_001.acr_.NEF` shares a 5.4 key
    with `2019-07-04_001.JPG` (5.4: the suffix is stripped before stems are compared), so a photo
    of that day may not take `_001`. The control: without the option the key differs, and `_001`
    is free.

    The NEF is dated 2020-01-01, so its own proposal (`2020-01-01_001.NEF`) is nowhere near the
    photo's; the only thing between the photo and `_001` is the NEF's CURRENT name. `A.JPG` is
    the photo's only unit on 2019-07-04, so its `{seq}` is 1 and rule 2's search moves it to 2.
    """

    def plan(tmp, toml):
        root = tmp / "photos"
        root.mkdir(parents=True)
        _put(root, "A.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
        _put(root, "2019-07-04_001.acr_.NEF", {"DateTimeOriginal": "2020:01:01 12:00:00"})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        if toml is not None:
            ws = workspace_if_exists(root)
            assert ws is not None
            ws.config_path.write_text(toml, encoding="utf-8")
            run_scan(root, exiftool_command=FAKE_COMMAND)
        summary = run_plan(root)
        assert summary.plan_id is not None
        return _proposed(root, summary.plan_id)

    ignored = plan(tmp_path / "ignored", '[scan]\nignore_stem_suffixes = [".acr_"]\n')
    assert ignored["A.JPG"] == "2019-07-04_002.JPG"
    assert ignored["2019-07-04_001.acr_.NEF"] == "2020-01-01_001.NEF"

    control = plan(tmp_path / "control", None)
    assert control["A.JPG"] == "2019-07-04_001.JPG"


def test_an_already_conforming_unit_keeps_its_name(tmp_path):
    """Z3: "The unit's own current names stay free to it." A photo already named what pam would
    name it holds the key `2019-07-04_001` - and it is the one holding it, so it is not moved."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is None, "an already-conforming file is in no plan (6.3 rule 3)"
    assert summary.already_conforming == 1
    assert summary.errors == []


def test_a_unit_whose_members_share_a_key_with_each_other_is_unaffected(tmp_path):
    """A Live Photo pair IS one key by construction (5.4). Neither member blocks the other: one
    conforming pair keeps its names, and one non-conforming pair is named `_002` (its event's
    {seq} is 2, after the conforming pair's 09:00 photo) with BOTH members on the one stem."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
    _put(root, "2019-07-04_001.MOV", {"CreationDate": "2019:07:04 09:00:00-07:00"})
    _put(root, "IMG_9.JPG", {"DateTimeOriginal": "2019:07:04 09:30:00"})
    _put(root, "IMG_9.MOV", {"CreationDate": "2019:07:04 09:30:00-07:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    assert summary.already_conforming == 2
    assert _proposed(root, summary.plan_id) == {
        "IMG_9.JPG": "2019-07-04_002.JPG",
        "IMG_9.MOV": "2019-07-04_002.MOV",
    }


# Z3.1 (the controller, 2026-11-13, pending the design model; the Task 5g review's 5g-F1): the
# unit's own CURRENT KEY is free to it against the files already on disk. Before Z3.1 a photo
# already named `2019-07-04_001.JPG` was moved to `_002` because a missing row, or a file 5.4 does
# not group, shared its stem - a rename that separated nothing and that no earlier pam made.
def test_a_conforming_unit_beside_a_missing_row_of_its_own_stem_keeps_its_name(tmp_path):
    """`2019-07-04_001.MOV` is scanned, deleted outside pam and scanned again (`missing`). The
    photo beside it is already named what pam would name it; it stays, in no plan."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
    _put(root, "2019-07-04_001.MOV", {"CreationDate": "2020:01:01 12:00:00-07:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "2019-07-04_001.MOV").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        status = conn.execute(
            "SELECT status FROM files WHERE rel_path = ?", ("2019-07-04_001.MOV",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "missing", "the construction needs a missing row"
    summary = run_plan(root)
    assert summary.plan_id is None, "a conforming photo was moved off its own stem"
    assert summary.already_conforming == 1


def test_a_conforming_unit_beside_an_ungrouped_file_of_its_own_stem_keeps_its_name(tmp_path):
    """`.LRV` (a GoPro low-resolution proxy) is not a default `scan.sidecar_exts` entry, so 5.4
    does not group it with the photo of its stem - and it shares that stem's key. The photo
    already holds the key with it on disk; keeping its name creates nothing."""
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.JPG", {"DateTimeOriginal": "2019:07:04 09:00:00"})
    _put(root, "2019-07-04_001.LRV", {})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        assert repo.companion_membership(conn) == set(), "the construction needs no group"
    finally:
        conn.close()
    summary = run_plan(root)
    assert summary.plan_id is None, "a conforming photo was moved off its own stem"
    assert summary.already_conforming == 1


def test_the_own_key_frees_the_files_on_disk_and_never_a_name_this_plan_reserved():
    """The rule's two halves, on the index alone. A unit holding `2019-07-04_001.JPG` asks about
    its own stem: the `.LRV` on disk in that key is free to it, and a `.MOV` ANOTHER unit of this
    plan reserved into the key still blocks - freeing that would let pam create the very sharing
    Z3 forbids. A stem the unit does not hold frees nothing."""
    keys = StemKeys(["2019/2019-07-04_001.JPG", "2019/2019-07-04_001.LRV", "2019/X.LRV"], ())
    own = {"2019-07-04_001.jpg"}
    assert keys.holders("2019", "2019-07-04_001.JPG", own) == []
    keys.reserve("2019", "2019-07-04_001.MOV")
    assert keys.holders("2019", "2019-07-04_001.JPG", own) == ["2019-07-04_001.MOV"]
    assert keys.holders("2019", "X.JPG", own) == ["X.LRV"]


def test_reserving_into_the_key_index_needs_the_folder():
    """The Task 5g review's Q1: `rel_dir` defaulted to `""`, the archive root, so a caller that
    forgot it filed every key under the root (M3219, M3225 measured it). It is required now."""
    keys = StemKeys([], ())
    try:
        reserve(set(), ["2019-07-04_001.jpg"], keys=keys)
    except TypeError:
        pass
    else:
        raise AssertionError("a reservation into the key index was filed with no folder")
    assert keys.holders("", "2019-07-04_001.mov", set()) == []


def _assign(seq, dir_taken, keys, exts=None, own=()):
    return assign_unique_stem(
        ("date", "seq"),
        {"date": "2019-07-04"},
        archive_root_abs=ROOT,
        rel_dir="2019",
        member_exts=exts or {1: "jpg"},
        own_names=own,
        seq=seq,
        dir_taken=dir_taken,
        keys=keys,
    )


def test_a_lowercase_jpg_clashes_with_an_existing_uppercase_mov_of_the_same_stem():
    """The comparison is case-insensitive, as rule 2's own (Z3). `2019-07-04_001.jpg` is a free
    NAME beside `2019-07-04_001.MOV`, and it is NOT a free stem. The control is the same call
    without the key index: the name check alone takes `_001`."""
    disk = ["2019/2019-07-04_001.MOV"]
    keys = StemKeys(disk, ())
    assert _assign(1, taken_names(disk)["2019"], keys).names == {1: "2019-07-04_002.jpg"}
    assert _assign(1, taken_names(disk)["2019"], None).names == {1: "2019-07-04_001.jpg"}


def test_a_reservation_holds_its_key_for_the_next_unit():
    """Z3: "a name another unit of the same plan already holds". `reserve` updates BOTH the names
    and the keys, so a second unit with another extension cannot take the first unit's stem."""
    dir_taken: set[str] = set()
    keys = StemKeys([], ())
    first = _assign(1, dir_taken, keys)
    assert first.names == {1: "2019-07-04_001.jpg"}
    reserve(dir_taken, first.names.values(), keys=keys, rel_dir="2019")
    assert _assign(1, dir_taken, keys, exts={2: "mov"}).names == {2: "2019-07-04_002.mov"}


def test_a_thousand_keys_held_by_ANOTHER_extension_are_seeded_past_not_walked(monkeypatch):
    """The seed reads the keys too (Z3: "No per-unit O(directory) work"). 1000 numbers are held
    by `.nef` files only, so no `.jpg` NAME is taken and every key is. A search that consulted
    only the names would step one number per attempt and run out of its budget at 999; a seeded
    one arrives at 1001 in a handful of stem builds."""
    calls = []
    real = collision.build_stem
    monkeypatch.setattr(
        collision, "build_stem", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )
    disk = [f"2019/2019-07-04_{n:03d}.nef" for n in range(1, 1001)]
    result = _assign(1, taken_names(disk)["2019"], StemKeys(disk, ()))
    assert result is not None, "the key-blocked numbers exhausted the search"
    assert result.names == {1: "2019-07-04_1001.jpg"}
    assert len(calls) <= 8, f"the search climbed instead of seeding: {len(calls)} attempts"

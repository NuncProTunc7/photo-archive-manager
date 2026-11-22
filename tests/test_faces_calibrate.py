"""`pam faces calibrate` (M13 Task 14, C9, H7): design 11's sweep over the user's own catalog.

It replaces `tools/epsilon_sweep.py` (M12 Task 4, P4), whose tests this module inherits. The
tool read a `pam faces export`; the command reads the catalog, so the end-to-end tests below plant
a real scanned catalog and drive the real command.

EVERY EXPECTATION BELOW IS WORKED OUT BY HAND, and the working is here, because the table is
the thing under test: an expected table produced by calling `count`, `exclude`, `sweep` or the
tool would agree with any value of them (CLAUDE.md: an oracle built from the definition it
checks). Names are the privacy rule's stand-ins.

THE TOY CATALOG. Eighteen faces in three dimensions. Thirteen lie on the unit circle of the
first two axes at the angles below, so the cosine distance between two of them is
`1 - cos(angle between)`; at epsilon 0.300 two plane faces are neighbours when they are at most
acos(0.7) = 45.57 degrees apart (45 -> 0.2929, in; 50 -> 0.3572, out).

    id  label     conf  review_state  path               where
     1  Person A  0.40  -             a/1.jpg            0   deg (AT the floor - kept)
     2  Person A  0.90  -             a/2.jpg            10
     3  -         0.90  -             a/3.jpg            45
     4  Person B  0.90  -             b/4.jpg            90
     5  Person B  0.90  -             b/5.jpg            100
     6  Person C  0.90  -             c/6.jpg            150
     7  Person D  0.90  -             d/7.jpg            165
     8  Person E  0.90  -             e/8.jpg            215
     9  Person E  0.90  -             e/9.jpg            225
    10  Person E  0.90  -             e/10.jpg           275
    11  -         0.90  -             e/11.jpg           285
    12  Person F  0.90  -             f/12.jpg           (0, 0, 1): distance 1 from every plane face
    13  -         0.90  -             u/13.jpg           (0, 0, -1): distance 1, and 2 from Person F
    14  Person A  0.39  -             a/14.jpg           5    -> below the floor
    15  -         0.90  not_a_face    c/15.jpg           150  -> review_state set
    16  Person G  0.90  -             m/16.jpg           120  -> --exclude-label "Person G"
    17  -         0.90  -             Scans/noise1.jpg   60   -> --exclude-path Scans/*
    18  -         0.20  not_a_face    Scans/noise2.jpg   30   -> below the floor (the FIRST reason)

EXCLUSIONS (floor 0.4, `--exclude-label "Person G" --exclude-path "Scans/*"`): below the floor
{14, 18}; review_state {15}; label {16}; path {17}. Five of eighteen; face 18 meets three
reasons and is counted once, under the first. Face 1 sits exactly on the floor and is KEPT.

AT EPSILON 0.300, over the thirteen kept faces. Gaps round the circle: 0-10-45-90-100 are each
within 45.57 of the next; 100 -> 150, 165 -> 215, 225 -> 275 are 50 (out); 285 -> 360 is 75.
So the groups are {1,2,3,4,5}, {6,7}, {8,9}, {10,11}, and 12 and 13 are noise:

    groups 4, singletons 2
    merges 2:  {1..5} holds Person A + Person B, size 5 - INDIRECT: with every unnamed face
               removed (face 3 is the only bridge) the two fall apart
               {6,7} holds Person C + Person D, size 2 - DIRECT, 15 degrees apart
               {10,11} holds Person E and one unnamed face: ONE name, so NOT a merge
    split 1:    Person E's faces lie in {8,9} and {10,11}
    fragmented: Person F's only face is noise - 1 of 1

THE CLOSEST CROSS-PERSON PAIR: Person C (150) / Person D (165), 1 - cos 15 = 0.0341. Because it
is below 0.250, Person C and Person D are merged at EVERY value tried, and the toy's closing
sentence is the "no value to suggest" one.

THE BOUNDARY CATALOG (`BOUNDARY`), for the recommendation. Four faces on the plane:

    id  label     where
     1  Person A  0
     2  Person A  10
     3  Person B  60
     4  Person B  70

Within each person 10 degrees (0.0152). Across: 2-3 is 50 degrees (1 - cos 50 = 0.3572), 1-3
and 2-4 are 60 (0.5000), 1-4 is 70 (0.6580). So at 0.250, 0.275, 0.300, 0.325 and 0.350 there
are two groups and no merge; at 0.375 and above 2 and 3 are neighbours and the four faces are ONE
group holding both names, a DIRECT merge (no unnamed face exists). The largest value with no
merged people is therefore 0.350; the first merging value is 0.375.
"""

import io
import math
import os
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from archives import FAKE_COMMAND

from pam import __version__
from pam.catalog.db import connect
from pam.cli import main
from pam.enrich import faces_model
from pam.faces import calibrate
from pam.faces.calibrate import Counts, Face, Merge
from pam.lock import WorkspaceLock
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = ("yunet+sface", "toy000000000")


def plane(degrees: float) -> tuple[float, float, float]:
    r = math.radians(degrees)
    return (math.cos(r), math.sin(r), 0.0)


# (id, label, confidence, review_state, rel_path, vector) - the toy table in the docstring.
TOY = [
    (1, "Person A", 0.40, None, "a/1.jpg", plane(0)),
    (2, "Person A", 0.90, None, "a/2.jpg", plane(10)),
    (3, None, 0.90, None, "a/3.jpg", plane(45)),
    (4, "Person B", 0.90, None, "b/4.jpg", plane(90)),
    (5, "Person B", 0.90, None, "b/5.jpg", plane(100)),
    (6, "Person C", 0.90, None, "c/6.jpg", plane(150)),
    (7, "Person D", 0.90, None, "d/7.jpg", plane(165)),
    (8, "Person E", 0.90, None, "e/8.jpg", plane(215)),
    (9, "Person E", 0.90, None, "e/9.jpg", plane(225)),
    (10, "Person E", 0.90, None, "e/10.jpg", plane(275)),
    (11, None, 0.90, None, "e/11.jpg", plane(285)),
    (12, "Person F", 0.90, None, "f/12.jpg", (0.0, 0.0, 1.0)),
    (13, None, 0.90, None, "u/13.jpg", (0.0, 0.0, -1.0)),
    (14, "Person A", 0.39, None, "a/14.jpg", plane(5)),
    (15, None, 0.90, "not_a_face", "c/15.jpg", plane(150)),
    (16, "Person G", 0.90, None, "m/16.jpg", plane(120)),
    (17, None, 0.90, None, "Scans/noise1.jpg", plane(60)),
    (18, None, 0.20, "not_a_face", "Scans/noise2.jpg", plane(30)),
]
assert len(TOY) == 18, "the docstring's toy table has eighteen rows"

BOUNDARY = [
    (1, "Person A", 0.90, None, "a/1.jpg", plane(0)),
    (2, "Person A", 0.90, None, "a/2.jpg", plane(10)),
    (3, "Person B", 0.90, None, "b/3.jpg", plane(60)),
    (4, "Person B", 0.90, None, "b/4.jpg", plane(70)),
]

TOY_FACES = [
    Face(id=i, confidence=c, label=label, review_state=state, rel_paths=(path,))
    for i, label, c, state, path, _v in TOY
]

# The docstring's exclusions, by hand.
EXPECTED_EXCLUDED = {
    "below --floor": [14, 18],
    "review_state set": [15],
    "--exclude-label": [16],
    "--exclude-path": [17],
}
EXPECTED_KEPT = list(range(1, 14))

# The docstring's two partitions at epsilon 0.300, by hand. Group NUMBERS are arbitrary here
# (the counting must not depend on them), so they are deliberately not DBSCAN's 1..4.
FULL_AT_0300 = {1: 7, 2: 7, 3: 7, 4: 7, 5: 7, 6: 3, 7: 3, 8: 9, 9: 9, 10: 5, 11: 5}
FULL_AT_0300.update({12: None, 13: None})
LABELLED_AT_0300 = {1: 1, 2: 1, 4: 2, 5: 2, 6: 3, 7: 3, 8: 4, 9: 4, 10: None, 12: None}
LABELS = {i: label for i, label, *_ in TOY if i <= 13}

EXPECTED_AT_0300 = Counts(
    groups=4,
    singletons=2,
    merges=(
        Merge(group=3, labels=("Person C", "Person D"), size=2, indirect=False),
        Merge(group=7, labels=("Person A", "Person B"), size=5, indirect=True),
    ),
    split={"Person E": 2},
    fragmented={"Person F": 1},
)

# The closing sentences, spelled from the plan (M13 Task 14 Step 2) and NOT read off the module.
FEWER_THAN_TWO = (
    "pam needs at least two people you have named, with several photos each, to suggest a"
    " value. Name some faces first (pam faces label), then run this again."
)
NONE_CLEAN = (
    "Every value pam tried, from 0.250 to 0.500, put two people you have named in one group,"
    " so pam has no value to suggest. Check the names listed under each row"
    " (pam faces label), then run this again."
)

TABLE_HEADER = "epsilon  groups  singletons  merges  indirect  split  fragmented"


# --- the catalog, planted with the SUITE's own SQL -------------------------------------------


def blob(vector) -> bytes:
    """Design 4's BLOB with the suite's own packer, not the production decoder's inverse."""
    return struct.pack(f"<{len(vector)}f", *vector)


def archive(tmp_path: Path, rows, *, config: str = "") -> Path:
    """A scanned archive holding one file per row, and one face per row on that file's bytes."""
    root = tmp_path / "photos"
    for _i, _label, _c, _state, rel_path, _v in rows:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"the only copy of {rel_path}".encode())
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(config, encoding="utf-8")
    conn = connect(ws.db_path)
    try:
        conn.row_factory = sqlite3.Row
        sha = {
            str(r["rel_path"]): str(r["sha256"])
            for r in conn.execute("SELECT rel_path, sha256 FROM files")
        }
        persons: dict[str, int] = {}
        for face_id, label, confidence, state, rel_path, vector in rows:
            person_id = None
            if label is not None:
                if label not in persons:
                    cur = conn.execute("INSERT INTO persons (label) VALUES (?)", (label,))
                    persons[label] = int(cur.lastrowid)
                person_id = persons[label]
            conn.execute(
                "INSERT INTO face_instances (id, sha256, bbox_json, embedding, det_confidence,"
                " person_id, review_state, model_name, model_version)"
                " VALUES (?, ?, '{}', ?, ?, ?, ?, ?, ?)",
                (face_id, sha[rel_path], blob(vector), confidence, person_id, state, *MODEL),
            )
        conn.commit()
    finally:
        conn.close()
    return root


@pytest.fixture
def stack_present(monkeypatch):
    """The `[faces]` gate answered "installed" without importing onnxruntime.

    The repository's venv has numpy (Pillow and imagehash bring it) and not onnxruntime, and
    the sweep needs only numpy. The gate's own refusal is tested below with the gate REAL.
    """
    import numpy

    monkeypatch.setattr(faces_model, "import_runtime", lambda: (object(), numpy))


def run(capsys, *args: str) -> tuple[int, list[str]]:
    """Through `pam.cli.main`, where the exit-code mapping lives (see `test_faces_cluster.py`)."""
    capsys.readouterr()
    code = main(["faces", "calibrate", *args])
    return code, capsys.readouterr().out.splitlines()


# --- the pure functions, by hand (inherited from tools/epsilon_sweep.py's tests) -----------


def test_exclusion_is_by_the_first_reason_and_the_floor_is_inclusive():
    kept, excluded = calibrate.exclude(
        TOY_FACES, floor=0.4, labels=["Person G"], paths=["Scans/*"]
    )
    assert [f.id for f in kept] == EXPECTED_KEPT
    assert excluded == EXPECTED_EXCLUDED


def test_every_adjacent_pair_of_reasons_is_decided_in_the_documented_order():
    """F1 (M12 Task 4 review, M2036/M2037): two faces built by hand for the pairs the toy's
    one multi-reason face (decided by the floor) cannot reach.

      21  Person G  review_state not_a_face  Scans/a.jpg  -> meets 2, 3 and 4: counted under 2
      22  Person G  -                        Scans/b.jpg  -> meets 3 and 4:    counted under 3
    """
    faces = [
        Face(id=21, confidence=0.9, label="Person G", review_state="not_a_face",
             rel_paths=("Scans/a.jpg",)),
        Face(id=22, confidence=0.9, label="Person G", review_state=None,
             rel_paths=("Scans/b.jpg",)),
    ]  # fmt: skip
    kept, excluded = calibrate.exclude(faces, floor=0.4, labels=["Person G"], paths=["Scans/*"])
    assert kept == []
    assert excluded == {
        "below --floor": [],
        "review_state set": [21],
        "--exclude-label": [22],
        "--exclude-path": [],
    }


def test_the_counts_at_one_epsilon_are_the_ones_worked_by_hand():
    """Merges (one direct, one indirect), the one-name group that is NOT a merge, the split
    person and the fragmented one - from partitions written out by hand, no numpy."""
    assert calibrate.count(FULL_AT_0300, LABELLED_AT_0300, LABELS) == EXPECTED_AT_0300
    assert EXPECTED_AT_0300.indirect == 1


def test_the_closest_cross_person_pair_is_person_c_and_person_d():
    kept = {i: v for i, _l, _c, _s, _p, v in TOY if i <= 13}
    pair = calibrate.closest_cross_pair(kept, LABELS)
    assert pair is not None
    assert (pair.label_a, pair.label_b, pair.face_a, pair.face_b) == (
        "Person C",
        "Person D",
        6,
        7,
    )
    assert pair.distance == pytest.approx(1 - math.cos(math.radians(15)), abs=1e-6)


def test_a_tie_keeps_the_pair_with_the_lowest_ids():
    """F2 (M12 Task 4 review, M2038): A at +x, B at +y, C at -x - A/B and B/C both at 1.0."""
    vectors = {1: (1.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0), 3: (-1.0, 0.0, 0.0)}
    pair = calibrate.closest_cross_pair(vectors, {1: "A", 2: "B", 3: "C"})
    assert pair is not None
    assert (pair.face_a, pair.face_b, pair.distance) == (1, 2, 1.0)


def _row(merged: bool) -> Counts:
    merges = (Merge(group=1, labels=("A", "B"), size=2, indirect=False),) if merged else ()
    return Counts(groups=1, singletons=0, merges=merges, split={}, fragmented={})


def test_the_largest_clean_value_is_the_last_one_BEFORE_the_first_merge():
    """Hand-built rows: clean, clean, MERGE, clean. The answer is the second value - not the
    first merging one (mutation 1), and not the clean row AFTER a merge, which a `max` over
    the clean rows would offer. A first row with a merge has no answer at all."""
    rows = [(0.25, _row(False)), (0.3, _row(False)), (0.35, _row(True)), (0.4, _row(False))]
    assert calibrate.largest_clean(rows) == 0.3
    assert calibrate.largest_clean([(0.25, _row(True)), (0.3, _row(False))]) is None
    assert calibrate.largest_clean([(0.25, _row(False)), (0.5, _row(False))]) == 0.5


# --- the command, end to end over a real catalog -------------------------------------------


def test_the_command_over_the_toy_catalog_prints_the_table_worked_by_hand(
    tmp_path, stack_present, capsys
):
    """The docstring's toy, planted in a scanned catalog and read by the command - which is
    what `tools/epsilon_sweep.py` printed for the same faces exported, spelled by hand rather
    than produced by the tool. Person C and Person D merge at every value, so the closing
    sentence is the "no value" one."""
    root = archive(tmp_path, TOY)
    code, lines = run(
        capsys,
        "--archive",
        str(root),
        "--exclude-label",
        "Person G",
        "--exclude-path",
        "Scans/*",
    )
    assert code == 0, lines
    assert lines[:17] == [
        f"Face calibration for {root}",
        "",
        "Excluded 5 of 18 faces:",
        "  below --floor 0.4: 2",
        "  review_state set: 1",
        "  --exclude-label Person G: 1",
        "  --exclude-path Scans/*: 1",
        "Pack: yunet+sface toy000000000",
        "Considered: 13 faces, 10 labelled.",
        "  Person A: 2",
        "  Person B: 2",
        "  Person C: 1",
        "  Person D: 1",
        "  Person E: 3",
        "  Person F: 1",
        "Closest cross-person pair: 0.0341 (Person C / Person D, faces 6 and 7) - below it"
        " no epsilon can merge two labelled people directly.",
        TABLE_HEADER,
    ]
    at = lines.index("  0.300       4           2       2         1      1           1")
    assert lines[at + 1 : at + 5] == [
        "    merged (indirect): Person A + Person B in one group of 5",
        "    merged (direct): Person C + Person D in one group of 2",
        "    split: Person E across 2 groups",
        "    left alone: Person F 1 of 1",
    ]
    rows = [line for line in lines if line[:3] == "  0" and line[3] == "."]
    assert [row.split()[0] for row in rows] == [
        "0.250", "0.275", "0.300", "0.325", "0.350", "0.375",
        "0.400", "0.425", "0.450", "0.475", "0.500",
    ]  # fmt: skip
    assert lines[-2:] == ["", NONE_CLEAN]


def test_two_people_apart_get_the_largest_clean_value_worked_by_hand(
    tmp_path, stack_present, capsys
):
    """The boundary catalog: the WHOLE table by hand, and 0.350 recommended - never 0.375,
    the first value that merges Person A and Person B."""
    root = archive(tmp_path, BOUNDARY)
    ws = workspace_if_exists(root)
    assert ws is not None
    code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    clean = "       2           0       0         0      0           0"
    merged = "       1           0       1         0      0           0"
    detail = "    merged (direct): Person A + Person B in one group of 4"
    assert lines == [
        f"Face calibration for {root}",
        "",
        "Excluded 0 of 4 faces:",
        "  below --floor 0.4: 0",
        "  review_state set: 0",
        "  --exclude-label (none): 0",
        "  --exclude-path (none): 0",
        "Pack: yunet+sface toy000000000",
        "Considered: 4 faces, 4 labelled.",
        "  Person A: 2",
        "  Person B: 2",
        "Closest cross-person pair: 0.3572 (Person A / Person B, faces 2 and 3) - below it"
        " no epsilon can merge two labelled people directly.",
        TABLE_HEADER,
        "  0.250" + clean,
        "  0.275" + clean,
        "  0.300" + clean,
        "  0.325" + clean,
        "  0.350" + clean,
        "  0.375" + merged,
        detail,
        "  0.400" + merged,
        detail,
        "  0.425" + merged,
        detail,
        "  0.450" + merged,
        detail,
        "  0.475" + merged,
        detail,
        "  0.500" + merged,
        detail,
        "",
        "The largest value with no merged people is 0.350. To use it, add  cluster_epsilon"
        f" = 0.350  under [enrich.faces] in {ws.config_path}.",
    ]


def test_one_named_person_gets_the_fewer_than_two_sentence(tmp_path, stack_present, capsys):
    rows = [
        (1, "Person A", 0.90, None, "a/1.jpg", plane(0)),
        (2, "Person A", 0.90, None, "a/2.jpg", plane(10)),
        (3, None, 0.90, None, "u/3.jpg", plane(90)),
    ]
    code, lines = run(capsys, "--archive", str(archive(tmp_path, rows)))
    assert code == 0, lines
    assert "Considered: 3 faces, 2 labelled." in lines
    assert "Closest cross-person pair: none - fewer than two labelled persons." in lines
    assert lines[-2:] == ["", FEWER_THAN_TWO]


def test_the_people_are_counted_AFTER_the_exclusions(tmp_path, stack_present, capsys):
    """The boundary catalog with Person B excluded is a catalog of one person: no merge at any
    value, and still no recommendation - two clean groups of one name are not evidence."""
    code, lines = run(
        capsys, "--archive", str(archive(tmp_path, BOUNDARY)), "--exclude-label", "Person B"
    )
    assert code == 0, lines
    assert "  --exclude-label Person B: 2" in lines
    assert lines[-2:] == ["", FEWER_THAN_TWO]


def test_a_hidden_person_is_still_a_person_the_sweep_keeps_apart(
    tmp_path, stack_present, capsys
):
    """M2599 (Task 14 review): HIDDEN is about filenames, not identity. A hidden person merged
    into another's group would still put the wrong name on their photos, so the sweep counts
    them as named. The boundary catalog with Person B hidden must give the same answer as with
    Person B shown - 0.350 - and never "fewer than two"."""
    root = archive(tmp_path, BOUNDARY)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        hid = conn.execute("UPDATE persons SET hidden = 1 WHERE label = 'Person B'").rowcount
        conn.commit()
    finally:
        conn.close()
    assert hid == 1, "the fixture's own precondition: Person B exists and is now hidden"
    code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    assert "  Person B: 2" in lines
    assert lines[-1].startswith("The largest value with no merged people is 0.350.")


def test_the_floor_defaults_to_the_archives_faces_floor(tmp_path, stack_present, capsys):
    """No `--floor`: `[tiers] faces.floor` from THIS archive's pam.toml. At 0.95 every face of
    the boundary catalog (0.90) is below it; `--floor` given on the line wins over the file."""
    root = archive(tmp_path, BOUNDARY, config="[tiers.faces]\nfloor = 0.95\n")
    code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    assert "  below --floor 0.95: 4" in lines
    code, lines = run(capsys, "--archive", str(root), "--floor", "0.5")
    assert code == 0, lines
    assert "  below --floor 0.5: 0" in lines


def test_the_command_never_writes_pam_toml(tmp_path, stack_present, capsys, monkeypatch):
    """H7: "It never writes pam.toml." The bytes are unchanged AND no open for writing reaches
    the file - a write of the same bytes would pass the first check, so the CALL is watched
    (CLAUDE.md: a test that asserts the OUTCOME cannot tell "never did it" from "left no
    trace")."""
    root = archive(tmp_path, BOUNDARY, config="# the user's own file\n")
    ws = workspace_if_exists(root)
    assert ws is not None
    before = ws.config_path.read_bytes()
    target = os.path.normcase(os.path.abspath(ws.config_path))
    writes: list[tuple[str, object]] = []
    real_open, real_os_open = io.open, os.open

    def is_target(file) -> bool:
        try:
            return os.path.normcase(os.path.abspath(os.fspath(file))) == target
        except TypeError:
            return False

    def spy_open(file, mode="r", *args, **kwargs):
        if is_target(file) and any(flag in mode for flag in "wax+"):
            writes.append(("open", mode))
        return real_open(file, mode, *args, **kwargs)

    def spy_os_open(path, flags, *args, **kwargs):
        if is_target(path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT):
            writes.append(("os.open", flags))
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    # The spy's own control: a write through pathlib reaches it.
    probe = tmp_path / "probe.toml"
    target_was = target
    target = os.path.normcase(os.path.abspath(probe))
    probe.write_text("x", encoding="utf-8")
    assert writes, "the spy did not see a pathlib write - it would see nothing below either"
    writes.clear()
    target = target_was

    code, lines = run(capsys, "--archive", str(root))

    assert code == 0, lines
    assert "cluster_epsilon" in lines[-1]
    assert writes == []
    assert ws.config_path.read_bytes() == before


def test_it_answers_while_another_command_holds_the_lock(tmp_path, stack_present, capsys):
    """A lock-free reader, like `pam status`: it takes no lock, so it does not wait for one."""
    root = archive(tmp_path, BOUNDARY)
    ws = workspace_if_exists(root)
    assert ws is not None
    with WorkspaceLock(ws.lock_path):
        code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    assert lines[-1].startswith("The largest value with no merged people is 0.350.")


def test_both_catalog_reads_run_inside_one_snapshot(
    tmp_path, stack_present, capsys, monkeypatch
):
    """M2600 (Task 14 review): S13 names `pam faces calibrate` among the lock-free readers, so
    its two reads - the generation check and the faces - run INSIDE `read_snapshot`'s
    transaction. Watched on the real connection, from inside the run, as
    `test_snapshot_reads.py` watches the other readers."""
    from pam.catalog import repo

    root = archive(tmp_path, BOUNDARY)
    seen: list[tuple[str, bool]] = []
    for name in ("model_generations", "labelled_face_embeddings"):
        original = getattr(repo, name)

        def spy(conn, *args, _name=name, _original=original, **kwargs):
            seen.append((_name, conn.in_transaction))
            return _original(conn, *args, **kwargs)

        monkeypatch.setattr(repo, name, spy)
    code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    assert seen == [("model_generations", True), ("labelled_face_embeddings", True)], seen


def test_the_connection_it_reads_through_refuses_a_write(
    tmp_path, stack_present, capsys, monkeypatch
):
    """M2601 (Task 14 review): read-only is a property of the CONNECTION the command builds,
    asserted on that connection from inside the run (`test_report.py`'s M421 shape) - a run
    that only reads cannot tell `connect` from `connect_readonly` by its output. `CREATE
    TABLE` attempts a write whatever the catalog holds."""
    from pam.catalog import repo

    root = archive(tmp_path, BOUNDARY)
    probed: list[str] = []
    original = repo.labelled_face_embeddings

    def spy(conn):
        try:
            conn.execute("CREATE TABLE calibrate_probe (x INTEGER)")
        except sqlite3.OperationalError as exc:
            probed.append(str(exc))
        else:
            probed.append("WROTE")
        return original(conn)

    monkeypatch.setattr(repo, "labelled_face_embeddings", spy)
    code, lines = run(capsys, "--archive", str(root))
    assert code == 0, lines
    assert len(probed) == 1, "the probe never ran - this test would pass by doing nothing"
    assert "readonly" in probed[0], probed


def _alter_face_4(root: Path, sql: str, params: tuple) -> None:
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        assert conn.execute(sql, params).rowcount == 1, "the fixture's own precondition"
        conn.commit()
    finally:
        conn.close()


def test_two_model_generations_are_refused_and_nothing_is_swept(
    tmp_path, stack_present, capsys
):
    """M2603 (Task 14 review): A6 - distances between two embedding spaces are meaningless, so
    a sweep over them would recommend a number about nothing. Face 4 re-labelled as another
    generation: exit 1, the generation sentence, and no table on stdout."""
    root = archive(tmp_path, BOUNDARY)
    _alter_face_4(root, "UPDATE face_instances SET model_version = ? WHERE id = 4", ("other",))
    capsys.readouterr()
    assert main(["faces", "calibrate", "--archive", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "this archive holds faces from 2 different model generations" in captured.err
    assert "Traceback" not in captured.err


def test_embeddings_of_two_widths_are_refused_and_nothing_is_swept(
    tmp_path, stack_present, capsys
):
    """M2604 (Task 14 review): A6's second half - one generation string, two vector widths
    (3 and 4 values here), which cannot be compared at all."""
    root = archive(tmp_path, BOUNDARY)
    _alter_face_4(
        root, "UPDATE face_instances SET embedding = ? WHERE id = 4", (blob((1, 0, 0, 0)),)
    )
    capsys.readouterr()
    assert main(["faces", "calibrate", "--archive", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "face embeddings of 2 different widths (3 values, 4 values)" in captured.err
    assert "Traceback" not in captured.err


def test_without_the_faces_extra_it_refuses_with_the_extras_own_sentence(
    tmp_path, monkeypatch, capsys
):
    """H7: "Needs [faces]". The gate is REAL here; onnxruntime is made unimportable."""
    root = archive(tmp_path, BOUNDARY)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    capsys.readouterr()
    assert main(["faces", "calibrate", "--archive", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "onnxruntime is not installed" in captured.err
    assert f"Face detection is an optional extra. Run: {_pip('faces')}" in captured.err
    assert "Traceback" not in captured.err


def test_importing_the_command_imports_neither_numpy_nor_onnxruntime():
    """Mutation 3's own guard, beside `tests/test_deps.py`'s whole-package closure. A FRESH
    interpreter, because this session has numpy already (Pillow, imagehash)."""
    probe = (
        "import sys, pam.faces.calibrate\n"
        "print(int('numpy' in sys.modules), int('onnxruntime' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == ["0", "0"], result.stdout

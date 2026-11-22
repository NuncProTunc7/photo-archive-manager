"""HT tooling's `tools/ht_manifest.py snapshot --expect` and `changed`, pinned in BOTH directions.

M13 Task 16 Step 4. Split out of the private HT-M13 tools module at the M15 Task 14
review: the public suite relies on `ht_manifest` (it is `test_faces_purge.py`'s paths-and-bytes
oracle, and `tools/build_tiny_library.py` imports it), so its tests ship with it.

Each tool REFUSES rather than guesses, so each refusal is a test here beside the pass it guards.
EVERY EXPECTATION IS SPELLED BY HAND. NOTHING HERE TOUCHES THE REAL `%TEMP%`.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import ht_manifest  # noqa: E402

# The plan's refusal, verbatim (Task 16 Step 4), with n = 3 and N = 4.
STOP_3_OF_4 = (
    "STOP: 3 files, expected 4. Something was added to or removed from the folder before the"
    " snapshot. Put it back the way it was, then run this again."
)


@pytest.fixture(autouse=True)
def private_temp(tmp_path, monkeypatch):
    """`%TEMP%` for this test is a directory of its own - the real one is never written."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    return temp


def tree(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


# --- ht_manifest.py snapshot --expect ----------------------------------------------------------


def test_snapshot_with_a_wrong_count_says_STOP_and_writes_no_manifest(tmp_path, capsys):
    root = tree(tmp_path / "archive", {"a.jpg": b"a", "b.jpg": b"b", "sub/c.jpg": b"c"})
    manifest = tmp_path / "before.json"

    code = ht_manifest.main(["snapshot", str(root), str(manifest), "--expect", "4"])

    assert code != 0
    assert STOP_3_OF_4 in capsys.readouterr().out
    assert not manifest.exists(), "a refused snapshot must write nothing"


def test_snapshot_with_the_right_count_writes_the_manifest(tmp_path, capsys):
    root = tree(tmp_path / "archive", {"a.jpg": b"a", "b.jpg": b"b", "sub/c.jpg": b"c"})
    manifest = tmp_path / "before.json"

    assert ht_manifest.main(["snapshot", str(root), str(manifest), "--expect", "3"]) == 0

    assert "3 files" in capsys.readouterr().out
    assert sorted(json.loads(manifest.read_text(encoding="utf-8"))) == [
        "a.jpg",
        "b.jpg",
        "sub/c.jpg",
    ]


def test_snapshot_refuses_an_expect_that_is_not_a_number(tmp_path):
    root = tree(tmp_path / "archive", {"a.jpg": b"a"})
    manifest = tmp_path / "before.json"

    assert ht_manifest.main(["snapshot", str(root), str(manifest), "--expect", "lots"]) == 2
    assert not manifest.exists()


# --- ht_manifest.py changed --------------------------------------------------------------------


def _snapshot(root: Path, manifest: Path) -> None:
    assert ht_manifest.main(["snapshot", str(root), str(manifest)]) == 0


def test_changed_PASSES_with_exactly_the_renames_and_every_hash(tmp_path, capsys):
    root = tree(
        tmp_path / "archive", {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c", "d.jpg": b"a"}
    )
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest)
    (root / "a.jpg").rename(root / "2019-07-04_001.jpg")
    (root / "b.jpg").rename(root / "2020-08-05_001.jpg")
    capsys.readouterr()

    code = ht_manifest.main(["changed", str(manifest), str(root), "--expect", "2"])

    assert code == 0
    assert "PASS  2 files renamed, 2 unchanged, every hash present" in capsys.readouterr().out


def test_changed_FAILS_when_the_count_is_not_the_one_apply_reported(tmp_path, capsys):
    root = tree(tmp_path / "archive", {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c"})
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest)
    (root / "a.jpg").rename(root / "2019-07-04_001.jpg")
    (root / "b.jpg").rename(root / "2020-08-05_001.jpg")
    capsys.readouterr()

    code = ht_manifest.main(["changed", str(manifest), str(root), "--expect", "1"])

    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("FAIL")
    assert "a.jpg -> 2019-07-04_001.jpg" in out, "a FAIL names the first differences"


def test_changed_FAILS_when_a_hash_is_gone_even_with_the_right_count(tmp_path, capsys):
    root = tree(tmp_path / "archive", {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c"})
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest)
    (root / "a.jpg").rename(root / "2019-07-04_001.jpg")
    (root / "c.jpg").write_bytes(b"changed")
    capsys.readouterr()

    code = ht_manifest.main(["changed", str(manifest), str(root), "--expect", "1"])

    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("FAIL") and "c.jpg" in out


def test_changed_counts_a_rename_between_two_identical_files_once(tmp_path, capsys):
    """Duplicates: a renamed copy of a duplicated content is ONE rename, not two."""
    root = tree(tmp_path / "archive", {"x.jpg": b"same", "y.jpg": b"same"})
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest)
    (root / "y.jpg").rename(root / "z.jpg")
    capsys.readouterr()

    assert ht_manifest.main(["changed", str(manifest), str(root), "--expect", "1"]) == 0
    assert "PASS  1 files renamed, 1 unchanged, every hash present" in capsys.readouterr().out


def test_changed_refuses_the_two_paths_swapped_and_a_missing_expect(tmp_path):
    root = tree(tmp_path / "archive", {"a.jpg": b"a"})
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest)

    assert ht_manifest.main(["changed", str(root), str(manifest), "--expect", "0"]) == 2
    assert ht_manifest.main(["changed", str(manifest), str(root)]) == 2
    assert ht_manifest.main(["changed", str(manifest), str(root), "--expect", "0"]) == 0

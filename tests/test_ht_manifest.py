"""`tools/ht_manifest.py`'s comparison, pinned in both directions (M6 Task 2 Step 3).

WHY A TOOL GETS SUITE COVERAGE AT ALL. `m5.md` section 3.4's convention: a `tools/`
script is covered for its **pure functions** - anything that decides what goes into a
durable artifact or into a verdict - and not for its measurement or I/O half, which is
proven by running it. `tools/bench_scan.py` complies (forced by mutation M235, where a
CSV writer with no coverage shipped a fabricated number with the whole suite green).
This module is the outstanding case, and it is the more load-bearing of the two:
`verify_bytes` and `verify_exact` are what turn HT-M5's byte-integrity claim over hundreds of
irreplaceable files into a proof rather than an assertion.

WHAT IS AND IS NOT TESTED HERE. Only `verify_bytes` and `verify_exact`, over manifests
built by hand. Nothing here walks a tree, hashes a file or reads 2.86 GB of anything -
`snapshot` and `main` are the measurement half, and the M5 human test is what exercised
them. That split is the convention, not an omission.

BOTH DIRECTIONS FOR BOTH MODES, because a checker that cannot fail is not a checker:
each mode gets its passing case AND every way it must refuse. `--exact` gets the case
that separates the two modes - right content at a wrong path - which is the mode that
carries undo's "restores exact original names" promise. The reviewer's mutation is to
make `--exact` ignore paths (turning it into `--bytes`); if nothing here fails, the
mode proving undo's guarantee is untested.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ht_manifest  # noqa: E402

# Three distinct contents, so a manifest can be described by its hashes alone.
A = "a" * 64
B = "b" * 64
C = "c" * 64


def manifest(**paths):
    """{rel_path: sha256} as the real Entry shape. Sizes are incidental to both
    comparisons - they are recorded by `snapshot` for a human reading the file, and
    neither `verify_bytes` nor `verify_exact` looks at them."""
    return {
        rel: ht_manifest.Entry(size=len(rel), sha256=digest) for rel, digest in paths.items()
    }


BEFORE = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": B, "sub/IMG_3.JPG": C})


# --- --bytes: what `pam apply` guarantees ------------------------------------------


def test_bytes_passes_when_every_name_changed_and_no_content_did():
    """THE apply case, and the reason the mode exists. Every path in the archive is
    different after a rename, so a path comparison would fail an entirely healthy
    apply - `--bytes` asks only that the same multiset of content is still there."""
    after = manifest(
        **{"2019-07-04_001.JPG": A, "2020-08-05_001.JPG": B, "sub/2021-09-06_001.JPG": C}
    )

    assert ht_manifest.verify_bytes(BEFORE, after) is True


def test_bytes_passes_on_an_untouched_tree():
    """The degenerate case, so the negative tests below cannot pass by being vacuous."""
    assert ht_manifest.verify_bytes(BEFORE, BEFORE) is True


def test_bytes_fails_on_a_deleted_file():
    """The failure the whole human test exists to catch. pam promises it deletes
    nothing, and a rename that lost a file is indistinguishable from a rename that
    worked unless something counts the content."""
    after = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": B})

    assert ht_manifest.verify_bytes(BEFORE, after) is False


def test_bytes_fails_on_an_added_file():
    """Content that was not in the snapshot is as much a discrepancy as content that
    vanished: pam renames in place and creates nothing, so an appearance means either
    a copy where a rename was intended or something else writing into the archive
    mid-run (a sync client is the realistic one - hence "pause OneDrive")."""
    after = dict(BEFORE)
    after.update(manifest(**{"IMG_4.JPG": "d" * 64}))

    assert ht_manifest.verify_bytes(BEFORE, after) is False


def test_bytes_fails_when_one_files_content_changed():
    """Byte modification, at the same path. Vision principle 1: pam never modifies
    image bytes, and this is the check that would catch a violation of it. Note that
    the path set is IDENTICAL here - only the hash moved - so this cannot pass by
    accidentally comparing paths."""
    after = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": "d" * 64, "sub/IMG_3.JPG": C})

    assert ht_manifest.verify_bytes(BEFORE, after) is False


def test_bytes_counts_duplicates_rather_than_matching_sets():
    """Two files with identical content are two files. A set comparison would call
    this a pass - the surviving copy still carries the hash - and losing one of a
    duplicated pair is exactly the failure a hash-set check cannot see. `Counter` is
    what makes this a multiset comparison; this is the test that pins that choice."""
    before = manifest(**{"IMG_1.JPG": A, "copy/IMG_1.JPG": A})
    after = manifest(**{"IMG_1.JPG": A})

    assert ht_manifest.verify_bytes(before, after) is False


# --- --exact: what `pam undo` guarantees -------------------------------------------


def test_exact_passes_when_paths_and_content_both_match():
    """UX 4's "Undo restores exact original names", as a comparison."""
    assert ht_manifest.verify_exact(BEFORE, dict(BEFORE)) is True


def test_exact_fails_when_the_content_is_right_and_a_path_is_not():
    """THE test that separates the two modes, and the one the reviewer's mutation
    attacks. This tree passes `--bytes` - nothing was lost, nothing appeared, no byte
    changed - and it is precisely the state a HALF-COMPLETED undo leaves: the files
    are all there, under names that are not the ones the user started with. An
    `--exact` that ignored paths would report PASS here and the human test would
    certify a broken undo."""
    after = manifest(**{"2019-07-04_001.JPG": A, "IMG_2.JPG": B, "sub/IMG_3.JPG": C})

    assert ht_manifest.verify_bytes(BEFORE, after) is True, "the fixture must pass --bytes"
    assert ht_manifest.verify_exact(BEFORE, after) is False


def test_exact_fails_on_a_deleted_file():
    after = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": B})

    assert ht_manifest.verify_exact(BEFORE, after) is False


def test_exact_fails_on_an_added_file():
    after = dict(BEFORE)
    after.update(manifest(**{"IMG_4.JPG": "d" * 64}))

    assert ht_manifest.verify_exact(BEFORE, after) is False


def test_exact_fails_when_one_files_content_changed():
    """Same path, different bytes - the one discrepancy `verify_exact` finds through
    its third branch rather than through a set difference."""
    after = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": "d" * 64, "sub/IMG_3.JPG": C})

    assert ht_manifest.verify_exact(BEFORE, after) is False


# --- the reporting half of the verdict ---------------------------------------------


def test_a_failing_bytes_check_names_the_content_that_was_lost(capsys):
    """A verdict of FAIL that does not say WHICH file is a dead end (UX 1.4), and this
    tool is read by a person mid-human-test with hundreds of files in front of them."""
    after = manifest(**{"IMG_1.JPG": A, "IMG_2.JPG": B})

    assert ht_manifest.verify_bytes(BEFORE, after) is False
    out = capsys.readouterr().out

    assert "FAIL" in out
    assert "sub/IMG_3.JPG" in out


def test_a_failing_bytes_check_does_not_name_innocent_duplicates(capsys):
    """The reason `lost` is sliced to the count the multiset actually differs by. One
    of three identical files went missing; naming all three would tell the user two of
    their photos are gone when they are not."""
    before = manifest(**{"one.JPG": A, "two.JPG": A, "three.JPG": A})
    after = manifest(**{"one.JPG": A, "two.JPG": A})

    assert ht_manifest.verify_bytes(before, after) is False
    out = capsys.readouterr().out

    assert out.count(".JPG") == 1


def test_a_failing_exact_check_separates_missing_extra_and_changed(capsys):
    """Three different kinds of discrepancy, reported as three different kinds. A
    single "does not match" count would leave the reader unable to tell a lost file
    from a renamed one."""
    after = manifest(**{"IMG_1.JPG": "d" * 64, "IMG_2.JPG": B, "IMG_9.JPG": C})

    assert ht_manifest.verify_exact(BEFORE, after) is False
    out = capsys.readouterr().out

    assert "MISSING" in out and "sub/IMG_3.JPG" in out
    assert "EXTRA" in out and "IMG_9.JPG" in out
    assert "CONTENT CHANGED" in out and "IMG_1.JPG" in out

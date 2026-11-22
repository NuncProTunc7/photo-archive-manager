"""M13 Task 12 (P11): `proposed_families` spells each `{seq}` family once, not once per draft.

The old body built n(n-1)/2 names for a family of n drafts numbered 001..n. The new one groups
drafts by (directory, stem template with the three-digit part marked, that part's index,
extension) and spells
`1 .. highest-1` once per family. The OUTPUT SET must not move, so the old body is kept below,
COPIED and not imported, as the equivalence oracle.
"""

import random

import pytest

from pam.catalog import repo
from pam.naming import families
from pam.naming.collision import split_rel_path


def _draft(old_rel_path: str, generated_filename: str) -> repo.PlanItemDraft:
    return repo.PlanItemDraft(
        file_id=1,
        old_rel_path=old_rel_path,
        unit_key="u",
        generated_filename=generated_filename,
        tokens_json="[]",
        tier="high",
        trust_score=100,
        trust_reasons_json="[]",
    )


def _proposed_families_reference(drafts, built=None):
    """The pre-P11 body, copied verbatim; `built` (a list) counts the names it constructs."""
    skipped: set[str] = set()
    for draft in drafts:
        stem, dot, ext = draft.generated_filename.rpartition(".")
        if not dot:
            continue
        parts = stem.split("_")
        at = [i for i, part in enumerate(parts) if len(part) == 3 and part.isdigit()]
        if len(at) != 1:
            continue
        index = at[0]
        rel_dir, _ = split_rel_path(draft.old_rel_path)
        for lower in range(1, int(parts[index])):
            spelled = [*parts[:index], f"{lower:03d}", *parts[index + 1 :]]
            name = f"{'_'.join(spelled)}.{ext}"
            if built is not None:
                built.append(name)
            skipped.add((f"{rel_dir}/{name}" if rel_dir else name).lower())
    return skipped


def _family(directory: str, n: int, stem: str = "2019-07-04") -> list[repo.PlanItemDraft]:
    return [_draft(f"{directory}/IMG_{k}.jpg", f"{stem}_{k:03d}.jpg") for k in range(1, n + 1)]


def test_the_old_body_builds_n_n_minus_1_over_2_names_for_a_full_family():
    """Step 1's gate, kept as a test: the measurement the redesign rests on."""
    built: list[str] = []
    _proposed_families_reference(_family("2019", 999), built)
    assert len(built) == 999 * 998 // 2 == 498_501


def test_a_full_family_builds_names_linear_in_its_highest_number(monkeypatch):
    real = families._spell
    calls: list[tuple] = []

    def spy(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(families, "_spell", spy)
    drafts = _family("2019/a", 999) + _family("2019/b", 40)
    result = families.proposed_families(drafts)
    assert len(result) == 998 + 39  # the sentence is whole: every lower number, both folders
    assert 0 < len(calls) <= 2 * (999 + 40)
    assert len(calls) == len(result)  # and each name is spelled exactly once


def _random_drafts(rng: random.Random):
    drafts = []
    for _ in range(rng.randint(1, 25)):
        directory = rng.choice(["", "a", "a/b", "c"])
        shape = rng.choice(["one", "one", "one", "two", "zero", "wide"])
        prefix = rng.choice(["2019-07-04", "2020-01-02", "2019-07-04_beach"])
        n = rng.randint(1, 12)
        ext = rng.choice(["jpg", "JPG", "Jpg", "heic"])
        if shape == "one":
            name = f"{prefix}_{n:03d}.{ext}"
        elif shape == "two":
            name = f"{prefix}_007_{n:03d}.{ext}"
        elif shape == "zero":
            name = f"{prefix}.{ext}"
        else:
            name = f"{prefix}_{n:04d}.{ext}"
        old = f"{directory}/IMG_{rng.randint(1, 99)}.jpg" if directory else "IMG_1.jpg"
        drafts.append(_draft(old, name))
    return drafts


def _classes(draft) -> set[str]:
    stem = draft.generated_filename.rpartition(".")[0]
    ext = draft.generated_filename.rpartition(".")[2]
    found = {"dir" if split_rel_path(draft.old_rel_path)[0] else "root"}
    threes = [p for p in stem.split("_") if len(p) == 3 and p.isdigit()]
    found.add(f"parts{min(len(threes), 2)}")
    found.add("upper-ext" if ext != ext.lower() else "lower-ext")
    return found


def test_the_new_body_equals_the_old_over_200_seeded_random_lists():
    rng = random.Random(20261008)
    cases = [_random_drafts(rng) for _ in range(200)]
    assert len(cases) == 200 and all(cases)
    # The generator must actually make each class it claims, or it cannot test it.
    seen: set[str] = set()
    multi_dir = 0
    for case in cases:
        for d in case:
            seen |= _classes(d)
        dirs = {split_rel_path(d.old_rel_path)[0] for d in case if "_0" in d.generated_filename}
        multi_dir += len(dirs) >= 2
    assert {"dir", "root", "parts0", "parts1", "parts2", "upper-ext", "lower-ext"} <= seen
    assert multi_dir >= 20
    # Some case holds one stem's extension in two cases (any folder): the case-differing case.
    assert any(
        {
            d.generated_filename.rpartition(".")[2]
            for d in case
            if "_beach_" not in d.generated_filename
        }
        >= {"jpg", "JPG"}
        for case in cases
    )
    nonempty = 0
    for case in cases:
        expected = _proposed_families_reference(case)
        nonempty += bool(expected)
        assert families.proposed_families(case) == expected
    assert nonempty >= 100


@pytest.mark.parametrize(
    "drafts",
    [
        # the second directory's family must not be folded into the first's
        [_draft("a/x.jpg", "2019-07-04_005.jpg"), _draft("b/x.jpg", "2019-07-04_003.jpg")],
        # the HIGHEST number of a family decides, whatever the order drafts arrive in
        [_draft("a/x.jpg", "2019-07-04_002.jpg"), _draft("a/y.jpg", "2019-07-04_006.jpg")],
        [_draft("a/y.jpg", "2019-07-04_006.jpg"), _draft("a/x.jpg", "2019-07-04_002.jpg")],
        # extensions differing in case spell one lower-cased set
        [_draft("a/x.jpg", "2019-07-04_004.JPG"), _draft("a/y.jpg", "2019-07-04_002.jpg")],
    ],
)
def test_hand_built_corners_match_the_old_body(drafts):
    expected = _proposed_families_reference(drafts)
    assert expected  # the corner is not vacuous
    assert families.proposed_families(drafts) == expected


def test_the_reference_is_a_copy_and_never_calls_production():
    """M2560 (Task 12 review, F1): the oracle must be a COPY, not a call.

    An `if built is None: return families.proposed_families(drafts)` in the reference left the
    suite green, and with it a family key that merged two stem templates (M2561) - the random
    equivalence compared production with itself. The reference's own code may name no part of
    the module it checks.
    """
    names = set(_proposed_families_reference.__code__.co_names)
    assert names, "the reference has no code to inspect"
    assert not names & {"families", "proposed_families", "_spell"}


# Spelled BY HAND from spec 6.1 (`{seq}` is three digits; a stem is its parts joined by one `_`)
# and C11's per-directory family - no call to either implementation builds an expectation. Each
# row names the operand of the family key, or the bound of the range, it is there for.
_LITERAL_ROWS = [
    # the stem TEMPLATE (M2551): two templates in one folder are two families
    (
        [("a/x.jpg", "2019-07-04_003.jpg"), ("a/y.jpg", "2020-01-02_002.jpg")],
        {"a/2019-07-04_001.jpg", "a/2019-07-04_002.jpg", "a/2020-01-02_001.jpg"},
    ),
    # the EXTENSION (M2552): a .heic and a .jpg family of one stem in one folder are two
    (
        [("a/x.jpg", "2019-07-04_002.heic"), ("a/y.jpg", "2019-07-04_003.jpg")],
        {"a/2019-07-04_001.heic", "a/2019-07-04_001.jpg", "a/2019-07-04_002.jpg"},
    ),
    # the part INDEX (M2553): one template, the number in a different place (text contract)
    (
        [("a/x.jpg", "x__005.jpg"), ("a/y.jpg", "x_002_.jpg")],
        {"a/x__001.jpg", "a/x__002.jpg", "a/x__003.jpg", "a/x__004.jpg", "a/x_001_.jpg"},
    ),
    # the DIRECTORY (M2549): the same name in two folders, and the root beside a nested folder
    (
        [("a/x.jpg", "2019-07-04_004.jpg"), ("b/x.jpg", "2019-07-04_002.jpg")],
        {
            "a/2019-07-04_001.jpg",
            "a/2019-07-04_002.jpg",
            "a/2019-07-04_003.jpg",
            "b/2019-07-04_001.jpg",
        },
    ),
    (
        [("IMG.jpg", "2019-07-04_002.jpg"), ("a/b/IMG.jpg", "2019-07-04_002.jpg")],
        {"2019-07-04_001.jpg", "a/b/2019-07-04_001.jpg"},
    ),
    # the HIGHEST number decides (M2550), whatever the order, a repeated number included
    (
        [
            ("a/x.jpg", "2019-07-04_002.jpg"),
            ("a/y.jpg", "2019-07-04_004.jpg"),
            ("a/z.jpg", "2019-07-04_002.jpg"),
        ],
        {"a/2019-07-04_001.jpg", "a/2019-07-04_002.jpg", "a/2019-07-04_003.jpg"},
    ),
    # extension CASE: one lower-cased set
    (
        [("a/x.jpg", "2019-07-04_003.JPG"), ("a/y.jpg", "2019-07-04_002.jpg")],
        {"a/2019-07-04_001.jpg", "a/2019-07-04_002.jpg"},
    ),
    # an ambiguous stem (two three-digit parts, four digits) beside a plain one adds nothing
    (
        [
            ("a/x.jpg", "2019-07-04_007_003.jpg"),
            ("a/y.jpg", "2019-07-04_0005.jpg"),
            ("a/z.jpg", "2019-07-04_002.jpg"),
        ],
        {"a/2019-07-04_001.jpg"},
    ),
]


def test_literal_families_spelled_from_the_spec():
    """M2560/M2561 (F1): an oracle with no reference in it, so a co-mutated pair cannot pass.

    A plain loop over a module constant with its length SPELLED, never a parametrize - pytest
    skips an empty parametrize, and the rows would vanish with the suite green (M743, M2545).
    Each row is checked against production AND the copied reference: the third reading.
    """
    assert len(_LITERAL_ROWS) == 8
    for rows, expected in _LITERAL_ROWS:
        drafts = [_draft(old, new) for old, new in rows]
        assert families.proposed_families(drafts) == expected, rows
        assert _proposed_families_reference(drafts) == expected, rows

"""The seeded round-trip property harness (M5 Task 11 Step 2).

Moved out of `tests/test_faults_matrix.py` at M6 Task 1, unchanged. The M5 review
(docs/implementation-notes/m5.md section 3.5) ruled it a self-contained DIFFERENT
thing that shared a file with the fault matrix only because it was written in the
same task: no subprocess, no `REACH` applicability table, no fault injection at all -
its own seed discipline and its own world builder, asserting one property (plan ->
apply -> undo leaves the archive byte-and-name identical) over randomly shaped
archives.

The distinction from the five `pam/execute/` modules that same review declined to
split is the point: those have a genuine cohesion argument - splitting them separates
things that must be read together - and this has none.

`WORLD_SEED` and `_build_world` moved WITH it, unchanged.

**M8 Task 11 (deferred to M8 by name in `m6.md` section 3.4):** the shape-coverage
guard that used to close this module - `assert drawn == set(_WORLD_UNITS)`, checked
once after all 25 worlds were built - was ruled a property of assertions, not a hole in
the suite: a test detecting *its* deletion would be a second assertion about the first,
the identical problem one level up. The fix is `_draw_unit` below, which makes shape
coverage TRUE BY CONSTRUCTION instead of checked after the fact. Mutation M234
(`rng.choice(_WORLD_UNITS)` -> `_WORLD_UNITS[0]`) is re-run against this redesign and is
still caught - now by `_draw_unit`'s own bounded retry loop raising immediately, rather
than by the closing assertion, which becomes a narrower backstop (see its comment).

**Task 11's REVIEW added the three contract pins at the bottom of this module** (M996,
M997, M1000 and M1005 were survivors: the randomised remainder could be made degenerate,
the forcing branch could be DELETED, and `_WORLD_UNITS` could lose a shape, each with the
whole suite green). Read them before changing anything above: coverage here rests on
`_draw_unit`'s contract AND on `WORLDS` being large enough to call it six times, and each
half is now pinned by a test that DRIVES it rather than by an assertion watching a run.
"""

import hashlib
import random
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, approve_archive, assert_verify_agrees, catalog, put
from archives import device_mov_bytes as _device_mov_bytes
from archives import digests as _digests

from pam.catalog import repo
from pam.execute.apply import run_apply
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

WORLD_SEED = 20260807
WORLDS = 25

_WORLD_DATES = (
    "2019:07:04 18:23:11",
    "2020:08:05 09:15:00",
    "2021:09:06 11:02:03",
)
# The unit shapes an archive of real files is made of: a lone photo, a STANDALONE VIDEO, a
# live-photo pair, a three-member group, the `.AAE` sidecar pair, and RAW+JPEG.
# Extensions within a unit are always distinct (spec 5.4 safety property 1), and a
# `.MOV` member is written with real device-shaped bytes.
_WORLD_UNITS = (
    (".JPG",),
    (".MOV",),
    (".HEIC", ".MOV"),
    (".JPG", ".MOV", ".XMP"),
    (".MOV", ".AAE"),
    (".NEF", ".JPG"),
)

# `_draw_unit`'s coverage-forcing phase retries a draw that repeats an already-seen
# shape. With 6 equally-likely shapes the odds of a real `rng.choice` failing to
# produce the one still-missing shape in this many draws are astronomical (worst case
# (5/6)**200 ~= 1e-16) - simulated against WORLD_SEED, the worst single forced draw
# needed only 4 attempts. The bound exists so a generator that has genuinely stopped
# producing variety (M234) fails FAST and LOUDLY instead of spinning forever.
_MAX_UNIT_DRAWS = 200


def _draw_unit(rng: random.Random, seen: set[tuple[str, ...]]) -> tuple[str, ...]:
    """Draw one unit shape from `_WORLD_UNITS`.

    Shape coverage is TRUE BY CONSTRUCTION (M8 Task 11 Step 1), not checked after the
    fact: while any shape in `_WORLD_UNITS` has never been drawn this test run, this
    function keeps re-drawing until it finds one that is not yet in `seen` - so the
    first six times it is asked for a unit with incomplete `seen`, it can only return a
    shape not already in `seen`. Once the sixth distinct shape lands, the constraint
    lifts and every further call is an ordinary, unconstrained `rng.choice` - the
    "randomised remainder". `seen` is threaded through the WHOLE test (not per-world)
    because the property this replaces was cumulative across all 25 worlds, not
    per-world.

    A generator that stops producing variety (M234: `rng.choice(_WORLD_UNITS)` ->
    `_WORLD_UNITS[0]`) can draw the FIRST shape once, satisfying it, but then can never
    produce a second DISTINCT shape - the retry loop below exhausts `_MAX_UNIT_DRAWS`
    and raises, immediately, on the very next call made while coverage is incomplete.
    """
    if len(seen) < len(_WORLD_UNITS):
        for _ in range(_MAX_UNIT_DRAWS):
            unit = rng.choice(_WORLD_UNITS)
            if unit not in seen:
                seen.add(unit)
                return unit
        missing = set(_WORLD_UNITS) - seen
        raise AssertionError(
            f"{_MAX_UNIT_DRAWS} draws in a row never produced {missing} - shape "
            "coverage can no longer be guaranteed by construction (see _draw_unit)"
        )
    return rng.choice(_WORLD_UNITS)


def _build_world(root: Path, rng: random.Random, seen: set[tuple[str, ...]]) -> None:
    """One world. `seen` accumulates the unit shapes drawn across the WHOLE test (not
    just this world) - see `_draw_unit`, which mutates it in place."""
    root.mkdir(parents=True)
    for index in range(rng.randint(1, 5)):
        # `IMG_<n>` never looks like a date, so nothing is already-conforming and every
        # file earns a plan item - otherwise a world could round-trip by doing nothing.
        stem = f"IMG_{index}"
        date = rng.choice(_WORLD_DATES)
        unit = _draw_unit(rng, seen)
        for suffix in unit:
            put(
                root,
                f"{stem}{suffix}",
                date=date,
                content=_device_mov_bytes() + stem.encode()
                if suffix == ".MOV"
                else f"the only copy of {stem}{suffix}".encode(),
            )


def test_seeded_worlds_round_trip_plan_apply_undo(tmp_path):
    """Step 2: plan -> apply -> undo leaves the archive byte-and-name identical.

    A SIBLING of M4's `test_collision.py::test_randomized_worlds_never_swap_cycle_or_
    collide`, not an extension of it: that harness is a pure-function planner test that
    never touches a filesystem, and grafting a real scan/apply/undo round trip into it
    would change what it is and make the planner suite pay for the filesystem. The seed
    discipline is what carries over - one fixed seed, worlds derived from it in order,
    so a failure reproduces from `(WORLD_SEED, world)` alone and the assertion messages
    below carry both.

    25 worlds rather than M4's 300 because each world here is an archive of real files: a scan
    (which spawns the fake exiftool), a plan, an apply and an undo, at ~0.2s a world.
    300 would cost a minute of every suite run for coverage this harness does not have
    to explore - its variability is six unit shapes and three dates, not the unbounded
    collision space M4's planner harness walks. That 25 is enough for full shape
    coverage is now GUARANTEED rather than merely observed: `_draw_unit` cannot return a
    repeat while any shape is still missing, so a seed change (or a shrunken `WORLDS`)
    that leaves too few total draws to reach all six shapes fails the closing assertion
    below instead of quietly reducing what the property covers, and a generator that
    stopped producing variety at all (M234) fails inside `_draw_unit` itself, before a
    single world even finishes building.

    **J6 as a global property** is asserted per world here AND suite-wide by
    `conftest._no_unit_was_left_split`, which reads every catalog every test touched.
    """
    rng = random.Random(WORLD_SEED)
    seen: set[tuple[str, ...]] = set()
    for world in range(WORLDS):
        where = f"seed={WORLD_SEED} world={world}"
        root = tmp_path / f"world-{world}" / "photos"
        _build_world(root, rng, seen)
        original = _digests(root)

        run_scan(root, exiftool_command=FAKE_COMMAND)
        plan_id = run_plan(root).plan_id
        assert plan_id is not None, where

        approve_archive(root, plan_id)  # F1: apply renames only what was approved
        applied = run_apply(plan_id, root)
        assert applied.failed == 0, f"{where}: {applied.failures}"
        assert applied.renamed == len(original) - _sidecar_count(original), where
        # Something really was renamed, or the round trip proves nothing.
        assert _digests(root) != original, where

        undone = run_undo(applied.session_id, root)
        assert undone.failed == 0, f"{where}: {undone.failures}"
        assert _digests(root) == original, f"{where}: the archive did not come back"

        conn = catalog(root)
        try:
            # J6 as a global property: no unit ever ends with mixed done/failed members.
            assert repo.split_units(conn) == [], where
            assert repo.rows_in_state(conn, "intent") == [], where
            assert repo.plan_status(conn, plan_id) == "undone", where
        finally:
            conn.close()

        # Spec 7.5's closing sentence (M6 Task 4 Step 2): `pam verify` after every
        # apply/undo scenario. A CLEAN BILL is expected of every world - this harness
        # injects no faults and asserts the archive came back byte-and-name identical,
        # so a finding here would mean a completed, successful round trip left the
        # catalog describing something the checker does not recognise.
        assert_verify_agrees(root, where=where)

    # `_draw_unit` guarantees that IF at least six calls happen this run, `seen` reaches
    # every shape in `_WORLD_UNITS` - or the run already failed loudly, inside
    # `_draw_unit`, well before this line. What it CANNOT guarantee on its own is that
    # six calls happen at all: that is a property of `WORLDS` and each world's random
    # file count, not of the drawing function, which has no way to know no more calls
    # are coming. This closing assertion is therefore kept, but it is now a narrower,
    # DIFFERENT check than the one it replaces - a volume floor, not a shape-diversity
    # check - and it would catch a `WORLDS` reduction leaving too few total draws to
    # reach full coverage, which `_draw_unit`'s bound cannot see coming.
    #
    # **It is NOT the only thing that would, and that correction is Task 11's review.**
    # As shipped it was, which made this line load-bearing AND unprotected: deleting it
    # was green (M998), and deleting it *together with* `WORLDS = 1` retired the whole
    # harness to 0.34 s with 2186 tests passing (M1000). The volume floor now has its own
    # test - `test_the_configured_worlds_really_do_reach_every_shape`, which builds the
    # configured worlds and checks them against `_SPEC_UNIT_SHAPES`. So the claim this
    # comment used to make - that its own deletion is harmless - is finally TRUE, by
    # something other than its own say-so.
    assert seen == set(_WORLD_UNITS), (
        f"only {len(seen)}/{len(_WORLD_UNITS)} shapes drawn across {WORLDS} worlds - "
        f"missing {set(_WORLD_UNITS) - seen}"
    )


# --- the harness's own contract pins (M8 Task 11 REVIEW) -----------------------------
# M996, M997, M998 and M1005 were all SURVIVORS: the randomised remainder could be made
# degenerate (companion units 46 -> 4), the coverage-forcing branch could be DELETED
# outright, the closing assertion could be deleted, and `_WORLD_UNITS` could silently
# LOSE A SHAPE - each of them with the entire suite green.
#
# `m6.md` section 3.4 ruled that an assertion inside a harness cannot protect itself, and
# that a test detecting its removal would be a second assertion about the first - the
# identical problem one level up. That regress terminates HERE, and NOT as a regress,
# because `_draw_unit` is a FUNCTION WITH A CONTRACT: a contract is pinned by DRIVING the
# function with adversarial input, never by observing that one lucky seed behaved. Same
# shape as `test_the_shared_mov_bytes_really_are_a_device_shaped_quicktime_file` below -
# a helper cannot pin its own claim.
#
# `_SPEC_UNIT_SHAPES` is written out from spec 5.4's grouping rules and the unit shapes a
# real-world library is made of, and is deliberately NOT derived from `_WORLD_UNITS`, which is
# the symbol under test: an oracle built from the definition it checks is a survivor
# waiting to happen (CLAUDE.md, and M8's eleventh instance of that family was M1005).
_SPEC_UNIT_SHAPES = frozenset(
    {
        (".JPG",),  # a lone photo
        (".MOV",),  # a standalone video
        (".HEIC", ".MOV"),  # a live-photo pair
        (".JPG", ".MOV", ".XMP"),  # a three-member group
        (".MOV", ".AAE"),  # the Apple edit-sidecar pair (C15)
        (".NEF", ".JPG"),  # RAW+JPEG
    }
)


class _StuckGenerator:
    """A generator that has stopped producing variety - M234's defect, as an INPUT.

    `rng.choice` reduced to `seq[0]`, which is exactly what M234/M994/M995 mutate the
    harness into. Driving `_draw_unit` with it turns "the seed happened to be lucky"
    into "the function refuses to be unlucky".
    """

    def choice(self, seq: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
        return seq[0]


def test_the_harness_draws_from_every_unit_shape_the_spec_names():
    """M1005: the closing assertion compares `seen` against the very tuple that shrank.

    Dropping `(".NEF", ".JPG")` from `_WORLD_UNITS` left the whole suite green and
    silently stopped round-tripping RAW+JPEG, because a 5-shape harness reports full
    coverage of 5 shapes. The expectation is therefore spelled out from the spec side.
    """
    assert _SPEC_UNIT_SHAPES, "the oracle emptied itself - it would then assert nothing"
    assert set(_WORLD_UNITS) == _SPEC_UNIT_SHAPES, (
        "the harness no longer draws the unit shapes spec 5.4 describes - a shape added "
        "or removed here changes what the round trip covers, silently"
    )
    assert len(_WORLD_UNITS) == len(_SPEC_UNIT_SHAPES), "a shape is repeated in the tuple"


def test_the_configured_worlds_really_do_reach_every_shape(tmp_path):
    """M999/M1000: `WORLDS` reduced left coverage unreachable, and the ONLY thing standing
    in the way was one deletable assertion at the end of the round-trip test.

    `_draw_unit` guarantees coverage IF at least six calls happen; whether six calls
    happen is a property of `WORLDS` and the per-world file count, which it cannot see.
    That half is pinned here instead - by building the configured worlds for real (no
    scan, no plan, no apply: this is a check on the harness's SHAPE, not a round trip)
    and asserting against `_SPEC_UNIT_SHAPES` rather than against `_WORLD_UNITS`.

    With this in place the round-trip test's own closing assertion is genuinely
    redundant rather than merely unprotected - which is what M8's acceptance clause
    "the removed assertion's absence is now harmless" was asking for.
    """
    rng = random.Random(WORLD_SEED)
    seen: set[tuple[str, ...]] = set()
    for world in range(WORLDS):
        _build_world(tmp_path / f"shape-{world}" / "photos", rng, seen)

    assert seen == _SPEC_UNIT_SHAPES, (
        f"{WORLDS} worlds at seed {WORLD_SEED} draw only {len(seen)} of "
        f"{len(_SPEC_UNIT_SHAPES)} shapes - missing {_SPEC_UNIT_SHAPES - seen}. The "
        "round trip silently stopped covering part of what it claims to cover."
    )


def test_draw_unit_forces_variety_and_keeps_randomising_once_coverage_is_complete():
    """M997 and M996 - the two halves of `_draw_unit`, each pinned by driving it.

    **First half (M997).** Deleting the coverage-forcing branch entirely - `_draw_unit`
    reduced to a plain `rng.choice` - passed the whole suite, because `WORLD_SEED`
    happens to cover all six shapes on its own. So coverage was still seed-luck plus an
    assertion. Driven with a generator that has stopped producing variety, the forcing
    branch MUST raise; a plain `rng.choice` cannot.

    **Second half (M996).** The redesign split one draw site into two, and only the first
    was protected: degenerating the randomised remainder to a single shape collapsed
    companion units from 46 to 4 and left the suite green, because the closing assertion
    only ever asked for set MEMBERSHIP, which six forced draws already satisfy.
    """
    # First half: while coverage is incomplete, a stuck generator must fail loudly.
    seen: set[tuple[str, ...]] = set()
    assert _draw_unit(_StuckGenerator(), seen) == _WORLD_UNITS[0]
    with pytest.raises(AssertionError, match="never produced"):
        _draw_unit(_StuckGenerator(), seen)

    # Second half: once coverage is complete the remainder must still vary. A degenerate
    # remainder returns one shape forever and never reaches the rest.
    rng = random.Random(20260822)
    complete: set[tuple[str, ...]] = set(_SPEC_UNIT_SHAPES)
    drawn = {_draw_unit(rng, complete) for _ in range(300)}
    assert drawn == _SPEC_UNIT_SHAPES, (
        f"the randomised remainder produced only {sorted(drawn)} over 300 draws - it is "
        "the remainder, not the forced first pass, that supplies most of the harness's "
        "units, and a degenerate one costs the companion-unit coverage silently"
    )


def _sidecar_count(digests: dict[str, str]) -> int:
    """The fake exiftool's `.tags.json` files, which pam classifies as `other` and
    never renames. Subtracted so the renamed count can be asserted exactly rather
    than as "more than zero"."""
    return sum(1 for name in digests.values() if name.endswith(".tags.json"))


# --- the two helper pins, moved here at M6 Task 9 ------------------------------------
# Both were written in `test_faults_matrix.py`, the module that first owned the helpers.
# The helpers then moved to `tests/archives.py` (M6 Task 1) and this module became their
# other consumer - but the PINS stayed behind. The reason they moved HERE was the CI fast
# lane: it ran everything EXCEPT `test_faults_matrix.py`, so leaving the pins there would
# have had that lane exercising `device_mov_bytes` and `digests` while their contracts were
# checked only in the module it skipped.
#
# THAT LANE NO LONGER EXISTS - retired by the owner 2026-09-04 (design 9.4 rev 21) and
# deleted at M11 Task 1, so the premise above is HISTORY, not a live constraint. The pins
# stay here anyway, and the reason is now the simpler one: this module is a consumer of both
# helpers, and a helper is pinned where it is used. Do not read the retirement as a reason to
# move them back. Bodies unchanged by the move.


def test_the_digest_map_tells_one_name_in_two_directories_apart(tmp_path):
    """`_digests` keys by REL_PATH, and only a NESTED archive can tell the difference.

    Every archive these harnesses build is flat, so keying by the bare `path.name`
    passes the entire suite - and then spec 9.2's fifth assertion ("undo restores exact
    names") would be blind to a file put back under the right name in the wrong
    directory, which is exactly what `perform_verified_rename`'s `not_same_directory`
    refusal exists to prevent. A helper cannot pin itself, so the nested case is built
    here rather than crashed into - the same reason
    `test_the_disk_side_split_check_notices_a_group_under_two_stems` exists.
    """
    root = tmp_path / "nested"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "x.jpg").write_bytes(b"the only copy")
    digest = hashlib.sha256(b"the only copy").hexdigest()

    before = _digests(root)
    assert before == {digest: "a/x.jpg"}

    (root / "a" / "x.jpg").rename(root / "b" / "x.jpg")
    assert _digests(root) == {digest: "b/x.jpg"}
    assert _digests(root) != before, "the same name in another directory read as unchanged"


def test_the_shared_mov_bytes_really_are_a_device_shaped_quicktime_file():
    """`device_mov_bytes` promises "a REAL device-shaped QuickTime file ... rather
    than a text placeholder", and NOTHING asserted it.

    M6 review, mutation M256: returning `b"not a real QuickTime file, just a text
    placeholder"` from it left the **whole suite green** - 971 passed. Every fault-matrix
    cell that writes these bytes takes its capture facts from the fake exiftool's
    sidecar, so no cell has any reason to notice, and the one cell that uses real
    exiftool (`test_a_standalone_video_survives_a_crash_under_real_exiftool`) calls
    `write_samples` directly and never touches this helper. The promise is what makes
    the video rows a rev-3 shipping gate rather than a rename of an oddly-named text
    file, so it is asserted here: a helper cannot pin its own claim. Same shape as
    M219 and M227 in M5.
    """
    data = _device_mov_bytes()
    # ISO-BMFF: a 4-byte big-endian box length, then the box type. A device clip
    # starts with `ftyp`, and must carry both the media data and the metadata box.
    assert data[4:8] == b"ftyp", "not an ISO base media file at all"
    for box in (b"moov", b"mdat"):
        assert box in data, f"no {box.decode()} box - this is not a device-shaped clip"
    assert len(data) > 1024, "a stub, not a clip: real bytes are what the video rows rename"

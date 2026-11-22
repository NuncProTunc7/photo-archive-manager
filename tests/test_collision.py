import random

# MAX_ATTEMPTS is deliberately NOT imported. Every expectation in this module about
# the search's limits is spelled as a number, because a fixture that reads the constant
# moves with it and looks like coverage of it: raising it to 500000 turned one test red
# only because its input followed it, while nothing asserted the value was sane (F17,
# M1108/M1119). What pins the magnitude now is the cost of the give-up path.
from pam.naming.collision import (
    assign_unique_stem,
    reserve,
    split_rel_path,
    taken_names,
)
from pam.naming.template import build_stem

ROOT = "C:\\archive"


def assign(seq, dir_taken, own=(), order=("date", "seq"), exts=None):
    return assign_unique_stem(
        order,
        {"date": "2019-07-04", "orig": "IMG_1"},
        archive_root_abs=ROOT,
        rel_dir="2019",
        member_exts=exts or {1: "jpg"},
        own_names=own,
        seq=seq,
        dir_taken=dir_taken,
    )


def test_split_rel_path():
    assert split_rel_path("2019/a.jpg") == ("2019", "a.jpg")
    assert split_rel_path("a.jpg") == ("", "a.jpg")


def test_taken_names_is_per_directory_and_lowercased():
    assert taken_names(["2019/A.JPG", "2019/b.jpg", "c.jpg"]) == {
        "2019": {"a.jpg", "b.jpg"},
        "": {"c.jpg"},
    }


def test_a_free_name_is_used_as_is():
    assert assign(1, set()).names == {1: "2019-07-04_001.jpg"}


def test_a_taken_name_increments_the_sequence():
    taken = taken_names(["2019/2019-07-04_001.jpg"])["2019"]
    result = assign(1, taken)
    # N7: NameAssignment.seq is gone (it was set and never read). The proposed
    # names ARE the result, and they carry the winning number.
    assert result.names == {1: "2019-07-04_002.jpg"}
    # The returned stem must describe the winning attempt, not the first one that
    # collided - Task 7 builds tokens_json provenance straight from stem.emitted,
    # so a stale stem would ship provenance for a filename that was never proposed.
    assert result.stem.stem == "2019-07-04_002"
    assert all(name == f"{result.stem.stem}.jpg" for name in result.names.values())
    taken.add("2019-07-04_002.jpg")
    assert assign(1, taken).names == {1: "2019-07-04_003.jpg"}


def test_the_units_own_name_is_free_to_itself():
    # This is what makes "already conforming" work (spec 6.3.3).
    taken = taken_names(["2019/2019-07-04_001.JPG"])["2019"]
    assert assign(1, taken, own={"2019-07-04_001.JPG"}).names == {1: "2019-07-04_001.jpg"}
    # ... and the comparison is case-insensitive in both directions, because
    # Windows filenames are.
    assert assign(1, taken).names == {1: "2019-07-04_002.jpg"}


def test_any_member_collision_bumps_the_whole_unit():
    # A companion unit renames as one journaled unit (spec 5.4): if one member's
    # name is taken, the whole group moves on.
    taken = taken_names(["2019/2019-07-04_001.cr2"])["2019"]
    result = assign(1, taken, exts={1: "jpg", 2: "cr2"})
    assert sorted(result.names.values()) == [
        "2019-07-04_002.cr2",
        "2019-07-04_002.jpg",
    ]


def test_seq_is_appended_only_when_the_template_omits_it():
    # spec 6.3.2: "on collision, append {seq}".
    assert assign(1, set(), order=("date",)).names == {1: "2019-07-04.jpg"}
    taken = taken_names(["2019/2019-07-04.jpg"])["2019"]
    # rev 6 (N6): appending uses the unit's CURRENT number. This assertion said
    # _002 before the fix - the append and the increment happened in one step, so
    # unit 1's first numbered attempt was already 002 and _001 could never appear.
    assert assign(1, taken, order=("date",)).names == {1: "2019-07-04_001.jpg"}


def test_appending_seq_uses_the_units_own_number_then_increments():
    # N6 / spec 6.3.2 rev 6, the reproduction from the M4 review at unit level:
    # three same-day photos under --template "{date}" get seq 1, 2, 3 from the
    # planner. Before the fix they produced 2019-07-04, _003, _004 - safe, but
    # _001 and _002 never appeared and the numbering was inexplicable.
    dir_taken: set[str] = set()
    got = []
    for seq in (1, 2, 3):
        result = assign(seq, dir_taken, order=("date",))
        got.append(result.names[1])
        reserve(dir_taken, result.names.values())
    assert got == ["2019-07-04.jpg", "2019-07-04_002.jpg", "2019-07-04_003.jpg"]


def test_a_further_collision_after_appending_seq_increments():
    # The other half of rev 6: the FIRST fallback keeps the unit's number, a
    # SECOND collision increments it. Unit 1's bare name and its _001 are both
    # held, so it must land on _002.
    taken = taken_names(["2019/2019-07-04.jpg", "2019/2019-07-04_001.jpg"])["2019"]
    assert assign(1, taken, order=("date",)).names == {1: "2019-07-04_002.jpg"}


def test_a_numbered_template_starts_numbered_and_therefore_moves_on_a_collision():
    # Mutation-gate finding (M36). A template already carrying {seq} starts numbered,
    # so its FIRST collision must move to another number; a unit that re-proposed the
    # name that just collided would make no progress at all. rev 15's seed did not
    # change that - it changed how far the move goes - so this stays the test that a
    # numbered template is treated as numbered from attempt zero.
    #
    # (Before rev 15 this case was also the exhaustion boundary: 999 numbers held, and
    # only a unit with its full budget of 1000 attempts reached _1000. It no longer
    # bounds anything - see the two tests below - so the assertion is now about the
    # name and not about the budget.)
    #
    # 999 is SPELLED OUT rather than taken from MAX_ATTEMPTS. Reading the held set off
    # the constant made this test move with it, which looked like coverage of the
    # constant and was not: raising MAX_ATTEMPTS to 500000 turned this red only because
    # the fixture followed it, while nothing anywhere asserted the value was sane
    # (M1108). What this test is about is the NAME, so its input is a number.
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 1000)])["2019"]
    result = assign(1, blocked)
    assert result is not None
    assert result.names == {1: "2019-07-04_1000.jpg"}


def test_a_thousand_taken_numbers_no_longer_exhaust_the_search():
    # spec 6.3 rule 2 rev 15 (amendment A7), and the reason it exists. Under rev 14's
    # (event_id, date) scope a unit must climb past every number an EARLIER EVENT
    # reserved on the same date in the same directory, and before the seed the climb
    # was one number per attempt against a budget of MAX_ATTEMPTS + 1. Measured end to
    # end before the change: 1000 units in event A plus 3 in event B, one date, one
    # directory, planned 1002 of 1003 and dropped `b00001.jpg` with
    # "b00001.jpg: no free name after 999 tries". The boundary was sharp - 999 units in
    # event A planned all 1002.
    #
    # 1000 held numbers is one MORE than the old budget could clear, so a seed that
    # merely raised the constant would still have to be raised again for 1001.
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 1001)])["2019"]
    result = assign(1, blocked)
    assert result is not None
    assert result.names == {1: "2019-07-04_1001.jpg"}


def test_the_ceiling_is_gone_rather_than_raised():
    # "Gone" means no constant is left that a large enough archive can exhaust. 5000
    # held numbers is five times the old budget and crosses the three-to-four digit
    # width boundary that `_next_free_number` deliberately refuses to reason across in
    # one go - so this also proves the caller's re-ask at the boundary works, which is
    # the one place the seed hands control back to the plain step.
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 5001)])["2019"]
    result = assign(1, blocked)
    assert result is not None
    assert result.names == {1: "2019-07-04_5001.jpg"}


def test_the_seed_never_skips_a_free_number_in_the_middle_of_a_run():
    # The amendment's whole justification is that seeding CHANGES NO NAME, and this is
    # the case a lazier seed gets wrong: "jump to the high-water mark" would send this
    # unit to _901 when _400 is sitting free. The old upward walk found _400, so the
    # seed must too - a number too high is a filename nobody chose.
    held = [n for n in range(1, 901) if n != 400]
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in held])["2019"]
    assert assign(1, blocked).names == {1: "2019-07-04_400.jpg"}
    # ... and the gap is found from wherever the unit's own number starts, including
    # from above it, where the unit must keep climbing rather than fall back into it.
    assert assign(401, blocked).names == {1: "2019-07-04_901.jpg"}


def test_a_name_that_is_not_this_familys_text_for_a_number_does_not_block_it():
    """F16: the three operands that decide whether a taken name belongs to this family.

    `_blocked_numbers` asks three separate questions of every taken name - right
    LENGTH, right PREFIX, right SUFFIX-and-extension - and each was individually
    deletable with all 2271 tests green. That is not a style point. Deleting one marks
    a number blocked that is not, the seed jumps past it, and a SKIPPED FREE NUMBER is
    a changed filename on an irreplaceable file.

    M15 Task 6 (C7) replaced that parse with `_number_is_blocked`, which SPELLS the family's
    name at each number and looks it up, so the three operands are now the three parts of
    the spelled name. The decoys stand unchanged: each is still a name a wrong spelling
    (wrong prefix, wrong width, wrong extension) would match.

    Each decoy below is a name an archive of real files holds, sized so that it reaches the
    operand it is aimed at rather than being rejected by an earlier one - which is the
    trap this test fell into on its first draft, where all three decoys died on the
    length check and the mutation survived anyway:

      * `2019-07-05_004.jpg` - the SAME directory, a DIFFERENT DATE. Right length,
        right extension, digits in the right place. Only the prefix says no.
      * `2019-07-04_0041.jpg` - a FOUR-digit sibling whose first three digits are 004.
        Right prefix, right extension; only the length says no. This is the width
        confusion `f"{n:03d}"` exists for, arriving from the other side.
      * `2019-07-04_004.png` - the right family, the WRONG EXTENSION.

    `_003` is taken so the unit really has to move; without a genuine collision the
    first candidate is free, `_next_free_number` is never called, and none of this is
    exercised at all.
    """
    blocked = taken_names(
        [
            "2019/2019-07-04_003.jpg",  # the real collision, so the seed is consulted
            "2019/2019-07-05_004.jpg",  # prefix decoy
            "2019/2019-07-04_0041.jpg",  # length decoy
            "2019/2019-07-04_004.png",  # extension decoy
        ]
    )["2019"]

    assert assign(3, blocked).names == {1: "2019-07-04_004.jpg"}


def test_the_seed_reads_EVERY_member_extension_not_just_the_first():
    """F18: a companion unit's ceiling, which survived A7 in the taken-set reading.

    The seed is given `member_exts.values()`, and narrowing that to the first
    extension alone was green across the whole suite AND moved no name in 8110 diffed
    plans - because with one extension the two are the same thing. What it does is
    restore the ceiling for a COMPANION unit: the numbers held by the second member's
    extension become invisible to the seed, so the search falls back to climbing them
    one at a time and runs out of budget again. On a phone library that is hundreds of
    live_photo groups and 6 RAW+JPEG pairs, so the case is the normal one, not an edge.

    1000 numbers are held by the `.nef` half only. A seed that cannot see them steps
    one at a time and returns None; a seed that can see them arrives at 1001 directly.
    """
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.nef" for n in range(1, 1001)])["2019"]

    result = assign(1, blocked, exts={1: "jpg", 2: "nef"})

    assert result is not None, "the seed did not see the second member's extension"
    assert result.names == {1: "2019-07-04_1001.jpg", 2: "2019-07-04_1001.nef"}


def test_a_stem_whose_number_the_budget_SHORTENED_is_not_a_family():
    """F14: `_seq_family`'s completeness and all-digits operands, pinned on the helper.

    Four guards protect this between them - two here and two in `_next_free_number`'s
    width check - and deleting ALL FOUR left the suite green and moved no name, because
    each covers what the others cover. Mutual redundancy is not depth: it means no
    single one of them is load-bearing, so none can fail a test, so none is tested.

    The case has to be a stem where `seq` IS emitted and is INCOMPLETE. A budget tight
    enough to remove the token altogether is refused on the first operand instead (no
    seq at all), which is how this test's first draft passed while the mutation
    survived. At budget 15 the reserved-stem rewrite and the budget collide: `001`
    emits as `00`, and `prefix + "00" + suffix` reproduces the stem exactly, so the
    round-trip guard does not catch it either. `00` is not this family's text for any
    number - `f"{0:03d}"` is `000` - and a caller that believed it were would describe
    a two-digit family the engine never emits.
    """
    from pam.naming.collision import _seq_family
    from pam.naming.sanitize import MAX_FULL_PATH

    def at_budget(budget, ext="jpg"):
        return "d" * (MAX_FULL_PATH - len(ROOT) - len(ext) - 1 - budget)

    def stem_at(budget):
        return build_stem(
            ("orig", "seq"),
            {"orig": "con.backup", "seq": "001"},
            archive_root_abs=ROOT,
            rel_dir=at_budget(budget),
            exts=("jpg",),
        )

    roomy = stem_at(16)
    assert roomy.stem == "con-x.backup_001"
    assert _seq_family(roomy) == ("con-x.backup_", "", 3)

    shortened = stem_at(15)
    assert shortened.stem == "con-x.backup_00"
    emitted = {token.name: token for token in shortened.emitted}
    assert "seq" in emitted and not emitted["seq"].complete, (
        "the budget no longer emits a shortened seq, so this test proves nothing"
    )
    assert shortened.stem == "con-x.backup_" + emitted["seq"].value, (
        "the round-trip guard would catch this on its own, so the operands are untested"
    )
    assert _seq_family(shortened) is None


def test_a_dense_directory_costs_a_HANDFUL_of_attempts_not_a_walk(monkeypatch):
    """F17: MAX_ATTEMPTS' magnitude, pinned by watching the call rather than the result.

    The constant's lower bound is pinned by every ceiling test above - too small and a
    unit stops finding names. Its UPPER bound was pinned by nothing: set it to 500000
    and the suite stayed green once the one fixture that read it was spelled out
    independently (M1108). A budget of 500000 is not a harmless number. The whole point
    of A7 is that a unit arrives at its free number instead of climbing to it, and a
    budget that large would let a REGRESSION of the seed - any future edit that quietly
    returns `seq + 1` again - climb half a million stems per unit and merely look slow
    rather than fail.

    So the assertion is on the work, not the outcome. `build_stem` is the expensive
    call the loop makes once per attempt; counting it says directly what "seeded"
    means. Asserting the outcome alone cannot tell "arrived in three attempts" from
    "climbed a thousand and got there anyway" - M697's lesson, pointed at cost instead
    of at safety.
    """
    from pam.naming import collision

    calls = []
    real = collision.build_stem
    monkeypatch.setattr(
        collision, "build_stem", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )

    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 5001)])["2019"]
    result = assign(1, blocked)

    assert result.names == {1: "2019-07-04_5001.jpg"}
    # Three: the first candidate, the re-ask at the 999->1000 width boundary, and the
    # winner. The exact number is less important than that it does not scale with the
    # 5000 held names - a walk would be 5001.
    assert len(calls) <= 8, f"the search climbed instead of seeding: {len(calls)} attempts"


def test_a_units_own_name_in_the_middle_of_a_run_is_still_free_to_itself():
    # The seed and the freeness test must read the same notion of "taken", or an
    # already-conforming file in a dense directory would be renamed for no reason.
    # Two derivations of "which names are taken" that can disagree is M4's ghost-row
    # shape; here they are one set difference, and this is the test that says so.
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 901)])["2019"]
    result = assign(1, blocked, own={"2019-07-04_500.JPG"})
    assert result.names == {1: "2019-07-04_500.jpg"}


def test_two_members_sharing_an_extension_are_refused_not_collided():
    # The uniqueness guarantee made LOCAL. `names` is keyed by file id and valued by
    # filename, so two members with the same extension map two distinct files onto
    # one proposed name - and this module exists to guarantee that never happens.
    # spec 5.4 safety property 1 forbids such a group and companions.py enforces it,
    # but that is three modules away; apply must not depend on an invariant this
    # function cannot see. Refusing is the safe answer: the planner records the unit
    # as an error and plans nothing for it, which is what pam does with any unit it
    # cannot name.
    assert assign(1, set(), exts={1: "jpg", 2: "jpg"}) is None
    # ... and case is not an escape hatch: Windows filenames are case-insensitive.
    assert assign(1, set(), exts={1: "JPG", 2: "jpg"}) is None
    # The control: different extensions are exactly what a companion unit is.
    assert assign(1, set(), exts={1: "jpg", 2: "cr2"}) is not None


# A directory path long enough to leave the stem an 11-character budget: `2019-07-04`
# fits, and the '_' before {seq} then leaves no room at all. spec 6.2 rule 5's last
# resort cuts {seq} out of the name ENTIRELY (the `room <= 0` branch in `template._cut`),
# which is the one remaining way this search can fail to make progress. The length is
# inverted from `sanitize.stem_budget`, the single source of that arithmetic, and the
# test asserts what the budget did rather than trusting this number.
SQUEEZED_DIR = "d" * 215


def squeezed(dir_taken, rel_dir=SQUEEZED_DIR):
    return assign_unique_stem(
        ("date", "seq"),
        {"date": "2019-07-04"},
        archive_root_abs=ROOT,
        rel_dir=rel_dir,
        member_exts={1: "jpg"},
        own_names=(),
        seq=1,
        dir_taken=dir_taken,
    )


def test_a_family_whose_name_cannot_carry_the_number_gives_up_loudly():
    # What MAX_ATTEMPTS still bounds since rev 15, and the reason the constant survives
    # the amendment rather than being deleted with the ceiling it used to be. When the
    # path budget cuts {seq} out of the stem, EVERY number builds the same name, so no
    # amount of searching can find a free one - and an unbounded loop would hang on it.
    # A silent hang is the failure mode "loud failure over silent wrong" has no answer
    # for, and the one a user cannot even report usefully.
    #
    # The premise is asserted rather than assumed: without these two lines the test
    # would pass just as happily over a family that DOES number, for the wrong reason.
    at_1, at_999 = (
        build_stem(
            ("date", "seq"),
            {"date": "2019-07-04", "seq": f"{n:03d}"},
            archive_root_abs=ROOT,
            rel_dir=SQUEEZED_DIR,
            exts=("jpg",),
        )
        for n in (1, 999)
    )
    assert at_1.stem == at_999.stem == "2019-07-04"
    assert not any(token.name == "seq" for token in at_1.emitted)

    blocked = taken_names([f"{SQUEEZED_DIR}/2019-07-04.jpg"])[SQUEEZED_DIR]
    snapshot = set(blocked)
    assert squeezed(blocked) is None
    assert blocked == snapshot  # the caller reserves; a failure claims nothing
    # The control, and it is not decoration: with the one name free the same call
    # succeeds, so the None above is the search failing and not the fixture refusing.
    assert squeezed(set()).names == {1: "2019-07-04.jpg"}


def test_the_family_that_cannot_progress_gives_up_PROMPTLY(monkeypatch):
    """F17: MAX_ATTEMPTS' magnitude, pinned through the case the constant governs.

    Its lower bound is pinned by every ceiling test above - too small and a unit stops
    finding names. Its upper bound was pinned by nothing: raising it to 500000 left the
    whole suite green (M1119), including a test that counts attempts on the SUCCESS
    path, because a working seed arrives in three attempts whatever the budget is.

    The magnitude only bites where the constant still has a job. A non-progressing
    family builds the same stem once per attempt and then gives up, so the budget IS
    the cost of that give-up: at 999 it is a thousand stem builds and returns; at
    500000 it is half a million, which is the silent hang the constant exists to
    prevent, wearing a bound. Asserting the number of builds says that directly, where
    asserting `MAX_ATTEMPTS == 999` would only restate the source line.
    """
    from pam.naming import collision

    calls = []
    real = collision.build_stem
    monkeypatch.setattr(
        collision, "build_stem", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )

    blocked = taken_names([f"{SQUEEZED_DIR}/2019-07-04.jpg"])[SQUEEZED_DIR]
    assert squeezed(blocked) is None
    assert len(calls) <= 1001, (
        f"giving up cost {len(calls)} stem builds; the bound exists to make this"
        " loud rather than to make it merely finite"
    )


def test_a_number_the_budget_only_shortened_still_gets_the_plain_step():
    # The other half of `_seq_family`'s guard. At a 12-character budget {seq} survives
    # but is cut to ONE digit, so `2019-07-04_0` stands for every number from 1 to 99 -
    # the name no longer identifies the number, and the prefix/suffix split would be a
    # description of a family that does not exist. The seed declines to jump and the
    # plain step takes over, which finds the free digit the long way.
    short_dir = "d" * 214
    numbered = build_stem(
        ("date", "seq"),
        {"date": "2019-07-04", "seq": "001"},
        archive_root_abs=ROOT,
        rel_dir=short_dir,
        exts=("jpg",),
    )
    assert numbered.stem == "2019-07-04_0"
    assert [(t.name, t.complete) for t in numbered.emitted] == [("date", True), ("seq", False)]
    # Digits 0 and 1 are held, so the unit must reach a number whose first digit is 2 -
    # `f"{200:03d}"[:1]`, found by stepping, since no jump is available here.
    held = taken_names([f"{short_dir}/2019-07-04_{d}.jpg" for d in "01"])[short_dir]
    assert squeezed(held, rel_dir=short_dir).names == {1: "2019-07-04_2.jpg"}


def test_reserve_lowercases():
    dir_taken = {"x.jpg"}
    reserve(dir_taken, ["2019-07-04_001.JPG"])
    assert dir_taken == {"x.jpg", "2019-07-04_001.jpg"}


def test_sequential_assignment_never_proposes_a_name_still_on_disk():
    # The invariant the whole module exists for: no plan may contain a swap or a
    # cycle, because apply verifies "target absent" per item (spec 7.1) and both
    # halves of a swap would fail. Three files, all wanting numbers that are held.
    disk = [
        "2019/IMG_1.jpg",
        "2019/2019-07-04_001.jpg",
        "2019/2019-07-04_002.jpg",
    ]
    on_disk = taken_names(disk)["2019"]
    dir_taken = set(on_disk)
    proposals = {}
    for rel_path, seq in sorted(
        [
            ("2019/2019-07-04_001.jpg", 2),
            ("2019/2019-07-04_002.jpg", 3),
            ("2019/IMG_1.jpg", 1),
        ]
    ):
        _, name = split_rel_path(rel_path)
        result = assign(seq, dir_taken, own={name})
        assert result is not None
        proposals[name] = result.names[1]
        reserve(dir_taken, result.names.values())
    for old, new in proposals.items():
        if new.lower() != old.lower():
            assert new.lower() not in on_disk, f"{old} -> {new} targets a live name"
    assert len({v.lower() for v in proposals.values()}) == 3


def test_randomized_worlds_never_swap_cycle_or_collide():
    # Regression harness for the swap/cycle safety property (reviewer proved it by
    # hand across 20k throwaway worlds; this replays a fixed, fast sample of them).
    rng = random.Random(20260725)
    for _world in range(300):
        n = rng.randint(3, 12)
        used: set[str] = set()
        own_names = []
        for i in range(n):
            # "orig_{i}.jpg" is unique per unit by construction - the guaranteed
            # fallback when the random conforming name collides with an earlier
            # unit's (two units can't really own the same on-disk name).
            name = (
                f"2019-07-04_{rng.randint(1, 4):03d}.jpg"
                if rng.random() < 0.5
                else f"orig_{i}.jpg"
            )
            if name in used:
                name = f"orig_{i}.jpg"
            used.add(name)
            own_names.append(name)
        seqs = [rng.randint(1, 4) for _ in range(n)]
        strays = {f"2019-07-04_{rng.randint(1, 4):03d}.jpg" for _ in range(rng.randint(0, 4))}
        strays -= set(own_names)
        disk = [f"2019/{name}" for name in [*own_names, *strays]]
        on_disk = taken_names(disk)["2019"]
        dir_taken = set(on_disk)
        order = list(range(n))
        rng.shuffle(order)
        proposed: set[str] = set()
        for i in order:
            result = assign(seqs[i], dir_taken, own={own_names[i]})
            assert result is not None
            new = result.names[1].lower()
            if new != own_names[i].lower():
                assert new not in on_disk  # never targets a name still on disk
            assert new not in proposed  # no two units propose the same name
            proposed.add(new)
            reserve(dir_taken, result.names.values())


class _FolderThatMustNotBeWalked:
    """A taken set that counts lookups and REFUSES every way of reading it whole (C7).

    `pam plan` on one flat folder was quadratic (M15 Task 6: flat20k 52 s, 158.8 s of 181.3 s
    under cProfile in `_blocked_numbers`, then 10.4 s of 22.5 s in `dir_taken - own`): each
    collision PARSED every name in the folder, and each unit COPIED the folder's taken set. Both
    are a read of the whole folder per unit. The assertion is on that work, never on a clock.

    DELIBERATELY NOT A `set` SUBCLASS (Task 6 review, M3712): CPython's `set(x)`, `frozenset(x)`
    and `set.update(x)` copy a set subclass through their C merge path without calling any
    Python method, so a per-unit `set(dir_taken)` - fix 2's quadratic term spelled through the
    constructor - passed every refusal a subclass could declare. Holding the names instead
    leaves `in` and `update` (all `assign_unique_stem` and `reserve` may use) and routes every
    whole read, operators and copies included, through `__iter__` or a missing method.
    """

    def __init__(self, names):
        self._names = set(names)
        self.lookups = 0

    def __contains__(self, name):
        self.lookups += 1
        return name in self._names

    def update(self, names):
        self._names.update(names)

    def __iter__(self):
        raise AssertionError("the collision search read the whole folder")


def _flat_folder_lookups(folder_size):
    """Five same-date units colliding in one folder that also holds `folder_size` other names."""
    others = [f"img_{k:06d}.jpg" for k in range(folder_size)]
    dir_taken = _FolderThatMustNotBeWalked(
        others + ["2019-07-04_001.jpg", "2019-07-04_002.jpg"]
    )
    names = []
    for unit in range(5):
        result = assign(1, dir_taken, own=[f"IMG_{unit}.jpg"])
        names.append(result.names[1])
        reserve(dir_taken, result.names.values())
    return names, dir_taken.lookups


def test_a_flat_folder_costs_each_unit_the_SAME_work_however_large_the_folder_is():
    """C7 (M15 Task 6): per-unit collision work does not grow with the folder.

    Every unit collides (`_001` and `_002` are held, then each unit's winner is), so the seed
    is consulted every time. The folder is never iterated or copied (the set above raises), and
    the number of lookups is IDENTICAL at 1,000 and 4,000 other names - the old body parsed every
    name per collision, and copied the set per unit, so both counts moved with the folder.
    """
    small_names, small = _flat_folder_lookups(1_000)
    large_names, large = _flat_folder_lookups(4_000)

    expected = [f"2019-07-04_{n:03d}.jpg" for n in range(3, 8)]  # spelled from spec 6.3 rule 2
    assert small_names == large_names == expected
    assert small > 0, "the fixture never consulted the taken set, so it measures nothing"
    assert small == large, f"lookups grew with the folder: {small} at 1,000, {large} at 4,000"


def test_the_seed_lowercases_the_EXTENSION_as_written_for_every_member():
    """M15 Task 6 review: the seed spells each member's tail LOWERCASED (design 6.3.2).

    The planner passes `ext_as_written` - upper-case `JPG`, `HEIC`, `MOV`, the common case from
    real cameras and phones - and
    the taken set holds lowercased names. A tail spelled in the file's own case never matches, the
    seed under-reports, the search steps one number at a time and a unit behind 1000 held numbers
    gets no name at all. Every other seed test used a lowercase extension, so dropping the
    `.lower()` was green. The decoy halves: `.NEF` held on the second member, `.JPG` on the first.
    """
    held = [f"2019/2019-07-04_{n:03d}.JPG" for n in range(1, 501)]
    held += [f"2019/2019-07-04_{n:03d}.NEF" for n in range(501, 1001)]
    blocked = taken_names(held)["2019"]

    result = assign(1, blocked, exts={1: "JPG", 2: "NEF"})

    assert result is not None, "the seed did not see the held numbers in their own case"
    assert result.names == {1: "2019-07-04_1001.JPG", 2: "2019-07-04_1001.NEF"}


def test_the_seed_lowercases_the_PREFIX_a_scene_label_puts_in_front_of_the_number():
    """M15 Task 6 review: a capitalised token BEFORE `{seq}` is part of the seed's `head`.

    A date has no letters, so every other seed test was blind to whether `head` is lowercased. A
    scene label keeps its case (`Beach`), the taken set does not, and an unlowered head turns the
    seed off for that family: 1000 held numbers become 1000 single steps and no name.
    """
    blocked = taken_names([f"2019/beach_2019-07-04_{n:03d}.jpg" for n in range(1, 1001)])[
        "2019"
    ]

    result = assign_unique_stem(
        ("scene", "date", "seq"),
        {"scene": "Beach", "date": "2019-07-04"},
        archive_root_abs=ROOT,
        rel_dir="2019",
        member_exts={1: "jpg"},
        own_names=(),
        seq=1,
        dir_taken=blocked,
    )

    assert result is not None, "the seed did not see a family whose head carries capitals"
    assert result.names == {1: "Beach_2019-07-04_1001.jpg"}


def test_a_name_whose_digit_is_not_an_ASCII_digit_neither_blocks_nor_crashes():
    """M15 Task 6 review: `2019-07-04_0²1.jpg` is not this family's text for any number.

    `str.isdigit` accepts `²`, and `int("0²1")` raises: the parse `_number_is_blocked` replaced
    crashed `pam plan` with a ValueError whenever such a name sat in a colliding family's folder.
    The spelled lookup cannot, and must not start marking a number blocked by it.
    """
    blocked = taken_names(["2019/2019-07-04_001.jpg", "2019/2019-07-04_0²1.jpg"])["2019"]

    assert assign(1, blocked).names == {1: "2019-07-04_002.jpg"}


def _parse_oracle(number, head, tails, taken, own):
    """931fff9's `_blocked_numbers`, kept as an INDEPENDENT reading: parse every name, never spell.

    `_number_is_blocked` spells the family's name and looks it up; this walks the names and
    decides membership from their text. Two derivations of "this number is held", so a
    co-mutation of one cannot carry the other with it.
    """
    width = len(f"{number:03d}")
    for name in taken - own:
        for tail in tails:
            if len(name) != len(head) + width + len(tail):
                continue
            if not name.startswith(head) or not name.endswith(tail):
                continue
            digits = name[len(head) : len(head) + width]
            if digits.isascii() and digits.isdigit() and int(digits) == number:
                return True
    return False


def test_the_spelled_lookup_agrees_with_a_parse_of_every_name():
    """M15 Task 6 review: `_number_is_blocked` against the parse it replaced, randomised.

    Decoys of every shape the parse rejected: another prefix, another width, another extension,
    the unit's own names, upper-case originals (the taken set is lowercased by `taken_names`).
    """
    from pam.naming.collision import _Blocking, _number_is_blocked

    numbers = [*range(0, 32), *range(994, 1007)]
    rng = random.Random(20261116)
    heads = ["2019-07-04_", "beach_2019-07-04_", "", "2019-07-05_"]
    exts = ["jpg", "JPG", "nef", "mov", "aae"]
    agreed = blocked_seen = 0
    for _world in range(400):
        head = rng.choice(heads)
        suffix = rng.choice(["", "_x", "_Beach"])
        tails = [f"{suffix}.{e}".lower() for e in rng.sample(exts, rng.randint(1, 3))]
        names = set()
        for _ in range(rng.randint(0, 40)):
            fmt = rng.choice(["{:03d}", "{:04d}", "{:02d}", "{:d}"])
            number = rng.choice([rng.randint(0, 30), rng.randint(995, 1005)])
            names.add(f"{rng.choice(heads)}{fmt.format(number)}{suffix}.{rng.choice(exts)}")
        taken = taken_names([f"d/{n}" for n in names]).get("d", set())
        own = {n for n in taken if rng.random() < 0.2}
        view = _Blocking(taken, own)
        for number in numbers:
            want = _parse_oracle(number, head.lower(), tails, taken, own)
            assert _number_is_blocked(number, head.lower(), tails, view) == want, (
                number,
                head,
                tails,
                sorted(taken),
                sorted(own),
            )
            blocked_seen += want
            agreed += 1
    assert agreed == 400 * len(numbers)
    assert blocked_seen > 200, "the generator never held a number, so nothing was compared"


def test_the_seed_reads_the_FIRST_member_extension_not_just_the_last():
    """M15 Task 6 review (M3706): F18's mirror. F18 holds the numbers on the SECOND member, so
    a seed that asked only the LAST member's tail passed it and the whole suite. Here the
    `.jpg` half - the first member - holds 1000 numbers: a seed blind to it steps one number at
    a time and runs out of budget; a seed that asks every member arrives at 1001 directly.
    """
    blocked = taken_names([f"2019/2019-07-04_{n:03d}.jpg" for n in range(1, 1001)])["2019"]

    result = assign(1, blocked, exts={1: "jpg", 2: "nef"})

    assert result is not None, "the seed did not see the first member's extension"
    assert result.names == {1: "2019-07-04_1001.jpg", 2: "2019-07-04_1001.nef"}

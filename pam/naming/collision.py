"""Collision-free name assignment for one rename unit (design spec section 6.3.2)."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pam.naming.template import StemResult, build_stem
from pam.scan.companions import stem_key

# spec 6.3 rule 2 rev 15: this is NO LONGER A CEILING ON THE ARCHIVE, and the
# distinction is the whole point of amendment A7. Until M9 the search stepped one
# number at a time, so this constant bounded how many units could share a directory,
# a date and a `{seq}` scope - and rev 14's `(event_id, date)` scope made that bound
# reachable in an ordinary archive (measured: 1000 units in event A plus 3 in event B,
# one date, one directory, dropped `b00001.jpg` with "no free name after 999 tries"
# and planned 1002 of 1003; the boundary was sharp at 999/1000). `_next_free_number`
# below now jumps over every number the taken set already holds, so a unit reaches its
# free number in a handful of attempts however large the archive is.
#
# The constant SURVIVES, deliberately, because a second exhaustion case is real and
# is not about size: a family that DOES NOT PROGRESS. When the path budget cuts
# `{seq}` out of the stem entirely (spec 6.2 rule 5's last resort - `("date", "seq")`
# at budget 11 emits `2019-07-04` and no seq token at all), every attempt builds the
# same name, and without a bound this loop would spin forever on a name that is
# taken. A silent hang is the one failure mode "loud failure over silent wrong" has
# no answer for, and it is the one a user cannot even report usefully - the same
# argument `template._finalize`'s tripwire is written under. The value is left at 999
# so that this can never fire EARLIER than the pre-A7 code did.
MAX_ATTEMPTS = 999


@dataclass(frozen=True)
class NameAssignment:
    stem: StemResult
    names: dict[int, str]  # file id -> proposed filename, extension included


def split_rel_path(rel_path: str) -> tuple[str, str]:
    """'2019/a.jpg' -> ('2019', 'a.jpg'); 'a.jpg' -> ('', 'a.jpg')."""
    head, _, name = rel_path.rpartition("/")
    return head, name


def taken_names(rel_paths: Iterable[str]) -> dict[str, set[str]]:
    """Per directory, the case-insensitive names the CATALOG holds (spec 6.3.2).

    NOT "names currently on disk", which is what this line said until the M10 close and
    which is FALSE - the caller (`planner.py`, at its `all_rows` comment) passes EVERY
    catalog row, `missing` ones included, and says at length why it must: `files.rel_path`
    is UNIQUE and rows are never deleted, so proposing a ghost row's name plans a rename
    that succeeds on disk and then fails in `set_file_path`, stranding a journal row in
    `intent` that recovery can never resolve - breaking J7. The behaviour is deliberate and
    load-bearing; only this sentence was wrong.

    IT COST A REAL READER REAL TIME, WHICH IS WHY IT IS WORTH THIS PARAGRAPH. At HT-M10 a
    `{seq}` number was skipped (`_001` -> `_002`) for a unit that appeared to be alone on its
    date, the tester came here to explain it, read "currently on disk", correctly concluded
    the code could not account for it, and logged it unexplained rather than overclaiming a
    cause. The cause was a ghost row: a member moved outside pam leaves its old name in the
    catalog for ever, that name is taken, and the unit's own current filenames no longer
    include it. Nothing pam prints explains a skipped number to a user - a separate gap,
    recorded at the M10 close, not closed here.

    Conservative on purpose in the other direction too: every taken name blocks, including
    names held by files that are themselves about to be renamed. The literal spec wording
    would let a unit claim a name another unit is about to free, but that emits plans
    containing swaps and cycles - and apply verifies "target absent" per item
    (spec 7.1), so both halves of a swap would simply fail. Cycle-breaking through
    temporary names is not something the journal protocol promises.
    """
    taken: dict[str, set[str]] = {}
    for rel_path in rel_paths:
        rel_dir, name = split_rel_path(rel_path)
        taken.setdefault(rel_dir, set()).add(name.lower())
    return taken


class StemKeys:
    """Per directory, which names hold each spec 5.4 grouping key (Z3, design 6.3 rule 2).

    Z3 (adopted by the owner 2026-11-13; record `docs/superpowers/specs/2026-11-13-m14-r1-f1-
    amendment.md`): a proposed stem must not share a 5.4 grouping key with any file outside the
    unit in its directory - a catalog file, or a name another unit of the plan already holds.
    Rule 2 made each FILENAME unique and never each stem, so two same-day events under the
    default template both took `2019-07-04_001` (a `.JPG` and a `.MOV`), and the next scan bound
    two unrelated files into one `live_photo` unit (finding 5f-F1, vision S3).

    THE KEY IS `companions.stem_key`, "the single definition of 'shares a stem'", called and
    never re-spelled - so `scan.ignore_stem_suffixes` applies exactly as grouping applies it.
    Built from the SAME rel_paths as `taken_names` (every catalog row, `missing` included: the
    conservative reading, as for names) and extended only by `reserve`, which updates the names
    and the keys together. So the two records cannot drift apart: one input, one writer.

    Names are kept AS WRITTEN, so a refusal can name the clash in its own case; "own" is
    compared lowercased, as `assign_unique_stem`'s `own_names` always has been.

    Per-unit cost is a dictionary lookup, never a pass over the directory: M14's X4 flat-folder
    bound and P11 stay as they were.
    """

    def __init__(self, rel_paths: Iterable[str], ignore_stem_suffixes: tuple[str, ...]) -> None:
        self._ignore = tuple(ignore_stem_suffixes)
        self._held: dict[tuple[str, str], set[str]] = {}
        self._on_disk: dict[tuple[str, str], set[str]] = {}  # lowercased; Z3.1's operand
        for rel_path in rel_paths:
            rel_dir, name = split_rel_path(rel_path)
            self.reserve(rel_dir, name)
            self._on_disk.setdefault(self._key(rel_dir, name), set()).add(name.lower())

    def _key(self, rel_dir: str, name: str) -> tuple[str, str]:
        return stem_key(f"{rel_dir}/{name}" if rel_dir else name, self._ignore)

    def reserve(self, rel_dir: str, name: str) -> None:
        self._held.setdefault(self._key(rel_dir, name), set()).add(name)

    def holders(self, rel_dir: str, name: str, own: set[str]) -> list[str]:
        """The names in `rel_dir`, outside `own` (lowercased), that share `name`'s key.

        Z3.1 (the controller, 2026-11-13, pending the design model; Task 5g review 5g-F1): THE
        UNIT'S OWN CURRENT KEY IS FREE TO IT, as its own current names are. A unit keeping the
        stem it already has creates no shared key - one a missing row or a file 5.4 does not
        group (a `.LRV`) already shares on disk - and moving it off that stem can undo nothing
        while it separates the unit from its stem-mates. Z3 exists to stop pam CREATING a
        shared key. So within the unit's own key only the files ALREADY ON DISK are free: a name
        another unit of this plan has reserved into that key still blocks, or pam would create
        the very sharing Z3 forbids.
        """
        key = self._key(rel_dir, name)
        free = set(own)
        if any(self._key(rel_dir, mine) == key for mine in own):
            free |= self._on_disk.get(key, set())
        return sorted(h for h in self._held.get(key, ()) if h.lower() not in free)


def _seq_family(stem: StemResult) -> tuple[str, str, int] | None:
    """A built stem split around its `{seq}` digits: (prefix, suffix, width).

    None when the stem carries no clean, whole `{seq}` value - which is exactly the
    case in which numbering does not move the name, and therefore the case in which
    the caller must fall back to stepping by one.

    The split is read off `stem.emitted`, the provenance the template engine already
    records, rather than by searching the stem text for digits: a date token is full
    of digits and `2019-07-04_001` has four plausible "numbers" in it. `emitted`
    carries the token that supplied each part IN STEM ORDER, and spec 6.1 says the
    parts are joined with a single '_', so the prefix and suffix are exact.

    Three conditions have to hold before the split may be trusted, and each of them
    is a way the assumption "only the digits move" fails:

      * exactly one emitted `seq` (a template may not repeat a token, so more than
        one would mean the engine changed under us, and zero means the budget cut
        the number away - see MAX_ATTEMPTS above);
      * the value is complete and all digits (a budget-shortened `00` is not this
        family's text for any number);
      * re-joining prefix + digits + suffix reproduces the stem EXACTLY.

    THE THIRD IS A TRIPWIRE, NOT A REACHABLE BRANCH, and saying so is the point.
    An earlier version of this docstring claimed it caught spec 6.2 rule 4's
    reserved-stem rewrite and `_finalize`'s edge-stripping. It does not: both of
    those edit a part BEFORE it is emitted, so `emitted` describes the stem that was
    actually built and the join always reproduces it - measured over 40,000
    randomised `build_stem` calls (unicode, reserved heads, edge characters, every
    budget from 1 to 250), with zero counterexamples in all 38,402 non-empty results.
    The invariant is `"_".join(t.value for t in emitted) == stem`, it belongs to the
    template engine, and `tests/test_template.py::test_the_stem_is_ALWAYS_the_emitted_
    values_joined_by_one_underscore` is where it is pinned - together with its one
    exception, an all-erased stem, which `emitted == ()` takes out of play here.

    It is kept because it is the assumption this whole module rests on, and an
    assumption that is currently true is exactly the thing a future template change
    can quietly falsify - at which point this returns None and the caller falls back
    to stepping by one, which is slow and correct rather than fast and wrong.
    """
    at = [i for i, token in enumerate(stem.emitted) if token.name == "seq"]
    if len(at) != 1:
        return None
    index = at[0]
    values = [token.value for token in stem.emitted]
    digits = values[index]
    if not stem.emitted[index].complete or not digits.isdigit():
        return None
    prefix = "_".join(values[:index]) + "_" if index else ""
    suffix = "_" + "_".join(values[index + 1 :]) if index + 1 < len(values) else ""
    if prefix + digits + suffix != stem.stem:
        return None
    return prefix, suffix, len(digits)


class _Blocking:
    """`dir_taken - own` as a membership test, without copying `dir_taken` (C7)."""

    def __init__(self, taken: set[str], own: set[str]) -> None:
        self._taken, self._own = taken, own

    def __contains__(self, name: object) -> bool:
        return name in self._taken and name not in self._own


def _number_is_blocked(number: int, head: str, tails: list[str], blocking: _Blocking) -> bool:
    """Whether the taken set already holds this family's text at `number` (spec 6.3 rule 2).

    Asked of `blocking` - the SAME set the freeness test below consults, minus the unit's
    own names - and of nothing else. A second, independently maintained record of "which
    names are taken" is the shape of M4's ghost-row defect: two derivations that can
    disagree, with the disagreement invisible until a plan proposes a name that is not free.

    The family's name at `number` is SPELLED and looked up: `head` (the prefix, lowercased)
    + `f"{number:03d}"` + one `tail` per member (suffix and extension, lowercased) - exactly
    the names this family emits, so a number is never marked blocked by a name the family
    would not actually propose (`0007` is a real filename, but this family's text for 7 is
    `007`). Over-reporting here would SKIP A FREE NUMBER, which is a changed filename.

    M15 Task 6 (C7): this replaced `_blocked_numbers`, which answered the same question for
    every number at once by PARSING every name in `blocking` - one pass over the folder per
    collision, so `pam plan` on one flat folder of 20,000 was quadratic (158.8 s of 181.3 s
    under cProfile, 7,727 passes). The answer for each number is the same set membership; it
    costs one lookup per member per number tried, the same numbers the walk below already
    tries one at a time.
    """
    digits = f"{number:03d}"
    return any(head + digits + tail in blocking for tail in tails)


def _next_free_number(
    seq: int,
    stem: StemResult,
    member_exts: dict[int, str],
    blocking: _Blocking,
    shared: Callable[[str], bool] | None = None,
) -> int:
    """spec 6.3 rule 2 rev 15: the next number to try, SEEDED FROM THE TAKEN SET.

    The number returned is the smallest above `seq` that the taken set does not
    already hold, so the search arrives at it directly instead of climbing past
    every number an earlier event reserved on the same date in the same directory.

    **This changes no name, and that is the amendment's entire justification.** The
    pre-A7 walk returned the first number above `seq` whose names are free; this
    returns the first number above `seq` that is not blocked. They are the same
    number, because for two numbers of the same width the stem differs only in the
    digits: the sanitizer is the identity on digits, the path budget depends on the
    value's LENGTH and not on its value, `_finalize`'s edge rule strips only `.-_`,
    and a run of digits is never a Windows device name, so rule 4's rewrite cannot
    fire on one either. Every other character of the stem is therefore fixed across
    the width class, which is what makes `prefix + digits + suffix` an exact
    description of the family rather than a guess.

    Two cases deliberately fall back to `seq + 1`, the old step:

      * the stem has no clean `{seq}` split (`_seq_family` returned None), and
      * the walk reaches a WIDTH BOUNDARY (999 -> 1000). A four-digit number is one
        character longer, which can move the path budget and so can move the rest of
        the stem; the family described here would no longer be the family emitted.
        The caller simply builds at 1000 and asks again - the answer comes back
        decomposed at the new width, so the seed keeps working above the boundary
        without this function ever having to reason about two widths at once.

    Both fallbacks are safe in the only direction that matters: a number too LOW
    costs one more attempt, a number too HIGH would be a filename nobody chose.

    Z3: `shared(stem)` says whether a candidate STEM's 5.4 key is held outside the unit. It is
    asked of the family's exact text at each number (`prefix + digits + suffix` IS the stem at
    that number, by the argument above), so a key-held number is skipped precisely and never a
    free one. One dictionary lookup per number skipped - no build, no pass over the directory -
    which is what keeps 1000 numbers held by ANOTHER extension from exhausting the search.
    """
    family = _seq_family(stem)
    if family is None:
        return seq + 1
    prefix, suffix, width = family
    if len(f"{seq:03d}") != width:
        return seq + 1  # the stem is not numbered the way this seq would number it
    head = prefix.lower()
    tails = [f"{suffix}.{ext}".lower() for ext in member_exts.values()]
    number = seq + 1
    while len(f"{number:03d}") == width and (
        _number_is_blocked(number, head, tails, blocking)
        or (shared is not None and shared(f"{prefix}{number:03d}{suffix}"))
    ):
        number += 1
    return number


def assign_unique_stem(
    order: tuple[str, ...],
    values: dict[str, str],
    *,
    archive_root_abs: str,
    rel_dir: str,
    member_exts: dict[int, str],
    own_names: Iterable[str],
    seq: int,
    dir_taken: set[str],
    keys: StemKeys | None = None,
) -> NameAssignment | None:
    """First sequence number whose names are all free (spec 6.3.2).

    A unit's own current names count as free to itself - that is what lets an
    already-conforming file keep its name. Returns None when MAX_ATTEMPTS attempts
    all failed, which since rev 15 means a family whose name does not change with the
    number (see MAX_ATTEMPTS) rather than an archive that outgrew a constant.
    dir_taken is never mutated here: the caller reserves the winning names, so a
    failed assignment leaves no trace.

    dir_taken MUST hold lowercased names: build it with taken_names() and extend
    it only with reserve(). Comparing case-insensitively is the whole point of
    this check on Windows, and it is done by lowering the candidate, not the set.

    `keys` (Z3) adds the stem rule: a candidate whose 5.4 key a file outside the unit holds is
    a collision exactly as a taken name is, and the search continues the same way. `None`
    checks names only - the planner and the review UI always pass it.
    """
    own = {name.lower() for name in own_names}
    # The names that can actually block this unit, defined ONCE and used both by the
    # freeness test below and by the seed, so that the seed cannot consult a different
    # notion of "taken" from the test that accepts the winner. A name is blocking when it
    # is in dir_taken AND not the unit's own. M15 Task 6 (C7): a VIEW, not the set
    # difference it was - copying the folder's whole taken set once per unit was the
    # second quadratic term on one flat folder (10.4 s of 22.5 s under cProfile at 20,000).
    blocking = _Blocking(dir_taken, own)
    # spec 6.3.2 rev 6: on collision append {seq} AT THE UNIT'S CURRENT NUMBER;
    # only a FURTHER collision increments. Appending and incrementing in one step
    # burned numbers that were never used - three same-day photos under
    # --template "{date}" produced 2019-07-04, 2019-07-04_003, 2019-07-04_004, with
    # _001 and _002 absent. Safe, but the names pam produces must be explicable.
    with_seq = order if "seq" in order else (*order, "seq")
    effective = order
    exts = tuple(member_exts.values())

    def shared(stem_text: str) -> bool:  # Z3: is this stem's 5.4 key held outside the unit?
        if keys is None or not exts:
            return False
        return bool(keys.holders(rel_dir, f"{stem_text}.{exts[0]}", own))

    for _attempt in range(MAX_ATTEMPTS + 1):
        candidate = dict(values)
        candidate["seq"] = f"{seq:03d}"
        stem = build_stem(
            effective,
            candidate,
            archive_root_abs=archive_root_abs,
            rel_dir=rel_dir,
            exts=exts,
        )
        names = {file_id: f"{stem.stem}.{ext}" for file_id, ext in member_exts.items()}
        # The uniqueness guarantee, made LOCAL. names is keyed by file id but valued
        # by filename, so two members sharing an extension would collapse two files
        # onto one name - and this function's whole job is that no two files are
        # ever proposed the same name. Spec 5.4 safety property 1 forbids such a
        # group and companions.py enforces it, but that is three modules away: an
        # invariant apply will rely on must not be provable only somewhere else.
        if len({name.lower() for name in names.values()}) != len(names):
            return None  # property 1 was violated upstream; refuse, never clash
        if not any(name.lower() in blocking for name in names.values()) and not shared(
            stem.stem
        ):
            return NameAssignment(stem=stem, names=names)
        # `"seq" in effective` IS the question "was the attempt that just collided
        # already numbered?" - asked of the state itself instead of a bool kept in
        # sync beside it. The separate `numbered` flag was derived, never
        # independent, and mis-initialising it (mutation M36) cost a unit one
        # attempt of its budget while every naming test stayed green.
        if "seq" in effective:
            # spec 6.3 rule 2 rev 15 (A7): seed from the taken set instead of adding
            # one. `stem` is the attempt that just collided, so the family is read
            # from a stem this unit really built rather than from a reconstruction.
            seq = _next_free_number(seq, stem, member_exts, blocking, shared)
        effective = with_seq
    return None


def reserve(
    dir_taken: set[str],
    names: Iterable[str],
    *,
    keys: StemKeys | None = None,
    rel_dir: str | None = None,
) -> None:
    """Claim assigned names so no later unit can propose them - nor their stems (Z3).

    ONE reservation updates both records (Z3: "a reservation updates both"), so the names and
    the keys a later unit is checked against cannot disagree about what an earlier unit took.
    `rel_dir` is REQUIRED with `keys` (Task 5g review Q1): a default of "" silently filed every
    key under the archive root, which two of that review's five survivors measured. "" is a
    real folder (the root), so the absent value is None.
    """
    names = list(names)
    dir_taken.update(name.lower() for name in names)
    if keys is not None:
        if rel_dir is None:
            raise TypeError("reserve(keys=...) needs the folder: pass rel_dir")
        for name in names:
            keys.reserve(rel_dir, name)

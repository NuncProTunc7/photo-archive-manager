"""Plan preflight (spec 7.1 rev 7's decomposition): what apply refuses, and why.

Every refusal here is asserted to write NO journal row. spec 7.0 J1 makes a journal
row a promise that pam is about to move a file; a refused plan moves none, and a row
left behind by a refusal would be adopted by spec 7.2's recovery as a rename to
finish.
"""

import inspect
import json
import sqlite3

import pytest
from archives import approve_all, approve_archive

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.errors import PamError
from pam.execute.validator import (
    ACTIONABLE_STATUSES,
    COMPANION_REASON,
    validate_plan,
)

NOW = "2026-08-01T09:00:00"

# Spec 4's plan_items.status enum and spec 7.1 rev 15's split of it, spelled out here
# rather than imported from `validator` or `repo`. An expectation built from the symbol
# it checks agrees with that symbol for EVERY value of it: `ACTIONABLE_STATUSES` could
# grow a status spec 4 does not define, or the parametrize list below could be emptied,
# and a comparison against production would stay green. Spec 4 is the oracle here, and
# `test_the_preflight_status_partition_covers_the_whole_enum` is where the two meet.
SPEC_ITEM_STATUSES = frozenset(
    {"proposed", "approved", "skipped", "applied", "failed", "undone"}
)
# Spec 7.1 rev 15 renames a unit whose members are ALL `approved` - and since F1 R1
# (HT-M12, ruled 2026-10-08) `approved` ONLY: the typed confirmation no longer approves.
SPEC_ACTIONABLE = frozenset({"approved"})
# The user's own decision to leave a file alone. Spec 8.2 writes it; it gets its own
# refusal sentence because nothing about it is a failure.
SPEC_DECIDED_OUT = frozenset({"skipped"})
# F1 R1: a name nobody decided. Not renamed, and - like a skip - not a failure, so it has
# its own sentence (UNDECIDED_COMPANION_REASON), ranked below the skip's.
SPEC_UNDECIDED = frozenset({"proposed"})
# Everything left: non-actionable, not the user's skip, not undecided. These read as
# COMPANION_REASON.
SPEC_UNEXPECTED = sorted(
    SPEC_ITEM_STATUSES - SPEC_ACTIONABLE - SPEC_DECIDED_OUT - SPEC_UNDECIDED
)

# Asserted at COLLECTION, not inside a test: pytest SKIPS an empty parametrize rather than
# failing it, so a control parametrized over an empty list disappears silently and takes
# its coverage with it (this project's M743, and M1024 on this very list). Every one of the
# four halves the control below runs over is asserted, each on its own.
assert SPEC_ACTIONABLE, "the actionable half of the partition control is empty"
assert SPEC_DECIDED_OUT, "the decided-out half of the partition control is empty"
assert SPEC_UNDECIDED, "the undecided half of the partition control is empty"
assert SPEC_UNEXPECTED, "the unexpected-status control would be parametrized over nothing"

# Everything apply will not act on, `skipped` included. Spelled from spec 4's enum above
# and NOT from `ACTIONABLE_STATUSES`, for the reason `SPEC_ITEM_STATUSES` already gives.
SPEC_NON_ACTIONABLE = sorted(SPEC_ITEM_STATUSES - SPEC_ACTIONABLE)

# Same reason as the assertion above: pytest SKIPS an empty parametrize (M743).
assert SPEC_NON_ACTIONABLE, "the moved-no-op status control would be parametrized over nothing"


@pytest.fixture()
def conn(tmp_path):
    (tmp_path / "workspace").mkdir()
    c = connect(tmp_path / "workspace" / "catalog.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def root(tmp_path):
    """The archive root, threaded into `validate_plan` as spec 7.1 step 1's `root`.

    It exists as a real directory for every test, empty unless the test puts a file in
    it: `_moved_no_op_refusals` is the only thing that touches the disk, and it looks at
    NO-OP ITEMS ONLY, of which every test but C1's below has none.
    """
    archive = tmp_path / "photos"
    archive.mkdir()
    return archive


def add_file(conn, rel_path, *, missing=False):
    file_id = repo.upsert_file(
        conn,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type="image",
        size=10,
        mtime_ns=123,
        placeholder=False,
        now=NOW,
    )
    if missing:
        repo.mark_missing(conn, [file_id])
    return file_id


def make_plan(conn, entries, *, status="open", approve=True):
    """entries: [(old_rel_path, new_filename, unit_key)] -> plan_id.

    Every item is APPROVED by default, with the suite's own SQL (`archives.approve_all`):
    since F1 R1 a plan nobody approved is refused whole, and these tests are about what the
    preflight does to a plan that reached it. `approve=False` leaves every item `proposed`.
    """
    plan_id = repo.create_plan(
        conn,
        now=NOW,
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=4,
    )
    repo.insert_plan_items(
        conn,
        plan_id,
        [
            repo.PlanItemDraft(
                file_id=add_file(conn, old_rel_path),
                old_rel_path=old_rel_path,
                unit_key=unit_key,
                generated_filename=new_filename,
                tokens_json="[]",
                tier="high",
                trust_score=100,
                trust_reasons_json="[]",
            )
            for old_rel_path, new_filename, unit_key in entries
        ],
    )
    if approve:
        approve_all(conn, plan_id)
    if status != "open":
        repo.set_plan_status(conn, plan_id, status)
    conn.commit()
    return plan_id


def journal_rows(conn):
    return [
        row
        for state in ("intent", "done", "failed", "reverted")
        for row in repo.rows_in_state(conn, state)
    ]


# --- whole-plan refusals: PamError, and nothing written ---------------------------


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        ("superseded", "superseded by a newer plan"),
        ("applying", "interrupted while pam was applying it"),
        ("applied", "has already been applied."),
        ("partially_applied", "already been applied in part"),
        ("undone", "was undone"),
    ],
)
def test_a_plan_that_is_not_open_is_refused_by_name(conn, root, status, fragment):
    """Each status says what happened AND what to run next (UX spec 2)."""
    plan_id = make_plan(conn, [("IMG_1.JPG", "a.JPG", "u1")], status=status)
    with pytest.raises(PamError) as excinfo:
        validate_plan(conn, plan_id, root=root)
    assert fragment in str(excinfo.value)
    assert f"Plan {plan_id}" in str(excinfo.value)
    assert "pam plan" in str(excinfo.value)
    assert journal_rows(conn) == []


def test_an_unknown_plan_id_is_refused(conn, root):
    with pytest.raises(PamError, match="no plan 99"):
        validate_plan(conn, 99, root=root)
    assert journal_rows(conn) == []


def test_a_plan_built_before_unit_key_existed_is_refused_whole(conn, root):
    """A pre-0004 plan carries unit_key '' (SQLite requires a default on ADD COLUMN
    NOT NULL). spec 7.3 rev 6 groups renames by unit_key and by nothing else, so such
    a plan gives apply nothing to group by and a companion group could be applied as
    two unrelated files - split across two stems (vision S3).

    Written with raw SQL on purpose: `repo.insert_plan_items` refuses an empty
    unit_key, deliberately, so the only way to reproduce a pre-0004 plan is the way
    migration 0004 itself produced them.
    """
    plan_id = make_plan(conn, [("IMG_1.JPG", "a.JPG", "u1"), ("IMG_2.JPG", "b.JPG", "u2")])
    conn.execute("UPDATE plan_items SET unit_key = '' WHERE old_rel_path = 'IMG_2.JPG'")
    conn.commit()
    with pytest.raises(PamError, match="older version of pam"):
        validate_plan(conn, plan_id, root=root)
    assert journal_rows(conn) == []


def test_an_open_plan_with_unit_keys_passes(conn, root):
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    preflight = validate_plan(conn, plan_id, root=root)
    assert preflight.refusals == {}
    assert [item.old_rel_path for item in preflight.actionable()] == ["IMG_1.JPG"]


# --- the ghost-row refusal (layer 2 of the workspace-bricking defect) --------------


def test_a_name_still_owned_by_a_deleted_files_row_refuses_that_item(conn, root):
    """`files.rel_path` is UNIQUE and rows are never deleted, so a photo deleted
    outside pam owns its name forever. Handing that name to a DIFFERENT file renames
    it successfully on disk and then raises IntegrityError in `set_file_path`, leaving
    a journal row in `intent` beside a completed rename that recovery can never
    resolve - spec 7.0 J7 broken permanently.
    """
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    add_file(conn, "2019-07-04_001.JPG", missing=True)  # the ghost: a different file
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)
    item = preflight.items[0]
    assert item.id in preflight.refusals
    assert "2019-07-04_001.JPG" in preflight.refusals[item.id]
    assert preflight.actionable() == []
    assert journal_rows(conn) == []


def test_the_ghost_comparison_ignores_case(conn, root):
    """The UNIQUE index on files.rel_path is BINARY, so a ghost differing only in case
    raises nothing at all - it writes a second row differing from the first only by
    case, which spec 7.5's check 6 classifies as catalog corruption on Windows, where
    the two names cannot both exist on disk. A case-sensitive check here would trade a
    loud IntegrityError for a silent corruption.
    """
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    add_file(conn, "2019-07-04_001.jpg", missing=True)  # same name, other case
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)
    assert preflight.refusals != {}


def test_an_items_own_files_row_is_not_a_ghost(conn, root):
    """Every item's own file is in `files` by construction. Refusing on that would
    refuse every plan pam has ever built."""
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    preflight = validate_plan(conn, plan_id, root=root)
    assert preflight.refusals == {}


def test_a_ghost_in_another_directory_does_not_refuse(conn, root):
    """A rename never leaves its directory (UX spec 4), so only the item's OWN
    directory can hold the name it proposes."""
    plan_id = make_plan(conn, [("2019/IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    add_file(conn, "2020/2019-07-04_001.JPG", missing=True)
    conn.commit()
    assert validate_plan(conn, plan_id, root=root).refusals == {}


def test_all_files_still_returns_missing_rows(conn, root):
    """The ghost check reads `repo.all_files` and depends on it returning rows of every
    status. A ghost IS a `missing` row, so a future "all_files returns live files"
    change would silently disable a guard against a defect that is unresolvable once
    it fires. Pinned here rather than left as an assumption two modules apart.
    """
    add_file(conn, "gone.JPG", missing=True)
    conn.commit()
    rows = repo.all_files(conn)
    assert "gone.JPG" in rows
    assert rows["gone.JPG"].status == "missing"


def test_a_ghost_in_the_items_own_subdirectory_refuses(conn, root):
    """The POSITIVE half of the pair above. `..._in_another_directory_does_not_refuse`
    asserts an empty result, so a `new_rel_path` that dropped the directory would make
    it pass for the wrong reason - the mutated bare name would match nothing either.
    This one only passes when the directory is genuinely carried into the comparison.
    """
    plan_id = make_plan(conn, [("2019/IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    add_file(conn, "2019/2019-07-04_001.JPG", missing=True)
    conn.commit()
    refusals = validate_plan(conn, plan_id, root=root).refusals
    assert len(refusals) == 1
    assert "2019/2019-07-04_001.JPG" in next(iter(refusals.values()))


def test_a_refused_member_refuses_its_whole_companion_unit(conn, root):
    """spec 7.3: a group is renamed together or not at all. Nothing has moved yet, so
    failing the unit here costs nothing - which is exactly why the check runs before
    the first rename rather than after it (vision S3)."""
    plan_id = make_plan(
        conn,
        [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "live-1"),
            ("IMG_1.MOV", "2019-07-04_001.MOV", "live-1"),
        ],
    )
    add_file(conn, "2019-07-04_001.JPG", missing=True)
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)
    assert len(preflight.refusals) == 2
    companion = preflight.items[1]
    assert preflight.refusals[companion.id] == COMPANION_REASON.format(old_rel_path="IMG_1.MOV")
    assert preflight.actionable() == []


# --- spec 7.3 rev 15: a member the user decided out refuses its unit too ----------
# The rule "a group is renamed together or not at all" used to be computed over the
# proposed/approved subset - a set a `skipped` member has already left - so approving a
# photo and skipping its video renamed the photo alone. See
# `tests/test_apply.py::test_apply_never_renames_a_proper_subset_of_a_companion_unit`
# for the end-to-end construction and for what the split costs.


def live_photo(conn, *, video_status=None):
    """A two-member unit, optionally with the video decided out. -> plan_id."""
    plan_id = make_plan(
        conn,
        [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "live-1"),
            ("IMG_1.MOV", "2019-07-04_001.MOV", "live-1"),
        ],
    )
    if video_status is not None:
        for item in repo.plan_items(conn, plan_id):
            if item.old_rel_path == "IMG_1.MOV":
                repo.set_plan_item_status(conn, item.id, video_status)
    conn.commit()
    return plan_id


def test_a_skipped_member_refuses_its_whole_companion_unit(conn, root):
    """The layer the M8 review could not mutate because it did not exist.

    Nothing failed and no name is stale: the ONLY reason this unit cannot be renamed is
    that the user decided one member out of it, and spec 7.3 rev 15 says a unit is
    decided as a unit.
    """
    plan_id = live_photo(conn, video_status="skipped")

    preflight = validate_plan(conn, plan_id, root=root)

    photo = preflight.items[0]
    assert photo.old_rel_path == "IMG_1.JPG"
    assert list(preflight.refusals) == [photo.id]
    assert preflight.actionable() == []
    assert journal_rows(conn) == []


def test_a_skipped_companions_refusal_says_skipped_and_names_the_route(conn, root):
    """UX 2: a refusal that does not say what to do next is a dead end - and this one
    is printed ALONE, because a skipped member is not itself refused and so carries no
    sibling sentence with a route on it.

    It must also not borrow COMPANION_REASON's words. "could not be renamed" describes
    a failure, and nothing here failed: the user was obeyed. Spelled out literally
    rather than compared against the production constant, which would pass for any
    value of it.
    """
    plan_id = live_photo(conn, video_status="skipped")

    reason = next(iter(validate_plan(conn, plan_id, root=root).refusals.values()))

    assert reason == (
        "IMG_1.JPG was left alone: another file it is grouped with was skipped, and"
        " pam renames a group together or not at all. Run: pam plan, then approve or"
        " skip the whole group together."
    )
    assert "could not be renamed" not in reason


def test_a_unit_blocked_by_both_a_ghost_name_and_a_skip_reads_as_the_ghost(conn, root):
    """Precedence, decided deliberately and pinned here.

    A three-member unit: one member proposes a name a deleted `files` row still owns,
    one member the user skipped, one member left to be refused. Both causes apply, and
    the ghost's wins - because re-deciding the unit cannot fix a stale name, the plan
    has to be rebuilt, and the ghost refusal printed beside this one is the sentence
    that says so. Telling the user to "approve or skip the whole group together" would
    send them round a loop that ends in the same refusal.
    """
    plan_id = make_plan(
        conn,
        [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "live-1"),
            ("IMG_1.MOV", "2019-07-04_001.MOV", "live-1"),
            ("IMG_1.XMP", "2019-07-04_001.XMP", "live-1"),
        ],
    )
    add_file(conn, "2019-07-04_001.JPG", missing=True)  # the ghost
    for item in repo.plan_items(conn, plan_id):
        if item.old_rel_path == "IMG_1.MOV":
            repo.set_plan_item_status(conn, item.id, "skipped")
    conn.commit()

    refusals = validate_plan(conn, plan_id, root=root).refusals

    sidecar = [i for i in repo.plan_items(conn, plan_id) if i.old_rel_path == "IMG_1.XMP"][0]
    assert refusals[sidecar.id] == COMPANION_REASON.format(old_rel_path="IMG_1.XMP")
    assert "was skipped" not in refusals[sidecar.id]


def test_a_unit_whose_members_are_all_skipped_is_not_reported_as_refused(conn, root):
    """Containment, not noise. The user skipped the whole unit; pam was never going to
    rename it, so there is nothing to refuse and nothing to tell them about. Reporting
    it would exit 1 (spec 13 rev 6 counts per-file errors) over a plan that did exactly
    what it was asked."""
    # REWRITTEN UNDER F1 (M13 Task 1 Step 9): a plan whose every item is skipped is now
    # refused WHOLE (F1 R4, nothing approved), so the all-skipped unit sits beside an
    # approved neighbour to keep this test's point - the skipped unit earns no refusal.
    plan_id = make_plan(
        conn,
        [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "live-1"),
            ("IMG_1.MOV", "2019-07-04_001.MOV", "live-1"),
            ("IMG_2.JPG", "2020-08-05_001.JPG", "u2"),
        ],
    )
    for item in repo.plan_items(conn, plan_id):
        if item.unit_key == "live-1":
            repo.set_plan_item_status(conn, item.id, "skipped")
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)

    assert preflight.refusals == {}
    assert [item.old_rel_path for item in preflight.actionable()] == ["IMG_2.JPG"]


def test_a_skipped_unit_of_one_refuses_nothing(conn, root):
    """A unit of one has no proper subset, so it cannot be split and must be
    unaffected. The over-broad fix - refusing every unit that holds a skipped item -
    would refuse the second unit here as well, and skipping ONE photo is the most
    ordinary thing a user does in `pam review`."""
    plan_id = make_plan(
        conn,
        [("IMG_1.JPG", "2019-07-04_001.JPG", "u1"), ("IMG_2.JPG", "2020-08-05_001.JPG", "u2")],
    )
    for item in repo.plan_items(conn, plan_id):
        if item.old_rel_path == "IMG_1.JPG":
            repo.set_plan_item_status(conn, item.id, "skipped")
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)

    assert preflight.refusals == {}
    assert [item.old_rel_path for item in preflight.actionable()] == ["IMG_2.JPG"]


def test_a_skip_in_one_unit_leaves_a_whole_neighbouring_unit_alone(conn, root):
    """The blast radius is the `unit_key` group and nothing wider. Two two-member
    units; one is decided out in part, and the other must still apply entire."""
    plan_id = make_plan(
        conn,
        [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "live-1"),
            ("IMG_1.MOV", "2019-07-04_001.MOV", "live-1"),
            ("IMG_2.JPG", "2020-08-05_001.JPG", "live-2"),
            ("IMG_2.MOV", "2020-08-05_001.MOV", "live-2"),
        ],
    )
    for item in repo.plan_items(conn, plan_id):
        if item.old_rel_path == "IMG_1.MOV":
            repo.set_plan_item_status(conn, item.id, "skipped")
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)

    assert [item.old_rel_path for item in preflight.actionable()] == ["IMG_2.JPG", "IMG_2.MOV"]


# The sentence each status of the photo's companion earns the photo, SPELLED OUT LITERALLY
# from the ruling and spec 7.3 - never `CONSTANT.format(...)`, which passes for any value of
# the constant. `None` means the photo is not refused at all.
_SKIPPED_SENTENCE = (
    "IMG_1.JPG was left alone: another file it is grouped with was skipped, and pam renames"
    " a group together or not at all. Run: pam plan, then approve or skip the whole group"
    " together."
)
_UNDECIDED_SENTENCE = (
    "IMG_1.JPG was left alone: another file it is grouped with was not approved, and pam"
    " renames a group together or not at all. Run: pam plan, then approve or skip the whole"
    " group together."
)
_COMPANION_SENTENCE = (
    "IMG_1.JPG was left alone: another file it is grouped with could not be renamed, and pam"
    " renames a group together or not at all. Run: pam plan to rebuild the plan."
)
PARTITION_CASES = (
    [(status, None) for status in sorted(SPEC_ACTIONABLE)]
    + [(status, _SKIPPED_SENTENCE) for status in sorted(SPEC_DECIDED_OUT)]
    + [(status, _UNDECIDED_SENTENCE) for status in sorted(SPEC_UNDECIDED)]
    + [(status, _COMPANION_SENTENCE) for status in SPEC_UNEXPECTED]
)


@pytest.mark.parametrize(("status", "sentence"), PARTITION_CASES)
def test_any_member_apply_will_not_rename_refuses_its_unit_however_it_got_there(
    conn, root, status, sentence
):
    """`skipped` is the status a user writes, but it is not the only non-actionable one
    spec 4 defines, and the guarantee is about renaming rather than about how a status
    was reached.

    `failed` is reachable in an OPEN plan through one narrow window - a power cut
    between `_record_preflight`'s commit and the `applying` write leaves a plan open
    with items this preflight refused last time. `applied` and `undone` are written only
    while a plan is `applying`, so they should never appear; they are refused rather
    than ignored because the safe reading of an unexpected status is "do not rename this
    unit". All three read as COMPANION_REASON, which is true of the one that is
    reachable and is the sentence its siblings were already given.

    REWRITTEN UNDER F1 (M13 Task 1 Step 9) over the FOUR halves of the partition: an
    approved companion refuses nothing, a skipped one reads as the skip, an UNDECIDED one
    (`proposed`, which apply no longer acts on - F1 R1) reads as the undecided sentence,
    and the rest read as COMPANION_REASON. Before F1 `proposed` was actionable and this
    control ran over the unexpected statuses only.
    """
    plan_id = live_photo(conn, video_status=status)

    preflight = validate_plan(conn, plan_id, root=root)

    photo = preflight.items[0]
    assert photo.old_rel_path == "IMG_1.JPG"
    if sentence is None:
        assert preflight.refusals == {}
        assert [item.old_rel_path for item in preflight.actionable()] == [
            "IMG_1.JPG",
            "IMG_1.MOV",
        ]
    else:
        assert preflight.refusals[photo.id] == sentence
        assert preflight.actionable() == []


def test_the_preflight_status_partition_covers_the_whole_enum():
    """Spec 4's six statuses split FOUR ways with nothing left over and no overlap.

    REWRITTEN UNDER F1 (M13 Task 1 Step 9): three ways became four when `proposed` left the
    actionable half (F1 R1) for a half of its own.

    Every half is spelled from the spec at the top of this module, so a seventh status
    added to the enum without a decision about which half it belongs in is a red test
    rather than a value the preflight silently treats as actionable - and a status added
    to `ACTIONABLE_STATUSES` that spec 4 does not define is red for the same reason.
    Modelled on `test_webui_decide.py::test_the_two_status_partitions_cover_the_whole_enum`,
    which pins spec 8.2's split of the same enum.
    """
    assert repo.ITEM_STATUSES == SPEC_ITEM_STATUSES
    assert frozenset(ACTIONABLE_STATUSES) == SPEC_ACTIONABLE
    halves = (SPEC_ACTIONABLE, SPEC_DECIDED_OUT, SPEC_UNDECIDED, frozenset(SPEC_UNEXPECTED))
    assert frozenset().union(*halves) == SPEC_ITEM_STATUSES
    assert sum(len(half) for half in halves) == len(SPEC_ITEM_STATUSES)
    # And that the control really does run over every status of every half. The collection
    # asserts above catch a half being emptied; this catches the parameters being narrowed
    # away from them - "applied and undone are unreachable, drop them" is the
    # plausible-looking edit, and it would silently halve the control.
    control = test_any_member_apply_will_not_rename_refuses_its_unit_however_it_got_there
    parametrized = [mark for mark in control.pytestmark if mark.name == "parametrize"]
    assert [mark.args[1] for mark in parametrized] == [PARTITION_CASES]
    assert sorted(status for status, _ in PARTITION_CASES) == sorted(SPEC_ITEM_STATUSES)


def test_the_companion_refusal_says_what_happened_and_names_the_route(conn, root):
    """COMPANION_REASON's copy, spelled out literally exactly once.

    Comparing a refusal against `COMPANION_REASON.format(...)` - which every other
    assertion in this module does, and rightly, because they are about WHICH sentence
    was chosen - passes for any value of the constant, including an empty one. This is
    the assertion that is about the sentence itself.

    UX 2: it carries a route, because rev 15's wider containment can refuse a unit whose
    blocking member is not a candidate and so earns no sentence of its own, leaving this
    one printed alone.
    """
    plan_id = live_photo(conn, video_status="failed")

    reason = next(iter(validate_plan(conn, plan_id, root=root).refusals.values()))

    assert reason == (
        "IMG_1.JPG was left alone: another file it is grouped with could not be renamed,"
        " and pam renames a group together or not at all. Run: pam plan to rebuild the"
        " plan."
    )


def test_a_refusal_does_not_touch_an_unrelated_unit(conn, root):
    plan_id = make_plan(
        conn,
        [("IMG_1.JPG", "2019-07-04_001.JPG", "u1"), ("IMG_2.JPG", "2020-08-05_001.JPG", "u2")],
    )
    add_file(conn, "2019-07-04_001.JPG", missing=True)
    conn.commit()

    preflight = validate_plan(conn, plan_id, root=root)
    assert [item.old_rel_path for item in preflight.actionable()] == ["IMG_2.JPG"]


def test_validation_writes_nothing_at_all(conn, root, tmp_path):
    """Preflight is a read. The refusals it finds are recorded by apply, in the same
    transaction as `--all`'s approvals - so a plan that is never applied keeps every
    item exactly as the user left it (here: approved, by `make_plan`)."""
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    add_file(conn, "2019-07-04_001.JPG", missing=True)
    conn.commit()
    validate_plan(conn, plan_id, root=root)

    reader = connect(tmp_path / "workspace" / "catalog.db")
    reader.row_factory = sqlite3.Row
    try:
        assert [item.status for item in repo.plan_items(reader, plan_id)] == ["approved"]
        assert repo.plan_status(reader, plan_id) == "open"
        assert journal_rows(reader) == []
    finally:
        reader.close()


# --- C1: a no-op item's file is not where pam believes it is (spec 7.0 J12) --------


def test_a_no_op_member_whose_file_moved_outside_pam_is_refused(conn, root):
    """C1: the no-op item goes behind the same existence check every other item passes.

    `is_noop` answers from `old_rel_path`, the catalog's BELIEF. Before this check, an
    external rename made pam record `applied` for a path that is not on disk, and spec
    7.5's twelve checks could not see it. One `stat` closes it, and spec 7.3's companion
    containment then blocks the unit for free - which is why this lives in the preflight.

    ADAPTED FROM THE PLAN'S LITERAL BODY: `tests/test_validator.py` drives `validate_plan`
    directly through its own `conn`/`make_plan` fixtures rather than through `pam scan`
    and `pam plan`, so the archive is built by hand here. The end-to-end construction the
    plan's body describes (`_partial_unit`) lives in `tests/test_f12_unit_membership.py`
    and is where Step 6's property is asserted against the real commands.
    """
    plan_id = make_plan(
        conn,
        [
            # The no-op member: the plan proposes the name it already has.
            ("2019-07-04_001.NEF", "2019-07-04_001.NEF", "u1"),
            # Its companion, a real rename, in the same unit.
            ("2019-07-04_001.acr_.png", "2019-07-04_001.png", "u1"),
        ],
    )
    (root / "2019-07-04_001.acr_.png").write_bytes(b"jpeg")
    # The user renamed the no-op member outside pam, after the plan was written.
    (root / "EXTERNAL.NEF").write_bytes(b"raw")

    preflight = validate_plan(conn, plan_id, root=root)

    reasons = list(preflight.refusals.values())
    assert len(reasons) == 2, reasons  # the no-op member AND its companion
    assert any("is not where pam last saw it" in r for r in reasons)
    assert any("Run: pam scan" in r for r in reasons)
    assert preflight.noops() == []
    assert preflight.actionable() == []


@pytest.mark.parametrize("status", SPEC_NON_ACTIONABLE)
def test_a_no_op_item_apply_will_not_touch_is_not_refused_for_having_moved(conn, root, status):
    """`_moved_no_op_refusals`' STATUS operand, which nothing pinned (reviewer's F1, M1702b).

    Neutralising `item.status in ACTIONABLE_STATUSES` inside `_moved_no_op_refusals` left
    the whole suite green at 2977. It is not benign: `apply._record_preflight` iterates
    `preflight.items`, not the candidates, so ANY id in `refusals` is written `failed` and
    recorded as a per-file error. A `skipped` no-op member that the user moved would have
    their own decision overwritten with `failed`, and `pam apply` would exit 1 over a plan
    that did exactly what it was asked - the argument
    `test_a_unit_whose_members_are_all_skipped_is_not_reported_as_refused` makes above.

    This is the same shape as `test_a_no_op_item_outside_the_actionable_statuses_is_never_
    resolved_to_applied` in `tests/test_f12_unit_membership.py`, which exists because the
    equivalent operand inside `noops()` also survived a whole suite (M1419). Where apply
    will not touch a file, where that file is is none of pam's business.

    THE STATUSES ARE SPELLED FROM SPEC 4'S ENUM, never read back from
    `ACTIONABLE_STATUSES`: an oracle built from the constant it checks agrees with a
    broken one, perfectly.
    """
    plan_id = make_plan(
        conn,
        [
            ("2019-07-04_001.NEF", "2019-07-04_001.NEF", "u1"),  # the no-op member
            ("2019-07-04_001.acr_.png", "2019-07-04_001.png", "u1"),  # its companion
        ],
    )
    moved = [i for i in repo.plan_items(conn, plan_id) if i.old_rel_path.endswith(".NEF")][0]
    repo.set_plan_item_status(conn, moved.id, status)
    conn.commit()
    # The companion is on disk; the no-op member is not - the user moved it.
    (root / "2019-07-04_001.acr_.png").write_bytes(b"jpeg")

    refusals = validate_plan(conn, plan_id, root=root).refusals

    assert moved.id not in refusals, refusals
    assert not any("is not where pam last saw it" in r for r in refusals.values()), refusals


def test_validate_plans_root_is_keyword_only_and_carries_no_default(conn, root):
    """The signature itself, because nothing else stands behind it (reviewer's F2, M1704).

    Giving `root` a default left the suite green at 2977 AND `mypy pam` and `ruff` clean:
    every call site passes it explicitly, so no test and no static gate can see the
    default. The docstring on `validate_plan` says what the default would cost - a future
    caller that omits it gets the process CWD, `(Path(".") / rel).exists()` is False for
    essentially every archive path, and `pam apply` refuses every no-op item in the
    archive - and a sentence that says "this must not happen" is an untested test until
    something asserts it.

    Keyword-only AND no default, as two separate assertions: a positional `root` would be
    silently satisfiable by `plan_id`'s neighbour, and a defaulted one is the case above.
    """
    param = inspect.signature(validate_plan).parameters["root"]

    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty

    # And the requirement is real at the call, not only in the signature object.
    plan_id = make_plan(conn, [("IMG_1.JPG", "2019-07-04_001.JPG", "u1")])
    with pytest.raises(TypeError, match="root"):
        validate_plan(conn, plan_id)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------------------
# spec 7.0 J13 (rev 26; the M11 review's R2): `pam apply` never writes a `{people}` value
# the catalog would no longer produce.
#
# BUILT BY THE REVIEW BEFORE THE FIX, end to end: a person HIDDEN in Manage People - whose
# own panel says "not used in filenames" - or removed by `pam faces purge`, after `pam
# plan`, reached disk through `pam apply` of the still-open plan, `renamed 1, failed 0`.
# Every `{people}` test before this planned and read the plan; none applied a plan made
# BEFORE a people change.
#
# EVERY CASE IS A LIVE PHOTO UNIT (`.JPG` + `.MOV`), so vision S3 is inside the test: the
# video carries no face, and a refusal that took the photograph and left the video would
# split the unit - which is why each case asserts BOTH files are still where they were.
# The real `run_plan`, the real `run_apply`, the real `run_purge`.
# ---------------------------------------------------------------------------------------

from test_m11_safety_constructions import _live_photo_unit, _plant_person_on  # noqa: E402

from pam.execute.apply import run_apply  # noqa: E402
from pam.execute.validator import STALE_PEOPLE_REASON  # noqa: E402
from pam.faces.purge import run_purge  # noqa: E402
from pam.naming.planner import run_plan  # noqa: E402
from pam.paths import workspace_if_exists  # noqa: E402

PEOPLE_TEMPLATE = "{date}_{people}_{seq}"
LIVE = ("LIVE_1.JPG", "LIVE_1.MOV")


def _planned_with_a_person(tmp_path, template=PEOPLE_TEMPLATE):
    root = _live_photo_unit(tmp_path)
    person = _plant_person_on(root, "LIVE_1.JPG", "Secret")
    plan_id = run_plan(root, template_override=template).plan_id
    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    return root, person, plan_id


def _sql(root, sql, params=()):
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _write_config(root, text):
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(text, encoding="utf-8")


def _merge_into_a_new_person(root, person):
    _sql(root, "INSERT INTO persons (label) VALUES ('Other')")
    _sql(root, "UPDATE face_instances SET person_id = (SELECT max(id) FROM persons)")
    _sql(root, "DELETE FROM persons WHERE id = ?", (person,))


def _on_disk(root):
    return sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json"))


# Every way the `{people}` answer changes after a plan, WRITTEN OUT BY HAND and never read
# off the faces routes: a list derived from the routes would silently drop the one cause
# that goes through NO route at all (`[tiers] faces.floor` edited in pam.toml).
STALE_CAUSES = {
    "hidden": lambda root, p: _sql(root, "UPDATE persons SET hidden = 1"),
    "renamed": lambda root, p: _sql(root, "UPDATE persons SET label = 'Renamed'"),
    "filename_label": lambda root, p: _sql(root, "UPDATE persons SET filename_label = 'Nick'"),
    "unassigned": lambda root, p: _sql(root, "UPDATE face_instances SET person_id = NULL"),
    "not_a_face": lambda root, p: _sql(
        root, "UPDATE face_instances SET review_state = 'not_a_face'"
    ),
    "dismissed": lambda root, p: _sql(
        root, "UPDATE face_instances SET review_state = 'dismissed'"
    ),
    "merged": _merge_into_a_new_person,
    "purged": lambda root, p: run_purge(root, confirm=lambda _i, _r: True),
    "floor_raised": lambda root, p: _write_config(root, "[tiers]\nfaces = { floor = 0.95 }\n"),
    "max_people_zero": lambda root, p: _write_config(root, "[naming]\nmax_people = 0\n"),
}
assert len(STALE_CAUSES) == 10, (
    "J13's causes were narrowed; each is a route a name took to disk"
)


@pytest.mark.parametrize("cause", sorted(STALE_CAUSES))
def test_j13_a_plan_whose_people_changed_is_refused_WHOLE_and_no_name_reaches_disk(
    tmp_path, cause
):
    root, person, plan_id = _planned_with_a_person(tmp_path)
    STALE_CAUSES[cause](root, person)

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE), "a member moved: the unit was split (vision S3)"
    reasons = dict(summary.failures)
    assert reasons["LIVE_1.JPG"] == STALE_PEOPLE_REASON.format(old_rel_path="LIVE_1.JPG")


def test_j13_the_positive_control_an_unchanged_catalog_applies_the_name(tmp_path):
    """Without it, every refusal above could come from a J13 that refuses everything."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    summary = run_apply(plan_id, root, confirm=None)
    assert (summary.renamed, summary.failed) == (2, 0), summary.failures
    assert _on_disk(root) == ["2019-07-04_Secret_001.JPG", "2019-07-04_Secret_001.MOV"]


def test_j13_a_name_the_user_EDITED_is_their_text_and_is_applied(tmp_path):
    """spec 8.2's manual rename: a stem a person typed is user truth. pam does not refuse
    it because the catalog changed under a token the person overrode."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    for name in LIVE:
        ext = name.rpartition(".")[2]
        _sql(
            root,
            "UPDATE plan_items SET new_filename = ? WHERE old_rel_path = ?",
            (f"typed_Secret.{ext}", name),
        )
    _sql(root, "UPDATE persons SET hidden = 1")

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (2, 0), summary.failures
    assert _on_disk(root) == ["typed_Secret.JPG", "typed_Secret.MOV"]


def test_j13_a_plan_with_NO_people_token_is_untouched_by_a_people_change(tmp_path):
    """The over-refusal control: J13 is about names built from people, not about plans
    that happen to sit in an archive with faces in it."""
    root, _person, plan_id = _planned_with_a_person(tmp_path, template="{date}_{seq}")
    _sql(root, "UPDATE persons SET hidden = 1")
    summary = run_apply(plan_id, root, confirm=None)
    assert (summary.renamed, summary.failed) == (2, 0), summary.failures


def test_j13_a_plan_made_BEFORE_j13_froze_the_named_people_is_refused(tmp_path):
    """A `{people}` entry with no `named` list is a plan pam cannot check - refused, with a
    sentence that says "cannot confirm" rather than claiming something changed."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        for item_id, tokens in conn.execute(
            "SELECT id, tokens_json FROM plan_items"
        ).fetchall():
            entries = json.loads(tokens)
            for entry in entries:
                entry.pop("named", None)
            conn.execute(
                "UPDATE plan_items SET tokens_json = ? WHERE id = ?",
                (json.dumps(entries), item_id),
            )
        conn.commit()
    finally:
        conn.close()

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE)
    assert "can no longer confirm the people" in dict(summary.failures)["LIVE_1.JPG"]


# --- J13's own mutation pass (the pre-HT fix pass, docs/mutations/m11.md M1967-M1974) ------
#
# Four mutants of `_stale_people_refusals` were predicted to survive the tests above, each
# because no NATURAL plan can separate the two readings. So each case below is BUILT BY HAND
# (M8's M993: where a property says "and not X", build the case where X and the truth
# disagree, even if the real pipeline never produces it).


def _rewrite_tokens(root, rel_path, rewrite):
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        (tokens,) = conn.execute(
            "SELECT tokens_json FROM plan_items WHERE old_rel_path = ?", (rel_path,)
        ).fetchone()
        conn.execute(
            "UPDATE plan_items SET tokens_json = ? WHERE old_rel_path = ?",
            (rewrite(tokens), rel_path),
        )
        conn.commit()
    finally:
        conn.close()


def _without_people(tokens):
    return json.dumps([e for e in json.loads(tokens) if e.get("resolved_from") != "people"])


def _without_named(tokens):
    entries = json.loads(tokens)
    for entry in entries:
        entry.pop("named", None)
    return json.dumps(entries)


def test_j13_refuses_the_WHOLE_unit_even_when_only_one_member_carries_the_token(tmp_path):
    """M1967. Every member normally carries the same `tokens_json`, so the merge ORDER
    (before `blocked_units`) is invisible - each member refuses itself. Strip the token off
    the video by hand and the order is the only thing standing between the refused
    photograph and a video renamed on its own: vision S3's split."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    _rewrite_tokens(root, "LIVE_1.MOV", _without_people)
    _sql(root, "UPDATE persons SET hidden = 1")

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE), "the video moved alone: the unit was split"
    assert dict(summary.failures)["LIVE_1.MOV"] == COMPANION_REASON.format(
        old_rel_path="LIVE_1.MOV"
    )


def test_j13_sees_a_change_to_the_SECOND_person_a_name_carries(tmp_path):
    """M1969. Every case above names one person, where "compare the first" and "compare
    them all" agree. Two people, and the change is to the one that sorts second."""
    root = _live_photo_unit(tmp_path)
    _plant_person_on(root, "LIVE_1.JPG", "Abby")
    zed = _plant_person_on(root, "LIVE_1.JPG", "Zed")
    plan_id = run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id
    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    _sql(root, "UPDATE persons SET hidden = 1 WHERE id = ?", (zed,))

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE)


def test_j13_a_pre_j13_plan_whose_person_is_ALSO_gone_is_still_refused(tmp_path):
    """M1972. A plan with no frozen list, and a person hidden since: `pam plan` would now
    name nobody, so a check that read a missing list as "nobody" would call the two equal
    and write the hidden name. The missing list is its own refusal."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    for name in LIVE:
        _rewrite_tokens(root, name, _without_named)
    _sql(root, "UPDATE persons SET hidden = 1")

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE)


def test_j13_a_tokens_json_that_does_not_parse_is_refused_not_waved_through(tmp_path):
    """M1974. pam never writes one, which is exactly why nothing tested it: a name pam
    cannot read the provenance of is a name it cannot confirm."""
    root, _person, plan_id = _planned_with_a_person(tmp_path)
    for name in LIVE:
        _rewrite_tokens(root, name, lambda _tokens: "{not json")

    summary = run_apply(plan_id, root, confirm=None)

    assert (summary.renamed, summary.failed) == (0, 2), summary.failures
    assert _on_disk(root) == sorted(LIVE)

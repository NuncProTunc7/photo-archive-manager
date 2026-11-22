"""M8 Task 9: {event}, {event-or-scene}, and {seq}'s (event_id, date) scope.

Design spec 6.1 rev 14 and 6.3 rule 2 rev 6. This is M8's shipping gate: the only
task in the milestone that changes a proposed filename, and a proposed filename is
what `pam apply` renames an irreplaceable file to.

Two things about how these tests are written, both learned the expensive way on this
project:

- **Every expectation is spelled out from the SPEC and the FIXTURE**, never from what
  the production call happened to return. `assert seq == _seq_numbers(...)` and
  `assert names == whatever_the_planner_emitted` are the vacuous shape M8 has now met
  nine times (M743, M793, M811, M820/M821, M824, M838, M894, M918, M942). The dates
  are literals, the event membership is worked out from spec 8.3's 4-hour rule by
  hand, and the sequence numbers are written down.
- **The rule-2 postcondition is asserted over the STORED plan rows**, read with the
  suite's own SQL, not over what `run_plan` returned. A planner that computed two
  identical names and then dropped one would satisfy an in-memory assertion and still
  write the archive-destroying plan.
"""

import json
import sqlite3

import pytest
from archives import FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.errors import TemplateError
from pam.naming.planner import run_plan
from pam.naming.tokens import resolve_tokens
from pam.naming.units import Unit, _seq_numbers
from pam.paths import workspace_for
from pam.scan.runner import run_scan

# --------------------------------------------------------------------------- fixtures


def build(tmp_path, files):
    """An archive of `{filename: exif date or None}`, ready to scan.

    A local builder rather than `archives.put`: these tests need per-file dates chosen
    to sit either side of spec 8.3's gap threshold, and one file with no date tag at
    all. The `.tags.json` stem-mate is what the fake exiftool reads.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in files.items():
        # `name` may carry a subdirectory: spec 6.3 rule 2's namespace is PER
        # DIRECTORY, and a flat archive cannot tell that half of the rule from no
        # rule at all.
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(f"the only copy of {name}".encode())
        tags = {} if date is None else {"DateTimeOriginal": date}
        (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")
    return root


def scanned(tmp_path, files):
    root = build(tmp_path, files)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def stored_items(root, plan_id):
    """{old rel_path: (generated_filename, tokens_json)} - the suite's own SQL.

    Read back from `plan_items` rather than taken from the PlanSummary, because what
    `pam apply` will rename to is the stored row and nothing else.
    """
    conn = _conn(root)
    try:
        return {
            str(r["old_rel_path"]): (str(r["generated_filename"]), str(r["tokens_json"]))
            for r in conn.execute(
                "SELECT old_rel_path, generated_filename, tokens_json"
                " FROM plan_items WHERE plan_id = ?",
                (plan_id,),
            )
        }
    finally:
        conn.close()


def stored_names(root, plan_id):
    return {old: name for old, (name, _) in stored_items(root, plan_id).items()}


def event_rows(root):
    """[(id, label, start_dt, end_dt)] in span order - the suite's own SQL."""
    conn = _conn(root)
    try:
        return [
            (int(r["id"]), r["label"], str(r["start_dt"]), str(r["end_dt"]))
            for r in conn.execute(
                "SELECT id, label, start_dt, end_dt FROM events ORDER BY start_dt, id"
            )
        ]
    finally:
        conn.close()


def set_label(root, event_id, label):
    """Seed a label on one events row (M8 plan clarification 3).

    In M8 no code writes a non-NULL label - `repo.events._insert_event` leaves it NULL
    because labels are geocoded and geocoding is M9. A test may write catalog state,
    the same way M5's tests seeded journal rows, and this is the ONLY way `{event}` can
    be exercised in this milestone. It survives a re-plan on purpose: spec 8.3 rev 14
    has a replacing run REUSE the row when `(start_dt, end_dt)` and the parameter
    identity are unchanged, and reuse does not rewrite the label.

    M9 TASK 9 MADE `label_source` PART OF WHAT THIS SEEDS, and it is not decoration.
    `pam plan` now geocodes every event's centroid and writes the answer through
    `repo.replace_events`, which OVERWRITES a label whose source is NULL or 'geocode'
    and leaves a 'user' one alone. These fixtures carry no GPS, so a re-plan would
    geocode them to nothing and clear a label seeded without a source - which is the
    correct behaviour for a stale geocode and the wrong one for a person's own label.
    Seeding 'user' is what makes this helper mean what its name says.
    """
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE events SET label = ?, label_source = 'user' WHERE id = ?",
            (label, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def assert_rule_2_postcondition(root, plan_id):
    """spec 6.3 rule 2: no two items of one plan propose one filename in one directory.

    Case-insensitively, because that is what rule 2 rev 6 and 7.1 both compare on and
    what Windows enforces. Asserted over the STORED rows, and asserted NON-EMPTY first:
    a plan with no items satisfies "no two items collide" for the uninteresting reason,
    and this project has already been bitten by a control that passed over nothing
    (M743's empty parametrize).
    """
    conn = _conn(root)
    try:
        rows = [
            (str(r["old_rel_path"]).rpartition("/")[0], str(r["generated_filename"]).lower())
            for r in conn.execute(
                "SELECT old_rel_path, generated_filename FROM plan_items WHERE plan_id = ?",
                (plan_id,),
            )
        ]
    finally:
        conn.close()
    assert rows, "the rule-2 postcondition must be checked over a plan that has items"
    assert len(set(rows)) == len(rows), f"two items propose one name: {sorted(rows)}"


def plan_file(**kw):
    base = dict(
        id=1,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        ext_as_written="jpg",
        media_type="image",
        placeholder=False,
        companion_group_id=None,
        capture_dt="2019-07-04T18:23:11",
        capture_dt_source="exif",
        has_capture=True,
    )
    base.update(kw)
    return repo.PlanFile(**base)


def sidecar_file(file_id, rel_path, ext):
    """A companion sidecar: no `capture` row, therefore in no event (spec 8.3 rev 14)."""
    return plan_file(
        id=file_id,
        rel_path=rel_path,
        filename=rel_path,
        ext=ext,
        ext_as_written=ext,
        media_type="sidecar",
        capture_dt=None,
        capture_dt_source=None,
        has_capture=False,
    )


def unit(file_id, rel_path, capture_dt):
    pf = plan_file(
        id=file_id,
        rel_path=rel_path,
        filename=rel_path.rpartition("/")[2],
        capture_dt=capture_dt,
    )
    return Unit(primary=pf, members=(pf,))


# ------------------------------------------------------- Step 1: {event} resolves


def test_the_event_token_is_the_events_label():
    # spec 6.1 rev 14: value = the label; provenance = (events, <event id>); tier =
    # the tier of the fact that produced the LABEL, `high` for a user-set one. Every
    # field is written here from that sentence, not read off the returned fact.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=repo.EventMembership(event_id=42, label="Lake Tahoe", label_source="user"),
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["event"].value == "Lake Tahoe"
    assert facts["event"].source_table == "events"
    assert facts["event"].source_id == 42
    assert facts["event"].tier == "high"


def test_the_event_tier_is_the_labels_and_never_the_groupings():
    # spec 6.1 rev 14: "never the tier of the grouping". The grouping of a file whose
    # date came from `file_mtime` is no less certain than any other - it is derived
    # from objective capture facts - so a file at the LOWEST date tier still carries a
    # `high` {event}. Reading the tier off the grouping (or off the item) would rate
    # this one `low`, and the two numbers here are deliberately different.
    facts = resolve_tokens(
        plan_file(capture_dt_source="file_mtime"),
        seq=1,
        event=repo.EventMembership(event_id=7, label="Camping", label_source="user"),
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["date"].tier == "low"
    assert facts["event"].tier == "high"


@pytest.mark.parametrize("label", [None, ""])
def test_an_event_with_an_empty_label_emits_no_event_token(label):
    # spec 6.1 rev 14: "an event with an empty label emits no token at all". Absent
    # from the dict, not present-and-empty - so no `events` pointer can be written for
    # a token that never reached a filename. This is the NORMAL case in M8.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=repo.EventMembership(event_id=3, label=label, label_source=None),
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert "event" not in facts


def test_a_file_in_no_event_has_no_event_token():
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert "event" not in facts
    assert set(facts) == {"date", "seq", "orig"}


# ------------------------------------------- Step 3: {seq} scopes per (event_id, date)

# The partition spec 6.1 rev 14 describes, written out by hand. Each row is
# (unit id, rel_path, capture_dt, event id or None, EXPECTED seq).
#
# Read it as four scopes:
#   (10, 2019-07-04) -> two units, ordered by capture_dt  -> 001, 002
#   (11, 2019-07-04) -> a DIFFERENT event on the SAME DAY -> 001 again
#   (10, 2019-07-05) -> the SAME event on a DIFFERENT DAY -> 001 again
#   (None, 2019-07-04) -> in no event: scopes by date alone, with the other
#                         no-event unit on that date and with NEITHER event above
SEQ_SCOPE_TABLE = [
    (1, "a.jpg", "2019-07-04T08:00:00", 10, 1),
    (2, "b.jpg", "2019-07-04T09:00:00", 10, 2),
    (3, "c.jpg", "2019-07-04T20:00:00", 11, 1),
    (4, "d.jpg", "2019-07-05T08:00:00", 10, 1),
    (5, "e.jpg", "2019-07-04T10:00:00", None, 1),
    (6, "f.jpg", "2019-07-04T11:00:00", None, 2),
]
assert SEQ_SCOPE_TABLE, "the {seq} scope table must not be empty"

# The floor on the table's EXTENT, added by the Task 9 stage-1 review (F3/M969). The
# non-empty assert above proves the table has rows and says nothing about what they
# cover: deleting the one row in which a second event shares a date with the first left
# the whole suite green while the table stopped expressing rev 14's headline change at
# all. Each line below is one clause of spec 6.1 rev 14, written from the SPEC and not
# from the table, so a row cannot go missing without one of them naming which.
_SEQ_SCOPES = {(event_id, dt[:10]) for _, _, dt, event_id, _ in SEQ_SCOPE_TABLE}
assert (10, "2019-07-04") in _SEQ_SCOPES and (11, "2019-07-04") in _SEQ_SCOPES, (
    "rev 14 scopes per (event_id, date): two events on ONE date must be two scopes"
)
assert (10, "2019-07-05") in _SEQ_SCOPES, (
    "rev 14 scopes per (event_id, date): one event over TWO dates must be two scopes"
)
assert (None, "2019-07-04") in _SEQ_SCOPES, (
    "rev 14 makes the scope total: a file in no event scopes by date alone"
)
assert (
    max(
        sum(1 for _, _, dt, e, _ in SEQ_SCOPE_TABLE if (e, dt[:10]) == scope)
        for scope in _SEQ_SCOPES
    )
    >= 2
), "some scope must hold two units, or the ordering rule is never exercised"


def test_seq_scopes_per_event_and_date():
    units = [unit(file_id, rel_path, dt) for file_id, rel_path, dt, _, _ in SEQ_SCOPE_TABLE]
    events = {
        file_id: repo.EventMembership(event_id=event_id, label=None, label_source=None)
        for file_id, _, _, event_id, _ in SEQ_SCOPE_TABLE
        if event_id is not None
    }
    assert _seq_numbers(units, events) == {
        file_id: expected for file_id, _, _, _, expected in SEQ_SCOPE_TABLE
    }


def test_a_file_in_no_event_scopes_by_date_alone():
    # spec 6.1 rev 14: "Rev 14 makes the scope total: a file that belongs to no event
    # scopes by date alone." The two ungrouped units below sit on ONE date and take
    # 001 and 002 - so `None` is that scope's date-only half, not a sentinel event
    # that every ungrouped file in the archive shares. The third unit is a day later
    # and starts again at 001, which is what "by date" means and what a single global
    # no-event bucket would get wrong.
    units = [
        unit(1, "a.jpg", "2019-07-04T08:00:00"),
        unit(2, "b.jpg", "2019-07-04T09:00:00"),
        unit(3, "c.jpg", "2019-07-05T08:00:00"),
    ]
    assert _seq_numbers(units, {}) == {1: 1, 2: 2, 3: 1}


def test_the_scope_is_read_off_the_primary_and_not_off_any_member():
    # spec 5.4/6.3: a unit renames as ONE stem built from its PRIMARY's facts, so its
    # {seq} scope is the PRIMARY's scope. A sidecar has no `capture` row, so it is in
    # `dated_files_for_events` for no event at all - and it must not be able to drag
    # the photograph it belongs to into the no-event bucket. That is vision S3: the two
    # halves of one unit landing on two stems.
    #
    # The primary sits in the MIDDLE of `members` deliberately. `_units` sorts members
    # by rel_path, so `IMG_1.AAE` < `IMG_1.jpg` < `IMG_1.xmp` puts a no-event member at
    # BOTH ends, and a scope read off `members[0]` or off `members[-1]` breaks alike.
    #
    # Added by the Task 9 stage-1 review (F1/M963): the end-to-end companion test below
    # cannot express this, because both members of a live-photo pair are dated and are
    # therefore in the SAME event, which makes the mutation equivalent there.
    primary = plan_file(
        id=1, rel_path="IMG_1.jpg", filename="IMG_1.jpg", capture_dt="2019-07-04T09:00:00"
    )
    members = tuple(
        [sidecar_file(2, "IMG_1.AAE", "aae"), primary, sidecar_file(3, "IMG_1.xmp", "xmp")]
    )
    pair = Unit(primary=primary, members=members)
    alone = unit(4, "a.jpg", "2019-07-04T08:00:00")
    events = {
        1: repo.EventMembership(event_id=10, label=None, label_source=None),
        4: repo.EventMembership(event_id=10, label=None, label_source=None),
    }
    # Both units are in event 10 on one date: `a.jpg` at 08:00 is 001 and the pair at
    # 09:00 is 002. A scope taken from either sidecar puts the pair alone in
    # `(None, 2019-07-04)`, where it would draw 001 and collide with `a.jpg`.
    assert _seq_numbers([pair, alone], events) == {1: 2, 4: 1}


def test_ordering_within_a_scope_is_capture_dt_then_old_filename():
    # spec 6.1: "ordered by (capture_dt, old filename)". rel_path order is the
    # REVERSE of capture_dt order here, so a sort that dropped capture_dt would
    # return 001 for z.jpg.
    units = [
        unit(1, "z.jpg", "2019-07-04T08:00:00"),
        unit(2, "a.jpg", "2019-07-04T09:00:00"),
    ]
    assert _seq_numbers(units, {}) == {1: 1, 2: 2}


def test_the_tiebreak_within_one_second_is_the_old_filename():
    units = [
        unit(1, "z.jpg", "2019-07-04T08:00:00"),
        unit(2, "a.jpg", "2019-07-04T08:00:00"),
    ]
    assert _seq_numbers(units, {}) == {2: 1, 1: 2}


# ---------------------------------------------- Step 4: two events on one day (THE GATE)

# Spec 8.3's default gap is 4 hours and the split test is `>`, so 08:00 and 18:00 on
# one day are TEN hours apart and are two events; 08:00 and 09:00 are one hour apart
# and are one. Every date below is chosen against that rule by hand.
TWO_EVENTS_ONE_DAY = {
    "morning_1.jpg": "2019:07:04 08:00:00",
    "morning_2.jpg": "2019:07:04 09:00:00",
    "evening_1.jpg": "2019:07:04 18:00:00",
    "evening_2.jpg": "2019:07:04 19:00:00",
}


def test_two_events_on_one_day_with_no_labels_get_collision_free_names(tmp_path):
    # THE case this task exists to make safe. Every label in M8 is empty, so {event}
    # emits nothing and the two events are indistinguishable in the stem: the morning
    # pair draws 001/002 within its own scope and the evening pair draws 001/002
    # within ITS own scope, so FOUR units on one date compete for TWO names.
    #
    # spec 6.3 rule 2 rev 6 is what resolves it, and it is explicit that appending
    # {seq} uses the unit's own current number and only a FURTHER collision
    # increments. Units are processed in rel_path order (evening before morning), so:
    #
    #   evening_1 (evening scope #1) -> 2019-07-04_001
    #   evening_2 (evening scope #2) -> 2019-07-04_002
    #   morning_1 (morning scope #1) -> 001 taken -> 002 taken -> 2019-07-04_003
    #   morning_2 (morning scope #2) -> 002 taken -> 2019-07-04_003 taken -> _004
    #
    # Numbers 001..004 with none skipped: safe AND explicable, which is the half of
    # rule 2 rev 6 that a "increment on first collision" loop got wrong.
    root = scanned(tmp_path, TWO_EVENTS_ONE_DAY)
    summary = run_plan(root, "{date}_{seq}")
    assert summary.events == 2
    assert [(start, end) for _, _, start, end in event_rows(root)] == [
        ("2019-07-04T08:00:00", "2019-07-04T09:00:00"),
        ("2019-07-04T18:00:00", "2019-07-04T19:00:00"),
    ]
    assert stored_names(root, summary.plan_id) == {
        "evening_1.jpg": "2019-07-04_001.jpg",
        "evening_2.jpg": "2019-07-04_002.jpg",
        "morning_1.jpg": "2019-07-04_003.jpg",
        "morning_2.jpg": "2019-07-04_004.jpg",
    }
    assert_rule_2_postcondition(root, summary.plan_id)


def test_two_events_on_one_day_are_stable_across_two_runs(tmp_path):
    # spec 8.3 rev 14's id reuse plus spec 6.2 rule 6's determinism, end to end: the
    # SECOND run re-groups the whole catalog from scratch, and must land on the same
    # events and therefore the same names. An id churn, or a scope that depended on
    # insertion order, would show up here as a different plan.
    root = scanned(tmp_path, TWO_EVENTS_ONE_DAY)
    first = run_plan(root, "{date}_{seq}")
    ids_before = [event_id for event_id, _, _, _ in event_rows(root)]
    second = run_plan(root, "{date}_{seq}")
    assert second.plan_id != first.plan_id
    assert [event_id for event_id, _, _, _ in event_rows(root)] == ids_before
    assert stored_names(root, second.plan_id) == stored_names(root, first.plan_id)
    assert_rule_2_postcondition(root, second.plan_id)


def test_the_default_template_also_survives_two_events_on_one_day(tmp_path):
    # The shipped template is `{date}_{event-or-scene}_{people}_{seq}`, and with every
    # label empty it degrades to exactly the stem above (spec 6.1: a missing token is
    # omitted along with its separator). Asserted separately because the previous test
    # passes a template override, and the names real users get come from this one.
    root = scanned(tmp_path, TWO_EVENTS_ONE_DAY)
    summary = run_plan(root)
    assert sorted(stored_names(root, summary.plan_id).values()) == [
        "2019-07-04_001.jpg",
        "2019-07-04_002.jpg",
        "2019-07-04_003.jpg",
        "2019-07-04_004.jpg",
    ]
    assert_rule_2_postcondition(root, summary.plan_id)


def test_the_seq_scope_change_is_visible_against_the_pre_m8_behaviour(tmp_path):
    # The pre-M8 planner scoped {seq} by date alone, so all four units above shared one
    # scope and drew 001..004 with no collision at all. M8 gives two of them 001 and
    # two of them 002 BEFORE rule 2 runs. The names come out the same only because
    # rule 2 resolved it - which is the whole reason this task is the shipping gate,
    # and is why the assertion below is about the SEQ NUMBERS and not about the names.
    root = scanned(tmp_path, TWO_EVENTS_ONE_DAY)
    run_plan(root, "{date}_{seq}")
    conn = _conn(root)
    try:
        by_path = {row.rel_path: row.id for row in repo.all_files(conn).values()}
        events = repo.event_membership(conn)
        files = repo.files_for_planning(conn, ("image", "video"))
    finally:
        conn.close()
    units = [Unit(primary=f, members=(f,)) for f in files]
    seq = _seq_numbers(units, events)
    assert [seq[by_path[name]] for name in sorted(TWO_EVENTS_ONE_DAY)] == [1, 2, 1, 2]


def test_the_scope_change_moves_a_real_filename_even_with_no_labels(tmp_path):
    # Step 6 measured ZERO changed names over both the tiny library and a human test's
    # real-world library, and that reading needs this test beside it or it reads as "the
    # change does nothing". It does something, and here is the archive that shows it.
    #
    # The trick is an archive where rel_path order DISAGREES with capture_dt order
    # across the two events. Units are planned in rel_path order, so `a.jpg` (18:00,
    # the evening event) is named first:
    #
    #   pre-M8, scoped by date alone and ordered by (capture_dt, old filename):
    #     z.jpg -> 1, a.jpg -> 2, so a.jpg takes _002 and z.jpg then takes _001.
    #   M8, scoped per (event_id, date):
    #     each is #1 in its OWN event, so a.jpg takes _001 and z.jpg collides up to
    #     _002 under rule 2.
    #
    # The two files SWAP numbers. Nothing here is a collision or an error - it is the
    # ordinary consequence of the scope spec 6.1 rev 14 mandates, and it is why this
    # task is M8's shipping gate.
    root = scanned(tmp_path, {"z.jpg": "2019:07:04 08:00:00", "a.jpg": "2019:07:04 18:00:00"})
    summary = run_plan(root, "{date}_{seq}")
    assert summary.events == 2
    assert stored_names(root, summary.plan_id) == {
        "a.jpg": "2019-07-04_001.jpg",
        "z.jpg": "2019-07-04_002.jpg",
    }
    assert_rule_2_postcondition(root, summary.plan_id)


# ------------------------------------------------- Steps 2 and 5: a seeded label

LABELLED = {
    "IMG_1.jpg": "2019:07:04 08:00:00",
    "IMG_2.jpg": "2019:07:04 09:00:00",
}


def labelled_archive(tmp_path, label, files=None):
    """Scan, plan once to create the event, seed a label on it, plan again.

    Two runs because the events row only exists after step 0 has run. The second run
    reuses it (spec 8.3 rev 14), so the label is still there when the names are built.
    """
    root = scanned(tmp_path, LABELLED if files is None else files)
    run_plan(root)
    rows = event_rows(root)
    assert len(rows) == 1, "the fixture must group into exactly one event"
    assert rows[0][1] is None, "M8 writes no label of its own - nothing else may either"
    set_label(root, rows[0][0], label)
    return root, rows[0][0]


def test_a_seeded_event_label_reaches_the_proposed_name(tmp_path):
    root, event_id = labelled_archive(tmp_path, "Fireworks")
    summary = run_plan(root, "{date}_{event}_{seq}")
    assert event_rows(root) == [
        (event_id, "Fireworks", "2019-07-04T08:00:00", "2019-07-04T09:00:00")
    ]
    assert stored_names(root, summary.plan_id) == {
        "IMG_1.jpg": "2019-07-04_Fireworks_001.jpg",
        "IMG_2.jpg": "2019-07-04_Fireworks_002.jpg",
    }


def test_a_seeded_label_is_sanitized_on_its_way_into_the_name(tmp_path):
    # spec 6.2 rules 1-3, applied to a label as to any other token: whitespace runs
    # become '-', '/' and '!' are outside the allowlist and go, and repeated '-'
    # collapse. The expectation is spelled from those three rules, not from
    # sanitize_token's return value.
    root, _ = labelled_archive(tmp_path, "Lake Tahoe / July 4th!")
    summary = run_plan(root, "{date}_{event}_{seq}")
    assert stored_names(root, summary.plan_id) == {
        "IMG_1.jpg": "2019-07-04_Lake-Tahoe-July-4th_001.jpg",
        "IMG_2.jpg": "2019-07-04_Lake-Tahoe-July-4th_002.jpg",
    }


def test_the_event_provenance_points_at_a_live_events_row(tmp_path):
    # spec 6.1 rev 6, made a WRITE-TIME postcondition by rev 14: the recorded
    # (source_table, source_id) must reference a row that EXISTS. Checked by looking
    # the id up in `events` with the suite's own SQL - the question is about the
    # catalog, so a production reader would only agree with itself.
    root, event_id = labelled_archive(tmp_path, "Fireworks")
    summary = run_plan(root, "{date}_{event}_{seq}")
    _, tokens_json = stored_items(root, summary.plan_id)["IMG_1.jpg"]
    event_tokens = [t for t in json.loads(tokens_json) if t["token"] == "event"]
    assert len(event_tokens) == 1
    token = event_tokens[0]
    assert token["value"] == "Fireworks"
    assert token["source_table"] == "events"
    assert token["tier"] == "high"
    conn = _conn(root)
    try:
        row = conn.execute(
            "SELECT label FROM events WHERE id = ?", (token["source_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the {event} provenance pointer must name a live events row"
    assert row["label"] == "Fireworks"


def test_event_or_scene_renders_the_event_label(tmp_path):
    # spec 6.1: {event-or-scene} is the event label if the file's event has one, else
    # the scene token (M10). {scene} resolves to nothing in M8, so a labelled event is
    # the only way this alias emits anything at all - and the emitted token's NAME is
    # 'event' while its SLOT is 'event-or-scene', which is what carries the `events`
    # provenance through to tokens_json.
    root, event_id = labelled_archive(tmp_path, "Fireworks")
    summary = run_plan(root, "{date}_{event-or-scene}_{seq}")
    name, tokens_json = stored_items(root, summary.plan_id)["IMG_1.jpg"]
    assert name == "2019-07-04_Fireworks_001.jpg"
    assert json.loads(tokens_json) == [
        {
            "token": "date",
            "resolved_from": "date",
            "value": "2019-07-04",
            "source_table": "capture",
            "source_id": token_source_id(root, "IMG_1.jpg"),
            "tier": "high",
            "truncated": False,
        },
        {
            "token": "event-or-scene",
            "resolved_from": "event",
            "value": "Fireworks",
            "source_table": "events",
            "source_id": event_id,
            "tier": "high",
            "truncated": False,
        },
        {
            "token": "seq",
            "resolved_from": "seq",
            "value": "001",
            "source_table": "planner",
            "source_id": None,
            "tier": "high",
            "truncated": False,
        },
    ]


def token_source_id(root, rel_path):
    """The files.id of one archive file - the suite's own SQL."""
    conn = _conn(root)
    try:
        row = conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel_path,)).fetchone()
    finally:
        conn.close()
    return int(row["id"])


def test_the_event_or_scene_alias_still_refuses_to_share_a_template(tmp_path):
    # spec 6.1 rev 4's mutual exclusion, confirmed with {event} now real and confirmed
    # AT PLAN TIME: `run_plan` parses the template before it touches the workspace, so
    # the refusal arrives before a lock is taken or an events row is written.
    root = scanned(tmp_path, LABELLED)
    for bad in ("{date}_{event-or-scene}_{event}", "{event-or-scene}_{scene}"):
        with pytest.raises(TemplateError):
            run_plan(root, bad)


# --------------------------------------------------- {seq}'s scope, end to end


def test_two_events_on_one_day_are_two_scopes_end_to_end(tmp_path):
    # The same partition as the pure test above, but through the real planner and a
    # real catalog: with a labelled morning event the two scopes become VISIBLE in the
    # names, because the stems no longer collide and rule 2 never fires. Both pairs
    # therefore read 001/002 - which is the (event_id, date) scope with nothing
    # papering over it.
    root = scanned(tmp_path, TWO_EVENTS_ONE_DAY)
    run_plan(root)
    rows = event_rows(root)
    assert len(rows) == 2
    set_label(root, rows[0][0], "Morning")
    set_label(root, rows[1][0], "Evening")
    summary = run_plan(root, "{date}_{event}_{seq}")
    assert stored_names(root, summary.plan_id) == {
        "morning_1.jpg": "2019-07-04_Morning_001.jpg",
        "morning_2.jpg": "2019-07-04_Morning_002.jpg",
        "evening_1.jpg": "2019-07-04_Evening_001.jpg",
        "evening_2.jpg": "2019-07-04_Evening_002.jpg",
    }
    assert_rule_2_postcondition(root, summary.plan_id)


def test_a_companion_unit_is_numbered_once_from_its_primary(tmp_path):
    # spec 5.4/6.3: a companion unit renames as ONE stem, built from its PRIMARY's
    # facts - so {seq} counts UNITS inside a scope, never files. The live-photo pair
    # below is one unit and takes 001 for both members; IMG_2.JPG is the second unit
    # and takes 002, not 003. Numbering by file would give the pair 001/002 and leave
    # IMG_2 at 003, and a scope keyed off a member rather than the primary could put
    # the two halves of the pair on two stems - vision S3, the outcome spec 7.3 exists
    # to prevent.
    root = scanned(
        tmp_path,
        {
            "IMG_1.JPG": "2019:07:04 08:00:00",
            "IMG_1.MOV": "2019:07:04 08:00:00",
            "IMG_2.JPG": "2019:07:04 09:00:00",
        },
    )
    run_plan(root)
    rows = event_rows(root)
    assert len(rows) == 1
    set_label(root, rows[0][0], "Camping")
    summary = run_plan(root, "{date}_{event}_{seq}")
    names = stored_names(root, summary.plan_id)
    assert names["IMG_1.JPG"] == "2019-07-04_Camping_001.JPG"
    assert names["IMG_1.MOV"] == "2019-07-04_Camping_001.MOV"
    assert names["IMG_2.JPG"] == "2019-07-04_Camping_002.JPG"
    assert_rule_2_postcondition(root, summary.plan_id)


# Two events on one day, in two DIRECTORIES. Stage 2 of the Task 9 review built this
# fixture: it is the first case in the suite where a plan legally contains one
# filename twice, and it exists because M8 made that case reachable.
#
# Pre-M8 the {seq} scope was the date alone, so two units on one date could never
# draw the same number and one plan could never hold a repeated filename, whatever
# the directories. Rev 14 scopes per (event_id, date): these two files are 8 hours
# apart (spec 8.3's threshold is 4), so they are two events, each unit is #1 in its
# own scope, and both propose 2019-07-04_001.jpg. That is CORRECT - spec 6.3 rule 2's
# namespace is "a case-insensitive set per DIRECTORY" - and it is what makes the
# per-directory half load-bearing rather than decorative from M8 onward.
TWO_EVENTS_TWO_DIRECTORIES = {
    "dirA/p.jpg": "2019:07:04 04:00:00",
    "dirB/q.jpg": "2019:07:04 12:00:00",
}


def test_two_events_on_one_day_in_two_directories_may_share_one_filename(tmp_path):
    # Every expectation below is written from the spec and the fixture, never from
    # what the planner returned. 04:00 and 12:00 are 8 h apart and spec 8.3's gap is
    # 4 h, so: two events, one unit each, each unit #1 in its own (event_id, date)
    # scope, and no collision to resolve because the two names are in two different
    # directories.
    root = scanned(tmp_path, TWO_EVENTS_TWO_DIRECTORIES)
    summary = run_plan(root, "{date}_{seq}")
    assert summary.events == 2
    assert [(start, end) for _, _, start, end in event_rows(root)] == [
        ("2019-07-04T04:00:00", "2019-07-04T04:00:00"),
        ("2019-07-04T12:00:00", "2019-07-04T12:00:00"),
    ]
    names = stored_names(root, summary.plan_id)
    assert names == {
        "dirA/p.jpg": "2019-07-04_001.jpg",
        "dirB/q.jpg": "2019-07-04_001.jpg",
    }
    # Said as its own assertion, because it is the property and not a side effect:
    # ONE plan, TWO items, ONE filename. Legal, and unreachable before rev 14.
    assert len(names) == 2 and len(set(names.values())) == 1
    # And the postcondition still passes over it. This is the only place in the suite
    # where that sentence means anything: everywhere else the plan is flat, so a
    # rule-2 check that dropped the directory from its key would agree with one that
    # kept it. Measured - deleting the directory from `assert_rule_2_postcondition`
    # left all 2155 other tests green.
    assert_rule_2_postcondition(root, summary.plan_id)

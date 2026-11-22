import pytest

from pam.catalog.repo import PlanFile
from pam.naming.tokens import (
    DATE_SOURCE_TIER,
    date_value,
    min_tier,
    resolve_tokens,
    stem_of,
)


def plan_file(**kw):
    base = dict(
        id=7,
        rel_path="2019/a.jpg",
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
    return PlanFile(**base)


@pytest.mark.parametrize(
    ("capture_dt", "expected"),
    [
        ("2019-07-04T18:23:11", "2019-07-04"),
        ("2019-07-04T00:00:00", "2019-07-04"),
        (None, ""),
        ("2019-07", ""),  # too short to be a date: absent, never guessed
    ],
)
def test_date_value(capture_dt, expected):
    assert date_value(capture_dt) == expected


@pytest.mark.parametrize(
    ("source", "tier"),
    [
        ("exif", "high"),
        ("qt_local", "high"),  # a real local time: ranks with exif (spec 5.5)
        ("filename", "medium"),
        ("qt_utc", "medium"),  # spec 6.1: the local date is unknowable from UTC
        ("file_mtime", "low"),
        ("none", "low"),
    ],
)
def test_date_tier_follows_the_source(source, tier):
    facts = resolve_tokens(
        plan_file(capture_dt_source=source),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["date"].tier == tier
    assert DATE_SOURCE_TIER[source] == tier


def test_absent_date_is_low_whatever_the_source_says():
    facts = resolve_tokens(
        plan_file(capture_dt=None, capture_dt_source="exif"),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["date"].value == "" and facts["date"].tier == "low"


def test_date_provenance_points_at_the_capture_row():
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
    assert facts["date"].source_table == "capture" and facts["date"].source_id == 7


def test_date_provenance_never_names_capture_when_there_is_no_capture_row():
    # spec 6.1 rev 6 / finding 4: a sidecar or placeholder has no capture row, and
    # 'source_table: capture' for it is a pointer into nothing - shipped straight
    # into the wire format M7's review UI reads. Worse than no provenance at all.
    facts = resolve_tokens(
        plan_file(capture_dt=None, capture_dt_source=None, has_capture=False),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["date"].source_table == "planner"
    assert facts["date"].source_id is None


def test_resolve_tokens_is_total_over_planfile_when_mtime_has_no_capture_row():
    # This shape (file_mtime source with no capture row) is not producible by
    # files_for_planning today - it is constructed deliberately here to pin that
    # resolve_tokens still attributes to `files` rather than crashing or naming a
    # nonexistent `capture` row, per spec 6.1 rev 6's total-over-PlanFile property.
    facts = resolve_tokens(
        plan_file(capture_dt_source="file_mtime", has_capture=False),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["date"].source_table == "files" and facts["date"].source_id == 7


def test_seq_is_three_digits():
    assert (
        resolve_tokens(
            plan_file(),
            seq=1,
            event=None,
            place=None,
            scene=None,
            caption_tier="medium",
            people=(),
            max_people=3,
            date=None,
        )["seq"].value
        == "001"
    )
    assert (
        resolve_tokens(
            plan_file(),
            seq=42,
            event=None,
            place=None,
            scene=None,
            caption_tier="medium",
            people=(),
            max_people=3,
            date=None,
        )["seq"].value
        == "042"
    )
    assert (
        resolve_tokens(
            plan_file(),
            seq=1,
            event=None,
            place=None,
            scene=None,
            caption_tier="medium",
            people=(),
            max_people=3,
            date=None,
        )["seq"].source_table
        == "planner"
    )


def test_orig_is_the_current_stem():
    facts = resolve_tokens(
        plan_file(filename="IMG_0007.jpg"),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["orig"].value == "IMG_0007"
    assert facts["orig"].source_table == "files"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("a.jpg", "a"), ("a.b.jpg", "a.b"), ("noext", "noext"), (".hidden", ".hidden")],
)
def test_stem_of(filename, expected):
    assert stem_of(filename) == expected


def test_enricher_tokens_are_absent_in_m4():
    # {event} lands in M8, {place} M9, {scene} M10, {people} M11. Absent, not
    # empty-stringed: build_stem omits them and their separator.
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
    assert set(facts) == {"date", "seq", "orig"}


def test_min_tier():
    assert min_tier(["high", "medium"]) == "medium"
    assert min_tier(["low", "high"]) == "low"
    # spec 6.3.4 rev 6 / finding 3: an item with NO emitted tokens is 'low'. The
    # old 'high' default rated a zero-fact name the most trustworthy pam can make,
    # which is exactly what M7's tier-gated bulk approval would sweep up.
    assert min_tier([]) == "low"


# The cross-module anchor that stood here, under `tests/test_people_naming.py`, M1937, was absorbed into
# `tests/census.py` at M12 Task 1 (m11.md condition C4): one hand-written inventory outside
# every guarded module, checked at collection by `conftest.py`.

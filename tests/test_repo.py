from pathlib import Path

import pytest
from archives import open_plan
from guards import (
    ALLOWED_REPO_REACHES,
    REPO_OWN_TABLES,
    REPO_REACH_EXEMPT,
    repo_cross_table_reaches,
    sql_table_names,
)

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.catalog.repo.plans import ext_of


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "catalog.db")
    migrate(c)
    yield c
    c.close()


NOW = "2026-07-23T10:00:00"
LATER = "2026-07-23T11:00:00"


# SPEC 8.1's FIVE ENRICHERS, WRITTEN OUT, and deliberately NOT `repo.ENRICHERS` (M6
# review, finding F2). Both tests below used to build their expectation from the very
# tuple `enrichment_coverage` iterates, so they agreed with any value of it: truncating
# it to `("caption",)` passed the whole 1238-test suite while deleting four enrichers
# from `pam status`. An oracle that calls the production definition cannot catch the
# production definition being wrong - the M5 review's `repo.open_plan` ruling, arriving
# for a constant instead of a query.
SPEC_8_1_ENRICHERS = ("caption", "faces", "ocr", "phash", "geocode")


def add_file(conn, rel_path="a/img.jpg", **overrides):
    kwargs = dict(
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1],
        media_type="image",
        size=100,
        mtime_ns=1_000,
        placeholder=False,
        now=NOW,
    )
    kwargs.update(overrides)
    return repo.upsert_file(conn, **kwargs)


def test_upsert_inserts_and_returns_id(conn):
    file_id = add_file(conn)
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    assert row["rel_path"] == "a/img.jpg"
    assert row["status"] == "present"
    assert row["first_seen_at"] == NOW
    assert row["last_scanned_at"] == NOW
    assert row["sha256"] is None


def test_upsert_updates_but_preserves_sha_and_first_seen(conn):
    file_id = add_file(conn)
    repo.set_sha256(conn, file_id, "abc123")
    same_id = add_file(conn, size=200, mtime_ns=2_000, now=LATER)
    assert same_id == file_id
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    assert row["size"] == 200
    assert row["mtime_ns"] == 2_000
    assert row["sha256"] == "abc123"
    assert row["first_seen_at"] == NOW
    assert row["last_scanned_at"] == LATER


def test_touch_files_updates_timestamp_and_status(conn):
    file_id = add_file(conn)
    repo.mark_missing(conn, [file_id])
    repo.touch_files(conn, [file_id], LATER)
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    assert row["status"] == "present"
    assert row["last_scanned_at"] == LATER


def test_mark_missing(conn):
    file_id = add_file(conn)
    repo.mark_missing(conn, [file_id])
    row = conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()
    assert row["status"] == "missing"


def test_all_files_roundtrip(conn):
    add_file(conn, rel_path="a/one.jpg")
    add_file(conn, rel_path="b/two.mp4", media_type="video")
    rows = repo.all_files(conn)
    assert set(rows) == {"a/one.jpg", "b/two.mp4"}
    assert rows["a/one.jpg"].media_type == "image"
    assert rows["b/two.mp4"].media_type == "video"
    assert rows["a/one.jpg"].placeholder is False


def test_capture_upsert_inserts_then_updates(conn):
    file_id = add_file(conn)
    cap = repo.CaptureFacts(
        capture_dt="2019-07-04T18:23:11",
        capture_dt_source="exif",
        tz_offset_min=120,
        gps_lat=39.1,
        gps_lon=-120.0,
        width=4000,
        height=3000,
        camera_make="Canon",
        camera_model="EOS R6",
        exif_json='{"Make": "Canon"}',
    )
    repo.upsert_capture(conn, file_id, cap)
    repo.upsert_capture(
        conn,
        file_id,
        repo.CaptureFacts(
            capture_dt="2020-01-01T00:00:00",
            capture_dt_source="filename",
            tz_offset_min=None,
            gps_lat=None,
            gps_lon=None,
            width=None,
            height=None,
            camera_make=None,
            camera_model=None,
            exif_json="{}",
        ),
    )
    rows = conn.execute("SELECT * FROM capture").fetchall()
    assert len(rows) == 1
    assert rows[0]["capture_dt"] == "2020-01-01T00:00:00"
    assert rows[0]["capture_dt_source"] == "filename"
    assert rows[0]["camera_make"] is None


def test_companion_group_create_and_clear(conn):
    jpg = add_file(conn, rel_path="a/IMG_1.jpg")
    raw = add_file(conn, rel_path="a/IMG_1.cr2")
    group_id = repo.create_companion_group(conn, jpg, "raw_jpeg", [jpg, raw])
    rows = repo.all_files(conn)
    assert rows["a/IMG_1.jpg"].companion_group_id == group_id
    assert rows["a/IMG_1.cr2"].companion_group_id == group_id
    group = conn.execute("SELECT * FROM companion_groups WHERE id = ?", (group_id,)).fetchone()
    assert group["kind"] == "raw_jpeg"
    assert group["primary_file_id"] == jpg
    repo.clear_companion_groups(conn)
    rows = repo.all_files(conn)
    assert rows["a/IMG_1.jpg"].companion_group_id is None
    assert conn.execute("SELECT COUNT(*) FROM companion_groups").fetchone()[0] == 0


def test_status_counts(conn):
    a = add_file(conn, rel_path="a.jpg")
    add_file(conn, rel_path="b.jpg")
    repo.mark_missing(conn, [a])
    assert repo.status_counts(conn) == {"present": 1, "missing": 1}


def test_media_type_counts_counts_missing_files_too(conn):
    """`pam status` prints these two lists side by side, and they must add up to the
    same total: a missing file is still a file pam knows about."""
    a = add_file(conn, rel_path="a.jpg")
    add_file(conn, rel_path="b.mov", media_type="video")
    repo.mark_missing(conn, [a])
    assert repo.media_type_counts(conn) == {"image": 1, "video": 1}


def test_placeholder_count_is_separate_from_status(conn):
    """Spec 4 stores `placeholder` as its own column and a placeholder is `present`:
    its entry is really there, its bytes are not (spec 5.2)."""
    add_file(conn, rel_path="a.jpg")
    add_file(conn, rel_path="cloud.jpg", placeholder=True)
    assert repo.placeholder_count(conn) == 1
    assert repo.status_counts(conn) == {"present": 2}


def test_last_scanned_at_is_the_newest_and_is_none_on_an_empty_catalog(conn):
    assert repo.last_scanned_at(conn) is None
    add_file(conn, rel_path="a.jpg", now=NOW)
    add_file(conn, rel_path="b.jpg", now=LATER)
    assert repo.last_scanned_at(conn) == LATER


def test_plan_status_counts_groups_every_status_it_finds(conn):
    ids = [
        repo.create_plan(
            conn,
            now=NOW,
            template="{date}",
            config_json="{}",
            app_version="0.1",
            schema_version=1,
        )
        for _ in range(3)
    ]
    repo.set_plan_status(conn, ids[0], "applied")
    repo.set_plan_status(conn, ids[1], "superseded")
    assert repo.plan_status_counts(conn) == {"applied": 1, "superseded": 1, "open": 1}


def test_enrichment_coverage_is_five_zeros_before_any_enricher_exists(conn):
    """Spec 12's "enrichment coverage" in M6: the rows exist and every one reads 0.

    A missing row reads as "not applicable"; a 0 reads as "not done yet", which is the
    true state of every enricher until M9.
    """
    add_file(conn, rel_path="a.jpg")
    assert repo.enrichment_coverage(conn) == dict.fromkeys(SPEC_8_1_ENRICHERS, 0)


def test_enrichment_coverage_counts_files_and_not_content_hashes(conn):
    """The join to `files` is what makes the number a coverage.

    Spec 4 keys the fact tables by `sha256`, so TWO identical photos are two covered
    files behind ONE fact row. Counting `DISTINCT sha256` inside `inferred_facts` would
    report 1 here, which answers a question spec 12 did not ask.
    """
    for name in ("a.jpg", "b.jpg"):
        file_id = add_file(conn, rel_path=name)
        repo.set_sha256(conn, file_id, "shared-digest")
    conn.execute(
        "INSERT INTO inferred_facts (sha256, kind, value_json, model_name, model_version,"
        " generated_at) VALUES ('shared-digest', 'caption', '{}', 'm', '1', ?)",
        (NOW,),
    )
    assert repo.enrichment_coverage(conn)["caption"] == 2


def test_enrichment_coverage_ignores_superseded_facts_and_unmapped_kinds(conn):
    """Two limits of the map, both deliberate and both visible here rather than only in
    a comment. Spec 8.1's re-enrichment supersedes the old fact, so counting both would
    report a file covered twice; and a `kind` the map does not list counts towards
    nothing at all - which is the thing M9 must revisit when it writes the vocabulary.
    """
    file_id = add_file(conn, rel_path="a.jpg")
    repo.set_sha256(conn, file_id, "digest")
    # The replacement is inserted FIRST so `superseded_by` has a row to point at:
    # `inferred_facts.superseded_by` is a self-referencing foreign key and
    # `db.connect` sets PRAGMA foreign_keys=ON.
    conn.execute(
        "INSERT INTO inferred_facts (id, sha256, kind, value_json, model_name,"
        " model_version, generated_at) VALUES (2, 'digest', 'not_a_mapped_kind', '{}',"
        " 'm', '2', ?)",
        (LATER,),
    )
    conn.execute(
        "INSERT INTO inferred_facts (id, sha256, kind, value_json, model_name,"
        " model_version, generated_at, superseded_by)"
        " VALUES (1, 'digest', 'caption', '{}', 'm', '1', ?, 2)",
        (NOW,),
    )
    assert repo.enrichment_coverage(conn) == dict.fromkeys(SPEC_8_1_ENRICHERS, 0)


def test_files_for_planning_joins_capture_and_skips_the_unnamed(conn):
    # spec 6.3.1: present images and videos, with facts. Sidecars come along only
    # as companion-group members (they rename with their primary, spec 5.4).
    a = repo.upsert_file(
        conn,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    repo.upsert_file(
        conn,
        rel_path="notes.txt",
        filename="notes.txt",
        ext="txt",
        media_type="other",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    lone = repo.upsert_file(
        conn,
        rel_path="lone.xmp",
        filename="lone.xmp",
        ext="xmp",
        media_type="sidecar",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    side = repo.upsert_file(
        conn,
        rel_path="a.xmp",
        filename="a.xmp",
        ext="xmp",
        media_type="sidecar",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    repo.upsert_capture(
        conn,
        a,
        repo.CaptureFacts(
            capture_dt="2019-07-04T18:23:11",
            capture_dt_source="exif",
            tz_offset_min=-420,
            gps_lat=None,
            gps_lon=None,
            width=None,
            height=None,
            camera_make=None,
            camera_model=None,
            exif_json="{}",
        ),
    )
    repo.create_companion_group(conn, a, "sidecar", [a, side])
    conn.commit()

    rows = repo.files_for_planning(conn, ("image", "video"))
    by_path = {r.rel_path: r for r in rows}
    assert set(by_path) == {"a.jpg", "a.xmp"}  # notes.txt and lone.xmp excluded
    assert lone not in [r.id for r in rows]
    assert by_path["a.jpg"].capture_dt == "2019-07-04T18:23:11"
    assert by_path["a.jpg"].capture_dt_source == "exif"
    assert by_path["a.xmp"].capture_dt is None  # sidecars have no capture row
    assert by_path["a.jpg"].companion_group_id == by_path["a.xmp"].companion_group_id
    # spec 6.1 rev 6: has_capture is the LEFT JOIN's own answer, so provenance can
    # never name a capture row that is not there.
    assert by_path["a.jpg"].has_capture is True
    assert by_path["a.xmp"].has_capture is False


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("IMG.JPG", "JPG"),
        ("img.jpg", "jpg"),
        ("IMG_1.acr_.Jpg", "Jpg"),
        ("noext", ""),
        (".hidden", ""),  # matches Path('.hidden').suffix, which walker uses
    ],
)
def test_ext_of_keeps_the_on_disk_case(filename, expected):
    # spec 5.4 rev 6: this - not files.ext - is what a proposed filename uses.
    assert ext_of(filename) == expected
    # And the invariant that lets files.ext stay lowercase for typing/matching.
    assert ext_of(filename).lower() == Path(filename).suffix.lower().lstrip(".")


def test_files_for_planning_records_the_extension_as_written(conn):
    # N1: files.ext is lowercased at walker.py:91, so building a proposed name
    # from it renamed almost every item of a phone library to a
    # case-only difference that spec 7.1 is guaranteed to reject.
    repo.upsert_file(
        conn,
        rel_path="a/IMG_1.HEIC",
        filename="IMG_1.HEIC",
        ext="heic",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    conn.commit()
    row = repo.files_for_planning(conn, ("image", "video"))[0]
    assert (row.ext, row.ext_as_written) == ("heic", "HEIC")


def test_set_file_path_rewrites_the_filename_and_leaves_ext_alone(conn):
    # M5 Task 5: RenameTransaction.commit() writes this in the same transaction as
    # the journal row. The NESTED path is the case that matters - rpartition("/") is
    # the only thing between `filename` and a full relative path, and a root-level
    # file cannot tell the two apart. `ext` stays lowercase for typing/matching
    # (spec 5.4 rev 6) while `filename` keeps the on-disk case.
    f = repo.upsert_file(
        conn,
        rel_path="2019/IMG_9.HEIC",
        filename="IMG_9.HEIC",
        ext="heic",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    repo.set_file_path(conn, f, "2019/2019-07-04_001.HEIC")
    conn.commit()
    row = repo.all_files(conn)["2019/2019-07-04_001.HEIC"]
    assert row.filename == "2019-07-04_001.HEIC"
    assert row.ext == "heic"


def test_set_plan_item_status_refuses_a_status_outside_the_enum(conn):
    # spec 4's status column carries no CHECK, so this guard is the only thing
    # standing between a typo and a plan_items row nothing downstream understands.
    # Every comparable vocabulary in journal.py (OPS, _STATES, _LEGAL_PREDECESSOR)
    # has a test; this one did not, and deleting the guard passed the whole suite.
    with pytest.raises(ValueError):
        repo.set_plan_item_status(conn, 1, "aplied")


def test_set_plan_item_status_accepts_every_member_of_the_enum(conn):
    f = repo.upsert_file(
        conn,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    plan_id = repo.create_plan(
        conn,
        template="{orig}",
        config_json="{}",
        now="t",
        app_version="test",
        schema_version=4,
    )
    repo.insert_plan_items(
        conn,
        plan_id,
        [
            repo.PlanItemDraft(
                file_id=f,
                old_rel_path="a.jpg",
                unit_key="\x1fa.jpg",
                generated_filename="b.jpg",
                tokens_json="[]",
                tier="high",
                trust_score=100,
                trust_reasons_json="[]",
            )
        ],
    )
    conn.commit()
    item_id = repo.plan_items(conn, plan_id)[0].id
    for status in sorted(repo.ITEM_STATUSES):
        repo.set_plan_item_status(conn, item_id, status)
        conn.commit()
        assert repo.plan_items(conn, plan_id)[0].status == status


def test_files_for_planning_skips_missing_files(conn):
    f = repo.upsert_file(
        conn,
        rel_path="gone.jpg",
        filename="gone.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    repo.mark_missing(conn, [f])
    conn.commit()
    assert repo.files_for_planning(conn, ("image", "video")) == []


def test_companion_primaries(conn):
    a = repo.upsert_file(
        conn,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    b = repo.upsert_file(
        conn,
        rel_path="a.mov",
        filename="a.mov",
        ext="mov",
        media_type="video",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    gid = repo.create_companion_group(conn, a, "live_photo", [a, b])
    conn.commit()
    assert repo.companion_primaries(conn) == {gid: a}


def test_plan_write_read_and_supersede(conn):
    f = repo.upsert_file(
        conn,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    conn.commit()
    first = repo.create_plan(
        conn,
        now="t1",
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=3,
    )
    repo.insert_plan_items(
        conn,
        first,
        [
            repo.PlanItemDraft(
                file_id=f,
                old_rel_path="a.jpg",
                unit_key="\x1fa.jpg",
                generated_filename="2019-07-04_001.jpg",
                tokens_json="[]",
                tier="high",
                trust_score=100,
                trust_reasons_json="[]",
            )
        ],
    )
    conn.commit()

    plan = open_plan(conn)
    assert plan is not None and plan.id == first and plan.status == "open"
    items = repo.plan_items(conn, first)
    assert len(items) == 1
    # spec 4: user edits land in new_filename, which starts as the generated name.
    assert items[0].generated_filename == items[0].new_filename == "2019-07-04_001.jpg"
    assert items[0].status == "proposed"
    # spec 4/6.3 rule 6: the frozen unit round-trips verbatim - spec 7.3 reads it
    # back to group the apply, so a lossy write is a split companion group.
    assert items[0].unit_key == "\x1fa.jpg"

    # spec 6.3.5: a new plan supersedes prior open plans.
    assert repo.supersede_open_plans(conn) == 1
    second = repo.create_plan(
        conn,
        now="t2",
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=3,
    )
    conn.commit()
    plan = open_plan(conn)
    assert plan is not None and plan.id == second
    assert repo.plan_status(conn, first) == "superseded"


def _draft(file_id, unit_key):
    return repo.PlanItemDraft(
        file_id=file_id,
        old_rel_path="a.jpg",
        unit_key=unit_key,
        generated_filename="2019-07-04_001.jpg",
        tokens_json="[]",
        tier="high",
        trust_score=100,
        trust_reasons_json="[]",
    )


def _one_file_and_plan(conn):
    file_id = repo.upsert_file(
        conn,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now="t",
    )
    conn.commit()
    plan_id = repo.create_plan(
        conn,
        now="t1",
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=4,
    )
    return file_id, plan_id


def test_insert_plan_items_refuses_an_empty_unit_key(conn):
    """M5 Task 2 review, finding I2. Migration 0004 had to give unit_key a
    `DEFAULT ''` (SQLite's ADD COLUMN NOT NULL requires one), so a pre-0004 plan
    legitimately carries ''. M5 Task 5's read-side refusal is for exactly those.
    An item written as '' by THIS version is indistinguishable from a pre-0004 item,
    so without this guard Task 5 would refuse plans this version wrote and blame the
    wrong cause. This is the only writer of unit_key, so it is where that gets loud.
    """
    file_id, plan_id = _one_file_and_plan(conn)
    with pytest.raises(ValueError, match="unit_key is required"):
        repo.insert_plan_items(conn, plan_id, [_draft(file_id, "")])
    conn.commit()
    assert repo.plan_items(conn, plan_id) == []


def test_insert_plan_items_accepts_a_normal_unit_key(conn):
    """The guard must not disturb the ordinary path (it fires on '' only)."""
    file_id, plan_id = _one_file_and_plan(conn)
    repo.insert_plan_items(conn, plan_id, [_draft(file_id, "\x1fa.jpg")])
    conn.commit()
    items = repo.plan_items(conn, plan_id)
    assert len(items) == 1 and items[0].unit_key == "\x1fa.jpg"


@pytest.mark.parametrize(
    "status", ["applying", "applied", "partially_applied", "undone", "superseded"]
)
def test_supersede_only_ever_touches_open_plans(conn, status):
    # The SQL is an allowlist (WHERE status = 'open'), so every other status in
    # the spec 4 enum is safe - and so is any status added later (a future
    # 'reviewing'), which is exactly why it must stay an allowlist.
    plan = repo.create_plan(
        conn,
        now="t",
        template="{date}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=3,
    )
    repo.set_plan_status(conn, plan, status)
    conn.commit()
    assert repo.supersede_open_plans(conn) == 0
    assert repo.plan_status(conn, plan) == status


def test_companion_membership_reports_kind_and_members_not_ids(conn):
    # spec 5.1 rev 6: the scan compares the grouping it just computed against the
    # stored one BY MEMBERSHIP. Group ids say nothing - clear_companion_groups
    # deletes and recreates every group on every scan.
    a = add_file(conn, "a/img.jpg")
    b = add_file(conn, "a/img.cr2", ext="cr2")
    c = add_file(conn, "b/clip.mov", ext="mov", media_type="video")
    d = add_file(conn, "b/clip.jpg")
    assert repo.companion_membership(conn) == set()

    repo.create_companion_group(conn, a, "raw_jpeg", [a, b])
    repo.create_companion_group(conn, d, "live_photo", [c, d])
    conn.commit()
    before = repo.companion_membership(conn)
    assert before == {
        ("raw_jpeg", frozenset({"a/img.jpg", "a/img.cr2"})),
        ("live_photo", frozenset({"b/clip.mov", "b/clip.jpg"})),
    }

    # Recreating the identical grouping under different ids must compare equal;
    # otherwise every scan would look like a regroup and supersede open plans.
    repo.clear_companion_groups(conn)
    repo.create_companion_group(conn, d, "live_photo", [c, d])
    repo.create_companion_group(conn, a, "raw_jpeg", [a, b])
    conn.commit()
    assert repo.companion_membership(conn) == before


def test_companion_membership_notices_a_changed_member_and_a_changed_kind(conn):
    a = add_file(conn, "a/img.jpg")
    b = add_file(conn, "a/img.cr2", ext="cr2")
    x = add_file(conn, "a/img.xmp", ext="xmp", media_type="sidecar")
    repo.create_companion_group(conn, a, "raw_jpeg", [a, b])
    conn.commit()
    pair = repo.companion_membership(conn)

    repo.clear_companion_groups(conn)
    repo.create_companion_group(conn, a, "raw_jpeg", [a, b, x])  # a member joined
    conn.commit()
    assert repo.companion_membership(conn) != pair

    repo.clear_companion_groups(conn)
    repo.create_companion_group(conn, a, "sidecar", [a, b])  # same members, new kind
    conn.commit()
    assert repo.companion_membership(conn) != pair


def test_no_reexported_symbol_shadows_a_submodule():
    """Architectural invariant (M4 finding 13): a name re-exported from
    catalog/repo/__init__.py must not collide with a submodule of the package.
    The re-export wins, so `import pam.catalog.repo.NAME as m` then silently binds
    the re-exported object instead of the module.

    KNOWN EXCEPTION - `plan_items`. The M5 plan prescribes both the module name
    (`repo/plan_items.py`, Task 2 Step 2) and the public function name
    (`repo.plan_items`, frozen since M4), so the split it mandates creates exactly
    one collision. It is pinned here rather than waved through: the exception set
    must never grow without a design ruling, and the assertions below prove this
    instance is currently benign (both objects remain reachable).
    """
    import importlib
    import pkgutil
    import types

    import pam.catalog.repo as repo_pkg

    submodules = {m.name for m in pkgutil.iter_modules(repo_pkg.__path__)}

    # Form-independent: ask the package what each submodule name actually resolves
    # to. Parsing __init__.py for `from pam.catalog.repo.X import Y as Y` lines was
    # blind to a plain `from ... import X`, a parenthesised multi-line import, or an
    # __all__ entry - all of which shadow just the same.
    shadowed = {
        n for n in submodules if not isinstance(getattr(repo_pkg, n, None), types.ModuleType)
    }
    assert shadowed == {"plan_items"}, (
        "a re-exported symbol now shadows a submodule; rename one of them"
    )
    # The known collision is benign only while both objects stay reachable.
    assert callable(repo_pkg.plan_items)
    submodule = importlib.import_module("pam.catalog.repo.plan_items")
    assert submodule.plan_items is repo.plan_items


def _fault_injection_offences(source: str) -> list[str]:
    """Every call to maybe_fault and every import of the fault module, by AST.

    Walking the tree rather than grepping the text, for two reasons. It is exact -
    comments, docstrings and a variable named `maybe_fault_count` are not matches, and
    an aliased `from pam.faults import maybe_fault as _mf` is. And it lets the rule be
    written in plain English where it applies: the text version was only satisfied
    because journal.py's own docstring spelled "maybe_fault" without the parenthesis,
    and a rule you cannot state plainly in prose is the wrong shape.
    """
    import ast

    offences = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "maybe_fault":
                offences.append("calls maybe_fault")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pam.faults" or alias.name.startswith("pam.faults."):
                    offences.append(f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pam.faults" or module.startswith("pam.faults."):
                offences.append(f"imports from {module}")
            elif module == "pam" and any(a.name == "faults" for a in node.names):
                offences.append("imports pam.faults")
    return offences


def test_no_fault_injection_inside_the_repo_layer():
    """Architectural invariant (spec 9.3 rev 7): the repo layer is pure SQL with no
    test affordances. Fault injection lives in M5's RenameTransaction wrapper, which
    CALLS these functions - so a checkpoint here would fire inside a transaction the
    wrapper is responsible for, and the crash windows the fault matrix names would
    stop meaning what they say.
    """
    import pam.catalog.repo as repo_pkg

    for path in sorted(Path(repo_pkg.__file__).parent.glob("*.py")):
        offences = _fault_injection_offences(path.read_text(encoding="utf-8"))
        assert offences == [], f"{path.name}: {offences}"


@pytest.mark.parametrize(
    "planted",
    [
        "from pam.faults import maybe_fault\ndef f():\n    maybe_fault('after_intent')\n",
        "from pam.faults import maybe_fault as _mf\ndef f():\n    _mf('after_intent')\n",
        "import pam.faults\ndef f():\n    pam.faults.maybe_fault('after_intent')\n",
        "import pam.faults as fx\ndef f():\n    fx.maybe_fault('after_intent')\n",
        "from pam import faults\ndef f():\n    faults.maybe_fault('after_intent')\n",
    ],
)
def test_the_fault_injection_guard_catches_every_route_into_the_repo_layer(planted):
    """The guard proven ENFORCING, not merely present - one row per route in.

    The aliased import is the one that matters most: it is the shape a future author
    reaches for when a plain import trips the guard, and it is invisible to a grep for
    `maybe_fault(`.
    """
    assert _fault_injection_offences(planted) != []


def test_the_fault_injection_guard_does_not_fire_on_prose_or_lookalikes():
    """The other direction. A guard that fires on a docstring gets the docstring
    reworded around it, which is exactly how the prose in journal.py ended up unable
    to say `maybe_fault()` out loud.
    """
    benign = (
        '"""No maybe_fault() call may appear here, and pam.faults is never imported."""\n'
        "maybe_fault_count = 0  # not a call\n"
        "# from pam.faults import maybe_fault\n"
        "from pam.errors import PamError\n"
    )

    assert _fault_injection_offences(benign) == []


# --- the cross-table reach guard (M6 review condition 2) ---------------------------


def _repo_sources() -> dict[str, str]:
    """Every module under `pam/catalog/repo/`, as filename -> source."""
    import pam.catalog.repo as repo_pkg

    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(Path(repo_pkg.__file__).parent.glob("*.py"))
    }


def _schema_tables(conn) -> set[str]:
    """The catalog's tables, as of the last migration, parsed by SQLite itself.

    Spec 4 used to call a `schema.sql` the single source of truth. THERE IS NO SUCH FILE
    and there never has been - the schema is the five forward-only migrations under
    `pam/catalog/migrations/`. Substituted deliberately (M7 Task 3), escalated, and
    RESOLVED IN THE PRODUCT'S FAVOUR: spec rev 12 (amendment B2) deletes `schema.sql`
    from the file tree and adopts the migrated database as the schema oracle - i.e. what
    this function does. A checked-in full-DDL copy beside five migrations would be a
    second implementation of the schema in production code, which is the one place this
    project's "keep an independent second implementation" rule inverts.

    Reading `sqlite_master` off a migrated connection rather than regexing the .sql
    files, because SQLite is the only real parser for the language they are written in:
    it applies every migration in order, so a later ALTER, DROP or RENAME is honoured
    for free rather than by a second hand-rolled interpretation of the DDL. This is the
    lesson `tests/test_ci_lanes.py` taught in M6, where a hand-rolled reader answered
    every question asked of a file that did not parse at all.

    Hand-copying the table list into this test would be exactly the vacuity the
    containment pin exists to prevent.
    """
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r["name"].lower() for r in rows}


def test_the_repo_module_map_covers_every_module_on_disk():
    """A guard that walks zero modules passes everything.

    Deleting a module from `REPO_OWN_TABLES` would drop it silently out of the reach
    arithmetic - the module with the new reach being the obvious one to drop. This pins
    the map against the directory in both directions.
    """
    on_disk = sorted(_repo_sources())
    mapped = sorted([*REPO_OWN_TABLES, *REPO_REACH_EXEMPT])
    assert mapped == on_disk


def test_the_reach_exemption_is_exactly_the_three_documented_modules():
    """The other half of the disarm surface (M7 Task 3 review, mutation M564).

    `REPO_REACH_EXEMPT` takes a whole module out of the reach arithmetic, and a module
    listed there can join three tables in one query without the guard saying a word.
    Measured: a planted `search.py` doing `files JOIN capture`, re-exported from the
    package front door and named here, passed all 1271 tests.

    So the exception set is spelled out, following the precedent
    `test_no_reexported_symbol_shadows_a_submodule` sets a few hundred lines above -
    the exception set must never grow without a design ruling. The blessing itself is
    still a one-word edit in `guards.py`; what this adds is that the edit cannot be
    made ALONE, which is the same bargain `ALLOWED_REPO_REACHES` already strikes.
    """
    assert REPO_REACH_EXEMPT == ("integrity.py", "reports.py", "review.py"), (
        "a module was exempted from the cross-table reach guard. An exemption is a "
        "blanket one - it hides every reach in that module forever - so it needs the "
        "reasoning written into pam/catalog/repo/__init__.py's docstring (the "
        "'NOT keyed to a table' paragraph) and a deliberate edit here. If the module "
        "is keyed to a TABLE rather than to a consumer, it belongs in REPO_OWN_TABLES "
        "with its reaches blessed one by one instead"
    )


def test_every_table_the_repo_layer_names_is_a_real_table(conn):
    """Containment pin for the extractor: a tripwire needs a reach pin of its own.

    An extractor that invents or mangles names would let the reach pin pass on nonsense
    - and this fired for real while the guard was being written, on `DO UPDATE SET`,
    which the first regex read as a table called `set`.

    Run over ALL ten modules, `integrity.py` and `reports.py` included: this is a claim
    about the extractor's accuracy, not about which reaches are blessed.
    """
    known = _schema_tables(conn)
    # Sanity: the connection really is migrated, so `known` is not vacuously small.
    assert {"files", "plans", "journal", "schema_migrations"} <= known

    for name, source in _repo_sources().items():
        for table in sorted(sql_table_names(source)):
            assert table in known or table.startswith("sqlite_"), (
                f"{name}: SQL names `{table}`, which is not a table in the schema - "
                "either the extractor mangled a name or the SQL has a typo"
            )


def test_no_module_may_pre_bless_a_table_it_does_not_own(conn):
    """Pin on the guard's own disarm surface (M7 Task 3 review, mutation M561).

    The reach pin refuses to be widened: it compares for equality, so adding a pair to
    `ALLOWED_REPO_REACHES` that is not really there is a red test. But `REPO_OWN_TABLES`
    is a subtraction, and nothing asserted anything about its VALUES - so declaring
    `files.py` to own `journal` and `plans` passed all 1270 tests while quietly
    pre-blessing two reaches that do not exist yet. Measured, not feared.

    Three assertions, none of them derived from `REPO_OWN_TABLES` itself:

    1. every declared own table is a real table, with `sqlite_master` as the oracle;
    2. the own-table sets are pairwise DISJOINT - one module per table is the package's
       whole organising rule, so two modules owning `files` is a contradiction;
    3. every declared own table is ACTUALLY NAMED by its own module's SQL.

    The direction of 3 is the load-bearing part. `own` is asserted to be a subset of
    what the module names; `named` is NOT asserted to be a subset of `own`. The second
    direction is the vacuity this project keeps re-learning (`repo.open_plan` in M5,
    `repo.ENRICHERS` in M6) and is precisely what the reach pin measures. Asserting only
    the first direction closes the escape hatch without turning the guard into a
    tautology.
    """
    known = _schema_tables(conn)
    assert {"files", "plans", "journal"} <= known  # the fixture really is migrated

    # Declared but not yet queried anywhere in production. Written out here rather than
    # imported, so growing the exemption is a visible edit in the test that permits it.
    not_yet_queried = {"enrichment_runs"}

    sources = _repo_sources()
    seen: dict[str, str] = {}
    for module, owned in REPO_OWN_TABLES.items():
        for table in sorted(owned):
            assert table in known, (
                f"{module} claims to own `{table}`, which is not a table in the schema"
            )
            assert table not in seen, (
                f"{module} and {seen[table]} both claim to own `{table}` - one module "
                "per table, or the reach arithmetic subtracts the same table twice"
            )
            seen[table] = module
            if table in not_yet_queried:
                continue
            assert table in sql_table_names(sources[module]), (
                f"{module} claims to own `{table}` but names it in no SQL. Widening a "
                "module's own-table set is how the reach pin gets disarmed silently; "
                "if this table is genuinely owned here but unqueried until a later "
                "milestone, add it to `not_yet_queried` above and say why"
            )


def test_the_extractor_sees_the_sql_that_is_actually_there():
    """Coverage pin. Break the regex so it returns nothing and the reach pin above goes
    green on an empty set - a guard that sees no SQL at all must not pass.

    Spelled out from reading each module's queries, deliberately NOT from the extractor
    and NOT from `REPO_OWN_TABLES` (which `enrichment.py` would fail: it owns
    `enrichment_runs` and queries it nowhere until M9).
    """
    definitely_named = {
        "capture.py": "capture",
        "companions.py": "companion_groups",
        "enrichment.py": "inferred_facts",
        "files.py": "files",
        "integrity.py": "journal",
        "journal.py": "journal",
        "plan_items.py": "plan_items",
        "plans.py": "plans",
        "reports.py": "files",
    }
    sources = _repo_sources()
    for name, table in definitely_named.items():
        assert table in sql_table_names(sources[name]), f"{name}: extractor saw no `{table}`"

    # And the one module that holds no SQL, which is why its own-table set is empty
    # rather than an exemption.
    assert sql_table_names(sources["__init__.py"]) == set()


def test_the_catalog_cross_table_reaches_are_exactly_the_documented_ones():
    """The inventory in `repo/__init__.py`'s docstring, enforced instead of promised.

    It drifted twice (1 reach -> 3 -> 4) while it was only prose. Five pairs for four
    documented reaches: reach 1, `plans.files_for_planning`, names two foreign tables.
    """
    reaches = repo_cross_table_reaches(_repo_sources())
    assert reaches == sorted(ALLOWED_REPO_REACHES), (
        "the cross-table reaches changed. If the new one is deliberate, document the "
        "reasoning in pam/catalog/repo/__init__.py and add it to ALLOWED_REPO_REACHES; "
        "do not widen the list to make this green"
    )


# The guard proven ENFORCING on planted source, not merely present. Each case drives
# `repo_cross_table_reaches` itself, so the reach arithmetic is under test and not only
# the extractor.
@pytest.mark.parametrize(
    ("planted", "expected"),
    [
        # The reviewer's mutation 1, as a test: a foreign SELECT smuggled into the one
        # module that legitimately names no other table.
        (
            'def f(conn):\n    conn.execute("SELECT id FROM files")\n',
            [("capture.py", "files")],
        ),
        # The other four verbs, since the regex names them separately.
        (
            'def f(conn):\n    conn.execute("INSERT INTO plans (id) VALUES (1)")\n',
            [("capture.py", "plans")],
        ),
        (
            'def f(conn):\n    conn.execute("UPDATE journal SET state = 1")\n',
            [("capture.py", "journal")],
        ),
        (
            'def f(conn):\n    conn.execute("DELETE FROM plan_items WHERE id = 1")\n',
            [("capture.py", "plan_items")],
        ),
        (
            'def f(conn):\n    conn.execute("SELECT 1 FROM capture c JOIN files f ON f.id = c.file_id")\n',
            [("capture.py", "files")],
        ),
        # Split across implicit string concatenation, which is how every multi-line
        # query in this package is actually written.
        (
            'def f(conn):\n    conn.execute("SELECT 1 FROM capture c"\n        " JOIN face_instances a ON 1")\n',
            [("capture.py", "face_instances")],
        ),
    ],
)
def test_the_reach_guard_catches_a_planted_foreign_table(planted, expected):
    assert repo_cross_table_reaches({"capture.py": planted}) == expected


def test_the_reach_guard_does_not_fire_on_prose_that_names_other_tables():
    """The other direction, and the reason docstrings are skipped structurally.

    Every module in this package discusses the tables it joins to - `journal.py`'s
    docstring says "reads `journal` JOIN `plan_items`" out loud. A guard that fires on
    that gets the docstring reworded around it, which is precisely how `journal.py`'s
    prose ended up unable to say `maybe_fault()` with its parenthesis.
    """
    benign = (
        '"""This module reads from files and writes INTO plans, or would if it did."""\n'
        "\n"
        "def f(conn):\n"
        '    """Selects FROM journal JOIN plan_items, in an earlier draft."""\n'
        '    conn.execute("SELECT id FROM capture")\n'
    )
    assert repo_cross_table_reaches({"capture.py": benign}) == []


def test_no_sql_outside_repo_and_migration_runner():
    """Architectural invariant: conn.execute* lives only in catalog/db.py and the
    catalog/repo/ package. The scan runner, the planner, and every later milestone
    go through repo.
    """
    import pam

    pam_dir = Path(pam.__file__).parent
    tokens = (".execute(", ".executemany(", ".executescript(")
    offenders = [
        rel
        for p in pam_dir.rglob("*.py")
        if (rel := p.relative_to(pam_dir).as_posix()) != "catalog/db.py"
        and not rel.startswith("catalog/repo/")
        and any(t in p.read_text(encoding="utf-8") for t in tokens)
    ]
    assert offenders == []

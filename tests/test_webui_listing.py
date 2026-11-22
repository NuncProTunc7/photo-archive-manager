"""M14 Task 2: the review list's groups (design 8.2, U25) and each row's note code (U15).

Every expectation below is SPELLED FROM THE SPEC - U25's three-step rule, U15's code
vocabulary, U2's group fields - and never read back from `webui/listing.py`'s constants or
from a production call (an oracle built from the definition it checks; the ENRICHERS
lesson). Pure functions are driven with hand-built `ReviewItem`s; the routes with a real
scanned archive and a real `ReviewServer` whose handlers are called directly.
"""

import dataclasses
import logging
from typing import Any

import pytest
from archives import FAKE_COMMAND, companion_archive, put

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import api, listing, nav
from pam.webui.api import ApiError, ApiRequest
from pam.webui.server import ReviewServer


def item(
    item_id: int,
    *,
    unit: str | None = None,
    date: str | None = "2019-07-04",
    event_id: int | None = None,
    event_label: str | None = None,
    status: str = "proposed",
    tier: str = "high",
    date_token: dict[str, Any] | None = None,
    reasons: list[str] | None = None,
    extra_tokens: list[dict[str, Any]] | None = None,
) -> ReviewItem:
    """A hand-built item. `date_token` replaces the default date token's provenance."""
    token: dict[str, Any] = {
        "resolved_from": "date",
        "value": date if date is not None else "undated",
        "source_table": "capture",
        "source_id": item_id,
        "truncated": False,
    }
    if date_token is not None:
        token |= date_token
    return ReviewItem(
        id=item_id,
        file_id=item_id,
        old_rel_path=f"{item_id}.jpg",
        rel_dir="",
        old_filename=f"{item_id}.jpg",
        unit_key=unit or f"\x1f{item_id}.jpg",
        generated_filename=f"{item_id}.jpg",
        new_filename=f"{item_id}.jpg",
        tier=tier,
        trust_score=100,
        status=status,
        tokens=[token, *(extra_tokens or [])],
        trust_reasons=reasons or [],
        filename=f"{item_id}.jpg",
        ext="jpg",
        media_type="image",
        placeholder=False,
        has_sha256=True,
        orientation=None,
        event_id=event_id,
        event_label=event_label,
    )


# --- U25: the group is the UNIT's ------------------------------------------------------


def test_a_unit_takes_the_event_of_its_lowest_id_member_that_has_one():
    """U25 step 1, over members: the sidecar (id 1, no event) and the photo (id 2, event 7)
    are one unit, and both are listed under `e7` - not the sidecar's day."""
    unit = "\x1fA.HEIC"
    rows = [
        item(1, unit=unit, event_id=None),
        item(2, unit=unit, event_id=7),
        item(3, unit=unit, event_id=9),
    ]
    keys = listing.unit_keys(rows)
    assert keys == {unit: "e7"}
    # "lowest-id" is the rule, not the order the rows arrive in (the R1 review, M2816):
    # `repo.plan_rows` happens to order by id, and a pure function must not lean on it.
    assert listing.unit_keys(list(reversed(rows))) == {unit: "e7"}
    assert all(listing.in_list_group(row, "e7", keys) for row in rows)


def test_a_unit_with_no_event_takes_its_date_tokens_day_and_else_undated():
    keys = listing.unit_keys([item(1, date="2019-07-04"), item(2, date=None)])
    assert keys == {"\x1f1.jpg": "2019-07-04", "\x1f2.jpg": "undated"}


def test_a_two_month_event_is_ONE_list_group():
    """U25: "the whole event even when it spans two months" (mutation 2: split by month)."""
    rows = [item(1, date="2019-07-31", event_id=4), item(2, date="2019-08-01", event_id=4)]
    groups = listing.list_groups(rows)
    assert [(g.key, g.item_ids, g.start, g.end) for g in groups] == [
        ("e4", (1, 2), "2019-07-31", "2019-08-01")
    ]


def test_groups_are_in_date_order_with_undated_last_and_count_on_the_server():
    rows = [
        item(1, date=None),
        item(2, date="2020-01-02", status="approved"),
        item(3, date="2019-05-05", tier="low", event_id=8),
        item(4, date="2019-05-06", event_id=8, status="skipped"),
        item(5, date="2019-05-06", event_id=8, tier="low"),
    ]
    groups = listing.list_groups(rows)
    assert [g.key for g in groups] == ["e8", "2020-01-02", "undated"]
    e8 = groups[0]
    assert (e8.total, e8.undecided, e8.attention_undecided, e8.all_low) == (3, 2, 2, False)
    assert (groups[1].undecided, groups[2].attention_undecided) == (0, 1)
    assert listing.list_groups([item(1, tier="low")])[0].all_low is True
    # Two groups starting on one day: the tie is broken by KEY, as the docstring says, and
    # not by which group's first member has the lower id (the R1 review, M2819).
    tied = listing.list_groups([item(1, event_id=9), item(2)])
    assert [g.key for g in tied] == ["2019-07-04", "e9"]


# U25's three forms, each in its one spelling, and the forms that are not it. Named lists
# asserted non-empty at collection: pytest SKIPS an empty parametrize rather than failing it
# (M743's shape), so emptying either list would otherwise be green (the R1 review, M2853/M2854).
LIST_GROUP_KEYS_ACCEPTED = ["e1", "e12", "2019-07-14", "2020-02-29", "undated"]
LIST_GROUP_KEYS_REFUSED = [
    "e",
    "e012",
    "e1.5",
    "e²",
    "E1",
    "2019-07",
    "2019-7-14",
    "2019-02-30",
    "2019-07-14x",
    "２０１９-07-14",
    "Undated",
    "2019-07.e12",
    "",
]
assert len(LIST_GROUP_KEYS_ACCEPTED) == 5 and len(LIST_GROUP_KEYS_REFUSED) == 13


@pytest.mark.parametrize("key", LIST_GROUP_KEYS_ACCEPTED)
def test_a_list_group_key_in_its_one_spelling_is_accepted(key):
    assert listing.is_list_group_key(key)


@pytest.mark.parametrize("key", LIST_GROUP_KEYS_REFUSED)
def test_any_other_list_group_form_is_refused(key):
    assert not listing.is_list_group_key(key)


# --- U15: the note code, from structure -----------------------------------------------

# U15's vocabulary, spelled from the amendment: `date_source: file_mtime | ocr | none |
# utc_video`, and `truncated`.
U15_CODES = ("file_mtime", "ocr", "none", "utc_video", "truncated")
# (description, item, the capture sources the page read, the code U15 assigns)
NOTE_CASES = [
    ("camera date", item(1), {1: "exif"}, None),
    ("date from the file's modification time", item(1), {1: "file_mtime"}, "file_mtime"),
    (
        "a files-table date is a modification time",
        item(1, date_token={"source_table": "files"}),
        {},
        "file_mtime",
    ),
    (
        "date read off the picture",
        item(1, date_token={"source_table": "inferred_facts"}),
        {},
        "ocr",
    ),
    ("no date found", item(1, date=None), {}, "none"),
    ("a video's UTC date", item(1), {1: "qt_utc"}, "utc_video"),
    (
        "the date was shortened",
        item(1, date_token={"truncated": True}),
        {1: "exif"},
        "truncated",
    ),
    (
        "another token was shortened",
        item(1, extra_tokens=[{"resolved_from": "place", "value": "Pl", "truncated": True}]),
        {1: "exif"},
        "truncated",
    ),
    # The R1 review (M2832): the path budget can cut the date token to a FRAGMENT
    # (`naming/template.py`: order ("date", "seq") at budget 9 gives `2019-07-0`), and then
    # the item has no date at all. Its sentence is the shortening, never "No date found".
    (
        "a date the budget cut to a fragment",
        item(1, date="2019-07-0", date_token={"truncated": True}),
        {1: "exif"},
        "truncated",
    ),
    # The R1 review (M2839): one sentence per row, so a date that needs checking outranks
    # a shortened OTHER token - the shipped precedence, pinned (the date is the fact the
    # user must verify; the shortening is visible in the name itself).
    (
        "a modification-time date beside a shortened token",
        item(1, extra_tokens=[{"resolved_from": "place", "value": "Pl", "truncated": True}]),
        {1: "file_mtime"},
        "file_mtime",
    ),
]
assert NOTE_CASES and len({case[3] for case in NOTE_CASES} - {None}) == len(U15_CODES)


@pytest.mark.parametrize(
    ("what", "row", "sources", "code"), NOTE_CASES, ids=[c[0] for c in NOTE_CASES]
)
def test_the_note_code_is_read_off_the_date_tokens_structure(what, row, sources, code):
    assert listing.note_code(row, sources) == code


def test_the_note_code_never_reads_a_reasons_text():
    """Mutation 7: a code chosen by matching copy. Reword every reason - the code stays;
    put the file_mtime SENTENCE on a camera-dated item - the code does not follow it."""
    mtime = "Date read from the file's modification time - no camera date present."
    worded = item(1, reasons=[mtime])
    reworded = item(1, reasons=["The clock on the file said so."])
    assert listing.note_code(worded, {1: "file_mtime"}) == "file_mtime"
    assert listing.note_code(reworded, {1: "file_mtime"}) == "file_mtime"
    assert listing.note_code(worded, {1: "exif"}) is None


# --- the routes ------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path):
    """An archive of real files - one HEIC+AAE unit and two lone photos - and a server not listening:
    its handlers are called directly, with the server's own connection factory."""
    root, plan_id = companion_archive(tmp_path, ("IMG_1.HEIC", "IMG_1.AAE"))
    put(root, "IMG_2.JPG", date="2020:08:05 09:15:00")
    put(root, "IMG_3.JPG", date="2020:08:05 10:15:00")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.webui_listing")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=summary.plan_id, logger=logger)
    yield server, summary.plan_id
    server.server_close()


def call(handler, server, plan_id, **query: str):
    return handler(
        server,
        ApiRequest(params={"id": str(plan_id)}, query={k: [v] for k, v in query.items()}),
    )


def test_a_sidecar_with_no_capture_row_is_listed_with_its_photo(served):
    """T2 mutation 1: the HEIC has an event, its `.AAE` has no `capture` row and so no
    event. One unit, so ONE list group - the photo's event - and the group's page holds
    both files."""
    server, plan_id = served
    with server.connection() as conn:
        rows = repo.plan_rows(conn, plan_id, floor=api.faces_floor(server))
    by_name = {row.old_filename: row for row in rows}
    heic, aae = by_name["IMG_1.HEIC"], by_name["IMG_1.AAE"]
    assert heic.unit_key == aae.unit_key, "precondition: one unit"
    assert heic.event_id is not None and aae.event_id is None, (
        "precondition: only the photo has an event"
    )
    event_key = f"e{heic.event_id}"
    groups = call(nav.plan_list, server, plan_id)["groups"]
    assert event_key in [g["key"] for g in groups]
    page = call(api.plan_items, server, plan_id, list_group=event_key)
    assert {card["old_filename"] for card in page["items"]} >= {"IMG_1.HEIC", "IMG_1.AAE"}
    assert page["total"] == sum(g["total"] for g in groups if g["key"] == event_key)


def test_the_list_route_spells_u25s_fields_and_its_counts_add_up(served):
    server, plan_id = served
    reply = call(nav.plan_list, server, plan_id)
    assert reply["plan_id"] == plan_id
    for group in reply["groups"]:
        # U25's fields, spelled; `shortcut_count` joined with M14 Task 3 (OD-1).
        assert set(group) == {
            "key",
            "title",
            "date_line",
            "total",
            "undecided",
            "attention_undecided",
            "shortcut_count",
            "all_low",
        }
    with server.connection() as conn:
        plan = repo.plan_rows(conn, plan_id, floor=api.faces_floor(server))
    assert sum(g["total"] for g in reply["groups"]) == len(plan)
    assert sum(g["undecided"] for g in reply["groups"]) == sum(
        1 for r in plan if r.status == "proposed"
    )


def test_paging_a_list_group_returns_every_id_exactly_once(served, monkeypatch):
    """T2 mutation 3 (`offset + limit - 1`): page a group of `limit*2+1` rows by `limit`."""
    server, plan_id = served
    with server.connection() as conn:
        plan = repo.plan_rows(conn, plan_id, floor=api.faces_floor(server))
    # Five hand-made members of ONE unit, so the group is `limit*2+1` rows at limit 2.
    unit = plan[0].unit_key
    fake = [dataclasses.replace(plan[0], id=100 + n, unit_key=unit) for n in range(5)]
    monkeypatch.setattr(repo, "plan_rows", lambda conn, pid, floor: fake)
    key = listing.unit_keys(fake)[unit]
    seen: list[int] = []
    for offset in range(0, 6, 2):
        page = call(
            api.plan_items, server, plan_id, list_group=key, offset=str(offset), limit="2"
        )
        assert page["total"] == 5
        seen += [card["id"] for card in page["items"]]
    assert sorted(seen) == [100, 101, 102, 103, 104] and len(seen) == 5


@pytest.mark.parametrize("bad", ["2019-07", "e012", "nonsense", "2019-02-30"])
def test_a_malformed_list_group_is_a_400_naming_it(served, bad):
    server, plan_id = served
    with pytest.raises(ApiError) as refused:
        call(api.plan_items, server, plan_id, list_group=bad)
    assert refused.value.status == 400
    assert str(refused.value.message).startswith("list_group:")


def test_every_card_carries_a_note_code_from_u15s_vocabulary(served):
    server, plan_id = served
    cards = call(api.plan_items, server, plan_id)["items"]
    assert cards and all("note_code" in card for card in cards)
    assert {card["note_code"] for card in cards} <= {None, *U15_CODES}


@pytest.mark.parametrize(
    ("start", "end", "line"),
    [
        ("2019-07-12", "2019-07-12", "July 12, 2019"),
        ("2019-07-12", "2019-07-14", "July 12–14, 2019"),
        ("2019-07-30", "2019-08-02", "July 30 – August 2, 2019"),
        ("2019-12-30", "2020-01-02", "December 30, 2019 – January 2, 2020"),
    ],
)
def test_a_groups_date_line_is_spelled_in_english_with_the_en_dash(start, end, line):
    assert nav.date_span(start, end) == line


# --- W9: the unit disclosure's kind ---------------------------------------------------

# The scanner's three group kinds, SPELLED from `pam/scan/companions.py`'s contract (its
# `kind: str  # 'raw_jpeg' | 'live_photo' | 'sidecar'`), never imported from it. A real
# archive with one unit of each, plus a lone file whose row discloses nothing.
W9_UNITS = {
    "IMG_1.HEIC": "sidecar",
    "IMG_1.AAE": "sidecar",
    "DSC_4.NEF": "raw_jpeg",
    "DSC_4.JPG": "raw_jpeg",
    "IMG_5.JPG": "live_photo",
    "IMG_5.MOV": "live_photo",
    "IMG_6.JPG": None,
}
assert set(W9_UNITS.values()) == {"sidecar", "raw_jpeg", "live_photo", None}


@pytest.fixture
def kinded(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for name in W9_UNITS:
        put(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.webui_listing.w9")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=summary.plan_id, logger=logger)
    yield server, summary.plan_id
    server.server_close()


def test_every_card_carries_its_units_companion_kind_from_the_catalog(kinded):
    """W9: `unit_kind` is the scanner's group kind for each of the three kinds, and None
    for a lone file - read from the catalog, so the page never guesses it from an
    extension."""
    server, plan_id = kinded
    cards = call(api.plan_items, server, plan_id, limit="200")["items"]
    assert {c["old_filename"]: c["unit_kind"] for c in cards} == W9_UNITS
    sizes = {c["old_filename"]: c["unit_size"] for c in cards}
    assert sizes["IMG_6.JPG"] == 1 and sizes["IMG_5.MOV"] == 2


def test_a_page_holding_one_member_still_carries_the_units_kind(kinded):
    """Taken over the WHOLE plan, like `unit_size`: a filter that keeps only the video of a
    Live Photo must not make its row read as a lone file."""
    server, plan_id = kinded
    page = call(api.plan_items, server, plan_id, q=".MOV")["items"]
    assert [(c["old_filename"], c["unit_kind"]) for c in page] == [("IMG_5.MOV", "live_photo")]


def test_a_units_kind_is_its_lowest_id_grouped_members_and_never_its_extensions():
    """The rule over members, by hand: member 1 has no group in the map, member 2's group
    is `raw_jpeg` - the unit reads `raw_jpeg` for both, whatever the files are called. A
    unit with no grouped member reads None."""
    unit = "\x1fpair"
    rows = [item(1, unit=unit), item(2, unit=unit), item(3)]
    assert listing.unit_kinds(rows, {2: "raw_jpeg"}) == {unit: "raw_jpeg", "\x1f3.jpg": None}
    assert listing.unit_kinds(rows, {1: "live_photo", 2: "raw_jpeg"})[unit] == "live_photo"
    assert listing.unit_kinds(rows, {}) == {unit: None, "\x1f3.jpg": None}


# --- the R1 review's closures (the routes' own wiring) --------------------------------


def test_a_cards_note_code_follows_its_date_token_to_the_capture_row(tmp_path):
    """M2840-M2842: the route must READ the capture rows its page's date tokens point at.
    A photo with a camera date reads no note; one with no date tag is dated from its
    modification time and reads `file_mtime`. Every hand-built case above passes `sources`
    in by hand, so without this nothing proves `plan_items` passes the catalog's."""
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "CAMERA.JPG")
    put(root, "NOTAG.JPG", date=None)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.webui_listing.r1")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=summary.plan_id, logger=logger)
    try:
        with server.connection() as conn:
            stored = dict(
                conn.execute(
                    "SELECT f.filename, c.capture_dt_source FROM capture c"
                    " JOIN files f ON f.id = c.file_id"
                ).fetchall()
            )
        assert stored == {"CAMERA.JPG": "exif", "NOTAG.JPG": "file_mtime"}, "precondition"
        cards = call(api.plan_items, server, summary.plan_id)["items"]
        assert {c["old_filename"]: c["note_code"] for c in cards} == {
            "CAMERA.JPG": None,
            "NOTAG.JPG": "file_mtime",
        }
    finally:
        server.server_close()


def test_the_list_routes_groups_are_spelled_field_by_field(served, monkeypatch):
    """M2821, M2857-M2860: every field of every group, spelled by hand from U25 and UX 5
    over a plan with a decided, a skipped and an undated item - so `undecided` is not
    `total`, an APPROVED low item is not `attention_undecided`, and the undated group's
    title is UX 5's `No date found`."""
    server, plan_id = served
    rows = [
        item(1, date=None, tier="low"),
        item(
            2,
            date="2019-07-12",
            event_id=3,
            event_label="Place A",
            tier="low",
            status="approved",
        ),
        item(3, date="2019-07-14", event_id=3, event_label="Place A"),
        item(4, date="2020-01-02", tier="low", status="skipped"),
    ]
    monkeypatch.setattr(repo, "plan_rows", lambda conn, pid, floor: rows)
    assert call(nav.plan_list, server, plan_id)["groups"] == [
        {
            "key": "e3",
            "title": "Place A",
            "date_line": "July 12\u201314, 2019",
            "total": 2,
            "undecided": 1,
            "attention_undecided": 0,
            "shortcut_count": 1,
            "all_low": False,
        },
        {
            "key": "2020-01-02",
            "title": "January 2, 2020",
            "date_line": "January 2, 2020",
            "total": 1,
            "undecided": 0,
            "attention_undecided": 0,
            "shortcut_count": 0,
            "all_low": True,
        },
        {
            "key": "undated",
            "title": "No date found",
            "date_line": "",
            "total": 1,
            "undecided": 1,
            "attention_undecided": 1,
            "shortcut_count": 0,
            "all_low": True,
        },
    ]


def test_an_empty_list_group_is_no_filter_like_every_other_items_filter(served):
    """M2828: `list_group=` with no value is the absent filter, as `group=`, `tier=` and
    the rest are (Step 1a's positive control sends them all empty) - not a 400."""
    server, plan_id = served
    whole = call(api.plan_items, server, plan_id)
    empty = call(api.plan_items, server, plan_id, list_group="")
    assert whole["total"] > 0 and empty["total"] == whole["total"]

"""The review list's groups (design 8.2 rev 34, U25) and each row's note code (U15). Pure.

A LIST GROUP IS A UNIT'S GROUP, NEVER A MEMBER'S. Event membership is not unit-uniform: a
sidecar or a cloud placeholder has no `capture` row, so `pam plan`'s step 0 puts it in no
event while its photo is in one (M8's measurement, and the route `group=YYYY-MM.eN` split
units through exactly that). So the key is computed once per `unit_key` and copied to every
member, the way the planner copies tier and trust: the event of the LOWEST-ID member that
has one; else the day of the unit's date token; else `undated`. The date token is uniform
across a unit because the planner writes one `tokens_json` per unit to every member
(`naming/planner.py::_plan_unit`), and nothing after it rewrites that column. A group's box
can then never select part of a unit, and `write_state._widen_to_units` is the second layer
rather than the only one.

An event is ONE group even when it spans two months: the nav tree's (month, event) pairs
stay the nav tree's (`webui/nav.py`).

The WORDS (a group's title and date line) are `webui/nav.py`'s, beside the month names; this
module answers only keys, dates and counts, so `webui/api.py` can import it for `list_group`
without a cycle. Computed here, from `repo.plan_rows`' items, and not in `catalog/repo/` (`repo/review.py`
is at the ~300 budget and a new repo module would move `REPO_OWN_TABLES`).
"""

import datetime
from dataclasses import dataclass

from pam.catalog.repo.review import DATE_TOKEN, ReviewItem
from pam.naming.accept import Acceptance, accept_all

UNDATED_KEY = "undated"
EVENT_PREFIX = "e"

# U15's reason codes, the vocabulary `note_code` answers in. The page maps each to W10's
# sentence; nothing here or there reads a reason's TEXT.
NOTE_NONE = "none"
NOTE_OCR = "ocr"
NOTE_FILE_MTIME = "file_mtime"
NOTE_UTC_VIDEO = "utc_video"
NOTE_TRUNCATED = "truncated"


@dataclass(frozen=True)
class ListGroup:
    """One group of the list: an event, a day, or `undated` - and the units in it."""

    key: str
    label: str | None  # the event's label; None for a day, `undated`, or an unlabelled event
    start: str | None  # the earliest and latest date token among the group's items
    end: str | None
    item_ids: tuple[int, ...]
    total: int
    undecided: int
    attention_undecided: int
    all_low: bool


def unit_keys(items: list[ReviewItem]) -> dict[str, str]:
    """`unit_key` -> its list group's key, by U25's rule (see the module docstring)."""
    members: dict[str, list[ReviewItem]] = {}
    for item in sorted(items, key=lambda i: i.id):
        members.setdefault(item.unit_key, []).append(item)
    keys: dict[str, str] = {}
    for unit, rows in members.items():
        evented = next((row for row in rows if row.event_id is not None), None)
        if evented is not None:
            keys[unit] = f"{EVENT_PREFIX}{evented.event_id}"
        elif rows[0].date is not None:
            keys[unit] = rows[0].date
        else:
            keys[unit] = UNDATED_KEY
    return keys


def is_list_group_key(text: str) -> bool:
    """`e12`, `2019-07-14` or `undated`, each in its ONE spelling - U25's three forms.

    `isascii() and isdecimal()`, never `isdigit()` alone: `'²'.isdigit()` is True and
    `int('²')` raises (the trap `api._bounded_int` records). A leading zero is refused, so
    `e012` cannot validate and then select nothing (UX 1.4's dead end); a day must be a real
    calendar day for the same reason.
    """
    if text == UNDATED_KEY:
        return True
    if text.startswith(EVENT_PREFIX):
        digits = text[len(EVENT_PREFIX) :]
        return digits.isascii() and digits.isdecimal() and str(int(digits)) == digits
    parts = text.split("-")
    if len(text) != 10 or [len(p) for p in parts] != [4, 2, 2]:
        return False
    if not all(p.isascii() and p.isdecimal() for p in parts):
        return False
    try:
        datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return False
    return True


def in_list_group(item: ReviewItem, key: str, keys: dict[str, str]) -> bool:
    """Is this item's UNIT in the list group `key`? `keys` is `unit_keys` over the plan."""
    return keys.get(item.unit_key) == key


def shortcut(items: list[ReviewItem], chosen: list[ReviewItem]) -> Acceptance:
    """M14 Task 3 (OD-1): what a one-tap shortcut approves - a group's `Approve N` and the top
    `Approve the other N`. ONE function for the count `/list` shows AND the write
    `decide-filter`'s shortcut branch makes, so the two agree by construction.

    WHOLE UNITS OF THE WHOLE PLAN: the candidates are EVERY member, from `items` (the whole
    plan), of each unit that has an item in `chosen` (the view, intersected with the list
    group). A `q` can select one member of a unit, and `accept_all` over a partial unit would
    approve that member alone (vision S3). `accept_all` then takes or holds each unit whole,
    holding Low (the one Low rule) and, through `also_hold`, the rest of the needs-a-look set.
    """
    units = {item.unit_key for item in chosen}
    return accept_all(
        [i for i in items if i.unit_key in units], also_hold=lambda i: i.attention
    )


def list_groups(
    items: list[ReviewItem], chosen: list[ReviewItem] | None = None
) -> list[ListGroup]:
    """The list's groups in date order, `undated` last; ties broken by key, so the order is
    total. Every count is computed here, server-side (U25).

    `chosen` is the view (`select_items(items, view)`, M14 Task 3): the KEYS are always the
    whole plan's (a unit's group must not move because a filter hid its evented member), the
    counts are over the view's rows, and a group the view shows no row of is left out. None
    is the unfiltered view."""
    keys = unit_keys(items)
    rows: dict[str, list[ReviewItem]] = {}
    for item in sorted(items if chosen is None else chosen, key=lambda i: i.id):
        rows.setdefault(keys[item.unit_key], []).append(item)
    groups = [_group(key, members) for key, members in rows.items()]
    return sorted(groups, key=lambda g: (g.start is None, g.start or "", g.key))


def _group(key: str, members: list[ReviewItem]) -> ListGroup:
    dates = sorted(item.date for item in members if item.date is not None)
    start, end = (dates[0], dates[-1]) if dates else (None, None)
    label = next((m.event_label for m in members if m.event_label), None)
    undecided = [item for item in members if item.status == "proposed"]
    return ListGroup(
        key=key,
        label=label if key.startswith(EVENT_PREFIX) else None,
        start=start,
        end=end,
        item_ids=tuple(item.id for item in members),
        total=len(members),
        undecided=len(undecided),
        attention_undecided=sum(1 for item in undecided if item.attention),
        all_low=all(item.tier == "low" for item in members),
    )


def _date_token(item: ReviewItem) -> dict[str, object] | None:
    if not isinstance(item.tokens, list):
        return None
    for token in item.tokens:
        if isinstance(token, dict) and token.get("resolved_from") == DATE_TOKEN:
            return token
    return None


def capture_ids(items: list[ReviewItem]) -> list[int]:
    """The `capture` rows the items' date tokens point at - what `note_code` reads."""
    ids = []
    for item in items:
        token = _date_token(item)
        if token is not None and token.get("source_table") == "capture":
            source = token.get("source_id")
            if isinstance(source, int):
                ids.append(source)
    return ids


def note_code(item: ReviewItem, sources: dict[int, str]) -> str | None:
    """U15: the code behind a needs-a-look row's one sentence, from STRUCTURE only.

    Read off the date token's provenance - `source_table`, and the `capture_dt_source` of
    the `capture` row its `source_id` points at (`sources`, read for the page) - and the
    `truncated` flags. Never off `trust_reasons_json`'s sentences: a code chosen by matching
    copy changes when the copy does (UX 5a rev 6's rule against an oracle made of copy).
    """
    token = _date_token(item)
    if token is not None:
        if token.get("truncated"):
            return NOTE_TRUNCATED
        if item.date is None:
            return NOTE_NONE
        table = token.get("source_table")
        if table == "inferred_facts":
            return NOTE_OCR
        if table == "files":
            return NOTE_FILE_MTIME
        source_id = token.get("source_id")
        source = sources.get(source_id) if isinstance(source_id, int) else None
        if table == "capture" and source == "file_mtime":
            return NOTE_FILE_MTIME
        if table == "capture" and source == "qt_utc":
            return NOTE_UTC_VIDEO
    if item.truncated:
        return NOTE_TRUNCATED
    return None


def unit_kinds(items: list[ReviewItem], kinds: dict[int, str]) -> dict[str, str | None]:
    """`unit_key` -> the companion group kind its row discloses (M14 W9), or None.

    `kinds` is `repo.companion_kinds` (`file_id` -> the scanner's group kind). Taken per
    UNIT, the way the list group is, from the lowest-id member the catalog groups: every
    member of a unit shares one group, and a member with no group (none does today) cannot
    make the disclosure disagree between two rows of one unit. The page maps the kind to
    W9's words; it never reads an extension to guess it.
    """
    found: dict[str, str | None] = {}
    for item in sorted(items, key=lambda i: i.id):
        if found.get(item.unit_key) is None:
            found[item.unit_key] = kinds.get(item.file_id)
    return found

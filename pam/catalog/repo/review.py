"""Catalog reads for spec 8.2's review API (M7 Task 8). All SQL lives under catalog/repo/.

NOT KEYED TO A TABLE, and filed under its one consumer for exactly the reason
`integrity.py` and `reports.py` are. Every read here is `plan_items` JOIN `files` LEFT
JOIN `capture` LEFT JOIN `file_events` LEFT JOIN `events` (M8 Task 10 added the last
two), because spec 8.2's item card and its nav are a question no one of those tables can
answer alone: `plan_items` carries the proposed name, the tier, the trust score and the
provenance, while the media type, the on-disk extension case, the placeholder flag and
the EXIF orientation live on `files` and `capture`, and the event the file belongs to
lives across `file_events` and `events`. Filing these under whichever table each names
first would put every one of them where it does not belong and would turn the package's
four documented cross-table reaches into a dozen. `guards.REPO_REACH_EXEMPT` names this
module and the `__init__` docstring carries the reasoning.

THE EVENT IS A LIVE JOIN AND THERE IS NO MIGRATION FOR IT (spec 8.2 rev 14). The
`unit_key` precedent - a plan-time COPY of a grouping - exists because `pam scan`
recomputes companion groups BETWEEN plan and apply, so the plan cannot trust the live
one. Nothing recomputes events except `pam plan`, which supersedes the plan it replaces
(spec 6.3 rule 5), and `pam review` serves `open` plans only
(`webui/__init__.py::_resolve_plan`). Those two sentences together are what make the live
read safe, and `tests/test_webui_events.py` pins the second of them rather than assuming
it, because the whole argument rests on it.

ONE QUERY, THEN PYTHON (M7 plan clarification 7). `plan_rows` reads the whole plan once
and every predicate spec 8.2 asks for - the group, the tier, the status, `attention`, the
search, the two sorts - is applied to that list as an ordinary Python filter. Two of the
predicates can only be answered by parsing `tokens_json` at all, and `reports.py`'s
docstring is emphatic that a predicate expressed half in SQL and half in Python drifts
silently. At v1 scale (<= ~20k items) reading the plan is milliseconds.

THE DATE TOKEN IS DEFINED ONCE, in `date_token_value`, and everything that needs it calls
that. Spec 8.2 builds three separate things on it - the nav tree's grouping key, the
second operand of `attention`, and the `date` sort - and three copies of "which entry in
`tokens_json` is the date, and when does it count as one" is three places for the nav to
stop agreeing with the names on the cards, which is the failure spec 8.2 names by name.

NOTHING HERE RECOMPUTES A NAME, A TIER OR A SCORE (spec 8.2, clarification 6).
`tokens_json` and `trust_reasons_json` are parsed exactly once, here, and carried on the
row as whatever `json.loads` returned. The API serializes that object; it never rebuilds,
reorders or edits a field of it.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from pam.catalog.repo.faces import PersonLabel, people_by_sha256
from pam.catalog.repo.plans import ext_of

# spec 6.1: the entry in `tokens_json` that carries the capture date. `naming/planner.py`
# writes BOTH `token` (the template slot the value filled) and `resolved_from` (the token
# that supplied it), and for the date those are the same word - `{date}` has no alias, in
# the way `{event-or-scene}` does. `resolved_from` is read because it names what produced
# the value rather than where it landed; `tests/test_webui_api.py` pins the two equal on a
# fixture the real planner built, so the choice is a recorded fact and not a guess.
DATE_TOKEN = "date"

# What a date token's value looks like when it IS a date, spelled from spec 6.1 rather
# than from the planner: `naming/tokens.date_value` emits `capture_dt[:10]`, a full ISO
# day and nothing else. Two other things can sit in that slot. `build_stem` substitutes
# the literal `undated` when no date was found, and spec 6.2 rule 5's path budget can
# leave the token holding a fragment (`2019-0`). Neither is a day, so neither can be filed
# under a year and a month, and both are `Undated` here - which is what the card will say.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The nav bucket for every item `date_token_value` cannot date. Spec 8.2 pins it last.
UNDATED = "undated"

# Spec 8.2 rev 14's third nav level, and the `group` form that selects it: a year-month,
# a DOT, an `e`, and the event's id - `2019-07.e12`. The MONTH is part of the key because
# "a nav level in a Year/Month/Event hierarchy is a (month, event) pair": an event that
# spans a month boundary appears under both months, with its count in each, and a key
# that named only the event could not tell the two entries apart.
EVENT_MARK = ".e"

# spec 6.3.4's vocabulary, spelled out here so a bad `?tier=` is a 400 naming the
# parameter rather than a silently empty page.
TIERS = ("high", "medium", "low")

# M10 Task 10 / spec 8.2's degradation table rev 16: which `inferred_facts` KINDS the `q`
# operand searches, each mapped to the `value_json` field its searchable text lives in.
#
# KIND-PARAMETERISED, AND WIRED TO EXACTLY ONE KIND, which is plan clarification 6 in as
# many words. Spec 8.2 rev 16 and UX spec 5 rev 10 move PLACE search to M10 and say why -
# M10 adds a second searchable fact kind to this table, so the operand is worth building
# once. **Neither document promises CAPTION or SCENE search at any milestone**, so
# `caption` is deliberately absent: shipping an operand no normative sentence asks for is
# what the deletion gate exists to remove, and a scene word already reaches this search
# through the proposed filename whenever `{scene}` is templated. M11's people and M12's
# OCR are a line each here, which is the whole of what "built once" buys. (M11 did NOT take
# its line: a person is not a fact kind - see `ReviewItem.people`.)
#
# BOTH HALVES ARE SPELLED FROM THE SPEC, never from `enrichment._FACT_KINDS` or from
# `enrich/validate.py::FACT_SCHEMAS`: spec 6.1's token is `{place}` and spec 6.4's
# write-time rule is written about "`place` tokens", so the kind is `place` and the field
# it carries is `place`. A map read out of another table in this repository would agree
# with that table by construction and prove nothing about either (M8's M922/M924).
SEARCHABLE_FACT_KINDS = {"place": "place"}

# spec 8.2's two orderings. `trust_asc` is the default because UX 5a puts the lowest
# trust first "so attention lands where it pays".
SORT_TRUST_ASC = "trust_asc"
SORT_DATE = "date"
SORTS = (SORT_TRUST_ASC, SORT_DATE)

# The exiftool tag EXIF orientation arrives under. Spec 8.2 says the review UI applies
# orientation client-side "from the orientation the catalog already recorded" - there is
# no `orientation` COLUMN anywhere in migrations 0001-0005 and nothing in `pam/` writes
# one, so the recorded orientation is the one inside `capture.exif_json`, which
# `FROZEN_ARGS_IMAGE`'s unrestricted `-json -n` dump carries for any image that has it.
# AN EXACT KEY LOOKUP on the parsed record, NEVER a substring test on the blob:
# `FileCreateDate`, `FileModifyDate` and `FileAccessDate` are on every record exiftool
# returns, and this project has twice read a bare tag name out of the raw text and
# believed the answer (CLAUDE.md). Escalated at the close: spec 8.2's sentence describes a
# column that does not exist, so either the sentence or the schema owes an amendment.
ORIENTATION_KEY = "Orientation"


@dataclass(frozen=True)
class ReviewItem:
    """One row of spec 8.2's `/plan/{id}/items`, before it is serialized.

    `tokens` and `trust_reasons` are the PARSED provenance, not the stored text: the
    planner's two JSON documents are loaded once here and handed onward untouched
    (clarification 6). `None` on either means the stored text would not parse - see
    `_parsed`.
    """

    id: int
    file_id: int
    old_rel_path: str
    rel_dir: str  # the directory half of `old_rel_path`, '' at the archive root
    old_filename: str  # the name half, frozen at plan time - see `_split_rel_path`
    unit_key: str
    generated_filename: str
    new_filename: str
    tier: str
    trust_score: int
    status: str
    tokens: Any
    trust_reasons: Any
    filename: str  # the name on disk, which is where `ext` gets its case
    ext: str  # spec 5.4 rev 6: the extension exactly as written
    media_type: str
    placeholder: bool
    has_sha256: bool
    orientation: int | None
    # M8 Task 10: the event this item's FILE belongs to right now, read live through
    # `file_events`. `None` for a file `pam plan`'s step 0 never grouped - a sidecar and
    # a cloud placeholder have no `capture` row, and spec 8.3 rev 14 says they "fall out
    # naturally". `event_label` is `None` for every event M8 produces (labels are
    # geocoded, and geocoding is M9), which is why the nav has a date-span branch at all.
    event_id: int | None
    event_label: str | None
    # M10 Task 10: the LIVE values of this file's searchable `inferred_facts`, in
    # `SEARCHABLE_FACT_KINDS` order and then fact-id order. `()` for a file no enricher
    # has covered, which is ordinary rather than an error - the joins that fill this are
    # LEFT joins in spirit and an empty tuple contributes nothing to `_matches_q`.
    facts: tuple[str, ...] = ()
    # M11 Task 12: the people this file's CONTENT shows, read by `faces.people_by_sha256` -
    # EXACTLY the set `{people}` would name, with the same `[tiers] faces` floor (owner
    # ruling 2026-09-17). Not a `SEARCHABLE_FACT_KINDS` entry: a person is not a fact kind,
    # and `inferred_facts` holds no face. `()` for a file nobody was labeled in.
    people: tuple[PersonLabel, ...] = ()

    @property
    def date(self) -> str | None:
        """This item's date token value, or None when it has none worth the name."""
        return date_token_value(self.tokens)

    @property
    def truncated(self) -> bool:
        """Did spec 6.2 rule 5's budget shorten any token that reached the name?

        `truncated` is written per token by the planner (`not token.complete`), which is
        the only place that knows. Read as a truthy test rather than `is True`, because
        the wire value is whatever `json.dumps` wrote for a bool.
        """
        if not isinstance(self.tokens, list):
            return False
        return any(
            isinstance(token, dict) and bool(token.get("truncated")) for token in self.tokens
        )

    @property
    def date_read_from_picture(self) -> bool:
        """Design 8.2 rev 27 (P12): is the date token's `source_table` `inferred_facts`?

        A date pam read off the picture (design 6.1 rev 27: only OCR writes one). Read off
        `self.tokens` as `date` and `truncated` are; a template repeats no token, so there
        is at most one date entry. NOT `!= "capture"`: a `planner`/`files` date token is
        provenance for a primary with no `capture` row, which is not a reading.
        """
        return isinstance(self.tokens, list) and any(
            isinstance(token, dict)
            and token.get("resolved_from") == DATE_TOKEN
            and token.get("source_table") == "inferred_facts"
            for token in self.tokens
        )

    @property
    def attention(self) -> bool:
        """Design 8.2 rev 27 (P12) / UX 5a rev 13: tier `low` OR no date token OR any token
        truncated OR the date token's `source_table` is `inferred_facts`.

        Four operands, none a re-encoding of another, each derivable from `plan_items`
        alone and none matched against copy. The rev-2 definition said "consistency-
        flagged", which is NOT recoverable from `plan_items` (spec 6.4 flags survive only as
        sentences inside `trust_reasons_json`); every consistency rule that exists caps its
        item to `low` or truncates, so the first and third operands catch every flag - a
        claim `tests/test_attention_classes.py` proves per flag class against the real
        planner. The fourth is M12's revisit (owner decision D3): OCR brought no flag of the
        anticipated kind but a DATE SOURCE that neither caps to `low` nor truncates, and a
        `medium` OCR date would otherwise be the one machine-read date a user could
        bulk-approve without being shown it. It HIGHLIGHTS and does not block:
        `Approve High+Medium` still reaches a `medium` OCR-dated item.
        """
        return (
            self.tier == "low"
            or self.date is None
            or self.truncated
            or self.date_read_from_picture
        )

    @property
    def edited(self) -> bool:
        """Spec 8.2 rev 14: did the USER type this name, rather than the planner write it?

        A BYTE comparison, deliberately. Spec 8.2's rename endpoint accepts a stem that
        differs from the planner's only in case (`sanitize` never case-folds, and spec
        5.4 rev 6 keeps the extension byte-for-byte), so `Img.JPG` over `IMG.JPG` IS an
        edit; a case-insensitive test would call it a non-edit and leave the planner's
        provenance attached to a name the planner did not write.

        ONE DEFINITION, TWO CONSUMERS - `api._card` and `write_api._unit_response` both
        read this property. The page needs the predicate twice as well (the `↺` control
        and the trust display) and spec 8.2 rev 14 makes it a response field for exactly
        that reason: two independently written copies of one predicate, in a file no tool
        in this project lints, types or executes, is how the button and the display come
        to disagree.
        """
        return self.new_filename != self.generated_filename

    @property
    def noop(self) -> bool:
        """UX 5 rev 11 / spec 7.0 J12: will this item rename nothing?

        A CASE-INSENSITIVE comparison, unlike `edited` directly above, and the two are
        different questions about the same pair of strings. `edited` asks who wrote the
        name (bytes, because `Img.JPG` over `IMG.JPG` IS an edit). This asks whether the
        file will move, and spec 6.3 rule 3, spec 6.3 rule 2 and `perform_verified_rename`
        all fold case for exactly that: v1.0 does not change a name in case alone
        (spec 8.2 rev 18), so a proposal differing only in capitalisation moves nothing.

        SERVED RATHER THAN DERIVED IN THE PAGE, by the route spec 8.2 rev 14 established
        for `edited`: nothing in this project lints, types or executes `app.js`, so a
        predicate written there cannot be tested and cannot be reached by the mutation
        gate. UX 5 rev 11's "no new key, no new badge, no new filter" is about the card's
        vocabulary for the user - a keyboard key, a badge, a nav filter - not about the
        response fields the page reads.
        """
        return self.new_filename.lower() == self.old_filename.lower()

    @property
    def thumbnailable(self) -> bool:
        """Spec 8.2 `has_thumb`: could a grid thumbnail exist for this file at all?

        The exclusions are spec 8.2's own list, and each is a rule rather than an
        optimisation. Every VIDEO and every SIDECAR and every `other` gets the glyph card
        (v1.0 decodes no video). A file with no sha256 has no cache key. A cloud
        PLACEHOLDER is excluded because reading one HYDRATES it, which is vision R2's
        whole point and the one exclusion that is a safety rule.

        False here means the frontend draws the glyph without spending a request. TRUE
        does not promise an image: whether the file carries an embedded preview at all is
        only knowable by extracting one, and spec 8.2 makes that 404 the frontend's signal
        to draw the glyph anyway. This is the cheap half of the question, honestly named.
        """
        return self.media_type == "image" and self.has_sha256 and not self.placeholder


def date_token_value(tokens: Any) -> str | None:
    """THE definition of "the date token's value" (spec 8.2). Spelled once, called thrice.

    Returns the `YYYY-MM-DD` the proposed name will actually say, or None when the name
    says no date: no date token was emitted (a template without `{date}`, or the budget
    cutting it away entirely), the token holds `build_stem`'s literal `undated`, or the
    budget left it holding a fragment of one.

    A template may not repeat a token (`naming/template.parse_template` refuses it), so
    there is at most one date entry and the first is it. A `tokens` that is not a list is
    treated as carrying no date rather than raising - see `_parsed`.
    """
    if not isinstance(tokens, list):
        return None
    for token in tokens:
        if not isinstance(token, dict) or token.get("resolved_from") != DATE_TOKEN:
            continue
        value = token.get("value")
        if isinstance(value, str) and _ISO_DATE.match(value):
            return value
        return None
    return None


def group_key(item: ReviewItem) -> str:
    """Which nav bucket this item's card belongs in: `YYYY-MM`, or `undated`."""
    date = item.date
    return UNDATED if date is None else date[:7]


def event_key(item: ReviewItem) -> str | None:
    """This item's `YYYY-MM.eN` nav key, or None when it sits under no event entry.

    None means one of two things, and neither has an event level to belong to. The
    item's date token may put it in `Undated`, which is pinned last and carries no
    months, so it carries no events either. Or the FILE may be in no event at all: spec
    8.3 rev 14 groups every present, dated file, so a sidecar and a cloud placeholder -
    neither of which has a `capture` row - are grouped by nothing, while their card can
    still be dated, because a companion takes its date token from its unit's primary.

    Such an item is reached through its MONTH, which counts it. That is why a month's
    count is the sum of its events' counts IN THAT MONTH plus its unevented items, and
    not the sum of its events.
    """
    key = group_key(item)
    if key == UNDATED or item.event_id is None:
        return None
    return f"{key}{EVENT_MARK}{item.event_id}"


@dataclass(frozen=True)
class EventGroup:
    """One (month, event) pair of the nav - spec 8.2 rev 14's third level.

    `start` and `end` are the earliest and latest DATE TOKEN among this pair's items,
    never `events.start_dt`/`end_dt`. Two reasons, and the first is spec 8.2's oldest
    rule about this endpoint: the nav is grouped by what the proposed name will say, or
    it stops agreeing with the cards. The second is arithmetic - an event that spans a
    month boundary has one span, and printing it under both months would put `May 31` in
    June's list. The pair's own span is inside its own month by construction.
    """

    key: str  # `YYYY-MM.eN`
    event_id: int
    label: str | None  # the `events` row's label; None for every event M8 writes
    start: str  # `YYYY-MM-DD`, the earliest date token in this (month, event) pair
    end: str  # `YYYY-MM-DD`, the latest
    count: int


@dataclass(frozen=True)
class MonthGroup:
    key: str  # `YYYY-MM`
    count: int
    events: tuple[EventGroup, ...]


@dataclass(frozen=True)
class YearGroup:
    """A year of the nav tree, or the `Undated` bucket, which carries no months."""

    key: str  # `YYYY`, or `undated`
    count: int
    months: tuple[MonthGroup, ...]


def _span(members: list[ReviewItem]) -> tuple[str, str]:
    """The earliest and latest date token among `members`, which is never empty here.

    The `is not None` filter is honest rather than defensive: `nav_tree` files an item
    under a month only when `group_key` returned one, and that is exactly the items whose
    `date` is not None. Writing `item.date or ""` instead would let a future caller with
    an undated member silently produce a span starting at the empty string.
    """
    dates = sorted(item.date for item in members if item.date is not None)
    return dates[0], dates[-1]


def _events_in(month: str, members: list[ReviewItem]) -> tuple[EventGroup, ...]:
    """One month's event level: a (month, event) pair per event present in THIS month.

    The count is the pair's members, never the event's whole membership - an event that
    spans a month boundary is two entries and each says how many of its photographs fall
    in the month the user is looking at. Sorted chronologically by the pair's own span,
    with the event id as a total-order tiebreaker: two events CAN share a span (spec 8.3
    splits on a GPS jump, so two photographs taken at the same instant a continent apart
    are two events with identical start and end).
    """
    buckets: dict[int, list[ReviewItem]] = {}
    labels: dict[int, str | None] = {}
    for item in members:
        if item.event_id is None:
            continue
        buckets.setdefault(item.event_id, []).append(item)
        labels[item.event_id] = item.event_label
    groups = []
    for event_id, pair in buckets.items():
        start, end = _span(pair)
        groups.append(
            EventGroup(
                key=f"{month}{EVENT_MARK}{event_id}",
                event_id=event_id,
                label=labels[event_id],
                start=start,
                end=end,
                count=len(pair),
            )
        )
    groups.sort(key=lambda group: (group.start, group.end, group.event_id))
    return tuple(groups)


def nav_tree(items: list[ReviewItem]) -> list[YearGroup]:
    """Spec 8.2 rev 14's Year -> Month -> Event tree, counts at EVERY level, Undated last.

    Pinned, not sorted into place: `undated` sorts after every four-digit year as a
    string today, and leaning on that would make the spec's rule an accident of the
    alphabet. It is appended after the sorted years instead.

    The three counts reconcile downward and the reconciliation is not an identity: a
    year's count is the sum of its months', but a month's count is the sum of its events'
    counts IN THAT MONTH plus the items in that month that belong to no event at all -
    see `event_key`. A month with an unevented sidecar in it has a count larger than its
    event level's, and that is correct rather than a rounding error.
    """
    months: dict[str, list[ReviewItem]] = {}
    undated = 0
    for item in items:
        key = group_key(item)
        if key == UNDATED:
            undated += 1
        else:
            months.setdefault(key, []).append(item)
    years: dict[str, list[MonthGroup]] = {}
    for key in sorted(months):
        years.setdefault(key[:4], []).append(
            MonthGroup(key=key, count=len(months[key]), events=_events_in(key, months[key]))
        )
    tree = [
        YearGroup(
            key=year,
            count=sum(month.count for month in group),
            months=tuple(group),
        )
        for year, group in sorted(years.items())
    ]
    if undated:
        tree.append(YearGroup(key=UNDATED, count=undated, months=()))
    return tree


@dataclass(frozen=True)
class ItemFilter:
    """Spec 8.2's query parameters, already validated. Every field composes with the rest.

    `group` is a year (`2019`), a year-month (`2019-07`), a (month, event) pair
    (`2019-07.e12`, rev 14) or `undated`; `attention` is tri-state, because `attention=0`
    is a real request for the items that need none.
    """

    group: str | None = None
    tier: str | None = None
    status: str | None = None
    attention: bool | None = None
    q: str | None = None
    sort: str = SORT_TRUST_ASC


def _matches_group(item: ReviewItem, group: str) -> bool:
    """Spec 8.2 rev 14's four `group` forms, narrowest first.

    The event form is tested first because it is the only one that is not a prefix
    question: `2019-07.e12` selects the items whose (month, event) PAIR is that one, so
    an item whose file is in event 12 but whose date token falls in June is NOT in it.
    The selection runs through `event_key`, which runs through the item's `event_id` -
    the membership `file_events` recorded - and never through the file's own id.
    """
    if EVENT_MARK in group:
        return event_key(item) == group
    key = group_key(item)
    if group == UNDATED or len(group) > 4:
        return key == group
    return key != UNDATED and key[:4] == group


def _matches_q(item: ReviewItem, needle: str) -> bool:
    """Spec 8.2 / A4: old name, proposed name, the EVENT'S LABEL, the PLACE, and PEOPLE.

    THE FIFTH OPERAND IS M11's (Task 12), UX spec 5's "Search matches: ... people". It
    matches a person's `label` AND their `filename_label`, because a user may search by the
    name they typed or by the one they see in the filename. It is also A NEW NON-UNIFORM
    FILTER DIMENSION: a Live Photo's `.MOV` has no faces, so `q=<person>` selects the photo
    and not the video. `decide-filter` widens through `write_state._widen_to_units` over the
    whole plan, and `test_a_people_search_matching_only_the_photo_of_a_live_photo_unit_
    decides_the_video` is the proof that the widening is reached.

    THE FOURTH OPERAND IS M10's (Task 10, `m9.md`'s C7), and it is the one A5 argued
    should be built once rather than twice. Spec 8.2 rev 16 and UX spec 5 rev 10 moved
    PLACE search from M9 to M10 for that reason and no other: M9 already wrote real
    `place` facts, but M10 adds a second searchable fact kind to the same
    content-addressed table, so the operand is written over
    `SEARCHABLE_FACT_KINDS` - see there for why exactly one kind is wired.

    A FILE WITH NO FACTS CONTRIBUTES NOTHING, exactly as an absent label does: `facts` is
    the empty tuple and `any()` over it is False, so an unenriched item is matched by its
    names or by nothing. A filter that widened as the data thinned would be the same
    defect from the other side.

    THE EVENT OPERAND IS M9's (Task 11), and the delay was the point. Spec 8.2's
    degradation table put event NAVIGATION in M8 and event SEARCH in M9 because an
    event's label is the geocoded place of its GPS centroid: every label M8 could write
    was NULL, so this clause would have matched nothing but the empty string and the
    placeholder would have promised a feature that returns silence - UX 1.4's dead end
    wearing a feature's clothes. M9 Task 9 gives labels a writer, so the clause has
    something to match and `index.html`'s placeholder changes in the same task.

    AN ABSENT LABEL CONTRIBUTES NOTHING, and the two ways of being absent are one way:
    `None` (no `events` row, or a row that was never geocoded) and `''` become the same
    empty string, exactly as `webui/nav.event_label` collapses them for the nav. That is
    what keeps this from being a filter that widens as the data thins - an item in no
    event is matched by its names or by nothing.

    CASE-FOLDED, NEVER TRANSLITERATED, and deliberately: spec 6.2's NFKD + fold table
    (spec 5.3) is normative for FILENAMES, and applying a filename rule to a search box
    would be inventing a rule no spec carries. An accented label stays reachable both
    ways regardless, by two honest routes rather than one lossy one: the ASCII spelling
    finds the PROPOSED NAME, because the default template carries `{event-or-scene}` and
    the fold table already turned the label into its unaccented form on the way into the
    filename, while the accented spelling finds the stored label itself. Folding here
    would buy neither route and would put two different normalizations in one predicate.
    """
    folded = needle.casefold()
    # `old_filename`, never `files.filename`: the plan froze the old name and the files
    # row moves to the NEW one the moment spec 7.1 applies it, so a search over the live
    # row would stop matching what the card says the file used to be called.
    return (
        folded in item.old_filename.casefold()
        or folded in item.new_filename.casefold()
        or folded in (item.event_label or "").casefold()
        or any(folded in value.casefold() for value in item.facts)
        or any(
            folded in person.label.casefold()
            or folded in (person.filename_label or "").casefold()
            for person in item.people
        )
    )


def _sort_key(item: ReviewItem, sort: str) -> tuple[Any, ...]:
    """A TOTAL order in both modes, with `id` last so paging cannot skip or repeat a row.

    Equal trust scores are commonplace - a whole archive of clean EXIF dates scores 100 -
    so without the tiebreaker `offset=50` would be answering a differently-ordered list
    from the one `offset=0` came out of, and the user would silently never see some cards.
    """
    if sort == SORT_DATE:
        date = item.date
        # Undated last here too, so the sort agrees with the nav tree's pin.
        return (date is None, date or "", item.id)
    return (item.trust_score, item.id)


def select_items(items: list[ReviewItem], filt: ItemFilter) -> list[ReviewItem]:
    """Spec 8.2's filters, composed, then ordered. Pure: no connection, no SQL."""
    chosen = [
        item
        for item in items
        if (filt.group is None or _matches_group(item, filt.group))
        and (filt.tier is None or item.tier == filt.tier)
        and (filt.status is None or item.status == filt.status)
        and (filt.attention is None or item.attention == filt.attention)
        and (filt.q is None or _matches_q(item, filt.q))
    ]
    chosen.sort(key=lambda item: _sort_key(item, filt.sort))
    return chosen


def unit_sizes(items: list[ReviewItem]) -> dict[str, int]:
    """`unit_key` -> how many of this plan's items rename with it (spec 7.3, A6).

    The frontend needs it on every card: a rename edits the whole companion unit's stem,
    so a card that renames two partners must say so before the user presses a key.
    Counted over the WHOLE plan, never over the filtered page - a unit's other member can
    easily be on another page or excluded by the current filter, and a count that shrank
    with the filter would tell the user a live-photo pair was a lone file.
    """
    sizes: dict[str, int] = {}
    for item in items:
        sizes[item.unit_key] = sizes.get(item.unit_key, 0) + 1
    return sizes


def _parsed(text: object) -> Any:
    """`json.loads`, or None if the stored text will not parse.

    pam wrote every one of these with `json.dumps`, so an unparseable one is catalog
    damage rather than an ordinary outcome - and `pam verify` is the command with an
    opinion about catalog damage, following `reports.py`'s ruling exactly. Raising here
    would turn one damaged row into a 500 for the whole review page; returning None costs
    that one card its provenance, and `date_token_value` then reads it as undated, which
    puts it in front of a human. Pinned by
    `test_a_row_whose_tokens_json_will_not_parse_is_served_as_undated_rather_than_a_500`.
    """
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _split_rel_path(rel_path: str) -> tuple[str, str]:
    """`2019/a.jpg` -> `('2019', 'a.jpg')`. Catalog rel_paths are POSIX-separated.

    `naming/collision.split_rel_path` is these same two lines and is deliberately NOT
    imported: `pam.naming` imports FROM this package (`naming/tokens.py` takes `PlanFile`
    off it), so reaching back the other way would be an import cycle. `plans.ext_of` sets
    the precedent for this package owning its own filename arithmetic, and
    `tests/test_webui_api.py` pins the two implementations equal.
    """
    head, _, name = rel_path.rpartition("/")
    return head, name


def _orientation(exif_json: object) -> int | None:
    """The recorded EXIF orientation for this file, or None. An exact KEY lookup."""
    record = _parsed(exif_json)
    if not isinstance(record, dict):
        return None
    value = record.get(ORIENTATION_KEY)
    # `bool` is an `int` in Python and `True` is not an orientation.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _searchable_facts(conn: sqlite3.Connection) -> dict[int, tuple[str, ...]]:
    """`{files.id: (value, ...)}` over LIVE facts of every `SEARCHABLE_FACT_KINDS` kind.

    A SECOND STATEMENT RATHER THAN A FIFTH JOIN IN `plan_rows`, and that is structural
    rather than stylistic: `inferred_facts` is content-addressed, so one file can carry
    several live facts, and a LEFT JOIN to it would multiply `plan_items` rows. Every
    field of a `ReviewItem` downstream assumes one row per item - `unit_sizes` counts
    them, `counts` counts them, and the page's `total` is read off the filtered list - so
    a join that duplicated an item would corrupt three numbers to save one query.

    ALL the live values, not the one a filename would use. `places_by_file_id` breaks the
    tie between two live facts by taking the highest id, because spec 6.2 rule 6 requires
    one name from one set of inputs; a SEARCH has no such obligation and the honest answer
    is that a needle matching any fact this archive holds about the file matches the file.
    Read this way it is also strictly the safer direction: an operand that matched fewer
    values would hide cards, and a review page that hides a card is the failure UX spec
    1.4 names.

    `superseded_by IS NULL` for the reason `enrichment._live_fact_strings` documents: a
    retired fact is not something this archive still says about the file.
    """
    kinds = tuple(sorted(SEARCHABLE_FACT_KINDS))
    if not kinds:  # pragma: no cover - `IN ()` is a syntax error, not an empty result
        return {}
    placeholders = ",".join("?" * len(kinds))
    rows = conn.execute(
        "SELECT f.id AS file_id, i.kind AS kind, i.value_json AS value_json FROM files f"
        " JOIN inferred_facts i ON i.sha256 = f.sha256"
        f" WHERE i.superseded_by IS NULL AND i.kind IN ({placeholders})"
        " ORDER BY f.id, i.kind, i.id",
        kinds,
    )
    found: dict[int, list[str]] = {}
    for r in rows:
        value = _parsed(r["value_json"])
        if not isinstance(value, dict):
            continue
        text = value.get(SEARCHABLE_FACT_KINDS[str(r["kind"])])
        if isinstance(text, str) and text:
            found.setdefault(int(r["file_id"]), []).append(text)
    return {file_id: tuple(values) for file_id, values in found.items()}


def plan_rows(conn: sqlite3.Connection, plan_id: int, *, floor: float) -> list[ReviewItem]:
    """Every item of one plan with the file facts spec 8.2's card needs, ordered by id.

    An INNER JOIN to `files`, not a LEFT one, and deliberately: `plan_items.file_id`
    carries a foreign key and nothing deletes a `files` row, so a plan item without its
    file is catalog corruption. The LEFT JOIN is to `capture`, which a sidecar or an
    unread file legitimately has none of.

    THE EVENT IS THE MODULE DOCSTRING'S LIVE JOIN, `plan_items -> files -> file_events ->
    events`, and both of its legs are LEFT: a file in no event is ordinary (a sidecar has
    no `capture` row, so spec 8.3's grouping never sees it). `event_id` is read from the
    `events` row - `ev.id`, never `fe.event_id` - so a membership pointing at a row that
    is not there reads as "no event" rather than as an event with no label. That mirrors
    `events.event_membership`'s INNER join, and it is the third layer of the same defence
    that module documents; `PRAGMA foreign_keys=ON` and `REFERENCES events(id)` are the
    two beneath it.

    THE SEARCHABLE FACTS ARE READ ALONGSIDE, NOT JOINED IN (M10 Task 10) - one extra
    statement over the whole archive, indexed by `files.id` and attached here.
    `_searchable_facts` argues why a fifth join would have been the wrong shape.

    PEOPLE LIKEWISE (M11 Task 12), keyed by the `f.sha256` this query already reads.
    `floor` is REQUIRED and is `[tiers] faces`' - the one `{people}` reads through - so a
    caller that forgot it is a TypeError and not a search that silently disagrees with the
    names on the cards.
    """
    facts = _searchable_facts(conn)
    people = people_by_sha256(conn, floor)
    rows = conn.execute(
        "SELECT i.id, i.file_id, i.old_rel_path, i.unit_key, i.generated_filename,"
        " i.new_filename, i.tokens_json, i.tier, i.trust_score, i.trust_reasons_json,"
        " i.status, f.filename, f.media_type, f.placeholder, f.sha256, c.exif_json,"
        " ev.id AS event_id, ev.label AS event_label"
        " FROM plan_items i JOIN files f ON f.id = i.file_id"
        " LEFT JOIN capture c ON c.file_id = f.id"
        " LEFT JOIN file_events fe ON fe.file_id = f.id"
        " LEFT JOIN events ev ON ev.id = fe.event_id"
        " WHERE i.plan_id = ? ORDER BY i.id",
        (plan_id,),
    )
    return [
        ReviewItem(
            id=int(r["id"]),
            file_id=int(r["file_id"]),
            old_rel_path=str(r["old_rel_path"]),
            rel_dir=_split_rel_path(str(r["old_rel_path"]))[0],
            old_filename=_split_rel_path(str(r["old_rel_path"]))[1],
            unit_key=str(r["unit_key"]),
            generated_filename=str(r["generated_filename"]),
            new_filename=str(r["new_filename"]),
            tier=str(r["tier"]),
            trust_score=int(r["trust_score"]),
            status=str(r["status"]),
            tokens=_parsed(r["tokens_json"]),
            trust_reasons=_parsed(r["trust_reasons_json"]),
            filename=str(r["filename"]),
            ext=ext_of(str(r["filename"])),
            media_type=str(r["media_type"]),
            placeholder=bool(r["placeholder"]),
            has_sha256=r["sha256"] is not None,
            orientation=_orientation(r["exif_json"]),
            event_id=None if r["event_id"] is None else int(r["event_id"]),
            event_label=None if r["event_label"] is None else str(r["event_label"]),
            facts=facts.get(int(r["file_id"]), ()),
            people=people.get(r["sha256"] or "", ()),
        )
        for r in rows
    ]

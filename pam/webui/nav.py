"""Spec 8.2's navigation read: `/plan/{id}/groups`, the Year -> Month -> Event tree.

M14 Task 2 Step 1: MOVED VERBATIM out of `webui/api.py` (at 290 executable lines against the
~300 budget) ahead of the list view, which reads the same month words and event spans. A pure
move: no behaviour changed. Like `write_api`, this imports the request primitives out of
`api`, so `api` cannot import it back, and `ReviewServer` merges `NAV_HANDLERS` beside
`api.HANDLERS`.
"""

import sqlite3
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.errors import PamError
from pam.webui import listing, rename_jobs
from pam.webui.api import (
    ApiRequest,
    _bad_request,
    _item_filter,
    _not_found,
    _plan_id,
    faces_floor,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# UX spec 5's nav labels. Spelled out rather than taken from `calendar.month_name`, which
# is locale-dependent: the same plan must not read "July" on one machine and "juillet" on
# another when nothing about the archive changed.
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
UNDATED_LABEL = "Undated"

# UX spec 5 rev 8's event level renders an unlabelled event as its DATE SPAN, so it needs
# the short form of the same twelve words - and it needs them from the same place and for
# the same reason: `calendar.month_abbr` is locale-dependent, and the nav must not read
# `Jul` on one machine and `juil.` on another when nothing about the archive changed.
# Derived from `MONTH_NAMES` rather than typed again, because English's twelve short forms
# ARE the first three letters and two lists that must agree are two lists that can stop.
MONTH_ABBREVIATIONS = tuple(name[:3] for name in MONTH_NAMES)

# The dash between the two days of a multi-day span. UX spec 5 rev 8 writes it as an EN
# DASH (U+2013) and this follows the spec's typography: it is copy the BROWSER renders,
# where `index.html` and `app.js` already carry arrows, bullets and em dashes and
# `webui/disclosure.py` already carries an em dash in a sentence pam shows the user. The
# ASCII habit elsewhere in this package is about console output, not about the page.
SPAN_DASH = "–"


def event_label(event: repo.EventGroup) -> str:
    """UX spec 5 rev 8: the event's LABEL if it has one, otherwise its DATE SPAN.

    EVERY EVENT IN M8 IS UNLABELLED - `repo/events.py::_insert_event` leaves the column
    NULL on purpose, because a label is the geocoded place of the event's GPS centroid
    and geocoding is M9 - so the span is the branch a human will actually see, and the
    label is the fallback that waits for M9 rather than the other way round. An empty
    label falls to the span too: "no label" and "the empty label" must not be tellable
    apart by accident (`EventMembership`'s docstring settles that for naming, and a nav
    entry rendered as `` () `` is the same mistake wearing a different hat).

    Rendered SERVER-SIDE, beside the month names and for the identical reason: the words
    must not be locale-dependent, and nothing in this project lints, types or executes
    `app.js`, so a contract expressed there cannot be tested (`m7.md` F4). The page reads
    `label` for all three nav levels and does no date arithmetic of its own.
    """
    if event.label:
        return event.label
    start = f"{MONTH_ABBREVIATIONS[int(event.start[5:7]) - 1]} {int(event.start[8:10])}"
    if event.end == event.start:
        return start
    # A (month, event) pair's span cannot cross a month, so only the DAY differs and the
    # month word is not repeated: `Jul 12-14`, never `Jul 12 - Jul 14`.
    return f"{start}{SPAN_DASH}{int(event.end[8:10])}"


def told_apart(labels: list[str]) -> list[str]:
    """HT-M14 F3 (AM7): events whose words are the same are told apart by their order.

    Two unlabelled events on one day both read `Aug 30` (the date tree) and `August 30, 2023`
    (the list), and the user cannot tell which is which. A label that occurs more than once is
    numbered in the order given - date order, which is the order both callers build in -
    `Aug 30, event 1`, `Aug 30, event 2`. A label that occurs once is untouched.
    """
    totals = Counter(labels)
    seen: Counter[str] = Counter()
    out = []
    for label in labels:
        if totals[label] > 1:
            seen[label] += 1
            out.append(f"{label}, event {seen[label]}")
        else:
            out.append(label)
    return out


def plan_groups(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`/api/plan/{id}/groups` - Year -> Month -> Event, counts at EVERY level (rev 14).

    Grouped by the DATE TOKEN'S VALUE, never by `capture_dt` (spec 8.2), so the nav says
    what the cards say - and that rule reaches the event level too: an event's entry sits
    under the month its ITEMS' date tokens fall in, and its span is read off those same
    tokens. An event that spans a month boundary is therefore two entries, one under each
    month, each counting its own share. `repo.nav_tree` owns all of that arithmetic; this
    adds only the copy.
    """
    plan_id = _plan_id(request)
    with server.connection() as conn:
        if repo.plan_header(conn, plan_id) is None:
            raise _not_found(f"pam does not know a plan with id {plan_id}")
        items = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
    return {
        "plan_id": plan_id,
        "groups": [
            {
                "key": year.key,
                "label": UNDATED_LABEL if year.key == repo.UNDATED else year.key,
                "count": year.count,
                "months": [
                    {
                        "key": month.key,
                        "label": MONTH_NAMES[int(month.key[5:7]) - 1],
                        "count": month.count,
                        "events": [
                            {"key": event.key, "label": label, "count": event.count}
                            for event, label in zip(
                                month.events,
                                told_apart([event_label(e) for e in month.events]),
                                strict=True,
                            )
                        ],
                    }
                    for month in year.months
                ],
            }
            for year in repo.nav_tree(items)
        ],
    }


# UX 5 (U2): the undated list group's title, pinned last.
NO_DATE_TITLE = "No date found"


def date_span(start: str, end: str) -> str:
    """A list group's date line: `July 12, 2019`, `July 12–14, 2019`,
    `July 30 – August 2, 2019`, or `December 30, 2019 – January 2, 2020`.

    Unlike the nav's (month, event) pairs, a list group is a WHOLE event (U25), so its span
    can cross a month and a year, and the month word is repeated only when it changes.
    """
    first = f"{MONTH_NAMES[int(start[5:7]) - 1]} {int(start[8:10])}"
    if start == end:
        return f"{first}, {start[:4]}"
    if start[:7] == end[:7]:
        return f"{first}{SPAN_DASH}{int(end[8:10])}, {start[:4]}"
    last = f"{MONTH_NAMES[int(end[5:7]) - 1]} {int(end[8:10])}"
    if start[:4] == end[:4]:
        return f"{first} {SPAN_DASH} {last}, {start[:4]}"
    return f"{first}, {start[:4]} {SPAN_DASH} {last}, {end[:4]}"


# M14 Task 3 (R1 F7 + F9): `/list` is VIEW-SCOPED. The view's filters, by the same names
# `/plan/{id}/items` parses them with (and U17's `view` carries): the tab, the trust filter,
# the search and - M14 Task 3's controller ruling - the date tree's `group`, because "the filter
# the user was looking at" includes the month, year, event or undated entry that narrowed the
# list. Spelled here rather than read off `api.ITEM_QUERY_PARAMETERS`, which also holds the sort
# and the page bounds - neither of which is part of a view.
LIST_QUERY_PARAMETERS = ("tier", "status", "attention", "q", "group")


def _refuse_unknown_list_parameters(request: ApiRequest) -> None:
    """A parameter `/list` does not take is a 400 in Task 0D's copy (M13 V3), never dropped:
    a dropped filter would show counts - and a shortcut's N - over a BROADER set."""
    unknown = sorted(name for name in request.query if name not in LIST_QUERY_PARAMETERS)
    if unknown:
        raise _bad_request(unknown[0], f'"{unknown[0]}" is not something pam can filter by.')


def plan_list(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`/api/plan/{id}/list` - the review list's groups (design 8.2, U25), in date order with
    `No date found` last. EVERY COUNT IS THE SERVER'S (`webui/listing.list_groups`); this adds
    only the words.

    M14 Task 3: every count - a group's header numbers, its `shortcut_count`, and the
    top-level summary the callout reads - is over `select_items(view)` (intersected with the
    group), and each `shortcut_count` is `listing.shortcut`, the SAME call `decide-filter`'s
    shortcut branch writes with, so a button's N is what its write takes."""
    plan_id = _plan_id(request)
    _refuse_unknown_list_parameters(request)
    filt = _item_filter(request)
    with server.connection() as conn:
        if repo.plan_header(conn, plan_id) is None:
            raise _not_found(f"pam does not know a plan with id {plan_id}")
        items = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
        renames = _renames(conn, server, plan_id)
    chosen = repo.select_items(items, filt)
    by_id = {item.id: item for item in chosen}
    groups = []
    for group in listing.list_groups(items, chosen):
        dated = group.start is not None and group.end is not None
        line = date_span(group.start, group.end) if group.start and group.end else ""
        members = [by_id[i] for i in group.item_ids]
        groups.append(
            {
                "key": group.key,
                "title": group.label or (line if dated else NO_DATE_TITLE),
                "date_line": line,
                "total": group.total,
                "undecided": group.undecided,
                "attention_undecided": group.attention_undecided,
                "shortcut_count": len(listing.shortcut(items, members).ids),
                "all_low": group.all_low,
            }
        )
    for group_out, title in zip(
        groups, told_apart([str(g["title"]) for g in groups]), strict=True
    ):
        group_out["title"] = title
    undecided = [item for item in chosen if item.status == "proposed"]
    return {
        "plan_id": plan_id,
        "groups": groups,
        # The top callout's two numbers (UX 5a, OD-1): "N need a look" and "Approve the
        # other N", over the same view and by the same call as the groups'.
        "undecided": len(undecided),
        "attention_undecided": sum(1 for item in undecided if item.attention),
        "shortcut_count": len(listing.shortcut(items, chosen).ids),
        # W16's footer N (M14 Task 6c, ruling B2): the WHOLE plan's, whatever the view.
        "renames": renames,
    }


def _renames(conn: sqlite3.Connection, server: "ReviewServer", plan_id: int) -> int:
    """How many files the Confirm screen would rename now (ruling B2): ITS OWN count,
    `rename_jobs.confirmation(...).approved` - the preflight's actionable items, so an approved
    no-op member (J12) and a refused item are not counted - never a second count. A plan `pam apply`
    would refuse (nothing approved, among others) renames nothing: 0, and the footer says W16's
    `Approve names to rename them`.

    MEMOISED (S1-F7: the preflight cost every `/list` +53 % at 100k). The key is everything that
    can change the catalog while this server holds the workspace lock: the folder bound, the
    plan, every POST finished (`ReviewServer.count_write`) and the job running, if any - a job
    writes on its own thread, and its start is a POST. Read BEFORE the preflight runs, so a
    write that lands meanwhile changes the key. What the key cannot see is a file changed on
    disk by something other than pam; the footer may then lag until the next write, and Confirm,
    which always runs the preflight afresh and binds the nonce to it, is the count that renames.
    """
    key = (server.binding, plan_id, server.writes, server.jobs.busy_with())
    memo = server.renames_memo
    if memo is not None and memo[0] == key:
        return memo[1]
    try:
        renames = rename_jobs.confirmation(
            conn, server.workspace.archive_root, plan_id
        ).approved
    except PamError:
        renames = 0
    server.renames_memo = (key, renames)
    return renames


# The nav row `ReviewServer` merges in beside `api.HANDLERS`.
NAV_HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("GET", "plan/{id}/groups"): plan_groups,
    # M14 Task 2 (U25): the review list's groups.
    ("GET", "plan/{id}/list"): plan_list,
}

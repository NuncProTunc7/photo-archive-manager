"""Spec 8.2's read API: the three GET endpoints the review page is built out of (Task 8).

THIS MODULE IMPORTS NOTHING FROM `server.py` AT RUNTIME, and that is what keeps the two
one-directional: `server.py` imports `HANDLERS`, `ApiRequest` and `ApiError` from here and
wires them into every `ReviewServer` it builds. The server type is needed only for
annotation, so it arrives under `TYPE_CHECKING`.

EVERY HANDLER IS A READER. It opens one connection through the server's per-request
factory (clarification 5), reads, and returns a JSON-serializable object. Nothing here
writes, and nothing here recomputes a name, a tier or a score - `tokens_json` and
`trust_reasons_json` are parsed exactly once, in `catalog/repo/review.py`, and this module
hands that object to `json.dumps` without touching a field of it (clarification 6). The
contract is SEMANTIC preservation, not byte-for-byte: these documents are embedded inside
a larger response, so whitespace and escaping may legitimately differ. What may not differ
is the meaning - same fields, same values, same array order, in both documents, because
order is meaning in both.

TWO REFUSALS, AND THEY ARE NOT THE TOKEN GATE'S. A request that reaches a handler has
already proved it carries the session token, so it gets a reason: an unknown plan or item
id is a 404 with `{error}`, and a query value the API cannot read is a 400 NAMING THE
PARAMETER (UX 1.4 - the page shows these, and a refusal that does not say which control is
wrong is a dead end). Amendment A3's silent empty 404 is a different thing entirely: it is
what an UNAUTHORIZED or unrouted request gets, and it stays empty precisely so a probing
page learns nothing. Both are honoured, because they answer different questions.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.webui import listing, lookalikes, provenance, thumbs

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

BAD_REQUEST_STATUS = 400
NOT_FOUND_STATUS = 404
# Task 9's refusal: the request was well-formed and the caller may not have it. Spec
# 8.2 uses it for the two things a decide or a rename can legitimately be told - the
# rows are not in a state that can be decided, and the name is already taken - both of
# which a page fixes by showing the user something, not by re-encoding the request.
CONFLICT_STATUS = 409

# Spec 8.2's paging bound, server-side. A browser asking for the whole plan in one
# response is how a 20,000-item archive becomes a page that never paints; the plan
# suggests 200 and the grid renders far fewer than that at a time.
MAX_LIMIT = 200
DEFAULT_LIMIT = 100

# Spec 8.2's thumbnails are extracted JPEGs, so this is the one non-JSON response the
# API produces. See `RawResponse` below for how it leaves the router.
JPEG_TYPE = "image/jpeg"

# UX spec 5's tier vocabulary, and vision Q3's two provenance fields, live in
# `webui/provenance.py`. They moved there in M10 Task 10 when `fact_models` and
# `confidence_note` arrived: three strings about how a name explains itself belong in one
# module, and this one was at 280 executable lines against the ~300 budget. `trust_label`
# is still a RESPONSE FIELD for spec 8.2 rev 14's reason - nothing in this project lints,
# types or executes `app.js`, so a band table expressed there cannot be tested.


@dataclass(frozen=True)
class ApiRequest:
    """One API call's inputs: the path parameters, and the parsed query string.

    `query` maps a name to EVERY value given for it, which is what `parse_qs` returns and
    what lets `?tier=high&tier=low` be a 400 rather than a silent choice of one of them.
    """

    params: Mapping[str, str]
    query: Mapping[str, list[str]]
    # Task 9: the POST body, already decoded. `{}` for a GET and for a POST that sent
    # none (spec 8.2's `restore` carries no fields). ALWAYS a mapping - `parse_body`
    # refuses anything else before a handler is reached - so every write handler reads
    # `request.body.get(...)` without first asking what shape arrived.
    body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawResponse:
    """A handler's answer that is NOT JSON: content type plus the bytes to send.

    Task 10's `/api/thumb/{file_id}` is the only producer and spec 8.2 is why: the
    endpoint's whole job is to hand the browser an `image/jpeg` that an `<img>` tag can
    point at, and JSON is the wrong envelope for bytes. `server._api` sends this through
    unencoded and `json.dumps` everything else, which keeps the route TABLE the thing
    that routes - a binary endpoint is a row like any other, not a branch in the router.
    """

    content_type: str
    body: bytes
    # M11 Task 10: extra response headers, as `(name, value)` pairs. The crop endpoint is
    # the first producer and spec 8.2 is why - `Cache-Control: no-store` "unless the cache
    # is inventoried and purgeable" (A19), and pam's is not. A TUPLE of pairs rather than a
    # dict because a frozen dataclass with a mutable default is a shared-state bug waiting,
    # and because the order a response sends its headers in should be the order they were
    # written.
    headers: tuple[tuple[str, str], ...] = ()


class ApiError(Exception):
    """A refusal a handler wants the browser to read: a status plus one sentence.

    `extra` (M14 Task 3, U17) is what a refusal carries BESIDE its sentence - the one
    producer is `decide-selection`'s stale-count 409, which hands back the fresh preview so
    the page can show the new count without a second request. Numbers and ids only: the
    token elision in `server` covers the sentence, and nothing here may carry a path.
    """

    def __init__(
        self, status: int, message: str, extra: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra: Mapping[str, Any] = extra if extra is not None else {}


def _not_found(message: str) -> ApiError:
    return ApiError(NOT_FOUND_STATUS, message)


def _bad_request(parameter: str, message: str) -> ApiError:
    """400, and the sentence always begins with the parameter the page must fix."""
    return ApiError(BAD_REQUEST_STATUS, f"{parameter}: {message}")


def _conflict(message: str) -> ApiError:
    """409: pam understood the request and is refusing it on the catalog's state."""
    return ApiError(CONFLICT_STATUS, message)


# --- reading the request body (Task 9) -----------------------------------------------


def parse_body(raw: bytes) -> Mapping[str, Any]:
    """The bytes of a POST body as spec 8.2's JSON object. `{}` when none was sent.

    A body that is not JSON, or is JSON but not an OBJECT, is a 400 that says which -
    `[1, 2]` and `null` are both valid JSON and neither can carry `action`, and a page
    that sent one deserves to be told that rather than to watch every field read as
    missing. The parameter name is `body` because that is what the page must fix.

    Never raises anything but `ApiError`: `json.loads` on arbitrary bytes can raise
    `UnicodeDecodeError` as easily as `JSONDecodeError`, and a browser posting a
    mis-encoded form must not become a 500 with a traceback in the session log.
    """
    if not raw:
        return {}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _bad_request("body", "is not JSON pam can read") from None
    if not isinstance(document, dict):
        raise _bad_request("body", 'must be a JSON object like {"action": "approve"}')
    return document


# --- reading the query string --------------------------------------------------------


def _single(request: ApiRequest, name: str) -> str | None:
    """One value for `name`, or None if it was not given. Two values is a 400.

    Silently taking the first would make `?tier=high&tier=low` mean `high`, which is a
    page showing a filter it was not asked for and never saying so.
    """
    values = request.query.get(name)
    if not values:
        return None
    if len(values) > 1:
        raise _bad_request(name, "given more than once - pam cannot tell which you meant")
    return values[0]


def _plan_id(request: ApiRequest) -> int:
    """The `{id}` out of the path. Not an integer -> 404, because no such plan exists."""
    raw = request.params["id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know a plan with id {raw!r}")
    return int(raw)


def _file_id(request: ApiRequest) -> int:
    """The `{file_id}` out of the path. Spec 8.2's thumb route is keyed by `files.id`.

    NOT a plan item id, and the difference matters: one file can appear in several plans
    and its embedded preview is a property of the FILE, so the cache is keyed by the
    file's sha256 and the route is keyed by the file's id.
    """
    raw = request.params["file_id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know a file with id {raw!r}")
    return int(raw)


def _one_of(request: ApiRequest, name: str, allowed: tuple[str, ...]) -> str | None:
    value = _single(request, name)
    if value is None or value == "":
        return None
    if value not in allowed:
        raise _bad_request(name, f"{value!r} is not one of {', '.join(allowed)}")
    return value


def _attention(request: ApiRequest) -> bool | None:
    """`attention=1` / `attention=0`, per spec 8.2. Anything else is a 400.

    Tri-state on purpose: absent means "do not filter on it", and `0` is a real request
    for the items that need no attention - the complement UX 5a's banner counts against.
    """
    value = _single(request, "attention")
    if value is None or value == "":
        return None
    if value not in ("0", "1"):
        raise _bad_request("attention", f"{value!r} is not 0 or 1")
    return value == "1"


def _group(request: ApiRequest) -> str | None:
    """A year, a `YYYY-MM`, a `YYYY-MM.eN` or `undated` (A4; rev 14 adds the third).

    THE ONE VALIDATOR FOR THIS FIELD, and `decide-filter` shares it (rev 13 amendment D).
    Spec 8.2 says why in as many words: "a `tier=nonsense` that 400s on the read endpoint
    and not on the write one is a bug". The event form is therefore a new FORM of a field
    both endpoints already carry - it widens neither, and "bulk-approve this event" is
    something the write endpoint gets for free rather than something it is granted.
    """
    value = _single(request, "group")
    if value is None or value == "":
        return None
    if value == repo.UNDATED:
        return value
    month, mark, event = value.partition(repo.EVENT_MARK)
    if mark and (not _is_year_month(month) or not event.isdigit()):
        raise _bad_request("group", f"{value!r} does not name an event like 2019-07.e12")
    if mark or _is_year_or_month(value):
        return value
    raise _bad_request(
        "group",
        f"{value!r} is not a year, a year-month like 2019-07, an event like"
        f" 2019-07.e12, or {repo.UNDATED}",
    )


def _list_group(request: ApiRequest) -> str | None:
    """U25's `list_group`: `e12`, `2019-07-14` or `undated`. Anything else is a 400."""
    value = _single(request, "list_group")
    if value is None or value == "":
        return None
    if not listing.is_list_group_key(value):
        raise _bad_request(
            "list_group",
            f"{value!r} is not an event like e12, a day like 2019-07-14, or {listing.UNDATED_KEY}",
        )
    return value


def _is_year_or_month(text: str) -> bool:
    """`2019` or `2019-07`. The two forms that were the whole vocabulary before rev 14.

    THE QUESTION IS WHETHER A DASH WAS PRESENT, never whether the tail is empty. Asking
    `tail == ""` accepted `2019-`, because `"2019-".partition("-")` yields an empty tail
    exactly as a bare `"2019"` does - and `_matches_group` then takes its `len > 4` branch
    and compares the value against `date[:7]`, which can never be a year and a dash. So a
    filter validated, and selected the empty set, on the read endpoint and on
    `decide-filter` alike: UX spec 1.4's dead end wearing an accepted control's clothes.
    Spec 8.2 rev 15 (amendment A2) settles the vocabulary at exactly four forms and this
    is the one that leaked - `2019-7`, `2019--` and `2019-.e1` were all refused already.

    `bool(dash)` is also what `_is_year_month` below has always asked, so the two
    predicates now differ in one thing only: whether the month half is optional.
    """
    head, dash, tail = text.partition("-")
    return len(head) == 4 and head.isdigit() and (not dash or _is_month(tail))


def _is_year_month(text: str) -> bool:
    """`2019-07` and nothing shorter: an event's key names the month it is filed under."""
    head, dash, tail = text.partition("-")
    return bool(dash) and len(head) == 4 and head.isdigit() and _is_month(tail)


def _is_month(text: str) -> bool:
    return len(text) == 2 and text.isdigit() and 1 <= int(text) <= 12


def _bounded_int(request: ApiRequest, name: str, default: int, maximum: int) -> int:
    value = _single(request, name)
    if value is None or value == "":
        return default
    # `isdigit` refuses `-1`, `+1` and ` 1`, which is why it is here - but it is NOT the
    # predicate "int() will succeed", and the comment that used to sit here claimed it
    # was, wrongly IN BOTH DIRECTIONS. `'²'.isdigit()` is True and `int('²')` RAISES, so
    # `?limit=²` escaped this validator as a `ValueError` and `server.py` turned it into
    # a 500 where spec 8.2 owes a 400; `'٢'.isdigit()` is also True and `int('٢')` returns 2,
    # so the Arabic-Indic digit is ACCEPTED where the comment said it was refused. The
    # same trap is recorded in CLAUDE.md and answered by `isdecimal()` in
    # `enrich/base.py::version_key`; here the `try` is kept as well, because a 500 out of
    # a query string is the failure mode this is protecting against and one predicate is
    # one thing to get wrong again.
    if not value.isdigit():
        raise _bad_request(name, f"{value!r} is not a whole number")
    try:
        number = int(value)
    except ValueError:
        raise _bad_request(name, f"{value!r} is not a whole number") from None
    if number > maximum:
        raise _bad_request(name, f"{number} is more than the maximum of {maximum}")
    return number


# Design 8.2's query parameters for `/plan/{id}/items`: the five filters, the sort, the page
# bounds, and M14's `list_group` (U25). A parameter outside this set is refused by name
# (`refuse_unknown_parameters`, M13 V3's rule carried to the read endpoint, M14 Task 2 Step 1a):
# the readers below read only the names they know, so a mistyped filter would otherwise be
# silently dropped and the page would show a BROADER set than it asked for.
ITEM_QUERY_PARAMETERS = (
    "group",
    "tier",
    "status",
    "attention",
    "q",
    "sort",
    "offset",
    "limit",
    "list_group",
)


def refuse_unknown_parameters(request: ApiRequest) -> None:
    """A query parameter `items` does not know is a 400 naming it, in `decide-filter`'s copy.

    When more than one is unknown the first in SORTED order is named, so the answer does not
    depend on the order the page built its query string in.
    """
    unknown = sorted(name for name in request.query if name not in ITEM_QUERY_PARAMETERS)
    if unknown:
        raise _bad_request(unknown[0], f'"{unknown[0]}" is not something pam can filter by.')


def _item_filter(request: ApiRequest) -> repo.ItemFilter:
    """Spec 8.2's five filters plus the sort, each validated by name."""
    q = _single(request, "q")
    return repo.ItemFilter(
        group=_group(request),
        tier=_one_of(request, "tier", repo.TIERS),
        status=_one_of(request, "status", tuple(sorted(repo.ITEM_STATUSES))),
        attention=_attention(request),
        q=q if q else None,
        sort=_one_of(request, "sort", repo.SORTS) or repo.SORT_TRUST_ASC,
    )


# --- the three endpoints -------------------------------------------------------------


def faces_floor(server: "ReviewServer") -> float:
    """`[tiers] faces` floor (A11), for every `repo.plan_rows` in this package (Task 12).

    ONE READ OF THE KEY for the review API, because people search must match exactly the set
    `{people}` names, and `naming/planner.py` reads the same key: a second spelling here that
    drifted would let a card be found by a person its name does not show, or the reverse.
    """
    return server.config.tiers["faces"].floor


def counts(items: list[ReviewItem]) -> dict[str, int]:
    """UX 5's header numbers and UX 5a's banner, from one plan's rows.

    THE ONE PLACE THIS ARITHMETIC LIVES. `/api/plan/{id}` renders it, and every Task 9
    write response carries it so the header updates without a second request - and a
    second implementation would let the number the page shows after a decision disagree
    with the number it shows after a reload, which is the same class of failure spec 8.2
    names when it says the banner must not claim twenty items need attention while the
    filter shows nineteen.
    """
    by_status = _counted(item.status for item in items)
    return {
        "total": len(items),
        "approved": by_status.get("approved", 0),
        "skipped": by_status.get("skipped", 0),
        # Everything that is neither, rather than `by_status["proposed"]`: an open plan's
        # items are proposed, approved or skipped today, and a status this endpoint had
        # not heard of would otherwise vanish out of a summary whose job is to account for
        # the whole plan (`webui.__init__._read_counts` records the same reasoning).
        "remaining": len(items) - by_status.get("approved", 0) - by_status.get("skipped", 0),
        "attention": sum(1 for item in items if item.attention),
    }


def plan_summary(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`/api/plan/{id}` - the header's data (UX 5) and Smart Review's banner (UX 5a).

    The counts are computed from the SAME rows the items endpoint serves, so the banner
    cannot claim twenty items need attention while the filter shows nineteen.
    """
    plan_id = _plan_id(request)
    with server.connection() as conn:
        header = repo.plan_header(conn, plan_id)
        if header is None:
            raise _not_found(f"pam does not know a plan with id {plan_id}")
        items = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
    return {
        "plan_id": header.id,
        "archive_root": str(server.workspace.archive_root),
        "template": header.template,
        "created_at": header.created_at,
        "status": header.status,
        **counts(items),
        "tiers": _counted(item.tier for item in items),
        "statuses": _counted(item.status for item in items),
    }


def plan_items(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`/api/plan/{id}/items` - one page of cards, filtered, ordered and sliced.

    `total` is the size of the FILTERED set, not of the page and not of the plan: it is
    what "showing 1-100 of 412" is read from, and paging past the end must be an empty
    page rather than a wrapped one.
    """
    plan_id = _plan_id(request)
    refuse_unknown_parameters(request)
    filt = _item_filter(request)
    list_group = _list_group(request)
    offset = _bounded_int(request, "offset", 0, maximum=2**31)
    limit = _bounded_int(request, "limit", DEFAULT_LIMIT, maximum=MAX_LIMIT)
    with server.connection() as conn:
        if repo.plan_header(conn, plan_id) is None:
            raise _not_found(f"pam does not know a plan with id {plan_id}")
        items = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
        sizes = repo.unit_sizes(items)
        kinds = listing.unit_kinds(items, repo.companion_kinds(conn))
        chosen = repo.select_items(items, filt)
        if list_group is not None:
            # U25: the group is the UNIT's, computed over the whole plan and copied to every
            # member, so a page of one group can never hold part of a unit. Filtering keeps
            # `select_items`' total order, so paging cannot skip or repeat.
            keys = listing.unit_keys(items)
            chosen = [item for item in chosen if listing.in_list_group(item, list_group, keys)]
        page = chosen[offset : offset + limit]
        # THE SECOND READ IS INSIDE THE SAME `with`, and the page is sliced first so it
        # asks about the facts THIS page's tokens point at rather than the whole plan's -
        # spec 8.2 rev 17's "every `source_id` the page's tokens point at", literally.
        # Selection is pure Python over rows already in memory (`review.py`'s "one query,
        # then Python"), so nothing slow happens under the connection's lock; `thumb`'s
        # subprocess is the extraction this package does keep outside it.
        facts = repo.facts_by_id(
            conn, [i for item in page for i, _ in provenance.fact_tokens(item.tokens)]
        )
        # M13 H3: the card's look-alikes, computed once per server and plan, under this lock.
        alikes = lookalikes.for_plan(server, conn, plan_id, items)
        # U15: the date sources the page's date tokens point at, for each row's note code.
        sources = repo.date_sources(conn) if listing.capture_ids(page) else {}
    return {
        "plan_id": plan_id,
        "total": len(chosen),
        "offset": offset,
        "limit": limit,
        "items": [
            _card(item, sizes[item.unit_key], facts, sources)
            | {"unit_kind": kinds[item.unit_key]}
            | lookalikes.card_fields(alikes, item.id)
            for item in page
        ],
    }


def _card(
    item: ReviewItem,
    unit_size: int,
    facts: dict[int, repo.FactModel],
    sources: dict[int, str] | None = None,
) -> dict[str, Any]:
    """One item, as the grid renders it. Every value is read; none is recomputed.

    `trust_label` and `edited` (spec 8.2 rev 14) do not weaken that sentence: neither
    recomputes a name, a tier or a score. `trust_label` RENDERS the tier the planner
    wrote, in the word UX spec 5 assigns to it; `edited` compares two stored filenames.
    Both are derived response fields over stored state, by the route amendment E (rev 13)
    established for `orientation`, and they are served rather than derived in the page
    because NOTHING IN THIS PROJECT LINTS, TYPES OR EXECUTES `app.js` (`m7.md` F4): a
    contract expressed there cannot be tested and cannot be reached by the mutation gate.
    """
    return {
        "id": item.id,
        "file_id": item.file_id,
        "old_rel_path": item.old_rel_path,
        "rel_dir": item.rel_dir,
        "old_filename": item.old_filename,
        "generated_filename": item.generated_filename,
        "new_filename": item.new_filename,
        "media_type": item.media_type,
        "ext": item.ext,
        "tier": item.tier,
        "trust_score": item.trust_score,
        "trust_label": provenance.trust_label(item.tier),
        # Vision Q3 rev 4's second half (amendment A7): where the model behind an
        # AI-derived token emits no confidence, the panel says so and names the tier that
        # produced. `None` when Q3's first half already covers the card.
        "confidence_note": provenance.confidence_note(item, facts),
        # Spec 8.2 REV 22 (A17): the map is on the CARD, keyed by the fact ids THIS item's
        # tokens point at. It was a response-level field through rev 21, which the page had
        # to merge across pages and reset on a filter change - so scrolling past a page
        # boundary could silently drop a page-1 card's model name, with no symptom but a
        # missing parenthesis, in a file nothing in this project lints, types or executes.
        # The class is DELETED rather than tested: with the map on the card there is no
        # cross-page state to merge and no reset to get wrong. `if i in facts` is not
        # defensive tidiness - it is what keeps a card from carrying models belonging to
        # facts its own tokens do not point at, which would disclose what OTHER photographs
        # were enriched with. `facts` is still fetched ONCE for the page.
        "fact_models": provenance.fact_models(
            {i: facts[i] for i, _ in provenance.fact_tokens(item.tokens) if i in facts}
        ),
        "edited": item.edited,
        # UX 5 rev 11's no-op card. The sentence is the page's; the PREDICATE is the
        # server's, for the reason `ReviewItem.noop` sets out.
        "noop": item.noop,
        "status": item.status,
        "unit_key": item.unit_key,
        "unit_size": unit_size,
        "orientation": item.orientation,
        "placeholder": item.placeholder,
        "has_thumb": item.thumbnailable,
        "attention": item.attention,
        # M14 U15: the code behind a needs-a-look row's one sentence, from the date token's
        # STRUCTURE (`webui/listing.note_code`), never from a reason's text.
        "note_code": listing.note_code(item, sources or {}),
        "date": item.date,
        # The planner's own two documents, parsed once and passed through unedited.
        "tokens": item.tokens,
        "trust_reasons": item.trust_reasons,
    }


def thumb(server: "ReviewServer", request: ApiRequest) -> RawResponse:
    """`/api/thumb/{file_id}?variant=grid|full` - the extracted embedded preview.

    THE FIRST REQUEST EXTRACTS, CACHES AND RETURNS IN ONE ROUND TRIP. Nothing pre-warms
    the cache, so a 200 here is either a cache hit or an extraction that just happened,
    and the browser cannot tell the two apart - which is the point.

    404 IS NOT AN ERROR HERE; IT IS THE GLYPH SIGNAL (spec 8.2). A video, a sidecar, an
    `other`, a file with no sha256, a cloud placeholder, a source that is no longer on
    disk and a photo whose camera embedded no preview all arrive as `None` from
    `get_thumb`, and the frontend draws the same glyph card with an extension badge for
    every one of them. An unreadable id or an unknown file is the same 404 with a
    sentence, because there is nothing else a page could usefully be told.

    THE EXTRACTION RUNS OUTSIDE THE DB LOCK (plan clarification 5). `server.connection()`
    holds the single lock for its whole `with` block, and extraction is a subprocess plus
    a disk write - so the block does the row lookup, and nothing else.
    """
    file_id = _file_id(request)
    variant = _one_of(request, "variant", thumbs.VARIANTS) or thumbs.GRID
    with server.connection() as conn:
        file_row = repo.file_by_id(conn, file_id)
        # Read with the row, inside the lock (R1-F1): a change of folder after the block
        # must not pair this folder's row with the next folder's workspace.
        workspace = server.workspace
    if file_row is None:
        raise _not_found(f"pam does not know a file with id {file_id}")
    path = thumbs.get_thumb(workspace, file_row, variant)
    if path is None:
        raise _not_found(f"pam has no {variant} preview it can show for that file")
    return RawResponse(JPEG_TYPE, path.read_bytes())


def _counted(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# The route table `ReviewServer` wires in. A `{name}` segment is a path parameter; every
# other segment matches literally.
#
# THE READ ROWS. Task 9's four POST rows are `write_api.WRITE_HANDLERS` and `ReviewServer`
# merges the two tables, which is still rows rather than router branches - `_api` and
# `_match_route` were not touched. They are a separate table for one mechanical reason:
# every write handler needs `ApiError`, `ApiRequest`, `_item_filter` and `counts` from
# this module, so `write_api` imports THIS module and this module cannot import it back.
# The alternative was moving the request primitives into a third module, which would have
# rewritten Task 8's file to buy nothing this comment does not say.
HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("GET", "plan/{id}"): plan_summary,
    ("GET", "plan/{id}/items"): plan_items,
    # Task 10. Keyed by `files.id`, not by a plan item, and it answers in `image/jpeg`
    # rather than JSON - see `RawResponse`.
    ("GET", "thumb/{file_id}"): thumb,
}


def error_body(message: str, extra: Mapping[str, Any] | None = None) -> bytes:
    """The one shape a refusal takes on the wire, so the page has one thing to render.

    `extra`'s fields ride beside `error` and can never replace it.
    """
    return json.dumps({**(extra or {}), "error": message}).encode("utf-8")

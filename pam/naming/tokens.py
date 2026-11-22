"""Catalog facts -> template token values with tiers (design spec section 6.1)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pam.catalog.repo import CaptionFact, EventMembership, PersonLabel, PlaceFact, PlanFile

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    # `naming/dates.py` imports `consistency`, which imports this module at runtime, so the
    # type is imported for mypy alone - the `events.py` / `PlanSummary` precedent.
    from pam.naming.dates import ResolvedDate

TIER_ORDER = ("low", "medium", "high")

# spec 6.1: the date token's tier follows the source that produced it. qt_local
# ranks with exif (it is a real local time); qt_utc is capped at medium because
# the local date is genuinely unknowable from a UTC stamp; file_mtime is low.
# spec 6.1 rev 15: {event}'s tier is "the tier of the fact that produced the LABEL -
# `high` for a user-set label, the geocoder's tier otherwise - and NEVER the tier of the
# grouping". Migration 0006 gives `events.label_source` the answer to read; this map is
# rev 15's sentence spelled out from the SPEC rather than from `repo.LABEL_SOURCES`,
# which is the catalog's vocabulary and would make this table agree with whatever that
# tuple happened to say (M6's ENRICHERS, M8's M922/M924).
#
# WHY `geocode` IS `medium`. The geocoder stores NULL confidence - a nearest-city lookup
# in an offline table is a measurement with no probability attached - and spec 8.1's rule
# for a confidence-less enricher is "tier by config default (`medium` cap for
# confidence-less enrichers)", which `config.TierBounds.max_tier_when_no_confidence`
# spells `medium`. A literal here and not a config read, because `resolve_tokens` is a
# pure function over catalog rows and takes no Config - and because spec 11's `[tiers]`
# table has no `geocode` row to read anyway, which is logged as an owed amendment in
# `docs/implementation-notes/m9-progress.md` rather than invented here.
EVENT_LABEL_SOURCE_TIER = {
    "user": "high",
    "geocode": "medium",
}

# A label whose source this catalog cannot name. Structurally unreachable through pam -
# migration 0006 backfilled `user` onto every pre-0006 label, and both writers set the
# column in the same statement as the label - so this is what a catalog corrupted from
# outside gets, and `low` is the safe direction: it means "needs review" and keeps the
# name out of M7's tier-gated bulk approval, which is the reasoning `min_tier`'s empty
# case is built on.
UNKNOWN_LABEL_SOURCE_TIER = "low"

# spec 6.1: "{place}: geocode enricher token (city or named place)". Its tier is derived
# from the SPEC, not by analogy with {event}'s geocoded half above, and the derivation is
# two sentences long:
#
# - `enrich/geocode.py` stores `confidence=None` - a nearest-city lookup in an offline
#   table is a measurement with no probability attached, and spec 6.4 allows NULL.
# - spec 8.1's only rule for a confidence-less enricher is "tier by config default
#   (`medium` cap for confidence-less enrichers)", and `config.TierBounds.
#   max_tier_when_no_confidence` spells that default `medium`.
#
# That the answer coincides with EVENT_LABEL_SOURCE_TIER["geocode"] is a consequence of
# both readings ending at the same sentence of spec 8.1, not a shared constant: the two
# are different questions (a token's own tier, and the tier of the fact behind an event's
# LABEL) and either could move without the other.
#
# A literal here and not a config read, for the reason the map above gives - `resolve_tokens`
# is a pure function over catalog rows and takes no Config - and because spec 11's `[tiers]`
# table still has no `geocode` row to read, which `docs/implementation-notes/m9-progress.md`
# records as an owed amendment rather than inventing one here.
PLACE_TIER = "medium"

# spec 11's `[tiers] caption` row, named so the ONE key `{scene}`'s tier is read from is
# spelled once. See `resolve_tokens`' `caption_tier` parameter for the derivation and for
# why this is a CONFIG READ where `PLACE_TIER` above is a literal.
CAPTION_TIER_KEY = "caption"

DATE_SOURCE_TIER = {
    "exif": "high",
    "qt_local": "high",
    "filename": "medium",
    "qt_utc": "medium",
    "file_mtime": "low",
    "none": "low",
}


@dataclass(frozen=True)
class TokenFact:
    value: str  # raw value; the sanitizer and the budget may still change it
    # 'capture' | 'files' | 'planner' | 'events' | 'inferred_facts' | 'persons' (provenance)
    source_table: str
    source_id: int | None
    tier: str
    # {people} only (design 7.0 J13, rev 26): every person the token NAMES, after the cap,
    # as `(persons.id, the value that reaches the filename)`, in token order. `value` is
    # the joined text and `source_id` the lowest id, and neither can tell `pam apply` that a
    # person has since been hidden, renamed, merged or purged; this can. Empty for every
    # other token, and never read by the template engine.
    named: tuple[tuple[int, str], ...] = ()
    # M12 (design 6.4 rev 27, P11): the token's OWN confidence - today only an OCR date's,
    # the score of the line it was read from. `assess_unit` writes it into this token's
    # `tokens_json` entry ONLY when it is not None, so no existing token's JSON changes.
    confidence: float | None = None


def min_tier(tiers: Iterable[str]) -> str:
    """Lowest tier wins (spec 6.3.4: item tier = min tier across its tokens).

    spec 6.3.4 rev 6: the empty case is `low`, not `high`. A file carrying zero
    facts is the least trustworthy name pam can produce, not the most - rating it
    `high` would feed it straight into M7's tier-gated bulk approval.
    """
    return min(tiers, key=TIER_ORDER.index, default="low")


def date_value(capture_dt: str | None) -> str:
    """capture_dt is ISO local time; the token is its YYYY-MM-DD prefix (spec 6.1).

    Anything shorter than a full date is treated as absent - build_stem then
    writes 'undated' rather than a partial date (never guess, vision principle 5).
    """
    if capture_dt is None or len(capture_dt) < 10:
        return ""
    return capture_dt[:10]


def stem_of(filename: str) -> str:
    """Filename without its final extension ('a.b.jpg' -> 'a.b')."""
    head, dot, _ = filename.rpartition(".")
    return head if dot and head else filename


def resolve_tokens(
    pf: PlanFile,
    seq: int,
    event: EventMembership | None,
    place: PlaceFact | None,
    scene: CaptionFact | None,
    people: tuple[PersonLabel, ...],
    *,
    caption_tier: str,
    max_people: int,
    date: "ResolvedDate | None",
) -> dict[str, TokenFact]:
    """The token facts: date, seq, orig, {event}, {place}, {scene} and (M11) {people}.

    An enricher with nothing to say leaves its token absent from this dict rather than
    empty - build_stem omits an absent token and its separator (spec 6.1 graceful
    degradation), so the realistic name with no enrichers is the vision's worst case,
    2019-07-04_001.

    `event`, `place`, `scene` and `people` - and `max_people` beside them - are REQUIRED
    arguments with no default, and deliberately so. A default of None (or of `()`) would let
    a caller that simply forgot to look the fact up produce names silently missing their
    token, which is the failure mode this project keeps meeting - the wiring, not the logic,
    is what goes wrong. With no default, forgetting is a TypeError at the call site.

    `caption_tier` IS A CONFIG READ, WHICH IS THE ONE PLACE {scene} DIFFERS FROM {place},
    and the derivation is two sentences with one source each:

    - `enrich/caption.py` stores `confidence=None` - spec 8.1's caption note says so in as
      many words ("confidence proxy: none from Ollama -> store NULL");
    - spec 8.1's only rule for a confidence-less enricher is "tier by config default
      (`medium` cap for confidence-less enrichers)", and UNLIKE the geocoder's, the caption
      enricher HAS a row to read that default from: spec 11's
      `[tiers] caption = { ..., max_tier_when_no_confidence = "medium" }`.

    So `PLACE_TIER` above is a literal because spec 11 has no `geocode` row to read (an
    owed amendment, not an invented one), and this is a parameter because spec 11 does have
    a `caption` row and a value the user may change. It arrives as a plain string rather
    than as a `Config`, so this function stays pure over catalog rows; `naming/planner.py`
    is where the key is read, on one line beside this call.

    `date` (M12, design 6.1 rev 27) is REQUIRED for the same reason: the primary's
    `naming/dates.py::ResolvedDate`. It is READ only when `pf.capture_dt_source` is `ocr` -
    which the planner sets exactly when P9 applied - and then the date token's provenance
    is the reading, `(inferred_facts, <the ocr_text fact id>)`, its tier the reading's
    (`low` or `medium`, never `high`, so `DATE_SOURCE_TIER` has no `ocr` row) and its
    `confidence` the score of the line the date was read from. An `ocr` source with no
    reading handed in is a WIRING bug, and raises rather than naming a file from nothing.
    """
    value = date_value(pf.capture_dt)
    source = pf.capture_dt_source or "none"
    # spec 6.1 rev 6: the recorded (source_table, source_id) must reference a row
    # that EXISTS. Companion sidecars and cloud placeholders carry no capture row,
    # and attributing their date to `capture` writes a dangling pointer into the
    # wire format M7's review UI reads.
    date_table: str
    date_id: int | None
    if pf.has_capture:
        date_table, date_id = "capture", pf.id
    elif source == "file_mtime":
        # Structurally unreachable from production today: capture_dt_source is a
        # `capture`-table column only, so files_for_planning's LEFT JOIN makes
        # has_capture=False imply capture_dt_source=None (source == "none"), never
        # "file_mtime". Kept because spec 6.1 rev 6 names this attribution in
        # normative text, and resolve_tokens should stay total over PlanFile.
        date_table, date_id = "files", pf.id
    else:
        date_table, date_id = "planner", None
    date_fact = TokenFact(
        value=value,
        source_table=date_table,
        source_id=date_id,
        tier=DATE_SOURCE_TIER.get(source, "low") if value else "low",
    )
    if source == "ocr":
        if date is None or date.fact_id is None or date.found is None or date.tier is None:
            raise AssertionError(
                f"{pf.rel_path}: its date source is 'ocr' but no OCR reading was handed in"
            )
        date_fact = TokenFact(
            value=value,
            source_table="inferred_facts",
            source_id=date.fact_id,
            tier=date.tier,
            confidence=date.found.confidence,
        )
    facts = {
        "date": date_fact,
        "seq": TokenFact(
            value=f"{seq:03d}", source_table="planner", source_id=None, tier="high"
        ),
        "orig": TokenFact(
            value=stem_of(pf.filename), source_table="files", source_id=pf.id, tier="high"
        ),
    }
    # spec 6.1 rev 14: {event} is the LABEL of the event this file belongs to, and "an
    # event with an empty label emits no token at all". Absent from this dict rather
    # than present-and-empty, for two reasons that both matter:
    #
    # - The stem is the same either way (build_stem omits an empty value with its
    #   separator), but the PROVENANCE is not. A present-and-empty fact would be a
    #   standing invitation to write an `events` pointer for a token that never reached
    #   a filename, and spec 6.1's tokens_json records only tokens that did.
    # - Absent is what {place}, {scene} and {people} already are here. One shape for
    #   "this enricher has nothing to say", not two.
    #
    # In M8 EVERY label is empty (repo.events._insert_event writes NULL; geocoding is
    # M9), so this branch does not fire for any archive pam itself grouped. It is
    # reachable, and tested, by seeding a label - which is also the only way a label
    # can exist in M8 at all.
    if event is not None and event.label:
        facts["event"] = TokenFact(
            value=event.label,
            # spec 6.1 rev 14: "(source_table='events', source_id=<event id>)". The id
            # came from a row `repo.event_membership` read in this same transaction, so
            # rev 6's write-time postcondition ("must reference a row that exists")
            # holds structurally rather than by care.
            source_table="events",
            source_id=event.event_id,
            # spec 6.1 rev 14/15: the tier of the fact that produced the LABEL - `high`
            # for a user-set label, the geocoder's tier otherwise - and NEVER the tier
            # of the grouping, which is derived from objective capture facts and is not
            # in question. Through M8 this was the LITERAL `high`, correct only while a
            # person was the only writer of a non-NULL label; rev 15 said in as many
            # words that it stops being correct the moment a geocoder lands. M9's
            # migration 0006 added `events.label_source` and this is the derivation it
            # exists for - A8's obligation, discharged here and nowhere else.
            tier=EVENT_LABEL_SOURCE_TIER.get(
                event.label_source or "", UNKNOWN_LABEL_SOURCE_TIER
            ),
        )
    # spec 6.1: "{place}: geocode enricher token (city or named place)". THE FIRST
    # DROPPABLE TOKEN THIS PROJECT HAS EVER SUPPLIED - spec 6.2 rule 5's trim order is
    # {scene}, {place}, {people}, and until this line none of the three could be emitted,
    # so rule 5's drop and rule 7's disclosure of it were both unreachable.
    #
    # Absent rather than present-and-empty, for the two reasons {event} gives above: the
    # stem is the same either way but the PROVENANCE is not, and one shape for "this
    # enricher has nothing to say" rather than two. `repo.places_by_file_id` has already
    # refused an empty name, so the `.place` test here is a second reading of the same
    # rule and not the only one.
    if place is not None and place.place:
        facts["place"] = TokenFact(
            value=place.place,
            # spec 6.1 rev 6's write-time postcondition: the row exists, because the id
            # came from the same query in the same transaction that read the name.
            source_table="inferred_facts",
            source_id=place.fact_id,
            tier=PLACE_TIER,
        )
    # spec 6.1: "{scene}: caption enricher's short label (the enricher emits both a `label`
    # <=3 words and a `sentence`; naming uses `label`)". THE FIRST ENTRY IN SPEC 6.2 RULE
    # 5's TRIM ORDER, so this is also the line that makes that order's first step reachable
    # with a real value - {place} made the second reachable in M9 and the first stayed
    # theoretical.
    #
    # Absent rather than present-and-empty, for the two reasons {event} and {place} give
    # above: the stem is the same either way but the PROVENANCE is not, and one shape for
    # "this enricher has nothing to say" rather than two. Spec 6.4's write-time rule already
    # refuses an empty label at the boundary and `captions_by_file_id` refuses it again, so
    # the `.label` test here is a third reading of the same rule and not the only one.
    if scene is not None and scene.label:
        facts["scene"] = TokenFact(
            value=scene.label,
            # spec 6.1 rev 6's write-time postcondition: the row exists, because the id came
            # from the same query in the same transaction that read the label.
            source_table="inferred_facts",
            source_id=scene.fact_id,
            tier=caption_tier,
        )
    # spec 6.1 as amended (A14). THE TIER IS `high` AND IT IS A LITERAL - not a config read,
    # not derived from `[tiers] faces`. A person put this name here; that is user truth, and
    # spec 6.1 rev 14 gives a user-set event label the same reading. `[tiers] faces.floor`
    # gates which DETECTIONS are read (A11) and says nothing about a name a human typed.
    #
    # ABSENT rather than present-and-empty, for the reasons {event}, {place} and {scene} give:
    # the stem is the same either way but the PROVENANCE is not. The guard is on the CAPPED
    # list: `max_people = 0` is legal config, and `people[:0]` names nobody.
    named = people[:max_people]
    if named:
        facts["people"] = TokenFact(
            # `people` arrives sorted by `filename_value` (`repo.people_by_sha256`), so the
            # order the name shows is the order this joins - A14's reason for that key.
            value="-".join(person.filename_value for person in named),
            source_table="persons",
            # A14: "a token naming several people records the LOWEST id" - of those NAMED,
            # not of everyone in the photograph, or the pointer names a person the cap left out.
            source_id=min(person.id for person in named),
            tier="high",
            named=tuple((person.id, person.filename_value) for person in named),
        )
    return facts

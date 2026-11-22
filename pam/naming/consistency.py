"""Plan-time consistency rules and the trust score (design spec section 6.4)."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from pam.catalog.repo import PersonLabel, PlanFile
from pam.naming.sanitize import sanitize_token
from pam.naming.template import EmittedToken
from pam.naming.tokens import TokenFact, min_tier
from pam.scan.objective import parse_filename_date


@dataclass(frozen=True)
class RuleContext:
    """Everything the rules may look at. Pure in, flags out.

    THE LAST FIELD IS THE FIRST TIME A RULE LOOKS BEYOND THE FILE IT JUDGES, and spec 6.4
    rev 18 says so and accepts it - see `_caption_contradicts_geocode` below. Every other
    field here is a fact about this one file.

    No defaults, on `resolve_tokens`' precedent: a caller that forgets to look an operand
    up must get a TypeError, not a rule that quietly never fires.
    """

    capture_dt: str | None
    capture_dt_source: str
    filename: str
    emitted: tuple[EmittedToken, ...]
    # spec 6.4 rev 17's two operands for the caption-vs-geocode rule. `scene` is the caption
    # enricher's LABEL (spec 6.1) as the token carries it, before sanitization; `place` is
    # this file's own live geocoded place name. Either may be None and the rule needs both.
    scene: str | None
    place: str | None
    # The archive's live place-name vocabulary (`repo.live_place_names`). Frozen because it
    # is one read for a whole plan run, shared by every unit in it.
    known_places: frozenset[str]


@dataclass(frozen=True)
class Flag:
    token: str  # token whose tier this caps
    cap: str  # the tier ceiling
    reason: str  # one human sentence for trust_reasons_json (UX spec section 8)
    penalty: int  # trust-score deduction; TRUST_CONSISTENCY unless spec says else


# spec 6.4's trust-score table, as named constants: these are business rules that
# will be re-balanced from users' archives, and re-tuning must not mean grepping the
# planner for magic numbers. The model-confidence row ("token confidence < high bound")
# is live from M12 (design 6.4 rev 27, P11): an OCR date is the first token that carries a
# confidence of its own.
TRUST_NO_DATE = 40
TRUST_FILE_MTIME = 25
TRUST_CONSISTENCY = 15
TRUST_QT_UTC = 10
TRUST_TRUNCATED = 10  # rev 4: replaces TRUST_CONSISTENCY for that flag, not additive
# spec 6.4: "people present (user-confirmed) +5" - an ADDITION, the table's only one. Its
# "people from unconfirmed source" half gets no code: `repo.people_by_sha256` reads only a
# face a human attached to a person, so the case cannot arise (I2).
TRUST_PEOPLE = 5
# Design 6.4 rev 27 (P11): "date read from text in the photo -10", and the table's "token
# confidence < high bound -10". Both are charged through `score`'s `date_deductions`, which
# `naming/dates.py::ocr_date_deductions` builds with their sentences.
TRUST_OCR_DATE = 10
TRUST_BELOW_HIGH = 10

# A14 and A15's two sentences (M11 Task 12). Disclosures, NOT deductions - neither reaches a
# `Flag`, so neither caps a tier or charges a point (spec 6.2 rule 7's closing paragraph).
PEOPLE_NAMED = "Names people you labeled: {names}."
PEOPLE_CAPPED_ONE = (
    "One more person is in this photo than the name has room for (max_people = {cap})."
)
PEOPLE_CAPPED = (
    "{count} more people are in this photo than the name has room for (max_people = {cap})."
)
# Spelled as words up to ten, the register of spec 6.2 rule 7's own example ("Two more
# people..."); `[naming] max_people` tops out at ten, but a photograph's people do not.
_NUMBER_WORDS = ("Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")


def people_reasons(
    people: tuple[PersonLabel, ...], *, max_people: int, emitted: bool
) -> list[str]:
    """A14's "trust_reasons_json names all of them" and A15's cap sentence, or nothing.

    ONLY WHEN THE TOKEN REACHED THE NAME (`emitted`), for N2's reason: a sentence about who
    a name names is false for a name the path budget took the people out of - rule 5's drop
    note says that instead - and a template without `{people}` names nobody. `max_people =
    0` names nobody either, so it is covered by the same guard: nothing is emitted.

    A15 IS NOT A TRUNCATION. No tier moves, no score moves, and `truncated` is not set, so
    spec 8.2's `attention` predicate does not move - the cap is a limit the user asked for.
    """
    named = people[:max_people]
    if not emitted or not named:
        return []
    reasons = [PEOPLE_NAMED.format(names=", ".join(p.filename_value for p in named))]
    extra = len(people) - len(named)
    if extra == 1:
        reasons.append(PEOPLE_CAPPED_ONE.format(cap=max_people))
    elif extra > 1:
        count = _NUMBER_WORDS[extra - 2] if extra <= 10 else str(extra)
        reasons.append(PEOPLE_CAPPED.format(count=count, cap=max_people))
    return reasons


# Design 6.4 (M13, S10 / `m12.md` C3): a unit member's own camera date far from the name's.
# A DISCLOSURE, NOT A `Flag`: a `Flag` always carries a cap and a penalty, and S10's row says
# only "carries a trust reason" - so this goes the way `people_reasons` goes, no tier and no
# score moving.
UNIT_DATE_CONFLICT = (
    "Another file in this group, {rel_path}, was dated {date} by its camera"
    " - more than a year from the date in this name."
)
# The sources a camera wrote (design 5.3/5.5). `filename` and `file_mtime` are not compared,
# and neither is `ocr` (a date pam read in the picture is not the camera's).
OBJECTIVE_SOURCES = frozenset({"exif", "qt_local", "qt_utc"})
# "More than a year" (design 6.4's tolerance): STRICTLY more than 365 days between the two
# CALENDAR dates - the time of day is ignored, so 2019-03-01 / 2020-02-29 (365 days) is not
# flagged and 2019-03-01 / 2020-03-01 (366 days) is.
UNIT_DATE_TOLERANCE = timedelta(days=365)


def _calendar_date(value: str | None) -> date | None:
    if value is None or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def unit_date_reasons(
    members: tuple[PlanFile, ...], *, primary_id: int, emitted: tuple[EmittedToken, ...]
) -> list[str]:
    """S10's sentence for every OTHER member whose objective date is > 1 year from the name's.

    The name's date is the `{date}` token's value AS EMITTED: no date in the name, nothing to
    disagree with. Members are the whole unit, so the sentence is the same on every member's
    item (the unit's reasons are shared, spec 6.3). This is why C3 exists: `m12.md` 4 Q2 found
    the planner never looked at a non-primary member's own camera date.
    """
    name_date = next(
        (_calendar_date(token.value) for token in emitted if token.name == "date"), None
    )
    if name_date is None:
        return []
    reasons = []
    for member in members:
        if member.id == primary_id or member.capture_dt_source not in OBJECTIVE_SOURCES:
            continue
        own = _calendar_date(member.capture_dt)
        if own is not None and abs(own - name_date) > UNIT_DATE_TOLERANCE:
            reasons.append(UNIT_DATE_CONFLICT.format(rel_path=member.rel_path, date=own))
    return reasons


def _video_date_ambiguous(ctx: RuleContext) -> list[Flag]:
    """spec 6.4 (rev 3): a UTC video stamp the filename disagrees with."""
    if ctx.capture_dt_source != "qt_utc":
        return []
    from_name = parse_filename_date(ctx.filename)
    if from_name is None or ctx.capture_dt is None:
        return []
    if from_name[:10] == ctx.capture_dt[:10]:
        return []
    return [
        Flag(
            token="date",
            cap="low",
            reason=(
                "Video time is recorded in UTC and the old filename suggests a "
                "different day - please check this one."
            ),
            penalty=TRUST_CONSISTENCY,
        )
    ]


def _truncated_token(ctx: RuleContext) -> list[Flag]:
    """spec 6.4 (rev 4): the path budget cut a token's value.

    Fires on `not complete` ONLY - never on `rewritten`. The spec's row says
    "truncated to fit the path budget"; rule 4's `-x` rewrite usually lengthens the
    value, so flagging it would print the wrong sentence and charge an unearned -10.
    """
    return [
        Flag(
            token=token.name,
            cap="medium",
            reason="Name shortened to fit the Windows path limit.",
            # The rev-4 amendment: TRUST_TRUNCATED replaces TRUST_CONSISTENCY for
            # this flag. Truncation is cosmetic, not a disagreement between facts.
            penalty=TRUST_TRUNCATED,
        )
        for token in ctx.emitted
        if not token.complete
    ]


# spec 6.2 rules 1-3 turn a run of whitespace into '-' and delete everything outside
# [A-Za-z0-9._-], so a sanitized multiword place is joined by one of these three. Splitting
# on them is what makes the match a WHOLE-TOKEN one: 'York' must not match 'Yorkshire', and
# 'St. Louis' must match a label the sanitizer wrote as 'St.-Louis'.
_TOKEN_SPLIT = re.compile(r"[-_.]+")


def _words(text: str) -> tuple[str, ...]:
    """One string -> its sanitized, case-folded whole tokens (spec 6.4 rev 17).

    Sanitized FIRST and case-folded second, in that order and by spec 6.4's own wording:
    "matches - case-insensitively, as a whole token, after spec 6.2's sanitization". The
    sanitizer is `naming/sanitize.py`'s and not a second reading of rules 1-3 here, so a
    caption and a place name are compared the way the filename would have spelled them.
    """
    return tuple(w.casefold() for w in _TOKEN_SPLIT.split(sanitize_token(text)) if w)


def _contains_run(label: tuple[str, ...], place: tuple[str, ...]) -> bool:
    """Does `place` appear in `label` as a contiguous run of whole tokens?

    A CONTIGUOUS RUN, not a subset: 'New York' matches "sunset over New York" and does not
    match "New buildings in York", which are different claims about the same two words. A
    substring test would match 'York' inside 'Yorkshire' and is what spec 6.4 rev 18 means
    by "*whole token* is where a plausible implementation goes wrong quietly".
    """
    if not place:
        return False
    return any(label[i : i + len(place)] == place for i in range(len(label) - len(place) + 1))


# spec 6.4's trust reason for the rule below. It names BOTH places, because the flag is
# about a disagreement and a sentence naming only one half cannot be acted on. The register
# is `_video_date_ambiguous`'s above - the other rule that surfaces a contradiction for a
# human rather than resolving it.
CAPTION_PLACE_REASON = (
    "The AI scene guess mentions {found}, but this photo's location reads {own} - "
    "please check this one."
)


def _caption_contradicts_geocode(ctx: RuleContext) -> list[Flag]:
    """spec 6.4 (rev 17/18): a caption naming a place that is not this photo's place.

    DEAD SINCE REV 2 AND LIVE HERE. The row needs a caption AND a geocode, and no milestone
    had both until M10. Its undefined term was "a known place token", and rev 17 settles it
    rather than leaving an implementer to guess:

    > "A known place token" means a place name this archive's own catalog holds - the
    > distinct set of `place` values across live `inferred_facts` rows. The rule fires only
    > when BOTH operands are present: the file has a live `place` fact, AND its caption
    > `label` matches - case-insensitively, as a whole token, after spec 6.2's sanitization
    > - a place in that set OTHER THAN ITS OWN.

    Both halves of that last clause are refusals, and both are tested: a caption naming a
    place for a file with NO `place` fact contradicts nothing and is not flagged; a caption
    naming its own file's place AGREES and is not flagged.

    THE ONE NON-LOCAL RULE IN SPEC 6.4'S TABLE (rev 18), accepted rather than overlooked.
    The vocabulary is the archive's own catalog, so adding an unrelated geocoded photograph
    of Paris makes a caption reading "Paris" elsewhere contradictory where it was not.
    There is no local alternative - pam ships no gazetteer and `reverse_geocoder` answers
    *coordinates -> place*, the wrong direction - and it is BOUNDED by what a flag can do:
    this caps a TIER and can never change a NAME, because a `low`-tier token still emits.
    So the worst consequence of the non-locality is that a card moves into or out of
    `attention` and out of `Approve High+Medium`'s reach - a human looks at it, or does
    not - and spec 6.2 rule 6's "same inputs, same name" is untouched.

    `sorted()` over the vocabulary so that an archive holding two contradicting places
    always reports the same one. A set's iteration order is not a rule, and the sentence
    this returns is stored in `trust_reasons_json` and read back by the review UI.
    """
    if ctx.scene is None or ctx.place is None:
        return []
    label = _words(ctx.scene)
    own = _words(ctx.place)
    for name in sorted(ctx.known_places):
        other = _words(name)
        if not other or other == own:
            continue
        if _contains_run(label, other):
            return [
                Flag(
                    token="scene",
                    cap="low",
                    reason=CAPTION_PLACE_REASON.format(found=name, own=ctx.place),
                    penalty=TRUST_CONSISTENCY,
                )
            ]
    return []


# spec 6.4: "adding a rule is a one-row change". M12 ADDS NO RULE, and design 6.4 rev 27
# (P11) is why - the deletion gate is hereby told, as for the stale-people row below:
# - `ocr_date` vs `exif` > 1 year is UNREACHABLE IN v1.0 PER FILE. OCR's `wants()` never
#   reads a file with an EXIF date, and a reading is keyed by content, so every file sharing
#   those bytes has the same EXIF. A milestone that lets OCR read EXIF-dated files inherits
#   the row. ACROSS A UNIT it is reachable - an OCR-dated display primary over a RAW whose
#   EXIF says otherwise (`m12.md` 4 Q2) - and that case is S10's row: `unit_date_reasons`
#   above, a disclosure and not a rule, since it caps nothing.
# - "a token's source fact is superseded" is unreachable at PLAN time (the planner reads live
#   facts only, and OCR supersedes nothing - the newest reading is authoritative), and at
#   APPLY time it is deliberately NOT a refusal: the name in an open plan is the name a human
#   reviewed. J13 refuses for `{people}` because a hidden or purged person withdraws consent;
#   a newer OCR run, a raised floor or a narrowed folder list does not.
# The OCR date's own two score rows are not rules either: they are `score`'s
# `date_deductions`, because they are about the date's SOURCE, not a contradiction.
#
# THE STALE-PEOPLE ROW IS NOT HERE AND WILL NOT BE (spec 6.4 rev 22, A16 - the deletion gate
# is hereby told). It is UNREACHABLE IN v1.0: `{people}` is derived from `face_instances`, so
# a face count of 0 makes it empty and the two operands cannot both hold. The route the row
# describes - a `user_facts` row of kind `'people'` - is written by no v1.0 command. A
# milestone that adds per-photo tagging inherits both that route and this rule.
RULES: tuple[Callable[[RuleContext], list[Flag]], ...] = (
    _video_date_ambiguous,
    _truncated_token,
    _caption_contradicts_geocode,
)


def apply_rules(ctx: RuleContext) -> list[Flag]:
    flags: list[Flag] = []
    for rule in RULES:
        flags.extend(rule(ctx))
    return flags


def token_tiers(
    facts: dict[str, TokenFact], emitted: tuple[EmittedToken, ...], flags: list[Flag]
) -> dict[str, str]:
    """Per-token tier after caps; the item's tier is the min of these (spec 6.3.4)."""
    caps: dict[str, str] = {}
    for flag in flags:
        caps[flag.token] = min_tier([caps.get(flag.token, "high"), flag.cap])
    return {
        token.name: min_tier(
            [
                facts[token.name].tier if token.name in facts else "high",
                caps.get(token.name, "high"),
            ]
        )
        for token in emitted
    }


def score(
    facts: dict[str, TokenFact],
    flags: list[Flag],
    *,
    date_source: str,
    emitted: tuple[EmittedToken, ...],
    date_deductions: tuple[tuple[int, str], ...],
) -> tuple[int, list[str]]:
    """The 0-100 trust score and one human sentence per deduction (spec 6.4).

    date_source and emitted are keyword-only, matching assign_unique_stem in the
    same package. token_tiers() and this function take the same two collections in
    OPPOSITE positional orders and the planner calls them on consecutive lines, so
    a positional `score(facts, flags, "exif", emitted)` says nothing about which
    argument is which - and a swap would type-check.

    Users think "how much should I trust this name?", not "what tier is it?".
    The people line landed in M11 (Task 12).

    `date_deductions` (M12, design 6.4 rev 27 P11) is REQUIRED, on `resolve_tokens`'
    precedent: the OCR date's two rows as `(points, sentence)`, built by
    `naming/dates.py::ocr_date_deductions` and empty for every other source. They are
    charged ONLY when the date token reached the name (N2) and BEFORE the clamp, so the
    `+5` people bonus clamps exactly as it always has.

    N2: the score is derived from `tokens_json`, which holds only the tokens that
    reached the filename - so a deduction may only be charged for a claim the name
    actually makes. `--template "{orig}"` on a qt_utc video used to score 90 with
    "the local date may differ" attached to a filename containing no date at all,
    while token_tiers (which does read `emitted`) correctly rated it high. Same
    predicate as token_tiers, applied to the deductions instead of the caps.
    """
    present = {token.name for token in emitted}
    points = 100
    reasons: list[str] = []
    date = facts.get("date")
    if "date" in present:
        if date is not None and date.value == "":
            points -= TRUST_NO_DATE
            reasons.append("No date could be found, so the name starts with 'undated'.")
        elif date_source == "file_mtime":
            points -= TRUST_FILE_MTIME
            reasons.append(
                "Date read from the file's modification time - no camera date present."
            )
        elif date_source == "qt_utc":
            points -= TRUST_QT_UTC
            reasons.append("Video time is recorded in UTC - the local date may differ.")
        for deduction, sentence in date_deductions:
            points -= deduction
            reasons.append(sentence)
    if "people" in present:
        # No sentence of its own: `people_reasons` names who, and that sentence IS the
        # record of this adjustment. Clamped below like everything else, so it shows only
        # on an item that already lost points.
        points += TRUST_PEOPLE
    for flag in flags:
        if flag.token not in present:
            continue
        points -= flag.penalty
        reasons.append(flag.reason)
    return max(0, min(100, points)), reasons

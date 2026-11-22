"""Spec 6.4's write-time structural validation. Malformed facts are dropped, not stored.

Spec 6.4, quoted because every rule below is one clause of it: "Before any insert, a
fact must be *well-formed*: `value_json` matches the declared schema for its `kind`
(each kind's schema is a module-level constant next to the enricher), confidence is
NULL or in `[0,1]`, `ocr_date` values must actually parse as dates, `place` tokens must
be non-empty after sanitization. Malformed output is logged and dropped - garbage never
enters the catalog." (Rev 27, P7: `ocr_date` is no longer a STORED kind - the date is
derived at plan time by a grammar that emits only calendar dates - so that clause now
binds the grammar, and `date_fields` below has no row using it.)

TWO DEVIATIONS FROM THAT PARAGRAPH, BOTH DELIBERATE AND BOTH REPORTED AT THE TASK
BOUNDARY RATHER THAN HIDDEN.

1. The schemas are one table HERE, not "a module-level constant next to the enricher".
   No enricher module exists yet - M9 Task 7 ships the runner and no enricher at all -
   and this project's own idiom for a vocabulary the whole product has to agree about
   is a declared table in one module (`repo.ENRICHERS`, `journal.OPS`,
   `plan_items.ITEM_STATUSES`, `guards.ALLOWED_MOVE_SITES`). One table is also the only
   arrangement a mutation pass can reach: a constant scattered across five modules
   nobody imports is the shape M6's `ENRICHERS` finding was about.

2. Where spec 8.1 names a KIND but not its SHAPE, the row below is this task's smallest
   reading and is owed a confirmation by the milestone that ships that enricher. Only
   `caption` is written out in the spec ("output parsed to `{label, sentence}`"); the
   rest carry a comment saying what they were read off. This is the same disclosure
   `repo.enrichment._FACT_KINDS` already makes about its own guesses, and it is why a
   guess is a comment here rather than an assumption.

WHAT IS NOT HERE, ON PURPOSE. There is no `geocode` row. `_FACT_KINDS` currently maps
the `geocode` enricher to a `geocode` kind, which the M9 plan's clarification 1 records
as wrong - the kind is `place`, and Task 9 corrects the map. Adding a `geocode` schema
here would make that defect insert facts successfully instead of dropping them, which
is the wrong direction: an unknown kind must fail loudly at the boundary rather than
quietly acquire a shape.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pam.naming.sanitize import sanitize_token


@dataclass(frozen=True)
class FactSchema:
    """The declared shape of one kind's `value_json` (spec 6.4).

    `fields` is exact, not a minimum: a key the schema does not declare is as much a
    sign of a misparsed model response as a missing one, and "matches the declared
    schema" is the stricter reading. Storing an undeclared key would also make it
    unreachable - nothing downstream would ever look for it - so it is garbage in spec
    6.4's sense even when it is well-meant.

    `word_caps` is spec 6.1's *"the enricher emits both a `label` <=3 words and a
    `sentence`"*, made a WRITE-TIME RULE by spec 8.1 rev 17 (M10 amendment A3) and
    declared here beside the schema because that is where rev 17 says it belongs. A
    field named in it must be non-empty and must carry no more than its cap in
    whitespace-separated words. **It is never truncated to fit** - spec 6.2 rule 5's
    truncation is the path budget acting on a name pam derived, while cutting a model's
    answer down to three words is pam inventing a label the model did not propose.

    `line_lists` is design 8.1 rev 27's OCR shape (P7, M12 Task 13): each field named in
    it is a list of objects with EXACTLY `text` (a str) and `score` (a number, not a bool,
    in [0, 1]). It is a per-kind rule beside `word_caps`, on that field's precedent: the
    shape is a property of the KIND, and the runner validates every kind the same way.

    `requires_confidence` is spec 6.4's confidence rule read in the other direction, and
    it exists because a kind's DESTINATION can make NULL impossible. Spec 6.4 says
    confidence "is NULL or in [0,1]" and that is right for `inferred_facts`, whose column
    is nullable; `face_instances.det_confidence` is NOT NULL (spec 4), so a detection
    arriving with no confidence has nowhere to be written. Dropping it at the boundary
    with a sentence is spec 6.4's own answer for output that will not fit the shape it
    declares - the alternative is an `IntegrityError` out of the sink, which ends the
    whole run over one bad detection.

    THIS IS THE ONE DEVIATION FROM THE M11 PLAN'S TASK 8 STEP 6 BLOCK, and it is
    disclosed rather than quiet: that block spells the two new schemas with `fields` only.
    The flag is a property OF A KIND, beside `place_tokens`, `date_fields` and
    `word_caps`, which is the shape this table already has.
    """

    fields: Mapping[str, type]
    place_tokens: tuple[str, ...] = ()
    date_fields: tuple[str, ...] = ()
    word_caps: Mapping[str, int] = field(default_factory=dict)
    line_lists: tuple[str, ...] = ()
    requires_confidence: bool = False


FACT_SCHEMAS: Mapping[str, FactSchema] = {
    # Spec 8.1, written out: "output parsed to `{label, sentence}`".
    # Spec 6.1's "<=3 words" is enforced here, not in the enricher (spec 8.1 rev 17).
    # `sentence` carries NO cap: it is spec 6.1's prose half and the review UI shows it.
    "caption": FactSchema(fields={"label": str, "sentence": str}, word_caps={"label": 3}),
    # Design 8.1 rev 27 (P7), CONFIRMED by M12 Task 13 - the enricher that writes it: ONE
    # stored kind, `{"lines": [{"text": <str>, "score": <number in [0, 1]>}, ...]}`, possibly
    # empty. It is the reading AND the per-file coverage marker. `ocr_date` is NOT a stored
    # kind (rev 27): the date is derived at plan time, so its row here is deleted, and 6.4's
    # "`ocr_date` values must actually parse as dates" is now a property of the grammar.
    "ocr_text": FactSchema(fields={"lines": list}, line_lists=("lines",)),
    # Spec 8.1: "dHash". A hex digest; the shape is this task's smallest reading.
    "phash": FactSchema(fields={"hash": str}),
    # Spec 6.1's `{place}` token, with spec 6.4's rule about it: "`place` tokens must
    # be non-empty after sanitization".
    "place": FactSchema(fields={"place": str}, place_tokens=("place",)),
    # A7's per-file marker. {"count": N}, INCLUDING N = 0 - a landscape the detector
    # looked at is covered, and without this row the cache cannot tell it from an
    # unprocessed file.
    "faces": FactSchema(fields={"count": int}),
    # A7's detection, routed to `face_instances` by the sink (design 4). `geometry` is
    # A13's versioned document. The embedding is raw float32 bytes - it goes into a BLOB
    # and never through `fact_value_json`. `det_confidence` is NOT a field: it is the
    # fact's `confidence`, which is what design 6.4's [0,1] check is about - and
    # `requires_confidence` is that column being NOT NULL, nothing more.
    "face": FactSchema(fields={"geometry": dict, "embedding": bytes}, requires_confidence=True),
}


def _confidence_error(confidence: float | None, required: bool) -> str | None:
    """Spec 6.4: confidence is NULL or in [0, 1].

    `bool` is rejected before the range check because `True` is an `int`, passes
    `0 <= x <= 1`, and would be stored as a confidence of 1.0 - a false certainty
    arriving through a type confusion. NaN fails the range check on its own: every
    comparison with NaN is False, so `0.0 <= nan` is already False and no explicit
    `isnan` is needed or wanted.
    """
    if confidence is None:
        return None if not required else "confidence is required for this kind and is NULL"
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return f"confidence is {type(confidence).__name__}, not a number"
    if not 0.0 <= float(confidence) <= 1.0:
        return f"confidence {confidence!r} is outside [0, 1]"
    return None


def _field_error(name: str, declared: type, value: object) -> str | None:
    """One field against its declared type. `bool` never satisfies a numeric field."""
    if isinstance(value, bool) and declared is not bool:
        return f"field {name!r} is a bool, not {declared.__name__}"
    if not isinstance(value, declared):
        return f"field {name!r} is {type(value).__name__}, not {declared.__name__}"
    return None


def fact_error(kind: str, value: Mapping[str, Any], confidence: float | None) -> str | None:
    """Why this fact is not well-formed, or None if it is (spec 6.4).

    A SENTENCE rather than a boolean, because spec 6.4 says malformed output is
    "logged and dropped" and a log line saying only that something was dropped cannot
    be acted on. The sentence names the kind, the field and what was wrong with it.

    The order of the checks is the order of the clauses in spec 6.4, with ONE exception
    that is load-bearing: the schema is looked up FIRST, because whether a NULL
    confidence is legal is a property of the kind (`requires_confidence`, above) and
    cannot be decided without it. Everything after that is checked in spec order and the
    first failure is the one reported. A kind with no schema is reported as such even
    when its confidence is also wrong - the unknown kind is the more useful sentence.
    """
    schema = FACT_SCHEMAS.get(kind)
    if schema is None:
        return f"kind {kind!r} has no declared schema"
    bad = _confidence_error(confidence, schema.requires_confidence)
    if bad is not None:
        return bad
    if not isinstance(value, Mapping):
        return f"value for {kind!r} is {type(value).__name__}, not an object"
    declared = set(schema.fields)
    present = set(value)
    if missing := sorted(declared - present):
        return f"{kind!r} is missing {missing}"
    if extra := sorted(present - declared):
        return f"{kind!r} carries undeclared {extra}"
    for name, want in schema.fields.items():
        if (bad := _field_error(name, want, value[name])) is not None:
            return f"{kind!r}: {bad}"
    for name, cap in schema.word_caps.items():
        # `str.split()` with no argument is exactly "whitespace-separated", and it
        # returns [] for a string that is empty or all whitespace - which is spec 8.1
        # rev 17's other half ("whose `label` is empty OR carries more than three").
        words = str(value[name]).split()
        if not words:
            return f"{kind!r}: {name} is empty"
        if len(words) > cap:
            return f"{kind!r}: {name}={value[name]!r} is {len(words)} words, over the {cap} allowed"
    for name in schema.place_tokens:
        if not sanitize_token(str(value[name])):
            return f"{kind!r}: place token {name!r} is empty after sanitization"
    for name in schema.line_lists:
        if (bad := _line_list_error(value[name])) is not None:
            return f"{kind!r}: {name} {bad}"
    for name in schema.date_fields:
        try:
            datetime.fromisoformat(str(value[name]))
        except ValueError:
            return f"{kind!r}: {name}={value[name]!r} does not parse as a date"
    return None


def _line_list_error(lines: list[Any]) -> str | None:
    """P7: every entry an object with EXACTLY `text` (str) and `score` (number, not bool, [0, 1])."""
    for index, line in enumerate(lines):
        if not isinstance(line, Mapping) or set(line) != {"text", "score"}:
            return f"entry {index} is not an object with exactly text and score"
        if not isinstance(line["text"], str):
            return f"entry {index}'s text is {type(line['text']).__name__}, not str"
        score = line["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return f"entry {index}'s score is {type(score).__name__}, not a number"
        if not 0.0 <= float(score) <= 1.0:
            return f"entry {index}'s score {score!r} is outside [0, 1]"
    return None


def fact_value_json(value: Mapping[str, Any]) -> str:
    """The `value_json` column for a fact that has already passed `fact_error`.

    `sort_keys=True` for the reason `repo.events._params_json` gives about its own
    identity string: two spellings of the same object must not read as two values.
    Nothing here compares `value_json` yet, but `inferred_facts` is content-addressed
    and a stable serialization is what lets a later milestone compare one at all.
    """
    return json.dumps(dict(value), sort_keys=True)

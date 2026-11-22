"""Spec 8.2 rev 18's `fact_models`, and vision Q3 rev 4's second half (M10 Task 10).

WHY A MODULE OF ITS OWN. `disclosure.py`'s docstring records the rule this follows - "the
next thing that would push one over goes in a new module" - and `api.py` was at 280
executable lines against the project's ~300 budget before this task. It cannot live in
`disclosure.py` either: that module imports `api._bad_request`, so `api` importing it back
would be a cycle. What is here is the derivation and the COPY; the SQL is
`catalog/repo/enrichment.facts_by_id` and the token document is the planner's.

`tokens_json` IS SERVED EXACTLY AS THE PLANNER WROTE IT - unedited, unreordered and
un-annotated (spec 8.2). Nothing here writes into a token. `fact_models` sits BESIDE the
items, and that separation is the point rather than a nicety: spec 6.1's per-token record
is `(value, source_table, source_id, tier)` and carries no model, so annotating the token
would be pam asserting a shape spec 6.1 does not define.

WHAT VISION Q3 rev 4 ACTUALLY ASKS FOR, because this module is the whole of pam's answer:

> every AI-derived token in a proposed name is traceable to a cached model output - **the
> model's name and version are shown at review** - and carries **either** the model's
> confidence score **or**, where the model emits none, **the tier that confidence-lessness
> produced, together with the sentence saying so**.

Two halves, and each has its own field. `fact_models` is the first half; `confidence_note`
is the second. **Never an invented proxy** (amendment A7): a fabricated confidence would
be worse than none, because vision principle 2 forbids provenance that does not match the
artifact, and a number manufactured to fill a column is exactly that.

EVERY STRING HERE IS SERVER-SIDE FOR THE REASON `trust_label` IS - and `trust_label` moved
here in the same task, so the three words UX spec 5 assigns to the three tiers are spelled
once in one module. Nothing in this project lints, types or executes `app.js` (`m7.md`
F4), so a band table or a sentence written there could not be tested and could not be
reached by the mutation gate. The page renders these strings verbatim and holds no tier
vocabulary of its own.
"""

from typing import Any

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem

# Spec 6.1's `source_table` value that means "this token came out of an enricher". The
# other four - `capture`, `files`, `events` and `planner` - name rows no model produced,
# and a token carrying one of them gets no `fact_models` entry (spec 8.2 rev 17).
FACT_SOURCE_TABLE = "inferred_facts"

# UX spec 5 rev 8 / spec 6.4 rev 14: THE TRUST LABEL IS THE TIER'S WORD, never the
# score's. The old 85/60 score bands are deleted - they asserted an equivalence spec 6.4
# does not provide, and the collision is CONSTRUCTIBLE from rules the spec already
# carries: a `qt_utc` video with three truncated tokens scores 70 at tier `medium`, and a
# `file_mtime` photo scores 75 at tier `low`, so `min(score over medium) < max(score over
# low)` and NO threshold separates them. Measured on a real-world library as HT-M7's P2:
# 35 cards read "High trust" beside a button that excluded every one of them.
#
# The percentage and the bar's FILL are still `trust_score` - nothing measurable is lost.
# Only the WORD stops contradicting the button.
TRUST_LABELS = {"high": "High trust", "medium": "Good", "low": "Needs review"}

# Vision Q3 rev 4's sentence, and it says the two things Q3 names: WHICH model gave no
# score, and WHICH tier that produced. One sentence, from the server.
#
# "capped" is the honest verb: spec 8.1's rule for a confidence-less enricher is "tier by
# config default (`medium` cap for confidence-less enrichers)", so the tier below is a
# ceiling the enricher's token could not rise above - not a measurement of this photograph.
#
# DESIGN 6.4 REV 27 (P11): SCOPED TO THE MODELS IT NAMES. Rev 26's "the AI-derived parts of
# this name are capped at ..." swept every AI token into the cap - on a card holding a
# `medium` OCR date (which carries its own score) and a caption capped at `low`, it said the
# OCR date was capped at Needs review. `{their}` is "its" for one model and "their" for more.
CONFIDENCE_NOTE = (
    "No confidence score is available from {models}; {their} contribution to this name is"
    " capped at {label}."
)

# What separates two model identities inside one `CONFIDENCE_NOTE`. Sorted and joined
# rather than listed in token order, so the sentence is stable across a re-plan that
# reorders the template - spec 6.2 rule 6's determinism, applied to copy.
MODEL_JOIN = " and "


def trust_label(tier: str) -> str:
    """UX spec 5's word for a tier, or the tier itself when it is not one of the three.

    An unrecognised tier is rendered as itself rather than raising, on `app.js`'s own
    precedent for the tier BADGE: `plan_items.tier` carries no CHECK constraint, and a
    review page that 500s on a row a future milestone wrote would hide the whole plan
    over one word. There is nothing better to say about a tier this spec does not name
    than to say it.
    """
    return TRUST_LABELS.get(tier, tier)


def _scored(token: dict[str, Any]) -> bool:
    """Design 6.4 rev 27 (P11, pass 2's A2): does this token carry a VALID NUMERIC confidence?

    A real number in `[0, 1]` - `0.0` INCLUDED, a bool refused (`True` is an `int`), NaN
    refused by the range - and never the key's presence or its truthiness. The planner
    writes `"confidence"` only on a token that has its own score (today, an OCR date's line).
    """
    value = token.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0.0 <= value <= 1.0


def _fact_entries(tokens: Any) -> list[tuple[int, str, bool]]:
    """`(inferred_facts id, the token's tier, whether it carries its own score)`."""
    if not isinstance(tokens, list):
        return []
    found = []
    for token in tokens:
        if not isinstance(token, dict) or token.get("source_table") != FACT_SOURCE_TABLE:
            continue
        source_id = token.get("source_id")
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            continue
        tier = token.get("tier")
        found.append((source_id, tier if isinstance(tier, str) else "", _scored(token)))
    return found


def fact_tokens(tokens: Any) -> list[tuple[int, str]]:
    """`(inferred_facts id, the tier the planner gave that token)` for the AI tokens.

    ONE PASS, ONE PREDICATE, TWO CONSUMERS. `fact_models` needs the ids and
    `confidence_note` needs the tiers, and two independently written copies of "which of
    these tokens came from an enricher" is how the map and the sentence come to disagree
    about the same card - the reason spec 8.2 rev 14 made `edited` a response field.

    A token whose `source_table` is anything else contributes nothing, and so does a token
    whose `source_id` is not an integer: `review._parsed` hands this module whatever
    `json.loads` returned from a column pam wrote with `json.dumps`, and a damaged row must
    cost one card its model name rather than 500 the whole review page - the ruling that
    module already records for the same document.

    `bool` is refused explicitly because `True` is an `int` in Python and is not a row id,
    exactly as `review._orientation` refuses it for the same reason.

    ONE PREDICATE STILL (M12 Task 10): `_fact_entries` is the pass, and this and
    `confidence_note` both read it. A SCORED token stays here - `fact_models` names its model
    whatever its score (design 6.4 rev 27, P11) - and only the note skips it.
    """
    return [(source_id, tier) for source_id, tier, _ in _fact_entries(tokens)]


def model_label(fact: repo.FactModel) -> str:
    """Spec 8.2 rev 17's value: `"<model_name> <model_version>"`, exactly that shape."""
    return f"{fact.model_name} {fact.model_version}"


def fact_models(facts: dict[int, repo.FactModel]) -> dict[int, str]:
    """Spec 8.2 rev 22's per-CARD field: `inferred_facts` id -> the model that wrote it.

    REV 22 (A17) CHANGED THE CALLER, NOT THIS FUNCTION. Through rev 21 `api.py` passed the
    whole page's facts and served one map beside `items`; it now passes the subset THIS
    item's tokens point at. The shape is unchanged, which is why the change is a caller's:
    a map keyed by fact id is already per-token, so narrowing its domain to one card's
    tokens is the only thing the amendment needs.

    KEYED BY FACT ID, NEVER BY FILE ID OR ITEM ID, because that is what the page holds: a
    token carries `source_id`, and one file can carry several facts from several models
    while one fact can be shared by every duplicate of one photograph (the table is
    content-addressed). A map keyed by the file would be unable to say which of two tokens
    came from which model, and would answer the wrong model for the other one.
    """
    return {fact_id: model_label(fact) for fact_id, fact in facts.items()}


def confidence_note(item: ReviewItem, facts: dict[int, repo.FactModel]) -> str | None:
    """Vision Q3 rev 4's second half for one card, or None when Q3's first half suffices.

    None means one of two things and neither owes a sentence: the item's name carries no
    AI-derived token at all, or every model behind one reported a confidence score - in
    which case Q3 is met by that score and inventing this sentence beside it would be
    saying something untrue. **M10 ships no enricher that emits a confidence** - `geocode`
    and `caption` both store NULL, which spec 8.1 records for each - so the second branch
    is unexercised by any command pam has today and is pinned by a seeded row instead.

    THE TIER IS READ, NOT RECOMPUTED. Every token in `tokens_json` carries the tier the
    planner assigned it (spec 6.1), and the lowest tier among the confidence-less AI tokens
    is what confidence-lessness actually produced on this card. The ITEM's tier is
    deliberately not used: spec 6.3 rule 4 makes it the minimum across ALL tokens, so a
    date read from `file_mtime` could drag it to `low` and this sentence would blame the
    model for a cap the model did not cause.

    `repo.TIERS` is the spec's own order (`high`, `medium`, `low`), so "lowest" is the last
    of them present. A tier this spec does not name sorts after all three and renders as
    itself, on `trust_label`'s precedent.

    A TOKEN THAT CARRIES ITS OWN SCORE IS NOT CONFIDENCE-LESS (design 6.4 rev 27, P11). An OCR
    date's `ocr_text` fact has a NULL fact-level confidence by design (P7) - the score lives on
    the line - so the token records it and is skipped here; the sentence names only the
    models it is about, and its label is only their tokens' lowest tier.
    """
    models: set[str] = set()
    tiers: list[str] = []
    for source_id, tier, scored in _fact_entries(item.tokens):
        fact = facts.get(source_id)
        if scored or fact is None or fact.confidence is not None:
            continue
        models.add(model_label(fact))
        tiers.append(tier)
    if not models:
        return None
    return CONFIDENCE_NOTE.format(
        models=MODEL_JOIN.join(sorted(models)),
        their="its" if len(models) == 1 else "their",
        label=trust_label(_lowest_tier(tiers)),
    )


def _lowest_tier(tiers: list[str]) -> str:
    """The last of `repo.TIERS` present, with anything it does not name sorting after."""
    return max(tiers, key=lambda tier: (_tier_rank(tier), tier))


def _tier_rank(tier: str) -> int:
    return repo.TIERS.index(tier) if tier in repo.TIERS else len(repo.TIERS)

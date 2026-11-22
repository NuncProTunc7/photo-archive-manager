"""spec 6.4: one unit's flags, tiers, trust score, `tokens_json` and `trust_reasons_json`.

M12 Task 8. This is `naming/planner.py::_plan_unit`'s assessment block moved out VERBATIM
ahead of M12's OCR date (P9), which changes exactly this block; `planner.py` was four
executable lines from the ~300 budget. On the precedent of M10 C2's `naming/events.py` and
M11's `naming/families.py`: pure, single-caller, cut first as a move.
"""

import json
from dataclasses import dataclass

from pam.catalog import repo
from pam.config import TierBounds
from pam.naming import consistency
from pam.naming.dates import ResolvedDate, ocr_date_deductions
from pam.naming.template import StemResult
from pam.naming.tokens import TokenFact, min_tier
from pam.naming.units import Unit


@dataclass(frozen=True)
class Assessment:
    item_tier: str
    trust_score: int
    tokens_json: str
    trust_reasons_json: str


def assess_unit(
    unit: Unit,
    facts: dict[str, TokenFact],
    result: StemResult,
    people: tuple[repo.PersonLabel, ...],
    *,
    scene: repo.CaptionFact | None,
    place: repo.PlaceFact | None,
    known_places: frozenset[str],
    max_people: int,
    date: ResolvedDate,
    ocr_bounds: TierBounds,
    name_evidence: str | None,
) -> Assessment:
    """Flags, tiers, the trust score, `tokens_json` and `trust_reasons_json` for one unit.

    MOVED VERBATIM from `naming/planner.py::_plan_unit` (M12 Task 8) ahead of M12's OCR date,
    which changes exactly this block. `planner.py` was four executable lines from the ~300 budget.

    M12 Task 10 (design 6.4 rev 27, P11): `date` is the primary's `ResolvedDate`. Its two score
    rows go through `consistency.score`'s `date_deductions`; its `reasons` - condition 4's
    disclosure, or the provenance sentence every emitted OCR date carries - are written FIRST
    in `trust_reasons_json`, and only when the date token reached the name (N2). A token that
    carries its own `confidence` records it in its `tokens_json` entry, and no other does.

    `name_evidence` (design 6.4, M13 H8): when pam wrote the primary's current name, the
    name it had before pam - `repo.name_evidence`. The "video date ambiguous" rule parses it
    instead of the current name, which is pam's own date and would compare a date with itself.
    """
    source = unit.primary.capture_dt_source or "none"
    flags = consistency.apply_rules(
        consistency.RuleContext(
            capture_dt=unit.primary.capture_dt,
            capture_dt_source=source,
            filename=unit.primary.filename if name_evidence is None else name_evidence,
            emitted=result.emitted,
            # spec 6.4 rev 17's two operands, both read off the PRIMARY - the same file
            # whose facts name the whole unit. The RAW values, not the sanitized ones: the
            # rule sanitizes both sides itself, and the reason it writes must be able to
            # print the name the catalog holds.
            scene=scene.label if scene is not None else None,
            place=place.place if place is not None else None,
            known_places=known_places,
        )
    )
    tiers = consistency.token_tiers(facts, result.emitted, flags)
    item_tier = min_tier(tiers.values())
    points, reasons = consistency.score(
        facts,
        flags,
        date_source=source,
        emitted=result.emitted,
        date_deductions=ocr_date_deductions(date, ocr_bounds),
    )
    tokens_json = json.dumps(
        [
            {
                "token": token.slot,
                "resolved_from": token.name,
                "value": token.value,
                "source_table": facts[token.name].source_table
                if token.name in facts
                else "planner",
                "source_id": facts[token.name].source_id if token.name in facts else None,
                "tier": tiers[token.name],
                "truncated": not token.complete,
                # J13 (design 7.0 rev 26): the people this name was built from, frozen so
                # `pam apply` can refuse a unit whose people have changed since.
                **({"named": facts["people"].named} if token.name == "people" else {}),
                # design 6.4 rev 27 (P11): the token's OWN score - an OCR date's line - so the
                # review page's confidence note can tell a scored token from a capped one.
                # Only when there is one, so no existing token's JSON changes.
                **(
                    {"confidence": facts[token.name].confidence}
                    if token.name in facts and facts[token.name].confidence is not None
                    else {}
                ),
            }
            for token in result.emitted
        ],
        ensure_ascii=False,
    )
    # spec 6.2 rule 7 (M9 Task 10): the path budget's token drops, disclosed in the same
    # pass that performed them. APPENDED TO `reasons` AND NOT ROUTED THROUGH A `Flag`,
    # because a disclosure is not a deduction: rule 7's closing paragraph and M8's A5 both
    # say a drop charges no penalty and caps no tier, and spec 6.4's table gains no row.
    # `consistency.score` filters flags to tokens PRESENT in `emitted` besides, so a
    # dropped token could never have reached the user through that channel at all.
    # A14/A15's people sentences sit between the deductions and the drop notes, and only
    # when the token reached the name - the same `emitted` N2 makes `score` read.
    emitted_people = any(token.name == "people" for token in result.emitted)
    reasons += consistency.people_reasons(people, max_people=max_people, emitted=emitted_people)
    # Design 6.4 (M13, S10): a member's own camera date far from the name's - a disclosure too.
    reasons += consistency.unit_date_reasons(
        unit.members, primary_id=unit.primary.id, emitted=result.emitted
    )
    if any(token.name == "date" for token in result.emitted):
        reasons = [*date.reasons, *reasons]
    reasons_json = json.dumps(reasons + list(result.notes), ensure_ascii=False)
    return Assessment(
        item_tier=item_tier,
        trust_score=points,
        tokens_json=tokens_json,
        trust_reasons_json=reasons_json,
    )

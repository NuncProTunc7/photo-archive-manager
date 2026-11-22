"""Token assembly: template string -> filename stem (design spec section 6.1)."""

import re
from dataclasses import dataclass

from pam.errors import TemplateError
from pam.naming.sanitize import (
    apply_reserved_stem_rule,
    reserved_head,
    sanitize_token,
    stem_budget,
)

TOKEN_NAMES = ("date", "event-or-scene", "event", "scene", "people", "place", "seq", "orig")
VALUE_TOKENS = tuple(t for t in TOKEN_NAMES if t != "event-or-scene")
_ALIASED = ("event", "scene")  # what {event-or-scene} may resolve to, in order
# The trailing '_' is cosmetic (the assembler always joins with exactly one), so
# it is matched and discarded rather than captured. The name class is wide so a
# wrong-case token reports "unknown token" instead of "malformed".
_TEMPLATE = re.compile(r"\{([A-Za-z0-9-]+)\}_?")
_TRIM_ORDER = ("scene", "place", "people")  # spec 6.2 rule 5 drop order
_EDGES = re.compile(r"^[.\-_]+|[.\-_]+$")
FALLBACK_STEM = "unnamed"  # only when every token is empty (spec 6.1 postcondition)

# SPEC 6.2 RULE 7'S SECOND SURFACE (M9), AND THE COPY APPEARS ONCE, HERE. Rule 7 rev 14
# names two surfaces and makes them one rule so that implementing either does not let the
# other be forgotten: the review UI's name editor (M8, `webui/disclosure.py`) and "rule
# 5's token drop (M9)" - "a token trimmed away entirely gets a sentence in
# `trust_reasons_json`, rendered by the Why? panel".
#
# WHY THE SENTENCE IS BUILT HERE AND NOT BY THE PLANNER. The drop is performed in this
# module, by `build_stem`, and this is the only place that knows WHICH token went and WHY
# - the planner holds the values it asked for and the stem it got back, and a set
# difference over those two would say THAT something is missing without being able to say
# it was the path budget that took it. `webui/disclosure.py`'s docstring makes the same
# argument for the editor's half ("nothing here diffs two strings"), and the counter-shape
# is `StemResult.dropped`, which M8 deleted: a record handed across a seam for someone
# else to interpret later. `notes` is not that record. It is the finished sentence, it has
# a production consumer in the commit that introduces it (`planner._plan_unit` writes it
# into `trust_reasons_json`), and nothing downstream re-derives anything from it.
#
# A DISCLOSURE IS NOT A DEDUCTION (rule 7's closing paragraph, and M8 amendment A5). This
# sentence never reaches `consistency.Flag`, so it charges no trust-score penalty and caps
# no tier, and spec 6.4's table gains no row. Truncation is charged -10 because the name
# makes a MANGLED claim; a dropped token makes no claim at all. What is forbidden is the
# silence.
#
# The register is `webui/disclosure.py`'s: "pam <verb> ... to fit the Windows path limit.",
# which is also spec 6.4's wording for the truncation reason beside it in the same panel.
# No em dash, because this sentence has no second clause - the one in `disclosure.py` is
# spelled with the spec's em dash and the reasoning there is about that clause, not about
# a project-wide rule.
DROPPED_NOTE = "pam left {label} out of this name to fit the Windows path limit."

# One human noun phrase per token spec 6.1 names, article included, so the sentence above
# reads for every one of them ("the people" and "the date" want different articles from
# nothing at all). Keyed by the DROP LABEL - the token that supplied the value - which is
# `event` or `scene` and never the `{event-or-scene}` slot, so this map covers exactly
# `VALUE_TOKENS`. Written out here from spec 6.1's token list rather than derived from
# `VALUE_TOKENS`, which would make the map complete by construction and say nothing.
TOKEN_LABELS = {
    "date": "the date",
    "event": "the event name",
    "scene": "the scene",
    "people": "the people",
    "place": "the place name",
    "seq": "the number",
    "orig": "the original name",
}


def parse_template(template: str) -> tuple[str, ...]:
    """'{date}_{seq}' -> ('date', 'seq'); TemplateError on anything else.

    Grammar (locked): one or more {token} groups joined by an optional
    single '_'. No other literal text; no repeats; {event-or-scene} excludes
    {event}/{scene}.
    """
    names: list[str] = []
    pos = 0
    while pos < len(template):
        m = _TEMPLATE.match(template, pos)
        if m is None:
            raise TemplateError(
                f"Naming template is malformed at position {pos}: {template!r}. "
                "Use tokens like {date}, joined with _"
            )
        name = m.group(1)
        if name not in TOKEN_NAMES:
            raise TemplateError(
                "Unknown template token {"
                + name
                + "}. Valid tokens: "
                + ", ".join("{" + t + "}" for t in TOKEN_NAMES)
            )
        names.append(name)
        pos = m.end()
    if not names:
        raise TemplateError("Naming template has no tokens.")
    if len(set(names)) != len(names):
        raise TemplateError("Naming template repeats a token.")
    if "event-or-scene" in names:
        clash = [n for n in _ALIASED if n in names]
        if clash:
            raise TemplateError(
                "Naming template uses {event-or-scene} together with "
                + ", ".join("{" + n + "}" for n in clash)
                + " - that would put the same value in the name twice. "
                "Use either {event-or-scene} or {event}/{scene}, not both."
            )
    return tuple(names)


@dataclass(frozen=True)
class EmittedToken:
    name: str  # token that supplied the value: 'event' or 'scene', never the alias
    slot: str  # template position it filled ('event-or-scene' when aliased)
    value: str  # the text AS IT APPEARS IN THE STEM (spec 6.1 rev 4)
    complete: bool  # False ONLY when the path budget SHORTENED this value
    # spec 6.2 rule 4 rewrote this value ('con' -> 'con-x'). A separate signal from
    # `complete`: the -x rewrite usually LENGTHENS the value, so reporting it as
    # truncation earns the item spec 6.4's "Name shortened to fit the Windows path
    # limit" reason, a -10 and a medium cap that none of it deserves.
    # KEPT DELIBERATELY (M5 Task 3 / N7 deletion sweep): mandated by M5 Task 1 in
    # this same milestone and not yet read in production. Surfacing it would mean
    # adding a field to tokens_json, which is a wire-format change needing a spec
    # amendment (spec 6.1) - so it waits, it does not get deleted as unused.
    rewritten: bool = False


@dataclass(frozen=True)
class StemResult:
    stem: str
    # M8 A5 deleted `dropped` from here, and M9 does NOT put it back. The record was
    # structurally unreachable then - rule 5's trim order is {scene}/{place}/{people} and
    # `resolve_tokens` supplied only date/seq/orig - and a field kept alive for a later
    # consumer is exactly the shape that gate exists to remove. M9 Task 10 makes the trim
    # order reachable ({place} is the first droppable token this project has ever had) and
    # discharges the obligation spec 6.2 rule 7 took over: `notes` below carries the
    # SENTENCE, not the record, and `planner._plan_unit` writes it into
    # `trust_reasons_json` in the same pass that built the stem.
    emitted: tuple[EmittedToken, ...]  # provenance for tokens_json, in stem order
    # Spec 6.2 rule 7's sentences about what the path budget removed - one per token that
    # entered assembly with a value and did not reach the stem, empty for every name pam
    # did not have to shorten (which is nearly all of them).
    #
    # IT COVERS BOTH SILENT-LOSS PATHS, and the second one is the older finding. Rule 5's
    # trim-order drop is the one rule 7 names. `_cut`'s budget-exhausted branch (room <= 0)
    # cuts a remaining part away ENTIRELY and has been reachable since M4, by the tokens
    # `resolve_tokens` really supplies: measured at M8 Task 1, order ("date", "seq") at
    # budget 9 returns '2019-07-0' with ONE emitted row - {seq} gone from the stem, from
    # provenance and from any record alike, while {date} at least carried complete=False,
    # so nothing downstream fired: no medium cap, no reason, trust 100, tier high. Both are
    # "a token trimmed away entirely" in rule 7's words and both are the path budget doing
    # it, so both get the same sentence. The set below is computed against what `_finalize`
    # actually emitted rather than against what `_cut` handed it, so `_finalize`'s
    # empty-part filter cannot grow a silent third case either.
    #
    # THAT LAST CLAUSE IS FUTURE-PROOFING, NOT COVERAGE, and the Task 10 review measured
    # the difference: 200,000 random draws of sanitized token values against budgets 1-25
    # found NO input where reading against `emitted` differs from reading against `_cut`'s
    # return, because the sanitizer's edge-stripping gets there first. So no test can fail
    # if this is written the other way today. It is written this way because the filter is
    # one sanitizer change away from mattering, and the failure it would produce is a
    # SILENT one - which is the thing rule 7 exists to forbid.
    notes: tuple[str, ...]


# A part carries [name, slot, value, complete, rewritten]. The two flags are
# separate signals and both use "1" for true / "" for false: `complete` says the
# budget did NOT shorten this value, `rewritten` says rule 4 altered it.
_COMPLETE, _REWRITTEN = 3, 4


def _cut(parts: list[tuple[str, str, str]], budget: int) -> list[list[str]]:
    """Trim the joined parts to `budget` chars -> [name, slot, value, flags...]."""
    out: list[list[str]] = []
    used = 0
    for name, slot, value in parts:
        if out:
            used += 1  # the '_' this part will be joined with
        room = budget - used
        if room <= 0:
            break  # cut away entirely: not in the filename, so never emitted
        if len(value) <= room:
            out.append([name, slot, value, "1", ""])
            used += len(value)
        else:
            out.append([name, slot, value[:room], "", ""])
            break
    return out


def _shorten_last(parts: list[list[str]]) -> None:
    """Drop one character from the last part - the "no room for -x" branch.

    A named function rather than three inline statements so a test can replace it
    with a no-op and prove the bound below RAISES instead of spinning. That no-op
    is exactly the mutant that spun during M5 (open question 10).
    """
    parts[-1][2] = parts[-1][2][:-1]
    parts[-1][_COMPLETE] = ""
    parts[-1][_REWRITTEN] = "1"


def _finalize(parts: list[list[str]], budget: int) -> tuple[str, tuple[EmittedToken, ...]]:
    """Enforce the spec 6.1 postcondition: non-empty, edge-clean, never reserved."""
    # M5 open question 10: this loop was `while True`. It terminates as written -
    # `_cut` has already trimmed the parts to at most max(budget, 1) characters in
    # total, the `-x` rewrite makes a 3-4 character device head stop being one in a
    # single pass, and every other pass removes a character - but a mutation during
    # M5 made it spin FOREVER, and a silent hang is the one failure mode "loud
    # failure over silent wrong" has no answer for. It is also the one a user cannot
    # even report usefully.
    #
    # The bound is a TRIPWIRE, not a policy: max(budget, 1) covers one pass per
    # surviving character, len(parts) covers the passes that empty a part outright,
    # and +8 is slack over the handful of rule-4 rewrites. No real input comes near
    # it (see test_the_finalize_loop_does_not_churn), so reaching it means the code
    # is wrong, not the input.
    limit = len(parts) + max(budget, 1) + 8
    for _ in range(limit):
        if parts:
            parts[0][2] = _EDGES.sub("", parts[0][2])
            parts[-1][2] = _EDGES.sub("", parts[-1][2])
        parts = [p for p in parts if p[2]]
        if not parts:
            return FALLBACK_STEM[: max(budget, 1)], ()
        stem = "_".join(p[2] for p in parts)
        if reserved_head(stem) is None:
            return stem, tuple(
                EmittedToken(
                    name=p[0],
                    slot=p[1],
                    value=p[2],
                    complete=bool(p[_COMPLETE]),
                    rewritten=bool(p[_REWRITTEN]),
                )
                for p in parts
            )
        if len(stem) + 2 <= budget:
            # A device name contains no '.' and no '_', so the reserved head is
            # always inside the first token's value - the -x lands there too.
            # This branch LENGTHENS the value, so it is a rewrite and not a
            # truncation: `complete` is left alone (spec 6.4, finding 2).
            parts[0][2] = apply_reserved_stem_rule(parts[0][2])
            parts[0][_REWRITTEN] = "1"
        else:
            # No room for -x: shorten instead until the head stops being a device
            # name ('con' -> 'co'). Terminates - device names are 3-4 chars.
            # Genuinely both: rule 4 rewrote it AND the budget shortened it.
            _shorten_last(parts)
    # TemplateError, which is a PamError, so spec 13's CLI contract catches it and
    # prints one clear line rather than a traceback (`cli.main`'s `except PamError`).
    # The tripwire firing IS a template-engine bug, so the engine's own error type is
    # the right one - but the message must not read like "check your template",
    # because there is nothing in the template for the user to correct.
    raise TemplateError(
        f"pam could not settle on a legal filename for {'_'.join(p[2] for p in parts)!r} "
        f"after {limit} passes (path budget {budget}). Nothing has been renamed. "
        "This is a bug in pam, not in your photos - please report it with the log."
    )


def build_stem(
    order: tuple[str, ...],
    values: dict[str, str],
    *,
    archive_root_abs: str,
    rel_dir: str,
    exts: tuple[str, ...],
) -> StemResult:
    """Assemble sanitized token values into a filename stem (no extension).

    values maps token name -> raw value. Missing or empty-after-sanitize values
    omit the token and its separator (graceful degradation, spec 6.1); an absent
    date becomes 'undated'. The path budget (spec 6.2 rule 5) drops scene, place,
    people in that order, then hard-truncates as a last resort. exts holds every
    extension that will share this stem - companion group members rename together
    (spec 5.4/6.3), so the longest one sets the budget; a leading dot is optional.
    Guarantees a legal stem (spec 6.1 rev 4): non-empty, edge-clean, and never a
    Windows device name. Pure and deterministic: the planner owns facts, seq, and
    collision handling.

    `StemResult.notes` carries spec 6.2 rule 7's sentence for every token the budget
    removed entirely - empty for a name that fit, and empty for `webui/disclosure.py`'s
    single-part call, whose docstring proves no drop is reachable there.
    """
    unknown = [name for name in order if name not in TOKEN_NAMES]
    if unknown:
        raise TemplateError(
            "Unknown template token {" + unknown[0] + "}. "
            "order must come from parse_template()."
        )
    clean = {name: sanitize_token(values.get(name, "")) for name in VALUE_TOKENS}
    if clean["date"] == "":
        clean["date"] = "undated"

    parts: list[tuple[str, str, str]] = []  # (drop-label, template slot, value)
    for slot in order:
        if slot == "event-or-scene":
            for name in _ALIASED:
                if clean[name]:
                    parts.append((name, slot, clean[name]))
                    break
        elif clean[slot]:
            parts.append((slot, slot, clean[slot]))

    budget = stem_budget(archive_root_abs, rel_dir, exts)
    # spec 6.2 rule 5: "Trim, in order: {scene}, {place}, {people} until it fits."
    # `dropped` is this loop's own record of which candidates it took, in the order it
    # took them; an ABSENT candidate consumes no step, which is why the membership test
    # gates the removal instead of the loop advancing on faith.
    dropped: list[str] = []
    for candidate in _TRIM_ORDER:
        if len("_".join(p[2] for p in parts)) <= budget:
            break
        if any(name == candidate for name, _, _ in parts):
            dropped.append(candidate)
            parts = [p for p in parts if p[0] != candidate]
    # max(budget, 1): one real character always beats the fallback - legality
    # wins over the budget, never over the facts.
    # N7: there is no `truncated` field. "The budget cut something" is already
    # carried per token by EmittedToken.complete; a third representation of one
    # fact is a third thing that can disagree with the other two.
    stem, emitted = _finalize(_cut(parts, max(budget, 1)), budget)
    # Everything that survived the trim loop and still did not reach the stem. Read
    # against `emitted` rather than against `_cut`'s return, so `_cut`'s room <= 0 branch
    # and `_finalize`'s empty-part filter are both covered by one statement and neither
    # can grow a silent third case. A token TRUNCATED to fit is not dropped - it is in
    # `emitted` carrying complete=False, which is spec 6.4's -10 row and a different
    # sentence. Names are unique in `parts` (parse_template refuses a repeat, and
    # {event-or-scene} contributes exactly one), so no token can be reported twice.
    reached = {token.name for token in emitted}
    dropped.extend(name for name, _, _ in parts if name not in reached)
    return StemResult(
        stem=stem,
        emitted=emitted,
        notes=tuple(
            DROPPED_NOTE.format(label=TOKEN_LABELS.get(name, f"the {name}")) for name in dropped
        ),
    )

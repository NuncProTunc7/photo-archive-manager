"""spec 6.2 rule 7 / C11: the `{seq}` numbers a plan's families SKIPPED, for one sentence.

M11 Task 12 Step 0. This is `naming/planner.py`'s `_proposed_families` moved out VERBATIM
ahead of M11's `{people}` token, which edits `_plan` and `_plan_unit` - the two largest
functions in a file that stood at 294 executable lines against the ~300 budget. The named
cut, on the precedent of C2's `_group_events` -> `naming/events.py`: pure, single-caller
and connection-free. The only change then was the leading underscore, dropped because the
function is now imported across a module boundary.

M13 Task 12 (P11) rewrote the body to spell each family ONCE, from its highest number,
instead of once per draft (n(n-1)/2 names for a family of n). The output set and the
ambiguity rule are unchanged; `tests/test_families_scale.py` keeps the old body as the
equivalence oracle.
"""

from pam.catalog import repo
from pam.naming.collision import split_rel_path


def proposed_families(drafts: list[repo.PlanItemDraft]) -> set[str]:
    """Every lower-cased catalog rel_path this plan's `{seq}` families SKIPPED (C11).

    A second, INDEPENDENT implementation of "which names does this family spell", written
    from spec 6.1's `{seq}` (*"3-digit"*) and spec 6.1's assembly rule that a stem is its
    token values joined by a single `_`. It deliberately does NOT call
    `collision._number_is_blocked`, `collision._seq_family` or anything else in the
    collision search: asking the search which numbers it skipped would make this sentence
    agree with the search by construction and say nothing about whether either is right -
    `CLAUDE.md`'s "an oracle built from the definition it checks is a survivor waiting to
    happen", applied to production code rather than to a test.

    It also CANNOT reuse them: those two read `StemResult.emitted`, the provenance the
    template engine records, and a `PlanItemDraft` carries only the finished filename. So
    the split is made from the text, by the spec's own two rules - and where the text is
    ambiguous the answer is *nothing*, never a guess:

      * the stem is split on `_`, and a part counts as the `{seq}` only when it is
        EXACTLY three digits (spec 6.1);
      * a stem with zero or more than one such part is SKIPPED ENTIRELY. Two three-digit
        parts (a scene label of `007`, say) would make "which part is the number"
        unanswerable from the text, and a wrong answer here names a file that is not
        blocking anything.

    The asymmetry is deliberate and is the opposite of the collision search's: there,
    over-reporting skips a free number and changes a FILENAME. Here nothing is computed
    from this but a sentence, so under-reporting costs the user an explanation and
    over-reporting tells them something false. Under-report.

    Returns the names BELOW the number actually assigned - those are the skipped ones. The
    assigned number's own name is not in the set: it is the name the plan proposes.
    """
    # P11 (M13 Task 12): one family = one directory, one stem template (the stem's parts with
    # the three-digit part marked), one extension. The union of the lower ranges its drafts
    # would each spell is exactly the range below the family's HIGHEST number, so each family
    # spells `1 .. highest-1` once instead of once per draft (n(n-1)/2 names for n drafts).
    highest: dict[tuple[str, tuple[str, ...], int, str], int] = {}
    for draft in drafts:
        stem, dot, ext = draft.generated_filename.rpartition(".")
        if not dot:
            continue
        parts = stem.split("_")
        at = [i for i, part in enumerate(parts) if len(part) == 3 and part.isdigit()]
        if len(at) != 1:
            continue
        index = at[0]
        rel_dir, _ = split_rel_path(draft.old_rel_path)
        key = (rel_dir, tuple(parts[:index]) + ("",) + tuple(parts[index + 1 :]), index, ext)
        highest[key] = max(highest.get(key, 0), int(parts[index]))
    skipped: set[str] = set()
    for (rel_dir, template, index, ext), top in highest.items():
        for lower in range(1, top):
            skipped.add(_spell(rel_dir, template, index, lower, ext))
    return skipped


def _spell(rel_dir: str, template: tuple[str, ...], index: int, number: int, ext: str) -> str:
    """The lower-cased catalog rel_path of one family member (the one name-construction site)."""
    spelled = [*template[:index], f"{number:03d}", *template[index + 1 :]]
    name = f"{'_'.join(spelled)}.{ext}"
    return (f"{rel_dir}/{name}" if rel_dir else name).lower()

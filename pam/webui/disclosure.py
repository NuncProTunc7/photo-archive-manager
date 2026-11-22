"""Design spec 6.2 rule 7 - "pam says what it changed" - for the review UI's editor.

RULE 7'S FIRST IMPLEMENTATION (amendment A5, HT-M7's P8). A user types `CON` and gets
`CON-x`; a user types a name containing `:` and gets it silently removed. Spec 8.2 has
always said "silent normalization is fine, silent DIFFERENCE is not", and until this
module the difference was on the card and nowhere else: the request succeeded, the
editor closed, and the card showed a name nobody had chosen.

This module owns the whole edit-a-name pipeline rather than only the sentence, and that
is deliberate: the flags rule 7 chooses between come OUT of the same `build_stem` call
that produces the stem. Splitting them across two modules would mean either running the
assembler twice or handing a `StemResult` across a seam for the sole purpose of
re-reading it. `write_api.py` was also at 298 executable lines against the project's
~300 budget, so the M8 plan's "the next thing that would push one over goes in a new
module" applies here on its own.

THE SANITIZER REPORTS WHICH RULE FIRED; NOTHING HERE DIFFS TWO STRINGS. `sanitize_report`
sets its flags at each substitution site and `EmittedToken` carries rule 4's and rule 5's,
so the choice below reads signals the engine raised rather than re-deriving spec 6.2 by
inspecting its output. A diff can say THAT something changed; it cannot say WHICH of the
three sentences is true, and that is the whole of what rule 7 asks for.

`EmittedToken.rewritten` HAS ITS FIRST PRODUCTION CONSUMER HERE. It was kept alive across
M5's N7 deletion sweep and M7's gate as "mandated but not yet read in production"; its own
comment reasoned that surfacing it would need a `tokens_json` wire-format change and a
spec 6.1 amendment. That reasoning is about `tokens_json`. Rule 7's channel is the RENAME
RESPONSE (spec 8.2), so no 6.1 amendment is owed and no planner document changes shape.
This is the counterpart to `StemResult.dropped`, which M8 Task 1 DELETED because M8 could
not honestly consume it - `rewritten` survives because M8 consumes it.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.naming.sanitize import sanitize_report
from pam.naming.template import build_stem
from pam.webui.api import _bad_request

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# THE COPY IS NORMATIVE (spec 6.2 rule 7, amendment A5) and appears once, here. One
# sentence, present tense, naming the stored stem - the user is looking at what they
# typed in the field the sentence sits under, so the sentence supplies the other half.
#
# THE SEPARATOR IS THE SPEC'S EM DASH, and the first attempt at this got it wrong. It
# shipped an ASCII hyphen on the argument that "every user-facing string in `pam/**/*.py`
# is ASCII" - a true measurement of the wrong set. The convention that binds a string is
# its OUTPUT CHANNEL, not the language it is composed in, and rule 7's two implementations
# (A5) both render only in a browser: this sentence through the rename response, and M9's
# path-budget sentence through `trust_reasons_json`, which `webui/api.py` alone consumes -
# `report.py` and the CLI never print it. So this sentence appears beside `app.js`'s own
# copy, which uses em dashes in eleven places, and directly under the Why? panel's
# `pam proposed ... - Good - 75%`. Two different dashes for the same clause on one screen
# is the defect, and A5 is normative besides. Corrected M8 Task 6 review.
RESERVED_NOTE = 'pam adjusted that to "{stem}" — Windows reserves that name.'
ILLEGAL_NOTE = (
    'pam adjusted that to "{stem}" — names use letters, numbers, dots, dashes and underscores.'
)
SHORTENED_NOTE = 'pam shortened that to "{stem}" to fit the Windows path limit.'

# RULE 7'S SECOND SURFACE IN THE REVIEW UI (spec 8.2 rev 15, amendment A1). A decision is
# written to the whole companion unit, so a keystroke aimed at one card can move two rows
# or three - and rule 7's sentence is not about names alone, it is "whenever pam stores
# something that differs from what its input asked for, it says so, in plain language, at
# the place it appears". A decision that silently moved a file the user never looked at is
# the same silence rule 7 was written against.
#
# ONE TEMPLATE, PLURALISED ON THE COUNT ONLY. The clause after the dash is the validator's
# own sentence for the same rule (`execute/validator.py::COMPANION_REASON`), deliberately:
# a user who meets this in the browser and then meets a companion refusal from `pam apply`
# should read the same reason twice, not two wordings of it. No route out is named because
# this is not a refusal - pam did what was asked and more, and the user's next keystroke is
# unaffected. "companion file" is the product's own word for this (spec 7.3, vision S3).
#
# The em dash is the spec's, on the same reasoning RESERVED_NOTE's comment above sets out:
# the convention that binds a string is its OUTPUT CHANNEL, and this one renders only in a
# browser, beside `app.js`'s own em dashes.
DECIDED_UNIT_NOTE = (
    "pam also {verb} {count} companion file{s} — pam renames a group together or not at all."
)

# Spelled from spec 8.2's three action names rather than imported from `write_api`, which
# imports THIS module: a constant that closed the loop would be a circular import, and an
# oracle built from the definition it describes is a survivor waiting to happen besides.
# `reset` is not a verb a person reads, so it gets the phrase UX 5 rev 8 puts on its own
# control - `Not decided` - rather than the column value.
# UX 5 REV 10, THE OTHER HALF OF THE SAME SENTENCE (finding F9). "Where any member of a
# widened unit was already decided the other way, the disclosure must say so as well as
# counting. Naming the number is not enough; naming what was overridden is the point."
# `decide-filter` with `status=proposed` and `approve` reaches a unit through its
# undecided member and widens onto the member the user deliberately SKIPPED - so the skip
# is reversed by a decision the user aimed somewhere else. THE BEHAVIOUR IS CORRECT AND
# DOES NOT CHANGE: leaving one member skipped is the split spec 7.3 forbids, and pam
# renames a group together or not at all. What was missing is that nobody was told.
#
# SCOPED TO THE COMPANIONS, which is what the amendment's own title says ("a bulk decision
# that WIDENS to a companion unit says what it overrode"). An item the caller named itself
# is an item the user aimed at, and re-deciding it is the request, not a surprise.
#
# "Decided the other way" is `approved` or `skipped` and not the status this action
# writes. `proposed` is not a decision - a reset over an undecided companion overrides
# nothing - and an already-`approved` companion under `approve` moved no further than the
# rest of its unit.
# Two templates rather than one with a pronoun switch, because the one-companion case is
# the F9 case: a Live Photo pair whose `.MOV` the user skipped on purpose. "1 of them"
# would be a sentence about a set with one element in it.
OVERRODE_LONE_NOTE = (
    " That file had been decided the other way — pam changed it to keep the group together."
)
OVERRODE_SOME_NOTE = (
    " {count} of them had been decided the other way — pam changed {it} to keep the"
    " group together."
)

DECIDED_VERB = {
    "approve": "approved",
    "skip": "skipped",
    "reset": "returned to undecided",
}


def decided_unit(action: str, companions: int, overrode: int = 0) -> str:
    """Rule 7's sentence for a decision that moved more files than it named.

    '' when it moved exactly what it named, which is every decision over a lone file and
    every decision whose selection already covered its units - the ordinary case, and the
    page shows nothing at all for it.

    `overrode` counts how many of those companions had been decided the other way, and
    adds UX 5 rev 10's second clause. It can never exceed `companions` and the caller is
    what guarantees that, so the two numbers are not re-derived here from each other.

    An action outside spec 8.2's three cannot reach here (`_action` refuses it with a 400
    before any row is read), and an unknown one would produce no sentence rather than a
    wrong one: a decision reported with the wrong verb is worse than one reported with
    none, and never the reverse.
    """
    verb = DECIDED_VERB.get(action)
    if companions < 1 or verb is None:
        return ""
    note = DECIDED_UNIT_NOTE.format(
        verb=verb, count=companions, s="" if companions == 1 else "s"
    )
    if overrode > 0:
        note += (
            OVERRODE_LONE_NOTE
            if companions == 1
            else OVERRODE_SOME_NOTE.format(count=overrode, it="it" if overrode == 1 else "them")
        )
    return note


# RULE 7'S THIRD SURFACE IN THE RENAME RESPONSE (design spec 8.2 rev 18, amendment B1).
# "A member whose resulting proposed filename equals its current on-disk filename
# case-insensitively - but not exactly - will not be renamed." The rename SUCCEEDS and
# the name is stored; what rule 7 requires is that the response NAME THE FILE and say it
# will not be renamed, because silently storing a name and quietly not renaming the file
# is the failure B1 exists to close. A refusal was considered and rejected as permanent.
#
# THE LITERAL WORDS ARE NOT NORMATIVE - spec 8.2 rev 18 states the content ("name the
# file this applies to and say it will not be renamed") and gives no copy, unlike rule
# 7's three adjustment notes above. The clause after the dash is the spec's own reason
# in the user's language, and the em dash is the spec's on the same argument
# RESERVED_NOTE's comment sets out: this string renders only in a browser.
#
# THIS IS NOT THE COLLISION BULLET'S COMPARISON. Changing `Holiday` to `holiday` in the
# editor is an ordinary re-edit and usually a real rename on disk; spec 8.2 calls that
# legal and it stays legal. This compares the RESULTING name against the file's CURRENT
# name, which is a different pair of strings.
NOT_RENAMED_NOTE = (
    "{names} will not be renamed — that name differs from the one on disk only in"
    " capitalisation, and pam does not change a name in case alone."
)


def not_renamed(current_and_resulting: list[tuple[str, str]]) -> str:
    """B1's sentence for the members a stored name will not actually move, or ''.

    EXACT EQUALITY IS DELIBERATELY EXCLUDED, which is spec 8.2 rev 18's own wording
    ("case-insensitively - but not exactly"): a user who typed the name the file already
    has can see that nothing will change, and a sentence explaining it would be noise on
    the one surface rule 7 is trying to keep readable. The item is still a no-op at
    preflight (spec 7.0 J12) either way - this governs the SENTENCE, not the behaviour.

    Takes the two filenames rather than `ReviewItem`s so that the predicate is spelled
    out at the one call site that knows the resulting names, which are not stored
    anywhere yet when the check has to be made.
    """
    names = [
        resulting
        for current, resulting in current_and_resulting
        if resulting.lower() == current.lower() and resulting != current
    ]
    if not names:
        return ""
    return NOT_RENAMED_NOTE.format(names=", ".join(names))


def disclosure(stem: str, *, reserved: bool, shortened: bool, illegal: bool) -> str:
    """Rule 7's one sentence for a stored stem, or '' when the input got what it asked.

    MORE THAN ONE RULE CAN FIRE ON ONE SUBMISSION and rule 7's copy is one sentence, so
    a precedence is owed. `CON:x` reaches rules 1-3 and can reach rule 4; a long name
    reaches rules 1-3 and rule 5; and `_finalize`'s "no room for -x" branch sets rule 4's
    flag and rule 5's flag on one token for one cause.

    THE ORDER IS: reserved, then shortened, then the allowlist - least explicable first.
    - `CON` -> `CON-x` is the only one of the three whose result explains nothing about
      itself. Nothing on screen says where `-x` came from, and no user is expected to
      know that Windows still reserves four decades' worth of device names. It is also
      by far the rarest, so giving it precedence costs the other two almost nothing.
    - A shortened name is visibly cut, but the REASON is not visible: it is the whole
      path's length, not the name's, so the same name is legal one folder up. Second.
    - A removed `:` or a folded `AE` shows itself: the result names the characters that
      went. Most common, most self-explaining, so it yields to the other two.

    The ordering also settles the one branch where rules 4 and 5 fire together for a
    single cause: `_shorten_last` shortens a stem only BECAUSE its head is a device
    name, and "Windows reserves that name" is the true sentence about it. Reporting the
    path limit there would name a limit that is not what went wrong.
    """
    if reserved:
        return RESERVED_NOTE.format(stem=stem)
    if shortened:
        return SHORTENED_NOTE.format(stem=stem)
    if illegal:
        return ILLEGAL_NOTE.format(stem=stem)
    return ""


@dataclass(frozen=True)
class EditedName:
    """The stem a rename will store, and rule 7's sentence about it ('' when none)."""

    stem: str
    note: str


def edited_name(
    server: "ReviewServer", submitted: str, clicked: ReviewItem, members: list[ReviewItem]
) -> EditedName:
    """The submitted name, reduced to a stem and put back through spec 6.2's real rules.

    THE EXTENSION IS VALIDATED AGAINST THE CLICKED MEMBER ONLY (spec 8.2 rev 12). That
    is the extension the user saw on the card, and a unit's members have DIFFERENT
    extensions by construction (`.NEF` + `.JPG`, `.JPG` + `.MOV`) - requiring them to
    match would refuse every live-photo pair in the archive. Byte-for-byte, case
    included: spec 5.4 rev 6 keeps an extension exactly as it is written on disk, and
    `pam` is not the tool that decides a `.JPG` should have been a `.jpg`.

    RE-SANITIZED THROUGH THE REAL ENGINE, never a re-implementation of it. `build_stem`
    with a single `{orig}` part IS spec 6.1's postcondition machine: it runs
    `sanitize_token`, applies spec 6.2 rule 5's budget from `stem_budget`, and re-applies
    rule 4's reserved-device rule AFTER the cut, which is the interaction a naive
    one-pass composition gets wrong (`Console` truncated to three characters is `Con` -
    M3 review condition C1) together with the one going the other way (`con` -> `con-x`
    is two characters LONGER and can bust the budget it was just brought inside). Both
    are handled by `_finalize`'s loop, which is private and takes token parts, so
    reaching it through its public front door is the only way to hold the postcondition
    without owning a second copy of it.

    The budget is taken against the LONGEST member path, because every member gets this
    same stem and the longest of them is the one that has to fit.

    THE TWO FLAGS ARE READ OFF `emitted`, AND ONE PART IS WHY THAT IS TOTAL HERE. This
    call has exactly one part, so `_cut`'s "cut away entirely" branch - the one that
    leaves a token in neither the stem nor `emitted`, recorded as an open finding in
    `StemResult`'s comment - cannot be reached: with no earlier part there is no join
    character to charge, and the budget floor is `max(budget, 1)`, so `room` is at least
    1 and the single part is always emitted. `emitted` empty would mean the fallback
    stem, which is likewise unreachable because `sanitize_report` has already refused an
    empty token and a sanitized value never begins with an edge character. Both are
    handled by `any()` over an empty tuple returning False rather than by an assertion:
    a wrong sentence is a worse outcome here than no sentence, and never the reverse.
    """
    ext = repo.ext_of(clicked.new_filename)
    suffix = f".{ext}"
    if not submitted.endswith(suffix) or len(submitted) <= len(suffix):
        raise _bad_request(
            "new_filename",
            f"must keep this file's extension {suffix} exactly as it is - pam never"
            " changes an extension",
        )
    report = sanitize_report(submitted[: -len(suffix)])
    if not report.text:
        raise _bad_request(
            "new_filename",
            "there is nothing left of that name once the characters Windows will not"
            " accept in a filename are removed - try letters, digits, - or _",
        )
    rel_dir = max((member.rel_dir for member in members), key=len)
    exts = tuple(repo.ext_of(member.new_filename) for member in members)
    result = build_stem(
        ("orig",),
        {"orig": report.text},
        archive_root_abs=str(server.workspace.archive_root),
        rel_dir=rel_dir,
        exts=exts,
    )
    return EditedName(
        stem=result.stem,
        note=disclosure(
            result.stem,
            reserved=any(token.rewritten for token in result.emitted),
            shortened=any(not token.complete for token in result.emitted),
            illegal=report.changed,
        ),
    )

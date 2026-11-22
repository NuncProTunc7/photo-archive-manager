"""M7 Tasks 11+12's structural cross-checks: `index.html` <-> `app.js` <-> `style.css`.

THE SUITE HAS NO BROWSER AND NO JS RUNTIME, and no dependency may be added to give it
one. Structural cross-checks against a real parse are therefore the honest ceiling of
this module, and the docstrings below say which side of that line each test sits on.

WHAT IS PINNED HERE
  - `index.html` PARSES, with a real parser (`html.parser`), and is well formed enough
    to walk: every start tag closed by its own end tag, no stray end tags, nothing left
    open at EOF. This module exists in this shape because of `tests/test_ci_lanes.py`,
    which hand-parsed a `.github/workflows/ci.yml` that did not parse as YAML, answered
    every question asked of it, and left BOTH CI lanes dead. A hand-rolled parser cannot
    notice that the document is not the language.
  - Every element id `app.js` queries exists EXACTLY ONCE in the page. The JS is the
    QUERY; the HTML parse is the ORACLE. This is the regression net for every DOM change
    Task 12 made, and it is why Task 12's controls are markup rather than nodes built in
    a script: an element the page creates at runtime is an element no parse can check.
  - The footer's UX 5 sentence, spelled from the spec rather than copied off the page.
  - UX 7's token names, its normative colour literals, its spacing scale and radius, and
    that a light AND a dark token block both exist and genuinely differ.
  - UX 5/5a's structural copy: the dead-server banner, the empty-filter line, the
    first-position `Needs attention` control, the two bulk presets, the ABSENCE of any
    "Approve All" control (vision 6a.3 - the trust contract), A4's search placeholder,
    and the narrow-viewport notice with the 1100px boundary UX 7 names.
  - TASK 12'S SURFACES EXIST AS MARKUP AND START HIDDEN: the shortcut overlay naming all
    seven UX 5 keys with what each one does, the preview overlay with its stage, its
    metadata panel, its Why? region and the same three decide controls a card carries,
    A6's single name editor with the extension shown OUTSIDE any field, the Smart Review
    banner, the session-resume banner with its [Jump to first undecided] control, the
    review-time estimate, and UX 5a's sort toggle offering spec 8.2's two orders.
  - THAT THE PAGE OFFERS NO PLAYER. UX 5 rev 3: v1.0 never decodes video, so a video is
    a glyph and a metadata panel, and no `<video>`/`<audio>`/`<source>` may appear.
  - The NORMATIVE SENTENCES TASK 12 BUILDS AT RUNTIME are present verbatim in the
    script - and that is a COPY check, not a behaviour check. It catches a wording drift
    away from UX 5/5a. It says nothing whatever about whether the sentence is ever shown,
    or shown at the right moment; that is HT-M7's, and it is listed below as such.

WHAT IS NOT PINNED HERE, AND WHERE IT GOES INSTEAD
  - That the page RENDERS at all, or that the four UX 5 states (skeletons while loading,
    the done panel, the empty state, the inert dead-server page) appear at the right
    moment. There is no DOM to observe. HT-M7.
  - Everything `app.js` builds at runtime: the trust bands and bar, tier badges, token
    chips and their tooltips, the [Why?] breakdown, the thumbnail `alt`, the glyph
    fallback, the A2 orientation transform, `Load more`. HT-M7.
      * The orientation transform needs walking, not glancing at. A grid card gets a
        fresh `<img>`, but THE OVERLAY REUSES ONE IMAGE for every photo the arrows move
        through, so `applyOrientation` clears `data-orientation` before it decides
        whether to set one. Walk the arrows across a rotated photo and an unrotated one
        and back: the failure is the second photo wearing the first one's rotation,
        which is a picture presented WRONGLY rather than an effect that is missing, and
        no parse can see the difference.
  - EVERY BEHAVIOUR TASK 12 ADDS. Named individually, because an overclaim here becomes
    a check the owner believes has already run:
      * that a key does what its row in the shortcut overlay says, in the grid AND in
        the preview alike, and that Space multi-selects rather than scrolling;
      * that keys are INERT while the name field has focus - the one that matters most,
        since a user typing `Emma` must not skip, edit and approve the card;
      * that deciding auto-advances to the next UNDECIDED item, and that the arrows move
        inside the overlay without closing it;
      * that a decision repaints its card and the header from the write's own `counts`;
      * that the two presets act on the CURRENT VIEW and not on the whole plan, and that
        a non-zero `excluded_low` is rendered - the API half of both is pinned in
        `tests/test_webui_decide.py` (M740 for the scoping, M754 for the report of the
        exclusion), but the page's wiring to it is not. `presetFilter` builds ONE scope
        for the POST body and for the count on the button; that they cannot drift is a
        property of one function, and that the button therefore never approves more than
        it names is HT-M7's to confirm;
      * that `Approve High` REFUSES, in a sentence, when the view's own tier filter names
        a different tier - rather than approving items the user cannot see, and rather
        than the button being silently inert. Filter the view to Medium, press
        `Approve High`, and read what appears on the counts line;
      * that the editor pre-fills the current stem, names the partners' extensions when
        `unit_size > 1`, renders the server's possibly-sanitized result, and shows a
        refusal at the field. The partner extensions are found with `?q=<shared stem>`
        and then filtered by `unit_key`, which is what makes a WRONG partner impossible
        (measured: `?q=` over-matches - a stem that merely contains the clicked one comes
        back too - and `unit_key` is the discriminator). What HT-M7 owns is the
        DEGRADED case: when fewer partners are found than `unit_size` promises, the
        weaker sentence that only COUNTS them must appear, never the one that lists
        extensions, because listing a subset tells the user fewer irreplaceable files
        are about to be renamed than really are;
      * that the header's counts are never stale. They are written ONLY from a write's
        own response, and `[Needs attention (K)]` and the preset counts are refreshed on
        a 400 ms debounce after a decision. Two things to try: decide rapidly and watch
        the numbers settle, and open the SAME review URL in a second tab, decide in one
        and look at the other - the second tab's header does not re-read the summary
        until it writes something itself;
      * that the overlay's `variant=full` request is made, and that its own 404 draws the
        glyph (A2 amendment 1 - the grid and the overlay legitimately disagree);
      * that the Smart Review, resume and estimate banners appear when their conditions
        hold and stay hidden otherwise, including the >= 10-decision floor;
      * UX 5a's flow END TO END - land on the banner, click `[Needs attention (K)]`,
        clear the K items with X/Z/C, and watch the banner reach step 3. K is the count
        of attention items STILL UNDECIDED and the filter scopes to the same set, so the
        button, the banner and the list all name the identical items; that they do is
        checkable only by running it. The SERVER half of this is now pinned - see
        `test_webui_decide.py::test_ux_5a_step_3_is_reachable_only_through_the_UNDECIDED_
        attention_count`, which proves the undecided count reaches 0 while the summary's
        `attention` never moves - so what is left for HT-M7 is that the PAGE reads the
        first number and not the second.
      * AND THAT STEP 3 IS ACTUALLY SEEN. The Smart Review banner hides itself when
        `remaining === 0`, where UX 5's done panel takes over, so `Nothing needs
        attention.` is on screen only while at least one item anywhere in the plan is
        still undecided. Clear the attention items on a plan that still has ordinary
        items left and step 3 appears with its two presets; clear the LAST item in the
        plan and the done panel replaces it with the `pam apply` command instead. Both
        are intended; that they are what the owner sees is not checkable here.
      * THAT `?` OPENS THE SHORTCUT OVERLAY ON THE MAINTAINER'S OWN KEYBOARD. The handler
        reads `event.key === "?"`, which is layout dependent: on a US layout it is
        Shift+/, on many others it is somewhere else entirely, and on a few it is a dead
        key. Nothing here can press a key. Named separately from the other six because
        it is the only one whose failure mode is "the discovery surface is unreachable
        and the user never learns the other six exist".
      * THAT NO CONTROL OFFERS TO APPROVE EVERYTHING UNDER ANOTHER NAME. The check below
        refuses `Approve All` however it is spelled in MARKUP, and cannot refuse a
        synonym - `[Approve everything]`, `[Accept all]`. See
        `assert_no_approve_all_control` for why that is bounded rather than open.
    ALL OF IT IS HT-M7's. There is no DOM, no event loop and no server in this module.
  - UX 7's MEASURED properties - contrast >= 4.5:1, visible focus rings, the 3-5 column
    responsive grid, ~96px cards. A parse cannot measure a rendered colour or a layout.
    HT-M7.
  - `Undated` pinned last in the nav: that is a SERVER-side property and is already
    pinned against the real API in `tests/test_webui_api.py`. The nav tree in this page
    is built from that response, so there is nothing about it in the markup to check.
  - The three files being served byte-identical through the token-gated route: pinned in
    `tests/test_webui_server.py`, next to the route it is a property of, rather than as
    a second copy here.
  - No `://`, no `/t/`, no root-relative `"/api` in any static file: also
    `tests/test_webui_server.py`, which already loops over `STATIC_FILES`.

THE EXTRACTOR IS A REGEX OVER JAVASCRIPT, WHICH IS ITSELF A HAND-ROLLED PARSER - the
exact species named above. Three things keep it honest, and all three are tested:
  0. COMMENTS ARE REMOVED FIRST, by a string-aware scan rather than a regex, because a
     comment is not a query. This was not foresight: the first version of this module
     demanded an element with `id="literal"`, because `app.js`'s header comment
     documents the extractor by spelling `getElementById("literal")`.
  1. It reads EXACTLY TWO SPELLINGS and nothing else:
        `getElementById("literal")` / `getElementById('literal')`
        `querySelector("...")` / `querySelectorAll("...")` with a quoted literal
        selector, from which a selector that is exactly `#name` yields an id.
     NOT handled, deliberately: a template literal (`` `x` ``), a computed name
     (`getElementById(name)`), whitespace or a newline between the `(` and the literal,
     a compound or descendant selector (`"#a .b"`), `getElementsByName`, and any
     attribute-selector spelling of an id.
  2. An unhandled spelling is a FAILURE, never a silent miss: every `getElementById(`
     and every `querySelector(`/`querySelectorAll(` occurrence in the source must be
     matched by the strict form as well as by the loose one, and the counts are asserted
     equal. An extractor that quietly matches nothing passes every cross-check
     vacuously, so the count gate - plus a non-zero floor and a spec-named anchor set -
     is what makes this a gate rather than a green light.

EVERY EXPECTATION SPELLED FROM THE SPEC IS ALSO WATCHED FAILING. The Tasks 11+12 review
measured the same defect seven times in this module: `ID_FLOOR = 0`, `ANCHOR_IDS =
frozenset()`, `UX_7_TOKENS = ()`, `UX_7_BAND_COLOURS = {}`, `UX_5_RUNTIME_COPY = ()`,
`UX_5_KEYBOARD = ()` and `UX_5_PRESETS = ()` EACH LEFT THE WHOLE SUITE GREEN (M718,
M719, M733-M737). An expectation asserted in a loop is an expectation that asserts
nothing once the loop is empty - `WRITE_FLAGS = ()` one milestone earlier, and
`repo.ENRICHERS` one before that. So each of those checks is now a named
`assert_*(document)` helper driven by a NEGATIVE CONTROL that plants the violation and
requires a refusal. `SPEC_8_2_SORTS` needed none of this and is the shape to prefer: it
is asserted with `==`, so emptying it fails on its own.
"""

import re
from html.parser import HTMLParser
from importlib import resources

import pytest

# --- UX spec 7's visual system, spelled from the SPEC ------------------------------
#
# Never read out of `style.css`. An oracle built from the definition it checks is a
# survivor waiting to happen, and this project has measured five of them
# (`repo.open_plan`, `repo.ENRICHERS`, Task 7's `LOOPBACK_HOST`, Task 8's
# `_orientation`, Task 10's `WRITE_FLAGS`).

UX_7_TOKENS = (
    "--bg",
    "--surface",
    "--text",
    "--text-muted",
    "--accent",
    "--ok",
    "--warn",
    "--danger",
    # UX 7 rev 16 (M14 Task 1): the accent's hover and the text on it, each band's tint,
    # the hairline and the hover wash.
    "--accent-hover",
    "--on-accent",
    "--ok-bg",
    "--warn-bg",
    "--danger-bg",
    "--line",
    "--wash",
)
# UX 7 rev 16 (M14 Task 1, U2/U4) - the mockups' tokens, light and dark.
UX_7_ACCENT_LIGHT = "#1f4fd1"
UX_7_ACCENT_DARK = "#8aaeff"
# Each band colour as (light, dark). Rev 16 gives each a dark variant, so EVERY definition of
# each in the stylesheet must be one of exactly these two literals, and both must be there.
UX_7_BAND_COLOURS = {
    "--ok": ("#17784a", "#6fd3a0"),
    "--warn": ("#8a5a00", "#e8bd62"),
    "--danger": ("#b23a1e", "#ff9c86"),
}
UX_7_SPACING = (4, 8, 12, 16, 24, 32)
UX_7_RADIUS = "10px"
UX_7_RADIUS_LARGE = "16px"
UX_7_MIN_WIDTH = 1100

# --- UX spec 5's normative copy, spelled from the SPEC ------------------------------

# W16 (U8; M14 Task 6c): the footer's sentence. "Nothing is renamed from this page. When done: pam
# apply N" was true until the page could rename; U5 replaces it in the commit that makes it false.
W16_FOOTER_SENTENCE = "Only approved names change. Nothing is renamed until you confirm."
# U12 (M14 Task 5e), replacing the rev-2 banner: the UX spec's three sentences, verbatim.
UX_5_SERVER_GONE = (
    "pam has stopped. Nothing is lost: every rename is written down before it happens. "
    "Start pam again and it will put things right before it does anything else."
)
UX_5_EMPTY_FILTER = "No items match — clear filters"
UX_5A_ATTENTION = "Needs attention"  # the card page's header button, retired at Step 6b
W5_ATTENTION_TAB = "Needs a look"  # U8's W5: the tab that replaced it
UX_5_PRESETS = ("Approve High", "Approve High+Medium")

# UX spec 5 rev 6/rev 8/rev 10: the placeholder names only what search matches TODAY, and
# it must name ALL of it - a box that silently returns nothing for a person's name is UX
# spec 1.4's dead end, and a box that never mentions a thing it does match is the same
# dead end from the other side. The two lists below are the spec's fill-in order split at
# M10: `event` moved across in M9 Task 11 and `place` in M10 Task 10, each together with
# `_matches_q`'s new operand and neither before it.
#
# `location` STAYS in the negative list and its meaning changes with this edit, which is
# recorded rather than left to be rediscovered. It was there as a synonym for the thing
# search could not do; now it is a COPY constraint - both normative documents call this
# filter `place` (spec 8.2's table, UX spec 5's bullet), and a box offering "locations"
# beside a spec saying "place" is drift, not a broken promise. Keeping it also keeps the
# negative list at four words rather than three, which is the half of this pair M1368
# found could be narrowed into uselessness.
#
# CAPTION AND SCENE ARE IN NEITHER LIST, DELIBERATELY. Neither normative document promises
# caption or scene search at any milestone (M10 plan clarification 6), so there is no
# milestone at which a row for them would move - and a negative row for a feature that is
# never coming would read as a schedule rather than as the rule this pair encodes.
#
# Both are literal words rather than a slice of one list, because the point of the pair is
# that they DISAGREE about `place`: a single list with an index would let one edit move the
# boundary in both directions at once.
SEARCHABLE_NOW = ("name", "event", "place")
NOT_SEARCHABLE_YET = ("people", "person", "location", "face")

# M9 TASK 11'S REVIEW, FINDING 1 (M1368). Narrowing `NOT_SEARCHABLE_YET` from five words
# to `("place",)` left all 2573 tests green: the two negative-control rows that exercise
# the list BOTH contain `place`, so one surviving word satisfies every control there is.
# With the people-words gone, a placeholder reading "Search old and new file names, event
# labels, and people" passes this guard TODAY - and people search is M11. UX 1.4's dead
# end, shipped green, which is the precise failure the pair was rebuilt to prevent.
#
# The collection-time non-emptiness assert below catches an EMPTIED list; only a length
# catches a NARROWED one, and narrowing is what happens when somebody deletes the entry
# their edit broke. Prefer the exact length over non-emptiness - the third time this
# project has written that sentence, after M743 and M9 Tasks 8 and 10.
#
# Spelled per word, from the milestone that owns each, so a future task cannot move one
# across by editing a number:
#   people, person, face -> M11 (`pam faces cluster`; faces are not searchable in v0.1-m9)
#   place                -> MOVED to SEARCHABLE_NOW in M10 Task 10, with `_matches_q`'s
#                           fourth operand. Spec 8.2 rev 16 and UX spec 5 rev 10 rescheduled
#                           it from M9 to M10, which is what closed `m9-progress.md`'s
#                           fifth owed amendment. It was moved, never deleted, and both
#                           lengths below moved with it.
#   location             -> stays: pam's word for this filter is `place` in both specs.
assert len(SEARCHABLE_NOW) == 3, "a search word was added or dropped without moving this"
assert len(NOT_SEARCHABLE_YET) == 4, (
    "a not-yet-searchable word was dropped without moving it to SEARCHABLE_NOW - and a "
    "narrowed list still passes every control, because the controls share their words"
)

# --- UX spec 5's keyboard, spelled from the SPEC ------------------------------------
#
# "Keyboard (one-handed, Lightroom-style), identical in grid and preview: X approve .
# Z skip . C edit name . <-/-> prev/next . Space select . Enter open/close preview .
# ? shortcut overlay." Seven keys; the arrows are one row and are checked as two
# lookups into it, because a list that named only one direction would pass while the
# page told the user about half a control.
# M14 U2: the LIST's keys - `↑/↓` move in the list, and `←/→` still move inside the
# preview (U16). `Space` (select) was reserved and inert until Task 3; M14 Task 3 (U3) makes it
# select the row under the keyboard, and the overlay names it.
UX_5_KEYBOARD = (
    ("X", ("approve",)),
    ("Z", ("skip",)),
    ("C", ("edit",)),
    ("↑", ("previous", "next", "list")),
    ("↓", ("previous", "next", "list")),
    ("←", ("previous", "next", "preview")),
    ("→", ("previous", "next", "preview")),
    ("Space", ("select",)),
    ("Enter", ("preview",)),
    ("?", ("shortcut",)),
)

# UX spec 5 rev 3: v1.0 never decodes video, so the preview overlay is a glyph and a
# metadata panel and there is deliberately no player anywhere on the page.
PLAYER_TAGS = frozenset({"video", "audio", "source", "iframe", "object", "embed"})

# Spec 8.2's `?sort=` vocabulary, and UX spec 5a's "sort toggle offers date order".
SPEC_8_2_SORTS = {"trust_asc", "date"}

# The sentences UX spec 5/5a make normative that `app.js` assembles at runtime. Present
# here as a COPY check only - see the module docstring. Each is a fragment that a
# concatenation cannot avoid carrying, spelled from the spec.
UX_5_RUNTIME_COPY = (
    # 5a step 1 rev 8, and BOTH forms are normative: `903 of 923 names need no attention.
    # Review the 20 that do first.` and, when exactly one is left, `99 of 100 names need
    # no attention. Review the one that does first.` The lead is shared; the two tails are
    # separate fragments because a page carrying only one of them is HT-M7's P1 again -
    # `attention == 1` is the normal state of a well-curated archive, and rev 2's copy was
    # lifted verbatim with the plural hard-coded.
    # M14 Task 2 Step 6b final: 5a step 1 and step 3 are U8's W3/W4 now (the card page's
    # Smart Review banner and its four fragments retired with it). W3 in both forms - the
    # plural is U8's spelled copy and the singular its inflection (the Task 2 ruling) - and
    # W4, spelled from U8, never read from `pam-list.js`.
    " names need a look.",
    " name needs a look.",
    "Nothing needs a look.",
    # 5 rev 8: `812 approved, 111 remaining` - the greeting is gone, see UX_5_DELETED_COPY.
    " approved, ",
    " remaining",
    # 5: `~N min at your pace`.
    " min at your pace",
    # 5 rev 8, the edited card's three sentences. The card face says this INSTEAD of a
    # label, a percentage, a bar and a tier badge; the Why? panel names whose name its
    # breakdown describes and then how to put that name back. All three are assembled
    # around values at runtime, so no element in `index.html` carries any of them.
    "Edited by you",
    "pam proposed ",
    "Press ↺ to put pam's name back.",
)

# --- UX spec 5 rev 8's counts line, spelled from the SPEC ---------------------------
#
# "The counts line names skipped (rev 8), so it reconciles: `923 items · 812 approved ·
# 15 skipped · 96 remaining`. Rev 2's format omitted skipped, so `22 approved · 75
# remaining` over 100 items did not add up and a user mid-review could not tell where the
# missing files had gone."
#
# The ARITHMETIC is the server's and is already pinned against the real API in
# `tests/test_webui_api.py::test_the_summary_counts_add_up_to_the_plan`; `/plan/{id}` has
# always returned all four numbers. What was broken was the DISPLAY, in `app.js`, which
# named three of them - so what is holdable here is that the fourth literal is present.
# The four are held as separate fragments rather than as one string because `renderHeader`
# concatenates them around runtime counts and no page element carries the assembled line.
#
# `≈N min at your pace` is deliberately NOT in this tuple. The amendment record's B7 line
# prints it as the tail of the same sentence, but UX 5 rev 8's body text makes it a
# separate bullet - a header element that stays HIDDEN until >= 10 decisions - and
# `index.html` has carried it as its own `#estimate` span since M7. It is pinned above in
# UX_5_RUNTIME_COPY and by `test_the_review_time_estimate_starts_hidden`.
# M14 Task 2 Step 6b final: the card page's counts line (`100 items · 22 approved · 3 skipped ·
# 75 remaining`, UX 5 rev 8 / B7) is gone; U8's W2 progress line replaces it and keeps B7's
# point - it names SKIPPED, so its numbers reconcile: `124 of 128 names approved`, then
# `0 skipped · 4 still to decide`. Spelled from U8's W2.
UX_5_COUNTS_LINE = (" of ", " names approved", " skipped · ", " still to decide")

# --- what UX spec 5 / 5a rev 8 DELETED, spelled from the SPEC -----------------------
#
# A deletion needs a check of its own, because the obvious wrong fix for both of these
# findings is to add the new wording and leave the old sentence standing beside it - a
# page that says the false thing twice as often. Both are quoted from the amendments that
# removed them:
#
#   "Rev 8 also removed the words *Good or better* from this sentence." A trust label
#   describes ONE NAME; the banner describes A SET, and `total - attention` is the
#   membership of no label under any scheme (HT-M7's P1).
#
#   "Rev 8 deletes the *Welcome back —* greeting." The product has no notion of a session,
#   the banner fires from the first decision, and inventing session state for a greeting
#   was the wrong trade (HT-M7's P6).
#
# Checked against the COMMENT-STRIPPED script, because both functions' docstrings quote
# the old copy to explain why it went - and a comment is not a sentence the user reads.
UX_5_DELETED_COPY = ("Good or better", "Welcome back")

# --- HT-M7's P3: the Why? panel spans the card ---------------------------------------
#
# UX spec 5 puts [Why?] on every card and 5a's flow turns on it; the M7 review calls it
# the most-clicked control in the product. `toggleWhy` appends the panel to `.card`
# ITSELF - the evidence belongs under both columns, not beside the thumbnail - and `.card`
# is a two-track grid whose first track is the 96px thumbnail. A grid item that declares
# no `grid-column` is auto-placed into the next empty cell, which is row 2 COLUMN 1, so
# the panel rendered 96px wide and its text wrapped every two words (`2023-08-` / `24`,
# measured on a real-world library).
#
# Spelled here as what the panel IS - a full-width panel inside a grid card - never read
# out of the stylesheet. Both halves are needed and each is checked: `.card` must still BE
# a grid (`grid-column` is inert outside one, which is exactly why the overlay's copy of
# the panel always read correctly), and `.why` must span every track of it.
WHY_PANEL_CARD_SELECTOR = ".card"
WHY_PANEL_CARD_DISPLAY = ("display", "grid")
WHY_PANEL_SPAN = (".why", "grid-column", "1 / -1")

# --- HT-M7's P5: the video glyph is not a play button --------------------------------
#
# UX spec 5 rev 3: "v1.0 never decodes video", and there must be "nothing that looks like
# it could be pressed to watch the file". The shipped glyph drew a solid right-pointing
# triangle centred in a bordered box - the universal play affordance - and the owner
# pressed it and got an overlay showing a larger one.
#
# WHETHER THE REPLACEMENT READS AS FILM IS UNCATCHABLE HERE and is HT-M8's; it is a
# question about what a human wants to press. What IS holdable is the negative half, and
# it is holdable only because of a property another test in this module already pins: the
# stylesheet fetches nothing (`url(` and `@import` are both refused), so no image and no
# SVG can enter it. That dependency was itself measured, not assumed - M889 plants an
# inline SVG `data:` URI here and the fetch-nothing check kills it.
#
# A solid triangle then has FOUR available spellings in this file. This comment said
# three until M890 planted the fourth and the whole suite stayed green; the count is now
# a measured number rather than an argued one, and it is the sort of sentence that should
# be re-measured rather than trusted the next time this rule is touched. All four are
# refused inside the video glyph's rules:
#   - the CSS border trick - a `border-color` with three `transparent` sides, which is how
#     the play triangle was actually drawn (the `image` glyph's mountain uses the same
#     idiom legitimately, which is why this is scoped to the video kind rather than to the
#     whole sheet);
#   - `clip-path`, which can cut one out of any box;
#   - a play character in `content`, which needs no geometry at all - and that one is
#     refused across the WHOLE stylesheet, since a triangle typed anywhere on this page is
#     the same mistake;
#   - the same character written as a CSS numeric escape (`content: "\25B6"`), which is
#     the form that needs no non-ASCII byte in the file and is therefore the one most
#     likely to be typed. Refused by resolving escapes before the character scan.
VIDEO_GLYPH_SELECTOR = '.glyph[data-kind="video"]'
VIDEO_GLYPH_FORBIDDEN_PROPERTIES = ("clip-path",)
PLAY_TRIANGLE_CHARACTERS = ("▶", "►", "▸", "▷", "➤", "⏵")

# UX spec 5 rev 8's edited card renders three classes nothing else in the page uses, and
# `style.css` is the only file in this project that can be asked about them. Spelled here
# from what each one IS - the card's replacement for the trust display, and the Why?
# panel's heading and its restore line - never read out of the stylesheet.
#
# WHY THIS CHECK EXISTS AT ALL: an element `app.js` creates and no rule styles is not a
# missing effect, it is a card that looks like it failed to finish loading. Nothing here
# executes `app.js`, so the rendering itself is HT-M8's; that a rule EXISTS for each class
# is the half a Python test can hold, and it is the half that was missing when this task
# was picked up.
EDITED_CARD_SELECTORS = (
    '.trust[data-edited="true"]',
    ".trust-edited",
    ".why-proposed",
    ".why-restore",
)

# --- UX spec 5 rev 8's un-decide, spelled from the SPEC -----------------------------
#
# "A decided card shows the control, beside its status note, and only a decided card
# does"; "the selection panel gains it too (`[Approve] [Skip] [Not decided]`)"; "there is
# no 'reset everything' control, deliberately - the mirror of the absent 'Approve All'".
#
# THE WORD IS CHECKED AS AN EXACT LABEL, NEVER WITH `in`, and the reason is one character
# away: an undecided card's status note reads `Not decided yet`, so a containment test
# over the page's text would pass over the NOTE and say nothing about a CONTROL.
UX_5_UNDECIDE = "Not decided"

# UX spec 5's keyboard bullet names seven rows - `X`, `Z`, `C`, the `<-/->` pair, `Space`,
# `Enter` and `?`; M14 U2 swaps `Space` (Task 3's, not named yet) for the `↑/↓` pair, so the
# page's list is still seven rows. Rev 8 adds the toggle to two of them and adds NO KEY, so the row count
# is the shape of "no new key enters UX 5's list". Counted from the spec's own bullet. M14 Task 3
# (U3) names `Space` - the spec's own seventh key, back in the list - so eight.
UX_5_KEY_ROWS = 8

# UX spec 5 rev 8: the selection panel is exactly `[Approve] [Skip] [Not decided]`. The
# count is the check that catches an unfiltered whole-plan control being added HERE,
# where `RESET_ALL` below cannot see one that is worded innocently.
UX_5_SELECTION_CONTROLS = 3

# UX spec 5 rev 8's EVENT nav level (M8 Task 10). `renderNav` nests a third list under
# each month and fills it with ordinary `.nav-entry` buttons; the list itself is the one
# element the level adds, and without a rule it is not indented, so the three levels read
# as one flat column of dates instead of as the hierarchy the spec draws.
#
# Same shape and same reason as EDITED_CARD_SELECTORS: an element `app.js` creates and no
# rule styles is not a missing effect, it is a page that looks half-finished. Whether the
# indent LOOKS like a hierarchy is HT-M8's - that a rule exists and declares something is
# the half a Python test can hold.
EVENT_NAV_SELECTORS = ("#nav-tree .events",)

# The two classes `statusRow` and `undecideButton` render, spelled from what each one IS:
# the line the status note and the un-decide control share, and the control itself. Same
# shape and same reason as EDITED_CARD_SELECTORS - an element `app.js` creates and no
# rule styles is not a missing effect, it is a card that looks half-loaded.
UNDECIDE_SELECTORS = (".status-row", ".undecide")


# --- reading the shipped files ------------------------------------------------------


def _static(name: str) -> str:
    """The shipped bytes, through `importlib.resources` - the same door the server uses."""
    return (resources.files("pam.webui") / "static" / name).read_bytes().decode("utf-8")


def _markup() -> str:
    return _static("index.html")


# M14 Task 1: the review page's script is FIVE classic scripts in one global scope, loaded
# by `index.html` in this order - so "the page's script" is all five, read in that order. The
# order is spelled here, not read off the page; `tests/test_static_syntax.py` holds the page
# to it with a real HTML parser.
# M14 Task 2: + `pam-row.js` and `pam-list.js`, the list (U2), before the boot.
REVIEW_SCRIPTS = (
    "pam-core.js",
    "pam-trust.js",
    "pam-keys.js",
    # Step 6b final: `pam-review.js`, the card page, retired here.
    "pam-row.js",
    "pam-selection.js",
    "pam-list.js",
    "pam-bar.js",
    # Step 6a: the preview overlay and the name editor, on the list.
    "pam-detail.js",
    # M14 Task 5a: Welcome's folder chooser (U13), cut out of `pam-welcome.js`.
    "pam-chooser.js",
    # M14 Task 5e: Welcome's Reading step (U11).
    "pam-reading.js",
    # M14 Task 4 Step 7: Welcome (W18, U13).
    "pam-welcome.js",
    # M14 Task 6c: the Confirm screen, then Done and undo (U6, W19-W21).
    "pam-confirm.js",
    "pam-done.js",
    "pam-stopped.js",
    # M14 Task 7: History (W22, X2).
    "pam-history.js",
    "app.js",
)


def _script() -> str:
    return "\n".join(_static(name) for name in REVIEW_SCRIPTS)


def _stylesheet() -> str:
    return _static("style.css")


# --- a real parser as the oracle ----------------------------------------------------

VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


class Node:
    """One element. `parts` interleaves text and child elements in document order."""

    def __init__(self, tag: str, attrs: dict[str, str | None], line: int) -> None:
        self.tag = tag
        self.attrs = attrs
        self.line = line
        self.parts: list[object] = []

    @property
    def text(self) -> str:
        out: list[str] = []
        for part in self.parts:
            out.append(part if isinstance(part, str) else part.text)  # type: ignore[union-attr]
        return "".join(out)

    def descendants(self):
        """Depth-first, pre-order - i.e. document order."""
        for part in self.parts:
            if isinstance(part, Node):
                yield part
                yield from part.descendants()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{self.tag} line {self.line}>"


class DocumentTree(HTMLParser):
    """A strict walk. `html.parser` is lenient by design; the strictness is here.

    It does NOT infer an end tag the way a browser would, which is the point: this
    module wants to know that the document says what it means, not that a forgiving
    consumer could make something of it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {}, 0)
        self.errors: list[str] = []
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.getpos()[0])
        self._stack[-1].parts.append(node)
        if tag not in VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # `<meta />`, `<link />`, `<input />`: self-closing, never pushed. Overriding
        # this stops the base class turning it into a start plus an end.
        self._stack[-1].parts.append(Node(tag, dict(attrs), self.getpos()[0]))

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag in VOID_ELEMENTS:
            self.errors.append(f"line {line}: </{tag}> closes a void element")
            return
        if len(self._stack) == 1:
            self.errors.append(f"line {line}: stray </{tag}> with nothing open")
            return
        open_node = self._stack[-1]
        if open_node.tag != tag:
            self.errors.append(
                f"line {line}: </{tag}> does not close <{open_node.tag}> "
                f"opened at line {open_node.line}"
            )
            return
        self._stack.pop()

    def handle_data(self, data):
        self._stack[-1].parts.append(data)

    def finish(self) -> "DocumentTree":
        self.close()
        for node in self._stack[1:]:
            self.errors.append(f"<{node.tag}> opened at line {node.line} is never closed")
        return self


def _parse(markup: str) -> DocumentTree:
    tree = DocumentTree()
    tree.feed(markup)
    return tree.finish()


def _page() -> Node:
    tree = _parse(_markup())
    assert not tree.errors, "index.html does not parse: " + "; ".join(tree.errors)
    return tree.root


def _ids_in(root: Node) -> list[str]:
    """Every `id` attribute in the document, WITH duplicates - the count is the point."""
    return [
        node.attrs["id"]
        for node in root.descendants()
        if node.attrs.get("id") is not None  # type: ignore[arg-type]
    ]


def _by_id(root: Node, wanted: str) -> Node:
    found = [node for node in root.descendants() if node.attrs.get("id") == wanted]
    assert len(found) == 1, f"expected exactly one #{wanted}, found {len(found)}"
    return found[0]


def _normalize(text: str) -> str:
    return " ".join(text.split())


# --- extracting the element queries `app.js` makes ----------------------------------


def _strip_js_comments(source: str) -> str:
    """Comments out, string literals intact - because A COMMENT IS NOT A QUERY.

    Found by running this module rather than by reading it. `app.js`'s own header
    comment documents the extractor by spelling `getElementById("literal")`, and the
    first version of this file dutifully demanded an element with `id="literal"`. A
    regex over JavaScript cannot tell a call from a sentence about a call, so the
    comments are removed before the regexes ever see the source.

    A string-aware scan, not a regex, because `"//"` and `"/*"` inside a string literal
    are text. The one JS construct it does not model is a REGEX LITERAL containing a
    quote or a slash (`/["']/`); `app.js` contains no regex literal, and if one ever
    appears the failure is LOUD in both directions - a mis-strip that eats code trips
    the floor and the anchor set below, and one that leaves a comment behind demands an
    id that does not exist.
    """
    out: list[str] = []
    index = 0
    end = len(source)
    quote: str | None = None
    while index < end:
        char = source[index]
        if quote is not None:
            if char == "\\":
                out.append(source[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = None
            out.append(char)
            index += 1
            continue
        if char in "\"'`":
            quote = char
            out.append(char)
            index += 1
            continue
        pair = source[index : index + 2]
        if pair == "/*":
            close = source.find("*/", index + 2)
            assert close != -1, "unterminated block comment in app.js"
            # A space, so a comment can never glue two tokens together.
            out.append(" ")
            index = close + 2
            continue
        if pair == "//":
            newline = source.find("\n", index)
            out.append(" ")
            index = end if newline == -1 else newline
            continue
        out.append(char)
        index += 1
    assert quote is None, "unterminated string literal in app.js"
    return "".join(out)


# Loose: every occurrence, whatever spelling. Strict: only the spellings the docstring
# claims. The two counts are asserted equal, so an unhandled spelling is a failure
# rather than a silent miss.
_GET_BY_ID_ANY = re.compile(r"\bgetElementById\s*\(")
_GET_BY_ID_STRICT = re.compile(r"""\bgetElementById\((?:"([^"'`\\\n]*)"|'([^"'`\\\n]*)')\)""")
_SELECTOR_ANY = re.compile(r"\bquerySelector(?:All)?\s*\(")
_SELECTOR_STRICT = re.compile(
    r"""\bquerySelector(?:All)?\((?:"([^"'`\\\n]*)"|'([^"'`\\\n]*)')\)"""
)
_SIMPLE_ID_SELECTOR = re.compile(r"^#([A-Za-z][\w-]*)$")


def _literals(pattern: re.Pattern[str], source: str) -> list[str]:
    return [
        match.group(1) if match.group(1) is not None else match.group(2)
        for match in pattern.finditer(source)
    ]


def assert_every_element_query_is_a_handled_spelling(source: str) -> None:
    """Every element query in `source` must be one the extractor can actually read."""
    loose_ids = len(_GET_BY_ID_ANY.findall(source))
    strict_ids = len(_GET_BY_ID_STRICT.findall(source))
    assert loose_ids == strict_ids, (
        f"{loose_ids - strict_ids} getElementById call(s) use a spelling this "
        "extractor cannot read (template literal, computed name, or a call split "
        "across lines). Change the call site or extend the extractor - do not leave "
        "it checking less than it appears to."
    )
    loose_sel = len(_SELECTOR_ANY.findall(source))
    strict_sel = len(_SELECTOR_STRICT.findall(source))
    assert loose_sel == strict_sel, (
        f"{loose_sel - strict_sel} querySelector/querySelectorAll call(s) use a "
        "spelling this extractor cannot read."
    )


def queried_ids(source: str) -> set[str]:
    """The ids `source` asks the document for, by the two handled spellings."""
    source = _strip_js_comments(source)
    assert_every_element_query_is_a_handled_spelling(source)
    ids = set(_literals(_GET_BY_ID_STRICT, source))
    for selector in _literals(_SELECTOR_STRICT, source):
        match = _SIMPLE_ID_SELECTOR.match(selector.strip())
        if match:
            ids.add(match.group(1))
    return ids


# UX spec 5 names these controls; the page cannot function without querying them, and
# they are spelled here from the wireframe rather than from `app.js`, so a page that
# stopped reaching for one is a failure rather than a smaller extraction.
# M14 Task 2 Step 6b final: the card page's anchors (`attention-filter`, `tier-filter`,
# `status-filter`, `counts`, `grid`, `load-more`, `smart-banner`) are retired with it; the
# list's anchors take their place - W2's progress line, W5's tabs, U2's left panel and the
# list itself, and W3/W4's callout - spelled from U2 and U8, never read from `pam-list.js`.
ANCHOR_IDS = frozenset(
    {
        "search",
        "error-banner",
        # M14 Task 6c (W16): the footer's `Rename N files`, in place of `footer-command`.
        "footer-rename",
        # Task 12. Every one of these is a surface UX 5/5a names, and a page that
        # stopped reaching for one has stopped offering it.
        "sort",
        "editor",
        "editor-stem",
        "overlay",
        "overlay-image",
        "shortcuts",
        "resume-banner",
        "jump-undecided",
        "estimate",
        # Step 6b final: the list (U2, U8 W2-W5)
        "list",
        "progress-approved",
        "progress-rest",
        "tab-all",
        "tab-attention",
        "trust-filter",
        "list-tree",
        "look-callout",
        "accept-all",
    }
)

# The page has a header, a nav, a grid region, a footer, two overlays and an editor, and
# every one of them has controls. An extraction well below this is not a leaner page, it
# is a broken regex.
ID_FLOOR = 45


def assert_extraction_is_plausible(ids: set[str]) -> None:
    """Three independent reasons to believe the extraction below is not vacuous.

    FACTORED OUT SO IT CAN BE WATCHED FAILING. This review measured that `ID_FLOOR = 0`
    and `ANCHOR_IDS = frozenset()` each left the entire suite green (M718, M719): the
    expectations were asserted, and not one of them was ever proven to fire. That is
    `WRITE_FLAGS = ()` again (M712), and CLAUDE.md's rule is one line - *a guard is
    proven on a planted violation, never by reading*. The negative controls below drive
    this function with extractions that must be refused.
    """
    assert ids, "the extractor read NO ids out of app.js - the cross-check is vacuous"
    assert len(ids) >= ID_FLOOR, f"only {len(ids)} ids extracted, expected >= {ID_FLOOR}"
    assert ANCHOR_IDS <= ids, f"app.js never queries {sorted(ANCHOR_IDS - ids)}"


def test_no_spec_derived_expectation_in_this_module_is_empty():
    """THE ONE SHAPE A NEGATIVE CONTROL CANNOT CATCH, measured rather than assumed.

    `ANCHOR_IDS = frozenset()` survived the negative controls that were written to close
    it (M743). `@pytest.mark.parametrize("anchor", sorted(ANCHOR_IDS))` over an empty set
    is SKIPPED by pytest, not failed - so nineteen controls vanished, the count went
    `1841 passed, 1 skipped` -> `1822 passed, 2 skipped`, and the run still read green.
    **A control parametrized over the very expectation it protects disappears with it.**

    So every spec-derived expectation is asserted non-empty here, unparametrized, in one
    place where nothing can make the assertion itself go away. This is the third form of
    the same defect in three milestones - `repo.ENRICHERS`, `WRITE_FLAGS = ()`, and now
    a whole module of them - and the general rule is in CLAUDE.md.
    """
    for name, expectation in (
        ("ANCHOR_IDS", ANCHOR_IDS),
        ("PLAYER_TAGS", PLAYER_TAGS),
        ("CONTROL_TAGS", CONTROL_TAGS),
        ("CONTROL_ROLES", CONTROL_ROLES),
        ("VOID_ELEMENTS", VOID_ELEMENTS),
        ("UX_7_TOKENS", UX_7_TOKENS),
        ("UX_7_BAND_COLOURS", UX_7_BAND_COLOURS),
        ("UX_7_SPACING", UX_7_SPACING),
        ("UX_5_KEYBOARD", UX_5_KEYBOARD),
        ("UX_5_PRESETS", UX_5_PRESETS),
        ("UX_5_RUNTIME_COPY", UX_5_RUNTIME_COPY),
        ("UX_5_COUNTS_LINE", UX_5_COUNTS_LINE),
        ("UX_5_DELETED_COPY", UX_5_DELETED_COPY),
        ("PLAY_TRIANGLE_CHARACTERS", PLAY_TRIANGLE_CHARACTERS),
        ("VIDEO_GLYPH_FORBIDDEN_PROPERTIES", VIDEO_GLYPH_FORBIDDEN_PROPERTIES),
        ("EDITED_CARD_SELECTORS", EDITED_CARD_SELECTORS),
        ("UNDECIDE_SELECTORS", UNDECIDE_SELECTORS),
        ("EVENT_NAV_SELECTORS", EVENT_NAV_SELECTORS),
        ("UX_5_UNDECIDE", UX_5_UNDECIDE),
        ("UX_5_KEY_ROWS", UX_5_KEY_ROWS),
        ("A4_TOGGLE_CLAUSES", A4_TOGGLE_CLAUSES),
        ("UX_5_SELECTION_CONTROLS", UX_5_SELECTION_CONTROLS),
        ("SEARCHABLE_NOW", SEARCHABLE_NOW),
        ("NOT_SEARCHABLE_YET", NOT_SEARCHABLE_YET),
        ("SPEC_8_2_SORTS", SPEC_8_2_SORTS),
    ):
        assert expectation, (
            f"{name} is empty, so every check that loops over it asserts nothing - and "
            "a control parametrized over it is SKIPPED rather than failed"
        )


# --- the parser itself, proven before it is trusted ---------------------------------


def test_the_document_walk_rejects_a_stray_end_tag():
    assert _parse("<div><p>x</p></div></span>").errors


def test_the_document_walk_rejects_an_unclosed_element():
    errors = _parse("<div><p>x</div>").errors
    assert errors, "an unclosed <p> was walked as if the document were well formed"


def test_the_document_walk_rejects_an_element_left_open_at_eof():
    """The one fault `finish()` ALONE can see - and nothing fed it one (closes M725).

    The test above feeds `<div><p>x</div>`, whose `</div>` trips the MISMATCH branch, so
    the case named for `finish()` was being killed by a different branch entirely:
    deleting `finish()`'s loop left the whole suite green. CLAUDE.md's rule, measured
    again - *a negative test can pass for the wrong reason*. This document's only fault
    is an element still open when the parser runs out of input, which is what a
    truncated `index.html` looks like.
    """
    errors = _parse("<div><p>x</p>").errors
    assert errors, "a <div> left open at EOF was walked as a well-formed document"
    assert any("never closed" in error for error in errors), errors


def test_the_document_walk_rejects_crossed_tags():
    assert _parse("<b><i>x</b></i>").errors


def test_the_document_walk_accepts_void_elements_written_either_way():
    assert not _parse("<div><br><img src='x'><input /></div>").errors


def test_the_document_walk_accepts_a_well_formed_document():
    assert not _parse("<div id='a'><p>x <em>y</em></p></div>").errors


# --- the comment stripper, proven before it is trusted ------------------------------


@pytest.mark.parametrize(
    ("source", "kept", "dropped"),
    [
        ('var a = 1; /* getElementById("ghost") */', "var a = 1;", "ghost"),
        ('var a = 1; // getElementById("ghost")', "var a = 1;", "ghost"),
        # A string is TEXT, not a comment: neither of these opens one.
        ('var a = "keep//me";', 'var a = "keep//me";', "\x00"),
        ('var a = "keep/*me*/too";', 'var a = "keep/*me*/too";', "\x00"),
        ("var a = 'keep//me';", "var a = 'keep//me';", "\x00"),
        ('var a = "esc\\"//still";', 'var a = "esc\\"//still";', "\x00"),
    ],
)
def test_the_comment_stripper_removes_comments_and_leaves_string_literals_alone(
    source, kept, dropped
):
    stripped = _strip_js_comments(source)
    assert kept in stripped
    assert dropped not in stripped


def test_the_comment_stripper_is_what_stops_a_comment_being_read_as_a_query():
    """The bug this module actually shipped with, kept as a regression.

    `app.js`'s header comment documents the extractor by spelling a call, and the first
    version of this file demanded an element with `id="literal"`.
    """
    documented = '/* the extractor reads document.getElementById("literal") */'
    assert queried_ids(documented) == set()


# --- the id extractor, proven before it is trusted ----------------------------------


def test_the_extractor_reads_exactly_the_spellings_its_docstring_claims():
    handled = """
      var a = document.getElementById("alpha");
      var b = document.getElementById('beta');
      var c = root.querySelector("#gamma");
      var d = root.querySelectorAll("#delta");
      var e = card.querySelector(".not-an-id");
      var f = document.querySelectorAll("button, select, input");
    """
    assert queried_ids(handled) == {"alpha", "beta", "gamma", "delta"}


@pytest.mark.parametrize(
    "source",
    [
        "document.getElementById(`alpha`)",
        "document.getElementById(name)",
        'document.getElementById("al" + "pha")',
        'document.getElementById(\n  "alpha"\n)',
        'document.getElementById ("alpha")',
        "root.querySelector(`#alpha`)",
        "root.querySelectorAll(selector)",
    ],
)
def test_an_unhandled_query_spelling_fails_loudly_instead_of_extracting_nothing(source):
    """THE TRAP THIS MODULE IS BUILT AROUND.

    Each of these is a real query the page could make and the regex cannot see. Without
    the count gate the cross-check would simply not know about it, and an id could be
    deleted from `index.html` with the suite green - which is the `test_ci_lanes`
    failure exactly, one language further down.
    """
    with pytest.raises(AssertionError):
        queried_ids(source)


def test_the_extractor_finds_a_plausible_number_of_ids_in_the_real_page():
    """The assertion that stops every cross-check below from passing vacuously.

    A regex that matches nothing satisfies "every queried id exists exactly once" for
    free. Three independent things say the number is real: a non-zero floor, the
    spec-named anchor set, and the count gate inside `queried_ids` itself.
    """
    assert_extraction_is_plausible(queried_ids(_script()))


def test_the_plausibility_check_refuses_an_extraction_that_went_quiet():
    """The FLOOR's own negative control (closes M718).

    An extraction holding every spec-named anchor and nothing else is the shape a
    half-broken regex actually produces - not zero ids, just far too few - and the floor
    is the only thing that sees it. The `<` assertion is deliberate and load-bearing: it
    is what fails outright if `ID_FLOOR` is ever lowered under the anchor set.
    """
    with pytest.raises(AssertionError):
        assert_extraction_is_plausible(set())
    assert len(ANCHOR_IDS) < ID_FLOOR, (
        "the floor no longer sits above the anchor set, so the control below cannot "
        "isolate it - and a lowered floor is exactly what this test exists to catch"
    )
    with pytest.raises(AssertionError):
        assert_extraction_is_plausible(set(ANCHOR_IDS))


@pytest.mark.parametrize("anchor", sorted(ANCHOR_IDS))
def test_the_plausibility_check_refuses_an_extraction_missing_any_one_anchor(anchor):
    """The ANCHOR SET's own negative control (closes M719), one spec-named id at a time.

    The real extraction minus one anchor is still comfortably over the floor, so only
    the anchor operand can refuse it. `ANCHOR_IDS = frozenset()` makes every one of
    these stop raising.
    """
    ids = queried_ids(_script()) - {anchor}
    assert len(ids) >= ID_FLOOR, "this control must isolate the anchors, not the floor"
    with pytest.raises(AssertionError):
        assert_extraction_is_plausible(ids)


# --- the cross-check ----------------------------------------------------------------


def test_index_html_is_well_formed_enough_to_walk():
    """A real parser, used as the oracle for every structural read beside it."""
    tree = _parse(_markup())
    assert not tree.errors, "; ".join(tree.errors)
    assert [node.tag for node in tree.root.parts if isinstance(node, Node)] == ["html"]


def _cross_check(
    markup: str, script: str, script_name: str = "app.js", page_name: str = "index.html"
) -> None:
    """Every id `script` queries is declared exactly once in `markup`.

    The two names are parameters rather than literals because M11 Task 10 gave this
    project a SECOND page (`faces.html` + `faces.js`), and a failure message naming the
    wrong file is a failure message that sends the reader to the wrong place.
    """
    tree = _parse(markup)
    assert not tree.errors, "; ".join(tree.errors)
    present = _ids_in(tree.root)
    for wanted in sorted(queried_ids(script)):
        assert present.count(wanted) == 1, (
            f"{script_name} queries #{wanted}, which {page_name} declares"
            f" {present.count(wanted)} time(s)"
        )


def test_every_id_app_js_queries_exists_exactly_once_in_index_html():
    """The JS is the query; the HTML parse is the oracle."""
    _cross_check(_markup(), _script())


def test_the_cross_check_sees_an_id_the_real_page_stopped_declaring():
    """The plan's own mutation for this task, made permanent and run in memory.

    Nothing on disk is touched: the shipped markup is read, one id `app.js` genuinely
    queries is renamed in the string, and the cross-check is required to fail. Without
    this, "every queried id exists" is a claim about an oracle nobody has watched fail.
    """
    script = _script()
    victim = sorted(queried_ids(script))[0]
    broken = _markup().replace(f'id="{victim}"', 'id="renamed-by-this-test"', 1)
    assert broken != _markup(), f'#{victim} is not declared as id="{victim}" in the markup'
    with pytest.raises(AssertionError):
        _cross_check(broken, script)


def test_the_cross_check_sees_a_duplicated_id():
    script = 'document.getElementById("grid");'
    _cross_check('<div id="grid"></div>', script)
    with pytest.raises(AssertionError):
        _cross_check('<div id="grid"></div><span id="grid"></span>', script)


def test_no_id_is_declared_twice_in_index_html():
    """Duplicates make `getElementById` a coin toss, silently and only sometimes."""
    present = _ids_in(_page())
    duplicated = sorted({name for name in present if present.count(name) > 1})
    assert not duplicated, f"declared more than once: {duplicated}"


# --- UX spec 5's normative copy ------------------------------------------------------


def test_the_footer_carries_w16s_sentence_and_the_rename_button():
    """W16 (M14 Task 6c): the footer says W16's sentence, verbatim, and holds the button whose
    label `pam-confirm.js` draws (`Rename N files` / `Approve names to rename them`). The old
    sentence and its `pam apply N` slot are gone with the capability that made them true."""
    page = _page()
    text = _normalize(_by_id(page, "footer").text)
    assert text == W16_FOOTER_SENTENCE, f"footer reads {text!r}"
    assert _by_id(page, "footer-rename") is not None
    assert "footer-rename" in queried_ids(_script())
    assert "footer-command" not in set(_ids_in(page))
    assert "Nothing is renamed from this page" not in _static("index.html")


def test_the_dead_server_banner_carries_ux_5s_sentence_verbatim():
    assert _normalize(_by_id(_page(), "error-banner").text) == UX_5_SERVER_GONE


def test_the_page_title_is_pam_and_no_longer_pam_review():
    """UX 5 (rev 16, M14 Task 5e): "The page's `<title>` is `pam`." - spelled from the spec, read
    from the page with the real parser (Task 5 review R2, M3158: nothing held the title)."""
    titles = [_normalize(node.text) for node in _page().descendants() if node.tag == "title"]
    assert titles == ["pam"], titles


def test_the_empty_filter_state_carries_ux_5s_line_and_a_clear_filters_control():
    empty = _by_id(_page(), "empty-state")
    assert _normalize(empty.text) == UX_5_EMPTY_FILTER
    buttons = [node for node in empty.descendants() if node.tag == "button"]
    assert len(buttons) == 1, "the [clear filters] half of the line is not a control"
    assert _normalize(buttons[0].text) == "clear filters"


def test_the_needs_a_look_tab_replaces_the_header_attention_filter():
    """Was `test_the_first_control_in_the_header_is_smart_reviews_attention_filter` (UX 5a).
    M14 Task 2 Step 6b final, to U8's W5: the card page's `[Needs attention (N)]` button is
    gone, and 5a's one-click filter is the list's second tab, `Needs a look K`."""
    page = _page()
    assert "attention-filter" not in _ids_in(page), "the card page's header button is back"
    labels = [_normalize(node.text) for node in page.descendants() if node.tag == "button"]
    assert not any(label.startswith(UX_5A_ATTENTION) for label in labels), labels
    tabs = [node for node in _by_id(page, "list-tabs").descendants() if node.tag == "button"]
    assert [node.attrs.get("id") for node in tabs] == ["tab-all", "tab-attention"]
    assert _normalize(tabs[1].text).startswith(W5_ATTENTION_TAB)


def assert_no_preset_is_offered(labels: set[str]) -> None:
    for preset in UX_5_PRESETS:
        assert preset not in labels, f"the page still offers [{preset}] (U2 retired it)"


def test_the_preset_buttons_are_gone_and_accept_all_stays():
    """Was `test_both_bulk_presets_exist`. M14 Task 2 Step 6b final (U2): `Approve High` and
    `Approve High+Medium` leave the page - their server route stays - and `Accept all` stays
    as the top shortcut."""
    labels = {_normalize(node.text) for node in _page().descendants() if node.tag == "button"}
    assert_no_preset_is_offered(labels)
    assert "Accept all" in labels


def test_the_preset_check_refuses_a_page_that_still_offers_one():
    """Was `test_the_preset_check_refuses_a_page_that_offers_neither` (M737), rewritten with
    the check it controls: each retired preset, planted beside `Accept all`, is refused."""
    for preset in ("Approve High", "Approve High+Medium"):
        with pytest.raises(AssertionError):
            assert_no_preset_is_offered({"Accept all", preset})


APPROVE_ALL = re.compile(r"approve\s*[-_]?\s*all", re.IGNORECASE)

# A control is not only a `<button>`. Found by planting one (M732): a
# `<div role="button">Approve All</div>` was invisible to the tag list alone, and the
# forbidden control is forbidden however it is spelled in markup.
CONTROL_TAGS = frozenset({"button", "a", "label", "option", "summary"})
CONTROL_ROLES = frozenset({"button", "link", "menuitem", "menuitemcheckbox", "tab"})


def _controls(root: Node) -> list[Node]:
    """Every element a reviewer could click, whatever tag it happens to wear."""
    return [
        node
        for node in root.descendants()
        if node.tag in CONTROL_TAGS
        or node.attrs.get("role") in CONTROL_ROLES
        or "tabindex" in node.attrs
    ]


def assert_no_approve_all_control(root: Node, script: str) -> None:
    """Vision 6a.3's trust contract, as far as a parse can hold it.

    WHAT THIS CANNOT SEE, stated rather than glossed: a control that offers the same
    thing under a different NAME - `[Approve everything]`, `[Accept all]`. `APPROVE_ALL`
    is a regex over one spelling and no enumeration of synonyms would be honest, so the
    absence of any such control is on the HT-M7 list as a human check. The consequence
    is bounded, and spec 8.2 says why in as many words: *a contract enforced in the
    frontend is not enforced*. `decide-filter` takes every `low`-tier item out of any
    approve server-side, so a differently-worded button still cannot cross the line the
    contract is actually about.
    """
    for node in _controls(root):
        label = _normalize(node.text)
        assert not APPROVE_ALL.search(label), f"the page carries an [{label}] control"
    assert not APPROVE_ALL.search(script), "app.js builds an Approve All control"


def test_there_is_no_approve_all_control():
    """DELIBERATE, and the product's trust contract rather than a wireframe detail.

    Low-tier items are never bulk-approvable (vision 6a.3); the refusal is enforced
    server-side, and the page must not offer a control that asks for it.

    READ FROM THE PARSE AND THE STRIPPED SCRIPT, not from raw text - found by running
    this, not by reading it. `index.html`'s own comment says "there is deliberately NO
    'Approve All'", and a grep over the file cannot tell that sentence from a button.
    A control is an ELEMENT WITH A LABEL, so that is what is checked, in the markup and
    in every string literal the script could build one from.
    """
    assert_no_approve_all_control(_page(), _strip_js_comments(_script()))


@pytest.mark.parametrize(
    "markup",
    [
        "<div><button>Approve All</button></div>",
        "<div><a href='x'>approve all</a></div>",
        "<div><summary>Approve  all</summary></div>",
        # The two spellings the tag list alone could not see (M732).
        "<div><div role='button'>Approve All</div></div>",
        "<div><span tabindex='0'>Approve-all</span></div>",
    ],
)
def test_the_approve_all_check_refuses_a_planted_control(markup):
    """A guard is proven on a planted violation, never by reading."""
    with pytest.raises(AssertionError):
        assert_no_approve_all_control(_parse(markup).root, "")


def test_the_approve_all_check_refuses_a_script_that_builds_one():
    with pytest.raises(AssertionError):
        assert_no_approve_all_control(
            _parse("<div></div>").root, 'make("button", "", "Approve All")'
        )


# UX spec 5 rev 8 / A4: "there is no 'reset everything' control, deliberately - the
# mirror of the absent 'Approve All'". Un-deciding is per item and per SELECTION;
# `decide-filter`'s `reset` action stays reachable server-side and unexposed as a
# whole-plan button, which A4 records so that a later deletion gate does not fire on it.
RESET_ALL = re.compile(
    r"\b(reset|un-?decide|undo)\s*[-_]?\s*(the\s+)?(all|everything|plan)\b", re.IGNORECASE
)


def assert_no_reset_all_control(root: Node, script: str) -> None:
    """The mirror of `assert_no_approve_all_control`, and bounded in the same way.

    A synonym - `[Start over]`, `[Clear my decisions]` - is invisible to a regex over one
    spelling, and no enumeration of synonyms would be honest. What makes the consequence
    bounded here is different from the Approve All case, and worth saying rather than
    borrowing: an unfiltered reset destroys no file and no name (spec 8.2's `reset`
    touches `status` alone and leaves `new_filename` where it is), so the cost of one is
    lost REVIEW WORK, not lost data. The absence of a differently-worded one is HT-M8's.
    """
    for node in _controls(root):
        label = _normalize(node.text)
        assert not RESET_ALL.search(label), f"the page carries a [{label}] control"
    assert not RESET_ALL.search(script), "app.js builds a whole-plan reset control"


def test_there_is_no_reset_everything_control():
    """UX spec 5 rev 8, and it is a deliberate absence rather than an oversight.

    Read from the parse and the STRIPPED script for the same reason its mirror is:
    `index.html` and `app.js` both carry comments saying this control does not exist, and
    a grep over either file cannot tell that sentence from a button.
    """
    assert_no_reset_all_control(_page(), _strip_js_comments(_script()))


@pytest.mark.parametrize(
    "markup",
    [
        "<div><button>Reset All</button></div>",
        "<div><button>reset the plan</button></div>",
        "<div><a href='x'>Undecide all</a></div>",
        # The two spellings the tag list alone could not see (M732's shape).
        "<div><div role='button'>Un-decide everything</div></div>",
        "<div><span tabindex='0'>Reset-all</span></div>",
    ],
)
def test_the_reset_all_check_refuses_a_planted_control(markup):
    with pytest.raises(AssertionError):
        assert_no_reset_all_control(_parse(markup).root, "")


def test_the_reset_all_check_refuses_a_script_that_builds_one():
    with pytest.raises(AssertionError):
        assert_no_reset_all_control(
            _parse("<div></div>").root, 'make("button", "", "Reset all")'
        )


def test_the_reset_all_check_still_permits_the_per_item_and_per_selection_controls():
    """The guard must not be so wide that it forbids what UX 5 rev 8 ASKS FOR.

    Both controls the amendment adds are planted here and required to pass: a guard that
    refused them would have to be weakened the moment the feature landed, and a guard
    that gets weakened is a guard nobody trusts.
    """
    permitted = f"<div><button>{UX_5_UNDECIDE}</button><button>Skip selected</button></div>"
    assert_no_reset_all_control(_parse(permitted).root, f'COPY.notDecided = "{UX_5_UNDECIDE}"')


def test_the_two_search_word_lists_cannot_both_claim_one_word():
    """A word in both lists makes the placeholder check unsatisfiable, and the failure
    would read as a copy bug rather than as the list bug it is. Asserted here so the
    next milestone that moves `place` or `people` across cannot leave a copy behind."""
    assert not set(SEARCHABLE_NOW) & set(NOT_SEARCHABLE_YET)


def assert_placeholder_promises_only_todays_search(placeholder: str) -> None:
    """Every word search matches today, and no word it does not (UX 5 rev 6/rev 8)."""
    assert placeholder, "the search box has no placeholder"
    lowered = placeholder.lower()
    for today in SEARCHABLE_NOW:
        assert today in lowered, (
            f"the placeholder does not name {today!r}, which search DOES match - a "
            f"filter the box never mentions is as unfindable as one it cannot keep: "
            f"{placeholder!r}"
        )
    for later in NOT_SEARCHABLE_YET:
        assert later not in lowered, (
            f"the placeholder promises {later!r}, which search does not match until a "
            "later milestone - a box that silently returns nothing is a dead end"
        )


def test_the_search_placeholder_names_only_what_search_matches_today():
    """A4 / UX spec 5 rev 10: names, event labels AND places in M10, nothing beyond."""
    assert_placeholder_promises_only_todays_search(
        _by_id(_page(), "search").attrs.get("placeholder") or ""
    )


@pytest.mark.parametrize(
    "placeholder",
    [
        "",
        "Search",
        "Search names, people, events, places…",
        "Search by name or place",
        # M9 Task 11's own failure mode, in both directions. The first is what M7
        # shipped: correct until the predicate widened under it, and silent about a
        # filter the box now honours. The second matches events and forgets it also
        # matches the two names it always did.
        "Search old and new file names",
        "Search event labels",
        # M10 Task 10's own failure mode, the same shape one milestone on: exactly what
        # M9 shipped, correct until `place` joined the predicate under it.
        "Search old and new file names, and event labels",
    ],
)
def test_the_placeholder_check_refuses_a_promise_search_cannot_keep(placeholder):
    """UX 5's own v1.0 wireframe string is one of these, and A4 is the reason it is
    not what M7 ships. The two M9 rows are the reason the check is a PAIR of lists:
    a one-directional check would have let the predicate and the copy drift apart."""
    with pytest.raises(AssertionError):
        assert_placeholder_promises_only_todays_search(placeholder)


def test_the_review_page_carries_no_phone_notice():
    """Was `test_a_narrow_viewport_gets_a_notice_and_not_a_broken_layout` (UX 7). M14 Task 2
    Step 6b final (U4): the review page works at any width, so its notice is gone; the
    layout still breaks at UX 7's old boundary, where the shell stacks the nav above the
    list. `tests/test_static_syntax.py` holds the CSS half (no rule hides the review page)."""
    page = _page()
    assert "narrow-notice" not in _ids_in(page), "the review page's phone notice is back (U4)"
    assert "larger screen" not in _normalize(page.text).lower()
    boundaries = {
        int(value)
        for value in re.findall(
            r"@media\s*\(\s*max-width\s*:\s*(\d+)px\s*\)", _strip_css_comments(_stylesheet())
        )
    }
    assert boundaries & {UX_7_MIN_WIDTH, UX_7_MIN_WIDTH - 1}, (
        f"no stylesheet boundary at UX 7's {UX_7_MIN_WIDTH}px; found {sorted(boundaries)}"
    )


# --- M7 Task 12: the interaction surfaces, as far as a parse can see them -----------
#
# Everything below checks that a surface EXISTS, is spelled the way UX 5/5a spells it,
# and starts in the state the page needs it to start in. Not one of them checks that a
# key, a click or a fetch does anything - there is no DOM here. The module docstring
# lists what that leaves to HT-M7, item by item.


def _definition_pairs(root: Node) -> list[tuple[str, str]]:
    """The `<dt>`/`<dd>` pairs under `root`, in document order."""
    terms = [_normalize(node.text) for node in root.descendants() if node.tag == "dt"]
    definitions = [_normalize(node.text) for node in root.descendants() if node.tag == "dd"]
    assert len(terms) == len(definitions), (
        f"{len(terms)} <dt> and {len(definitions)} <dd> - the list does not pair up"
    )
    return list(zip(terms, definitions, strict=True))


def _labels_in(root: Node, tag: str = "button") -> list[str]:
    return [_normalize(node.text) for node in root.descendants() if node.tag == tag]


def assert_every_ux_5_key_is_named_with_what_it_does(pairs: list[tuple[str, str]]) -> None:
    assert pairs, "the shortcut overlay lists nothing at all"
    for key, expected_words in UX_5_KEYBOARD:
        rows = [(term, meaning) for term, meaning in pairs if key in term]
        assert len(rows) == 1, f"{key} is named in {len(rows)} rows of the shortcut list"
        meaning = rows[0][1].lower()
        for word in expected_words:
            assert word in meaning, (
                f"the {key} row reads {rows[0][1]!r}, which never says {word!r}"
            )


def test_the_shortcut_overlay_names_every_ux_5_key_and_what_it_does():
    """UX spec 5's seven keys, spelled from the spec and looked up in the page.

    A shortcut list that names a key without saying what it does is a list nobody can
    learn from, so the description is checked too - and UX 7 makes this overlay "the
    feature discovery surface", which is a promise about the DESCRIPTIONS.
    """
    assert_every_ux_5_key_is_named_with_what_it_does(
        _definition_pairs(_by_id(_page(), "shortcuts"))
    )


@pytest.mark.parametrize(
    "pairs",
    [
        [("Q", "does nothing")],
        # Every key named, none of them explained: UX 7's "feature discovery surface"
        # is a promise about the descriptions, not about the letters.
        [(key, "press it") for key, _words in UX_5_KEYBOARD],
    ],
)
def test_the_keyboard_check_refuses_a_shortcut_list_that_teaches_nothing(pairs):
    """Closes M736: `UX_5_KEYBOARD = ()` left this test asserting `pairs` and no more."""
    with pytest.raises(AssertionError):
        assert_every_ux_5_key_is_named_with_what_it_does(pairs)


def test_the_shortcut_overlay_doubles_as_the_feature_discovery_surface():
    """UX spec 7: "the shortcut overlay (`?`) doubles as the feature discovery surface".

    So it must name at least the two controls a reviewer would otherwise never find -
    the [Why?] breakdown, which UX 5 expects to be the most-clicked control in the UI,
    and the restore button an edited name grows.
    """
    text = _normalize(_by_id(_page(), "shortcuts").text)
    assert "Why?" in text, "the shortcut overlay never mentions the Why? breakdown"
    assert "↺" in text, "the shortcut overlay never mentions the restore control"


def test_the_shortcut_overlay_starts_hidden():
    assert "hidden" in _by_id(_page(), "shortcuts").attrs


def test_the_preview_overlay_is_a_full_review_surface_and_starts_hidden():
    """UX spec 5: "reviewers of 20,000 photos live here, not in the grid" - so the
    overlay owns a large image, the metadata panel and the Why? breakdown, not a
    lightbox with a picture in it."""
    page = _page()
    overlay = _by_id(page, "overlay")
    assert "hidden" in overlay.attrs, "the preview overlay does not start closed"
    present = {node.attrs.get("id") for node in overlay.descendants()}
    for region in ("overlay-stage", "overlay-image", "overlay-glyph", "overlay-meta"):
        assert region in present, f"the overlay has no #{region}"
    assert "overlay-why" in present, "the overlay carries no Why? breakdown region"


def test_the_preview_overlay_offers_the_same_three_decisions_a_card_does():
    """UX spec 5: "the same decision keys as the grid". The keys are HT-M7's; that the
    overlay carries the three controls they stand for is checkable here."""
    labels = _labels_in(_by_id(_page(), "overlay"))
    for wanted in ("Approve", "Skip", "Edit name"):
        assert wanted in labels, f"the overlay has no [{wanted}]; found {labels}"


# M14 Task 2 Step 6b final: the card page's selection panel is RETIRED; Task 3 rebuilds
# multi-select as W13's bar, and between Tasks 2 and 3 there is none (the Task 2 ruling).
# `UX_5_SELECTION_CONTROLS` stays as the spelled count of UX 5 rev 8's panel, which W13's
# bar must be checked against when Task 3 lands it.
RETIRED_SELECTION_LABELS = ("Approve selected", "Skip selected")


def assert_the_card_selection_panel_is_gone(page: Node) -> None:
    assert "selection" not in _ids_in(page), "the card page's #selection panel is back"
    for label in RETIRED_SELECTION_LABELS:
        assert label not in _labels_in(page), f"the page offers [{label}] again"


def test_the_card_selection_panel_is_gone_until_task_3():
    """Was `test_the_selection_panel_offers_ux_5_rev_8s_third_control`: the panel it pinned
    is retired, so the check is now that it is gone."""
    assert_the_card_selection_panel_is_gone(_page())


@pytest.mark.parametrize(
    "panel",
    [
        "<div id='selection'><button>Not decided</button></div>",
        "<div><button>Approve selected</button></div>",
    ],
)
def test_the_selection_check_refuses_a_page_that_still_carries_the_panel(panel):
    """Was `test_the_selection_panel_count_refuses_a_fourth_control_however_it_is_spelled`,
    rewritten with the check it controls; both literals are written out, not derived."""
    with pytest.raises(AssertionError):
        assert_the_card_selection_panel_is_gone(_parse(f"<div>{panel}</div>").root)


def test_the_preview_overlay_offers_the_un_decide_control_and_it_starts_hidden():
    """UX spec 5 rev 8: "a decided card shows the control ... and only a decided card
    does", and the overlay is a full review surface carrying the same controls.

    So the markup half is two things: the control exists in the overlay, and it is NOT on
    screen before an item has been decided. Which items it appears on is `renderOverlay`'s
    and therefore HT-M8's - there is no DOM here - but a control that shipped visible on
    an undecided item would be offering to un-decide something nobody decided, and that
    is checkable from the attribute.
    """
    page = _page()
    labels = _labels_in(_by_id(page, "overlay"))
    assert UX_5_UNDECIDE in labels, (
        f"the preview overlay offers no [{UX_5_UNDECIDE}] control; found {labels}"
    )
    assert "hidden" in _by_id(page, "overlay-reset").attrs, (
        "the overlay's un-decide control starts visible, so an undecided item would be"
        " offered a way back out of a decision it has not made"
    )


# A4 point 2, as two PAIRINGS rather than as three loose words: "`X` on an APPROVED item
# resets it; `Z` on a SKIPPED one does the same". Each key must be named next to the
# status it comes FROM, within one sentence (the cap is generous;  the `[^.]` is what actually bounds it).
#
# THE PAIRING IS THE ASSERTION BECAUSE THE THREE WORDS ALONE ARE NOT (M861). A line
# reading "Press X on a name you have already approved or press it again on one you
# skipped" names `X`, names `Z` (in its opening clause) and says "not decided" - and is
# FALSE: `X` on a skipped item approves it, which is A4's third clause. A copy check that
# cannot tell that line from the right one is checking that words are present, not that
# the page explains anything.
A4_TOGGLE_CLAUSES = (
    ("X", "approved"),
    ("Z", "skipped"),
)


def assert_the_toggle_line_explains_a4s_two_clauses(line: str) -> None:
    assert A4_TOGGLE_CLAUSES, "the toggle-line expectation is empty"
    assert "not decided" in line.lower(), (
        f"the toggle line never says where the two keys put the item: {line!r}"
    )
    for key, status in A4_TOGGLE_CLAUSES:
        assert re.search(rf"{key}[^.]{{0,60}}{status}", line), (
            f"the toggle line never pairs {key} with an {status} item: {line!r}"
        )


def test_the_shortcut_overlay_explains_the_toggle_without_adding_a_key():
    """UX spec 5 rev 8: "the decision keys toggle, and no new key is added".

    Two assertions, and the first is the normative one. The list of `<dt>`/`<dd>` rows
    must still be UX 5's seven - a new row here would BE a new key entering the list,
    whatever the handler does - and the explanation must therefore live beside the list
    rather than inside it. `assert_every_ux_5_key_is_named_with_what_it_does` already
    requires each spec key in exactly one row; this adds that there are no others.
    """
    pairs = _definition_pairs(_by_id(_page(), "shortcuts"))
    assert len(pairs) == UX_5_KEY_ROWS, (
        f"the shortcut list has {len(pairs)} rows against UX 5's {UX_5_KEY_ROWS} - a row"
        f" is a key, so this is a key entering the list: {[term for term, _ in pairs]}"
    )
    assert_the_toggle_line_explains_a4s_two_clauses(
        _normalize(_by_id(_page(), "shortcuts-undecide").text)
    )


# The overlay's two sentences about the list's rows, spelled from the page as Task 2 left it
# (review R2 F5): rows carry no Why? button - it lives in the preview (U16) - so the sentence
# names the preview, and the toggle sentence names the row the user stays on.
OVERLAY_ROW_SENTENCES = (
    ("shortcuts-undecide", "leaving you on that row instead of moving you on."),
    (
        "shortcuts-discovery",
        "the large preview (Enter) has a Why? button that shows where each part of the name"
        " came from, and a name you have edited keeps a ↺ button that puts pam's own name back.",
    ),
)
assert len(OVERLAY_ROW_SENTENCES) == 2


def test_the_overlay_says_where_why_lives_and_names_the_row():
    """Review R2 F5: Task 2's card-to-row noun swap made "every row has a Why? button"
    false. Pinned verbatim so neither sentence can drift back to a control the row lacks."""
    for element_id, sentence in OVERLAY_ROW_SENTENCES:
        text = _normalize(_by_id(_page(), element_id).text)
        assert sentence in text, f"#{element_id} no longer says: {sentence!r}; got: {text!r}"
    discovery = _normalize(_by_id(_page(), "shortcuts-discovery").text)
    assert "every row has" not in discovery, (
        "the overlay again claims a Why? button on every row"
    )


def test_the_preview_overlay_can_move_without_closing():
    """UX spec 5: "`<-/->` moves without closing". The keys are HT-M7's; the two
    controls that stand for them are here, and they are not the close button."""
    labels = _labels_in(_by_id(_page(), "overlay"))
    assert any("Previous" in label for label in labels), "no previous control"
    assert any("Next" in label for label in labels), "no next control"
    assert "Close" in labels, "no close control"


def assert_no_player(root: Node) -> None:
    found = sorted({node.tag for node in root.descendants() if node.tag in PLAYER_TAGS})
    assert not found, f"the page carries {found}, and v1.0 decodes no video"


def test_the_page_offers_no_player_anywhere():
    """UX spec 5 rev 3: v1.0 NEVER decodes video, so a video is a glyph card and a
    metadata panel. A `<video>` tag would be the page promising a decode pam has no
    code and no dependency to do."""
    assert_no_player(_page())


@pytest.mark.parametrize("tag", sorted(PLAYER_TAGS))
def test_the_no_player_check_refuses_each_tag_that_would_decode_something(tag):
    with pytest.raises(AssertionError):
        assert_no_player(_parse(f"<div><{tag}></{tag}></div>").root)


def test_the_name_editor_shows_the_extension_and_never_offers_to_edit_it():
    """A6: the editor is pre-filled with the stem, "extension shown but not editable".

    pam never changes an extension (spec 5.4 rev 6 keeps it byte-for-byte, case
    included), so the page must not draw a control that invites one to be typed.
    """
    page = _page()
    editor = _by_id(page, "editor")
    assert "hidden" in editor.attrs, "the name editor does not start closed"
    stem = _by_id(page, "editor-stem")
    assert stem.tag == "input", f"the stem is a <{stem.tag}>, not a field"
    extension = _by_id(page, "editor-ext")
    assert extension.tag not in {"input", "textarea", "select"}, (
        "the extension is a form control - A6 says it is shown, not edited"
    )
    assert "contenteditable" not in extension.attrs
    labels = _labels_in(editor)
    assert any("Save" in label for label in labels), f"no save control; found {labels}"
    assert any("Cancel" in label for label in labels), f"no cancel control; found {labels}"


def test_the_editor_carries_its_own_error_line_for_a_refusal_at_the_field():
    """UX spec 1.4: a collision, an empty name or a reserved one is answered AT THE
    FIELD. A refusal that appears somewhere else on the page is a dead end."""
    error = _by_id(_page(), "editor-error")
    assert "hidden" in error.attrs, "the editor's error line does not start hidden"
    assert error.tag == "p"


def test_the_id_cross_check_reads_every_script_the_page_loads():
    """M14 Task 4 review R2 (M3063): `REVIEW_SCRIPTS` is what this module's id cross-check
    reads, so a script left out of it is a script whose `getElementById` queries nobody checks
    against `index.html`. Dropping `pam-welcome.js` from it left the suite green. The page's
    own `<script src>` tags, walked with the real parser, are the oracle here - the ORDER is
    held to the spec by `tests/test_static_syntax.py`."""
    loaded = tuple(
        node.attrs.get("src") for node in _page().descendants() if node.tag == "script"
    )
    assert len(loaded) >= 12, loaded
    assert loaded == REVIEW_SCRIPTS


def test_there_is_exactly_one_name_editor_on_the_page():
    """ONE editor, moved to whichever card is being edited.

    Not a style choice: "keys are inert while the edit field has focus" is a claim
    about one field, and a page with an editor per card has as many fields as it has
    cards - each of which the keyboard handler would have to know about.

    M14 Task 4 Step 7: Welcome (W18, U13) carries ONE more text field, its paste box. It is
    not a name editor and it is not on the review surface - Welcome stands in for the list, and
    the list's key listener is removed while it shows (`app.js`) - so it is counted on its own.
    """

    def text_fields(nodes):
        return [
            node
            for node in nodes
            if node.tag == "input" and (node.attrs.get("type") or "text") == "text"
        ]

    page = _page()
    welcome = [node for node in page.descendants() if node.attrs.get("id") == "welcome"]
    assert len(welcome) == 1, "the page has no Welcome section"
    inside = {id(node) for node in welcome[0].descendants()}
    stems = text_fields(node for node in page.descendants() if id(node) not in inside)
    assert len(stems) == 1, f"{len(stems)} text fields on the page, expected exactly one"
    assert stems[0].attrs.get("id") == "editor-stem"
    paste = text_fields(welcome[0].descendants())
    assert [node.attrs.get("id") for node in paste] == ["welcome-paste-path"]


def test_the_smart_review_banner_is_gone_and_the_look_callout_starts_hidden():
    """Was `test_the_smart_review_banner_exists_with_both_presets_and_starts_hidden` (UX 5a
    step 3). M14 Task 2 Step 6b final: U8's W3/W4 callout replaced the banner and its preset
    twins; the callout starts hidden because its number comes from the server."""
    page = _page()
    ids = set(_ids_in(page))
    for retired in ("smart-banner", "smart-banner-text", "smart-banner-presets"):
        assert retired not in ids, f"#{retired} is back"
    assert "hidden" in _by_id(page, "look-callout").attrs, "the callout does not start hidden"


def test_the_session_resume_banner_offers_jump_to_first_undecided():
    """UX spec 5 rev 8: `N approved, M remaining [Jump to first undecided]`.

    The sentence is assembled from the server's counts at runtime; what a parse can
    hold is the control beside it and that the banner starts hidden, because it appears
    only when a decision already exists.

    Rev 8 deleted the `Welcome back — ` that used to open the line, and the control is
    what survived: the banner fires from the FIRST decision, so the greeting was shown to
    a user who never left. That the words are gone is held by
    `test_the_sentences_ux_5_rev_8_deleted_are_gone_from_the_script`; that the control is
    still beside them is held here.
    """
    banner = _by_id(_page(), "resume-banner")
    assert "hidden" in banner.attrs, "the resume banner does not start hidden"
    assert "Jump to first undecided" in _labels_in(banner)


def test_the_review_time_estimate_starts_hidden():
    """UX spec 5: the estimate appears "once >= 10 decisions have been made this
    session; hidden before that". A page that shipped it visible would show a pace
    nobody has worked at yet."""
    assert "hidden" in _by_id(_page(), "estimate").attrs


def test_the_sort_toggle_offers_exactly_spec_8_2s_two_orders():
    """UX spec 5a: lowest trust first in every view, with a toggle to date order.

    The two values are spelled from spec 8.2's `?sort=` line, not read out of the
    page and not read out of `repo.SORTS` - an oracle built from the definition it
    checks is a survivor waiting to happen, and this project has measured five.
    """
    options = [node for node in _by_id(_page(), "sort").descendants() if node.tag == "option"]
    values = {node.attrs.get("value") for node in options}
    assert values == SPEC_8_2_SORTS, f"the sort control offers {sorted(values)}"
    for option in options:
        assert _normalize(option.text), "a sort option has no words, only a value"


def test_accept_all_carries_an_empty_count_slot_and_the_preset_slots_are_gone():
    """Was `test_each_bulk_preset_carries_an_empty_slot_for_the_count_ux_5_draws`. M14 Task 2
    Step 6b final (U2): only `Accept all` is left, and its slot still ships empty, or the page
    would state a count before it has asked for one."""
    page = _page()
    slot = _by_id(page, "accept-all-count")
    assert _normalize(slot.text) == "", "#accept-all-count ships with a count already in it"
    button = _by_id(page, "accept-all")
    assert "accept-all-count" in {node.attrs.get("id") for node in button.descendants()}
    ids = set(_ids_in(page))
    for retired in (
        "approve-high-count",
        "approve-high-medium-count",
        "banner-approve-high-count",
        "banner-approve-high-medium-count",
        "banner-accept-all-count",
    ):
        assert retired not in ids, f"#{retired} is back"


def test_the_normative_sentences_task_12_builds_at_runtime_are_present_verbatim():
    """A COPY CHECK, AND ONLY THAT - read the module docstring before trusting it.

    These four sentences are UX 5/5a normative and `app.js` assembles each one from a
    count, so no element in the markup carries them and no parse can see them rendered.
    What this holds is that the WORDING has not drifted away from the spec. It says
    nothing about whether the sentence is ever shown, or shown at the right moment;
    that is HT-M7's, and the docstring lists it there.
    """
    assert_ux_5_runtime_copy_is_present(_strip_js_comments(_script()))


def assert_ux_5_runtime_copy_is_present(script: str) -> None:
    for fragment in UX_5_RUNTIME_COPY:
        assert fragment in script, f"UX 5/5a's {fragment!r} is nowhere in app.js"


def test_the_runtime_copy_check_refuses_a_script_that_carries_none_of_it():
    """Closes M735, and this is the load-bearing one of the five emptied expectations:
    no element in the markup carries these sentences, so this list is the ONLY hold on
    UX 5/5a's runtime wording."""
    with pytest.raises(AssertionError):
        assert_ux_5_runtime_copy_is_present('var x = "a script that says none of it";')


def assert_ux_5_rev_8_deleted_copy_is_gone(script: str) -> None:
    for fragment in UX_5_DELETED_COPY:
        assert fragment not in script, (
            f"UX 5/5a rev 8 deleted {fragment!r} and app.js still says it"
        )


def test_the_sentences_ux_5_rev_8_deleted_are_gone_from_the_script():
    """HT-M7's P1 and P6, held as DELETIONS rather than as replacements.

    The check above would pass with both wordings present at once, which is the obvious
    wrong fix for both findings: add the new sentence, leave the old one standing. Two
    sentences the spec calls false is not an improvement on one.

    Read from the COMMENT-STRIPPED script on purpose. Both functions' comments quote the
    copy they lost, to say why it went - and a comment is not a sentence the user reads.
    That the stripper is a string-aware scan rather than a regex, and is itself tested, is
    what makes this safe to rely on; the module docstring's point 0 is the same reliance.
    """
    assert_ux_5_rev_8_deleted_copy_is_gone(_strip_js_comments(_script()))


def test_the_deleted_copy_check_refuses_a_script_that_still_carries_it():
    """Written out by hand rather than parametrized over UX_5_DELETED_COPY: pytest SKIPS
    an empty parametrize, so a control assembled from the list it protects vanishes
    together with it (M743). Each string is an independent second spelling of the
    amendment's own words."""
    with pytest.raises(AssertionError):
        assert_ux_5_rev_8_deleted_copy_is_gone('t = "903 of 923 names are Good or better"')
    with pytest.raises(AssertionError):
        assert_ux_5_rev_8_deleted_copy_is_gone('t = "Welcome back — 812 approved"')


def assert_the_counts_line_names_all_four_counts(script: str) -> None:
    for fragment in UX_5_COUNTS_LINE:
        assert fragment in script, f"U8 W2's progress line has no {fragment!r}"


def test_the_progress_line_names_skipped_so_it_reconciles():
    """Was `test_the_counts_line_names_skipped_so_it_reconciles` (UX 5 rev 8 / B7, HT-M7's
    P12). M14 Task 2 Step 6b final: the counts line is gone with the card page, and W2's
    progress line carries B7's point - approved, skipped and still-to-decide add up to the
    total. A COPY CHECK only; `tests/js/list_test.js` renders the line under node."""
    assert "counts" not in _ids_in(_page()), "the card page's #counts line is back"
    assert_the_counts_line_names_all_four_counts(_strip_js_comments(_script()))


def test_the_progress_line_check_refuses_a_script_that_omits_skipped():
    """Was `test_the_counts_line_check_refuses_a_script_that_omits_skipped`: rev 2's defect
    planted in W2's shape - the line without its skipped count."""
    with pytest.raises(AssertionError):
        assert_the_counts_line_names_all_four_counts(
            'p.textContent = a + " of " + t + " names approved"; r.textContent = n'
            ' + " still to decide";'
        )


# --- UX spec 7's visual system -------------------------------------------------------


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _balanced_block(css: str, brace_at: int) -> tuple[str, int]:
    """The body of the `{...}` starting at `brace_at`, and the index of its `}`."""
    assert css[brace_at] == "{"
    depth = 0
    for index in range(brace_at, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[brace_at + 1 : index], index
    raise AssertionError(f"unbalanced braces from offset {brace_at}")


def _declarations(block: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r"(--[A-Za-z0-9-]+)\s*:\s*([^;{}]+);", block)
    }


def _token_blocks() -> tuple[dict[str, str], dict[str, str]]:
    """The light `:root` block and the dark one, as declaration maps."""
    css = _strip_css_comments(_stylesheet())
    dark_rule = re.search(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{", css)
    assert dark_rule, "no `prefers-color-scheme: dark` block - UX 7 requires both from day one"
    dark_body, dark_end = _balanced_block(css, dark_rule.end() - 1)
    light_source = css[: dark_rule.start()] + css[dark_end + 1 :]

    light_root = re.search(r":root\s*\{", light_source)
    assert light_root, "no unconditional `:root` token block"
    light_body, _ = _balanced_block(light_source, light_root.end() - 1)

    dark_root = re.search(r":root\s*\{", dark_body)
    assert dark_root, "the dark media block declares no `:root` tokens"
    dark_tokens, _ = _balanced_block(dark_body, dark_root.end() - 1)
    return _declarations(light_body), _declarations(dark_tokens)


def assert_every_ux_7_token_is_defined(declarations: dict[str, str]) -> None:
    missing = [token for token in UX_7_TOKENS if token not in declarations]
    assert not missing, f"UX 7 tokens never defined: {missing}"


def test_the_stylesheet_defines_every_ux_7_token_name():
    light, _ = _token_blocks()
    assert_every_ux_7_token_is_defined(light)


def test_the_token_check_refuses_a_block_that_declares_none_of_them():
    """Closes M733: `UX_7_TOKENS = ()` left `missing` empty for any stylesheet at all."""
    with pytest.raises(AssertionError):
        assert_every_ux_7_token_is_defined({"--brand-blue": "#2f6feb"})


def test_a_light_and_a_dark_token_block_both_exist_and_the_dark_one_is_really_dark():
    """UX 7: "both defined from day one (two token blocks, nothing else themed)".

    A dark block that redeclares the light values is a dark block on paper only, so the
    surface palette is required to actually differ.
    """
    light, dark = _token_blocks()
    for token in ("--bg", "--surface", "--text", "--text-muted", "--accent"):
        assert token in dark, f"the dark block never redefines {token}"
        assert dark[token] != light[token], f"{token} is identical in both blocks"


def test_the_accent_carries_ux_7s_two_normative_literals():
    light, dark = _token_blocks()
    assert light["--accent"].lower() == UX_7_ACCENT_LIGHT
    assert dark["--accent"].lower() == UX_7_ACCENT_DARK


def assert_every_band_colour_is_ux_7s_literal(css: str) -> None:
    for token, expected in UX_7_BAND_COLOURS.items():
        found = {
            value.strip().lower() for value in re.findall(rf"{token}\s*:\s*([^;{{}}]+);", css)
        }
        assert found, f"{token} is never defined"
        assert found == set(expected), (
            f"{token} is defined as {sorted(found)}, UX 7 says {sorted(expected)}"
        )


def test_every_definition_of_a_band_colour_is_ux_7s_literal():
    """`--ok` / `--warn` / `--danger` carry one light and one dark literal in UX 7 rev 16,
    so EVERY definition of each - in either block - must be one of the two, and both must
    be defined."""
    assert_every_band_colour_is_ux_7s_literal(_strip_css_comments(_stylesheet()))


@pytest.mark.parametrize(
    "css",
    [
        ":root { --text: #000; }",
        # One light literal drifted.
        ":root { --ok: #17784a; --warn: #8a5a00; --danger: #b00020; }"
        "@media (prefers-color-scheme: dark) { :root { --ok: #6fd3a0; --warn: #e8bd62;"
        " --danger: #ff9c86; } }",
        # One dark literal drifted.
        ":root { --ok: #17784a; --warn: #8a5a00; --danger: #b23a1e; }"
        "@media (prefers-color-scheme: dark) { :root { --ok: #3fb950; --warn: #e8bd62;"
        " --danger: #ff9c86; } }",
        # No dark variant at all: rev 16 requires one for each.
        ":root { --ok: #17784a; --warn: #8a5a00; --danger: #b23a1e; }",
    ],
)
def test_the_band_colour_check_refuses_a_stylesheet_that_drifts(css):
    """Closes M734: `UX_7_BAND_COLOURS = {}` left this test iterating nothing."""
    with pytest.raises(AssertionError):
        assert_every_band_colour_is_ux_7s_literal(css)


def test_the_stylesheet_declares_ux_7s_spacing_scale_and_radius():
    light, _ = _token_blocks()
    values = {value.strip().lower() for value in light.values()}
    missing = [f"{step}px" for step in UX_7_SPACING if f"{step}px" not in values]
    assert not missing, f"UX 7 spacing steps never declared: {missing}"
    assert UX_7_RADIUS in values, f"UX 7's {UX_7_RADIUS} radius is never declared"
    assert UX_7_RADIUS_LARGE in values, f"UX 7's {UX_7_RADIUS_LARGE} radius is never declared"


def test_the_stylesheet_uses_a_system_font_stack_and_fetches_nothing():
    """UX 7 names a system font stack, and spec 8.2 rev 12 forbids any external fetch.

    `://` is pinned across all three files in `tests/test_webui_server.py`; this adds
    the two spellings that reach the network WITHOUT a scheme - a relative `url()` and
    an `@import`.
    """
    css = _strip_css_comments(_stylesheet())
    light, _ = _token_blocks()
    stacks = [value for value in light.values() if "system-ui" in value]
    assert stacks, "no system font stack token"
    assert "sans-serif" in stacks[0], f"the font stack has no generic fallback: {stacks[0]!r}"
    assert "url(" not in css.replace(" ", ""), "the stylesheet fetches a resource"
    assert "@import" not in css, "the stylesheet imports a resource"


def _heads_a_rule_that_declares_something(selector: str, css: str) -> bool:
    """Does `selector` HEAD a rule, and does that rule DECLARE a property?

    Two conditions, because two mutations walked through the two halves separately.

    HEADS a rule - followed by `{` or `,`, never merely present. A plain `in` test was
    written here first, and renaming `.trust-edited` to `.trust-edited-DISARMED` left every
    class unstyled with the check green, because the old name is a PREFIX of the new one
    (M837). A substring is not a selector.

    DECLARES something - its body holds at least one `property: value`. Requiring only the
    head let `.trust-edited { }` through with the whole suite green (M838), and an element
    whose rule is empty is exactly as unstyled as one with no rule at all. The question
    this check exists to ask is whether UX 5 rev 8's edited card renders STYLED elements,
    and an empty rule answers no.

    `[^{}]*` is deliberately not used to reach the brace: it would re-open M837 by matching
    straight over `-DISARMED`. The head is anchored first, and only then is the rule's
    opening brace found - a `,`-separated selector list carries no braces, so the next `{`
    after an anchored head is always this rule's own.
    """
    return any(
        re.search(r"[A-Za-z-]+\s*:\s*[^;{}]+", body) for body in _rule_bodies(selector, css)
    )


# A trailing pseudo-element or pseudo-class chain: `::after`, `::before`, `:hover`. Opt-in
# per call, because the glyph cards are drawn ENTIRELY in `::before`/`::after` and a check
# anchored to the bare selector would find no rule and pass over anything. It is safe to
# allow only because it demands the `:` marker: `-DISARMED` still does not match it, so
# M837's prefix hole stays shut.
_PSEUDO_TAIL = r"(?:::?[A-Za-z-]+)*"


def _rule_bodies(selector: str, css: str, with_pseudo: bool = False) -> list[str]:
    """Every rule body `selector` HEADS, in source order. The anchoring is the careful
    half and is documented above - `_heads_a_rule_that_declares_something` is its first
    caller and the reason it is written this way; `_declared_value` is its second."""
    tail = _PSEUDO_TAIL if with_pseudo else ""
    bodies = []
    for match in re.finditer(re.escape(selector) + tail + r"\s*[,{]", css):
        brace = css.find("{", match.start())
        if brace == -1:
            continue
        body, _ = _balanced_block(css, brace)
        bodies.append(body)
    return bodies


def _declared_value(
    selector: str, prop: str, css: str, with_pseudo: bool = False
) -> str | None:
    """The value `selector` declares for `prop`, or `None` if it declares none.

    The property is anchored on BOTH sides, because M837's lesson applies to property
    names exactly as it does to selectors. On the left, the lookbehind refuses `column`
    matching inside the different property `grid-column`. On the right, no lookahead is
    needed: `grid-column-start` is refused because the colon must follow the name across
    nothing but whitespace, and `-start` is not whitespace. Both spellings are real CSS
    and either one silently answering for the other would make this check a coincidence.
    """
    pattern = re.compile(r"(?<![-\w])" + re.escape(prop) + r"\s*:\s*([^;{}]+)")
    for body in _rule_bodies(selector, css, with_pseudo):
        match = pattern.search(body)
        if match:
            return " ".join(match.group(1).split())
    return None


def assert_every_edited_card_selector_is_styled(css: str) -> None:
    missing = [
        selector
        for selector in EDITED_CARD_SELECTORS
        if not _heads_a_rule_that_declares_something(selector, css)
    ]
    assert not missing, f"UX 5 rev 8's edited card renders unstyled elements: {missing}"


def test_the_stylesheet_styles_every_class_an_edited_card_renders():
    """UX spec 5 rev 8. An edited card replaces four elements with one word, and the
    Why? panel gains a heading and a restore line - each is a class `app.js` creates and
    only `style.css` can be asked about here.

    The attribute selector is in the list on purpose: `trustDisplay` sets
    `data-edited="true"` on the wrapper, and an attribute no rule reads is an attribute
    that has stopped doing anything.
    """
    assert_every_edited_card_selector_is_styled(_strip_css_comments(_stylesheet()))


def test_the_edited_card_style_check_refuses_a_stylesheet_that_styles_none_of_it():
    """The module's convention: every spec-derived list gets a negative control that
    plants the violation, because a list that loops over nothing asserts nothing."""
    with pytest.raises(AssertionError):
        assert_every_edited_card_selector_is_styled(".trust { display: flex; }")


def test_the_edited_card_style_check_refuses_a_rule_that_declares_nothing():
    """M838's negative control, and it is the half this check's first version missed.

    Emptying `.trust-edited`'s body left `style.css` naming every class UX 5 rev 8 renders
    and styling none of them, with the whole suite green. Written out by hand and
    unparametrized on purpose: pytest SKIPS an empty parametrize rather than failing it,
    and a control assembled from the list it protects vanishes together with it.
    """
    present_but_empty = "\n".join(selector + " {\n}" for selector in EDITED_CARD_SELECTORS)
    with pytest.raises(AssertionError):
        assert_every_edited_card_selector_is_styled(present_but_empty)


# M14 Task 2 Step 6b final: the card page's un-decide row (`statusRow`/`undecideButton`), its
# date tree's event level (`renderNav`) and the Why? panel's span across a CARD all retired
# with it. The list's un-decide is the row's circle and the preview's `Not decided`; the list's
# tree is `#list-tree`; the panel lives in the preview only. Each trio of style checks below is
# rewritten to pin the RETIREMENT: no rule styles the retired selector, and no review script
# builds it. The selectors are the spelled lists above, unchanged.
def _heads_a_rule(selector: str, css: str) -> bool:
    return bool(re.search(r"(?:^|[\s,}])" + re.escape(selector) + r"\s*[,{]", css))


def assert_no_rule_styles(selectors, css: str) -> None:
    styled = [selector for selector in selectors if _heads_a_rule(selector, css)]
    assert not styled, f"the stylesheet still styles retired card-page selectors: {styled}"


def test_the_card_s_un_decide_classes_are_gone_from_the_stylesheet():
    """Was `test_the_stylesheet_styles_every_class_the_un_decide_control_renders`."""
    assert_no_rule_styles(UNDECIDE_SELECTORS, _strip_css_comments(_stylesheet()))
    script = _strip_js_comments(_script())
    for name in ('"status-row"', '"undecide"'):
        assert name not in script, f"a review script still builds {name}"


def test_the_un_decide_absence_check_refuses_a_stylesheet_that_still_styles_one():
    """Was `test_the_undecide_style_check_refuses_a_stylesheet_that_styles_none_of_it`."""
    with pytest.raises(AssertionError):
        assert_no_rule_styles(UNDECIDE_SELECTORS, ".undecide { font-size: 0.9em; }")


def test_the_un_decide_absence_check_refuses_even_an_empty_rule():
    """Was `test_the_undecide_style_check_refuses_a_rule_that_declares_nothing` (M838)."""
    with pytest.raises(AssertionError):
        assert_no_rule_styles(UNDECIDE_SELECTORS, ".a, .status-row {\n}")


def test_the_card_page_s_event_nav_level_is_gone_from_the_stylesheet():
    """Was `test_the_stylesheet_styles_the_event_nav_level`: the card page's `#nav-tree` is
    gone; the list's events are entries of `#list-tree`."""
    assert_no_rule_styles(EVENT_NAV_SELECTORS, _strip_css_comments(_stylesheet()))
    assert "nav-tree" not in _ids_in(_page())


def test_the_event_nav_absence_check_refuses_a_stylesheet_that_still_styles_it():
    """Was `test_the_event_nav_style_check_refuses_a_stylesheet_that_styles_none_of_it`."""
    with pytest.raises(AssertionError):
        assert_no_rule_styles(EVENT_NAV_SELECTORS, "#nav-tree .events { padding-left: 1px; }")


def test_the_event_nav_absence_check_refuses_even_an_empty_rule():
    """Was `test_the_event_nav_style_check_refuses_a_rule_that_declares_nothing` (M838)."""
    with pytest.raises(AssertionError):
        assert_no_rule_styles(EVENT_NAV_SELECTORS, "#nav-tree .events {}")


def assert_the_why_panel_is_no_longer_a_card_span(css: str) -> None:
    assert not _heads_a_rule(WHY_PANEL_CARD_SELECTOR, css), "a .card rule is back"
    selector, prop, _value = WHY_PANEL_SPAN
    for match in re.finditer(r"(?:^|[\s}])" + re.escape(selector) + r"\s*\{([^}]*)\}", css):
        assert prop not in match.group(1), f"{selector} still declares {prop} for a card"


def test_the_why_panel_spans_no_card_because_the_card_is_gone():
    """Was `test_the_why_panel_spans_every_track_of_the_card_it_is_appended_to` (HT-M7 P3).
    M14 Task 2 Step 6b final: the card the panel was appended to is gone; the panel lives in
    the preview overlay, a flex child, where `grid-column` was always inert."""
    assert_the_why_panel_is_no_longer_a_card_span(_strip_css_comments(_stylesheet()))


def test_the_card_span_check_refuses_a_stylesheet_that_keeps_the_span():
    """Was `test_the_why_panel_span_check_refuses_a_stylesheet_that_drops_the_span`."""
    with pytest.raises(AssertionError):
        assert_the_why_panel_is_no_longer_a_card_span(".why { grid-column: 1 / -1; }")


def test_the_card_span_check_refuses_a_stylesheet_that_keeps_the_card():
    """Was `test_the_why_panel_span_check_refuses_a_card_that_is_no_longer_a_grid`."""
    with pytest.raises(AssertionError):
        assert_the_why_panel_is_no_longer_a_card_span(".card { display: grid; } .why { }")


def _is_a_border_triangle(body: str) -> bool:
    """Three `transparent` sides in one `border-color` - the CSS triangle idiom, and the
    way the play button was actually drawn. Counted rather than pattern-matched, so it
    catches the triangle whichever direction it points."""
    for match in re.finditer(r"(?<![-\w])border-color\s*:\s*([^;{}]+)", body):
        if match.group(1).split().count("transparent") == 3:
            return True
    return False


def _resolve_css_escapes(css: str) -> str:
    r"""CSS numeric escapes resolved to the characters they denote (closes M890's F1).

    `content: "\25B6"` renders the SAME solid triangle as typing the character, and it
    is the spelling a stylesheet author reaches for first because it needs no non-ASCII
    byte in the file. A substring scan for the literal character cannot see it, so the
    check below passed over a stylesheet drawing exactly the play button HT-M7's P5 is
    about, with the whole suite green.

    CSS 2.1 4.1.3: a backslash, one to six hex digits, and an OPTIONAL single trailing
    whitespace consumed as part of the escape rather than emitted. Both spellings are
    resolved. An out-of-range codepoint is left as written rather than raised on: this is
    a normaliser feeding a negative check, and a stylesheet does not have to be valid for
    the check to be entitled to run.
    """

    def _one(match: "re.Match[str]") -> str:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):  # pragma: no cover - malformed escape
            return match.group(0)

    return re.sub(r"\\([0-9A-Fa-f]{1,6})[ \t\r\n]?", _one, css)


def assert_the_video_glyph_is_not_a_play_button(css: str) -> None:
    bodies = _rule_bodies(VIDEO_GLYPH_SELECTOR, css, with_pseudo=True)
    assert bodies, f"nothing draws {VIDEO_GLYPH_SELECTOR}, so a video card renders empty"
    for body in bodies:
        assert not _is_a_border_triangle(body), (
            "the video glyph is drawn as a border triangle - UX 5 rev 3 wants nothing "
            "that looks like it could be pressed to watch the file (HT-M7's P5)"
        )
    for prop in VIDEO_GLYPH_FORBIDDEN_PROPERTIES:
        declared = _declared_value(VIDEO_GLYPH_SELECTOR, prop, css, with_pseudo=True)
        assert declared is None, (
            f"the video glyph declares {prop}: {declared!r}, which can cut a play "
            "triangle out of any box"
        )
    # Scanned over the ESCAPE-RESOLVED sheet, never the raw bytes. A codepoint written
    # as `\25B6` is the fourth spelling of the play triangle, and the cheapest one to
    # reach for; see `_resolve_css_escapes` and M890.
    unescaped = _resolve_css_escapes(css)
    for character in PLAY_TRIANGLE_CHARACTERS:
        assert character not in unescaped, f"the stylesheet types a play triangle ({character})"


def test_the_video_glyph_is_drawn_as_something_other_than_a_play_button():
    """HT-M7's P5. UX spec 5 rev 3: v1.0 never decodes video, so there must be "nothing
    that looks like it could be pressed to watch the file". The owner pressed the glyph
    and got the preview overlay showing a larger one.

    THE NEGATIVE HALF ONLY, and it is holdable only because of a property pinned by
    `test_the_stylesheet_uses_a_system_font_stack_and_fetches_nothing`: no `url()` and no
    `@import`, so no image and no SVG can enter this file (M889 measured that dependency
    rather than assuming it). A solid triangle therefore has FOUR spellings here - the
    border idiom, `clip-path`, a typed character, and that character written as a CSS
    numeric escape - and all four are refused. It said THREE until M890 planted the
    fourth and the suite stayed green. The border check is scoped to the video glyph's own rules because
    the `image` glyph's mountain peak uses the same idiom legitimately; the character
    check is not scoped at all, because a play triangle typed anywhere on this page is the
    same mistake.

    WHAT NO TEST HERE CAN ASK is the actual finding: whether the replacement reads as
    FILM rather than as PLAY. That is a question about what a human wants to press, it has
    no structural proxy, and it is HT-M8's - logged as UNCATCHABLE-BY-CONSTRUCTION.
    """
    assert_the_video_glyph_is_not_a_play_button(_strip_css_comments(_stylesheet()))


def test_the_video_glyph_check_refuses_the_play_triangle_that_shipped():
    """M7's own rule, planted verbatim."""
    with pytest.raises(AssertionError):
        assert_the_video_glyph_is_not_a_play_button(
            '.glyph[data-kind="video"]::after { border-width: 7px 0 7px 11px; '
            "border-color: transparent transparent transparent var(--text-muted); }"
        )


def test_the_video_glyph_check_refuses_a_triangle_that_is_typed_rather_than_drawn():
    """The spelling the border check cannot see, and the cheapest one to reach for.
    Hand-written beside the other controls rather than parametrized over
    PLAY_TRIANGLE_CHARACTERS: pytest SKIPS an empty parametrize."""
    with pytest.raises(AssertionError):
        assert_the_video_glyph_is_not_a_play_button(
            '.glyph[data-kind="video"]::after { content: "▶"; }'
        )


def test_the_video_glyph_check_refuses_a_triangle_cut_out_with_clip_path():
    """M894's survivor: the one forbidden PROPERTY had no planted violation of its own.

    The border idiom and the typed character each had a control; `clip-path` had only
    `VIDEO_GLYPH_FORBIDDEN_PROPERTIES`'s non-empty assertion, which holds that the list
    has entries and nothing whatever about WHICH. Replacing `"clip-path"` with `"mask"`
    left all 1958 tests green over a check that now forbade a property nobody would write
    and permitted the one it exists to forbid.

    Hand-written rather than parametrized over the list it protects - pytest SKIPS an
    empty parametrize, and a control assembled from its own expectation vanishes with it.
    """
    with pytest.raises(AssertionError):
        assert_the_video_glyph_is_not_a_play_button(
            '.glyph[data-kind="video"]::after { clip-path: polygon(0 0, 100% 50%, 0 100%); }'
        )


def test_the_video_glyph_check_refuses_a_triangle_written_as_a_css_escape():
    r"""M890's survivor, planted: the FOURTH spelling, which needs no non-ASCII byte.

    `content: "\25B6"` is the same codepoint and the same rendering as typing the
    character, and it is what a stylesheet author writes by default. The character scan
    read raw bytes, so the entire suite went green over a video glyph drawing the exact
    play button UX 5 rev 3 forbids - the finding this check was written to prevent,
    reintroduced through the one spelling the check's own docstring said did not exist.

    Three forms are planted, not one: bare, whitespace-terminated, and lower-case hex.
    CSS consumes a single trailing space as part of the escape, and a normaliser that
    handles only the bare form is half a fix. Written out by hand rather than
    parametrized over a list - pytest SKIPS an empty parametrize.
    """
    for spelling in ('"\\25B6"', '"\\25b6 "', '"\\25B6"'):
        with pytest.raises(AssertionError):
            assert_the_video_glyph_is_not_a_play_button(
                '.glyph[data-kind="video"]::after { content: ' + spelling + "; }"
            )


def test_the_escape_resolver_leaves_ordinary_css_alone():
    r"""The other direction, which is what stops the resolver from being deleted later.

    A normaliser that mangled ordinary declarations would make the check above fire on
    innocent stylesheets, and a guard that cries wolf gets removed rather than fixed.
    """
    plain = ".why { grid-column: 1 / -1; }"
    assert _resolve_css_escapes(plain) == plain
    assert _resolve_css_escapes(r'a { content: "\2014"; }') == 'a { content: "—"; }'


def test_the_video_glyph_check_refuses_a_stylesheet_that_draws_no_video_glyph():
    """A rule that is gone asserts nothing about what it used to draw, and a video card
    with no glyph at all is a blank 96px box rather than a fix."""
    with pytest.raises(AssertionError):
        assert_the_video_glyph_is_not_a_play_button('.glyph[data-kind="image"] { top: 0; }')


# --- M11 Task 10 Step 6: the SECOND page, `faces.html` <-> `faces.js` <-> `style.css` -
#
# WHY THESE ARRIVE NOW AND NOT AT STEP 1, recorded because the deferral looks like an
# omission. Step 1 shipped `faces.js` as a STUB: it queried no id and created no class, so
# "every id the JS queries exists exactly once in the page" and "every class the JS
# creates has a CSS rule" would each have run over an EMPTY expectation and passed for any
# page at all - `repo.ENRICHERS`, `WRITE_FLAGS = ()` and `ANCHOR_IDS = frozenset()` again.
# The progress note recorded that they belong with Step 6. Step 6 is here, and so are
# they, with the floors that stop them going quiet again.


def _faces_markup() -> str:
    return _static("faces.html")


def _faces_script() -> str:
    return _static("faces.js")


# UX spec 6's wireframe, read as a list of surfaces the page cannot function without: the
# face strip, the photo count, the name field and its suggestion list, Save name, Skip,
# the progress line, the group position, and the done state. Spelled from the SPEC, so a
# page that stopped reaching for one is a failure here rather than a smaller extraction
# that still agrees with itself.
FACES_ANCHOR_IDS = frozenset(
    {
        "face-strip",
        "photo-count",
        "name-input",
        "person-list",
        "name-form",
        "save-name",
        "skip-group",
        "progress-line",
        "group-position",
        "done-panel",
        "empty-panel",
        "error-banner",
        # M11 Task 11: UX spec 6's two remaining controls, now live, and the three
        # surfaces that make them safe - the split proposal (Step 4), the one undo
        # (Step 5) and the Ignored faces view that makes Ignore reversible (Step 3, I3).
        "not-same-person",
        "ignore-group",
        "split-panel",
        "confirm-split",
        "undo-last",
        "show-ignored",
        "ignored-region",
        # Task 11 Step 2: Manage people - the way in, the view, the open person's panel,
        # the three fields vision R6 says start empty, and merge's two-step confirmation.
        "show-people",
        "people-region",
        "person-panel",
        "rename-input",
        "filename-input",
        "move-input",
        "merge-panel",
        "confirm-merge",
        "cancel-merge",
    }
)

# Task 10's page had a header, a face strip, a form with four controls, a progress line
# and three panels (14). Task 11 adds the two live controls, the split panel's five ids,
# the undo, and the Ignored faces view's eight. Task 11 Step 2 adds Manage people's thirty:
# its button and region, the empty sentence and list, the person panel's title, summary
# and filename line, three forms with seven controls and three fields, Hide, the faces
# and selection count, the merge select, button, panel, message and its two choices, and
# Back. An extraction well below this is not a leaner page, it is a broken regex. Counted
# from the markup's surfaces, never as `len(FACES_ANCHOR_IDS)`.
FACES_ID_FLOOR = 60

# THE ROUTES TASK 11's CONTROLS POST, SPELLED BY HAND FROM DESIGN SPEC 8.2 (A18) - each
# with the literal fragments `faces.js` must build it from. Replaced, at Task 11 Steps
# 3-5, the Task 10 guard that held `Ignore` and `Not the same person` DISABLED because
# these routes did not exist: they do now, and the claim that replaces "disabled and
# explained" is "enabled AND posting to a route the live server actually serves". The
# route keys are checked against the server's own handler tables, so a page posting into
# spec 8.2's silent empty 404 is red here rather than in a browser.
FACES_CORRECTION_ROUTES = {
    ("POST", "faces/cluster/{id}/dismiss"): ('"api/faces/cluster/"', '"/dismiss"'),
    ("POST", "faces/cluster/{id}/split"): ('"api/faces/cluster/"', '"/split"'),
    ("GET", "faces/ignored"): ('"api/faces/ignored"',),
    ("POST", "faces/faces/state"): ('"api/faces/faces/state"',),
    ("POST", "faces/faces/assign"): ('"api/faces/faces/assign"',),
    # Task 11 Step 2 (owner ruling 4 for the GET): Manage people
    ("GET", "faces/persons"): ('"api/faces/persons"',),
    ("GET", "faces/person/{id}"): ('"api/faces/person/"',),
    ("POST", "faces/person/{id}"): ('"api/faces/person/"',),
    ("POST", "faces/person/{id}/merge"): ('"api/faces/person/"', '"/merge"'),
}

# Each Task 11 control, and the route it reaches. `undo-last` reaches two (a label's undo
# is `faces/assign`, a split's is `split`); the restore button is built per face and has
# no id, so its route is pinned through `FACES_CORRECTION_ROUTES` alone.
FACES_LIVE_CONTROLS = {
    "ignore-group": (("POST", "faces/cluster/{id}/dismiss"),),
    "not-same-person": (("POST", "faces/cluster/{id}/split"),),
    "confirm-split": (("POST", "faces/cluster/{id}/split"),),
    "undo-last": (("POST", "faces/faces/assign"), ("POST", "faces/cluster/{id}/split")),
    "show-ignored": (("GET", "faces/ignored"),),
    # Task 11 Step 2. `propose-merge` and `cancel-merge` issue no request by design, so
    # they are anchors above, not live controls; the per-person open button has no id.
    "show-people": (("GET", "faces/persons"),),
    "rename-person": (("POST", "faces/person/{id}"),),
    "set-filename": (("POST", "faces/person/{id}"),),
    "clear-filename": (("POST", "faces/person/{id}"),),
    "toggle-hidden": (("POST", "faces/person/{id}"),),
    "unassign-faces": (("POST", "faces/faces/assign"),),
    "move-faces": (("POST", "faces/faces/assign"),),
    "not-a-face": (("POST", "faces/faces/state"),),
    "confirm-merge": (("POST", "faces/person/{id}/merge"),),
}

# The classes `faces.js` builds at runtime. Written from what the page DRAWS (UX spec 6's
# row of faces, UX spec 7's ~96px thumbnails; Task 11's proposed groups with a caption
# each, and the Ignored faces view's card-with-Restore) rather than scraped out of the
# script, so a class that loses its rule is red here.
FACES_RENDERED_SELECTORS = (
    ".face-card",
    ".face-crop",
    ".face-missing",
    ".split-group",
    ".split-caption",
    ".ignored-card",
    ".restore-face",
    # Task 11 Step 2: a row per named person with its counts and hidden flag, and the
    # open person's selectable faces with the below-floor word.
    ".person-row",
    ".person-open",
    ".person-counts",
    ".person-hidden",
    ".person-face",
    ".face-select",
    ".below-floor",
)

# UX spec 6's done state, verbatim. The page's copy and the script's - both carry it, and
# each is checked against THIS literal rather than against the other, because a pin
# between two carriers proves only that they drift together (M769/M776).
UX_6_DONE_SENTENCE = (
    "All clusters handled. People will appear in filenames on your next pam plan."
)


def assert_faces_extraction_is_plausible(ids: set[str]) -> None:
    """The floor under every faces cross-check below, watched failing by three controls.

    M718/M719's shape: an expectation that is asserted but never proven to fire is an
    expectation that can be emptied with the suite green.
    """
    assert ids, "the extractor read NO ids out of faces.js - the cross-check is vacuous"
    assert len(ids) >= FACES_ID_FLOOR, (
        f"only {len(ids)} ids extracted, want >= {FACES_ID_FLOOR}"
    )
    assert FACES_ANCHOR_IDS <= ids, f"faces.js never queries {sorted(FACES_ANCHOR_IDS - ids)}"


# F8 (M11 Task 11 Stage 1, M1894): "non-empty" let ANY single entry of these four lists be
# deleted with the suite green - including the only check that the destructive merge button
# ships enabled. Each floor is COUNTED BY HAND from the surfaces it names, never as
# `len(...)` of the list: anchors - UX 6's twelve, Task 11 Steps 3-5's seven, Step 2's
# nine; routes - Steps 3-5's five, Step 2's four; live controls - Steps 3-5's five, Step
# 2's nine; rendered classes - seven drawn by Tasks 10-11 Steps 3-5, seven by Step 2.
# Removing a surface is a deliberate edit of both the list and its floor.
FACES_EXPECTATION_FLOORS = {
    "FACES_ANCHOR_IDS": 28,
    "FACES_CORRECTION_ROUTES": 9,
    "FACES_LIVE_CONTROLS": 14,
    "FACES_RENDERED_SELECTORS": 14,
}


def test_no_faces_expectation_lost_an_entry_without_its_floor_moving():
    """F8's closure: an entry deleted from a hand-spelled faces list is red here."""
    lists = {
        "FACES_ANCHOR_IDS": FACES_ANCHOR_IDS,
        "FACES_CORRECTION_ROUTES": FACES_CORRECTION_ROUTES,
        "FACES_LIVE_CONTROLS": FACES_LIVE_CONTROLS,
        "FACES_RENDERED_SELECTORS": FACES_RENDERED_SELECTORS,
    }
    assert set(lists) == set(FACES_EXPECTATION_FLOORS)
    for name, expectation in lists.items():
        assert len(expectation) >= FACES_EXPECTATION_FLOORS[name], (
            f"{name} holds {len(expectation)} entries, floor {FACES_EXPECTATION_FLOORS[name]}"
        )


def test_no_faces_expectation_in_this_module_is_empty():
    """M743, applied to the expectations Step 6 adds.

    pytest SKIPS an empty parametrize rather than failing it, so a control built from one
    of these lists disappears together with the list. Asserted here, unparametrized, where
    nothing can make the assertion itself go away.
    """
    for name, expectation in (
        ("FACES_ANCHOR_IDS", FACES_ANCHOR_IDS),
        ("FACES_CORRECTION_ROUTES", FACES_CORRECTION_ROUTES),
        ("FACES_LIVE_CONTROLS", FACES_LIVE_CONTROLS),
        ("FACES_RENDERED_SELECTORS", FACES_RENDERED_SELECTORS),
        ("UX_6_DONE_SENTENCE", UX_6_DONE_SENTENCE),
    ):
        assert expectation, (
            f"{name} is empty, so every check that loops over it asserts nothing - and "
            "a control parametrized over it is SKIPPED rather than failed"
        )
    assert FACES_ID_FLOOR > 0, "the id floor is zero, so the extraction can go quiet"


def test_faces_html_is_well_formed_enough_to_walk():
    """A real parser as the oracle, for the second page as for the first."""
    tree = _parse(_faces_markup())
    assert not tree.errors, "; ".join(tree.errors)
    assert [node.tag for node in tree.root.parts if isinstance(node, Node)] == ["html"]


def test_faces_js_queries_a_plausible_number_of_ids():
    """The extraction is not vacuous. Without this, every check below is a tautology."""
    assert_faces_extraction_is_plausible(queried_ids(_faces_script()))


def test_the_faces_plausibility_check_refuses_an_extraction_that_went_quiet():
    with pytest.raises(AssertionError):
        assert_faces_extraction_is_plausible(set())


@pytest.mark.parametrize("anchor", sorted(FACES_ANCHOR_IDS))
def test_the_faces_plausibility_check_refuses_an_extraction_missing_any_one_anchor(anchor):
    """One control per surface UX spec 6 names, so losing any single one is named."""
    with pytest.raises(AssertionError):
        assert_faces_extraction_is_plausible(set(FACES_ANCHOR_IDS) - {anchor})


def test_every_id_faces_js_queries_exists_exactly_once_in_faces_html():
    """The JS is the query; the HTML parse is the oracle. The Step 1 deferral, discharged."""
    _cross_check(_faces_markup(), _faces_script(), "faces.js", "faces.html")


def test_the_faces_cross_check_sees_an_id_the_real_page_stopped_declaring():
    """Nothing on disk is touched: one genuinely queried id is renamed in the string."""
    script = _faces_script()
    victim = sorted(queried_ids(script))[0]
    broken = _faces_markup().replace(f'id="{victim}"', 'id="renamed-by-this-test"', 1)
    assert broken != _faces_markup(), f'#{victim} is not declared as id="{victim}"'
    with pytest.raises(AssertionError):
        _cross_check(broken, script, "faces.js", "faces.html")


def test_no_id_is_declared_twice_in_faces_html():
    """Duplicates make `getElementById` a coin toss, silently and only sometimes."""
    present = _ids_in(_parse(_faces_markup()).root)
    duplicated = sorted({name for name in present if present.count(name) > 1})
    assert not duplicated, f"declared more than once: {duplicated}"


def test_the_two_pages_share_exactly_the_frame_and_nothing_else():
    """One stylesheet, two pages: an id styled for one page is styled for the other.

    Not a purity rule - the page frame, the header, the dead-server banner and the
    narrow-viewport notice are SHARED on purpose (UX spec 7 is one visual system). What
    this pins is that the sharing is deliberate and enumerated, so a third page cannot
    quietly reuse an id that means something else on the first.
    """
    shared = set(_ids_in(_page())) & set(_ids_in(_parse(_faces_markup()).root))
    expected = {
        "page",
        "header",
        "main",
        "footer",
        "error-banner",
        # M14 Task 2 Step 6b final (U4): "narrow-notice" left the review page; the faces
        # page keeps it (U20), so it is no longer shared. M14 Task 6c (W16): neither is
        # "footer-command" - the review page's footer is `Rename N files` now.
        "done-panel",
    }
    assert shared == expected, sorted(shared)


def assert_every_faces_selector_is_styled(css: str) -> None:
    """Every class the face strip renders has a rule that declares something."""
    for selector in FACES_RENDERED_SELECTORS:
        assert _heads_a_rule_that_declares_something(selector, css), (
            f"{selector} is rendered by faces.js and styled by nothing"
        )


def test_the_stylesheet_styles_every_class_the_face_strip_renders():
    """The second cross-check the Step 1 note deferred: a class built at runtime is a
    class no parse of the page can see, so the pairing is script-side to CSS-side."""
    assert_every_faces_selector_is_styled(_stylesheet())


def test_the_faces_style_check_refuses_a_stylesheet_that_styles_none_of_it():
    with pytest.raises(AssertionError):
        assert_every_faces_selector_is_styled("#face-strip { display: flex; }")


def test_the_faces_style_check_refuses_a_rule_that_declares_nothing():
    """M838: an element whose rule is empty is exactly as unstyled as one with no rule."""
    empty = " ".join(f"{selector} {{ }}" for selector in FACES_RENDERED_SELECTORS)
    with pytest.raises(AssertionError):
        assert_every_faces_selector_is_styled(empty)


@pytest.mark.parametrize("selector", FACES_RENDERED_SELECTORS)
def test_every_styled_face_class_is_one_faces_js_actually_builds(selector):
    """Both directions. A rule for a class nothing renders is dead CSS that reads as
    coverage, and this is the half that notices the script dropping the class."""
    assert selector.lstrip(".") in _faces_script(), f"{selector} is styled but never built"


def _served_faces_routes() -> set[tuple[str, str]]:
    """Every `(method, route)` the review server's handler tables serve.

    The same four tables `ReviewServer.__init__` merges, imported rather than re-typed, so
    a route this module expects that the server dropped is red here. The EXPECTATION side
    is `FACES_CORRECTION_ROUTES`, spelled by hand; this is only the oracle it is checked
    against.
    """
    from pam.webui.api import HANDLERS
    from pam.webui.faces_api import FACES_HANDLERS
    from pam.webui.faces_manage_api import FACES_MANAGE_HANDLERS
    from pam.webui.faces_split_api import FACES_SPLIT_HANDLERS
    from pam.webui.write_api import WRITE_HANDLERS

    return {
        *HANDLERS,
        *WRITE_HANDLERS,
        *FACES_HANDLERS,
        *FACES_MANAGE_HANDLERS,
        *FACES_SPLIT_HANDLERS,
    }


def assert_route_is_served_and_built(route: tuple[str, str], script: str) -> None:
    """The route is in the server's tables, and the script spells every fragment of it."""
    assert route in _served_faces_routes(), f"the server does not serve {route}"
    code = _strip_js_comments(script)
    for fragment in FACES_CORRECTION_ROUTES[route]:
        assert fragment in code, f"faces.js does not build {route} ({fragment} missing)"


def assert_control_is_live(markup: str, script: str, control: str) -> None:
    """Present (UX spec 6 or Task 11 draws it), NOT disabled, and reached by `faces.js`."""
    button = _by_id(_parse(markup).root, control)
    assert button.tag == "button", f"#{control} is a {button.tag}, not a button"
    assert "disabled" not in button.attrs, f"#{control} ships disabled"
    assert control in queried_ids(script), f"faces.js never reaches for #{control}"


@pytest.mark.parametrize("control", sorted(FACES_LIVE_CONTROLS))
def test_a_task_11_control_is_live_and_posts_to_a_route_the_server_serves(control):
    """THE REPLACEMENT FOR "DISABLED AND EXPLAINED" (Task 10's guard, retired at Task 11).

    Task 10 held `Ignore` and `Not the same person` disabled because their routes did not
    exist and an enabled button would post into spec 8.2's silent empty 404. The routes
    exist now, so the claim becomes its positive: the button is enabled, the script
    reaches for it, and every route it posts is one the running server actually serves.
    """
    assert_control_is_live(_faces_markup(), _faces_script(), control)
    for route in FACES_LIVE_CONTROLS[control]:
        assert_route_is_served_and_built(route, _faces_script())


@pytest.mark.parametrize("route", sorted(FACES_CORRECTION_ROUTES))
def test_every_correction_route_faces_js_posts_is_one_the_server_serves(route):
    """Including the per-face Restore, which has no id for the control check to reach."""
    assert_route_is_served_and_built(route, _faces_script())


def test_the_live_control_check_refuses_a_button_that_ships_disabled():
    """The live check, watched failing on Task 10's own shape: nothing on disk is touched."""
    markup = _faces_markup()
    broken = markup.replace('id="ignore-group" type="button"', 'id="ignore-group" disabled', 1)
    assert broken != markup, "#ignore-group is not spelled as this test expects"
    with pytest.raises(AssertionError):
        assert_control_is_live(broken, _faces_script(), "ignore-group")


def test_the_route_check_refuses_a_route_the_server_does_not_serve():
    """The oracle, watched failing: a route one letter off is not served."""
    with pytest.raises(AssertionError):
        assert_route_is_served_and_built(("POST", "faces/cluster/{id}/dismis"), _faces_script())


def test_the_labeling_page_carries_ux_6s_done_sentence_verbatim():
    """Spelled from UX spec 6, not copied off the page."""
    panel = _by_id(_parse(_faces_markup()).root, "done-panel")
    assert UX_6_DONE_SENTENCE in _normalize(panel.text)


def test_the_script_and_the_page_agree_about_ux_6s_done_sentence():
    """Two carriers, one sentence, each checked against the SPEC side."""
    assert UX_6_DONE_SENTENCE in _faces_script()


def test_skip_and_ignore_are_described_as_different_things():
    """Step 6's own requirement: Skip is session state and Ignore is the persisted one,
    and the page has to make the difference legible rather than offering two buttons that
    look alike. The words are checked, because a user reads words and not a data model."""
    note = _normalize(_by_id(_parse(_faces_markup()).root, "skip-note").text)
    assert "Skip" in note and "Ignore" in note, note
    assert "this visit" in note, f"the note does not say Skip is only for this visit: {note}"
    assert "remember" in note, f"the note does not say Ignore is the remembered one: {note}"


def test_the_page_offers_a_way_back_from_having_skipped_everything():
    """Skip being session state is exactly why this control exists: without it, skipping
    the last group leaves a page with nothing on it and no way back, and drawing the done
    panel there would summarize a job left undone as a success (vision principle 9)."""
    page = _parse(_faces_markup()).root
    panel = _by_id(page, "skipped-panel")
    assert "hidden" in panel.attrs, "the skipped panel does not start hidden"
    assert "skipped" in _normalize(panel.text).lower()
    assert _by_id(page, "unskip").tag == "button"


def test_the_labeling_page_names_no_external_target_anywhere():
    """Spec 8.2 rev 12: a page that fetches a font announces the archive to a third party,
    and pam's network canary watches pam's own sockets, not the browser's."""
    assert "://" not in _faces_markup(), "the labeling page names an external target"
    assert "://" not in _faces_script(), "faces.js names an external target"


def test_no_route_the_script_builds_is_root_relative():
    """Every route lives under the per-session token prefix the browser supplies from the
    address bar. A root-relative path parses, looks right, drops that prefix and 404s."""
    script = _faces_script()
    assert '"api/faces/' in script, "faces.js no longer builds any faces route"
    assert '"/api/' not in script, "a root-relative route drops the session token prefix"


# M1670's lesson (M10's C4), applied to the controls THIS COMMIT adds. The module's
# M7-era checks predate the practice and are deliberately not retro-registered here -
# claiming to inventory them would be a claim nobody has made good on. What is registered
# is the faces section, spelled by hand and never derived from `globals()`, which would
# agree with any deletion.
FACES_LOAD_BEARING_CONTROLS = {
    "test_no_faces_expectation_lost_an_entry_without_its_floor_moving": (
        "F8: without it one entry of any faces expectation list - the merge button's "
        "live-control check among them - can be deleted with the suite green"
    ),
    "test_no_faces_expectation_in_this_module_is_empty": (
        "M743: a control parametrized over an emptied list is SKIPPED, not failed"
    ),
    "test_faces_js_queries_a_plausible_number_of_ids": (
        "the floor under every faces cross-check; without it they are tautologies"
    ),
    "test_the_faces_plausibility_check_refuses_an_extraction_that_went_quiet": (
        "the floor, watched failing (M718/M719)"
    ),
    "test_the_faces_plausibility_check_refuses_an_extraction_missing_any_one_anchor": (
        "one control per UX 6 surface, so losing any single one is named"
    ),
    "test_every_id_faces_js_queries_exists_exactly_once_in_faces_html": (
        "the Step 1 deferral's discharge: the JS is the query, the parse is the oracle"
    ),
    "test_the_faces_cross_check_sees_an_id_the_real_page_stopped_declaring": (
        "the cross-check, watched failing on the real shipped page"
    ),
    "test_the_stylesheet_styles_every_class_the_face_strip_renders": (
        "a class built at runtime is a class no parse of the page can see"
    ),
    "test_the_faces_style_check_refuses_a_rule_that_declares_nothing": (
        "M838: an empty rule is exactly as unstyled as no rule"
    ),
    "test_every_styled_face_class_is_one_faces_js_actually_builds": (
        "the other direction; dead CSS reads as coverage"
    ),
    "test_a_task_11_control_is_live_and_posts_to_a_route_the_server_serves": (
        "Ignore/split/undo/Ignored faces must post only to routes the server serves"
        " (replaced Task 10's disabled-and-explained guard once the routes existed)"
    ),
    "test_every_correction_route_faces_js_posts_is_one_the_server_serves": (
        "Restore has no id, so its route is pinned here or nowhere"
    ),
    "test_the_live_control_check_refuses_a_button_that_ships_disabled": (
        "the live check, watched failing on Task 10's disabled shape"
    ),
    "test_the_route_check_refuses_a_route_the_server_does_not_serve": (
        "the handler-table oracle, watched failing (M718/M719)"
    ),
    "test_skip_and_ignore_are_described_as_different_things": (
        "Step 6's own requirement, checked in the words a user reads"
    ),
    "test_the_page_offers_a_way_back_from_having_skipped_everything": (
        "vision principle 9: a skipped-out page must not draw the done panel"
    ),
    "test_the_node_driver_module_exists_and_registers_its_own_controls": (
        "the cross-module anchor: this module cannot execute what it reads"
    ),
}

assert FACES_LOAD_BEARING_CONTROLS, "the faces control list is empty; it protects nothing"


def test_no_faces_control_in_this_file_can_be_deleted_without_saying_so():
    """Removing one of these is a deliberate edit to `FACES_LOAD_BEARING_CONTROLS`, with
    the reason it was kept written beside it - not a quiet deletion during a refactor."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(FACES_LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing faces control has been deleted: {missing}."
        f" Each is why another assertion here means anything"
        f" ({[FACES_LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )


def test_the_node_driver_module_exists_and_registers_its_own_controls():
    """A CROSS-MODULE ANCHOR, and this project has needed one ten tasks running.

    The behaviour half of Step 8 lives in `tests/test_faces_page.py` and
    `tests/js/faces_state_test.js`: this module can read the page and the script, and
    cannot execute either. Deleting that pair would leave every check here passing over a
    page whose transitions nobody runs, and no control in it can notice its own absence.
    """
    import test_faces_page

    assert test_faces_page.LOAD_BEARING_CONTROLS, "the Node runner registers no controls"
    assert test_faces_page.NODE_DRIVER.is_file(), "the Node driver is gone"
    assert test_faces_page.REQUIRED_TRANSITIONS, "the Node driver is required to run nothing"


# M14 Task 2 review R2 (M2872, M2873): the card page's retired anchors, ALL of them, spelled
# here from `index.html` at `a9e4206` (the last card page) minus what the list still carries -
# never read from today's page. Several had a negative pin of their own (`#counts`, `#selection`,
# `#smart-banner`, `#attention-filter`, `#narrow-notice`, the preset slots); `#grid`,
# `#load-more` and the two header selects had none, so putting the grid or a header select back
# was green.
RETIRED_CARD_PAGE_IDS = (
    "approve-high",
    "approve-high-count",
    "approve-high-medium",
    "approve-high-medium-count",
    "attention-filter",
    "banner-accept-all",
    "banner-accept-all-count",
    "banner-approve-high",
    "banner-approve-high-count",
    "banner-approve-high-medium",
    "banner-approve-high-medium-count",
    "bulk-note",
    "counts",
    "grid",
    "load-more",
    "narrow-notice",
    "nav-tree",
    "selection",
    "selection-approve",
    "selection-count",
    "selection-reset",
    "selection-skip",
    "smart-banner",
    "smart-banner-presets",
    "smart-banner-text",
    "status-filter",
    "tier-filter",
)
assert len(RETIRED_CARD_PAGE_IDS) >= 27, "the retired-id list is empty or cut"


def assert_no_retired_card_page_id(page: Node) -> None:
    back = sorted(set(RETIRED_CARD_PAGE_IDS) & set(_ids_in(page)))
    assert back == [], f"the retired card page's anchors are back: {back}"


def test_no_anchor_of_the_retired_card_page_is_back():
    assert_no_retired_card_page_id(_page())


@pytest.mark.parametrize("retired", ["grid", "status-filter", "tier-filter", "load-more"])
def test_the_retired_id_check_refuses_each_anchor_that_had_no_pin_of_its_own(retired):
    with pytest.raises(AssertionError):
        assert_no_retired_card_page_id(
            _parse(f'<div id="list"></div><div id="{retired}"></div>').root
        )

"""`tests/js/app_logic_test.js` executed, and the floor that keeps it honest (M12 Task 3).

WHAT THIS CLOSES. `m11.md` condition C5: `pam/webui/static/app.js` is ~2300 lines of which,
until this task, exactly one function (`sourceWords`) was ever EXECUTED by anything in this
project. C5's ruling is **capability, not size** - nothing is split. Every function that
implements a spec rule (the Why? panel's lines, the trust display, UX 5a's banner and the
undecided count it is reached by, the preset filter, the count query, decided/undecided) is
now a pure function exported through the one CommonJS seam `tests/js/app_provenance_test.js`
already reaches through, and the node driver runs it.

THE SHAPE IS `tests/test_app_provenance.py`'s, including its two halves. `node` is an
external tool on the exiftool precedent and a machine may not have it, so the node half skips
where it is absent - and a control that disarms silently on the machine without the tool is
the failure mode (CLAUDE.md, M783/M784). So the stdlib half NEVER skips: it reads the driver
and `app.js` with Python and fails on either machine. CI asserts `node` is present
(`m7.md` C3), so the node half is not optional there.

THE LISTS BELOW ARE HAND-WRITTEN from M12 plan Task 3's table and the spec sentences each
check is about - never read back out of the driver or out of `app.js`.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "app_logic_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
# M14 Task 1: the rules this file checks moved, unchanged, out of `app.js` into the two review
# scripts that now hold them - with the DOM functions that render them and the two export
# seams the driver reaches through. Read in `index.html`'s order. (`pam-core.js` and
# `pam-keys.js` hold no rule; core's seam is the shared scope the driver reproduces, not a
# tested function.)
# M14 Task 2 Step 6b final: the card page, `pam-review.js`, is retired - its last pure
# functions had moved (`MOVED_EXPORTS`) and the rest retired with it (`RETIRED_EXPORTS`).
SUBJECT_SCRIPTS = (STATIC_DIR / "pam-trust.js",)
# M14 Task 2 Step 6a: `runAcceptAll` moved to `pam-list.js` (it refreshes the list), and still
# calls this file's two pure functions. Its WIRING rows are read there; the EXPORT seam stays
# the two scripts above.
# Step 6b final: + `pam-row.js`, where the look-alike line's caller moved with the card.
WIRED_SCRIPTS = (*SUBJECT_SCRIPTS, STATIC_DIR / "pam-list.js", STATIC_DIR / "pam-row.js")
# M14 Task 2 Step 6b: five pure functions MOVED, unchanged, out of `pam-review.js` before the
# card page retired - the decision's words and the look-alike line into `pam-core.js` (the row
# and the preview call them), Accept all's body and sentence into `pam-list.js` (its one
# caller). Each is still in `REQUIRED_EXPORTS` and still executed by the driver; the seam it is
# exported through is now the file it lives in. Spelled by hand, one file per name.
MOVED_EXPORTS = (
    ("isDecided", STATIC_DIR / "pam-core.js"),
    ("statusNote", STATIC_DIR / "pam-core.js"),
    ("lookAlikeLine", STATIC_DIR / "pam-core.js"),
    ("acceptAllScope", STATIC_DIR / "pam-list.js"),
    ("acceptAllReport", STATIC_DIR / "pam-list.js"),
)

# THE CHECKS THE DRIVER MUST ACTUALLY EXECUTE. The node half asserts the set of `ok` names
# EQUALS this, so a check deleted from the driver and a check deleted from here both fail.
REQUIRED_CHECKS = (
    # M14 Task 2 Step 6b final: the card page's five retired rules, ONE NEGATIVE PIN EACH
    # (they replace the 17 checks that executed them: UX 5a's banner (5) and its undecided
    # count (2) - U8's W3/W4 callout replaced them, `list_test.js` runs it - and the presets'
    # scope (4), refusal (5) and count query (1) - U2 retired the preset buttons)
    "the Smart Review banner's sentence is gone from the review page",
    "the banner's undecided-count fallback is gone from the review page",
    "the presets' scope is gone from the review page",
    "the presets' refusal is gone from the review page",
    "the card page's count query is gone from the review page",
    # design 6.4 rev 14 / UX 5 - read against style.css, never against TIER_BANDS
    "each tier's bar takes the colour UX 5 gives it and the badge beside it agrees",
    "an unknown tier's bar is amber, never transparent and never red",
    "the label is the server's trust_label and the number is the score",
    "the bar's width is clamped to the bar while the number is not",
    "the badge carries UX 5's symbol and word for every tier",
    "an edited name carries no label, no score, no bar and no badge",
    # UX 5's Why? panel and rev 8's edited heading
    "an edited item's Why? panel leads with UX 5 rev 8's proposed heading",
    "a name pam proposed has no proposed heading and no restore line",
    "each line names the value and its source, in the planner's order",
    "with no reason the total says nothing lowered it",
    "negative reasons appear in the same list and the total stops claiming nothing",
    "a truncated token's source says it was shortened to fit",
    "a person's Why? line never names the model of a fact that shares its id",
    "the confidence note is the server's sentence verbatim, and absent when it is",
    "a malformed token list renders no lines rather than failing",
    # UX 5 rev 8
    "approved and skipped are decisions and nothing else is",
    "the status note names the decision, and anything else reads as not decided",
    # M13 Task 2 (F1 R2): Accept all carries none of the view and reports R2's sentence
    "Accept all's body is exactly the action and the flag, whatever the view",
    "Accept all's report names the low count only when there is one",
    # M13 Task 10 (H3): the card's look-alike line, singular, plural, and absent
    "the look-alike line names one other photo in the singular",
    "the look-alike line names several in the plural and counts the rest",
    "a card with no look-alikes has no line",
)

# EVERY name `app.js`'s seam exports - and, since M13 Task 2 (C5), NOTHING ELSE: the check
# below is EQUALITY, not membership. A dropped export makes the node half fail where node
# exists and VANISH where it does not, so it is pinned here, in the half that never skips;
# an export no list names is a function the suite believes is tested and is not. One literal
# for the whole seam: `tests/test_app_provenance.py`'s two names (`SOURCE_WORDS`,
# `sourceWords`), plan M12 Task 3's table, and M13 Task 2's three.
REQUIRED_EXPORTS = (
    # tests/test_app_provenance.py
    "SOURCE_WORDS",
    "sourceWords",
    # M12 Task 3
    "whyLines",
    "trustView",
    "isDecided",
    "statusNote",
    # M13 Task 2
    "acceptAllScope",
    "acceptAllReport",
    # M13 Task 10
    "lookAlikeLine",
)

# M14 Task 2 Step 6b final: the card page's pure functions that RETIRED with it (U2 retired
# the preset buttons, U8's W3/W4 callout replaced the Smart Review banner). Spelled by hand;
# each must be in NO review script's seam and defined in NONE of the scripts the page loads.
RETIRED_EXPORTS = (
    "smartBannerText",
    "attentionTodoOf",
    "presetFilter",
    "presetRefusal",
    "scopeQuery",
)
# Every script `index.html` loads, spelled (the order is `test_static_syntax.py`'s to hold).
REVIEW_PAGE_SCRIPTS = tuple(
    STATIC_DIR / name
    for name in (
        "pam-core.js",
        "pam-trust.js",
        "pam-keys.js",
        "pam-row.js",
        "pam-selection.js",
        "pam-list.js",
        "pam-bar.js",
        "pam-detail.js",
        "pam-chooser.js",
        "pam-reading.js",
        "pam-welcome.js",
        "pam-confirm.js",
        "pam-done.js",
        "pam-stopped.js",
        "pam-history.js",
        "app.js",
    )
)

# The WIRING: each DOM function that used to hold a rule must now CALL the pure function
# that holds it. An export is not a wiring - a DOM function that re-grew its own copy of the
# rule would leave every node check green over a page that no longer runs the tested code.
#
# AND A CALL IS NOT A WIRING EITHER (M12 Task 3 review, F1 / M2015): `presetFilter(tier, {})`
# in `runPreset` still "calls presetFilter", passes every node check (which executes the
# function with whatever it is handed) and makes `Approve High` in a filtered view approve
# every undecided high item in the PLAN. So each row spells the EXACT CALLS, arguments
# included, from plan Task 3's table ("the caller passes `state.filters`") and the state
# each rule reads - and the body must hold that many calls to the pure function and no more,
# so a second, differently-argued call beside the right one is caught too.
WIRING = (
    ("trustDisplay", "trustView", ("trustView(item)",)),
    ("whyPanel", "whyLines", ("whyLines(item)",)),
    # M14 Task 2 Step 6b final: the rows for `renderSmartBanner`, `attentionTodo`,
    # `refreshPresetCounts` and `runPreset` (x2) RETIRED with the card page - their pure
    # functions are `RETIRED_EXPORTS`, pinned gone below.
    # M13 Task 2 (F1 R2): the POST body and the sentence are the pure functions'
    ("runAcceptAll", "acceptAllScope", ("acceptAllScope(LIST.view)",)),
    ("runAcceptAll", "acceptAllReport", ("acceptAllReport(result)",)),
    # M13 Task 10 (H3): the ROW (was the card, until Step 6b) takes the pure function's line
    # for THIS item - `rowModel` builds what `buildRow` draws
    ("rowModel", "lookAlikeLine", ("lookAlikeLine(item)",)),
)


def _sources() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in SUBJECT_SCRIPTS]


def _source() -> str:
    return "\n".join(_sources())


def _export_pairs(source: str) -> list[tuple[str, str]]:
    # EVERY multi-line `module.exports` block, in order: since M14 Task 1 `_source()` is two
    # scripts and each carries one.
    blocks = re.findall(r"module\.exports = \{(.*?)\n  \};", source, re.DOTALL)
    assert blocks, "app.js has no multi-line module.exports block"
    return [
        pair
        for block in blocks
        for pair in re.findall(r"^\s*(\w+): (\w+),", block, re.MULTILINE)
    ]


def _export_names(source: str) -> set[str]:
    # `name: name,` only - an export bound to a DIFFERENT function is not the one named.
    return {key for key, value in _export_pairs(source) if key == value}


def assert_exports_are_exactly(found: set[str], expected: tuple[str, ...]) -> None:
    """EQUALITY (M13 Task 2, C5): nothing missing AND nothing extra."""
    missing = sorted(set(expected) - found)
    extra = sorted(found - set(expected))
    assert missing == [] and extra == [], f"missing {missing}, never registered {extra}"


def _function_body(source: str, name: str) -> str:
    # Searched ONE SCRIPT AT A TIME (M14 Task 1): over the joined text, the last function of
    # one file would run on into the next file's header and element map. Every caller passes
    # `_source()`, the scripts joined, so the search is over the scripts it was joined from.
    assert source == _source(), "_function_body reads the review scripts, not a planted text"
    for script in (path.read_text(encoding="utf-8") for path in WIRED_SCRIPTS):
        match = re.search(
            rf"^function {name}\(.*?(?=^function |\Z)", script, re.DOTALL | re.MULTILINE
        )
        if match:
            return match.group(0)
    raise AssertionError(f"app.js defines no top-level function {name}")


# --- the stdlib half: never skips ----------------------------------------------------


def test_the_review_page_scripts_are_every_script_index_html_loads():
    """M14 Task 5 review R2 (M3170): the negative scans above walk REVIEW_PAGE_SCRIPTS, so a
    script dropped from it would be silently unscanned - the page's own `<script src>` tags, read
    outside comments, hold the list to the page (the ORDER is `test_static_syntax.py`'s)."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    loaded = re.findall(
        r'<script src="([^"]+)"', re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
    )
    assert len(loaded) >= 12, loaded
    assert sorted(script.name for script in REVIEW_PAGE_SCRIPTS) == sorted(loaded)


def test_the_required_lists_are_not_empty():
    """A parametrized control built from an empty list is SKIPPED by pytest, not failed
    (M743), so every list that gives a test below its meaning is pinned non-empty here."""
    # M14 Task 2 Step 6b final: 39 -> 27 (17 card-page checks retired, 5 negative pins in
    # their place), 14 -> 9 exports (the 5 `RETIRED_EXPORTS`), 10 -> 5 wiring rows and 4 -> 3
    # renders (the smart banner's and the presets' rows retired; the card's moved to the row).
    assert len(REQUIRED_CHECKS) >= 27
    assert len(REQUIRED_EXPORTS) >= 9
    assert len(RETIRED_EXPORTS) == 5
    assert len(WIRING) >= 5
    assert len(RENDERS) >= 3


def test_the_node_driver_ships_and_is_not_empty():
    assert NODE_DRIVER.is_file()
    assert len(NODE_DRIVER.read_text(encoding="utf-8")) > 8000


@pytest.mark.parametrize("check", REQUIRED_CHECKS)
def test_the_driver_names_every_check_this_condition_owes(check):
    assert f'test("{check}"' in NODE_DRIVER.read_text(encoding="utf-8")


def _seam_of(export: str) -> str:
    for name, path in MOVED_EXPORTS:
        if name == export:
            return path.read_text(encoding="utf-8")
    return _source()


@pytest.mark.parametrize("export", REQUIRED_EXPORTS)
def test_app_js_exports_every_function_plan_task_3_names(export):
    assert export in _export_names(_seam_of(export))


def test_app_js_exports_exactly_the_registered_seam():
    """The registry moved to EQUALITY (M13 Task 2, C5): every key bound to its own name, and
    the set of keys exactly `REQUIRED_EXPORTS` - less the names M14 Task 2 Step 6b moved, which
    the test above finds in the seam of the file each moved to."""
    moved = {name for name, _ in MOVED_EXPORTS}
    assert len(moved) == len(MOVED_EXPORTS) == 5 and moved <= set(REQUIRED_EXPORTS)
    pairs = _export_pairs(_source())
    assert [key for key, value in pairs if key != value] == []
    assert_exports_are_exactly(
        {key for key, _ in pairs}, tuple(e for e in REQUIRED_EXPORTS if e not in moved)
    )


def test_the_export_equality_refuses_an_extra_name_and_a_missing_one():
    """The equality pinned on hand-built sets, so a check weakened back to membership is
    red: an EXTRA export is exactly what membership cannot see."""
    assert_exports_are_exactly({"a", "b"}, ("a", "b"))
    with pytest.raises(AssertionError):
        assert_exports_are_exactly({"a", "b", "c"}, ("a", "b"))
    with pytest.raises(AssertionError):
        assert_exports_are_exactly({"a"}, ("a", "b"))


@pytest.mark.parametrize(("dom", "pure", "calls"), WIRING)
def test_each_dom_function_renders_what_its_pure_function_returns(dom, pure, calls):
    body = _function_body(_source(), dom)
    for call in calls:
        assert call in body, f"{dom} no longer calls {call}"
    assert body.count(f"{pure}(") == len(calls), f"{dom} calls {pure} some other way too"


# AND A CALL IS NOT A RENDERING (M12 Task 3 review, F2 / M2019): `trustDisplay` keeping
# `var view = trustView(item)` and then setting `data-band` from `TIER_BANDS[item.tier]`
# passes every WIRING row (the call is there, once) and every node check (which runs
# `trustView`, never `trustDisplay`) - and an unknown tier's bar is TRANSPARENT again, the
# exact defect `|| "warn"` exists to prevent. Plan Task 3: the DOM function "calls it and
# renders the result". So each render-shaped DOM function must read every field of the
# result - spelled from plan Task 3's table's return shapes, NOT from the pure function's
# source - and must not read the rule's own inputs itself (`item.`, `state.summary.`) or
# the rule's own tables.
RENDERS = (
    (
        "trustDisplay",
        ("view.edited", "view.band", "view.label", "view.percent", "view.width", "view.badge"),
        ("item.", "TIER_BANDS", "TIER_BADGES"),
    ),
    (
        "whyPanel",
        (
            "why.proposed",
            "why.tokens",
            "why.reasons",
            "why.total",
            "why.confidence",
            "why.restore",
        ),
        ("item.",),
    ),
    # M14 Task 2 Step 6b final: `renderSmartBanner`'s row retired with the banner.
    # M13 Task 10 review (M2524): H3's line is shown ONLY when the pure function returns one,
    # as a text node of its own (UX 5: "only when the server names any"). Spelled from UX 5,
    # not from `buildRow`; an inverted guard rendered an empty line on every bare card. Step
    # 6b final: the card's `buildCard` row MOVED to the row's `buildRow` (kind (a)).
    (
        "buildRow",
        ('["row-alike", model.alike]', "lines[i][1] !== null"),
        ("item.look_alikes",),
    ),
)


@pytest.mark.parametrize(("dom", "reads", "forbidden"), RENDERS)
def test_each_dom_function_renders_the_result_and_decides_nothing_itself(dom, reads, forbidden):
    body = _function_body(_source(), dom)
    for field in reads:
        assert field in body, f"{dom} no longer renders {field}"
    for raw in forbidden:
        assert raw not in body, f"{dom} reads {raw} itself instead of rendering the result"


# M13 Task 2 review (M2393, M2394): the bulk buttons' WIRING and Accept all's COUNT live in DOM
# code nothing executes, so deleting a listener or dropping half the count was green. M14
# Task 2 Step 6b final (U2): the two presets' buttons and the step-3 banner's three twins are
# GONE, and `Accept all` (U2/U3: the top shortcut stays) is wired by the list's `wireList`.
# Both halves are spelled here from U2, never read from `wireList` or `index.html`.
RETIRED_BULK_BUTTON_IDS = (
    "approve-high",
    "approve-high-medium",
    "banner-approve-high",
    "banner-approve-high-medium",
    "banner-accept-all",
)
assert len(RETIRED_BULK_BUTTON_IDS) == 5, "the retired-button list is spelled; it cannot shrink"


def _listener(body: str, node: str) -> str:
    """The text of `el.<node>.addEventListener("click", ...)` up to the next statement."""
    start = body.find(f'el.{node}.addEventListener("click", ')
    assert start >= 0, f"wireList never wires el.{node}"
    end = body.find("\n  el.", start + 1)
    return body[start : end if end >= 0 else len(body)]


def test_accept_all_is_wired_and_the_preset_buttons_are_gone():
    """Was `test_every_bulk_button_is_wired_to_its_own_handler` (M2393): rewritten at M14 Task
    2 Step 6b final to U2's contract - one bulk button left, wired; the other five gone."""
    body = _function_body(_source(), "wireList")
    assert "runAcceptAll" in _listener(body, "acceptAll"), (
        "el.acceptAll does not run runAcceptAll"
    )
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="accept-all"' in page
    for button in RETIRED_BULK_BUTTON_IDS:
        assert f'id="{button}"' not in page, f"index.html still offers #{button} (U2)"
    for script in REVIEW_PAGE_SCRIPTS:
        assert "runPreset" not in script.read_text(encoding="utf-8"), script.name


def test_accept_alls_count_is_the_plans_undecided_high_plus_medium():
    # M14 Task 2 Step 6b part 2: the count moved, with its scope and source, out of the
    # presets' `refreshPresetCounts` into `pam-list.js`'s `refreshAcceptAllCount`.
    body = _function_body(_source(), "refreshAcceptAllCount")
    scopes = re.findall(r"^\s*listCount\((.*?)\),?$", body, re.MULTILINE)
    high = scopes.index('{ status: "proposed", tier: "high" }')
    medium = scopes.index('{ status: "proposed", tier: "medium" }')
    assert len(scopes) == 2, scopes
    assert (
        f'listElements().acceptAllCount.textContent = "(" + (both[{high}] + both[{medium}]) + ")";'
        in body
    )


def test_no_review_script_exports_or_defines_a_retired_card_page_function():
    """M14 Task 2 Step 6b final: the stdlib half of the node driver's five negative pins, so a
    machine without node still sees a retired rule come back. The card page is not shipped,
    not loaded, and none of its retired functions is defined or exported anywhere."""
    assert not (STATIC_DIR / "pam-review.js").exists(), "the card page ships again"
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "pam-review.js" not in re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
    assert set(RETIRED_EXPORTS).isdisjoint(REQUIRED_EXPORTS)
    for script in REVIEW_PAGE_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        names = _export_names(text) if "module.exports = {" in text else set()
        for retired in RETIRED_EXPORTS:
            assert f"function {retired}(" not in text, f"{script.name} defines {retired}"
            assert retired not in names, f"{script.name} exports {retired}"


def test_the_export_reader_sees_a_deleted_export():
    """The reader above, pinned on a hand-built block, so a regex that matched nothing
    cannot pass every parametrized case by reading an empty set as 'nothing missing'."""
    block = "module.exports = {\n    a: a,\n    b: c,\n    d: d,\n  };"
    assert _export_names(block) == {"a", "d"}


# --- the node half: skips where node is absent, and that is why the half above exists --


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_executes_exactly_the_checks_it_claims():
    run = subprocess.run(
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    ran = {line[3:] for line in run.stdout.splitlines() if line.startswith("ok ")}
    assert ran == set(REQUIRED_CHECKS), (
        sorted(set(REQUIRED_CHECKS) - ran),
        sorted(ran - set(REQUIRED_CHECKS)),
    )
    assert f"ran {len(REQUIRED_CHECKS)}" in run.stdout.splitlines()

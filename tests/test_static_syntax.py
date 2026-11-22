"""Whether the review UI's own files are LOADABLE - the other thing this suite cannot see.

WHY THIS FILE EXISTS (M7 close, finding F4). `pam/webui/static/app.js` is ~2300 executable
lines and **nothing else in this project reads it**: ruff and mypy see Python, and every
webui test treats the file as bytes to serve rather than as a program to run. Measured at
the M7 close by appending one unclosed brace to it: **1862 passed, 1 skipped** - the entire
suite green over a page that a browser refuses to parse, so `pam review` opens a window
containing nothing at all. That is the same shape as the packaging finding beside it
(`test_packaging.py`): the suite tests what it can reach, and the deliverable is elsewhere.

TWO CHECKS, AND ONLY ONE OF THEM CAN SKIP. That split is deliberate. A guard whose whole
value depends on an external tool is a guard that disarms silently on the machine that does
not have it - this project's standing worry about controls that vanish rather than fail. So
the asset-reference check below uses nothing but the standard library and **always runs**,
and the syntax check that needs Node is the addition on top rather than the whole thing.

WHY `node --check` AND NOT A HAND-ROLLED BRACE COUNTER. CLAUDE.md's rule from the M6 close:
a hand-rolled parser cannot notice that the document is not the language. Deciding whether a
`/` opens a regular expression or divides requires parsing JavaScript, so a counter written
here would answer confidently and wrongly. Node is a real parser, it is preinstalled on
GitHub's hosted runners, and `--check` neither executes the file nor touches the network.
**It adds no Python dependency** - it is an external tool on the exiftool precedent.

M11 TASK 10 STEP 2 - THE GUARD MUST NOT BE ITS OWN ORACLE (the plan's Blocker 8.2). The
review page stopped being the only page here: `pam faces label` ships `faces.html` +
`faces.js` beside it, sharing `style.css`. The obvious way to widen this module - iterate
`STATIC_FILES` and check whatever it names - is exactly the failure this project keeps
measuring (`repo.ENRICHERS`, `repo.open_plan`): **deleting an entry from `STATIC_FILES`
would delete the SUBJECT along with the expectation**, and every assertion here would pass
over a smaller set that still agreed with itself. So:

  - **the inventory is DISCOVERED**, by walking the static directory on the filesystem and
    by reading it back out of the installed package, neither of which consults
    `STATIC_FILES`;
  - **the required files are LITERALS**, spelled from design spec 8.2 (the review page) and
    UX spec 6 (the labeling page), and each must be BOTH on disk AND named in
    `STATIC_FILES`. Delete `faces.js` from `STATIC_FILES` and that is red here;
  - **`node --check` runs over every discovered `.js`**, not over a named constant, so the
    next page's script is covered the day it lands rather than the day somebody remembers;
  - **every discovered `.html` has its local references cross-checked** against what the
    server will actually serve, because a `<script src>` typo blanks a page as completely
    as a syntax error does and was invisible to all 194 webui tests at the M7 close (M784).

WHAT THIS MODULE DOES NOT DUPLICATE. `tests/test_webui_server.py::test_every_shipped_static
_file_is_routable_and_every_routable_name_ships` already compares an independent on-disk
inventory against `STATIC_FILES` in both directions. That check is not repeated here; what
is added is the installed-package side of the discovery, the parse, the reference
cross-check, and the literal floor naming the files the two pages cannot run without.
"""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path

import pytest

from pam.webui.server import (
    BAR_SCRIPT,
    CHOOSER_SCRIPT,
    CONFIRM_SCRIPT,
    CORE_SCRIPT,
    DETAIL_SCRIPT,
    DONE_SCRIPT,
    FACES_INDEX,
    FACES_SCRIPT,
    HISTORY_SCRIPT,
    INDEX,
    KEYS_SCRIPT,
    LIST_SCRIPT,
    READING_SCRIPT,
    ROW_SCRIPT,
    SCRIPT,
    SELECTION_SCRIPT,
    STATIC_FILES,
    STOPPED_SCRIPT,
    STYLESHEET,
    TRUST_SCRIPT,
    WELCOME_SCRIPT,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "pam" / "webui" / "static"

# The files the two pages cannot run without, spelled from the SPECS rather than read back
# from `STATIC_FILES` or from the directory: design spec 8.2's review page is one page, one
# script and one stylesheet; UX spec 6's labeling page is a second page and a second script
# sharing that same stylesheet. An oracle built from the definition it checks is a survivor
# waiting to happen, and this list is the independent side of that pairing.
REQUIRED_STATIC_FILES = (
    "index.html",
    # M14 Task 1: the review page's five classic scripts, in the order design spec 2 lists
    # them and `index.html` loads them.
    "pam-core.js",
    "pam-trust.js",
    "pam-keys.js",
    # M14 Task 2: the list (U2) - one row's render, then the list. Step 6b retired the card
    # page, `pam-review.js`.
    "pam-row.js",
    # M14 Task 3 (U3, U17): the selection as data, and its bar.
    "pam-selection.js",
    "pam-list.js",
    "pam-bar.js",
    # M14 Task 2 Step 6a: the preview overlay and the name editor, on the list.
    "pam-detail.js",
    # M14 Task 5a: Welcome's folder chooser (U13), cut out of `pam-welcome.js`, before it.
    "pam-chooser.js",
    # M14 Task 5e: Welcome's Reading step (U11), after the chooser and before Welcome.
    "pam-reading.js",
    # M14 Task 4 Step 7: Welcome (W18, U13), before the boot that decides on it.
    "pam-welcome.js",
    # M14 Task 6c: the Confirm screen, then Done and undo (U6, W19-W21), before the boot.
    "pam-confirm.js",
    "pam-done.js",
    # M15 Task 5b: a rename that did not run or stopped part-way, cut out of `pam-done.js`.
    "pam-stopped.js",
    # M14 Task 7: History (W22, X2), after Done, whose undo it reuses.
    "pam-history.js",
    "app.js",
    "style.css",
    "faces.html",
    "faces.js",
)

# The order the review page loads its scripts in, spelled from M14 Task 1 / design spec 2's
# tree - never read off the page. `pam-core.js` creates `window.PAM` and the rest attach to
# it, so this order is load-bearing (`tests/js/load_order_test.js` runs it).
REVIEW_SCRIPT_ORDER = (
    "pam-core.js",
    "pam-trust.js",
    "pam-keys.js",
    "pam-row.js",
    # M14 Task 3 (U3, U17): the selection before the list, its bar after it.
    "pam-selection.js",
    "pam-list.js",
    "pam-bar.js",
    # M14 Task 2 Step 6a: the preview overlay and the name editor, on the list.
    "pam-detail.js",
    # M14 Task 5a: Welcome's folder chooser (U13), cut out of `pam-welcome.js`, before it.
    "pam-chooser.js",
    # M14 Task 5e: Welcome's Reading step (U11), after the chooser and before Welcome.
    "pam-reading.js",
    # M14 Task 4 Step 7: Welcome (W18, U13), before the boot that decides on it.
    "pam-welcome.js",
    # M14 Task 6c: the Confirm screen, then Done and undo (U6, W19-W21), before the boot.
    "pam-confirm.js",
    "pam-done.js",
    # M15 Task 5b: a rename that did not run or stopped part-way, cut out of `pam-done.js`.
    "pam-stopped.js",
    # M14 Task 7: History (W22, X2), after Done, whose undo it reuses.
    "pam-history.js",
    "app.js",
)

# UX spec 6's page, named on its own so the assertion about it cannot vanish with a
# parametrize list (M743: pytest SKIPS an empty parametrize rather than failing it).
REQUIRED_FACE_ASSETS = ("faces.html", "faces.js")

# COUNTED FROM THE SPECS, not derived as `len(REQUIRED_STATIC_FILES)` - deriving it is what
# mutation M793 exposed in `test_packaging.py`, where emptying the tuple made the floor
# `0 >= 0` and both halves of the guard disappeared together, green. M14 Task 1: design
# spec 2's `static/` tree names NINE files - two pages, the review page's five scripts, the
# faces page's script and the shared stylesheet.
MINIMUM_STATIC_FILES = (
    20  # M14 Task 2: + `pam-row.js`, `pam-list.js`; Step 6a: + `pam-detail.js`; Step 6b:
    # - `pam-review.js` (the card page, retired); M14 Task 3: + `pam-selection.js`, `pam-bar.js`;
    # M14 Task 4: + `pam-welcome.js`; M14 Task 5a: + `pam-chooser.js`; 5e: + `pam-reading.js`;
    # M14 Task 6c: + `pam-confirm.js`, `pam-done.js`; M14 Task 7: + `pam-history.js`;
    # M15 Task 5b: + `pam-stopped.js` (cut out of `pam-done.js`)
)

# The number of scripts the specs describe: design spec 2's five review scripts (M14 Task
# 1; `app.js` alone until then) and UX spec 6's `faces.js`. The floor under the parametrized
# parse check below, written independently of the discovery it protects.
MINIMUM_SCRIPTS = (
    17  # M14 Task 2: + row, list; 6a: + detail; 6b: - review; Task 3: + selection, bar;
    # Task 4: + welcome; Task 5a: + chooser; Task 5e: + reading; Task 6c: + confirm, done;
    # Task 7: + history; M15 Task 5b: + stopped
)

# The number of pages the specs describe: the review page and the labeling page.
MINIMUM_PAGES = 2

# Local assets each page pulls in, spelled from the SPEC side rather than scraped from the
# files, so a reference that goes missing is a failure here rather than a smaller set that
# still agrees with itself. Design spec 8.2: one stylesheet, one script. UX spec 7: the
# labeling page shares that stylesheet and brings its own script.
EXPECTED_REFERENCES = {
    # M14 Task 1: the review page's one script became five (design spec 2).
    # M14 Task 2: + the row and the list (U2).
    INDEX: (
        STYLESHEET,
        CORE_SCRIPT,
        TRUST_SCRIPT,
        KEYS_SCRIPT,
        ROW_SCRIPT,
        SELECTION_SCRIPT,
        LIST_SCRIPT,
        BAR_SCRIPT,
        DETAIL_SCRIPT,
        CHOOSER_SCRIPT,
        READING_SCRIPT,
        WELCOME_SCRIPT,
        CONFIRM_SCRIPT,
        DONE_SCRIPT,
        STOPPED_SCRIPT,
        HISTORY_SCRIPT,
        SCRIPT,
    ),
    FACES_INDEX: (STYLESHEET, FACES_SCRIPT),
}

_REFERENCE = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _local_references(html: str) -> list[str]:
    """Every `href`/`src` in the page that names a file rather than an external target."""
    return [
        value
        for value in _REFERENCE.findall(html)
        if "://" not in value and not value.startswith(("#", "data:", "mailto:"))
    ]


def _discovered_on_disk() -> list[str]:
    """The static inventory as the FILESYSTEM has it. Consults no production constant."""
    return sorted(entry.name for entry in STATIC_DIR.iterdir() if entry.is_file())


def _discovered_in_package() -> list[str]:
    """The static inventory as the INSTALLED PACKAGE has it.

    `server.py` reads these files with `importlib.resources`, so this is the view the
    running product actually gets. Under pytest it resolves to the source tree and agrees
    with the walk above; installed non-editable it does not have to, and the difference is
    the one `test_packaging.py` exists for - a file on disk that no `package-data` pattern
    carries is a file the wheel does not have.
    """
    static = resources.files("pam.webui") / "static"
    return sorted(entry.name for entry in static.iterdir() if entry.is_file())


def _discovered(suffix: str) -> list[str]:
    return [name for name in _discovered_on_disk() if name.endswith(suffix)]


def test_the_required_static_file_list_is_not_empty():
    """M743's shape, unparametrized so it cannot be deleted by what it protects.

    The checks below are parametrized over `REQUIRED_STATIC_FILES`, and pytest SKIPS an
    empty parametrize instead of failing it. This test is the one that cannot disappear
    with the list.
    """
    assert len(REQUIRED_STATIC_FILES) >= MINIMUM_STATIC_FILES, REQUIRED_STATIC_FILES
    assert REQUIRED_FACE_ASSETS, "no face asset is named, so the labeling page is unguarded"


def test_the_discovery_finds_a_real_inventory_rather_than_nothing():
    """M528's lesson applied to this module's own input.

    Every parametrized check below is built from the discovery. If the walk ever returns
    nothing - a moved directory, a renamed package - those checks are SKIPPED rather than
    failed and this module protects nothing at all. The floor is counted from the specs.
    """
    found = _discovered_on_disk()
    assert len(found) >= MINIMUM_STATIC_FILES, found
    assert len(_discovered(".js")) >= MINIMUM_SCRIPTS, found
    assert len(_discovered(".html")) >= MINIMUM_PAGES, found


def test_the_installed_package_offers_the_same_static_files_the_filesystem_does():
    """The installed-package side of the discovery, which the filesystem walk cannot see.

    `server.py` serves these bytes out of `pam.webui` with `importlib.resources`, never
    off a path it joined. A file that sits in the source tree but that the package does not
    expose is a 500 waiting for the request that asks for it.
    """
    assert _discovered_in_package() == _discovered_on_disk()


@pytest.mark.parametrize("name", REQUIRED_STATIC_FILES)
def test_every_required_static_file_is_present_and_routable(name):
    """The LITERAL half. Two claims, deliberately in one test.

    The file exists, AND the server will serve it: `_static_file` looks a name up in
    `STATIC_FILES` and 404s anything absent, so a file shipped without an entry is a file
    no browser can ever load. Because this list is written from the specs and not read off
    `STATIC_FILES`, deleting an entry from that map is red here - which is what the guard
    derived from `STATIC_FILES` could not be.
    """
    assert (STATIC_DIR / name).is_file(), f"{name} is missing from pam/webui/static/"
    assert name in STATIC_FILES, f"{name} ships but the server will 404 it"


def test_the_labeling_page_ships_both_of_its_own_assets():
    """UX spec 6's page, asserted where no parametrize list can take it away with it."""
    for name in REQUIRED_FACE_ASSETS:
        assert (STATIC_DIR / name).is_file(), f"{name} is missing from pam/webui/static/"
        assert name in STATIC_FILES, f"{name} ships but the server will 404 it"


@pytest.mark.parametrize("name", _discovered(".html"))
def test_every_discovered_page_loads_only_files_the_server_will_serve(name):
    """A typo in one `src` blanks a page as completely as a syntax error does (M784).

    The server answers by NAME chosen from `STATIC_FILES` and 404s anything else, so a
    reference to `ap.js` is not a broken link the user can shrug at - it is the page
    failing to load, reported as a missing file nobody looks for. Parametrized over what
    was DISCOVERED, so a page added later is covered without being remembered.
    """
    references = _local_references((STATIC_DIR / name).read_text(encoding="utf-8"))
    # Non-empty first: a regex that silently matched nothing would pass every claim below.
    assert references, f"no local href/src found in {name} - the reader is broken"
    for reference in references:
        assert reference in STATIC_FILES, f"{name} loads {reference!r}, which the server 404s"
        assert (STATIC_DIR / reference).is_file(), f"{name} loads {reference!r}, not on disk"


@pytest.mark.parametrize("name", sorted(EXPECTED_REFERENCES))
def test_every_page_pulls_in_exactly_the_assets_its_spec_gives_it(name):
    """The spec-side half of the check above: not merely servable, but the RIGHT set.

    The loop above would be satisfied by a page that loaded only its stylesheet. This one
    says which files each page is supposed to load, spelled from design spec 8.2 and UX
    spec 7 rather than read back off the page.
    """
    references = _local_references((STATIC_DIR / name).read_text(encoding="utf-8"))
    assert sorted(references) == sorted(EXPECTED_REFERENCES[name]), references


def test_the_spec_side_reference_table_covers_every_page_that_ships():
    """Both directions, so a new page cannot quietly escape the table above.

    Without this, adding a third page would be checked only by the servable-reference loop
    - the weaker claim - and the stronger spec-side expectation would silently not apply
    to it.
    """
    assert sorted(EXPECTED_REFERENCES) == _discovered(".html")


def test_the_reference_reader_ignores_external_and_non_file_targets():
    """The negative control under `_local_references`.

    A reader that returned everything would make the `in STATIC_FILES` assertions above
    fail loudly on any page, and a reader that returned nothing would make them pass
    vacuously. Neither failure would name itself, so the filter is pinned here directly.
    """
    markup = (
        '<link rel="stylesheet" href="style.css">'
        '<script src="app.js"></script>'
        '<a href="#top">top</a>'
        '<a href="mailto:someone@example.invalid">mail</a>'
        '<img src="data:image/png;base64,AAAA">'
        '<script src="https://cdn.example.invalid/x.js"></script>'
    )
    assert _local_references(markup) == ["style.css", "app.js"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize("name", _discovered(".js"))
def test_every_shipped_script_parses_as_javascript(name):
    """`node --check` - parse only. It does not run the file and opens no socket.

    This is the check that would have caught F4, widened from the named constant `SCRIPT`
    to every script DISCOVERED in the static directory. It skips where Node is absent,
    which is why the reference checks above deliberately do not depend on it.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied args
        [shutil.which("node"), "--check", str(STATIC_DIR / name)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.strip()[:500]


# --- M14 Task 1: the review page's script ORDER, read by a real parser -------------------


class _ScriptReader(HTMLParser):
    """Every `<script>` in a page, in document order: its `src` and any inline data.

    The stdlib's `html.parser` - a real HTML tokenizer, no dependency - is the ORACLE beside
    `tests/js/load_order_test.js`'s regex read of the same list (node has no HTML parser).
    Comments are the tokenizer's, so a `<script>` written inside `<!-- -->` is not a script.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str | None] = []
        self.inline: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.sources.append(dict(attrs).get("src"))
            self._inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside and data.strip():
            self.inline.append(data.strip())


def _scripts_in(markup: str) -> tuple[list[str | None], list[str]]:
    reader = _ScriptReader()
    reader.feed(markup)
    reader.close()
    return reader.sources, reader.inline


def test_the_review_script_order_is_spelled_and_names_only_required_files():
    """The floor under the order check below, which a list emptied with it would pass."""
    assert len(REVIEW_SCRIPT_ORDER) >= 5, REVIEW_SCRIPT_ORDER
    assert REVIEW_SCRIPT_ORDER[0] == "pam-core.js", (
        "pam-core.js creates window.PAM; it is first"
    )
    assert REVIEW_SCRIPT_ORDER[-1] == "app.js", "app.js is the boot; it is last"
    assert set(REVIEW_SCRIPT_ORDER) <= set(REQUIRED_STATIC_FILES)


def test_the_review_page_loads_its_scripts_in_the_spec_order_and_none_inline():
    """M14 Task 1: `index.html`'s scripts, parsed, are exactly the spec's five in order.

    `test_every_page_pulls_in_exactly_the_assets_its_spec_gives_it` compares SORTED sets, so
    it cannot see two tags swapped; this does. And no `<script>` carries inline code - the
    precondition under which `tests/js/load_order_test.js`'s regex read is sound.
    """
    sources, inline = _scripts_in((STATIC_DIR / INDEX).read_text(encoding="utf-8"))
    assert sources, "the parser found no <script> in index.html - the read is broken"
    assert sources == list(REVIEW_SCRIPT_ORDER), sources
    assert inline == [], f"index.html carries inline script code: {inline}"


def test_the_script_parser_sees_a_swap_inline_code_and_a_commented_tag():
    """The negative control under `_scripts_in`, on markup written here."""
    swapped = '<script src="b.js"></script><script src="a.js"></script>'
    assert _scripts_in(swapped)[0] == ["b.js", "a.js"]
    assert _scripts_in("<script>start();</script>") == ([None], ["start();"])
    assert _scripts_in('<!-- <script src="x.js"></script> -->') == ([], [])


# --- M14 Task 1: the load-order harness ----------------------------------------------------

LOAD_ORDER_DRIVER = Path(__file__).resolve().parent / "js" / "load_order_test.js"

# The checks the harness must actually EXECUTE, spelled here and never read back out of the
# driver; the node half asserts the set that ran EQUALS this.
REQUIRED_LOAD_ORDER_CHECKS = (
    "the page lists at least one script, and every script is an external file",
    "every script the page lists runs in the page's order without an error",
    "each script attaches its own part of the page's namespace",
    "the page boots, and its first request is the ping",
    "each namespace offers exactly what that script's node seam exports",
    "a review script loaded before pam-core.js fails at load, and is named",
    "the script reader sees a missing src and inline code",
)


def test_the_load_order_harness_ships_and_names_every_check():
    """The stdlib half, which never skips: without it a machine with no node would run
    nothing here and report green over a deleted harness."""
    assert len(REQUIRED_LOAD_ORDER_CHECKS) >= 7
    assert LOAD_ORDER_DRIVER.is_file()
    text = LOAD_ORDER_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 4000
    for check in REQUIRED_LOAD_ORDER_CHECKS:
        assert f'test("{check}"' in text, check


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_load_order_harness_executes_exactly_the_checks_it_claims():
    """The review page's scripts run in `index.html`'s order in one `vm` context."""
    run = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied args
        [shutil.which("node"), str(LOAD_ORDER_DRIVER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    ran = {line[3:] for line in run.stdout.splitlines() if line.startswith("ok ")}
    assert ran == set(REQUIRED_LOAD_ORDER_CHECKS), (
        sorted(set(REQUIRED_LOAD_ORDER_CHECKS) - ran),
        sorted(ran - set(REQUIRED_LOAD_ORDER_CHECKS)),
    )
    assert f"ran {len(REQUIRED_LOAD_ORDER_CHECKS)}" in run.stdout.splitlines()
    # F-1-1 (M2743): the checks' NAMES cannot tell a harness that loaded the page from one that
    # returned early over an empty list. The files it actually executed, in order, are held to
    # the order spelled above - never to what the harness read.
    assert "loaded " + ",".join(REVIEW_SCRIPT_ORDER) in run.stdout.splitlines(), run.stdout


# --- M14 Task 1: UX spec 7's contrast floor, computed ---------------------------------------
#
# UX 7: "contrast >= 4.5:1". The ratio is computed HERE from the token VALUES with this
# module's own WCAG 2.x formula - never read from the ratios `style.css` writes in its
# comments, which are stripped before the values are read. The pairs are spelled from UX 7
# rev 16's token list: every text token on every background it is drawn on.

CONTRAST_FLOOR = 4.5
CONTRAST_PAIRS = (
    ("--text", "--bg"),
    ("--text", "--surface"),
    ("--text", "--wash"),
    ("--text-muted", "--bg"),
    ("--text-muted", "--surface"),
    ("--text-muted", "--wash"),
    ("--accent", "--bg"),
    ("--accent", "--surface"),
    ("--on-accent", "--accent"),
    ("--on-accent", "--accent-hover"),
    ("--ok", "--ok-bg"),
    ("--warn", "--warn-bg"),
    ("--danger", "--danger-bg"),
    ("--ok", "--surface"),
    ("--warn", "--surface"),
    ("--danger", "--surface"),
    # F-1-2 (M2745): the band colours are also TEXT on the page background - the faces page's
    # `#action-note` (`--ok`), `.person-hidden` (`--warn`) and `#request-error` (`--danger`).
    ("--ok", "--bg"),
    ("--warn", "--bg"),
    ("--danger", "--bg"),
)
THEMES = ("light", "dark")


def _relative_luminance(colour: str) -> float:
    """WCAG 2.x relative luminance of a `#rrggbb` (or `#rgb`) colour."""
    digits = colour.strip().lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    assert re.fullmatch(r"[0-9a-fA-F]{6}", digits), f"not a hex colour: {colour!r}"
    channels = []
    for start in (0, 2, 4):
        value = int(digits[start : start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)))[::-1]
    return (lighter + 0.05) / (darker + 0.05)


def _block_after(css: str, opening: re.Match[str]) -> str:
    depth = 0
    for index in range(opening.end() - 1, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[opening.end() : index]
    raise AssertionError("unbalanced braces in style.css")


def theme_tokens(css: str) -> dict[str, dict[str, str]]:
    """The light `:root` tokens and the dark ones, the dark theme INHERITING every token it
    does not redefine - which is what a browser does. Comments are stripped first."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    dark_rule = re.search(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{", css)
    assert dark_rule, "no dark token block"
    dark_body = _block_after(css, dark_rule)
    light_css = css.replace(dark_body, "", 1)
    light_root = re.search(r":root\s*\{", light_css)
    dark_root = re.search(r":root\s*\{", dark_body)
    assert light_root and dark_root, "a :root token block is missing"

    def declared(block: str) -> dict[str, str]:
        found = re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);", block)
        return {name: value.strip() for name, value in found}

    light = declared(_block_after(light_css, light_root))
    return {"light": light, "dark": {**light, **declared(_block_after(dark_body, dark_root))}}


def test_the_contrast_pair_list_is_not_empty_and_covers_both_themes():
    """M743: the parametrized check below is SKIPPED, not failed, over an empty list."""
    assert len(CONTRAST_PAIRS) >= 19
    assert THEMES == ("light", "dark")


def test_the_contrast_formula_agrees_with_wcag_reference_values():
    """The formula pinned on values WCAG itself gives, so a broken formula cannot pass every
    pair by returning a large number."""
    assert round(contrast_ratio("#000000", "#ffffff"), 2) == 21.0
    assert round(contrast_ratio("#ffffff", "#ffffff"), 2) == 1.0
    assert round(contrast_ratio("#777777", "#ffffff"), 2) == 4.48
    assert contrast_ratio("#777777", "#ffffff") < CONTRAST_FLOOR
    assert round(contrast_ratio("#fff", "#000"), 2) == 21.0
    tokens = theme_tokens(
        ":root { --a: #000000; --b: #ffffff; }"
        "@media (prefers-color-scheme: dark) { :root { --a: #777777; } }"
    )
    assert tokens["dark"] == {"--a": "#777777", "--b": "#ffffff"}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("text", "background"), CONTRAST_PAIRS)
def test_every_text_and_background_pair_clears_ux_7s_contrast_floor(theme, text, background):
    tokens = theme_tokens((STATIC_DIR / STYLESHEET).read_text(encoding="utf-8"))[theme]
    assert text in tokens and background in tokens, f"{text} or {background} is undefined"
    ratio = contrast_ratio(tokens[text], tokens[background])
    assert ratio >= CONTRAST_FLOOR, (
        f"{theme}: {text} {tokens[text]} on {background} {tokens[background]} is "
        f"{ratio:.2f}:1, under UX 7's {CONTRAST_FLOOR}:1"
    )


# --- M14 Task 1 review: UX 7 rev 16's token VALUES, the phone notice, U20's scope, U4 ---------
#
# The contrast check above proves every pair clears 4.5:1; it cannot prove the stylesheet holds
# the values UX 7 rev 16 SPELLS (M2746: dark `--surface` one unit off the spec, green) or that
# the rules Task 1 added stay where U20 puts them (M2761) and do what U4 says (M2762). Each
# expectation below is spelled from the SPEC's text, never read back out of `style.css`.

# UX 7 rev 16's Tokens bullet, value for value (lower-cased: the stylesheet stores lower-case
# hex). The dark theme inherits what it does not redefine, so the size tokens are light's only.
UX_7_REV_16_TOKENS = {
    "light": {
        "--bg": "#f4f5f7",
        "--surface": "#ffffff",
        "--text": "#16191d",
        "--text-muted": "#5b636e",
        "--line": "#e3e6ea",
        "--wash": "#ebedf0",
        "--accent": "#1f4fd1",
        "--accent-hover": "#163c9f",
        "--on-accent": "#ffffff",
        "--ok": "#17784a",
        "--ok-bg": "#e6f4ec",
        "--warn": "#8a5a00",
        "--warn-bg": "#fbf1dc",
        "--danger": "#b23a1e",
        "--danger-bg": "#fbe9e4",
        "--radius": "10px",
        "--radius-lg": "16px",
        "--touch": "44px",
    },
    "dark": {
        "--bg": "#111418",
        "--surface": "#1a1e24",
        "--text": "#e8eaed",
        "--text-muted": "#a3abb6",
        "--line": "#2c323a",
        "--wash": "#252a31",
        "--accent": "#8aaeff",
        "--accent-hover": "#b0c8ff",
        "--on-accent": "#0b1730",
        "--ok": "#6fd3a0",
        "--ok-bg": "#12301f",
        "--warn": "#e8bd62",
        "--warn-bg": "#33270d",
        "--danger": "#ff9c86",
        "--danger-bg": "#3a1d16",
    },
}


def test_every_token_ux_7_rev_16_spells_has_its_value_in_both_themes():
    """F-1-3 (M2745, M2746): UX 7 rev 16 spells every colour, both themes; the suite pinned 8."""
    assert len(UX_7_REV_16_TOKENS["light"]) >= 18
    assert len(UX_7_REV_16_TOKENS["dark"]) >= 15
    tokens = theme_tokens((STATIC_DIR / STYLESHEET).read_text(encoding="utf-8"))
    for theme, spelled in UX_7_REV_16_TOKENS.items():
        for name, value in spelled.items():
            found = tokens[theme].get(name, "<undefined>").lower()
            assert found == value, f"{theme} {name} is {found}, UX 7 rev 16 says {value}"


def _css_rules(css: str) -> list[tuple[list[str], str, str | None]]:
    """Every rule in a stylesheet as (selectors, body, enclosing `@media` prelude or None).

    A small brace walker, enough for this project's own stylesheet: comments stripped first,
    `@media` blocks walked into. Selectors and bodies are whitespace-normalised."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[list[str], str, str | None]] = []

    def walk(text: str, media: str | None) -> None:
        index = 0
        while (opening := text.find("{", index)) >= 0:
            depth = 0
            close = -1
            for position in range(opening, len(text)):
                if text[position] == "{":
                    depth += 1
                elif text[position] == "}":
                    depth -= 1
                    if depth == 0:
                        close = position
                        break
            assert close >= 0, "unbalanced braces in the stylesheet"
            prelude, body = text[index:opening].strip(), text[opening + 1 : close]
            if prelude.startswith("@media"):
                walk(body, " ".join(prelude.split()))
            else:
                selectors = [" ".join(each.split()) for each in prelude.split(",")]
                rules.append((selectors, " ".join(body.split()), media))
            index = close + 1

    walk(css, None)
    return rules


# M14 Task 2 Step 6b (U4): THE REVIEW PAGE HAS NO PHONE NOTICE. U4 retires `index.html`'s
# "Open this on a larger screen" and the 1099px rule that swapped the review page out; the
# faces page keeps its own notice in M14 (U20). Task 1's two controls below pinned the swap;
# they are rewritten to pin its retirement (kind (b)), keeping their registration, renamed.
REVIEW_PAGE_SCOPE = "body:not(.review-page)"
RETIRED_NOTICE_WORDS = ("narrow-notice", "larger screen")


def assert_only_the_faces_page_is_swapped_for_a_notice(css: str, review_html: str) -> None:
    """U4: the review page carries no notice and is never hidden at a narrow width; U20: the
    faces page is still replaced by its notice below 1100px, never squeezed."""
    for word in RETIRED_NOTICE_WORDS:
        assert word not in review_html, f"the review page still carries {word!r} (U4)"
    rules = _css_rules(css)
    narrow = [
        (selectors, body)
        for selectors, body, media in rules
        if media and re.search(r"max-width\s*:\s*1099px", media)
    ]
    hiding = [
        selector
        for selectors, body in narrow
        if "display: none" in body
        for selector in selectors
        if "#page" in selector
    ]
    unscoped = [selector for selector in hiding if REVIEW_PAGE_SCOPE not in selector]
    assert unscoped == [], f"a 1099px rule still hides the review page: {unscoped}"
    assert hiding, "no 1099px rule hides the faces page's #page: it would be squeezed (U20)"
    assert any("#narrow-notice" in s and "display: block" in b for s, b in narrow), (
        "no max-width 1099px rule shows the faces page's #narrow-notice"
    )
    assert any(
        s == ["#narrow-notice"] and "display: none" in b for s, b, media in rules if not media
    ), "#narrow-notice is not hidden at a supported width"


def test_the_review_page_has_no_phone_notice_and_the_faces_page_keeps_its_swap():
    """Was Task 1's `test_the_narrow_viewport_rule_swaps_the_page_for_the_notice` (F-1-4,
    M2759/M2760). M14 Task 2 Step 6b (U4) retired the review page's notice, so the check is
    now that it is GONE from the review page and still whole on the faces page (U20)."""
    assert_only_the_faces_page_is_swapped_for_a_notice(
        (STATIC_DIR / STYLESHEET).read_text(encoding="utf-8"),
        (STATIC_DIR / INDEX).read_text(encoding="utf-8"),
    )
    assert "narrow-notice" in (STATIC_DIR / FACES_INDEX).read_text(encoding="utf-8")


_FACES_SWAP = (
    "#narrow-notice { display: none; }"
    "@media (max-width: 1099px) { body:not(.review-page) #page { display: none !important; }"
    " #narrow-notice { display: block; } }"
)


@pytest.mark.parametrize(
    ("css", "review_html"),
    [
        # The review page swapped out again: an unscoped hide (Task 1's rule, as it shipped).
        (
            "#narrow-notice { display: none; }"
            "@media (max-width: 1099px) { #page { display: none !important; }"
            " #narrow-notice { display: block; } }",
            "<body class='review-page'></body>",
        ),
        # The review page's notice markup put back.
        (_FACES_SWAP, "<div id='narrow-notice'></div>"),
        # The review page's notice copy put back under another id.
        (_FACES_SWAP, "<p>Open this on a larger screen</p>"),
        # ONE ROW PER OPERAND of the faces half (U20): the hide deleted,
        (
            "#narrow-notice { display: none; }"
            "@media (max-width: 1099px) { #narrow-notice { display: block; } }",
            "",
        ),
        # the notice never shown,
        (
            "#narrow-notice { display: none; }"
            "@media (max-width: 1099px) { body:not(.review-page) #page { display: none; } }",
            "",
        ),
        # and the notice shown at every supported width.
        (
            "@media (max-width: 1099px) { body:not(.review-page) #page { display: none; }"
            " #narrow-notice { display: block; } }",
            "",
        ),
    ],
)
def test_the_no_notice_check_refuses_a_review_page_that_is_still_swapped_out(css, review_html):
    """Was Task 1's `test_the_narrow_rule_check_refuses_a_rule_that_only_half_swaps`: the
    negative control, rewritten with the check it controls."""
    with pytest.raises(AssertionError):
        assert_only_the_faces_page_is_swapped_for_a_notice(css, review_html)


def test_the_no_notice_check_accepts_the_faces_only_swap():
    """The positive control on a hand-built pair, so the refusals above are not refusing
    everything."""
    assert_only_the_faces_page_is_swapped_for_a_notice(_FACES_SWAP, "<body></body>")


# M14 Task 2 review R2 (M2870): the swap above is spared by `body:not(.review-page)`, so it is
# only as true as the review page's <body> carrying that class - a check on the CSS alone is
# green over a review page that lost the class and is hidden below 1100px again. Read with a
# real HTML parser: the review page's body opts out, the faces page's does not (U4, U20).
class _BodyClasses(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[set[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.found.append(set((dict(attrs).get("class") or "").split()))


def _body_classes(markup: str) -> set[str]:
    reader = _BodyClasses()
    reader.feed(markup)
    assert len(reader.found) == 1, f"expected one <body>, found {len(reader.found)}"
    return reader.found[0]


def assert_the_scope_spares_only_the_review_page(review_html: str, faces_html: str) -> None:
    assert "review-page" in _body_classes(review_html), (
        "the review page's <body> lost .review-page: the 1099px rule hides it again (U4)"
    )
    assert "review-page" not in _body_classes(faces_html), (
        "the faces page's <body> carries .review-page: it is squeezed, not swapped (U20)"
    )


def test_the_review_pages_body_opts_out_of_the_swap_and_the_faces_pages_does_not():
    assert_the_scope_spares_only_the_review_page(
        (STATIC_DIR / INDEX).read_text(encoding="utf-8"),
        (STATIC_DIR / FACES_INDEX).read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    ("review_html", "faces_html"),
    [
        ("<body></body>", "<body></body>"),
        ("<body class='shell'></body>", "<body></body>"),
        ("<body class='review-page'></body>", "<body class='review-page'></body>"),
    ],
)
def test_the_body_scope_check_refuses_a_page_pair_the_rule_would_get_wrong(
    review_html, faces_html
):
    with pytest.raises(AssertionError):
        assert_the_scope_spares_only_the_review_page(review_html, faces_html)


SHELL_SECTION = "/* --- the review page's layout shell"
# The shell's rule count today is 17; the floor keeps a section emptied with its rules red.
MINIMUM_SHELL_RULES = 10


def _shell_rules(css: str) -> list[tuple[list[str], str, str | None]]:
    start = css.index(SHELL_SECTION)
    end = css.index("/* ---", start + len(SHELL_SECTION))
    return _css_rules(css[start:end])


def assert_every_shell_rule_is_scoped_to_the_review_page(css: str) -> None:
    rules = _shell_rules(css)
    assert len(rules) >= MINIMUM_SHELL_RULES, f"the layout shell has {len(rules)} rules"
    for selectors, _body, _media in rules:
        for selector in selectors:
            assert selector.startswith(".review-page "), (
                f"{selector!r} is not under .review-page, so it reaches the faces page (U20)"
            )


def test_every_layout_shell_rule_is_scoped_to_the_review_page():
    """F-1-5 (M2761): U20 gives the faces page the tokens and nothing else, and the faces page
    shares this stylesheet and the shell's ids."""
    assert_every_shell_rule_is_scoped_to_the_review_page(
        (STATIC_DIR / STYLESHEET).read_text(encoding="utf-8")
    )


def test_the_scope_check_refuses_an_unscoped_shell_rule():
    rules = "".join(f".review-page .r{n} {{ margin: 0; }}" for n in range(MINIMUM_SHELL_RULES))
    scoped = f"{SHELL_SECTION} */ {rules} /* --- next */"
    assert_every_shell_rule_is_scoped_to_the_review_page(scoped)
    unscoped = scoped.replace(".review-page .r3", ".review-page .r3, button")
    with pytest.raises(AssertionError):
        assert_every_shell_rule_is_scoped_to_the_review_page(unscoped)


def test_every_review_control_meets_the_44px_touch_target():
    """F-1-6 (M2762): UX 7 rev 16 - touch targets at least 44px. The size is the token's
    (pinned above); this pins that the review page's controls USE it."""
    rules = _shell_rules((STATIC_DIR / STYLESHEET).read_text(encoding="utf-8"))
    sized = {
        selector
        for selectors, body, media in rules
        if media is None and "min-height: var(--touch)" in body
        for selector in selectors
    }
    for control in (".review-page button", ".review-page select", ".review-page input"):
        assert control in sized, f"{control} has no min-height: var(--touch)"


# The type each kind of file is served as, spelled by extension - never read from
# `STATIC_FILES`, whose served-header test compares the header with `STATIC_FILES` itself.
SERVED_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def test_every_static_file_is_served_with_the_type_its_extension_names():
    """F-1-7 (M2765)."""
    assert len(STATIC_FILES) >= MINIMUM_STATIC_FILES
    for name, served in STATIC_FILES.items():
        assert served == SERVED_TYPES[Path(name).suffix], f"{name} is served as {served}"


# M1670's lesson, applied to this module in the commit that widens it (M10's C4): an
# instrument's own coverage is owned by nobody unless somebody writes it down. Spelled by
# hand, never derived from `globals()`, which would agree with any deletion.
LOAD_BEARING_CONTROLS = {
    "test_the_required_static_file_list_is_not_empty": (
        "the floor under REQUIRED_STATIC_FILES - an empty parametrize SKIPS (M743)"
    ),
    "test_the_discovery_finds_a_real_inventory_rather_than_nothing": (
        "the floor under the discovery every parametrized check here is built from (M528)"
    ),
    "test_the_installed_package_offers_the_same_static_files_the_filesystem_does": (
        "the installed-package side; the walk alone cannot see what the product reads"
    ),
    "test_every_required_static_file_is_present_and_routable": (
        "the literal-vs-STATIC_FILES pairing - the half that is not its own oracle"
    ),
    "test_the_labeling_page_ships_both_of_its_own_assets": (
        "UX 6's two files, asserted outside any parametrize list"
    ),
    "test_every_discovered_page_loads_only_files_the_server_will_serve": (
        "M784: a mistyped script src was invisible to all 194 webui tests"
    ),
    "test_every_page_pulls_in_exactly_the_assets_its_spec_gives_it": (
        "the spec-side expectation; the servable loop alone permits a page loading less"
    ),
    "test_the_spec_side_reference_table_covers_every_page_that_ships": (
        "stops a new page escaping the spec-side table above"
    ),
    "test_the_reference_reader_ignores_external_and_non_file_targets": (
        "the negative control under _local_references; without it the loops prove nothing"
    ),
    "test_every_shipped_script_parses_as_javascript": (
        "M783: one unclosed brace left 1862 tests green over an unparseable page"
    ),
    # M14 Task 1: the review page became five classic scripts whose ORDER is load-bearing.
    "test_the_review_script_order_is_spelled_and_names_only_required_files": (
        "the floor under the spelled order; an emptied list would pass the order check"
    ),
    "test_the_review_page_loads_its_scripts_in_the_spec_order_and_none_inline": (
        "the real parser's read of the order - the sorted reference check cannot see a swap"
    ),
    "test_the_script_parser_sees_a_swap_inline_code_and_a_commented_tag": (
        "the negative control under the html.parser read"
    ),
    "test_the_load_order_harness_ships_and_names_every_check": (
        "the half of the harness check that never skips"
    ),
    "test_the_load_order_harness_executes_exactly_the_checks_it_claims": (
        "the only check that runs the five scripts in one scope, in the page's order"
    ),
    "test_the_contrast_pair_list_is_not_empty_and_covers_both_themes": (
        "the floor under the parametrized contrast check (M743)"
    ),
    "test_the_contrast_formula_agrees_with_wcag_reference_values": (
        "the formula's own control; a formula returning a large number passes every pair"
    ),
    "test_every_text_and_background_pair_clears_ux_7s_contrast_floor": (
        "UX 7's 4.5:1, computed from the token values in both themes, never from comments"
    ),
    # M14 Task 1 review: F-1-3 .. F-1-7.
    "test_every_token_ux_7_rev_16_spells_has_its_value_in_both_themes": (
        "M2746: a spec-spelled token one unit off its value was green"
    ),
    "test_the_review_page_has_no_phone_notice_and_the_faces_page_keeps_its_swap": (
        "M2759/M2760 (the notice deleted was green), rewritten at M14 Task 2 Step 6b: U4"
        " retires the review page's notice, U20 keeps the faces page's"
    ),
    "test_the_no_notice_check_refuses_a_review_page_that_is_still_swapped_out": (
        "the negative control under the phone-notice check, rewritten with it"
    ),
    "test_the_review_pages_body_opts_out_of_the_swap_and_the_faces_pages_does_not": (
        "M2870 (Task 2 review R2): the review page's <body> losing .review-page was green"
    ),
    "test_the_body_scope_check_refuses_a_page_pair_the_rule_would_get_wrong": (
        "the negative control under the <body> class check"
    ),
    "test_the_no_notice_check_accepts_the_faces_only_swap": (
        "M14 Task 2 Step 6b: the positive control, so the refusals are not refusing everything"
    ),
    "test_every_layout_shell_rule_is_scoped_to_the_review_page": (
        "M2761: a shell rule reaching the faces page (U20) was green"
    ),
    "test_the_scope_check_refuses_an_unscoped_shell_rule": (
        "the negative control under the U20 scope check"
    ),
    "test_every_review_control_meets_the_44px_touch_target": (
        "M2762: the 44px touch rule emptied was green"
    ),
    "test_every_static_file_is_served_with_the_type_its_extension_names": (
        "M2765: a script served as text/plain was green"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own inventory (M1670's precedent).

    Removing one of these is a deliberate edit to `LOAD_BEARING_CONTROLS`, with the reason
    it was kept for written beside it - not a quiet deletion during a refactor of the thing
    that framed it. **When this file gains a control, its registration lands in the same
    commit** (CLAUDE.md, M10 close).
    """
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}: {missing}."
        f" Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )

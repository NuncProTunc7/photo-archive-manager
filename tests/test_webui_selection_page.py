"""`tests/js/selection_test.js` executed, and the floor that keeps it honest (M14 Task 3 slice 3c).

The review list's selection is two classic scripts nothing else in this suite runs:
`pam-selection.js` (U17's `{ids, groups, all, except_ids}` as data - pure) and `pam-bar.js` (W13's
bar: the server's preview, `Clear`, `Skip`, `Approve`, and U3's two sentences). The node driver
executes them in the page's shared scope over a hand-written DOM and a stub server; this module
runs it and reads the names it prints, so a known set must actually EXECUTE, not merely exit 0.

THE SHAPE IS `tests/test_webui_list_page.py`'s, including its two halves: `node` is an external
tool on the exiftool precedent, so the node half skips where it is absent - and the stdlib half
NEVER skips, so a deleted driver, a deleted sentence or a deleted control is red on either machine
(CLAUDE.md, M783/M784).

U3's SENTENCES ARE SPELLED HERE FROM THE SPEC (U3, W14) and the 409's from the controller's Task 3
ruling - never read back out of `pam-bar.js`. The bar's three buttons are spelled from W13.

THE NEGATIVE CONTROLS ARE RUN: T3 mutation 6 (a group's box enumerating the loaded ids instead of
sending the group key) is planted in a COPY of `pam-selection.js` through `PAM_SELECTION_JS`, and
`Approve` sending while a preview is in flight in a copy of `pam-bar.js` through `PAM_BAR_JS`; the
driver must go red on the check that exists for each. Nothing on disk is touched.
"""

import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "selection_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
SELECTION_JS = STATIC_DIR / "pam-selection.js"
BAR_JS = STATIC_DIR / "pam-bar.js"
INDEX = STATIC_DIR / "index.html"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from Steps 1 and 6 of the Task 3 brief.
REQUIRED_CHECKS = (
    # Step 1, the model, U17
    "a row's square sends its id, and ticking it again takes it out",
    # T3 mutation 6
    "a group's box sends the group key, so its unloaded rows are included",
    "a row unticked inside a ticked group is an exception, and ticking it again takes it back",
    "Select all sends all, an unticked row is an exception, and Select all again empties it",
    "unticking one group under Select all keeps every other group by its key",
    "rowState and groupState read none, some and all",
    "Shift-click adds the loaded rows between two rows as ids, either way round",
    "toWire sends U17's four view fields and the date tree's group, and leaves out the empty ones",
    "a view change keeps the ticked ids and drops the groups, Select all and exceptions",
    # Step 6, the bar, W13/W14
    "the bar shows only while something is selected, and N is the preview's widened items",
    "the low line reads U3's plural and singular from the preview's low",
    "Approve is disabled while a preview is in flight, and sends the low count it showed",
    "after Approve the page says U3's sentence from the response's low_included",
    "a 409 asks for the preview again and says the selection changed",
    "the request wrapper keeps a refusal's body, so a 409 can carry the fresh preview",
    "Skip sends the selection with no low fields, and every bulk write is decide-selection",
    "a row's square, a group's box, Select all and Space drive the selection",
    "on a stopped page the bar selects nothing and writes nothing",
    # Task 3 review R2's closures (F1, F2): the model's rulings and the preview's races
    "Select all on drops earlier exceptions, Select all off drops ticked ids, and a box off leaves none of its rows",
    "a preview that answers after a newer change is dropped, so the bar and Approve follow the newest",
    "a refused preview shows its message and leaves Approve nothing to send",
    "two quick changes ask for one preview, of the second",
    "a preview that arrives on a stopped page leaves Approve disabled",
    "a refusal with no fresh preview says its own message, not the selection changed",
    # Task 3 review R2 F4 (the controller's fix): one bulk write at a time
    "a second Approve or Skip while a write is in flight sends nothing, and the next one after it settles does",
)

# U3's sentences (W14) as the page must carry them verbatim: the singular disclosure, the
# singular confirmation's tail, and the 409's sentence. The plurals carry a number in their
# middle; the node half checks them whole.
BAR_SENTENCES = (
    "Includes 1 name that needs review. Approve will approve it too.",
    " names that need review. Approve will approve them too.",
    ", including 1 name that needs review. Every rename can be undone later.",
    " names that need review. Every rename can be undone later.",
    "The selection changed. Check the count and try again.",
)

# W13: `N selected [Clear] [Skip] [Approve]`, in that order.
W13_BUTTONS = ("Clear", "Skip", "Approve")

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(REQUIRED_CHECKS) >= 25, "the required check list is empty or cut"
assert len(BAR_SENTENCES) >= 5, "the bar sentence list is empty or cut"
assert len(W13_BUTTONS) == 3, "W13 names three buttons"

# T3 mutation 6's defect, as the mutation would apply it.
GROUP_KEY = "    selAdd(SELECTION.groups, group.key);"
LOADED_IDS = "    (group.ids || []).forEach(function (id) { selAdd(SELECTION.ids, id); });"

# Step 6's defect: Approve no longer waits for the preview in flight.
APPROVE_WAITS = "  if (PAGE.stopped || BAR.pending || BAR.preview === null) {"
APPROVE_RUSHES = "  if (PAGE.stopped || BAR.preview === null) {"


class _Bar(HTMLParser):
    """The button labels inside `#selection-bar`, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.in_button = False
        self.labels: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += tag != "input"
            if tag == "button":
                self.in_button = True
                self.labels.append("")
        elif dict(attrs).get("id") == "selection-bar":
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1
            if tag == "button":
                self.in_button = False

    def handle_data(self, data: str) -> None:
        if self.in_button:
            self.labels[-1] += data.strip()


def _bar_labels(html: str) -> list[str]:
    parser = _Bar()
    parser.feed(html)
    return parser.labels


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_node_driver_ships_and_names_every_required_check():
    assert NODE_DRIVER.is_file()
    text = NODE_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in text, check


@pytest.mark.parametrize("sentence", BAR_SENTENCES)
def test_the_bar_carries_u3s_sentences_verbatim(sentence):
    assert f'"{sentence}"' in BAR_JS.read_text(encoding="utf-8"), sentence


def test_the_bar_is_w13s_three_buttons_in_order_and_starts_hidden():
    html = INDEX.read_text(encoding="utf-8")
    assert _bar_labels(html) == list(W13_BUTTONS)
    assert '<div id="selection-bar" class="selection-bar" hidden>' in html


def test_the_bar_reader_sees_a_missing_and_a_reordered_button():
    """The reader's own control: a bar it cannot tell apart from W13 is not a check."""
    wrong = (
        '<div id="selection-bar"><button>Approve</button><button>Skip</button></div>'
        "<button>Clear</button>"
    )
    assert _bar_labels(wrong) == ["Approve", "Skip"]


def test_the_lines_the_negative_controls_plant_against_are_there_once():
    """If either line is reworded, the controls below stop planting anything."""
    assert SELECTION_JS.read_text(encoding="utf-8").count(GROUP_KEY) == 1
    assert BAR_JS.read_text(encoding="utf-8").count(APPROVE_WAITS) == 1


# --- the node half: skips where node is absent, which is why the half above exists ---------


def _run(env=None):
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied args
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_executes_every_check_it_claims():
    run = _run()
    assert run.returncode == 0, run.stdout + run.stderr
    ran = {line[3:] for line in run.stdout.splitlines() if line.startswith("ok ")}
    assert ran == set(REQUIRED_CHECKS), (
        sorted(set(REQUIRED_CHECKS) - ran),
        sorted(ran - set(REQUIRED_CHECKS)),
    )
    assert f"ran {len(REQUIRED_CHECKS)}" in run.stdout.splitlines()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_groups_box_enumerates_its_loaded_rows(tmp_path):
    """THE NEGATIVE CONTROL, RUN (T3 mutation 6): planted in a copy, red on its check."""
    planted = tmp_path / "pam-selection.js"
    source = SELECTION_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(GROUP_KEY, LOADED_IDS), encoding="utf-8")
    run = _run({**os.environ, "PAM_SELECTION_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert (
        "FAIL a group's box sends the group key, so its unloaded rows are included"
        in run.stdout
    )
    assert "ok a row's square sends its id, and ticking it again takes it out" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_approve_sends_while_a_preview_is_in_flight(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Step 6): the in-flight wait removed in a copy of `pam-bar.js`."""
    planted = tmp_path / "pam-bar.js"
    source = BAR_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(APPROVE_WAITS, APPROVE_RUSHES), encoding="utf-8")
    run = _run({**os.environ, "PAM_BAR_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert (
        "FAIL Approve is disabled while a preview is in flight, and sends the low count it showed"
        in run.stdout
    )
    assert "ok the low line reads U3's plural and singular from the preview's low" in run.stdout

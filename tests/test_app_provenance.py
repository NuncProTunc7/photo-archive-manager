"""`tests/js/app_provenance_test.js` executed, and the floor that keeps it honest.

WHAT THIS CLOSES. M11 Task 12's review measured (M1938) that the review page's provenance
guard - the line that stops a caption model being named as the source of a person's name,
spec 8.2 rev 22 - was held by exactly one regex over `app.js`'s source text, and then
measured what that is worth (M1939): breaking the guard AND weakening the regex in the same
run left **3620 tests green**. A grep cannot survive an edit to the text it greps for.
`sourceWords` is a pure function, so a real `node` can execute it, which is what
`tests/js/faces_state_test.js` already does for `faces.js`.

THE SHAPE IS `tests/test_faces_page.py`'s, deliberately, including its two halves. `node`
is an external tool on the exiftool precedent and a machine may not have it, so the Node
half skips where it is absent - and a control that disarms silently on the machine without
the tool is the failure mode, not the missing tool (CLAUDE.md, M783/M784). So the stdlib
half below NEVER skips: it reads the driver and `app.js` with Python and fails on either
machine.

THE NEGATIVE CONTROL IS RUN, not assumed. `test_the_node_driver_fails_when_the_page_reads_a_model_for_a_person`
plants M1938's defect in a COPY of `pam-trust.js` (`app.js` until M14 Task 1) in a temporary
directory, points the driver at it through `PAM_TRUST_JS`, and requires the run to fail on the named test. Nothing on disk is
touched.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "app_provenance_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
APP_JS = STATIC_DIR / "app.js"
# M14 Task 1: `sourceWords`, its guard and the CommonJS seam the driver reaches through moved,
# unchanged, from `app.js` into `pam-trust.js`. `app.js` is the boot and keeps the page
# bootstrap's guard, which the browser test below still reads there.
TRUST_JS = STATIC_DIR / "pam-trust.js"

# THE CHECKS THE DRIVER MUST ACTUALLY EXECUTE, spelled from spec 8.2 rev 22, UX spec 5 and
# A14 rather than read back out of the driver - an oracle built from the definition it
# checks is a survivor waiting to happen (`repo.ENRICHERS`). The driver prints `ok <name>`
# per check, so this asserts that a known set RAN: a driver whose tests had all been
# deleted would exit 0 too.
REQUIRED_CHECKS = (
    # spec 8.2 rev 22 - "a `{people}` token gets NO entry", on the built collision
    "a person's chip never carries a model, even when a fact shares the id",
    # the separation: the same card still names the model for the fact that owns it
    "the same card still names the model for the AI token beside it",
    # UX spec 5's words, and A14's source_table
    "a person's chip reads UX 5's words, not a catalog table name",
    # the fall-through branch, which no served card reaches today
    "a token whose source table pam does not know still renders",
)

# The seam the driver reaches through. An export dropped from `app.js` would make the Node
# half fail where node exists and VANISH where it does not, so it is pinned here too.
REQUIRED_EXPORTS = ("SOURCE_WORDS", "sourceWords")

# M1938's defect, as the mutation applied it: the guard that reads a model for
# `inferred_facts` tokens only, and the unguarded lookup it replaces.
GUARDED = 'var model = table === "inferred_facts" ? models[token.source_id] : null;'
UNGUARDED = "var model = models[token.source_id];"


# --- the stdlib half: never skips ----------------------------------------------------


def test_the_required_check_list_is_not_empty():
    """A parametrized control built from an empty list is SKIPPED by pytest, not failed
    (M743), so the list that gives the two tests below their meaning is pinned non-empty
    here rather than trusted."""
    assert len(REQUIRED_CHECKS) >= 4
    assert REQUIRED_EXPORTS


def test_the_node_driver_ships_and_is_not_empty():
    """Without this, every check that needs node would simply skip on a machine without
    it - including on a tree where somebody deleted the driver."""
    assert NODE_DRIVER.is_file()
    assert len(NODE_DRIVER.read_text(encoding="utf-8")) > 2000


@pytest.mark.parametrize("check", REQUIRED_CHECKS)
def test_the_driver_names_every_check_this_finding_owes(check):
    assert check in NODE_DRIVER.read_text(encoding="utf-8")


@pytest.mark.parametrize("export", REQUIRED_EXPORTS)
def test_app_js_still_exports_the_seam_the_driver_reaches_through(export):
    assert re.search(rf"\b{export}: {export}\s*[,}}]", TRUST_JS.read_text(encoding="utf-8"))


def test_the_browser_never_reaches_the_commonjs_seam():
    """The export block is guarded and the page bootstrap is its ELSE, so a browser - where
    `module` is undefined - starts the page exactly as before, and a node run starts
    nothing. Red on every machine, not only on one that has node."""
    source = APP_JS.read_text(encoding="utf-8")
    assert 'if (typeof module !== "undefined" && module.exports) {' in source
    assert re.search(r"} else \{\n  start\(\);\n}", source), (
        "app.js runs its page bootstrap outside the module guard, so requiring it under "
        "node would start the page"
    )


def test_the_guard_this_file_protects_is_the_one_in_the_page():
    """The defect the negative control plants is M1938's, and this keeps the two spellings
    from drifting apart: if `app.js`'s line is reworded, the control below stops planting
    anything and would pass over a page nobody checked."""
    assert GUARDED in TRUST_JS.read_text(encoding="utf-8")


# --- the node half: skips where node is absent, and that is why the half above exists --


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_executes_every_check_it_claims():
    """The actual execution. `node`, no network, no dependency - the exiftool precedent."""
    run = subprocess.run(
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    ran = {line[3:] for line in run.stdout.splitlines() if line.startswith("ok ")}
    assert set(REQUIRED_CHECKS) <= ran, sorted(set(REQUIRED_CHECKS) - ran)
    assert f"ran {len(ran)}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_page_reads_a_model_for_a_person(tmp_path):
    """THE NEGATIVE CONTROL, RUN. M1938 planted in a COPY: the driver must go red on the
    check that exists for it, and must not merely error on something else."""
    planted = tmp_path / "pam-trust.js"
    source = TRUST_JS.read_text(encoding="utf-8")
    assert source.count(GUARDED) == 1
    planted.write_text(source.replace(GUARDED, UNGUARDED), encoding="utf-8")
    run = subprocess.run(
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PAM_TRUST_JS": str(planted)},
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "FAIL a person's chip never carries a model" in run.stdout
    assert "ok the same card still names the model for the AI token beside it" in run.stdout

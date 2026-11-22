"""`tools/count_executable_lines.py` - the ~300-line budget's instrument (M8 review C6).

WHY THIS FILE EXISTS. The budget is normative (design spec section 0, rev 10) and had no
counter in the repository. M4, M5, M6, M7 and M8 each wrote a throwaway one in a review
session and calibrated it against the numbers the PREVIOUS milestone published. The
published numbers were the definition, and five scripts were the implementation.

THE PIN, AND WHY IT IS THE NUMBERS AND NOT A SECOND CALL. `v0.1-m7` published 226 / 244 /
279 for `planner.py` / `api.py` / `write_api.py` (`docs/implementation-notes/m7.md`, and
the M8 review re-measured them). Those three integers are written out below as literals.
A test asserting that the counter agrees with the counter would be vacuous - this
project's standing lesson about oracles built from the thing they check - so the numbers
come from the notes and the content comes from the tag.

WHY THE TAGGED CONTENT IS COMMITTED RATHER THAN READ OUT OF GIT AT TEST TIME. `git show
v0.1-m7:<path>` needs a repository with that tag in it, and `actions/checkout@v4` fetches
neither tags nor history by default - so the load-bearing check would SKIP on both CI
lanes and pass locally, which is the failure mode this project keeps meeting (the gate
lane's race, the YAML that never parsed, `node --check` on a machine without node). The
three files are therefore committed under `tests/fixtures/v0.1-m7/` as `.txt`, the pin
below needs nothing but the standard library and CANNOT skip, and the git comparison is
the addition on top rather than the whole thing - the same split, oriented the same way,
as `tests/test_static_syntax.py`.

The fixtures are immutable historical content. Regenerate or re-verify with:
`git show v0.1-m7:pam/webui/api.py | diff - tests/fixtures/v0.1-m7/api.py.txt`.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import count_executable_lines as counter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v0.1-m7"

# The published numbers, transcribed from `docs/implementation-notes/m7.md` and the M8
# architecture review, against the path each one was published for. Nothing here is
# computed: these are four milestones of record, and the instrument answers to them.
PUBLISHED_V0_1_M7 = {
    "pam/naming/planner.py": 226,
    "pam/webui/api.py": 244,
    "pam/webui/write_api.py": 279,
}

# Lesson 3 of M9's Tasks 1-4: a collection-time assert catches an EMPTIED parametrize and
# only the exact length catches a NARROWED one. Both, and the length rather than mere
# non-emptiness, because two of these three could be dropped and the remaining row would
# still read as a green calibration.
assert len(PUBLISHED_V0_1_M7) == 3, PUBLISHED_V0_1_M7

# M9 Task 5 review / FINDING 4. The sha256 of each tagged file's content, newline-
# normalized, taken from `git show v0.1-m7:<path>` on 2026-08-27 with the git
# cross-check below passing. It exists because that cross-check SKIPS wherever the tag
# is unreachable - which is both CI lanes - and a fixture that drifts in a way the
# counts cannot see (a comment, a docstring, a blank line) is then invisible: measured,
# a deleted comment line plus an unreachable tag was `2289 passed, 2 skipped`.
#
# CLAUDE.md's rule from the M7 close, applied literally: when a guard's value depends on
# an external tool, pair it with one that CANNOT skip. This is that pair. It pins the
# bytes; the git comparison below still pins their PROVENANCE wherever it can run, and
# the two are not redundant - this one could only ever say the fixtures have not moved
# since today, and that one says which commit they came from.
FIXTURE_SHA256_V0_1_M7 = {
    "pam/naming/planner.py": "4727d7d897b0691b8b839afad647e730543dd751259452e6e9afa1ef58770d67",
    "pam/webui/api.py": "6abe407c7e33549ebaec793624b6f20182496f9c5003b55fdfd4951eb48366b9",
    "pam/webui/write_api.py": "0b4742ddb02075e6cf2db782fedaf3670ba745977aef711d969bce91851d5616",
}

assert sorted(FIXTURE_SHA256_V0_1_M7) == sorted(PUBLISHED_V0_1_M7), FIXTURE_SHA256_V0_1_M7


def fixture_for(source_path: str) -> Path:
    return FIXTURES / (Path(source_path).name + ".txt")


@pytest.mark.parametrize(("source_path", "published"), sorted(PUBLISHED_V0_1_M7.items()))
def test_the_counter_reproduces_the_numbers_v0_1_m7_published(source_path, published):
    """The calibration. If this fails, the instrument moved - not four milestones of notes.

    Three files rather than one: they differ in the ratio of prose to code (`planner.py`
    is the most heavily commented module in `pam/`, `write_api.py` the least), so a
    counter that mishandled docstrings would land on at most one of them by luck.
    """
    assert counter.count_file(fixture_for(source_path)) == published


@pytest.mark.parametrize(("source_path", "digest"), sorted(FIXTURE_SHA256_V0_1_M7.items()))
def test_the_committed_fixtures_still_hold_the_bytes_they_were_committed_with(
    source_path, digest
):
    """The half that CANNOT skip: needs no git, no tag, no network, no external tool.

    Newline-normalized before hashing, for the same reason the git comparison below
    compares `splitlines()`: `.gitattributes` pins end-of-line conversion only under
    `tests/tiny_library/`, so these `.txt` files may land as CRLF on one machine and LF
    on another and are the same file either way.
    """
    content = fixture_for(source_path).read_text(encoding="utf-8")
    normalized = "\n".join(content.splitlines()).encode("utf-8")
    assert hashlib.sha256(normalized).hexdigest() == digest, source_path


def test_the_committed_fixtures_really_are_the_tagged_files():
    """The addition on top: that the content above is v0.1-m7's and not a copy that drifted.

    SKIPS where the tag is unreachable - a shallow CI checkout, a tarball, a machine with
    no git - and that is deliberate rather than sloppy: the calibration itself does not
    depend on this, so a skip here removes a cross-check and disarms nothing. Compared as
    TEXT with newlines normalized, because `.gitattributes` pins end-of-line conversion
    only under `tests/tiny_library/` and the counter does not care either way.
    """
    for source_path in PUBLISHED_V0_1_M7:
        try:
            blob = subprocess.run(
                ["git", "show", f"v0.1-m7:{source_path}"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            pytest.skip(f"v0.1-m7:{source_path} is not reachable from here: {exc}")
        committed = fixture_for(source_path).read_text(encoding="utf-8")
        assert committed.splitlines() == blob.splitlines(), source_path


# --- the definition itself, spelled out by hand ------------------------------------

# Every clause of the definition, once each, with the physical line numbers written in.
# The expected total below is counted BY HAND from those numbers; it is the independent
# second reading that keeps the calibration above from being three magic integers.
#
#   1-4   module docstring, spanning four lines        excluded
#   5     blank                                        excluded
#   6     a whole-line comment                         excluded
#   7     code with a trailing comment                 COUNTS
#   8     blank                                        excluded
#   9-11  a multi-line string that is NOT a docstring  COUNTS (all three)
#   12-13 blank                                        excluded
#   14-15 a def split across two physical lines        COUNTS (both)
#   16    a function docstring                         excluded
#   17    a comment inside a body                      excluded
#   18    a '#' inside a string literal                COUNTS
#   19-22 a return split over four lines, incl. `)`    COUNTS (all four)
#   23-24 blank                                        excluded
#   25-26 a def and its first statement                COUNTS (both)
#   27    a bare string that is NOT body[0]            COUNTS
#   28    a return                                     COUNTS
SAMPLE = '''\
"""Module docstring line one.

Still the docstring.
"""

# A comment line.
import os  # a trailing comment; the line still carries code

SQL = """
SELECT 1
"""


def f(a,
      b):
    """A docstring on its own line."""
    # comment
    text = "# not a comment"
    return (
        a
        + b
    )


def g():
    x = 1
    """Not a docstring: it is the second statement, so it is a value expression."""
    return x
'''

# 7, 9, 10, 11, 14, 15, 18, 19, 20, 21, 22, 25, 26, 27, 28
SAMPLE_EXPECTED = 15


def test_the_definition_holds_on_a_sample_built_clause_by_clause():
    assert counter.count_executable_lines(SAMPLE) == SAMPLE_EXPECTED


def test_the_sample_really_contains_every_clause_it_claims_to():
    """A sample that lost its awkward cases would still pass the count above by accident."""
    lines = SAMPLE.splitlines()
    assert len(lines) == 28, len(lines)
    assert lines[5] == "# A comment line."
    assert lines[6].endswith("# a trailing comment; the line still carries code")
    assert lines[8] == 'SQL = """'
    assert lines[15].strip().startswith('"""A docstring')
    assert lines[17].strip() == 'text = "# not a comment"'
    assert lines[21].strip() == ")"
    assert lines[26].strip().startswith('"""Not a docstring')


def test_a_docstring_only_module_counts_as_nothing_at_all():
    """The clause the calibration cannot exercise: prose alone is not executable code."""
    assert counter.count_executable_lines('"""Only prose.\n\nTwo paragraphs of it.\n"""\n') == 0


def test_line_endings_do_not_move_the_count():
    """A fixture checked out under `core.autocrlf` must not read as a different file."""
    assert counter.count_executable_lines(SAMPLE.replace("\n", "\r\n")) == SAMPLE_EXPECTED


# --- the report ---------------------------------------------------------------------


def test_the_budget_is_the_number_the_spec_states():
    """Design spec section 0: 'files stay under ~300 lines of executable code'."""
    assert counter.BUDGET == 300


def test_no_file_in_pam_is_over_the_budget():
    """THE BUDGET APPLIED TO `pam/`, which until M10 Task 2 NOTHING IN THE PROJECT DID.

    THE FINDING THIS CLOSES (M10 Task 2 review, R2; mutation M1453). Every other test in
    this module checks the INSTRUMENT - its arithmetic, its boundary, its exit code, its
    agreement with four milestones of published numbers - and not one of them ever ran it
    over the package it was built for. Padding `pam/webui/write_state.py` to 335
    executable lines left **all 2653 tests green**; `tools/count_executable_lines.py` was
    run by no test, by no pre-commit hook and by neither CI lane. The counter was created
    as M8 review condition C6 and given no consumer.

    THAT IS NOT A HYPOTHETICAL, IT IS THE HISTORY OF THIS FILE'S SUBJECT. `write_api.py`
    reached **325** executable lines and shipped a whole milestone that way; F11 was found
    by a human reading the code at the M9 review, and Task 2 is the split that repaired it.
    An instrument with no consumer is the M781/M783 shape - a control that silently stops
    covering the thing it names - and this project has now paid for it three times.

    THE ASSERTION NAMES THE OFFENDERS RATHER THAN COUNTING THEM, so a failure reads as
    "`foo.py` is at 312" and not as "expected 0, got 1". `> BUDGET`, never `>=`: design
    spec section 0 says files stay UNDER ~300, and a file of exactly 300 is not over it -
    the same boundary the CLI test above pins from the other side.

    ON THE `~` IN "~300". The budget is approximate BY DESIGN, and this test does not
    pretend otherwise: it is a tripwire, not a linter. A file that genuinely earns 301
    lines is a conversation with the owner and an entry in the milestone notes - which is
    exactly what F11 was - and the right response to this test going red is to split the
    file or to record a decision, never to raise `BUDGET` so the red goes away.
    """
    over = {
        path.relative_to(ROOT).as_posix(): counter.count_file(path)
        for path in sorted((ROOT / "pam").rglob("*.py"))
        if counter.count_file(path)
        > RULED_OVER_BUDGET.get(path.relative_to(ROOT).as_posix(), counter.BUDGET)
    }
    assert over == {}, f"over the ~{counter.BUDGET}-line budget: {over}"


# THE DOCSTRING ABOVE'S "RECORD A DECISION", recorded. Each entry is a file the OWNER has
# ruled may sit over the budget, pinned at its MEASURED count - a CEILING, not a raised
# budget, so one more executable line is red again and the file cannot drift further on
# the strength of a ruling about where it stood.
#
# `faces.py`: M11 Task 12 adds `people_by_sha256` (the composed `{people}` read) and the
# owner ruled on 2026-09-17 that it crossing 300 is KNOWN AND ACCEPTED, for the M11 close to
# rule on. The alternative module would be a new entry in `guards.REPO_OWN_TABLES` for two
# tables `faces.py` owns. The close deletes this entry or keeps it; nothing else may.
RULED_OVER_BUDGET = {"pam/catalog/repo/faces.py": 310}


def test_every_ruled_over_budget_file_is_really_over_it():
    # A stale entry is a silent licence: once the close splits the file, the ceiling must
    # go with it, or the next growth past 300 would be green up to the old number.
    #
    # THE CEILING IS PINNED AT THE MEASUREMENT, NOT ABOVE IT (M11 Task 12's review, M1941).
    # `<= ceiling` was green when the number was raised 310 -> 400, which turns "the owner
    # ruled on a file at 310 lines" into a 400-line budget nobody granted: the ruling's
    # whole force IS the number. Equality also makes a SHRINKING file red, which is right -
    # a file that comes back under the budget should lose its entry, not keep a licence.
    for rel_path, ceiling in RULED_OVER_BUDGET.items():
        measured = counter.count_file(ROOT / rel_path)
        assert counter.BUDGET < measured, f"{rel_path} is no longer over the budget"
        assert measured == ceiling, (
            f"{rel_path} is {measured} executable lines and its ruled ceiling is {ceiling}."
            " The owner ruled on the file AT that count: re-measure, and change the number"
            " only with the reason in the commit message."
        )


def test_the_report_flags_a_file_over_the_budget_and_says_so_in_its_exit_code(tmp_path, capsys):
    """The CLI half. An instrument whose verdict nobody can read is a number generator."""
    under = tmp_path / "under.py"
    under.write_text("x = 1\n", encoding="utf-8")
    over = tmp_path / "over.py"
    over.write_text(
        "".join(f"x{n} = {n}\n" for n in range(counter.BUDGET + 1)), encoding="utf-8"
    )

    assert counter.main([str(under)]) == 0
    assert "over the ~300 budget" not in capsys.readouterr().out

    # M9 Task 5 review / FINDING 3: the BOUNDARY. Design spec section 0 says files stay
    # *under* ~300, so a file of exactly BUDGET lines is not over it - and `under.py`
    # (1 line) and `over.py` (301) both sit far enough away that `count > BUDGET` could
    # be `>=` with the whole suite green. An instrument that reports a compliant file as
    # a breach is the same defect as one that misses a breach, in the other direction.
    at_the_budget = tmp_path / "at_the_budget.py"
    at_the_budget.write_text(
        "".join(f"x{n} = {n}\n" for n in range(counter.BUDGET)), encoding="utf-8"
    )
    assert counter.count_file(at_the_budget) == counter.BUDGET
    assert counter.main([str(at_the_budget)]) == 0
    assert "over the ~300 budget" not in capsys.readouterr().out

    assert counter.main([str(over)]) == 1
    printed = capsys.readouterr().out
    assert "over the ~300 budget" in printed
    assert f"{counter.BUDGET + 1:,}" in printed
    assert "1 over 300." in printed

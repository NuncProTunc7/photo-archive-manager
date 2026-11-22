"""Fault-injection switch (design spec section 9.3 rev 7).

`PAM_FAULT` and `faults._counts` are process-global; every test here goes through
the `set_fault` fixture in conftest, which is what resets both around each test and
what the conftest tripwire checks.
"""

import sqlite3

import pytest

from pam import faults


def test_checkpoints_are_exactly_the_ten_the_spec_names():
    """Spec 9.3 rev 8 lists seven rename checkpoints; rev 35 (E12, M15 Task 9) adds three
    MIGRATION checkpoints around the compatibility ledger's reservation. The names are the
    wire format between the fault scenarios and the shipped binary, so a typo or a rename
    here silently turns a scenario into a no-op that still reports 'passed'.

    Renamed from `..._the_six_...` when rev 8 added `mid_unit_revert`, and from
    `..._the_seven_...` when rev 35 added the migration three. The rename is deliberate and
    is the record that the wire format changed: this test failing on an addition is the
    test working, not collateral damage. BOTH TUPLES ARE SPELLED: the fault matrix's rows
    are the rename tuple, so a checkpoint moved between them would shrink the matrix.
    """
    assert faults.RENAME_CHECKPOINTS == (
        "before_intent_commit",
        "after_intent",
        "after_rename",
        "before_done_commit",
        "mid_companion_unit",
        "mid_unit_revert",
        "before_plan_finalize",
    )
    assert faults.MIGRATION_CHECKPOINTS == (
        "before_ledger_reservation",
        "after_ledger_reservation",
        "after_migration_commit",
    )
    assert faults.CHECKPOINTS == faults.RENAME_CHECKPOINTS + faults.MIGRATION_CHECKPOINTS
    assert faults.MODES == ("crash", "dbfail")


def test_unset_pam_fault_is_a_no_op_at_every_checkpoint():
    for name in faults.CHECKPOINTS:
        for _ in range(3):
            assert faults.maybe_fault(name) is None


def test_crash_fires_on_the_nth_hit_only(set_fault):
    set_fault("crash:after_intent:3")

    assert faults.maybe_fault("after_intent") is None
    assert faults.maybe_fault("after_intent") is None
    with pytest.raises(SystemExit) as excinfo:
        faults.maybe_fault("after_intent")
    assert "after_intent" in str(excinfo.value)

    # And only that hit: a fourth call must not fire again, or a retry loop in apply
    # would look like a second independent crash.
    assert faults.maybe_fault("after_intent") is None


def test_other_checkpoints_do_not_advance_the_armed_counter(set_fault):
    """Counts are per-checkpoint. If hits on a different checkpoint advanced the
    armed one, `crash:after_intent:3` would fire at whichever call happened to be
    third overall - and every multi-checkpoint scenario in Task 11's matrix would
    crash at the wrong place while still reporting a crash.
    """
    set_fault("crash:after_intent:2")

    for name in faults.CHECKPOINTS:
        if name != "after_intent":
            assert faults.maybe_fault(name) is None
    assert faults.maybe_fault("after_intent") is None
    with pytest.raises(SystemExit):
        faults.maybe_fault("after_intent")


def test_dbfail_raises_an_operational_error(set_fault):
    """A different exception type on purpose: SystemExit kills the process and
    recovery runs on the next invocation, while a DB error is caught in-process and
    the run must not be left half-committed. Task 11 asserts different things about
    each, so they must not be interchangeable.
    """
    set_fault("dbfail:before_done_commit:1")

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        faults.maybe_fault("before_done_commit")
    assert "before_done_commit" in str(excinfo.value)
    assert not isinstance(excinfo.value, SystemExit)


@pytest.mark.parametrize(
    "spec",
    [
        "crash:after_intent",  # too few parts
        "crash:after_intent:1:extra",  # too many parts
        "",  # empty
        "boom:after_intent:1",  # unknown mode
        "crash:after_intnet:1",  # unknown checkpoint (typo)
        "CRASH:after_intent:1",  # modes are case-sensitive
        "crash:after_intent:one",  # non-numeric hit count
        "crash:after_intent:-1",  # negative hit count
        "crash:after_intent:0",  # counts are 1-based, so 0 can never be reached
    ],
)
def test_a_malformed_or_unknown_spec_is_ignored_at_every_checkpoint(set_fault, spec):
    """A typo in a test env var must never look like a passing run - but it must not
    break a real user invocation either, so it is ignored rather than raised on.
    """
    set_fault(spec, malformed=True)

    for name in faults.CHECKPOINTS:
        assert faults.maybe_fault(name) is None


def test_a_non_numeric_hit_count_does_not_even_touch_the_counter(set_fault):
    """The guard sits BEFORE the `_counts` bump, and the ordering is the point.

    `int(n)` raises ValueError on a non-numeric n, so the pre-fix code both raised
    and left the count bumped - corrupting hit counting for every later call on that
    checkpoint even if the spec were fixed mid-run. Asserting the count is untouched
    is what pins the ordering; asserting only "it did not raise" would pass with the
    guard placed after the bump.
    """
    set_fault("crash:after_intent:one", malformed=True)

    for _ in range(3):
        assert faults.maybe_fault("after_intent") is None
    assert faults._counts == {}


def test_a_zero_hit_count_can_never_fire(set_fault):
    """`_counts` starts at 1, so `crash:X:0` arms a checkpoint no call can reach.

    Left un-rejected it is the worst kind of malformed spec: a Task 11 scenario armed
    with it runs a fully successful apply and reports a pass, having tested nothing.
    Rejected here it is inert AND leaves no count behind, so `set_fault.hits()` reads
    zero and the fixture's arm-time validation (which refuses '0' outright) has
    something true to rest on.
    """
    set_fault("crash:after_intent:0", malformed=True)

    for _ in range(3):
        assert faults.maybe_fault("after_intent") is None
    assert faults._counts == {}


def test_an_armed_checkpoint_does_not_fire_at_a_different_checkpoint(set_fault):
    set_fault("crash:after_rename:1")

    assert faults.maybe_fault("before_intent_commit") is None
    with pytest.raises(SystemExit):
        faults.maybe_fault("after_rename")


def test_a_checkpoint_outside_CHECKPOINTS_can_never_be_armed(set_fault):
    """Closes mutation M70. Dropping the `checkpoint not in CHECKPOINTS` operand
    survived the whole suite, because every OTHER test passes maybe_fault a name that
    is in CHECKPOINTS, and then `checkpoint != name` refuses the unknown spec anyway.

    The operand is not redundant: it makes CHECKPOINTS the CLOSED vocabulary. A
    checkpoint call added in a later task but never registered in the tuple must stay
    unarmable, so a fault-matrix scenario naming it fails visibly instead of quietly
    doing nothing while still reporting a pass. Driven here with a name on both sides
    that the tuple does not contain - the only shape in which the two operands differ.
    """
    assert "not_a_real_checkpoint" not in faults.CHECKPOINTS
    set_fault("crash:not_a_real_checkpoint:1", malformed=True)

    for _ in range(3):
        assert faults.maybe_fault("not_a_real_checkpoint") is None


def test_reset_clears_the_counts(set_fault):
    set_fault("crash:mid_companion_unit:2")

    assert faults.maybe_fault("mid_companion_unit") is None
    faults.reset()
    # Back to hit 1, so the next call must not fire.
    assert faults.maybe_fault("mid_companion_unit") is None
    with pytest.raises(SystemExit):
        faults.maybe_fault("mid_companion_unit")


def test_counts_do_not_leak_in_from_an_earlier_test():
    """Companion to test_reset_clears_the_counts: proves the conftest fixture does
    the reset, not just that reset() exists. Several tests above leave `_counts`
    populated; this one asserts a fresh test starts empty.
    """
    assert faults._counts == {}


def test_an_ambient_pam_fault_does_not_arm_the_suite():
    """Closes mutation M73. The conftest fixture pops PAM_FAULT at setup AND restores
    it at teardown, and every in-process test is blind to the difference: nothing a
    test does can leave the variable behind, so removing the setup pop changed nothing.

    The setup pop exists for the one source a test cannot simulate from inside - a
    PAM_FAULT already set in the shell that launched pytest. Without it, a developer
    who armed a fault by hand and forgot runs the entire suite with a live crash
    checkpoint, and every failure names a checkpoint the failing test never mentions.
    So this drives it from outside: a real pytest subprocess with PAM_FAULT in its
    environment, running the one test that hits every checkpoint.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = dict(os.environ)
    env["PAM_FAULT"] = "crash:after_intent:1"
    env.pop("PAM_HOME", None)  # let the child's own conftest pick its own home
    root = Path(__file__).resolve().parent.parent

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_faults.py::test_unset_pam_fault_is_a_no_op_at_every_checkpoint",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        "an ambient PAM_FAULT armed the child run; the conftest fixture must pop it"
        f" at setup\n{proc.stdout}\n{proc.stderr}"
    )


# --- the set_fault fixture itself (M5 Task 11 leans on all of this) ----------------


@pytest.mark.parametrize(
    "spec",
    [
        "crash:after_renmae:1",  # checkpoint typo - the motivating case
        "crsah:after_rename:1",  # mode typo
        "crash:after_rename:one",  # non-numeric hit count
        "crash:after_rename:0",  # a hit count that can never be reached
        "crash:after_rename",  # wrong arity
    ],
)
def test_arming_a_spec_that_could_never_fire_fails_the_test_immediately(set_fault, spec):
    """Production is lenient, the test route is strict - and that asymmetry is the fix.

    A scenario armed with `crash:after_renmae:1` runs a fully SUCCESSFUL apply under
    the shipped `maybe_fault`, so all of its assertions pass trivially and the cell
    reports green while testing nothing. In a 48-cell matrix that is the failure mode
    that matters, so the fixture refuses the spec at arm time.
    """
    with pytest.raises((AssertionError, ValueError)):
        set_fault(spec)

    # And it refused BEFORE writing the variable, so nothing is left armed.
    import os

    assert os.environ.get("PAM_FAULT") is None


def test_a_deliberately_malformed_spec_is_still_armable(set_fault):
    """The escape hatch the tests of production's leniency need, and only they."""
    import os

    set_fault("crash:after_renmae:1", malformed=True)

    assert os.environ["PAM_FAULT"] == "crash:after_renmae:1"


def test_next_invocation_clears_both_halves_of_the_switch(set_fault):
    """A fresh process has no PAM_FAULT and no hit counts; this restores both.

    Popping the env var alone is the tempting half-fix, and it produces no error at
    all: `faults._counts` keeps the hits the crashed run spent, so the recovery pass
    that follows starts mid-count. With `crash:after_rename:2` on a three-member unit
    that means the REVERT path can consume the hit meant for the apply path, and the
    scenario crashes somewhere other than where it says it does.
    """
    import os

    set_fault("crash:after_rename:2")
    assert faults.maybe_fault("after_rename") is None
    assert faults._counts == {"after_rename": 1}

    set_fault.next_invocation()

    assert os.environ.get("PAM_FAULT") is None
    assert faults._counts == {}
    # Proven by behaviour, not just by inspection: the checkpoint is inert now.
    for _ in range(3):
        assert faults.maybe_fault("after_rename") is None


def test_next_invocation_does_not_trip_the_isolation_tripwire(set_fault):
    """The tripwire fires on a PAM_FAULT left behind; next_invocation leaves none.

    Written as its own test because the tripwire runs at TEARDOWN: if
    next_invocation() were ever changed to leave the variable set, the failure would
    surface here rather than in whichever fault scenario happened to run last.
    """
    set_fault("crash:after_intent:1")
    set_fault.next_invocation()


def test_hits_reports_whether_a_checkpoint_actually_fired(set_fault):
    """`hits` is how a scenario proves it crashed where it claims to have crashed.

    Without it a cell asserts only on the aftermath, and an aftermath that looks
    right because nothing happened is indistinguishable from one that looks right
    because everything was recovered.
    """
    set_fault("crash:mid_companion_unit:3")

    assert set_fault.hits("mid_companion_unit") == 0
    faults.maybe_fault("mid_companion_unit")
    faults.maybe_fault("mid_companion_unit")
    assert set_fault.hits("mid_companion_unit") == 2
    assert set_fault.hits("after_rename") == 0

    with pytest.raises(SystemExit):
        faults.maybe_fault("mid_companion_unit")
    assert set_fault.hits("mid_companion_unit") == 3

    set_fault.next_invocation()
    assert set_fault.hits("mid_companion_unit") == 0


@pytest.mark.parametrize(
    "spec",
    [
        "crash:after_renmae:1",  # checkpoint typo - the motivating case
        "crsah:after_rename:1",  # mode typo
        "crash:after_rename:0",  # a hit count that can never be reached
    ],
)
def test_a_child_env_that_could_never_fire_fails_the_test_immediately(set_fault, spec):
    """`fault_env` validates exactly as `arm` does, and it used not to.

    M5 Task 11's matrix runs 31 of its 42 cells in a CHILD process, so `fault_env` is
    the strict route for most of the gate. Unvalidated, a typo there launches a fully
    successful `pam apply` in a fresh interpreter - and the cell then passes or fails
    entirely on whether its author remembered to assert that the crash happened.
    """
    with pytest.raises((AssertionError, ValueError)):
        set_fault.fault_env(spec)

    # And it still builds one when the caller says the spec is deliberately malformed.
    assert set_fault.fault_env(spec, malformed=True)["PAM_FAULT"] == spec


def test_fault_env_arms_a_child_without_arming_this_process(set_fault):
    """The subprocess route: SystemExit unwinds rather than killing, so a genuine
    process-level crash only exists in a child process. Building that child's env
    must not arm the parent, or every test after this one inherits the fault.
    """
    import os

    env = set_fault.fault_env("crash:after_rename:1")

    assert env["PAM_FAULT"] == "crash:after_rename:1"
    assert os.environ.get("PAM_FAULT") is None
    assert env["PATH"] == os.environ["PATH"]  # a real environment, not a bare dict

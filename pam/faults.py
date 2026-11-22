"""Named failure checkpoints for fault-injection testing (design spec section 9.3).

Production code, not test scaffolding: the shipped binary can be made to fail at a
known point via PAM_FAULT, which is how the journal protocol's crash-recovery
guarantees are tested against real filesystem state rather than mocks.
Cost when PAM_FAULT is unset: one os.environ lookup per checkpoint.

Read `maybe_fault`'s docstring before writing a fault scenario: the `crash:` mode is
a clean shutdown at a known point, NOT a power cut, and the difference decides what
a scenario is actually testing.
"""

import os
import sqlite3

# The rename checkpoints (spec 9.3): the journal protocol's, and the fault matrix's rows.
RENAME_CHECKPOINTS = (
    "before_intent_commit",
    "after_intent",
    "after_rename",
    "before_done_commit",
    "mid_companion_unit",
    "mid_unit_revert",
    "before_plan_finalize",
)
# The migration checkpoints (spec 9.3 rev 35, E12): `db.migrate`'s three boundaries around the
# compatibility ledger's reservation. No rename reaches them, so the rename matrix has no row for
# them; `tests/test_schema_ledger.py` drives each one.
MIGRATION_CHECKPOINTS = (
    "before_ledger_reservation",
    "after_ledger_reservation",
    "after_migration_commit",
)
CHECKPOINTS = RENAME_CHECKPOINTS + MIGRATION_CHECKPOINTS
MODES = ("crash", "dbfail")

_counts: dict[str, int] = {}


def maybe_fault(name: str) -> None:
    """Raise at the Nth hit of `name` when PAM_FAULT says so.

    PAM_FAULT syntax: '<mode>:<checkpoint>:<n>', 1-based.
      crash:  raises SystemExit             - a clean shutdown at a known point.
      dbfail: raises sqlite3.OperationalError - caught in-process; the run must
              not be left half-committed.

    WHAT `crash:` ACTUALLY DOES (verified empirically, M5 Task 4 review). SystemExit
    is an ordinary exception: it UNWINDS THE STACK rather than killing the process.
    So between the checkpoint and `main`, every `finally` block runs, every context
    manager's `__exit__` runs (`with conn:` rolls back, the workspace lock releases,
    close_session_log runs), and pam/cli.py's `except SystemExit` handler echoes the
    message and returns EXIT_EXPECTED. The process is still alive afterwards, the
    hit counts in `_counts` survive, and PAM_FAULT stays armed.

    Two consequences, both load-bearing for the fault matrix:
      1. No code between a checkpoint and `main` may depend on a `finally` for
         durability - a `finally` is exactly what a real power cut would skip, and
         `crash:` will happily run it, so a scenario relying on one proves nothing
         about a real crash.
      2. A genuine process-level crash needs the command run in a SUBPROCESS. That
         is what spec 9.3's "recovery runs on the *next* invocation" means: a
         separate process, started after the first one died.

    A malformed PAM_FAULT is ignored: a typo in a test env var must never look
    like a passing run, but it must not break a real user invocation either. The
    hit counter is not touched by an ignored spec, so a typo cannot skew counting
    either. (The strict route for tests is the `set_fault` fixture, which refuses a
    malformed spec at arm time instead - conftest, spec 9.3 rev 7.)
    """
    spec = os.environ.get("PAM_FAULT")
    if not spec:
        return
    parts = spec.split(":")
    if len(parts) != 3:
        return
    mode, checkpoint, n = parts
    if mode not in MODES or checkpoint not in CHECKPOINTS or checkpoint != name:
        return
    # Before the counter bump, deliberately: `int(n)` raises ValueError on a
    # non-numeric n, and a bump that happened first would corrupt hit counting for
    # every later call as well. '0' is rejected too - `_counts` starts at 1, so
    # 'crash:after_intent:0' arms a checkpoint that can never fire, which reads as a
    # passing scenario while testing nothing.
    if not n.isdigit() or n == "0":
        return
    _counts[name] = _counts.get(name, 0) + 1
    if _counts[name] != int(n):
        return
    if mode == "crash":
        raise SystemExit(f"PAM_FAULT: crashed at {name} hit {n}")
    raise sqlite3.OperationalError(f"PAM_FAULT: simulated DB failure at {name} hit {n}")


def reset() -> None:
    """Clear hit counts. Tests call this; production never does."""
    _counts.clear()

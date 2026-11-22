"""A real pam process for `tests/test_run_registry.py` (E11, M15 Task 13A). Not a test module.

    python run_registry_child.py READY HOLD [GATE]

It runs THE REAL ENTRY POINT, `pam.entry.main` (the console script, M15 Task 13A-F1), with one extra
command, `hold-for-test`, which writes this process's pid to READY and then waits until the file HOLD
is gone. Adding that command imports `pam.cli` before the entry runs, so this child cannot speak to
the import window; `test_run_registry.py`'s `_ENTRY_PROBE` does, in a fresh interpreter. It reports its OWN pid
because a venv's `python.exe` is a launcher that starts the interpreter as a child (spike 10C,
M5): `Popen.pid` is not the pid that registered, and killing it does not kill the holder.

With GATE, the production `run_registry.setup_running` is wrapped so this process stops at the
moment the entry checks for setup - after whatever it does first - writing its pid to
GATE/reached and waiting for GATE/go (and again, passing at once, at `pam.cli.main`'s second look). That is how a test forces 10C's interleavings with no sleep.
A refused command never writes READY; `main`'s exit code is this process's.
"""

import os
import sys
import time
from pathlib import Path

DEADLINE_S = 120


def _write(path: Path, text: str) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="ascii")
    os.replace(partial, path)


def _wait(condition) -> None:
    end = time.monotonic() + DEADLINE_S
    while not condition():
        if time.monotonic() > end:
            sys.exit(9)
        time.sleep(0.005)


def main() -> int:
    ready, hold = Path(sys.argv[1]), Path(sys.argv[2])
    gate = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    from pam import cli, entry, run_registry

    if gate is not None:
        original = run_registry.setup_running

        def paused(*args, **kwargs):
            _write(gate / "reached", str(os.getpid()))
            _wait((gate / "go").exists)
            return original(*args, **kwargs)

        run_registry.setup_running = paused

    @cli.cli.command("hold-for-test")
    def hold_for_test() -> None:
        _write(ready, str(os.getpid()))
        _wait(lambda: not hold.exists())

    return entry.main(["hold-for-test"])


if __name__ == "__main__":
    sys.exit(main())

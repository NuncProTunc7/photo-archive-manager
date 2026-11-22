"""The gate lane's end-to-end pass over the spec 9.1 tiny library (M6 Task 9).

`scan -> plan -> verify` against an archive of REAL files, through the real command line, in a
throwaway `PAM_HOME`. The suite already asserts what each of those commands produces;
what this adds is the part a suite cannot: that the *installed* `pam` works on a machine
that is not the developer's, over a library rebuilt from the repository rather than one
that happens to be sitting on disk.

TWO THINGS HERE ARE LOAD-BEARING AND BOTH LOOK LIKE PLUMBING.

  1. THE LIBRARY COMES FROM `tests/archives.py::materialise_tiny_library`, never from a
     `shutil.copytree` of `tests/tiny_library/`. Seven of the eight reserved device-stem
     files (`AUX`, `CON`, `NUL`, `PRN`, `COM1`, `LPT1`, `LPT0`) cannot be committed on
     Windows, so the committed folder holds 99 files and the library under test holds
     106; and the fixture stamps mtimes, without which the fourteen date-less files take
     their dates from the moment of `git checkout`. A hand-rolled copy here would be
     green on this machine and red on a fresh clone - which is precisely the machine
     this file exists for. See that function's docstring for the three mutations that
     established it.

  2. `pam verify` MUST EXIT 0 HERE. Spec 7.5 gives verify three exit codes - 0 all pass,
     1 warnings only, 2 errors - and an archive out in the world can legitimately warn
     forever (an M6 review finding proposes that CI read exit 1 and gate on exit 2).
     The tiny library is not such an archive: it is built by this repository, scanned by
     this repository, and every one of the twelve checks passes today. So the gate holds
     it to 0 and prints the report either way. If this ever starts warning, that is a
     result to look at, not a threshold to relax.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _pam_command() -> list[str]:
    """The installed console script if there is one, else this interpreter's entry point.

    The console script is preferred because it is what a user runs and what
    `pip install -e .` is being asked to prove. The fallback keeps the tool usable from
    a shell whose PATH does not carry the venv's `Scripts/`, which is the normal state
    of an agent harness on this project.
    """
    found = shutil.which("pam")
    if found:
        return [found]
    return [
        sys.executable,
        "-c",
        "import sys;from pam.cli import main;sys.exit(main(sys.argv[1:]))",
    ]


def _run(command: list[str], args: list[str], env: dict[str, str]) -> int:
    printable = " ".join(["pam", *args])
    print(f"\n$ {printable}", flush=True)
    completed = subprocess.run([*command, *args], env=env, cwd=str(REPO_ROOT))
    print(f"[exit {completed.returncode}] {printable}", flush=True)
    return completed.returncode


def run_gate(workdir: Path) -> int:
    """Materialise, scan, plan, verify. Returns a process exit code."""
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    from archives import materialise_tiny_library  # noqa: PLC0415  (tests/ is not a package)

    root = workdir / "tiny_library"
    materialise_tiny_library(root)
    found = sum(1 for path in root.rglob("*") if path.is_file())
    print(f"Materialised the spec 9.1 tiny library at {root}: {found} files", flush=True)

    # POINT 1 OF THE DOCSTRING, ENFORCED RATHER THAN EXPLAINED (M6 Task 9 review, M527).
    # Swapping the fixture for a `shutil.copytree` of `tests/tiny_library/` left this gate
    # exiting 0 over 99 files instead of 106: `pam verify` has no opinion about which files
    # it was handed, so the missing seven reserved device stems cost nothing observable.
    # The committed count is read from git's own copy, so this is a property - "the library
    # under test is bigger than the one in the repository" - and not a magic number.
    committed = sum(1 for path in (TESTS_DIR / "tiny_library").rglob("*") if path.is_file())
    if found <= committed:
        print(
            f"\nGATE FAILED: the library holds {found} files, no more than the {committed}"
            " committed to git. The seven reserved device stems cannot be committed on"
            " Windows, so a library that is only a copy of tests/tiny_library/ is missing"
            " them - and this gate is then weaker than it reads.",
            flush=True,
        )
        return 1

    env = dict(os.environ)
    # A throwaway PAM_HOME: the gate must not read, write or depend on whatever archive
    # pointers the machine running it already has (see spec 3 and `pam forget`).
    env["PAM_HOME"] = str(workdir / "pam_home")
    # Printed so the property is observable from outside the process (M532): a caller can
    # check that the home really sits beside the scratch library rather than in the user's.
    print(f"Throwaway PAM_HOME: {env['PAM_HOME']}", flush=True)
    command = _pam_command()
    print(f"Driving: {' '.join(command)}", flush=True)

    for args in (["scan", str(root)], ["plan", "--archive", str(root)]):
        code = _run(command, args, env)
        if code != 0:
            print(f"\nGATE FAILED: `pam {args[0]}` exited {code}", flush=True)
            return 1

    code = _run(command, ["verify", "--archive", str(root)], env)
    if code != 0:
        # Never swallowed, and the two nonzero codes are not the same event: see this
        # module's docstring, point 2.
        meaning = {1: "warnings", 2: "errors"}.get(code, "an unexpected failure")
        print(
            f"\nGATE FAILED: `pam verify` exited {code} ({meaning}) over the tiny"
            " library, which every check passes today. Read the report above.",
            flush=True,
        )
        return 1

    print("\nGATE PASSED: scan, plan and verify are all clean over the tiny library.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="where to build the scratch library (default: a temporary directory)",
    )
    options = parser.parse_args(argv)
    if options.workdir is not None:
        options.workdir.mkdir(parents=True, exist_ok=True)
        return run_gate(options.workdir)
    with tempfile.TemporaryDirectory() as directory:
        return run_gate(Path(directory))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

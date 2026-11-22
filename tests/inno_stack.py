"""ONE boolean, decided ONCE, for every `inno`-marked test (M15 Task 13B, ruling 7).

The installer's behavioural tests compile a TEST build with Inno Setup's compiler and run it, so
they need `ISCC.exe` on a Windows machine. Where it is absent they skip - all of them, on this
one decision, as the faces and ocr stacks do (`tests/faces_stack.py`, A2).

`PAM_INNO_REQUIRED=1` turns "absent" into a COLLECTION ERROR (`tests/conftest.py`): the CI job
that exists to run these tests (Task 14) must not go green having run nothing, which is the M11
review's R3 for the faces job. The local suite leaves it unset.

Where ISCC is: `PAM_INNO_ISCC` if set, else the per-user default
`%LOCALAPPDATA%\\Programs\\Inno Setup 6\\ISCC.exe` (the developer's install, Inno Setup 6.7.4).
"""

import os
import sys
from pathlib import Path

REQUIRED_VAR = "PAM_INNO_REQUIRED"
ISCC_VAR = "PAM_INNO_ISCC"


def iscc_path() -> Path:
    override = os.environ.get(ISCC_VAR, "").strip()
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"


def _probe() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "the installer is Windows-only"
    path = iscc_path()
    if not path.is_file():
        return False, f"Inno Setup's compiler is not at {path} (set {ISCC_VAR})"
    return True, f"ISCC at {path}"


STACK_READY, STACK_REASON = _probe()

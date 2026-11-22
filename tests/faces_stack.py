"""ONE boolean, decided ONCE, for every `faces`-marked test in the suite (A2).

WHY THIS IS A MODULE AND NOT A `skipif` ON EACH TEST. A2 requires a **deterministic skip
count per lane**: the push lane installs `.[dev]` and skips every faces-marked test, the
faces job installs `.[faces]` with a real pack and skips none. A count that lands
somewhere in between is a count nobody can reconcile against either lane, and it is what
happens the moment two tests ask two slightly different questions - one checks the extra,
another checks the pack, a third checks a file inside it. So the question is asked here,
once, at import; every marked test shares the answer by construction, and "all or none"
is a property of the shape rather than of anybody's diligence.

WHERE THE PACK COMES FROM. `PAM_FACES_MODEL_DIR`, an environment variable, because the
pack is ~37 MB of third-party weights and **is not in this repository** - `pyproject.toml`
says so: the weights are not a dependency. The CI faces job fetches the two pinned files,
verifies their hashes and points this at them. A developer sets it to wherever they put
theirs. Unset is the ordinary case and is not an error.

WHAT IS DELIBERATELY *NOT* MARKED. Everything that can be answered without the extra: the
`describe("")` sentence, `pam doctor`'s optional line, the AST walk over `pam/` for
module-scope imports and for `providers=`. Those are the checks that would otherwise be
invisible on the push lane, which is exactly when a mistake gets committed.
"""

import os
from pathlib import Path

ENV_VAR = "PAM_FACES_MODEL_DIR"

# THE FACES JOB SETS THIS, AND ONLY THE FACES JOB (the M11 review's R3). Without it, a probe
# that answers "no" for ANY reason - the pack variable lost from the workflow, a manifest
# `describe` refuses, the extra missing - skips all ten marked tests and `pytest -m faces`
# exits 0: measured, `10 skipped, 3735 deselected`, exit 0, with a real SHA-verified pack.
# The only release gate on the real model stack would go green having run nothing. With it
# set, `tests/conftest.py` turns the shared skip into a collection error naming the reason.
# The push lane leaves it unset and keeps its deterministic skip (A2).
REQUIRED_VAR = "PAM_FACES_REQUIRED"


def _probe() -> tuple[bool, str]:
    """Is the whole faces stack here? Extra AND pack, as one answer."""
    import importlib.util

    for module in ("onnxruntime", "numpy"):
        try:
            if importlib.util.find_spec(module) is None:
                return False, f"the optional [faces] extra is not installed ({module})"
        except (ImportError, ValueError):
            return False, f"the optional [faces] extra is not importable ({module})"
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return False, f"{ENV_VAR} is not set, so there is no model pack to load"
    from pam.enrich import faces_model

    ok, sentence = faces_model.describe(raw)
    if not ok:
        return False, f"{ENV_VAR}={raw}: {sentence}"
    return True, sentence


STACK_READY, STACK_REASON = _probe()


def model_dir() -> Path:
    """The pack's directory. Only ever called from a `faces`-marked test."""
    assert STACK_READY, STACK_REASON
    return Path(os.environ[ENV_VAR].strip())

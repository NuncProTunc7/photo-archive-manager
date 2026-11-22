"""Error hierarchy, exit codes, and the one refusal sentence two subsystems both owe."""

EXIT_OK = 0  # success
EXIT_EXPECTED = 1  # a PamError: known failure, message already printed
EXIT_INTERNAL = 2  # unexpected exception: traceback printed


class PamError(Exception):
    """Base class for all expected pam errors. The CLI prints the message and exits 1."""


class ConfigError(PamError):
    """pam.toml is invalid: unknown key, wrong type, or out-of-range value."""


class WorkspaceLockedError(PamError):
    """Another pam process holds this archive's lock."""


class MigrationError(PamError):
    """The catalog schema cannot be safely migrated."""


class CatalogTooNewError(PamError):
    """The catalog names a migration this pam does not ship: a newer pam wrote it (E1)."""


class LedgerError(PamError):
    """The compatibility ledger (E12) cannot be read, or a reservation cannot be written."""


class RunRegistryError(PamError):
    """E11: pam is being installed or updated, or pam cannot tell whether it is."""


class ExifToolError(PamError):
    """exiftool is missing, or its process stopped mid-scan."""


class TemplateError(PamError):
    """The naming template is malformed, uses an unknown token, or the template
    engine could not assemble a legal stem from it (`template._finalize`'s bound)."""


class PamNetworkError(PamError):
    """A network destination is not on this machine, or the local one could not be reached.

    Spec 10: `pam/net.py`'s `LoopbackHttpClient` is the only HTTP code in the codebase
    and it raises this unless the host **resolves** to a loopback address. It is a
    `PamError` and not an internal error on purpose: a user who points pam at a model
    server on another box has made an ordinary configuration mistake, and deserves the
    printed message and exit 1 rather than a traceback.
    """


# THE PRE-0004 PLAN REFUSAL, AND WHY THE SENTENCE LIVES HERE RATHER THAN AT ONE OF ITS
# TWO CALLERS. Migration 0004 had to declare `unit_key TEXT NOT NULL DEFAULT ''` (SQLite
# requires a default when adding a NOT NULL column), so a plan built before it
# legitimately carries ''. Spec 7.3 groups renames by `unit_key` and by nothing else, so
# such a plan gives apply nothing to group by: a companion group would be applied as two
# unrelated files and could end up split across two stems (vision S3). Rebuild it.
#
# `execute/validator.py` refuses on it and, from M10 Task 4 (finding F10), so does the
# review UI - a plan the browser lets a user decide four hundred files of, and `pam apply`
# then refuses outright, is a wasted review pass. One condition gets ONE sentence; two
# wordings is how a user learns the browser and the CLI are different programs.
#
# It sits in `errors.py` because `tests/guards.py::ALLOWED_WEBUI_EXECUTE_IMPORTS` forbids
# `pam/webui/` from importing anything under `pam.execute` that is not named there, and
# that allowlist is the guard behind spec 8.2's "the UI reads and approves; it never
# applies". Sharing the copy must not cost the guard an entry: `pam.errors` is already
# what both subsystems import for `PamError`, and a string cannot rename a file.
OLD_PLAN_REASON = (
    "Plan {plan_id} was built by an older version of pam. Run: pam plan to rebuild it."
)

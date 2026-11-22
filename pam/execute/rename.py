"""The one verified rename in pam (spec 7.1 rev 7's decomposition; spec 7.0 J8).

Apply, undo and recovery's revert all call `perform_verified_rename`. That is not
tidiness: three copies of "check, then move an irreplaceable file" would drift apart
by M8, and the drift would stay invisible until the day one of them lost a photo.
The single implementation is enforced mechanically - `tests/test_rename.py` asserts
that the whole `pam` package contains exactly one `os.rename` call, the one below.

Recovery's ADOPTION path (new exists, old absent -> mark the row `done`) deliberately
does NOT come through here. It performs no rename, and routing it through a function
whose contract is "verify, then rename" would make that contract dishonest.

This module writes nothing to the catalog and nothing to the journal. It is called
AFTER `RenameTransaction.begin()` has committed the intent (spec 7.0 J1) and its
answer decides which resolution the caller reaches for.

M14 X4 (adopted 2026-11-05): the case-insensitive occupancy check (check 4) is answered
from ONE listing of the target directory per run, held in a `DirectoryNames` the caller
builds once per `pam apply`, `pam undo` or recovery pass. Without it, a folder of N files
was listed N times: 20,000 files in one folder took 639.6 s to apply against 94.9 s spread
over 150 folders.
"""

import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pam.faults import maybe_fault

# spec 5.4 rev 6 / M4 finding N1: refused, never performed. The planner cannot emit
# a case-only rename any more (M5 Task 1); this is the second layer, and it is the
# layer that would survive a planner regression.
CASE_ONLY_REASON = "pam does not rename a file to a different capitalisation of the same name."

# UX 4's confirmation copy is normative and promises "0 files will be deleted, moved,
# or modified - renames only, in place." Nothing else in the decomposition enforces
# that: validator.py's scope (spec 7.1's table) is status, unit_key and schema, and
# two of this function's three callers - recovery's revert and undo - build their
# paths from JOURNAL ROWS rather than from a validated plan. So the promise is kept
# here, in the one function permitted to move a file.
SAME_DIRECTORY_REASON = (
    "pam only renames files where they are, and that name would move this one to a"
    " different folder, so it was left alone. Run pam plan again."
)

# The refusal codes. `reason` is a sentence for a human; `code` is for pam. Tasks 8
# and 9 must tell "changed on disk" (skip, leave the row `done` - spec 7.4, J4) apart
# from "target occupied" and from "the OS refused", and branching on English prose is
# a defect waiting for the first time someone improves the wording.
CODE_CASE_ONLY = "case_only"
CODE_NOT_SAME_DIRECTORY = "not_same_directory"
CODE_SOURCE_UNREADABLE = "source_unreadable"
CODE_CHANGED_ON_DISK = "changed_on_disk"
CODE_TARGET_OCCUPIED = "target_occupied"
CODE_RENAME_FAILED = "rename_failed"
REFUSAL_CODES = frozenset(
    {
        CODE_CASE_ONLY,
        CODE_NOT_SAME_DIRECTORY,
        CODE_SOURCE_UNREADABLE,
        CODE_CHANGED_ON_DISK,
        CODE_TARGET_OCCUPIED,
        CODE_RENAME_FAILED,
    }
)


@dataclass(frozen=True)
class RenameOutcome:
    """What happened. `ok=False` is an OUTCOME, not an error (spec 13).

    An occupied target and a file that changed since the scan are ordinary things
    to find in an archive of real files: record, count, continue. Raising for them would abort
    a run over one file, which vision principle 9 forbids.

    `reason` is the sentence a human reads. `code` is the discriminator pam branches
    on. `detail` carries the OS's own words where there were any - it belongs in the
    session log and the report's detail column, not glued onto `reason`, because
    `str(OSError)` repeats the absolute paths the report already prints beside it.
    """

    ok: bool
    reason: str | None = None
    code: str | None = None
    detail: str | None = None


class DirectoryNames:
    """One run's memory of the names in each folder it renames in (spec 6.3.2, 7.1; X4).

    One per run: one `pam apply`, one `pam undo`, one recovery pass. Never shared between
    runs and never kept beyond one.

    - **First touch of a folder:** it is listed once with `os.scandir` and its names are
      kept LOWERED, counted rather than merely collected, so a case-sensitive filesystem
      holding both `a.jpg` and `A.JPG` loses the name only when both are gone.
    - **"Taken" is re-verified:** when memory says a name is taken, the folder is listed
      fresh, memory is replaced with that listing, and only the fresh answer counts. So
      every refusal is exactly the answer a per-rename listing gives.
    - **"Free" is confirmed by the caller:** `perform_verified_rename` calls `os.lstat` on
      the target immediately before `os.rename`, and refuses (and forgets the folder) if
      anything is there. NTFS's `lstat` matches case-insensitively, so a case variant
      created mid-run is caught there too. On a case-sensitive filesystem it is not: that
      is X4's one accepted residual (XQ2), and it cannot overwrite a file there.
    - **After a successful `os.rename`** the old name leaves memory and the new one enters
      it, so pam's own earlier renames in the same run (a chain, a swap, undo's reverse
      order) are seen. **After a failed `os.rename`** the folder is forgotten, and its next
      rename lists it fresh.

    A CRASH LEAVES NOTHING STALE. The memory lives in the process. A crash between
    `os.rename` and the memory update (the `after_rename` checkpoint sits exactly there)
    loses it with the process - or, with `crash:`, which unwinds, with the run that held
    it - and recovery builds its own on the next invocation from a fresh listing. A stale
    entry would still be answered honestly: a stale "taken" is re-listed, and a stale
    "free" meets `lstat`.
    """

    def __init__(self) -> None:
        self._names: dict[Path, Counter[str]] = {}

    def known(self, directory: Path, name: str) -> bool | None:
        """Memory's own answer, with no disk access: None if the folder is not remembered."""
        names = self._names.get(directory)
        if names is None:
            return None
        return name.lower() in names

    def taken(self, directory: Path, name: str) -> bool:
        """Is `name` taken in `directory`, ignoring case? Free from memory; taken from disk.

        A folder not yet listed, or a name memory says is taken, is answered by a fresh
        listing, which replaces memory. Raises OSError if the folder cannot be listed.
        """
        if self.known(directory, name) is False:
            return False
        return name.lower() in self.relist(directory)

    def relist(self, directory: Path) -> Counter[str]:
        """List `directory` now and remember exactly that."""
        with os.scandir(directory) as entries:
            names = Counter(entry.name.lower() for entry in entries)
        self._names[directory] = names
        return names

    def renamed(self, directory: Path, old_name: str, new_name: str) -> None:
        """Record a rename that HAPPENED: the old name has gone, the new one is there."""
        names = self._names.get(directory)
        if names is None:
            return
        old = old_name.lower()
        names[old] -= 1
        if names[old] <= 0:
            del names[old]
        names[new_name.lower()] += 1

    def forget(self, directory: Path) -> None:
        """Drop what is remembered about `directory`; its next question lists it fresh."""
        self._names.pop(directory, None)


def perform_verified_rename(
    *,
    archive_root: Path,
    old_rel: str,
    new_rel: str,
    expect_size: int,
    expect_mtime_ns: int,
    logger: logging.Logger,
    names: DirectoryNames | None = None,
) -> RenameOutcome:
    """Verify against DISK, rename, log. No journal writes, no catalog writes.

    The five refusals, in the order they are checked:

    0. The target is not in the source's own directory. A rename in pam moves a name,
       never a file (UX 4). This also rejects `..` and an absolute target as a side
       effect, so nothing can be renamed out of the archive.
    1. A case-only rename (spec 5.4 rev 6). `os.rename` on Windows would refuse it
       anyway, or - worse, on a case-insensitive filesystem - perform it as a no-op
       that later reads as success.
    2. The source is unreadable or gone.
    3. `(size, mtime_ns)` disagrees with the snapshot the plan was built from. The
       numbers come from `os.stat` on the real path, never from the catalog: a
       disagreement is exactly the signal that the file changed since the scan, and
       comparing the catalog against itself could not see it.
    4. The target name is taken, compared case-insensitively (spec 6.3.2). pam never
       overwrites (spec 7.0 J8). With `names=None` the target's folder is listed for
       this one rename, as it always was. With a run's `DirectoryNames` (X4) the answer
       comes from one listing of the folder per run, kept current with pam's own
       renames: a "taken" answer is confirmed by a fresh listing before refusing, and a
       "free" answer by `os.lstat` of the target immediately before `os.rename`. On a
       case-sensitive filesystem, a case variant another process creates during the run
       is not detected; it cannot be overwritten there either.

    `old_rel == new_rel` needs no case of its own: check 4 finds the source itself
    sitting on the target name and refuses, which is the correct answer for a rename
    with nothing to do.

    Returns RenameOutcome(ok, reason, code, detail). Never raises for an expected
    condition; any OSError becomes `ok=False` carrying the OS's own words in `detail`.
    """
    # Compared as the POSIX rel_paths the catalog actually stores (walker normalizes
    # with as_posix()), so no resolve()/is_relative_to() dance is needed: "../x.jpg"
    # and "C:/x.jpg" both fail the directory comparison outright.
    old_dir, _, _ = old_rel.rpartition("/")
    new_dir, _, new_name = new_rel.rpartition("/")
    if new_dir != old_dir or not new_name or new_name in (".", ".."):
        return _refuse(logger, old_rel, new_rel, SAME_DIRECTORY_REASON, CODE_NOT_SAME_DIRECTORY)

    if old_rel.lower() == new_rel.lower() and old_rel != new_rel:
        return _refuse(logger, old_rel, new_rel, CASE_ONLY_REASON, CODE_CASE_ONLY)

    old_abs = archive_root / old_rel
    new_abs = archive_root / new_rel

    try:
        stat = os.stat(old_abs)
    except OSError as exc:
        return _refuse(
            logger,
            old_rel,
            new_rel,
            f"pam could not read {old_rel}, so it was left alone.",
            CODE_SOURCE_UNREADABLE,
            detail=exc.strerror,
        )
    if (stat.st_size, stat.st_mtime_ns) != (expect_size, expect_mtime_ns):
        return _refuse(
            logger,
            old_rel,
            new_rel,
            f"{old_rel} changed on disk since pam last read it, so it was left alone."
            " Run pam scan and plan again.",
            CODE_CHANGED_ON_DISK,
        )

    try:
        if names is None:
            taken = _name_in_use(new_abs.parent, new_abs.name)
        else:
            taken = names.taken(new_abs.parent, new_abs.name)
    except OSError as exc:
        return _refuse(
            logger,
            old_rel,
            new_rel,
            f"pam could not read the folder holding {old_rel}, so it was left alone.",
            CODE_SOURCE_UNREADABLE,
            detail=exc.strerror,
        )
    if taken:
        return _refuse(
            logger,
            old_rel,
            new_rel,
            _occupied_reason(old_rel, new_rel, new_abs.name),
            CODE_TARGET_OCCUPIED,
        )
    if names is not None:
        refusal = _confirm_free(names, logger, old_rel, new_rel, new_abs)
        if refusal is not None:
            return refusal

    try:
        # spec 7.0 J8. os.rename, NEVER os.replace: os.replace overwrites an existing
        # target silently and would destroy an irreplaceable file. Windows os.rename
        # refuses an existing target, which is a second line of defence behind the
        # explicit check above - and on POSIX, where os.rename would overwrite, that
        # check is the only line, which is why it is explicit rather than delegated
        # to the filesystem.
        os.rename(old_abs, new_abs)
    except OSError as exc:
        if names is not None:
            names.forget(new_abs.parent)
        return _refuse(
            logger,
            old_rel,
            new_rel,
            f"pam could not rename {old_rel}, so it was left alone.",
            CODE_RENAME_FAILED,
            detail=exc.strerror,
        )

    maybe_fault("after_rename")
    if names is not None:
        # AFTER the checkpoint, deliberately: a crash there is "renamed, memory not yet
        # updated", and the memory dies with the run (see `DirectoryNames`).
        names.renamed(new_abs.parent, old_abs.name, new_abs.name)
    # This wording is load-bearing for two tests, which is unusual enough to say out
    # loud: `test_the_revert_is_attempted_in_reverse_order` reads it out of the session
    # log to prove the ORDER the filesystem was asked to move things in (journal ids
    # are insertion order and read the same either way round), and
    # `test_a_revert_that_fails_...` triggers its interference off it. Both fail LOUDLY
    # if it changes rather than passing on a no-op, so this is a note, not a freeze.
    logger.info("renamed: %s -> %s", old_rel, new_rel)
    return RenameOutcome(ok=True)


def _occupied_reason(old_rel: str, new_rel: str, name: str) -> str:
    """Why the target name could not be used.

    Identity gets its own sentence: when old and new are the same path, the file
    "already there" is the user's own file, and telling them pam refused because
    something is in the way would be untrue.
    """
    if old_rel == new_rel:
        return f"{name} already has the name pam would give it, so it was left alone."
    return f"a file named {name} is already there, and pam never overwrites."


def _confirm_free(
    names: DirectoryNames,
    logger: logging.Logger,
    old_rel: str,
    new_rel: str,
    new_abs: Path,
) -> RenameOutcome | None:
    """X4's "free" check on the disk: `os.lstat` of the target, immediately before the rename.

    Memory said the name is free; this asks the filesystem about the one path. Anything
    there - put there by another process since the folder was listed - is refused with
    check 4's own code and sentence, and the folder is forgotten so its next rename lists
    it fresh. A target that cannot be asked about is refused with an unlistable folder's
    code, and a sentence that names the target.
    """
    try:
        os.lstat(new_abs)
    except FileNotFoundError:
        return None
    except OSError as exc:
        names.forget(new_abs.parent)
        return _refuse(
            logger,
            old_rel,
            new_rel,
            f"pam could not check whether {new_abs.name} is free, so {old_rel} was left alone.",
            CODE_SOURCE_UNREADABLE,
            detail=exc.strerror,
        )
    names.forget(new_abs.parent)
    return _refuse(
        logger,
        old_rel,
        new_rel,
        _occupied_reason(old_rel, new_rel, new_abs.name),
        CODE_TARGET_OCCUPIED,
    )


def _name_in_use(directory: Path, name: str) -> bool:
    """Is `name` taken in `directory`, ignoring case? (spec 6.3.2, spec 5.4 rev 6)

    The directory is listed once and the names are compared lowered, deliberately
    instead of asking `Path.exists()`. `exists()` answers case-insensitively on NTFS
    and case-sensitively on a Linux CI runner, so a safety check written against it
    means two different things depending on where it runs - and the one that matters
    (never overwrite `IMG_1.JPG` with `img_1.jpg`) is the one the CI runner would
    stop testing. This means the same thing everywhere.
    """
    wanted = name.lower()
    with os.scandir(directory) as entries:
        return any(entry.name.lower() == wanted for entry in entries)


def _refuse(
    logger: logging.Logger,
    old_rel: str,
    new_rel: str,
    reason: str,
    code: str,
    *,
    detail: str | None = None,
) -> RenameOutcome:
    """Log the refusal where the session log can show it, and report it upward."""
    logger.warning(
        "rename refused: %s -> %s: %s (%s) %s", old_rel, new_rel, reason, code, detail or ""
    )
    return RenameOutcome(ok=False, reason=reason, code=code, detail=detail)

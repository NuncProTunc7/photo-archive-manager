"""Read-only exiftool wrappers (design spec section 5.3): the batch one and the one-shot.

TWO SURFACES, ONE MODULE, AND BOTH FROZEN. The `-stay_open` batch wrapper below is
spec 5.3's first two frozen argument lists (`FROZEN_ARGS_IMAGE` / `FROZEN_ARGS_VIDEO`).
The one-shot binary extractor at the bottom is spec 5.3's THIRD, which spec 8.2 names
in as many words; it lived in `pam/webui/thumbs.py` until M10 Task 6 and moved here so
that `pam/enrich/` could use it WITHOUT importing `pam/webui/` (M10 clarification 4).
That import would be a layering inversion in a package whose review UI "contains no
code that can rename a file", and the loader for a RAW file needs the same chain the
review overlay uses (spec 8.1 rev 17 / amendment A4).

WHAT MOVED AND WHAT DID NOT. Only the `subprocess.run` and the frozen tag tuples are
here. The thumbnail CACHE, the two VARIANTS, the empty marker and the cache-key format
stay in `thumbs.py`, because they are spec 8.2's and nothing about them is exiftool's:
spec 7.5 check 7 reads the leading 64 hex characters of a cache filename and a
different shape would report the whole cache stale.
"""

import contextlib
import json
import shutil
import subprocess
from pathlib import Path

from pam.errors import ExifToolError

# Frozen, read-only argument lists (spec 5.3, rev 4). extract() sends exactly one
# of these plus the paths; there is no way for a caller to add arguments, so no
# write flag can ever reach exiftool. -n = numeric tags; -charset filename=UTF8 =
# accept UTF-8 path bytes on Windows. Images use -fast2 (skip trailer parsing);
# videos must not - it returns NO QuickTime metadata when the moov atom follows
# mdat (GoPro/DSLR/some Android), exiting 0 with no warning. See spec 5.5.
FROZEN_ARGS_IMAGE: tuple[str, ...] = ("-json", "-n", "-fast2", "-charset", "filename=UTF8")
FROZEN_ARGS_VIDEO: tuple[str, ...] = ("-json", "-n", "-fast", "-charset", "filename=UTF8")
BATCH_LIMIT = 100


def default_command() -> list[str]:
    exe = shutil.which("exiftool")
    if exe is None:
        raise ExifToolError(
            "exiftool was not found on PATH, and scanning needs it to read "
            "photo metadata. Run: pam doctor"
        )
    return [exe]


class ExifTool:
    """One long-lived exiftool process fed batches of paths over stdin."""

    def __init__(self, command: list[str] | None = None):
        self._command = command if command is not None else default_command()
        self._proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> "ExifTool":
        self._proc = subprocess.Popen(
            [*self._command, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.write("-stay_open\nFalse\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
        finally:
            # C8: close the pipes here, or the TextIOWrapper finalizer retries the
            # unflushed write later and prints 'Exception ignored ... OSError' to
            # stderr - directly after pam's own friendly message, undercutting the
            # spec 13 contract at the one moment it is supposed to pay off.
            for pipe in (proc.stdin, proc.stdout):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()

    def extract(
        self, paths: list[Path], media_type: str = "image"
    ) -> dict[str, dict[str, object]]:
        """Metadata per path, keyed by the '/'-separated absolute path string.

        A path with no entry in the result could not be read; the caller
        records the error and continues (design spec section 13).
        """
        results: dict[str, dict[str, object]] = {}
        for start in range(0, len(paths), BATCH_LIMIT):
            self._run_batch(paths[start : start + BATCH_LIMIT], results, media_type=media_type)
        return results

    def _run_batch(
        self,
        batch: list[Path],
        results: dict[str, dict[str, object]],
        media_type: str = "image",
    ) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise ExifToolError("ExifTool must be used as a context manager.")
        try:
            args = FROZEN_ARGS_VIDEO if media_type == "video" else FROZEN_ARGS_IMAGE
            for arg in args:
                proc.stdin.write(arg + "\n")
            for path in batch:
                proc.stdin.write(str(path) + "\n")
            proc.stdin.write("-execute\n")
            proc.stdin.flush()
        except OSError as e:
            raise ExifToolError("exiftool stopped unexpectedly while scanning.") from e
        lines: list[str] = []
        while True:
            line = proc.stdout.readline()
            if line == "":
                raise ExifToolError("exiftool stopped unexpectedly while scanning.")
            if line.startswith("{ready"):
                break
            lines.append(line)
        text = "".join(lines).strip()
        if not text:
            return
        try:
            records = json.loads(text)
        except json.JSONDecodeError:
            return  # whole-batch garbage: callers treat these files as unread
        if not isinstance(records, list):
            return  # valid JSON, wrong shape (e.g. an error object): same contract
        for record in records:
            source = record.get("SourceFile")
            if isinstance(source, str):
                results[source.replace("\\", "/")] = record


# --------------------------------------------------------------------------------------
# Spec 5.3's THIRD frozen surface: one-shot BINARY extraction of an embedded preview.
#
# Moved here from `pam/webui/thumbs.py` at M10 Task 6 (clarification 4). `-b` writes the
# tag's raw bytes to stdout and nothing to the file, and an auditor asking "can a write
# flag reach exiftool?" answers it by reading the constants below - there is no
# caller-supplied argument on this surface either. It is a one-shot `subprocess.run`
# rather than the `-stay_open` wrapper above because binary output through that wrapper's
# JSON protocol is complexity with no payoff at one file per extraction (spec 8.2).
# --------------------------------------------------------------------------------------

# `-b` = write the tag's raw binary value to stdout. It is the only mode in which
# exiftool hands over an embedded JPEG, and it writes nothing anywhere.
BINARY_ARG = "-b"

# THE GRID'S WHOLE ARGUMENT LIST, spelled as one tuple because it is one attempt and
# never becomes two. Spec 8.2: "the grid variant is `-ThumbnailImage` alone and never
# falls back to a larger tag" - a `-PreviewImage` on a RAW is routinely a full-resolution
# JPEG, and a contact sheet of two hundred ~96px cards each pulling one would move
# hundreds of megabytes to draw postage stamps. IT IS DECLARED SEPARATELY FROM
# `PREVIEW_TAGS` rather than sliced out of it, so that merging the two chains has to be
# written on purpose instead of happening as a refactor.
THUMBNAIL_ARGS: tuple[str, ...] = (BINARY_ARG, "-ThumbnailImage")

# THE FALLBACK CHAIN, in spec 8.2's order: the largest embedded preview first, the small
# thumbnail last, so the caller gets the best image the camera left behind and still gets
# something when the small one is all there is. Spec 8.1 rev 17 gives the SAME chain to
# the RAW branch of the image loader, and the order is load-bearing there in a way it is
# not in the overlay: it decides which rendering of the photograph every RAW file's
# perceptual hash is computed over.
PREVIEW_TAGS: tuple[str, ...] = ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage")
PREVIEW_ATTEMPTS: tuple[tuple[str, ...], ...] = tuple((BINARY_ARG, tag) for tag in PREVIEW_TAGS)

# How long ONE extraction may take before it is treated as "no answer" (M7 Task 10
# review, Q4). With no timeout a hung exiftool pins its caller for ever: the review
# server never answers that request and the card spins with no error and no log line.
#
# THE ASYMMETRY IS WHY THIS VALUE CAN BE GENEROUS AND STILL BE RIGHT. Tripping early
# costs a glyph card, which spec 8.2 already treats as an ordinary outcome and which the
# next page load re-attempts; tripping late costs a dead thread. Reading an embedded
# preview is a seek and a few kilobytes - milliseconds, plus ~0.1s of exiftool start-up -
# so 15s is roughly a hundred times the honest cost. THE VALUE IS THE MAINTAINER'S TO RETUNE.
EXTRACT_TIMEOUT_S = 15


def extract_binary(source: Path, attempts: tuple[tuple[str, ...], ...]) -> bytes | None:
    """First non-empty result of `attempts` over `source`; `b""` none; `None` no answer.

    Empty output is exiftool's answer for "this tag is not here": it exits 0 and writes
    nothing, which is indistinguishable from - and means the same thing as - a file that
    is not an image at all. Each attempt is tried in order until one produces bytes, so
    `attempts` IS the chain and its order is the contract.

    `None` IS A THIRD ANSWER. It means some attempt never completed - exiftool was not
    runnable, or it was killed at `EXTRACT_TIMEOUT_S` - so nothing was learned about this
    file. Callers that remember an outcome must not remember this one: writing "no
    preview" because one read of a OneDrive-throttled archive timed out would suppress a
    preview the file really has, permanently. The distinction is between "pam knows there
    is nothing here" and "pam did not find out".

    A chain in which one attempt errors and a later one answers is `b""`, not `None`: a
    completed pass over the rest of the tags still establishes that the ones it read are
    absent. `None` only wins when nothing completed and nothing produced bytes.
    """
    command = default_command()
    complete = True
    for args in attempts:
        data = _run_binary(command, args, source)
        if data:
            return data
        complete = complete and data is not None
    return b"" if complete else None


def _run_binary(command: list[str], args: tuple[str, ...], source: Path) -> bytes | None:
    """One one-shot exiftool call: stdout, `b""` for no such tag, `None` for no answer.

    `check=False` and no interpretation of the exit code: exiftool exits 1 for a file it
    cannot open and 0 with empty output for a file that simply has no such tag, and both
    are the same answer to the only question being asked.

    An `OSError` (the binary moved or is not executable between `default_command()` and
    here) and a `TimeoutExpired` are NOT that answer, and this is the one place the
    difference is visible; see `extract_binary`. `subprocess.run` kills the child and
    reaps it before raising, so a tripped timeout leaves no orphan exiftool behind, which
    is the property that makes swallowing it honest rather than tidy.
    """
    try:
        completed = subprocess.run(
            [*command, *args, str(source)],
            capture_output=True,
            check=False,
            timeout=EXTRACT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout

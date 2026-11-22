r"""The release workflow's own checks, before it downloads or builds anything (M15 Task 14).

    python tools/release_pins.py check [--tag vX.Y.Z]   refuse an unpinned input; print the channel
    python tools/release_pins.py split-lock LOCK OUT     the lock's wheel-only lines, for `pip download`

`check` reads the pins from the environment, where `.github/workflows/release.yml` sets them in ONE
place (its top-level `env:`), and refuses - exit 2, one sentence - unless every SHA-256 is 64
lowercase hex digits and every URL is HTTPS. A pin still holding its placeholder is refused by name:
exiftool's SHA-256 is measured by the owner from the official zip before the first release, and
until then the release cannot run, so it can never ship an exiftool nobody checked. With `--tag` it
also refuses a tag that is not `v` + this checkout's `pam.__version__`, and prints the channel the
version's grammar names (OD-8: `X.Y.ZrcN` is `rc`, `X.Y.Z` is `stable`).

`split-lock` writes the lock minus its one sdist-only package (reverse-geocoder publishes no wheel;
the lock's header says how its wheel is built), so `pip download --only-binary=:all:
--require-hashes` can fetch everything else unchanged, hashes and all. It prints that package's
requirement line with its hashes, for the workflow's `pip wheel` step.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pam.errors import PamError  # noqa: E402
from pam.update.version import parse as parse_version  # noqa: E402

EXIT_OK, EXIT_REFUSED = 0, 2
PLACEHOLDER = "PLACEHOLDER"
HEX64 = re.compile(r"[0-9a-f]{64}")
# Each pin the workflow must carry, by environment variable.
# The four GPL/LGPL source archives (maintainer's decision 2, 2026-11-21) are pinned like the rest.
SOURCES = ("LIBHEIF_SRC", "LIBDE265_SRC", "X265_SRC", "PILLOW_HEIF_SRC")
SHA_PINS = ("EXIFTOOL_SHA256", "INNO_SHA256", *(f"{name}_SHA256" for name in SOURCES))
URL_PINS = ("EXIFTOOL_URL", "INNO_URL", *(f"{name}_URL" for name in SOURCES))
SDIST_ONLY = "reverse-geocoder"


class Refused(Exception):
    pass


def check_pins(env: dict[str, str]) -> None:
    for name in SHA_PINS:
        value = env.get(name, "")
        if PLACEHOLDER in value.upper():
            raise Refused(
                f"{name} is still a placeholder; measure the file and pin its SHA-256 first"
            )
        if not HEX64.fullmatch(value):
            raise Refused(f"{name} is not a SHA-256 (64 lowercase hex digits): {value!r}")
    for name in URL_PINS:
        if not env.get(name, "").startswith("https://"):
            raise Refused(f"{name} must be an https:// URL")


def channel_for(tag: str, version: str) -> str:
    if tag != f"v{version}":
        raise Refused(f"the tag {tag} is not v{version}, the version this checkout builds")
    return "rc" if parse_version(version).rc is not None else "stable"


def split_lock(text: str) -> tuple[str, str]:
    """(the lock without SDIST_ONLY's requirement, that requirement on one line)."""
    kept: list[str] = []
    taken: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith(f"{SDIST_ONLY}=="):
            inside = True
        elif inside and not line.startswith(" "):
            inside = False
        (taken if inside else kept).append(line)
    if not taken:
        raise Refused(f"the lock has no {SDIST_ONLY} requirement to split out")
    parts = [part.strip().rstrip("\\").strip() for part in taken]
    joined = " ".join(part for part in parts if part and not part.startswith("#"))
    return "\n".join(kept) + "\n", joined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--tag")
    split = sub.add_parser("split-lock")
    split.add_argument("lock", type=Path)
    split.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check_pins(dict(os.environ))
            if args.tag:
                import pam

                print(channel_for(args.tag, pam.__version__))
            return EXIT_OK
        kept, line = split_lock(args.lock.read_text(encoding="utf-8"))
        args.out.write_text(kept, encoding="utf-8")
        print(line)
        return EXIT_OK
    except (Refused, PamError, OSError) as error:
        print(f"x {error}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())

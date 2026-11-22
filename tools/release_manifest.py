"""Write `pam-release.json` (E5) for a built installer (M15 Task 9; used by Task 13's release).

    .venv\\Scripts\\python tools\\release_manifest.py INSTALLER --payload DIR --channel stable|rc
        [--version X.Y.Z[rcN]] [--out PATH]

INSTALLER is the built `pam-setup-<version>.exe`; DIR is the payload it was built from (the folder
holding `pam\\catalog\\migrations\\`). `--version` defaults to this checkout's `pam.__version__`;
`--out` to `pam-release.json` beside the installer.

`migrations` is THIS CHECKOUT's `pam/catalog/migrations/` inventory, and the tool REFUSES unless
the payload's inventory is exactly the same set - a stale or hand-edited payload must not ship a
manifest that understates or overstates what it migrates. `sha256` and `size` are read from the
installer. Every field then goes through `pam.update.manifest.validate`, the reader's own rules,
before anything is written. An existing manifest is never overwritten.

Exit 0 written, 2 refused (one sentence on stderr).
"""

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pam  # noqa: E402
from pam.errors import PamError  # noqa: E402
from pam.update import manifest  # noqa: E402

SOURCE = Path(__file__).resolve().parent.parent / "pam" / "catalog" / "migrations"
EXIT_WRITTEN = 0
EXIT_REFUSED = 2


class Refused(Exception):
    pass


def source_inventory(folder: Path = SOURCE) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in folder.iterdir()))


def _payload_inventory(payload: Path) -> tuple[str, ...]:
    folder = payload / "pam" / "catalog" / "migrations"
    if not folder.is_dir():
        raise Refused(f"The payload {payload} has no pam\\catalog\\migrations folder.")
    return tuple(sorted(path.name for path in folder.iterdir()))


def _digest(installer: Path) -> tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with installer.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def build(installer: Path, payload: Path, channel: str, version: str) -> manifest.Manifest:
    migrations = source_inventory()
    shipped = _payload_inventory(payload)
    if shipped != migrations:
        missing = sorted(set(migrations) - set(shipped))
        extra = sorted(set(shipped) - set(migrations))
        raise Refused(
            f"The payload's migrations are not this pam's: missing {missing or 'none'},"
            f" extra {extra or 'none'}. Rebuild the payload from this checkout."
        )
    if not installer.is_file():
        raise Refused(f"There is no installer at {installer}.")
    sha256, size = _digest(installer)
    data = {
        "version": version,
        "channel": channel,
        "installer": installer.name,
        "sha256": sha256,
        "size": size,
        "schema": migrations[-1] if migrations else "",
        "migrations": list(migrations),
    }
    return manifest.validate(data, tag=f"v{version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_manifest")
    parser.add_argument("installer", type=Path)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--version", default=pam.__version__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = args.out or args.installer.parent / "pam-release.json"
    try:
        built = build(args.installer, args.payload, args.channel, args.version)
        if out.exists():
            raise Refused(f"{out} already exists; pam never overwrites a manifest.")
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(manifest.to_json(built))
    except (Refused, PamError) as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"wrote {out}")
    return EXIT_WRITTEN


if __name__ == "__main__":
    sys.exit(main())

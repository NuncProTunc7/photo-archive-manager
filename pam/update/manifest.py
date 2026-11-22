"""E5's release manifest, `pam-release.json` (M15 Task 9; read by `pam update`, Task 12).

`{"version", "channel", "installer", "sha256", "size", "schema", "migrations"}`, every one
required and nothing else admitted. EVERY FIELD AND EVERY CROSS-FIELD RELATION IS VALIDATED HERE,
BEFORE ANY FILESYSTEM USE - this module touches no path at all, so a manifest that names
`..\\x.exe`, a device name or a stream never reaches code that could act on it:
  - `version` is OD-8's grammar and equals the release tag without its `v`;
  - `channel` is `stable` or `rc`, and `rc` exactly when the version is `X.Y.ZrcN` (OD-11);
  - `installer` is exactly `pam-setup-<version>.exe` - an equality, not a pattern, so no
    separator, device name, stream or trailing dot can get through;
  - `sha256` is 64 lowercase hex digits; `size` a whole number of bytes, 1 to the cap;
  - `migrations` is EVERY migration the release ships (E5 rev 2: a skipped release cannot hide
    one), each a valid filename, unique and sorted; and `schema == max(migrations)` (rev 3: "is
    in" permitted an understated level). That the list equals the PAYLOAD's inventory is a
    build-time fact, checked by `tools/release_manifest.py`, which has the payload to read.
A JSON object that names a key twice is refused: last-wins would hide a first value another reader
takes.
"""

import json
from dataclasses import dataclass

from pam.catalog.ledger import MIGRATION_NAME
from pam.errors import PamError
from pam.update.version import Version, VersionError
from pam.update.version import parse as parse_version

FIELDS = ("version", "channel", "installer", "sha256", "size", "schema", "migrations")
CHANNELS = ("stable", "rc")
# No installer pam publishes comes near this; a size above it is a manifest that is wrong.
MAX_INSTALLER_BYTES = 1 << 30
_HEX = frozenset("0123456789abcdef")


class ManifestError(PamError):
    """A release manifest that fails one of E5's rules. The sentence names the rule."""


@dataclass(frozen=True)
class Manifest:
    version: Version
    channel: str
    installer: str
    sha256: str
    size: int
    schema: str
    migrations: tuple[str, ...]


def _refuse(detail: str) -> ManifestError:
    return ManifestError(f"The release manifest {detail}")


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise _refuse(f"names {key} more than once.")
        seen[key] = value
    return seen


def parse(text: str | bytes, *, tag: object) -> Manifest:
    """JSON text (or UTF-8 bytes) to a validated `Manifest`, or `ManifestError`."""
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        data = json.loads(text, object_pairs_hook=_object_without_duplicates)
    except (UnicodeDecodeError, ValueError) as error:
        if isinstance(error, ManifestError):
            raise
        raise _refuse(f"is not JSON ({error}).") from None
    return validate(data, tag=tag)


def validate(data: object, *, tag: object) -> Manifest:
    """`data` as a `Manifest` when every rule holds; otherwise `ManifestError` naming the rule."""
    if not isinstance(data, dict):
        raise _refuse("is not a JSON object.")
    missing = sorted(set(FIELDS) - set(data))
    if missing:
        raise _refuse(f"is missing {', '.join(missing)}.")
    unknown = sorted(str(key) for key in set(data) - set(FIELDS))
    if unknown:
        raise _refuse(f"has unknown {', '.join(unknown)}.")
    version = _version(data["version"], tag)
    channel = _channel(data["channel"], version)
    installer = data["installer"]
    if installer != f"pam-setup-{version}.exe":
        raise _refuse(f"installer {installer!r} is not pam-setup-{version}.exe.")
    sha256 = data["sha256"]
    if not (isinstance(sha256, str) and len(sha256) == 64 and set(sha256) <= _HEX):
        raise _refuse("sha256 is not 64 lowercase hex digits.")
    size = data["size"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 < size <= MAX_INSTALLER_BYTES
    ):
        raise _refuse(
            f"size {size!r} is not a whole number of bytes from 1 to {MAX_INSTALLER_BYTES}."
        )
    migrations = _migrations(data["migrations"])
    schema = data["schema"]
    if schema != migrations[-1]:
        raise _refuse(f"schema {schema!r} is not its newest migration, {migrations[-1]}.")
    return Manifest(version, channel, installer, sha256, size, schema, migrations)


def to_json(manifest: Manifest) -> str:
    """The manifest as `pam-release.json`'s text: fields in E5's order, a final newline."""
    data = {
        "version": str(manifest.version),
        "channel": manifest.channel,
        "installer": manifest.installer,
        "sha256": manifest.sha256,
        "size": manifest.size,
        "schema": manifest.schema,
        "migrations": list(manifest.migrations),
    }
    return json.dumps(data, indent=2) + "\n"


def _version(value: object, tag: object) -> Version:
    try:
        version = parse_version(value)
    except VersionError as error:
        raise _refuse(f"version: {error}") from None
    if tag != f"v{version}":
        raise _refuse(f"version {version} does not match the release tag {tag!r}.")
    return version


def _channel(value: object, version: Version) -> str:
    if value not in CHANNELS:
        raise _refuse(f"channel {value!r} is not stable or rc.")
    if (value == "rc") != version.is_rc:
        raise _refuse(f"says channel {value} for version {version}.")
    return str(value)


def _migrations(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _refuse("migrations is not a non-empty list of migration filenames.")
    for name in value:
        if not isinstance(name, str) or not MIGRATION_NAME.match(name):
            raise _refuse(f"migrations names {name!r}, which is not a migration filename.")
    for earlier, later in zip(value, value[1:], strict=False):
        if earlier == later:
            raise _refuse(f"migrations names {earlier} more than once.")
    if value != sorted(value):
        raise _refuse("migrations are not sorted.")
    return tuple(value)

"""Which release `pam update` offers, and the URLs it may fetch (M15 Task 12; spike 10B, OD-11).

THE REPOSITORY IS A CONSTANT, SPELLED ONCE, HERE (OD-5). pam never takes an owner, a repository,
a tag or a URL from a response: it BUILDS every URL from these constants, the validated tag and a
fixed asset name, and uses GitHub's JSON only to CHECK what it built (10B section 2.3). The private
repository is never named in a constant.

PURE: no network import, no filesystem. `pam/update/fetch.py` is the transport; this module turns
GitHub's release JSON into one admitted `Release`, or a `ReleaseError` sentence:
  - stable: `/releases/latest` - not a draft, not a prerelease, tag `v` + OD-8 with no `rc`;
  - rc: one page of `/releases?per_page=10` (its `Link` is never followed); an entry is admitted
    only if it is not a draft and its `prerelease` flag says what its version says; the offer is
    the highest admitted release of EITHER kind by OD-8's order - never by list position, which is
    GitHub's and not version order (10B F3);
  - the release must hold exactly one `pam-release.json` and one `pam-setup-<version>.exe`, each
    `uploaded`, and each one's `browser_download_url` must equal the URL pam built.
"""

import json
from dataclasses import dataclass

from pam.errors import PamError
from pam.install_method import DOWNLOAD_HOST, OWNER, REPO, WHEEL_PREFIX
from pam.update.version import Version, VersionError
from pam.update.version import parse as parse_version

PUBLIC_RELEASES_URL = f"https://github.com/{OWNER}/{REPO}/releases"

API_HOST = "api.github.com"
ASSET_HOST = "release-assets.githubusercontent.com"

STABLE = "stable"
RC = "rc"
CHANNELS = (STABLE, RC)
DISCOVERY_URLS = {
    STABLE: f"https://{API_HOST}/repos/{OWNER}/{REPO}/releases/latest",
    RC: f"https://{API_HOST}/repos/{OWNER}/{REPO}/releases?per_page=10",
}
# The release `body` (its notes) is the unbounded field; 10B measured ~3.4 KB per release.
DISCOVERY_CAPS = {STABLE: 1 << 20, RC: 2 << 20}
JSON_ACCEPT = "application/vnd.github+json"
BYTES_ACCEPT = "application/octet-stream"

MANIFEST_NAME = "pam-release.json"
MANIFEST_CAP = 64 * 1024  # E5's seven fields are a few hundred bytes

# OWNER, REPO, DISTRIBUTION, DOWNLOAD_HOST and WHEEL_PREFIX live in `pam/install_method.py`, which
# `pam doctor` reads without importing `pam.update` (ALLOWED_UPDATE_IMPORTS).
PLACEHOLDER_VERSION = "X.Y.Z"


class ReleaseError(PamError):
    """GitHub's answer is not a release pam can offer. The sentence says why."""


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    manifest_url: str
    installer_url: str
    installer_size: int


def download_url(tag: str, name: str) -> str:
    """The one download URL pam uses (never the API asset URL, never `releases/latest/download`)."""
    return f"https://{DOWNLOAD_HOST}/{OWNER}/{REPO}/releases/download/{tag}/{name}"


def installer_name(version: Version) -> str:
    return f"pam-setup-{version}.exe"


def wheel_name(version: str) -> str:
    """The wheel asset of the release `v<version>` (maintainer's decision 1, 2026-11-21)."""
    return f"{WHEEL_PREFIX}-{version}-py3-none-any.whl"


# The pip route for a pam the installer did not put on this PC (OD-12, AMENDED by the maintainer's
# decision 1 of 2026-11-21): pip installs the release's own wheel asset. The git URL it replaces
# failed on a default Windows setup (pip's clone of a test fixture exceeds MAX_PATH). The version
# is the user's to choose: this route makes no network request, so pam cannot know the newest.
PIP_ROUTE = (
    "pip install --upgrade"
    f' "{download_url("v" + PLACEHOLDER_VERSION, wheel_name(PLACEHOLDER_VERSION))}"'
)


def offer(body: bytes, channel: str) -> Release:
    """The release `channel` offers, from the discovery response's body, bound to its assets."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ReleaseError("GitHub's answer about pam's releases is not JSON.") from None
    if channel == STABLE:
        candidates = [data]
    elif isinstance(data, list):
        candidates = data[:10]
    else:
        raise ReleaseError("GitHub's list of pam's releases is not a list.")
    admitted = [(version, entry) for entry in candidates if (version := _admit(entry, channel))]
    if not admitted:
        raise ReleaseError(
            f"GitHub offers no pam release pam can read on the {channel} channel."
        )
    version, entry = max(admitted, key=lambda pair: pair[0])
    return _bind(entry, version)


def _admit(entry: object, channel: str) -> Version | None:
    """The entry's version when it may be offered on `channel`, else None."""
    if not isinstance(entry, dict) or entry.get("draft") is not False:
        return None
    tag = entry.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    try:
        version = parse_version(tag[1:])
    except VersionError:
        return None
    if entry.get("prerelease") is not version.is_rc:
        return None
    if channel == STABLE and version.is_rc:
        return None
    return version


def _bind(entry: dict[str, object], version: Version) -> Release:
    """10B section 2.3: exactly one manifest and one installer, uploaded, at the URLs pam built."""
    tag = f"v{version}"
    assets = entry.get("assets")
    if not isinstance(assets, list):
        assets = []
    urls: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for name in (MANIFEST_NAME, installer_name(version)):
        found = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        if len(found) != 1:
            raise ReleaseError(
                f"The release {tag} on GitHub has {len(found)} files named {name}, not one,"
                " so pam will not install it."
            )
        asset = found[0]
        url = download_url(tag, name)
        if asset.get("state") != "uploaded" or asset.get("browser_download_url") != url:
            raise ReleaseError(
                f"The release {tag} on GitHub names {name} somewhere other than {url},"
                " or has not finished uploading it, so pam will not install it."
            )
        size = asset.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ReleaseError(f"The release {tag} on GitHub gives {name} no size.")
        urls[name], sizes[name] = url, size
    installer = installer_name(version)
    return Release(tag, version, urls[MANIFEST_NAME], urls[installer], sizes[installer])

"""`pam update` (M15 Task 12, Step 3; E2, OD-11, OD-12, E11, E12) against a fake GitHub on 127.0.0.1.

The transport is REAL - `tls_server.TlsServer` speaks verified TLS for E3's three names, reached
through the injected resolver - and every other effect is a seam the test watches: the download is
the request for the installer reaching the server, the launch is a spy, the staging folder comes
from a spy. "Never downloads" and "never launches" are asserted on THOSE CALLS, not on an outcome
(CLAUDE.md, M697: a test that asserts the outcome is unchanged cannot tell "never did it" from "did
it and left no trace").

The expectations are spelled from the brief, the rulings and the amendment record, never read back
from `pam/update/`: the URLs, the migration names, the sentences.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import tls_server
from click.testing import CliRunner
from tls_server import CA_FILE, TlsServer

from pam import install_method, run_registry
from pam.catalog import ledger
from pam.cli import cli
from pam.errors import LedgerError, PamError, RunRegistryError
from pam.update import command
from pam.update import release as release_module
from pam.update.command import UpdateError, confirm_update, run_update
from pam.update.fetch import Transport
from pam.update.leftovers import reap_leftovers

# The repository, spelled from the maintainer's ruling (OD-5), not from `pam/update/release.py`.
REPO_PATH = "/repos/NuncProTunc7/photo-archive-manager"
STABLE_PATH = f"{REPO_PATH}/releases/latest"
RC_PATH = f"{REPO_PATH}/releases?per_page=10"
DOWNLOAD_PREFIX = "/NuncProTunc7/photo-archive-manager/releases/download"
RELEASES_PAGE = "https://github.com/NuncProTunc7/photo-archive-manager/releases"
# What this pam ships at schema 0008 (design 4), spelled by hand.
SHIPPED = tuple(
    f"{n:04d}_{name}.sql"
    for n, name in enumerate(
        [
            "init",
            "scanner",
            "video_reread",
            "plan_unit_key",
            "plan_already_conforming",
            "event_label_source",
            "face_review_state",
            "files_rel_path_nocase",
        ],
        start=1,
    )
)
NEW_9 = "0009_people_index.sql"
NEW_10 = "0010_place_cache.sql"
# Task 12-G, F4: the E12 refusal in the maintainer's words (2026-11-20), spelled here, never read from
# `pam/catalog/ledger.py`. No migration number appears in it.
NEEDS_NEWER_REFUSAL = (
    "pam update will not install pam 1.1.0: a newer pam has already updated a catalog on this PC,"
    " and pam 1.1.0 could not open it. Nothing was changed. If you no longer use any catalog a"
    " newer pam opened, delete {path} and try again."
)


class FakeGitHub:
    """Publishes releases onto a `TlsServer` the way 10B measured GitHub serves them."""

    def __init__(self, server: TlsServer):
        self.server = server
        self.releases: list[dict] = []

    def publish(
        self,
        version,
        *,
        prerelease=None,
        draft=False,
        installer=None,
        migrations=SHIPPED,
        manifest_sha=None,
        manifest_size=None,
        asset_size=None,
        omit=(),
    ):
        tag = f"v{version}"
        rc = "rc" in version
        installer = installer if installer is not None else f"MZ installer {version}".encode()
        name = f"pam-setup-{version}.exe"
        manifest = {
            "version": version,
            "channel": "rc" if rc else "stable",
            "installer": name,
            "sha256": manifest_sha or hashlib.sha256(installer).hexdigest(),
            "size": manifest_size if manifest_size is not None else len(installer),
            "schema": migrations[-1],
            "migrations": list(migrations),
        }
        manifest_bytes = json.dumps(manifest).encode()
        assets = []
        for asset_name, body in (("pam-release.json", manifest_bytes), (name, installer)):
            if asset_name in omit:
                continue
            path = f"{DOWNLOAD_PREFIX}/{tag}/{asset_name}"
            target = f"/github-production-release-asset/1/{tag}-{asset_name}?sig=s"
            self.server.route(
                "github.com",
                path,
                tls_server.redirect(f"https://release-assets.githubusercontent.com{target}"),
            )
            self.server.route(
                "release-assets.githubusercontent.com", target, tls_server.ok(body)
            )
            size = asset_size if (asset_size is not None and asset_name == name) else len(body)
            assets.append(
                {
                    "name": asset_name,
                    "state": "uploaded",
                    "size": size,
                    "browser_download_url": f"https://github.com{path}",
                    "url": f"https://api.github.com{REPO_PATH}/releases/assets/1",
                }
            )
        release = {
            "tag_name": tag,
            "draft": draft,
            "prerelease": rc if prerelease is None else prerelease,
            "assets": assets,
            "body": "release notes",
        }
        self.releases.append(release)
        return release

    def serve_latest(self, release):
        self.server.route(
            "api.github.com", STABLE_PATH, tls_server.ok(json.dumps(release).encode())
        )

    def serve_list(self):
        body = json.dumps(self.releases).encode()
        self.server.route("api.github.com", RC_PATH, tls_server.ok(body))

    def installer_requests(self):
        return [
            r
            for r in self.server.requests
            if r.host == "github.com" and r.path.endswith(".exe")
        ]

    def api_requests(self):
        return [r for r in self.server.requests if r.host == "api.github.com"]


@pytest.fixture
def github():
    with TlsServer() as server:
        yield FakeGitHub(server)


class Run:
    """`run_update` with every effect a spy. `outcome()` runs it and records what happened."""

    def __init__(self, github, tmp_path):
        self.github = github
        self.lines: list[str] = []
        self.launched: list[Path] = []
        self.staged: list[Path] = []
        self.awaited: list[object] = []
        self.confirmed = 0
        self.reaped: list[int] = []  # Task 12-G: what the server had been asked when reap ran
        self.reap_count = 0
        self.tmp_path = tmp_path
        self.kwargs = dict(
            transport=Transport(resolver=github.server.resolver, cafile=CA_FILE),
            confirm=self._confirm,
            launch=self._launch,
            staging=self._staging,
            await_setup=self._await,
            another_pam_running=lambda: False,
            reap=self._reap,  # never the real TEMP: the default is pinned by its own test
            install_state=lambda: install_method.Provenance(install_method.INSTALLER, tmp_path),
            ledger_level=lambda: None,
            platform="win32",
            installed="1.0.0",
            shipped=SHIPPED,
            echo=self.lines.append,
        )
        self.answer = True

    def _confirm(self):
        self.confirmed += 1
        return self.answer

    def _reap(self):
        self.reaped.append(len(self.github.server.lookups))
        return self.reap_count

    def _launch(self, path):
        self.launched.append(path)
        assert path.read_bytes()  # the installer is on disk when it is launched
        return FakeChild()

    def _staging(self):
        folder = Path(tempfile.mkdtemp(prefix="pam-update-", dir=self.tmp_path))
        self.staged.append(folder)
        return folder

    def _await(self, child_exited):
        self.awaited.append(child_exited)
        return True

    def __call__(self, check=False, channel="stable", **overrides):
        return run_update(check=check, channel=channel, **{**self.kwargs, **overrides})

    def nothing_downloaded_or_launched(self):
        return (
            self.github.installer_requests() == [] and self.launched == [] and self.staged == []
        )


class FakeChild:
    def poll(self):
        return None


@pytest.fixture
def run(github, tmp_path):
    return Run(github, tmp_path)


# --- step 1: how pam was installed ----------------------------------------------------------

PIP_LINES = [
    "This pam was not installed by pam's installer, so pam update does not replace it."
    " Update it with pip:",
    '  pip install --upgrade "https://github.com/NuncProTunc7/photo-archive-manager/releases/'
    'download/vX.Y.Z/photo_archive_manager-X.Y.Z-py3-none-any.whl"',
    "Put the version you want in place of X.Y.Z, both times. The releases are listed at"
    f" {RELEASES_PAGE}",
]


@pytest.mark.parametrize(
    ("platform", "method"),
    [
        ("win32", install_method.NO_RECORD),
        ("linux", install_method.NO_RECORD),
        ("darwin", install_method.INSTALLER),
        ("linux", install_method.MALFORMED),
    ],
)
def test_a_pam_the_installer_did_not_put_here_is_told_the_pip_route_and_nothing_else(
    run, platform, method
):
    """OD-12's route as AMENDED by the owner 2026-11-21: the release's own wheel asset, pinned
    exactly (never `pip install --upgrade pam`: the PyPI name `pam` is an unrelated project; never
    the git URL, whose clone exceeds MAX_PATH on a default Windows setup). No lookup, no request."""
    state = install_method.Provenance(method, None)
    assert run(platform=platform, install_state=lambda: state) == 0
    assert run.lines == PIP_LINES
    assert run.github.server.lookups == [] and run.github.server.requests == []
    assert "pip install --upgrade pam" not in "\n".join(run.lines)


@pytest.mark.parametrize(
    ("version", "name"),
    [
        ("1.0.0", "photo_archive_manager-1.0.0-py3-none-any.whl"),
        ("1.0.0rc1", "photo_archive_manager-1.0.0rc1-py3-none-any.whl"),
    ],
)
def test_the_wheel_asset_name_is_the_one_pip_wheel_writes(version, name):
    """Spelled from `pip wheel` of this tree at 1.0.0 and at 1.0.0rc1 (Task 15W, 2026-11-21): PEP
    427 escapes the distribution's `-` to `_`, and an rc keeps `rc` with no separator."""
    assert release_module.wheel_name(version) == name


@pytest.mark.parametrize("doc", ["docs/user-guide.md", "public/README.md"])
def test_the_documents_show_the_route_pam_prints_and_never_the_git_url(doc):
    """Task 15W: the user guide and the public README carry PIP_LINES' command, word for word."""
    root = Path(__file__).resolve().parent.parent
    # In this repository the public README is `public/README.md`; the export writes it at the root
    # (`tools/export_public.py`), so in the public repository it is `README.md` (M15 Task 15B, F6).
    if doc.startswith("public/") and not (root / "public").is_dir():
        doc = doc.removeprefix("public/")
    text = (root / doc).read_text(encoding="utf-8")
    assert PIP_LINES[1].strip() in text
    assert "git+https" not in text and "core.longpaths" not in text


def test_a_release_carrying_the_wheel_and_the_source_archives_is_still_offered(run):
    """The release workflow uploads five more assets beside the two pam binds (maintainer's decisions of
    2026-11-21); pam binds its two by exact name and ignores the rest."""
    release = run.github.publish("1.1.0")
    for name in (
        "photo_archive_manager-1.1.0-py3-none-any.whl",
        "libheif-1.23.6.tar.gz",
        "libde265-1.1.3.tar.gz",
        "x265-4.3.tar.gz",
        "pillow_heif-1.8.1.tar.gz",
    ):
        url = f"https://github.com{DOWNLOAD_PREFIX}/v1.1.0/{name}"
        release["assets"].append(
            {"name": name, "state": "uploaded", "size": 1, "browser_download_url": url}
        )
    run.github.serve_latest(release)
    assert run(check=True) == 0
    assert run.lines[:2] == ["Installed: pam 1.0.0", "Offered:   pam 1.1.0 (stable)"]
    assert run.nothing_downloaded_or_launched()


def test_a_malformed_install_record_is_refused_with_no_guessed_command_and_no_network(run):
    state = install_method.Provenance(install_method.MALFORMED, None)
    with pytest.raises(UpdateError) as caught:
        run(install_state=lambda: state)
    assert str(caught.value) == (
        "pam cannot tell how it was installed. Reinstall it from"
        " https://github.com/NuncProTunc7/photo-archive-manager/releases."
    )
    assert run.lines == [] and run.github.server.lookups == []


# --- step 2: another pam -----------------------------------------------------------------------


def test_another_pam_running_is_refused_before_any_request(run):
    with pytest.raises(UpdateError, match="another pam is running"):
        run(another_pam_running=lambda: True)
    assert run.github.server.lookups == [] and run.nothing_downloaded_or_launched()


def test_a_pam_started_while_the_prompt_waits_is_refused_before_the_download(run):
    """F2 (Task 12 Stage 2): the prompt is a human-length wait. A pam that registers during it is
    seen by a SECOND look after the typed confirmation - before any installer byte is fetched."""
    looks = []

    def started_during_the_prompt():
        looks.append(run.confirmed)
        return run.confirmed > 0  # nobody before the prompt; someone once it was answered

    run.github.serve_latest(run.github.publish("1.1.0"))
    with pytest.raises(UpdateError) as refused:
        run(another_pam_running=started_during_the_prompt)
    assert str(refused.value) == (
        "pam cannot update now: another pam is running. Close it (and stop pam start), then run"
        " pam update again. Nothing was changed."
    )
    assert looks == [0, 1] and run.confirmed == 1
    assert run.nothing_downloaded_or_launched()


def test_a_registry_pam_cannot_read_refuses_too(run):
    def cannot_tell():
        raise RunRegistryError("pam cannot tell whether another pam is running (denied).")

    with pytest.raises(RunRegistryError):
        run(another_pam_running=cannot_tell)
    assert run.github.server.lookups == []


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="E11's registry protects Windows only"
)
def test_the_default_exclusion_reads_the_real_run_registry(run, isolated_run_registry):
    """A held `run\\<pid>.lock` of ANOTHER pid, through the production default - not a stub."""
    folder = run_registry.registry_root() / run_registry.RUN_DIR
    folder.mkdir(parents=True, exist_ok=True)
    handle = run_registry._hold(folder / "424242.lock", 1.0)
    try:
        kwargs = dict(run.kwargs)
        del kwargs["another_pam_running"]
        with pytest.raises(UpdateError, match="another pam is running"):
            run_update(check=True, **kwargs)
    finally:
        run_registry._close(handle)
    assert run.github.server.lookups == []


# --- steps 3-6: discover, compare, name ---------------------------------------------------------


def test_check_names_both_versions_and_downloads_nothing(run):
    run.github.serve_latest(run.github.publish("1.1.0", migrations=(*SHIPPED, NEW_9)))
    assert run(check=True) == 0
    assert run.lines == [
        "Installed: pam 1.0.0",
        "Offered:   pam 1.1.0 (stable)",
        f"It updates each catalog it opens ({NEW_9}).",
        "After that, pam 1.0.0 cannot open those catalogs.",
        "Next: pam update",
    ]
    assert run.nothing_downloaded_or_launched() and run.confirmed == 0
    # 10B: --check costs exactly one rate-charged request on the stable channel.
    assert [r.path for r in run.github.api_requests()] == [STABLE_PATH]


def test_an_offer_with_no_new_migration_says_so(run):
    run.github.serve_latest(run.github.publish("1.0.1"))
    assert run(check=True) == 0
    assert run.lines[2] == "It makes no change to your catalogs."


def test_the_same_version_is_up_to_date_and_nothing_follows(run):
    run.github.serve_latest(run.github.publish("1.0.0"))
    assert run() == 0
    assert run.lines == ["pam is up to date (1.0.0)."]
    assert run.nothing_downloaded_or_launched() and run.confirmed == 0


def test_an_older_release_is_up_to_date_too(run):
    run.github.serve_latest(run.github.publish("0.9.0"))
    assert run(installed="1.0.0") == 0
    assert run.lines == ["pam is up to date (1.0.0)."]
    assert run.nothing_downloaded_or_launched()


def test_a_prerelease_moves_onto_its_stable_release(run):
    run.github.serve_latest(run.github.publish("1.0.0"))
    assert run(check=True, installed="1.0.0rc2") == 0
    assert run.lines[:2] == ["Installed: pam 1.0.0rc2", "Offered:   pam 1.0.0 (stable)"]


def test_a_skipped_release_still_names_every_migration_this_pam_lacks(run):
    """1.1.0 added 0009 and 1.2.0 added 0010; a 1.0.0 user offered 1.2.0 hears about both."""
    run.github.serve_latest(run.github.publish("1.2.0", migrations=(*SHIPPED, NEW_9, NEW_10)))
    assert run(check=True) == 0
    assert f"It updates each catalog it opens ({NEW_9}, {NEW_10})." in run.lines


def test_the_production_default_names_the_migrations_this_pam_really_ships(run):
    run.github.serve_latest(run.github.publish("1.1.0", migrations=(*SHIPPED, NEW_9)))
    kwargs = dict(run.kwargs)
    del kwargs["shipped"]
    assert run_update(check=True, **kwargs) == 0
    assert f"It updates each catalog it opens ({NEW_9})." in run.lines


def test_the_rc_channel_offers_the_highest_admitted_release_of_either_kind(run):
    """One page, never by list position; a draft, a mis-flagged entry and a bad tag are skipped."""
    run.github.publish("1.1.0rc1")
    run.github.publish("1.2.0", draft=True)
    run.github.publish("1.1.0rc3", prerelease=False)  # flag and version disagree
    run.github.publish("1.1.0rc2")
    run.github.publish("1.0.1")
    run.github.releases.append({"tag_name": "1.9.0", "draft": False, "prerelease": False})
    run.github.serve_list()
    assert run(check=True, channel="rc") == 0
    assert run.lines[1] == "Offered:   pam 1.1.0rc2 (rc)"
    assert run.lines[-1] == "Next: pam update --channel rc"
    assert [r.path for r in run.github.api_requests()] == [RC_PATH]


def test_the_rc_channel_moves_onto_a_stable_release_above_its_candidates(run):
    run.github.publish("1.1.0rc2")
    run.github.publish("1.1.0")
    run.github.serve_list()
    assert run(check=True, channel="rc") == 0
    assert run.lines[1] == "Offered:   pam 1.1.0 (stable)"


def test_the_stable_channel_refuses_a_latest_that_is_a_prerelease(run):
    run.github.serve_latest(run.github.publish("1.1.0rc1"))
    with pytest.raises(PamError, match="no pam release pam can read on the stable channel"):
        run(check=True)


@pytest.mark.parametrize(
    ("level", "refused"),
    [("0007_face_review_state.sql", False), ("0009_people_index.sql", False), (NEW_10, True)],
)
def test_a_release_below_the_compatibility_ledger_is_refused(run, level, refused):
    """E12: refused when the offered schema sorts BELOW the ledger's; equal is allowed."""
    run.github.serve_latest(run.github.publish("1.1.0", migrations=(*SHIPPED, NEW_9)))
    if not refused:
        assert run(check=True, ledger_level=lambda: level) == 0
        return
    with pytest.raises(UpdateError) as refused:
        run(ledger_level=lambda: level)
    assert str(refused.value) == NEEDS_NEWER_REFUSAL.format(path=ledger.ledger_path())
    assert "0010" not in str(refused.value) and "pam 00" not in str(refused.value)
    assert run.nothing_downloaded_or_launched() and run.confirmed == 0


# --- the refusals GitHub can cause --------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "headers", "sentence"),
    [
        (404, {}, "GitHub has no pam release to offer. Nothing was changed."),
        (500, {}, "GitHub answered HTTP 500; try again later. Nothing was changed."),
        (503, {}, "GitHub answered HTTP 503; try again later. Nothing was changed."),
        (
            403,
            {"x-ratelimit-remaining": "0"},
            "GitHub allows 60 release checks an hour from this PC, and they are used up."
            " Try again in an hour. Nothing was changed.",
        ),
    ],
)
def test_github_refusals_are_named_and_change_nothing(run, code, headers, sentence):
    run.github.server.route("api.github.com", STABLE_PATH, tls_server.status(code, headers))
    with pytest.raises(UpdateError) as caught:
        run(check=True)
    assert str(caught.value) == sentence
    assert run.nothing_downloaded_or_launched()


def test_a_rate_limit_names_the_reset_time(run):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}
    run.github.server.route("api.github.com", STABLE_PATH, tls_server.status(429, headers))
    with pytest.raises(UpdateError, match=r"Try again after \d\d:\d\d\. Nothing was changed\."):
        run(check=True)


@pytest.mark.parametrize("missing", ["pam-release.json", "pam-setup-1.1.0.exe"])
def test_a_release_missing_an_asset_is_refused(run, missing):
    run.github.serve_latest(run.github.publish("1.1.0", omit=(missing,)))
    with pytest.raises(PamError, match=f"has 0 files named {missing}"):
        run()
    assert run.nothing_downloaded_or_launched()


def test_an_asset_url_other_than_the_one_pam_builds_is_refused(run):
    release = run.github.publish("1.1.0")
    release["assets"][1]["browser_download_url"] = "https://github.com/someone/else/x.exe"
    run.github.serve_latest(release)
    with pytest.raises(PamError, match="names pam-setup-1.1.0.exe somewhere other than"):
        run()
    assert run.nothing_downloaded_or_launched()


def test_a_manifest_size_that_is_not_the_assets_is_refused(run):
    run.github.serve_latest(run.github.publish("1.1.0", asset_size=999))
    with pytest.raises(UpdateError, match="in its manifest and 999 on GitHub"):
        run()
    assert run.nothing_downloaded_or_launched()


# --- steps 7-9: confirm, download, launch -------------------------------------------------------


def test_a_cancelled_prompt_downloads_and_launches_nothing(run):
    run.github.serve_latest(run.github.publish("1.1.0"))
    run.answer = False
    assert run() == 0
    assert run.lines[-1] == "Nothing was changed."
    assert run.confirmed == 1 and run.nothing_downloaded_or_launched()


def test_a_confirmed_update_downloads_checks_and_launches_from_the_staging_folder(run):
    installer = b"MZ" + bytes(range(256)) * 300
    run.github.serve_latest(run.github.publish("1.1.0", installer=installer))
    assert run() == 0
    (folder,) = run.staged
    assert run.launched == [folder / "pam-setup-1.1.0.exe"]
    assert run.launched[0].read_bytes() == installer
    assert len(run.github.installer_requests()) == 1
    assert len(run.awaited) == 1 and run.awaited[0]() is False  # FakeChild has not exited
    assert run.lines[-2:] == [
        "Checked: the size and SHA-256 match the release manifest.",
        "The installer is starting. pam closes now; the installer finishes the update.",
    ]


def test_a_hash_mismatch_deletes_the_staging_folder_and_launches_nothing(run):
    run.github.serve_latest(run.github.publish("1.1.0", manifest_sha="0" * 64))
    with pytest.raises(UpdateError, match="does not match the release manifest's SHA-256"):
        run()
    (folder,) = run.staged
    assert not folder.exists() and run.launched == []


def test_a_release_republished_between_the_manifest_and_the_installer_is_refused(run):
    """The installer served is a different build of the same size: the hash catches it."""
    release = run.github.publish("1.1.0", installer=b"MZ build one")
    run.github.serve_latest(release)
    path = f"{DOWNLOAD_PREFIX}/v1.1.0/pam-setup-1.1.0.exe"
    target = "/github-production-release-asset/1/republished?sig=s"
    run.github.server.route(
        "github.com",
        path,
        tls_server.redirect(f"https://release-assets.githubusercontent.com{target}"),
    )
    run.github.server.route(
        "release-assets.githubusercontent.com", target, tls_server.ok(b"MZ build two")
    )
    with pytest.raises(UpdateError, match="SHA-256"):
        run()
    (folder,) = run.staged
    assert not folder.exists() and run.launched == []


def test_a_short_download_deletes_the_staging_folder(run):
    run.github.serve_latest(run.github.publish("1.1.0", installer=b"MZ" * 50))
    path = "/github-production-release-asset/1/v1.1.0-pam-setup-1.1.0.exe?sig=s"
    run.github.server.route(
        "release-assets.githubusercontent.com", path, tls_server.truncated(b"MZ" * 10, 100)
    )
    with pytest.raises(UpdateError, match="pam deleted the partial download"):
        run()
    (folder,) = run.staged
    assert not folder.exists() and run.launched == []


def test_an_interrupted_download_deletes_the_staging_folder_and_still_interrupts(run):
    run.github.serve_latest(run.github.publish("1.1.0"))
    real = run.kwargs["transport"]

    class Interrupting:
        def get(self, url, **kwargs):
            if kwargs.get("sink") is not None:
                raise KeyboardInterrupt
            return real.get(url, **kwargs)

    with pytest.raises(KeyboardInterrupt):
        run(transport=Interrupting())
    (folder,) = run.staged
    assert not folder.exists() and run.launched == []


def test_a_launch_that_fails_deletes_the_download(run):
    run.github.serve_latest(run.github.publish("1.1.0"))

    def cannot(path):
        raise OSError("blocked")

    with pytest.raises(UpdateError, match="could not start the installer"):
        run(launch=cannot)
    (folder,) = run.staged
    assert not folder.exists()


def test_the_staging_folder_is_a_fresh_mkdtemp_named_pam_update(monkeypatch, tmp_path):
    made = []

    def spy(prefix=None, **kwargs):
        made.append(prefix)
        return str(tmp_path / "x")

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    assert command.make_staging() == tmp_path / "x"
    assert made == ["pam-update-"]


def test_the_installer_is_launched_detached_with_restart_manager_off_and_no_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)) or "child")
    assert command.launch_installer(Path("C:/x/pam-setup-1.1.0.exe")) == "child"
    ((args, kwargs),) = calls
    assert args == (
        [
            str(Path("C:/x/pam-setup-1.1.0.exe")),
            "/NOCLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
            f"/UPDATERPID={os.getpid()}",  # 10C: this pam, by the interpreter's own pid
        ],
    )
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    assert kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    ("typed", "expected"), [("update", True), ("Update", False), ("yes", False), ("", False)]
)
def test_only_the_word_update_confirms(monkeypatch, typed, expected):
    monkeypatch.setattr("builtins.input", lambda: typed)
    assert confirm_update() is expected


def test_a_closed_stdin_cancels(monkeypatch):
    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert confirm_update() is False


# --- the command line -----------------------------------------------------------------------


def test_pam_update_from_the_command_line_takes_the_pip_route_in_a_venv(monkeypatch):
    """The suite runs in a venv - not the installed layout - so the real defaults print the pip
    route and make no request; the command is wired to `run_update` through the lazy import."""
    result = CliRunner().invoke(cli, ["update", "--check"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == PIP_LINES


def test_the_channel_option_admits_only_stable_and_rc():
    result = CliRunner().invoke(cli, ["update", "--channel", "beta"])
    assert result.exit_code == 2


# --- Stage 1 review closures (M15 Task 12): each a survivor of the review's mutation pass --------


def test_an_asset_that_has_not_finished_uploading_is_refused(run):
    """10B 2.3: each asset in state `uploaded` - an `uploaded` URL alone is not enough (M4333)."""
    release = run.github.publish("1.1.0")
    release["assets"][1]["state"] = "starter"
    run.github.serve_latest(release)
    with pytest.raises(PamError, match="or has not finished uploading it"):
        run()
    assert run.nothing_downloaded_or_launched()


def test_a_release_with_two_files_of_one_name_is_refused(run):
    """10B 2.3: EXACTLY one manifest and one installer - a second of either is refused (M4334)."""
    release = run.github.publish("1.1.0")
    release["assets"].append(dict(release["assets"][0]))
    run.github.serve_latest(release)
    with pytest.raises(PamError, match="has 2 files named pam-release.json, not one"):
        run()
    assert run.nothing_downloaded_or_launched()


@pytest.mark.parametrize(
    "lookalikes",
    [
        ("pam-setup-1.1.0.exe.sha256", "pam-release.json.sig"),
        ("Pam-Setup-1.1.0.exe", "PAM-RELEASE.JSON"),
        ("pam-setup-1.1.0.exe ", "x-pam-release.json"),
    ],
)
def test_a_release_carrying_lookalike_names_binds_only_the_exact_two(run, lookalikes):
    """Task 15W review (M5306): a release now carries seven assets, and may carry more (a checksum,
    a signature). pam binds its two by EXACT name - a name that only begins with, differs in case
    from, or merely contains one of them is ignored, never a second match and never the file bound."""
    release = run.github.publish("1.1.0")
    for name in lookalikes:
        url = f"https://github.com{DOWNLOAD_PREFIX}/v1.1.0/{name}"
        release["assets"].append(
            {"name": name, "state": "uploaded", "size": 1, "browser_download_url": url}
        )
    run.github.serve_latest(release)
    assert run(check=True) == 0
    assert run.lines[:2] == ["Installed: pam 1.0.0", "Offered:   pam 1.1.0 (stable)"]
    assert run.nothing_downloaded_or_launched()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone/else/releases/download/v1.1.0/pam-setup-1.1.0.exe",
        f"https://github.com{DOWNLOAD_PREFIX}/v1.0.9/pam-setup-1.1.0.exe",
        f"https://github.com{DOWNLOAD_PREFIX}/v1.1.0/pam-setup-1.1.0.exe?x=1",
    ],
)
def test_an_asset_url_that_only_resembles_the_one_pam_builds_is_refused(run, url):
    """The binding is EQUALITY with the URL pam builds: the right file name under another
    repository, under another tag, or with a query is refused (M4335)."""
    release = run.github.publish("1.1.0")
    release["assets"][1]["browser_download_url"] = url
    run.github.serve_latest(release)
    with pytest.raises(PamError, match="names pam-setup-1.1.0.exe somewhere other than"):
        run()
    assert run.nothing_downloaded_or_launched()


@pytest.mark.parametrize("size", ["missing", None, 0, -1, True, "18"])
def test_an_installer_asset_with_no_usable_size_is_refused(run, size):
    """The asset's own size must be a positive whole number before it is compared (M4336)."""
    release = run.github.publish("1.1.0")
    if size == "missing":
        del release["assets"][1]["size"]
    else:
        release["assets"][1]["size"] = size
    run.github.serve_latest(release)
    with pytest.raises(PamError, match="gives pam-setup-1.1.0.exe no size"):
        run()
    assert run.nothing_downloaded_or_launched()


def test_a_download_shorter_than_the_manifest_with_an_honest_length_is_refused_by_size(run):
    """GitHub says 20 bytes and sends 20, so the transport is satisfied; the manifest says 100. The
    SIZE check refuses it, by name, before the hash, and the folder is deleted (M4338)."""
    run.github.serve_latest(run.github.publish("1.1.0", installer=b"MZ" * 50))
    path = "/github-production-release-asset/1/v1.1.0-pam-setup-1.1.0.exe?sig=s"
    run.github.server.route(
        "release-assets.githubusercontent.com", path, tls_server.ok(b"MZ" * 10)
    )
    with pytest.raises(UpdateError, match="The download is 20 bytes, not the 100"):
        run()
    (folder,) = run.staged
    assert not folder.exists() and run.launched == []


def test_an_unreadable_compatibility_ledger_refuses_before_any_download(run):
    """E12: an unknown constraint is refused, never read as none (M4355)."""
    run.github.serve_latest(run.github.publish("1.1.0", migrations=(*SHIPPED, NEW_9)))

    def unreadable():
        raise LedgerError("pam cannot read the compatibility ledger (denied).")

    with pytest.raises(LedgerError):
        run(ledger_level=unreadable)
    assert run.nothing_downloaded_or_launched() and run.confirmed == 0


def test_the_production_default_reads_the_real_compatibility_ledger(
    run, isolated_schema_ledger
):
    """The default `ledger_level` is the real E12 ledger (this test's isolated one): a catalog at
    0010 refuses a release whose newest migration is 0009 (M4356)."""
    isolated_schema_ledger.parent.mkdir(parents=True, exist_ok=True)
    isolated_schema_ledger.write_text(f"{NEW_10}\n", encoding="ascii")
    run.github.serve_latest(run.github.publish("1.1.0", migrations=(*SHIPPED, NEW_9)))
    kwargs = dict(run.kwargs)
    del kwargs["ledger_level"]
    with pytest.raises(UpdateError) as refused:
        run_update(check=False, **kwargs)
    assert str(refused.value) == NEEDS_NEWER_REFUSAL.format(path=isolated_schema_ledger)
    assert run.nothing_downloaded_or_launched() and run.confirmed == 0


# --- Task 12-F: every discovery refusal says nothing was changed; admission rows from the ruling ---

NOTHING_CHANGED = "Nothing was changed."


def test_a_release_refusal_ends_with_nothing_was_changed_and_downloads_nothing(run):
    """A `ReleaseError` (the stable `latest` is a prerelease) reached the user without the closing
    sentence. Spelled from E2 rev 2: every outcome names what changed. It is an `UpdateError`, which
    the command line prints and exits 1 on."""
    run.github.serve_latest(run.github.publish("1.1.0rc1"))
    with pytest.raises(UpdateError) as refused:
        run()
    assert str(refused.value) == (
        "GitHub offers no pam release pam can read on the stable channel. " + NOTHING_CHANGED
    )
    assert issubclass(UpdateError, PamError)
    assert run.nothing_downloaded_or_launched()


def test_a_manifest_refusal_ends_with_nothing_was_changed_and_downloads_nothing(run):
    run.github.serve_latest(run.github.publish("1.1.0", manifest_sha="not-a-digest"))
    with pytest.raises(UpdateError) as refused:
        run()
    assert str(refused.value) == (
        "The release manifest sha256 is not 64 lowercase hex digits. " + NOTHING_CHANGED
    )
    assert run.nothing_downloaded_or_launched()


def test_a_draft_release_is_never_offered_on_the_stable_channel(run):
    run.github.serve_latest(run.github.publish("1.1.0", draft=True))
    with pytest.raises(UpdateError, match="no pam release pam can read on the stable channel"):
        run(check=True)
    assert run.nothing_downloaded_or_launched()


def test_a_draft_release_is_never_offered_on_the_rc_channel(run):
    """A draft stable and a draft candidate are both skipped; the admitted one is offered."""
    run.github.publish("1.3.0", draft=True)
    run.github.publish("1.3.0rc1", draft=True)
    run.github.publish("1.1.0rc1")
    run.github.serve_list()
    assert run(check=True, channel="rc") == 0
    assert run.lines[1] == "Offered:   pam 1.1.0rc1 (rc)"


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [("1.1.0rc1", False), ("1.1.0", True)],
    ids=["rc-as-final", "final-as-rc"],
)
def test_an_rc_channel_entry_whose_prerelease_flag_disagrees_with_its_version_is_not_admitted(
    run, version, prerelease
):
    """OD-11 / the ruling's Discovery: admitted only if `prerelease` matches whether the version has
    `rc`. Alone in the list, so the refusal (not another entry's offer) is what proves it."""
    run.github.publish(version, prerelease=prerelease)
    run.github.serve_list()
    with pytest.raises(UpdateError, match="no pam release pam can read on the rc channel"):
        run(check=True, channel="rc")
    assert run.nothing_downloaded_or_launched()


# --- Task 12 Stage 2 review closures (cross-process properties; M4509-M4513) -----------------


def test_the_installer_is_handed_none_of_pams_own_handles(monkeypatch):
    """M4513 (and M4501): measured across processes at the Stage 2 review - with `close_fds=False`
    the installer inherited pam's pipe handles and whoever ran `pam update` (a script reading its
    output) waited 6.8 s, until the INSTALLER ended. `close_fds=True` and the three `DEVNULL`s guard
    that together, each masking the other's deletion, so both operands are spelled here."""
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(k) or "child")
    command.launch_installer(Path("C:/x/pam-setup-1.1.0.exe"))
    (kwargs,) = calls
    assert kwargs.get("close_fds") is True
    assert kwargs.get("stdin") is subprocess.DEVNULL
    assert kwargs.get("stdout") is subprocess.DEVNULL
    assert kwargs.get("stderr") is subprocess.DEVNULL


def test_the_staging_folder_is_made_in_the_temp_folder_never_in_the_current_one(
    monkeypatch, tmp_path
):
    """M4511: the plan's `tempfile.mkdtemp(prefix="pam-update-")` - in the system TEMP folder. A user
    who types `pam update` inside their photo folder must never find an installer staged there."""
    temp, here = tmp_path / "temp", tmp_path / "archive"
    temp.mkdir()
    here.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.chdir(here)
    folder = command.make_staging()
    try:
        assert folder.is_absolute() and folder.parent == temp
        assert folder.name.startswith("pam-update-") and folder.is_dir()
        assert list(here.iterdir()) == []
    finally:
        folder.rmdir()


def test_the_updater_waits_ten_seconds_for_setup_as_10c_measured():
    """M4510: A4 (spike 10C, adopted 2026-11-17) - the updater, still registered, waits up to 10 s
    for setup to hold `setup.lock`. Spelled from the record, not read from `run_registry`; a zero
    wait lets the updater leave before setup locks, so two updaters could each launch a setup."""
    import inspect

    assert run_registry.AWAIT_SETUP_S == 10.0
    assert inspect.signature(run_registry.await_setup).parameters["deadline_s"].default == 10.0
    assert inspect.signature(run_update).parameters["await_setup"].default is (
        run_registry.await_setup
    )


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="E11's registry protects Windows only"
)
def test_a_registered_pam_update_is_not_another_pam_to_itself(run, isolated_run_registry):
    """M4509: `pam update` is an ordinary pam - the console entry registered it before it ran - so the
    production exclusion must not count its own run file. Driven through the real default while this
    process holds its registration; the offer is made (a self-refusal would raise)."""
    run.github.serve_latest(run.github.publish("1.1.0"))
    kwargs = dict(run.kwargs)
    del kwargs["another_pam_running"]
    with run_registry.registered():
        mine = isolated_run_registry / "run"
        assert len(list(mine.iterdir())) == 1  # this process's own run file is held
        assert run_update(check=True, **kwargs) == 0
    assert run.lines[1] == "Offered:   pam 1.1.0 (stable)"


# --- Task 12-G (F3): the leftover installer downloads are reaped after the exclusion check ------

REAPED_LINE = "pam removed 2 installer download(s) left from earlier updates."  # as ruled


def test_leftovers_are_reaped_once_after_the_exclusion_and_before_any_request(run):
    run.github.serve_latest(run.github.publish("1.1.0"))
    assert run(check=True) == 0
    assert run.reaped == [0]  # once, and before the first lookup


def test_nothing_is_reaped_while_another_pam_runs(run):
    with pytest.raises(UpdateError, match="another pam is running"):
        run(another_pam_running=lambda: True)
    assert run.reaped == []


def test_nothing_is_reaped_on_the_pip_route(run):
    run(install_state=lambda: install_method.Provenance(install_method.PIP, run.tmp_path))
    assert run.reaped == []


def test_the_count_is_said_first_and_only_when_above_zero(run):
    run.github.serve_latest(run.github.publish("1.1.0"))
    run.reap_count = 2
    assert run(check=True) == 0
    assert run.lines[0] == REAPED_LINE
    assert run.lines[2] == "Offered:   pam 1.1.0 (stable)"
    quiet = Run(run.github, run.tmp_path)
    assert quiet(check=True) == 0
    assert not any("installer download" in line for line in quiet.lines)
    assert quiet.lines[1] == "Offered:   pam 1.1.0 (stable)"


def test_the_production_reap_is_the_leftovers_module():
    import inspect

    assert inspect.signature(run_update).parameters["reap"].default is reap_leftovers


def test_the_production_reap_removes_a_real_leftover_in_temp(run, monkeypatch):
    """No reap seam: `run_update`'s default reaps `tempfile.gettempdir()` (this test's own)."""
    temp = run.tmp_path / "temp"
    leftover = temp / "pam-update-old"
    leftover.mkdir(parents=True)
    (leftover / "pam-setup-1.0.0.exe").write_bytes(b"MZ")
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    run.github.serve_latest(run.github.publish("1.1.0"))
    kwargs = dict(run.kwargs)
    del kwargs["reap"]
    assert run_update(check=True, **kwargs) == 0
    assert not leftover.exists()
    assert run.lines[0] == "pam removed 1 installer download(s) left from earlier updates."

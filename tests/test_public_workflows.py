"""The public repository's workflows, read with a real YAML parser (M15 Task 14; OD-10).

`release.yml` lives at `public/.github/workflows/release.yml` here and is written to
`.github/workflows/release.yml` by the export: in this repository it never runs. `ci.yml` is one
file shared by both repositories.

EVERY PIN IS SPELLED HERE from the record that measured it (the rulings: Inno Setup 6.7.4 measured
by the controller on 2026-11-20; exiftool 13.64 measured with permission, equal to
exiftool.org's own checksums.txt), never read back from the workflow it checks.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
# Here it is the overlay under `public/`; in the public checkout the export has written it to its
# place. The private repository never has a `.github/workflows/release.yml` of its own.
OVERLAY = REPO / "public" / ".github" / "workflows" / "release.yml"
RELEASE = OVERLAY if OVERLAY.is_file() else REPO / ".github" / "workflows" / "release.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"

EXIFTOOL_VERSION = "13.64"
EXIFTOOL_URL = "https://sourceforge.net/projects/exiftool/files/exiftool-13.64_64.zip/download"
EXIFTOOL_SHA256 = "72c29c44d1e860eebcaefa71692d979af9858fe9a6d7d791a5e7f1dac77804af"
INNO_VERSION = "6.7.4"
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe"
INNO_SHA256 = "a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c"
# The GPL/LGPL source archives (Task 15W; the maintainer's decision 2, 2026-11-21): env prefix -> (URL,
# SHA-256, the file and release-asset name). Each measured once with the maintainer's approval on
# 2026-11-21, from GitHub's archive of the upstream tag THIRD-PARTY-NOTICES.txt names.
SOURCE_ARCHIVES = {
    "LIBHEIF_SRC": (
        "https://github.com/strukturag/libheif/archive/refs/tags/v1.23.6.tar.gz",
        "c9a00b0452f5c16d72ef71e5e40c0e90fbe2ad37148d27a1b2f9bb21546f5f03",
        "libheif-1.23.6.tar.gz",
    ),
    "LIBDE265_SRC": (
        "https://github.com/strukturag/libde265/archive/refs/tags/v1.1.3.tar.gz",
        "189baa08fd6d2dd34db099de411bd6b2e6bd5eb88e81236a3d0fa3826b9715c4",
        "libde265-1.1.3.tar.gz",
    ),
    "X265_SRC": (
        "https://github.com/Multicorewareinc/x265/archive/refs/tags/4.3.tar.gz",
        "fb157f809b74dabbef5002af99489fcaebb4d0073724beffdaaf2be15cbaf7ac",
        "x265-4.3.tar.gz",
    ),
    "PILLOW_HEIF_SRC": (
        "https://github.com/bigcat88/pillow_heif/archive/refs/tags/v1.8.1.tar.gz",
        "eb3108e2f62a55cd38322e22da6ba09cba0e3c6f6c6df9958eb846f84fc1a95e",
        "pillow_heif-1.8.1.tar.gz",
    ),
}
assert len(SOURCE_ARCHIVES) == 4
# The wheel asset, as `pam update` prints it (`photo_archive_manager-X.Y.Z-py3-none-any.whl`, measured
# from `pip wheel` at 1.0.0 and 1.0.0rc1), spelled with the build step's and the create step's version.
WHEEL_IN_BUILD = (
    r"pam-wheel\photo_archive_manager-${{ steps.pins.outputs.version }}-py3-none-any.whl"
)
WHEEL_IN_CREATE = r"pam-wheel\photo_archive_manager-$version-py3-none-any.whl"
DOWNLOADERS = ("Invoke-WebRequest", "curl", "pip download", "pip install", "choco install")


def load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path.name} is not a YAML mapping"
    return document


def triggers(document: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    return document.get("on", document.get(True))


def runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job["steps"]]


def step_index(job: dict, needle: str) -> int:
    return next(i for i, run in enumerate(runs(job)) if needle in run)


def pinned_download(
    job: dict, url_env: str, outfile: str, pin_env: str, extra: str = ""
) -> int:
    """The step's FIRST three lines are exactly download -> hash THAT file -> refuse a mismatch.

    Line equality, in order (Task 14 review F1): a check that hashes something else, compares the
    pin with itself, or is disabled by an extra operand is not a check (M4968, M4970), and a job
    whose pinned download lost its check altogether finds no such step (M4969).
    """
    expected = [
        f"Invoke-WebRequest -Uri $env:{url_env} -OutFile {outfile}{extra}",
        f"$got = (Get-FileHash {outfile} -Algorithm SHA256).Hash.ToLower()",
        f'if ($got -ne $env:{pin_env}) {{ throw "{outfile} hashed $got, expected $env:{pin_env}" }}',
    ]
    index = step_index(job, f"$env:{url_env}")
    lines = [line.strip() for line in runs(job)[index].splitlines() if line.strip()]
    assert lines[:3] == expected
    return index


@pytest.fixture(scope="module")
def release() -> dict:
    return load(RELEASE)


@pytest.fixture(scope="module")
def ci() -> dict:
    return load(CI)


# --- release.yml -----------------------------------------------------------------------------------


def test_the_release_runs_on_a_v_tag_and_nothing_else(release):
    assert triggers(release) == {"push": {"tags": ["v*"]}}


def test_the_release_jobs_are_the_four_the_plan_names_in_order(release):
    assert list(release["jobs"]) == ["manifest", "suite", "installer", "build"]
    jobs = release["jobs"]
    assert jobs["suite"]["needs"] == "manifest" and jobs["installer"]["needs"] == "manifest"
    assert sorted(jobs["build"]["needs"]) == ["installer", "suite"]
    assert not any(job.get("continue-on-error") for job in jobs.values())


def test_the_exiftool_pins_are_exactly_the_measured_ones(release):
    env = release["env"]
    assert (env["EXIFTOOL_VERSION"], env["EXIFTOOL_URL"], env["EXIFTOOL_SHA256"]) == (
        EXIFTOOL_VERSION,
        EXIFTOOL_URL,
        EXIFTOOL_SHA256,
    )


def test_the_inno_pins_are_exactly_the_measured_ones_in_both_workflows(release, ci):
    env = release["env"]
    assert (env["INNO_VERSION"], env["INNO_URL"], env["INNO_SHA256"]) == (
        INNO_VERSION,
        INNO_URL,
        INNO_SHA256,
    )
    ci_env = ci["jobs"]["installer"]["env"]
    assert (ci_env["INNO_URL"], ci_env["INNO_SHA256"]) == (INNO_URL, INNO_SHA256)


def test_the_pins_are_set_in_one_place_and_never_inline(release):
    text = RELEASE.read_text(encoding="utf-8")
    sources = [value for url, sha, _ in SOURCE_ARCHIVES.values() for value in (url, sha)]
    for value in (EXIFTOOL_SHA256, EXIFTOOL_URL, INNO_SHA256, INNO_URL, *sources):
        assert text.count(value) == 1, value
    for job in release["jobs"].values():
        assert "env" not in job or not set(job["env"]) & set(release["env"])


def test_gate_2_recomputes_the_checkout_before_anything_else_runs(release):
    manifest = release["jobs"]["manifest"]
    assert runs(manifest)[-1] == "python tools/export_public.py --verify . EXPORT-MANIFEST.txt"
    assert not any(d in run for run in runs(manifest) for d in DOWNLOADERS)


def test_the_release_suite_is_the_full_suite_unnarrowed(release):
    suite = runs(release["jobs"]["suite"])
    assert "pytest -q" in suite
    for check in ("ruff check .", "ruff format --check .", "mypy pam", "node --version"):
        assert check in suite


def test_the_installer_job_installs_inno_by_hash_then_runs_the_13b_job_body(release):
    job = runs(release["jobs"]["installer"])
    inno = step_index(release["jobs"]["installer"], "$env:INNO_URL")
    body = step_index(release["jobs"]["installer"], "ci_installer_job.ps1")
    assert inno < body
    assert "$env:INNO_SHA256" in job[inno] and "throw" in job[inno]
    assert '-Iscc "$env:RUNNER_TEMP\\inno\\ISCC.exe"' in job[body]


def test_the_build_refuses_unpinned_inputs_before_it_downloads_anything(release):
    build = release["jobs"]["build"]
    check = step_index(build, "tools/release_pins.py check --tag $env:GITHUB_REF_NAME")
    first_download = min(
        i for i, run in enumerate(runs(build)) if any(d in run for d in DOWNLOADERS)
    )
    assert check < first_download


def test_exiftool_is_checked_by_hash_extracted_and_renamed_before_the_build(release):
    build = release["jobs"]["build"]
    step = runs(build)[step_index(build, "$env:EXIFTOOL_URL")]
    lines = [line.strip() for line in step.splitlines()]
    order = [
        next(i for i, line in enumerate(lines) if needle in line)
        for needle in (
            "Invoke-WebRequest -Uri $env:EXIFTOOL_URL",
            "if ($got -ne $env:EXIFTOOL_SHA256) { throw",
            "Expand-Archive exiftool.zip",
            '$folder = "exiftool-zip\\exiftool-$($env:EXIFTOOL_VERSION)_64"',
            'Copy-Item -Recurse "$folder\\exiftool_files" exiftool\\exiftool_files',
            'Copy-Item "$folder\\exiftool(-k).exe" exiftool\\exiftool.exe',
            "& exiftool\\exiftool.exe -ver",
        )
    ]
    assert order == sorted(order)
    assert step_index(build, "$env:EXIFTOOL_URL") < step_index(build, "--exiftool-dir exiftool")


def test_the_wheels_come_from_the_lock_by_hash_and_the_build_from_the_lock(release):
    build = runs(release["jobs"]["build"])
    joined = "\n".join(build)
    assert "split-lock tools/installer/requirements-314.lock" in joined
    assert "pip download --require-hashes --no-deps --only-binary=:all:" in joined
    assert "pip wheel --no-deps --no-build-isolation --require-hashes" in joined
    assert "tools/installer/build.py --embed-zip" in joined and "--iscc" in joined


def test_the_release_writes_the_update_manifest_and_creates_a_draft_only(release):
    build = release["jobs"]["build"]
    joined = "\n".join(runs(build))
    assert "python tools/release_manifest.py" in joined
    assert "--channel ${{ steps.pins.outputs.channel }}" in joined
    create = runs(build)[step_index(build, "gh release create")]
    assert '$flags = @("--draft")' in create
    assert "gh release create $env:GITHUB_REF_NAME @flags" in create
    assert "--draft" in create and "publish" not in create.replace("published", "")
    assert '"Installer SHA-256: ``$sha``" >> $env:GITHUB_STEP_SUMMARY' in create
    assert "Installer SHA-256: $sha" in create


def test_only_the_build_job_may_write_and_the_default_is_read(release):
    assert release["permissions"] == {"contents": "read"}
    writers = [n for n, job in release["jobs"].items() if job.get("permissions")]
    assert writers == ["build"]
    assert release["jobs"]["build"]["permissions"] == {"contents": "write"}


# --- ci.yml, shared by both repositories ----------------------------------------------------------


def test_ci_checks_the_checkout_against_its_manifest_first(ci):
    lane = ci["jobs"]["ci"]
    check = step_index(lane, "python tools/export_public.py --check-checkout")
    assert check < step_index(lane, 'pip install -e ".[dev]"')


def test_ci_runs_the_installer_job_with_inno_required_and_pinned(ci):
    job = ci["jobs"]["installer"]
    assert job["runs-on"] == "windows-latest" and not job.get("continue-on-error")
    inno = step_index(job, "$env:INNO_URL")
    body = step_index(job, "ci_installer_job.ps1")
    assert inno < body
    assert "$env:INNO_SHA256" in runs(job)[inno] and "throw" in runs(job)[inno]
    assert '-Iscc "$env:RUNNER_TEMP\\inno\\ISCC.exe"' in runs(job)[body]


# The four pinned downloads, spelled out here rather than read from the workflow (Task 14 review F1).
PINNED_DOWNLOADS = [
    ("release.yml", "installer", "INNO_URL", "innosetup.exe", "INNO_SHA256", ""),
    ("release.yml", "build", "INNO_URL", "innosetup.exe", "INNO_SHA256", ""),
    (
        "release.yml",
        "build",
        "EXIFTOOL_URL",
        "exiftool.zip",
        "EXIFTOOL_SHA256",
        ' -UserAgent "Wget"',
    ),
    ("ci.yml", "installer", "INNO_URL", "innosetup.exe", "INNO_SHA256", ""),
    # Task 15W: the GPL/LGPL source archives, spelled out like the rest
    ("release.yml", "build", "LIBHEIF_SRC_URL", "libheif-1.23.6.tar.gz", "LIBHEIF_SRC_SHA256", ""),
    ("release.yml", "build", "LIBDE265_SRC_URL", "libde265-1.1.3.tar.gz", "LIBDE265_SRC_SHA256", ""),
    ("release.yml", "build", "X265_SRC_URL", "x265-4.3.tar.gz", "X265_SRC_SHA256", ""),
    ("release.yml", "build", "PILLOW_HEIF_SRC_URL", "pillow_heif-1.8.1.tar.gz", "PILLOW_HEIF_SRC_SHA256", ""),
]  # fmt: skip
assert len(PINNED_DOWNLOADS) == 8


@pytest.mark.parametrize(
    ("workflow", "job", "url_env", "outfile", "pin_env", "extra"), PINNED_DOWNLOADS
)
def test_every_pinned_download_is_hashed_and_refused_on_a_mismatch(
    release, ci, workflow, job, url_env, outfile, pin_env, extra
):
    document = release if workflow == "release.yml" else ci
    pinned_download(document["jobs"][job], url_env, outfile, pin_env, extra)


def test_the_pinned_downloads_are_at_least_as_many_as_the_census_says():
    """Task 15W review (M5308): a row cut together with the count assert above is green inside this
    module; the floor lives OUTSIDE it, in `tests/census.py`."""
    from census import PUBLIC_WORKFLOWS_FLOORS

    assert len(PINNED_DOWNLOADS) >= PUBLIC_WORKFLOWS_FLOORS["PINNED_DOWNLOADS"]
    assert len(set(PINNED_DOWNLOADS)) == len(PINNED_DOWNLOADS)


def test_the_source_archive_pins_are_exactly_the_measured_ones(release):
    env = release["env"]
    for prefix, (url, sha, _) in SOURCE_ARCHIVES.items():
        assert (env[f"{prefix}_URL"], env[f"{prefix}_SHA256"]) == (url, sha), prefix


def _create_line(build: dict) -> str:
    create = runs(build)[step_index(build, "gh release create")]
    return next(ln.strip() for ln in create.splitlines() if "gh release create" in ln)


def test_the_release_uploads_the_wheel_and_every_source_archive_it_downloaded(release):
    """The maintainer's decisions of 2026-11-21: the wheel the pip route names, and the GPL/LGPL source,
    are assets of the release. Each name is spelled here, and each source archive is the file its
    pinned step wrote."""
    build = release["jobs"]["build"]
    create = runs(build)[step_index(build, "gh release create")]
    line = _create_line(build)
    assert line.endswith(r"$installer dist\pam-release.json $wheel @sources"), line
    assert f'$wheel = "{WHEEL_IN_CREATE}"' in [ln.strip() for ln in create.splitlines()]
    names = ", ".join(f'"{outfile}"' for _, _, outfile in SOURCE_ARCHIVES.values())
    assert f"$sources = @({names})" in [ln.strip() for ln in create.splitlines()]
    for prefix, (_, _, outfile) in SOURCE_ARCHIVES.items():
        assert pinned_download(build, f"{prefix}_URL", outfile, f"{prefix}_SHA256") < (
            step_index(build, "gh release create")
        )


def test_the_build_refuses_a_wheel_not_named_as_pam_update_prints_it(release):
    build = release["jobs"]["build"]
    step = runs(build)[step_index(build, "tools/installer/build.py")]
    lines = [ln.strip() for ln in step.splitlines()]
    assert f'$wheel = "{WHEEL_IN_BUILD}"' in lines
    assert any(ln.startswith("if (-not (Test-Path $wheel)) { throw") for ln in lines)
    assert "--pam-wheel $wheel" in step
    assert "Get-ChildItem" not in step


def test_the_build_compiles_with_the_iscc_its_pinned_step_installed(release):
    build = release["jobs"]["build"]
    inno = pinned_download(build, "INNO_URL", "innosetup.exe", "INNO_SHA256")
    assert r'"/DIR=$env:RUNNER_TEMP\inno"' in runs(build)[inno]
    compile_step = step_index(build, "tools/installer/build.py")
    assert inno < compile_step
    assert r'--iscc "$env:RUNNER_TEMP\inno\ISCC.exe"' in runs(build)[compile_step]


def test_neither_workflow_names_the_private_repository():
    for path in (RELEASE, CI):
        assert "photo-archive-manager" + "-private" not in path.read_text(encoding="utf-8")


# --- THIRD-PARTY-NOTICES.txt names what the release actually ships (Task 14 review F5) ----------

# notices heading -> where its version is pinned, spelled by hand. GeoNames (item 10) has none.
NOTICE_SOURCES = {
    "Python": ("release-env", "EMBED_VERSION"),
    "click": ("lock", "click"),
    "Pillow": ("lock", "pillow"),
    "pillow-heif": ("lock", "pillow-heif"),
    "ImageHash": ("lock", "imagehash"),
    "PyWavelets": ("lock", "pywavelets"),
    "NumPy": ("lock", "numpy"),
    "SciPy": ("lock", "scipy"),
    "reverse_geocoder": ("lock", "reverse-geocoder"),
    "ExifTool": ("release-env", "EXIFTOOL_VERSION"),
    "ONNX Runtime": ("lock", "onnxruntime"),
    "protobuf": ("lock", "protobuf"),
    "FlatBuffers": ("lock", "flatbuffers"),
    "packaging": ("lock", "packaging"),
}
assert len(NOTICE_SOURCES) == 14


def test_the_notices_hold_no_open_item():
    """Task 15W: every [OPEN] item is resolved; the release job runs this module, so a notices file
    that reopens one cannot be released."""
    notices = (REPO / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    assert "[OPEN" not in notices and "OPEN]" not in notices
    assert "DRAFT" not in notices


def test_the_notices_name_every_source_archive_the_release_uploads():
    notices = (REPO / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    for _, _, outfile in SOURCE_ARCHIVES.values():
        assert notices.count(outfile) >= 2, outfile  # the mechanism paragraph and its item


def test_every_versioned_notice_matches_its_pin_in_the_lock_or_the_release(release):
    notices = (REPO / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    headings = dict(re.findall(r"^\d+\. (.+?) (\d[\w.]*)\b", notices, re.MULTILINE))
    lock_text = (REPO / "tools" / "installer" / "requirements-314.lock").read_text(
        encoding="utf-8"
    )
    lock = {
        name.lower().replace("_", "-"): version
        for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\;]+)", lock_text, re.M)
    }
    assert set(NOTICE_SOURCES) <= set(headings), sorted(set(NOTICE_SOURCES) - set(headings))
    for heading, (kind, key) in NOTICE_SOURCES.items():
        pinned = release["env"][key] if kind == "release-env" else lock[key]
        assert headings[heading] == pinned, (heading, headings[heading], pinned)

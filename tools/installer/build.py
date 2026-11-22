r"""Build pam's Windows installer (M15 Task 13B; OD-1, OD-3, OD-9, E12, E13).

    python tools/installer/build.py --embed-zip python-3.14.10-embed-amd64.zip \
        --wheels <folder of the locked wheels> --exiftool-dir <exiftool.exe + exiftool_files> \
        --out <folder> [--pam-wheel <wheel> | --wheel-python <a python with setuptools>]

THE INPUTS ARE LOCAL FILES. This script downloads nothing: the release pipeline (Task 14) stages
the CPython embeddable zip (checked here against `EMBEDDABLE_SHA256`), the locked wheels
(checked by pip's `--require-hashes` against the lock, `--no-index`) and exiftool's official
package (OD-4: the pipeline downloads it by pinned hash). The lock is
`tools/installer/requirements-314.lock` (OD-1; spike 10A's 3.14 lock, byte for byte), consumed
UNCHANGED: its two sections are split into two temporary requirement files, because pip
installs every line of a `-r` file and the component's four must be installable apart from the
base.

THE LAYOUT (spike 10A, "Tree layout"; `pam/install_method.py` reads it):

    {app}\pam.cmd             the launcher: pam.entry:main, never pam.cli (Task 13A-F1)
    {app}\python\             the embeddable interpreter + python314._pth (no `import site`)
    {app}\python\Lib\site-packages\   the base payload and pam, --no-deps --require-hashes
    {app}\exiftool\           exiftool.exe and exiftool_files\
    {app}\LICENSE             pam's licence
    {app}\THIRD-PARTY-NOTICES.txt   the licences of everything else the payload carries
    {app}\licenses\          the licence texts no shipped wheel carries (the notices name them)
    {app}\pam-install.json    written by the installer itself, never by the build

`bin\` (console scripts bound to the BUILD interpreter, 10A addendum A4) and every `*.onnx`
(onnxruntime's three toy graphs, Q3: the inspection stays literal) are removed; the build then
asserts there is none. Every `.py` is compiled by the payload's own interpreter with
unchecked-hash `.pyc` files, and the launcher runs `-B`, so pam never writes `__pycache__` into
`{app}` and the uninstaller leaves no folder behind.

THE SCRIPT TAKES ITS VALUES FROM PAM, NEVER RETYPED: the version, the newest shipped migration
(E12), the component label (E13) and the ledger's refusal sentence. A PRODUCTION compile passes no
`PamTest*` define (ruling 5); tests compile with `compile_installer(..., test=...)`.
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pam import __version__, install_method  # noqa: E402
from pam.catalog import ledger  # noqa: E402
from pam.catalog.db import shipped_migrations  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "pam.iss"
LOCK = REPO / "tools/installer/requirements-314.lock"
NOTICES = "THIRD-PARTY-NOTICES.txt"
# GPL-2.0 (x265), LGPL-3.0 (libheif, libde265), LGPL-2.1 (reverse_geocoder), Apache-2.0 (FlatBuffers),
# each verbatim from its upstream source at the shipped version (Task 15W); installed at
# {app}\licenses\.
LICENSES = Path(__file__).resolve().parent / "licenses"
INNO_VERSION = "6.7.4"  # the maintainer's ruling, 2026-11-20
ISCC_DEFAULT = Path(r"~\AppData\Local\Programs\Inno Setup 6\ISCC.exe").expanduser()
# The CPython embeddable package, by version, as measured against python.org's SBOM (10A addendum).
EMBEDDABLE_SHA256 = {
    "3.14.10": "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159",
}
COMPONENT_MARKER = "# ---- OPTIONAL COMPONENT"
# Where pam.iss's [Files] puts the component, below {app}.
COMPONENT_UNDER_APP = r"python\Lib\site-packages"
# Inno 6.7.4 writes each file first as `<its folder>\is-XXXXXXXXXX.tmp`, then renames it (measured,
# Task 13B-F: a folder of 246 characters failed the copy of an 11-character name in it, while the
# file's own path was 258).
DIRECTORY_MARGIN = len(r"\is-XXXXXXXXXX.tmp")
# The launcher. `-I` is redundant with the `._pth` (10A) and kept; `-B` keeps `{app}` free of
# `__pycache__`; `reconfigure` is 10A Q7's fix for a piped console (adopted 2026-11-17).
LAUNCHER = (
    "@echo off\r\n"
    "rem pam's launcher (M15 Task 13B): the bundled interpreter runs pam.entry:main.\r\n"
    "setlocal\r\n"
    'set "PAM_APP=%~dp0"\r\n'
    'set "PATH=%PAM_APP%exiftool;%PATH%"\r\n'
    '"%PAM_APP%python\\python.exe" -I -B -c "import sys;'
    " sys.stdout.reconfigure(errors='backslashreplace');"
    " sys.stderr.reconfigure(errors='backslashreplace');"
    " sys.argv[0] = 'pam'; from pam.entry import main; sys.exit(main())\" %*\r\n"
    "exit /b %ERRORLEVEL%\r\n"
)
WHEEL_SOURCES = ("pam", "pyproject.toml")
PTH_ENTRIES = ("python{tag}.zip", ".", "Lib\\site-packages")


class BuildError(Exception):
    """The build cannot produce a correct installer; the message says why."""


def target(lock_text: str) -> tuple[str, str]:
    """(embeddable version, `cp` tag digits) from the lock's header, never hard-coded (A2)."""
    version = re.search(r"embeddable package (\d+\.\d+\.\d+)", lock_text)
    tag = re.search(r"\(cp(\d+)\)", lock_text)
    if version is None or tag is None:
        raise BuildError("the lock's header names no embeddable package and cp tag")
    return version.group(1), tag.group(1)


def split_lock(lock_text: str) -> tuple[str, str]:
    """(base requirements, component requirements): the lock's two sections, lines unchanged."""
    head, marker, tail = lock_text.partition(COMPONENT_MARKER)
    if not marker or "==" not in head or "==" not in tail:
        raise BuildError(
            f"the lock has no '{COMPONENT_MARKER}' section between two sets of pins"
        )
    return head, marker + tail


def highest_migration() -> str:
    return max(shipped_migrations())


def longest_path(payload: Path, component: Path) -> int:
    r"""The longest path, in characters, the installer creates below `{app}`: the payload as it
    is, and the component under `python\Lib\site-packages\` (Task 13B-F). Setup refuses an `{app}`
    that would take `{app}\<this>` to MAX_PATH (260). A FOLDER counts `DIRECTORY_MARGIN` more than
    its name, for the temporary file setup writes in it, so a folder can be the limit before any
    file in it is (it also covers CreateDirectoryW's MAX_PATH - 12). A missing folder contributes
    nothing."""
    lengths = [0]
    for folder, below in ((payload, ""), (component, COMPONENT_UNDER_APP + "\\")):
        if folder.is_dir():
            lengths += [
                len(below + str(p.relative_to(folder)))
                + (DIRECTORY_MARGIN if p.is_dir() else 0)
                for p in folder.rglob("*")
            ]
    return max(lengths)


def defines(payload: Path, component: Path, out: Path) -> dict[str, str]:
    """The production compile's defines: values read from pam, never a `PamTest*` (ruling 5)."""
    return {
        "PamVersion": __version__,
        "HighestMigration": highest_migration(),
        "ComponentLabel": install_method.COMPONENT_LABEL,
        "NeedsNewer": ledger.NEEDS_NEWER,
        "PayloadDir": str(payload),
        "ComponentDir": str(component),
        "OutputDir": str(out),
        "LongestPayloadPath": str(longest_path(payload, component)),
    }


def iscc_version(iscc: Path) -> str | None:
    """The version the ISCC at `iscc` reports ("Compiler engine version: Inno Setup X"), or None.

    ISCC prints that line before it reads the script; an empty script on stdin ends the run there."""
    done = subprocess.run(
        [str(iscc), "-"],
        input="",
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    found = re.search(r"^Compiler engine version: Inno Setup (\S+)\s*$", done.stdout, re.M)
    return found.group(1) if found else None


def check_iscc(iscc: Path) -> None:
    """The maintainer's ruling (2026-11-20): pam's installer is compiled by Inno Setup 6.7.4 and no other."""
    try:
        found = iscc_version(iscc)
    except OSError as error:
        raise BuildError(f"{iscc} cannot be run ({error})") from None
    if found != INNO_VERSION:
        raise BuildError(
            f"{iscc} reports Inno Setup {found or 'of no known version'}; pam's installer is built"
            f" with Inno Setup {INNO_VERSION} only"
        )


def compile_installer(
    iscc: Path, values: dict[str, str], test: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """ISCC over pam.iss. `test` adds the `PamTest*` defines; production passes None."""
    merged = dict(values)
    for name, value in (test or {}).items():
        if not name.startswith("PamTest"):
            raise BuildError(f"a test define must start with PamTest: {name}")
        merged[name] = value
    args = [
        str(iscc),
        "/Q",
        *(f"/D{name}={value}" for name, value in merged.items()),
        str(SCRIPT),
    ]
    return subprocess.run(args, capture_output=True, text=True, errors="replace", check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_interpreter(embed_zip: Path, version: str, tag: str, python: Path) -> None:
    pinned = EMBEDDABLE_SHA256.get(version)
    if pinned is None:
        raise BuildError(f"no pinned SHA-256 for the {version} embeddable package")
    if sha256(embed_zip) != pinned:
        raise BuildError(f"{embed_zip.name} does not match the pinned SHA-256 for {version}")
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(python)
    pth = python / f"python{tag}._pth"
    if not pth.is_file():
        raise BuildError(f"the embeddable package has no {pth.name}")
    pth.write_text(
        "\r\n".join(e.format(tag=tag) for e in PTH_ENTRIES) + "\r\n", encoding="ascii"
    )


def pip_install(
    requirements: str, wheels: Path, dest: Path, version: str, tag: str, hashes: bool = True
) -> None:
    """`pip install --target`, offline (`--no-index`), for the embeddable's platform and ABI."""
    with tempfile.TemporaryDirectory() as scratch:
        file = Path(scratch) / "requirements.txt"
        file.write_text(requirements, encoding="utf-8")
        args = [sys.executable, "-m", "pip", "install", "--no-deps", "--no-index",
                "--find-links", str(wheels), "--only-binary=:all:", "--no-compile",
                "--platform", "win_amd64", "--python-version", version.rsplit(".", 1)[0],
                "--implementation", "cp", "--abi", f"cp{tag}", "--target", str(dest),
                "--disable-pip-version-check", "-r", str(file)]  # fmt: skip
        if hashes:
            args.insert(5, "--require-hashes")
        done = subprocess.run(
            args, capture_output=True, text=True, errors="replace", check=False
        )
    if done.returncode != 0:
        raise BuildError(
            f"pip could not install into {dest}:\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
        )


def scrub(site: Path) -> None:
    """Remove `bin\\` and every `*.onnx`; then prove neither is left (Q3, A4)."""
    shutil.rmtree(site / "bin", ignore_errors=True)
    for model in site.rglob("*.onnx"):
        model.unlink()
    left = [p for p in site.rglob("*") if p.suffix.lower() == ".onnx" or p.name == "bin"]
    if left:
        raise BuildError(f"the payload still holds {left[0]}")


def build_pam_wheel(wheel_python: Path, dest: Path) -> Path:
    """pam's wheel from `git archive HEAD` (the committed tree, never the working tree), of only
    what the wheel is built from: the tests' tiny library holds names longer than MAX_PATH."""
    dest.mkdir(parents=True, exist_ok=True)
    source = dest / "src"
    archive = dest / "head.tar"
    with archive.open("wb") as out:
        subprocess.run(
            ["git", "-C", str(REPO), "archive", "HEAD", *WHEEL_SOURCES], stdout=out, check=True
        )
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter="data")
    subprocess.run(
        [
            str(wheel_python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "-w",
            str(dest),
            str(source),
        ],  # fmt: skip
        check=True,
        capture_output=True,
    )
    return next(dest.glob("photo_archive_manager-*.whl"))


def compile_bytecode(python_exe: Path, folder: Path) -> None:
    """unchecked-hash `.pyc` by the payload's own interpreter; a file that does not compile
    (a package's test data) is left as source, which `-B` never caches."""
    subprocess.run(
        [
            str(python_exe),
            "-I",
            "-m",
            "compileall",
            "-q",
            "-j",
            "0",
            "--invalidation-mode",
            "unchecked-hash",
            str(folder),
        ],  # fmt: skip
        check=False,
        capture_output=True,
    )


def stage(args: argparse.Namespace, work: Path) -> tuple[Path, Path]:
    """The base payload and the component folder, ready for ISCC."""
    lock_text = Path(args.lock).read_text(encoding="utf-8")
    version, tag = target(lock_text)
    base_reqs, component_reqs = split_lock(lock_text)
    payload, component = work / "payload", work / "component"
    python = payload / "python"
    site = python / "Lib" / "site-packages"
    stage_interpreter(Path(args.embed_zip), version, tag, python)
    pip_install(base_reqs, Path(args.wheels), site, version, tag)
    pam_wheel = (
        Path(args.pam_wheel)
        if args.pam_wheel
        else build_pam_wheel(Path(args.wheel_python), work / "pam-wheel")
    )
    pip_install(str(pam_wheel), pam_wheel.parent, site, version, tag, hashes=False)
    pip_install(component_reqs, Path(args.wheels), component, version, tag)
    for folder in (site, component):
        scrub(folder)
        compile_bytecode(python / "python.exe", folder)
    exiftool = Path(args.exiftool_dir)
    if not (exiftool / "exiftool.exe").is_file() or not (exiftool / "exiftool_files").is_dir():
        raise BuildError(f"{exiftool} holds no exiftool.exe beside exiftool_files")
    # exiftool.exe and exiftool_files only: the folder it is found in may hold anything else
    shutil.copytree(exiftool / "exiftool_files", payload / "exiftool" / "exiftool_files")
    shutil.copy2(exiftool / "exiftool.exe", payload / "exiftool" / "exiftool.exe")
    shutil.copy2(REPO / "LICENSE", payload / "LICENSE")
    shutil.copy2(REPO / NOTICES, payload / NOTICES)
    # The licence texts the third-party wheels do not carry (THIRD-PARTY-NOTICES.txt names each).
    shutil.copytree(LICENSES, payload / "licenses")
    (payload / "pam.cmd").write_bytes(LAUNCHER.encode("ascii"))
    return payload, component


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--embed-zip", required=True)
    parser.add_argument("--wheels", required=True)
    parser.add_argument("--exiftool-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lock", default=str(LOCK))
    parser.add_argument("--iscc", default=str(ISCC_DEFAULT))
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pam-wheel")
    which.add_argument("--wheel-python")
    parser.add_argument("--keep-stage", help="stage here and keep it (Q8 reads the payload)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        check_iscc(Path(args.iscc))
    except BuildError as error:
        print(f"x {error}", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pam-installer-") as scratch:
        work = Path(args.keep_stage).resolve() if args.keep_stage else Path(scratch)
        work.mkdir(parents=True, exist_ok=True)
        try:
            payload, component = stage(args, work)
        except BuildError as error:
            print(f"x {error}", file=sys.stderr)
            return 1
        done = compile_installer(Path(args.iscc), defines(payload, component, out))
    if done.returncode != 0:
        print(f"x ISCC failed:\n{done.stdout}{done.stderr}", file=sys.stderr)
        return 1
    installer = out / f"pam-setup-{__version__}.exe"
    print(f"{installer} {installer.stat().st_size} bytes sha256 {sha256(installer)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

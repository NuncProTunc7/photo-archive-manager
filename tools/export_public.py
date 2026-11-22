r"""Export pam's PUBLIC tree from one commit of this repository, and verify an export (M15 Task 14).

    python tools/export_public.py COMMIT STAGING        export COMMIT into a new, empty STAGING
    python tools/export_public.py --verify STAGING MANIFEST
                                                        gate 1: STAGING is exactly MANIFEST
    python tools/export_public.py --check-checkout      CI: verify this checkout, if it is an export

WHAT AN EXPORT IS. `git archive` of one COMMIT (tracked content only: an untracked file, a
`__pycache__` or a local `.pyc` never reaches it), filtered by `ALLOWED` and `EXCLUDED` below and
written into a staging folder that must be new or empty. Files under `public/` are written at the
root with the prefix removed (`public/README.md` -> `README.md`): they are the public repository's
own versions of files this repository keeps differently, and they are inert here (a workflow under
`public/.github/` never runs in this repository). The export REFUSES, writing nothing further:
  - a symlink or submodule (git mode 120000 / 160000) anywhere in the selected paths;
  - a tracked `__pycache__` folder or `.pyc` file;
  - a binary file (a NUL byte in its first 8000 bytes) that `tests/fixtures/PROVENANCE.md` does
    not name, path by path - every binary fixture says how it was synthesised;
  - a path that `public/` and the allowlist would both write.
It then writes `EXPORT-MANIFEST.txt`: the source commit, then every file with its SHA-256.

THE THREE GATES (plan rev 3). Approval binds the TREE, not a manifest's hash:
  1. `--verify STAGING MANIFEST` recomputes every file's SHA-256 under STAGING and fails on an
     added, missing, changed or special file, and on a STAGING whose own `EXPORT-MANIFEST.txt` is
     not byte-identical to MANIFEST (the file the owner read and approved). The owner pushes only
     a tree that passed it.
  2. Public CI runs `--check-checkout`: the committed `EXPORT-MANIFEST.txt` against the checkout.
  3. The release: the installer's SHA-256 that HT-M15 tested is the one published.

Exit 0 done/verified, 1 a verification failed, 2 refused (one sentence each on stderr).
"""

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "EXPORT-MANIFEST.txt"
MANIFEST_HEADER = (
    "# pam public export manifest: the source commit, then sha256 and path per file"
)
PROVENANCE = "tests/fixtures/PROVENANCE.md"
OVERLAY = "public/"
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

# THE ALLOWLIST - tracked paths of THIS repository that the public tree carries. A pattern ending
# in "/**" is a folder and everything below it; anything else is one exact path. Spelled out, never
# derived: a path is public because it is listed here, not because nothing excluded it.
ALLOWED = (
    "pam/**",
    "tests/**",
    "public/**",
    "tools/installer/**",
    "tools/_proctree.py",
    "tools/build_tiny_library.py",
    "tools/ci_gate_library.py",
    "tools/ci_required_passed.py",
    "tools/count_executable_lines.py",
    "tools/export_public.py",
    "tools/ht_manifest.py",
    "tools/privacy_scan.py",
    "tools/release_manifest.py",
    "tools/release_pins.py",
    ".github/workflows/ci.yml",
    ".gitignore",
    ".python-version",
    "LICENSE",
    "THIRD-PARTY-NOTICES.txt",
    "docs/user-guide.md",
    "mypy.ini",
    "pyproject.toml",
    "ruff.toml",
)
# Inside an allowed folder, these stay private: the suite's assertions about private documents and
# tools (Task 14's split), and the tracked .gitattributes the public tree replaces.
EXCLUDED = ("tests/private/**",)
BINARY_PROBE = 8000


class Refused(Exception):
    """The export or verification cannot proceed; the message is the one sentence to print."""


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif path == pattern:
            return True
    return False


def selected(path: str) -> bool:
    return matches(path, ALLOWED) and not matches(path, EXCLUDED)


def public_name(path: str) -> str:
    return path[len(OVERLAY) :] if path.startswith(OVERLAY) else path


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_PROBE]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str, cwd: Path = REPO_ROOT) -> bytes:
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", *args],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Refused(f"git {args[0]} failed: {message[-1] if message else done.returncode}")
    return done.stdout


def resolve_commit(commit: str, repo: Path = REPO_ROOT) -> str:
    return (
        _git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", cwd=repo)
        .decode()
        .strip()
    )


def tracked(commit: str, repo: Path = REPO_ROOT) -> dict[str, str]:
    """path -> git mode, for every path tracked at COMMIT."""
    out = _git("ls-tree", "-r", "-z", "--full-tree", commit, cwd=repo)
    modes: dict[str, str] = {}
    for record in out.split(b"\0"):
        if record:
            meta, _, name = record.partition(b"\t")
            modes[name.decode("utf-8")] = meta.split()[0].decode()
    return modes


def provenance_list(text: str) -> set[str]:
    """Every path a PROVENANCE.md list item names: lines of the form "- `tests/...`"."""
    named = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- `") and stripped.count("`") >= 2:
            named.add(stripped.split("`")[1])
    return named


def plan_export(modes: dict[str, str]) -> dict[str, str]:
    """public path -> source path, after every refusal that needs only the tree's listing."""
    chosen = {path: mode for path, mode in modes.items() if selected(path)}
    for path, mode in sorted(chosen.items()):
        if mode == "120000":
            raise Refused(f"{path} is a symlink; the export carries regular files only")
        if mode == "160000":
            raise Refused(f"{path} is a submodule; the export carries regular files only")
        parts = PurePosixPath(path).parts
        if "__pycache__" in parts or path.endswith((".pyc", ".pyo")):
            raise Refused(f"{path} is compiled bytecode; the export carries source only")
    mapping: dict[str, str] = {}
    for path in sorted(chosen):
        name = public_name(path)
        if name in mapping:
            raise Refused(f"{name} would be written twice (from {mapping[name]} and {path})")
        mapping[name] = path
    if MANIFEST_NAME in mapping:
        raise Refused(f"{MANIFEST_NAME} is written by the export and may not be tracked")
    return mapping


def pathspecs(sources: list[str]) -> list[str]:
    """The allowlist's own roots that hold a selected source: a short, fixed command line."""
    roots = [pattern[:-3] if pattern.endswith("/**") else pattern for pattern in ALLOWED]
    return sorted(
        {root for root in roots for s in sources if s == root or s.startswith(root + "/")}
    )


def read_archive(commit: str, sources: list[str], repo: Path = REPO_ROOT) -> dict[str, bytes]:
    """source path -> bytes for exactly SOURCES, from `git archive` of the allowlist at COMMIT."""
    wanted = set(sources)
    specs = pathspecs(sources)
    blob = _git("archive", "--format=tar", commit, "--", *specs, cwd=repo) if specs else b""
    contents: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tar:
        for member in tar:
            if member.isdir() or member.type in (tarfile.XGLTYPE, tarfile.XHDTYPE):
                continue
            if member.name not in wanted:
                continue
            if not member.isfile():
                raise Refused(f"{member.name} is not a regular file in the archive")
            extracted = tar.extractfile(member)
            assert extracted is not None
            contents[member.name] = extracted.read()
    return contents


def check_binaries(files: dict[str, bytes]) -> None:
    listed = provenance_list(files.get(PROVENANCE, b"").decode("utf-8", "replace"))
    for name, data in sorted(files.items()):
        if is_binary(data) and name not in listed:
            raise Refused(f"{name} is binary and {PROVENANCE} does not say how it was made")


def manifest_text(commit: str, files: dict[str, bytes]) -> str:
    lines = [MANIFEST_HEADER, f"source-commit {commit}"]
    lines += [f"{sha256(files[name])}  {name}" for name in sorted(files)]
    return "\n".join(lines) + "\n"


def export(commit: str, staging: Path, repo: Path = REPO_ROOT) -> str:
    """Write the public tree of COMMIT into STAGING; returns the manifest's text."""
    if staging.exists() and (not staging.is_dir() or any(staging.iterdir())):
        raise Refused(
            f"{staging} is not a new, empty folder; the export never writes into old files"
        )
    full = resolve_commit(commit, repo)
    mapping = plan_export(tracked(full, repo))
    by_source = read_archive(full, sorted(mapping.values()), repo)
    files = {name: by_source[source] for name, source in mapping.items()}
    if set(by_source) != set(mapping.values()):
        raise Refused("git archive did not return exactly the selected paths")
    check_binaries(files)
    text = manifest_text(full, files)
    staging.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = staging.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (staging / MANIFEST_NAME).write_bytes(text.encode("utf-8"))
    return text


def parse_manifest(text: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines()
    if (
        len(lines) < 2
        or lines[0] != MANIFEST_HEADER
        or not lines[1].startswith("source-commit ")
    ):
        raise Refused("the manifest does not start with the export's header and source commit")
    entries: dict[str, str] = {}
    for line in lines[2:]:
        digest, sep, name = line.partition("  ")
        if not sep or len(digest) != 64 or name in entries:
            raise Refused(f"the manifest has a malformed or repeated line: {line[:80]!r}")
        entries[name] = digest
    return lines[1].split(" ", 1)[1], entries


def walk(root: Path) -> tuple[dict[str, str], list[str]]:
    """relative path -> sha256 for every regular file under ROOT, and every special entry.

    A `.git` folder at ROOT is never part of the tree: gate 1 may run in a staging folder the
    owner has already `git init`-ed, and gate 2 runs in a checkout.
    """
    found: dict[str, str] = {}
    special: list[str] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        for name in list(dirs):
            path = here / name
            rel = path.relative_to(root).as_posix()
            if rel == ".git":
                dirs.remove(name)
            elif path.is_symlink() or os.path.isjunction(path):
                special.append(rel)
                dirs.remove(name)
        for name in names:
            path = here / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                special.append(rel)
            elif rel != MANIFEST_NAME:
                found[rel] = sha256(path.read_bytes())
    return found, special


def verify(staging: Path, manifest: Path) -> list[str]:
    """Every difference between STAGING and MANIFEST; empty means the tree is the approved one."""
    approved = manifest.read_bytes()
    _, entries = parse_manifest(approved.decode("utf-8"))
    problems = []
    own = staging / MANIFEST_NAME
    if not own.is_file() or own.read_bytes() != approved:
        problems.append(f"{MANIFEST_NAME} in the tree is not the approved manifest")
    found, special = walk(staging)
    problems += [f"special file: {rel}" for rel in sorted(special)]
    problems += [f"added: {rel}" for rel in sorted(set(found) - set(entries))]
    problems += [f"missing: {rel}" for rel in sorted(set(entries) - set(found))]
    problems += [
        f"changed: {rel}"
        for rel in sorted(set(found) & set(entries))
        if found[rel] != entries[rel]
    ]
    return problems


def check_checkout(root: Path = REPO_ROOT) -> list[str]:
    """CI's gate 2: an export verifies against its own manifest; this private repository says so."""
    if (root / MANIFEST_NAME).is_file():
        return verify(root, root / MANIFEST_NAME)
    if (root / "tests" / "private").is_dir():
        print("this is the private repository: there is no export manifest to check")
        return []
    return [f"{MANIFEST_NAME} is missing: a public checkout must carry the export's manifest"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", nargs=2, metavar=("STAGING", "MANIFEST"))
    parser.add_argument("--check-checkout", action="store_true")
    parser.add_argument("export", nargs="*", metavar="COMMIT STAGING")
    args = parser.parse_args(argv)
    try:
        if args.check_checkout or args.verify:
            if args.check_checkout:
                problems = check_checkout()
            else:
                problems = verify(Path(args.verify[0]), Path(args.verify[1]))
            for problem in problems:
                print(f"x {problem}", file=sys.stderr)
            if not problems:
                print("verified: every file matches the manifest, and nothing else is there")
            return EXIT_FAILED if problems else EXIT_OK
        if len(args.export) != 2:
            raise Refused(
                "give a COMMIT and a new STAGING folder (or --verify / --check-checkout)"
            )
        text = export(args.export[0], Path(args.export[1]))
    except (Refused, OSError, UnicodeDecodeError) as error:
        print(f"x {error}", file=sys.stderr)
        return EXIT_REFUSED
    print(
        f"exported {len(text.splitlines()) - 2} files; manifest sha256 {sha256(text.encode())}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

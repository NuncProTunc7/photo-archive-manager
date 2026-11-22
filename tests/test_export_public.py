"""`tools/export_public.py` - the public tree's export and its gate-1 verification (M15 Task 14).

EVERY EXPECTATION IS SPELLED HERE, never read back from the tool: the allowlist is written out below
from the Task 14 plan and rulings (OD-5/OD-7: pam, the public suite, the tools the public CI and the
installer build need, the user guide, the licence files), and the manifest is recomputed with
`hashlib` rather than with the tool's own writer.

The repositories here are built in `tmp_path` with real `git`: what is "tracked" is git's answer,
which is the property the export rests on (an untracked `.pyc` never reaches `git archive`).
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import export_public  # noqa: E402
from export_public import MANIFEST_NAME, Refused  # noqa: E402

# The allowlist as the plan and the rulings define it, spelled by hand (an oracle built from the
# tool's own tuple would agree with any value of it).
SPEC_ALLOWED = {
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
}
SPEC_EXCLUDED = {"tests/private/**"}

PUBLIC_PATHS = [
    "pam/cli.py",
    "pam/webui/static/app.js",
    "tests/test_apply.py",
    "tests/fixtures/PROVENANCE.md",
    "tools/installer/build.py",
    "tools/installer/requirements-314.lock",
    "tools/export_public.py",
    "docs/user-guide.md",
    ".github/workflows/ci.yml",
    "public/README.md",
    "public/.github/workflows/release.yml",
    "LICENSE",
    "THIRD-PARTY-NOTICES.txt",
]
PRIVATE_PATHS = [
    "CLAUDE.md",
    "README.md",
    ".gitattributes",
    ".pre-commit-config.yaml",
    ".claude/skills/codex-plan-review/SKILL.md",
    "docs/superpowers/specs/2026-07-20-v1-design-spec.md",
    "docs/implementation-notes/m15.md",
    "docs/testing/ht-m15.md",
    "docs/suite-cost.csv",
    "tests/private/test_ht_m14_doc.py",
    "tools/ht_check.py",
    "tools/ht_run.py",
    "tools/q_measure.py",
    "tools/bench_scan.py",
    "tools/suite_cost.py",
    "tools/Invoke-CodexPlanReview.ps1",
    "PUBLICATION-APPROVALS.txt",
    "toolsx/ht.py",
    "pamx/cli.py",
]


def test_the_allowlist_is_exactly_the_one_the_plan_names():
    assert set(export_public.ALLOWED) == SPEC_ALLOWED
    assert set(export_public.EXCLUDED) == SPEC_EXCLUDED


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_a_public_path_is_selected(path):
    assert export_public.selected(path)


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_a_private_path_is_not_selected(path):
    assert not export_public.selected(path)


def test_the_path_lists_are_not_empty():
    # pytest SKIPS an empty parametrize; these controls must not vanish with their lists.
    assert len(PUBLIC_PATHS) >= 13 and len(PRIVATE_PATHS) >= 19


def test_public_overlay_files_are_written_at_the_root():
    assert export_public.public_name("public/README.md") == "README.md"
    assert export_public.public_name("public/.github/workflows/release.yml") == (
        ".github/workflows/release.yml"
    )
    assert export_public.public_name("pam/public/x.py") == "pam/public/x.py"


# --- a real git repository in tmp_path ------------------------------------------------------------


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.com",
         *args],
        cwd=repo, input=stdin, capture_output=True, check=True,
    )  # fmt: skip
    return done.stdout.decode().strip()


PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + b"\0" * 16


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for name, data in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


BASE = {
    "pam/__init__.py": b"__version__ = '1.0.0'\n",
    "pam/cli.py": b"print('pam')\n",
    "tests/test_x.py": b"def test_x():\n    pass\n",
    "tests/private/test_secret.py": b"def test_y():\n    pass\n",
    "tests/fixtures/PROVENANCE.md": b"# binaries\n\n- `tests/fixtures/drawn.png`\n",
    "tests/fixtures/drawn.png": PNG,
    "public/README.md": b"# pam (public)\n",
    "README.md": b"# pam (private)\n",
    "CLAUDE.md": b"private notes\n",
    "docs/user-guide.md": b"# guide\n",
    "docs/notes.md": b"private\n",
    "tools/export_public.py": b"# tool\n",
    "tools/ht_check.py": b"# private tool\n",
}


def expected_manifest(commit: str, files: dict[str, bytes]) -> str:
    """An independent second writer of the manifest format (header, commit, sha256 + path)."""
    lines = [export_public.MANIFEST_HEADER, f"source-commit {commit}"]
    lines += [f"{hashlib.sha256(files[n]).hexdigest()}  {n}" for n in sorted(files)]
    return "\n".join(lines) + "\n"


def test_an_export_writes_exactly_the_allowlisted_tracked_files_and_their_manifest(tmp_path):
    repo = make_repo(tmp_path, BASE)
    commit = git(repo, "rev-parse", "HEAD")
    staging = tmp_path / "staging"
    export_public.export("HEAD", staging, repo)
    public = {
        "pam/__init__.py": BASE["pam/__init__.py"],
        "pam/cli.py": BASE["pam/cli.py"],
        "tests/test_x.py": BASE["tests/test_x.py"],
        "tests/fixtures/PROVENANCE.md": BASE["tests/fixtures/PROVENANCE.md"],
        "tests/fixtures/drawn.png": PNG,
        "README.md": BASE["public/README.md"],
        "docs/user-guide.md": BASE["docs/user-guide.md"],
        "tools/export_public.py": BASE["tools/export_public.py"],
    }
    written = {
        p.relative_to(staging).as_posix(): p.read_bytes()
        for p in staging.rglob("*")
        if p.is_file()
    }
    manifest = written.pop(MANIFEST_NAME).decode()
    assert written == public
    assert manifest == expected_manifest(commit, public)


def test_a_reused_destination_holding_stale_files_is_refused_and_nothing_is_written(tmp_path):
    repo = make_repo(tmp_path, BASE)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "stale.txt").write_text("from an earlier export")
    with pytest.raises(Refused, match="not a new, empty folder"):
        export_public.export("HEAD", staging, repo)
    assert sorted(p.name for p in staging.iterdir()) == ["stale.txt"]


def test_an_untracked_pyc_and_untracked_files_are_not_exported_and_edits_are_not_either(
    tmp_path,
):
    repo = make_repo(tmp_path, BASE)
    (repo / "pam" / "__pycache__").mkdir()
    (repo / "pam" / "__pycache__" / "cli.cpython-314.pyc").write_bytes(b"\0compiled")
    (repo / "pam" / "stray.py").write_text("untracked = True\n")
    (repo / "pam" / "cli.py").write_text("print('edited, never committed')\n")
    staging = tmp_path / "staging"
    export_public.export("HEAD", staging, repo)
    assert not (staging / "pam" / "__pycache__").exists()
    assert not (staging / "pam" / "stray.py").exists()
    assert (staging / "pam" / "cli.py").read_bytes() == BASE["pam/cli.py"]


def test_a_tracked_pycache_or_pyc_is_refused(tmp_path):
    repo = make_repo(tmp_path, {**BASE, "pam/__pycache__/x.cpython-314.pyc": b"text-only pyc"})
    with pytest.raises(Refused, match="compiled bytecode"):
        export_public.export("HEAD", tmp_path / "staging", repo)


def test_an_escaping_symlink_is_refused(tmp_path):
    repo = make_repo(tmp_path, BASE)
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=b"../../../outside/secret.txt")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},tests/escape.txt")
    git(repo, "commit", "-q", "-m", "a symlink")
    with pytest.raises(Refused, match="tests/escape.txt is a symlink"):
        export_public.export("HEAD", tmp_path / "staging", repo)
    assert not (tmp_path / "staging").exists()


def test_an_unlisted_binary_fixture_is_refused(tmp_path):
    repo = make_repo(tmp_path, {**BASE, "tests/fixtures/photo.jpg": b"\xff\xd8\xff\0EXIF"})
    with pytest.raises(Refused, match="tests/fixtures/photo.jpg is binary"):
        export_public.export("HEAD", tmp_path / "staging", repo)


def test_a_listed_binary_passes_and_a_mention_outside_a_list_item_does_not_list_it():
    text = (
        "intro `tests/fixtures/a.png` in prose\n- `tests/fixtures/b.png`\n  - `tests/c.png`\n"
    )
    assert export_public.provenance_list(text) == {"tests/fixtures/b.png", "tests/c.png"}


def test_an_overlay_path_colliding_with_an_allowlisted_one_is_refused(tmp_path):
    repo = make_repo(tmp_path, {**BASE, "public/pam/cli.py": b"print('other')\n"})
    with pytest.raises(Refused, match="pam/cli.py would be written twice"):
        export_public.export("HEAD", tmp_path / "staging", repo)


def test_an_unknown_commit_is_refused(tmp_path):
    repo = make_repo(tmp_path, BASE)
    with pytest.raises(Refused, match="git rev-parse failed"):
        export_public.export("no-such-commit", tmp_path / "staging", repo)


# --- gate 1: the tree the owner approved -----------------------------------------------------------


@pytest.fixture
def exported(tmp_path):
    repo = make_repo(tmp_path, BASE)
    staging = tmp_path / "staging"
    export_public.export("HEAD", staging, repo)
    approved = tmp_path / "approved-manifest.txt"
    approved.write_bytes((staging / MANIFEST_NAME).read_bytes())
    return staging, approved


def test_the_exported_tree_verifies_against_its_approved_manifest(exported):
    staging, approved = exported
    assert export_public.verify(staging, approved) == []


def test_a_file_changed_after_the_manifest_was_generated_fails_gate_1(exported):
    staging, approved = exported
    (staging / "pam" / "cli.py").write_text("print('changed after approval')\n")
    assert export_public.verify(staging, approved) == ["changed: pam/cli.py"]


def test_a_private_file_added_after_generation_fails_gate_1(exported):
    staging, approved = exported
    (staging / "docs" / "notes.md").write_text("private\n")
    assert export_public.verify(staging, approved) == ["added: docs/notes.md"]


def test_a_missing_file_fails_gate_1(exported):
    staging, approved = exported
    (staging / "tests" / "test_x.py").unlink()
    assert export_public.verify(staging, approved) == ["missing: tests/test_x.py"]


def test_a_tree_whose_own_manifest_is_not_the_approved_one_fails_gate_1(exported):
    staging, approved = exported
    own = staging / MANIFEST_NAME
    own.write_bytes(own.read_bytes() + b"\n")
    assert export_public.verify(staging, approved) == [
        f"{MANIFEST_NAME} in the tree is not the approved manifest"
    ]


def test_a_tree_without_its_own_manifest_fails_gate_1_and_does_not_crash(exported):
    staging, approved = exported
    (staging / MANIFEST_NAME).unlink()
    assert export_public.verify(staging, approved) == [
        f"{MANIFEST_NAME} in the tree is not the approved manifest"
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is the Windows special folder")
def test_a_special_folder_fails_gate_1(exported, tmp_path):
    import _winapi

    staging, approved = exported
    (tmp_path / "elsewhere").mkdir()
    _winapi.CreateJunction(str(tmp_path / "elsewhere"), str(staging / "pam" / "linked"))
    assert export_public.verify(staging, approved) == ["special file: pam/linked"]


def test_a_symlinked_file_fails_gate_1_even_when_its_bytes_match(exported, monkeypatch):
    # Task 14 review F3 (M4980): the FILE-level special check. A link whose target holds the
    # approved bytes must still fail. Patched, so it runs without symlink privilege.
    staging, approved = exported
    linked = staging / "pam" / "cli.py"
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked or real(self))
    problems = export_public.verify(staging, approved)
    assert "special file: pam/cli.py" in problems


def test_a_git_folder_at_the_root_is_not_part_of_the_tree(exported):
    staging, approved = exported
    (staging / ".git").mkdir()
    (staging / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert export_public.verify(staging, approved) == []


def test_the_cli_exits_1_on_a_changed_tree_and_names_the_file(exported, capsys):
    staging, approved = exported
    (staging / "pam" / "cli.py").write_text("tampered\n")
    assert export_public.main(["--verify", str(staging), str(approved)]) == 1
    assert "x changed: pam/cli.py" in capsys.readouterr().err


# --- gate 2: CI on a checkout ---------------------------------------------------------------------


def test_check_checkout_verifies_a_public_checkout_against_its_own_manifest(exported):
    staging, _ = exported
    assert export_public.check_checkout(staging) == []
    (staging / "pam" / "cli.py").write_text("drifted\n")
    assert export_public.check_checkout(staging) == ["changed: pam/cli.py"]


def test_check_checkout_refuses_a_public_checkout_without_a_manifest(tmp_path):
    (tmp_path / "pam").mkdir()
    assert export_public.check_checkout(tmp_path) == [
        f"{MANIFEST_NAME} is missing: a public checkout must carry the export's manifest"
    ]


def test_check_checkout_says_so_in_the_private_repository(tmp_path, capsys):
    (tmp_path / "tests" / "private").mkdir(parents=True)
    assert export_public.check_checkout(tmp_path) == []
    assert "private repository" in capsys.readouterr().out


# --- this repository, as it is committed ----------------------------------------------------------


@pytest.mark.skipif(
    not (REPO / "tests" / "private").is_dir(),
    reason="the export runs in the private repository",
)
def test_this_repositorys_head_exports_no_private_path_and_every_binary_is_listed(tmp_path):
    """The real allowlist over the real INDEX, and every binary it carries listed.

    The index's tree, not HEAD: under the pre-commit hook it is exactly the commit being made, and
    anywhere else it equals HEAD's tree unless something is staged.
    """
    tree = git(REPO, "write-tree")
    mapping = export_public.plan_export(export_public.tracked(tree))
    assert mapping, "the export selected nothing"
    for source in mapping.values():
        assert (
            not source.startswith(("docs/", "tests/private/")) or source == "docs/user-guide.md"
        )
        assert source not in ("CLAUDE.md", ".pre-commit-config.yaml", ".gitattributes")
        assert not (source.startswith("tools/") and source.count("/") == 1) or (
            source in SPEC_ALLOWED
        ), source
    contents = export_public.read_archive(tree, sorted(mapping.values()))
    files = {name: contents[source] for name, source in mapping.items()}
    export_public.check_binaries(files)  # raises Refused for an unlisted binary
    binaries = sorted(n for n, data in files.items() if export_public.is_binary(data))
    assert len(binaries) >= 100  # the tiny library, the OCR fixtures, the parity image


def test_main_refuses_without_a_commit_and_a_staging_folder(capsys):
    assert export_public.main([]) == 2
    assert "give a COMMIT" in capsys.readouterr().err


def test_main_exports_and_prints_the_manifests_sha256(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, BASE)
    monkeypatch.setattr(export_public, "REPO_ROOT", repo)
    monkeypatch.setattr(export_public.export, "__defaults__", (repo,))
    staging = tmp_path / "staging"
    assert export_public.main(["HEAD", str(staging)]) == 0
    digest = hashlib.sha256((staging / MANIFEST_NAME).read_bytes()).hexdigest()
    assert f"manifest sha256 {digest}" in capsys.readouterr().out


def test_os_walk_finds_no_symlinked_files_in_a_plain_export(exported):
    staging, _ = exported
    assert not any(os.path.islink(p) for p in staging.rglob("*"))

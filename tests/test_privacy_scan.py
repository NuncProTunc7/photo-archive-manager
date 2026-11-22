"""`tools/privacy_scan.py` - the scan over an export's staged tree (M15 Task 14).

EVERY PLANTED STRING IS BUILT BY CONCATENATION, so this module's own source never holds the shape it
plants: the scan reads the exported tree, this file included, and a literal here would be a hit.

The denylist is ALWAYS a fixture in `tmp_path`. The real one belongs to the owner, lives outside the
repository, and is never read by the suite.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import privacy_scan  # noqa: E402
from privacy_scan import Refused  # noqa: E402

# The pattern names the plan requires (path, email, PC name, the four number-in-context shapes),
# plus the private repository's name and private keys. Spelled by hand.
SPEC_PATTERNS = {
    "windows-user-path",
    "posix-home-path",
    "email",
    "pc-name",
    "archive-figure",
    "real-archive",
    "test-folder",
    "owners",
    "private-repository",
    "private-key",
}

FIXTURE_ENTRY_1 = "Zq" + "xwellby"
FIXTURE_ENTRY_2 = "Place " + "Vornhal"


@pytest.fixture
def denylist(tmp_path):
    path = tmp_path / "denylist.txt"
    path.write_text(f"# fixture\n\n{FIXTURE_ENTRY_1}\n{FIXTURE_ENTRY_2}\n", encoding="utf-8")
    return privacy_scan.load_denylist(path)


def hits_for(line: str, denylist, rel: str = "pam/x.py") -> list[str]:
    return [name for _, _, name in privacy_scan.scan_text(rel, line, denylist)]


def test_the_pattern_set_is_the_one_the_plan_names():
    assert set(privacy_scan.PATTERNS) == SPEC_PATTERNS


def test_a_planted_anonymised_archive_observation_is_flagged(denylist):
    planted = "the scan read 312 " + "of 340 real " + "files"
    assert hits_for(planted, denylist) == ["archive-figure"]


def test_the_figure_without_the_word_real_is_flagged_too(denylist):
    assert hits_for("1,204 " + "of 2,000 " + "files", denylist) == ["archive-figure"]


def test_the_enrichers_own_summary_copy_is_not_an_observation(denylist):
    assert hits_for("caption: 2 of 10 files read, 2 results stored", denylist) == []


def test_a_planted_noreply_address_passes(denylist):
    address = "264298919+NuncProTunc7" + "@" + "users.noreply.github.com"
    assert hits_for(f"Author: NuncProTunc7 <{address}>", denylist) == []


def test_a_planted_real_looking_address_is_flagged(denylist):
    assert hits_for("mail someone" + "@" + "gmail.com", denylist) == ["email"]


def test_an_example_domain_and_a_dll_version_pass(denylist):
    assert hits_for("t" + "@" + "example.com", denylist) == []
    assert hits_for("api-ms-win-core" + "@" + "1.dll", denylist) == []
    assert hits_for("mailto:someone" + "@" + "example.invalid", denylist) == []
    assert hits_for("a" + "@" + "pam.test", denylist) == []


@pytest.mark.parametrize(
    "line, name",
    [
        ("C:" + "\\Users\\" + "zqx\\Desktop", "windows-user-path"),
        ("C:" + "/Users/" + "zqx/x", "windows-user-path"),
        ("/home/" + "zqx/photos", "posix-home-path"),
        ("built on " + "DESKTOP-" + "AB12CD3", "pc-name"),
        ("the " + "real " + "archive holds", "real-archive"),
        ("copied to " + "TEST " + "FOLDER", "test-folder"),
        ("the " + "owner" + "'s photos", "owners"),
        ("photo-archive-manager" + "-private", "private-repository"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----", "private-key"),
    ],
)
def test_each_pattern_flags_its_planted_shape(denylist, line, name):
    assert hits_for(line, denylist) == [name]


@pytest.mark.parametrize(
    "line",
    [
        "C:" + "\\Users\\" + "user\\photos",
        "%LOCALAPPDATA%\\Programs\\pam",
        "C:" + "\\Users\\" + "Public\\x",
        "the maintainer's machine",
        "MY-PC",
        "github.com/NuncProTunc7/photo-archive-manager",
    ],
)
def test_the_stand_ins_and_the_public_names_pass(denylist, line):
    assert hits_for(line, denylist) == []


def test_the_planted_lists_are_not_empty():
    # pytest SKIPS an empty parametrize; spelled again so the controls cannot vanish.
    assert len(SPEC_PATTERNS) == 10


def test_a_denylist_hit_names_its_entry_number_and_never_echoes_the_text(denylist, tmp_path):
    staging = tmp_path / "staging"
    (staging / "pam").mkdir(parents=True)
    (staging / "pam" / "x.py").write_text(f"a\n# taken at {FIXTURE_ENTRY_2.lower()}\n")
    count, hits = privacy_scan.scan(staging, denylist)
    assert (count, hits) == (1, [("pam/x.py", 2, "denylist entry 2")])
    report = "\n".join(privacy_scan.report(count, hits))
    assert "Vornhal" not in report and "pam/x.py:2: denylist entry 2" in report


def test_a_denylist_entry_matches_a_whole_word_only(denylist):
    assert hits_for(FIXTURE_ENTRY_1 + "s", denylist) == []
    assert hits_for(f"({FIXTURE_ENTRY_1})", denylist) == ["denylist entry 1"]


@pytest.mark.parametrize(
    "line",
    [
        f"2019-07-04_{FIXTURE_ENTRY_1}_001.jpg",
        f"tests/{FIXTURE_ENTRY_1.lower()}_birthday.jpg",
        f"{FIXTURE_ENTRY_1}2019",
        f"7{FIXTURE_ENTRY_1}",
    ],
)
def test_an_underscore_or_a_digit_beside_an_entry_is_an_edge_not_a_letter(denylist, line):
    # pam's names join tokens with `_` and number them (Task 14 review F2).
    assert hits_for(line, denylist) == ["denylist entry 1"]


def test_a_name_in_a_file_name_is_flagged_as_line_0_without_a_manifest(denylist, tmp_path):
    staging = tmp_path / "staging"
    (staging / "tests").mkdir(parents=True)
    (staging / "tests" / f"2019-07-04_{FIXTURE_ENTRY_1}_001.jpg").write_bytes(b"\xff\xd8")
    count, hits = privacy_scan.scan(staging, denylist)
    rel = f"tests/2019-07-04_{FIXTURE_ENTRY_1}_001.jpg"
    assert (count, hits) == (1, [(rel, 0, "denylist entry 1")])


def test_no_denylist_is_refused_with_a_sentence_naming_the_path(tmp_path, capsys):
    missing = tmp_path / "nowhere.txt"
    with pytest.raises(Refused, match="refuses to run without it") as raised:
        privacy_scan.load_denylist(missing)
    assert str(missing) in str(raised.value)
    (tmp_path / "staging").mkdir()
    assert privacy_scan.main([str(tmp_path / "staging"), "--denylist", str(missing)]) == 2
    assert str(missing) in capsys.readouterr().err


# The format of the file the owner keeps (the rulings): comments, blanks, entries with spaces,
# backslashed paths and accented letters, matched case-insensitively as whole words. Every entry
# is invented.
SHAPED = (
    "\ufeff# a comment line, with a BOM in front of it\n"
    "\n"
    "   # an indented comment\n"
    "Vale " + "Orquin\n"
    "C:\\" + "Users\\" + "zqxw\\\n"
    "Sant" + "\u00e9 Mor\n"
    "Sant" + "e Mor\n"
    "  Bly" + "the  \n"
)


@pytest.fixture
def shaped(tmp_path):
    path = tmp_path / "shaped.txt"
    path.write_text(SHAPED, encoding="utf-8")
    return privacy_scan.load_denylist(path)


def test_the_owners_format_loads_five_entries_skipping_comments_blanks_and_a_bom(shaped):
    assert len(shaped) == 5


@pytest.mark.parametrize(
    "line, expected",
    [
        ("we met at vale " + "orquin today", ["denylist entry 1"]),
        ("vale  " + "orquin", []),  # two spaces: not the entry
        (
            "opened C:\\" + "users\\" + "ZQXW\\Pictures\\a.jpg",
            ["windows-user-path", "denylist entry 2"],
        ),
        ("C:\\" + "Users\\" + "zqxwz\\", ["windows-user-path"]),
        ("SANT" + "\u00c9 MOR, a place", ["denylist entry 3"]),
        ("sant" + "e mor", ["denylist entry 4"]),
        ("Bly" + "the", ["denylist entry 5"]),
        ("Bly" + "thewood", []),  # a whole-word non-match
        ("Sant" + "\u00e9 Morand", []),  # an accented entry is whole-word too
    ],
)
def test_each_shape_of_entry_matches_as_a_whole_word(shaped, line, expected):
    assert hits_for(line, shaped) == expected


def test_the_default_denylist_is_in_the_user_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert privacy_scan.default_denylist() == tmp_path / ".pam-privacy-denylist.txt"


def test_an_empty_denylist_is_refused(tmp_path):
    path = tmp_path / "d.txt"
    path.write_text("# only a comment\n\n")
    with pytest.raises(Refused, match="has no entries"):
        privacy_scan.load_denylist(path)


def test_the_test_only_tls_keys_are_allowed_by_exact_folder_and_suffix(denylist):
    key = "-----BEGIN " + "PRIVATE KEY-----"
    assert hits_for(key, denylist, "tests/fixtures/tls/server.TEST-ONLY.key") == []
    for rel in (
        "tests/fixtures/tls/sub/server.TEST-ONLY.key",
        "tests/fixtures/tls/server.key",
        "tests/fixtures/other/server.TEST-ONLY.key",
        "pam/server.TEST-ONLY.key",
    ):
        assert hits_for(key, denylist, rel) == ["private-key"], rel
    # The exemption is for the key line only: anything else in the file is still scanned.
    assert hits_for("/home/" + "zqx", denylist, "tests/fixtures/tls/a.TEST-ONLY.key") == [
        "posix-home-path"
    ]


def test_a_binary_file_is_scanned_too_and_a_root_git_folder_is_not(denylist, tmp_path):
    staging = tmp_path / "staging"
    (staging / ".git").mkdir(parents=True)
    (staging / ".git" / "config").write_text("C:" + "\\Users\\" + "zqx\\repo\n")
    (staging / "img.png").write_bytes(b"\x89PNG\0\0" + FIXTURE_ENTRY_1.encode() + b"\0")
    count, hits = privacy_scan.scan(staging, denylist)
    assert (count, hits) == (1, [("img.png", 1, "denylist entry 1")])


def test_the_cli_exits_1_on_hits_and_0_when_clean(tmp_path, capsys):
    deny = tmp_path / "d.txt"
    deny.write_text(FIXTURE_ENTRY_1 + "\n")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "a.txt").write_text("nothing private\n")
    assert privacy_scan.main([str(staging), "--denylist", str(deny)]) == 0
    (staging / "b.txt").write_text("the " + "real " + "archive\n")
    assert privacy_scan.main([str(staging), "--denylist", str(deny)]) == 1
    out = capsys.readouterr().out
    assert "b.txt:1: real-archive" in out and "scanned 2 files: 1 hits" in out


def test_the_scan_tool_does_not_flag_its_own_source(denylist):
    for name in ("privacy_scan.py", "export_public.py", "release_pins.py"):
        text = (REPO / "tools" / name).read_text(encoding="utf-8")
        assert privacy_scan.scan_text(f"tools/{name}", text, denylist) == [], name


def test_this_module_does_not_flag_itself(denylist):
    text = Path(__file__).read_text(encoding="utf-8")
    assert privacy_scan.scan_text("tests/test_privacy_scan.py", text, denylist) == []


def test_every_file_the_public_tree_carries_is_clean_on_the_patterns(denylist):
    """The patterns half of gate 1, on every run: in this repository over what the export would
    carry (the allowlist over git's tracked files), in the public one over every tracked file.
    The denylist half runs at the gate itself, never here."""
    import subprocess

    import export_public

    listed = (
        subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
        .stdout.decode("utf-8")
        .split("\0")
    )
    private_repo = (REPO / "tests" / "private").is_dir()
    files = [f for f in listed if f and (export_public.selected(f) or not private_repo)]
    assert len(files) > 400
    hits = []
    for rel in files:
        text = (REPO / rel).read_bytes().decode("utf-8", "replace")
        hits += privacy_scan.scan_text(export_public.public_name(rel), text, denylist)
    assert hits == []


# The maintainer's ruling (2026-11-21): a verbatim third-party licence text is exempt from the DENYLIST only,
# and only while its bytes are the pinned upstream text. The four names are spelled by hand.
LICENCE_FILES = ("GPL-2.0.txt", "LGPL-3.0.txt", "LGPL-2.1.txt", "Apache-2.0.txt")
assert LICENCE_FILES


def _lf_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _planted_licence(tmp_path, monkeypatch, *, pin_it: bool, text: str, rel: str = None):
    rel = rel or "tools/installer/licenses/X-1.0.txt"
    staging = tmp_path / "staging"
    (staging / rel).parent.mkdir(parents=True)
    (staging / rel).write_bytes(text.encode("utf-8"))
    pinned = "tools/installer/licenses/X-1.0.txt"
    monkeypatch.setattr(
        privacy_scan,
        "VERBATIM_LICENCES",
        {pinned: _lf_sha256(text.encode("utf-8")) if pin_it else "0" * 64},
    )
    return staging


def _names(staging, denylist):
    return [name for _, _, name in privacy_scan.scan(staging, denylist)[1]]


def test_a_pinned_verbatim_licence_is_exempt_from_the_denylist(denylist, tmp_path, monkeypatch):
    text = f"licence words\n{FIXTURE_ENTRY_1} is a word here\n"
    staging = _planted_licence(tmp_path, monkeypatch, pin_it=True, text=text)
    assert _names(staging, denylist) == []


def test_an_edited_licence_is_scanned_like_any_file(denylist, tmp_path, monkeypatch):
    text = f"licence words\n{FIXTURE_ENTRY_1} is a word here\n"
    staging = _planted_licence(tmp_path, monkeypatch, pin_it=True, text=text)
    rel = staging / "tools/installer/licenses/X-1.0.txt"
    rel.write_bytes(text.replace("words", "Words").encode("utf-8"))
    assert _names(staging, denylist) == ["denylist entry 1"]


def test_the_same_bytes_at_another_path_are_not_exempt(denylist, tmp_path, monkeypatch):
    text = f"{FIXTURE_ENTRY_1}\n"
    staging = _planted_licence(
        tmp_path, monkeypatch, pin_it=True, text=text, rel="docs/X-1.0.txt"
    )
    assert _names(staging, denylist) == ["denylist entry 1"]


def test_a_pinned_licence_still_gets_the_patterns_and_its_path_checked(
    denylist, tmp_path, monkeypatch
):
    planted = "write to someone@" + "example-mail.org\n"
    staging = _planted_licence(tmp_path, monkeypatch, pin_it=True, text=planted)
    assert _names(staging, denylist) == ["email"]
    named = _planted_licence(
        tmp_path / "b",
        monkeypatch,
        pin_it=True,
        text="plain\n",
        rel=f"tools/installer/licenses/{FIXTURE_ENTRY_1}.txt",
    )
    monkeypatch.setattr(
        privacy_scan,
        "VERBATIM_LICENCES",
        {f"tools/installer/licenses/{FIXTURE_ENTRY_1}.txt": _lf_sha256(b"plain\n")},
    )
    assert _names(named, denylist) == ["denylist entry 1"]


def test_the_exemption_lists_exactly_the_committed_licence_texts_at_their_bytes():
    folder = REPO / "tools" / "installer" / "licenses"
    expected = {
        f"tools/installer/licenses/{name}": _lf_sha256((folder / name).read_bytes())
        for name in LICENCE_FILES
    }
    assert privacy_scan.VERBATIM_LICENCES == expected
    for rel in expected:
        assert privacy_scan.verbatim_licence(rel, (REPO / rel).read_bytes()), rel

"""E5's release manifest, `pam-release.json` (M15 Task 9): every field and every cross-field
relation is validated BEFORE any filesystem use.

THE BAD MANIFESTS ARE A HAND-WRITTEN TABLE, ONE PER RULE, each a GOOD manifest with exactly one
thing wrong, and each row names the words its refusal must contain - so a row cannot pass by
tripping an earlier rule than the one it is about. The good manifest is spelled out by hand.
"""

import json
from pathlib import Path

import pytest

from pam.errors import PamError
from pam.update import manifest as manifest_mod
from pam.update.manifest import ManifestError, parse, validate
from pam.update.version import parse as parse_version

SHA = "0123456789abcdef" * 4
MIGRATIONS = [
    "0001_init.sql",
    "0002_scanner.sql",
    "0003_video_reread.sql",
    "0004_plan_unit_key.sql",
    "0005_plan_already_conforming.sql",
    "0006_event_label_source.sql",
    "0007_face_review_state.sql",
    "0008_files_rel_path_nocase.sql",
]


def good(**changes):
    data = {
        "version": "1.0.0",
        "channel": "stable",
        "installer": "pam-setup-1.0.0.exe",
        "sha256": SHA,
        "size": 123_456_789,
        "schema": "0008_files_rel_path_nocase.sql",
        "migrations": list(MIGRATIONS),
    }
    data.update(changes)
    return data


def without(key):
    data = good()
    del data[key]
    return data


RC = {
    "version": "1.0.0rc2",
    "channel": "rc",
    "installer": "pam-setup-1.0.0rc2.exe",
}

# (id, manifest, tag, words the refusal must contain)
BAD = [
    ("not-an-object", ["1.0.0"], "v1.0.0", "not a JSON object"),
    ("missing-field", without("sha256"), "v1.0.0", "missing sha256"),
    ("unknown-field", good(url="https://example.invalid/x"), "v1.0.0", "unknown url"),
    ("version-not-grammar", good(version="1.0"), "v1.0", "not a pam version"),
    ("version-not-a-string", good(version=1), "v1", "not a pam version"),
    ("version-not-the-tag", good(), "v1.0.1", "does not match the release tag"),
    ("tag-without-v", good(), "1.0.0", "does not match the release tag"),
    ("tag-not-a-string", good(), None, "does not match the release tag"),
    ("channel-unknown", good(channel="beta"), "v1.0.0", "channel"),
    ("channel-rc-for-a-release", good(channel="rc"), "v1.0.0", "channel rc"),
    (
        "channel-stable-for-an-rc",
        {**good(), **RC, "channel": "stable"},
        "v1.0.0rc2",
        "channel stable",
    ),
    ("installer-other-version", good(installer="pam-setup-1.0.1.exe"), "v1.0.0", "installer"),
    ("installer-with-a-folder", good(installer="x/pam-setup-1.0.0.exe"), "v1.0.0", "installer"),
    ("installer-parent", good(installer="..\\pam-setup-1.0.0.exe"), "v1.0.0", "installer"),
    ("installer-device", good(installer="CON"), "v1.0.0", "installer"),
    ("installer-stream", good(installer="pam-setup-1.0.0.exe:x"), "v1.0.0", "installer"),
    ("installer-trailing-dot", good(installer="pam-setup-1.0.0.exe."), "v1.0.0", "installer"),
    ("installer-case", good(installer="PAM-setup-1.0.0.exe"), "v1.0.0", "installer"),
    ("installer-not-a-string", good(installer=None), "v1.0.0", "installer"),
    ("sha256-uppercase", good(sha256=SHA.upper()), "v1.0.0", "sha256"),
    ("sha256-short", good(sha256=SHA[:-1]), "v1.0.0", "sha256"),
    ("sha256-long", good(sha256=SHA + "0"), "v1.0.0", "sha256"),
    ("sha256-not-hex", good(sha256="g" * 64), "v1.0.0", "sha256"),
    ("sha256-not-a-string", good(sha256=0), "v1.0.0", "sha256"),
    ("size-zero", good(size=0), "v1.0.0", "size"),
    ("size-negative", good(size=-1), "v1.0.0", "size"),
    ("size-over-the-cap", good(size=manifest_mod.MAX_INSTALLER_BYTES + 1), "v1.0.0", "size"),
    ("size-a-float", good(size=1.5), "v1.0.0", "size"),
    ("size-a-string", good(size="100"), "v1.0.0", "size"),
    ("size-a-bool", good(size=True), "v1.0.0", "size"),
    ("migrations-not-a-list", good(migrations="0001_init.sql"), "v1.0.0", "migrations"),
    ("migrations-empty", good(migrations=[]), "v1.0.0", "migrations"),
    (
        "migrations-bad-name",
        good(migrations=["0001_init.sql", "0002_Scanner.sql"], schema="0002_Scanner.sql"),
        "v1.0.0",
        "0002_Scanner.sql",
    ),
    (
        "migrations-trailing-newline",
        good(migrations=["0001_init.sql\n"], schema="0001_init.sql\n"),
        "v1.0.0",
        "not a migration filename",
    ),
    (
        "migrations-not-a-string",
        good(migrations=["0001_init.sql", 2], schema="0001_init.sql"),
        "v1.0.0",
        "migrations",
    ),
    (
        "migrations-unsorted",
        good(migrations=["0002_scanner.sql", "0001_init.sql"], schema="0002_scanner.sql"),
        "v1.0.0",
        "sorted",
    ),
    (
        "migrations-duplicate",
        good(migrations=["0001_init.sql", "0001_init.sql"], schema="0001_init.sql"),
        "v1.0.0",
        "more than once",
    ),
    ("schema-understated", good(schema="0007_face_review_state.sql"), "v1.0.0", "schema"),
    ("schema-not-listed", good(schema="0009_future.sql"), "v1.0.0", "schema"),
    ("schema-not-a-string", good(schema=8), "v1.0.0", "schema"),
]


def test_the_good_manifests_are_accepted():
    accepted = validate(good(), tag="v1.0.0")
    assert accepted.version == parse_version("1.0.0")
    assert accepted.channel == "stable"
    assert accepted.installer == "pam-setup-1.0.0.exe"
    assert accepted.sha256 == SHA
    assert accepted.size == 123_456_789
    assert accepted.schema == "0008_files_rel_path_nocase.sql"
    assert accepted.migrations == tuple(MIGRATIONS)
    rc = validate({**good(), **RC}, tag="v1.0.0rc2")
    assert rc.version.is_rc
    assert rc.channel == "rc"


def test_the_bad_table_has_a_row_for_every_field():
    assert len(BAD) >= 35
    named = " ".join(words for _id, _data, _tag, words in BAD)
    for field in manifest_mod.FIELDS:
        assert field in named or field == "version", field


@pytest.mark.parametrize(
    ("data", "tag", "words"),
    [row[1:] for row in BAD],
    ids=[row[0] for row in BAD],
)
def test_a_bad_manifest_is_refused_by_its_own_rule(data, tag, words):
    with pytest.raises(ManifestError) as raised:
        validate(data, tag=tag)
    assert words in str(raised.value)


def test_the_refusal_is_a_pam_error():
    with pytest.raises(PamError):
        validate(good(size=0), tag="v1.0.0")


def test_validation_touches_no_filesystem(monkeypatch):
    """Validated before any filesystem use: the module must not reach for a path at all."""

    def refuse(*args, **kwargs):
        raise AssertionError("manifest validation touched the filesystem")

    for name in ("open", "stat", "exists", "read_bytes", "read_text", "is_file", "resolve"):
        monkeypatch.setattr(Path, name, refuse, raising=False)
    monkeypatch.setattr("builtins.open", refuse)
    # NOTHING MAY FAIL WHILE THE PATCHES ARE IN: pytest reads source files to report a failure,
    # so a failure here was an INTERNALERROR that ended the whole run and hid every later test
    # (M4116, Task 9 review). Collect the outcome, undo, THEN assert.
    raised: list[BaseException] = []
    accepted = []
    try:
        validate(good(), tag="v1.0.0")
        parse(json.dumps(good()), tag="v1.0.0")
        for row_id, data, tag, _words in BAD:
            try:
                validate(data, tag=tag)
            except ManifestError:
                continue
            accepted.append(row_id)
    except BaseException as error:  # noqa: BLE001 - reported after the undo
        raised.append(error)
    monkeypatch.undo()
    assert raised == []
    assert accepted == []


def test_parse_reads_json_text_and_bytes():
    assert parse(json.dumps(good()), tag="v1.0.0").size == 123_456_789
    assert parse(json.dumps(good()).encode("utf-8"), tag="v1.0.0").size == 123_456_789


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{",
        "null",
        b"\xff\xfe{}",
        '{"version": "1.0.0", "version": "1.0.0"}',
    ],
)
def test_text_that_is_not_one_json_object_is_refused(text):
    with pytest.raises(ManifestError):
        parse(text, tag="v1.0.0")


def test_a_duplicate_key_is_refused_even_when_the_last_value_is_good():
    """JSON's last-wins would hide a first value a different reader takes."""
    body = json.dumps(good())[:-1] + ', "size": 123456789}'
    with pytest.raises(ManifestError, match="more than once"):
        parse(body, tag="v1.0.0")


def test_to_json_round_trips():
    accepted = validate(good(), tag="v1.0.0")
    assert parse(manifest_mod.to_json(accepted), tag="v1.0.0") == accepted
    assert json.loads(manifest_mod.to_json(accepted)) == good()

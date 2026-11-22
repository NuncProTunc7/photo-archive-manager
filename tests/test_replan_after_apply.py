"""HT-M14 F1 (AM6): a re-plan after an apply must not churn the names pam has just given.

HT-M14 Leg C found it on a real-world library: after `pam apply`, the next `pam plan` proposed to swap
`{seq}` numbers between files pam had just named (`…Boy-at-sculpture_075` -> `_074`, and back),
and to turn a name the owner had typed back into pam's own proposal.

The causes: `{seq}` ordered a scope by the CURRENT rel_path, and pam's names sort by their tokens
rather than by the originals; and nothing remembered that a name was typed. AM6: `{seq}` orders by
the name BEFORE pam, and a typed name is kept while the template that proposed it is in force.

Every expected name is spelled by hand from design 6.1, and the catalog is read with the suite's
own SQL.
"""

import json
import sqlite3

from archives import FAKE_COMMAND, approve_and_apply

from pam.catalog.db import connect
from pam.naming.planner import run_plan
from pam.paths import workspace_for
from pam.scan.runner import run_scan

TEMPLATE = "{date}_{scene}_{seq}"
DATE = "2024:05:01 12:00:00"  # one second for both files: the seq order is the tiebreak


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def build(tmp_path, labels):
    """Files with ONE capture second, each with a live caption, so `{scene}` is the label."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in labels:
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": DATE, "PamTestSidecar": name}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = _conn(root)
    try:
        for name, label in labels.items():
            sha = conn.execute(
                "SELECT sha256 FROM files WHERE rel_path = ?", (name,)
            ).fetchone()
            conn.execute(
                "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
                " model_version, generated_at) VALUES (?, 'caption', ?, NULL, 'stub', '1',"
                " '2026-11-16T09:00:00')",
                (sha[0], json.dumps({"label": label, "sentence": "A photograph."})),
            )
        conn.commit()
    finally:
        conn.close()
    return root


def items(root, plan_id):
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT id, old_rel_path, generated_filename FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r["old_rel_path"]: (r["id"], r["generated_filename"]) for r in rows}


def type_name(root, item_id, name):
    """What the review page's rename writes for a unit of one: the typed name, approved."""
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE plan_items SET new_filename = ?, status = 'approved' WHERE id = ?",
            (name, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_replan_after_an_apply_keeps_the_seq_numbers_pam_gave(tmp_path):
    # `a.jpg` sorts before `b.jpg`, so a gets 001; but `zebra` sorts after `apple`, so pam's
    # names sort the OTHER way - the shape HT-M14 found.
    root = build(tmp_path, {"a.jpg": "zebra", "b.jpg": "apple"})
    first = run_plan(root, TEMPLATE)
    assert {k: v[1] for k, v in items(root, first.plan_id).items()} == {
        "a.jpg": "2024-05-01_zebra_001.jpg",
        "b.jpg": "2024-05-01_apple_002.jpg",
    }
    applied = approve_and_apply(first.plan_id, root)
    assert (applied.renamed, applied.failed) == (2, 0)

    again = run_plan(root, TEMPLATE)
    # Before the fix: a plan of two items swapping 001 and 002.
    assert again.plan_id is None
    assert again.already_conforming == 2
    assert sorted(p.name for p in root.iterdir() if p.suffix == ".jpg") == [
        "2024-05-01_apple_002.jpg",
        "2024-05-01_zebra_001.jpg",
    ]


def test_a_name_the_user_typed_is_kept_while_the_template_is_unchanged(tmp_path):
    root = build(tmp_path, {"a.jpg": "zebra", "b.jpg": "apple"})
    first = run_plan(root, TEMPLATE)
    got = items(root, first.plan_id)
    type_name(root, got["a.jpg"][0], "my-own-name.jpg")
    applied = approve_and_apply(first.plan_id, root)
    assert (applied.renamed, applied.failed) == (2, 0)
    assert (root / "my-own-name.jpg").exists()

    again = run_plan(root, TEMPLATE)
    # Before the fix: an item proposing `2024-05-01_zebra_001.jpg` for the typed name.
    assert again.plan_id is None
    assert again.already_conforming == 2


def test_a_typed_name_is_kept_after_the_photo_is_edited_in_place(tmp_path):
    """An edit in place keeps the catalog row and changes the size; the name is still the one the
    user typed (AM6). The M3494 survivor: under `name_evidence`'s size rule this re-proposed it."""
    root = build(tmp_path, {"a.jpg": "zebra", "b.jpg": "apple"})
    first = run_plan(root, TEMPLATE)
    type_name(root, items(root, first.plan_id)["a.jpg"][0], "my-own-name.jpg")
    approve_and_apply(first.plan_id, root)
    (root / "my-own-name.jpg").write_bytes(b"the same photograph, edited and saved in place")
    (root / "my-own-name.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": DATE, "PamTestSidecar": "a.jpg"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    again = run_plan(root, TEMPLATE)
    # The typed name is kept, and `b`'s pam name still orders by its pre-pam `b.jpg` (002).
    assert again.plan_id is None
    assert again.already_conforming == 2


def test_a_typed_name_is_offered_again_once_the_template_changes(tmp_path):
    """The negative control: AM6 keeps a typed name, it does not freeze it forever."""
    root = build(tmp_path, {"a.jpg": "zebra", "b.jpg": "apple"})
    first = run_plan(root, TEMPLATE)
    got = items(root, first.plan_id)
    type_name(root, got["a.jpg"][0], "my-own-name.jpg")
    approve_and_apply(first.plan_id, root)

    again = run_plan(root, "{scene}_{date}_{seq}")
    assert {k: v[1] for k, v in items(root, again.plan_id).items()} == {
        "my-own-name.jpg": "zebra_2024-05-01_001.jpg",
        "2024-05-01_apple_002.jpg": "apple_2024-05-01_002.jpg",
    }


def test_a_generated_name_is_not_kept_as_if_typed(tmp_path):
    """The other control: `typed_under` is only for TYPED names. A file pam named and the user
    then renamed outside pam is the user's, and planned normally (name_evidence's rule)."""
    root = build(tmp_path, {"a.jpg": "zebra", "b.jpg": "apple"})
    first = run_plan(root, TEMPLATE)
    approve_and_apply(first.plan_id, root)
    (root / "2024-05-01_zebra_001.jpg").rename(root / "renamed-by-hand.jpg")
    (root / "renamed-by-hand.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": DATE, "PamTestSidecar": "a.jpg"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    again = run_plan(root, TEMPLATE)
    # The hand-named file orders by its CURRENT name, `renamed-by-hand.jpg`; the other still
    # holds pam's name and orders by its pre-pam `b.jpg`. b < r, so the numbers trade places.
    assert {k: v[1] for k, v in items(root, again.plan_id).items()} == {
        "renamed-by-hand.jpg": "2024-05-01_zebra_002.jpg",
        "2024-05-01_apple_002.jpg": "2024-05-01_apple_001.jpg",
    }

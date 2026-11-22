"""The suite's own approval helpers (M13 Task 1, F1) - tested, because they are oracles.

`approve_all` stands behind every test that applies a plan after F1: if it approved a `skipped`
item, every "a skip is not renamed" test would be exercising a plan the user never decided that
way; if it reached another plan, two-plan tests would apply approvals nobody gave. CLAUDE.md:
mutate the code that TESTS the code - so the code that tests the code has a test.
"""

from archives import approve_all, approve_ids

from pam.catalog import repo
from pam.catalog.db import connect, migrate

NOW = "2026-10-09T09:00:00"


def _plan(conn, names):
    plan_id = repo.create_plan(
        conn,
        now=NOW,
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=4,
    )
    drafts = []
    for name in names:
        file_id = repo.upsert_file(
            conn,
            rel_path=f"p{plan_id}/{name}",
            filename=name,
            ext=name.rsplit(".", 1)[-1].lower(),
            media_type="image",
            size=10,
            mtime_ns=123,
            placeholder=False,
            now=NOW,
        )
        drafts.append(
            repo.PlanItemDraft(
                file_id=file_id,
                old_rel_path=f"p{plan_id}/{name}",
                unit_key=f"u-{plan_id}-{name}",
                generated_filename=f"new_{name}",
                tokens_json="[]",
                tier="low",
                trust_score=10,
                trust_reasons_json="[]",
            )
        )
    repo.insert_plan_items(conn, plan_id, drafts)
    conn.commit()
    return plan_id


def _statuses(conn, plan_id):
    """The test's own SQL: {old_rel_path: status}."""
    return dict(
        conn.execute(
            "SELECT old_rel_path, status FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    )


def test_approve_all_leaves_skipped_and_touches_only_its_plan(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    try:
        migrate(conn)
        p1 = _plan(conn, ["A.JPG", "B.JPG", "C.JPG"])
        p2 = _plan(conn, ["D.JPG", "E.JPG"])
        conn.execute(
            "UPDATE plan_items SET status = 'skipped' WHERE old_rel_path = ?",
            (f"p{p1}/B.JPG",),
        )
        conn.commit()

        approve_all(conn, p1)

        # Spelled by hand: Low is approved too (it is the suite's helper, not --all).
        assert _statuses(conn, p1) == {
            f"p{p1}/A.JPG": "approved",
            f"p{p1}/B.JPG": "skipped",
            f"p{p1}/C.JPG": "approved",
        }
        assert _statuses(conn, p2) == {f"p{p2}/D.JPG": "proposed", f"p{p2}/E.JPG": "proposed"}
    finally:
        conn.close()


def test_approve_ids_approves_exactly_the_ids_it_is_given(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    try:
        migrate(conn)
        p1 = _plan(conn, ["A.JPG", "B.JPG", "C.JPG"])
        ids = dict(
            conn.execute(
                "SELECT old_rel_path, id FROM plan_items WHERE plan_id = ?", (p1,)
            ).fetchall()
        )

        approve_ids(conn, [ids[f"p{p1}/C.JPG"]])

        assert _statuses(conn, p1) == {
            f"p{p1}/A.JPG": "proposed",
            f"p{p1}/B.JPG": "proposed",
            f"p{p1}/C.JPG": "approved",
        }
    finally:
        conn.close()

"""`pam report duplicates` (design 8.1, 12; M13 Task 9, amendment H2).

Step 0's cut: the subject moved whole from `pam/report.py` to `pam/report_duplicates.py`, and
the first check is that it has ONE definition and no second import path through the old module.

H2's look-alikes, on users' archives built from the spec 9.1 tiny library's decodable pictures,
scanned by the real scanner and fingerprinted by the real `pam enrich phash`. Every expected
group is spelled out by hand from H2's rules - by content, present files, a companion unit
alone is not reported - never read back from the report.
"""

import ast
import io
import sqlite3
import sys
from pathlib import Path

from archives import FAKE_COMMAND, put
from PIL import Image

import pam.report_duplicates as duplicates_mod
from pam.cli import main
from pam.enrich.command import run_enrich
from pam.paths import workspace_if_exists
from pam.report_duplicates import run_report_duplicates
from pam.scan.runner import run_scan

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_tiny_library  # noqa: E402

REPO_ROOT = Path(duplicates_mod.__file__).resolve().parents[1]

# The names Step 0 moved, spelled by hand from the plan (never read back from either module).
# `EXACT_ONLY` moved too, and H2 then DELETED it: its "arrives in a later version" was false.
MOVED = frozenset(
    {"DuplicateReport", "run_report_duplicates", "duplicate_lines", "print_duplicates"}
)
DELETED = frozenset({"EXACT_ONLY"})
assert MOVED and DELETED


def _top_level_definitions(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imported_from(tree: ast.Module, module: str) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _attributes_of(tree: ast.Module, alias: str) -> set[str]:
    """`alias.X` attribute reads - `import pam.report as report_mod; report_mod.X`."""
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == alias
    }


def _aliases_of(tree: ast.Module, module: str) -> set[str]:
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == module
    }


def test_the_moved_names_have_one_definition_and_no_re_export():
    new_path = REPO_ROOT / "pam" / "report_duplicates.py"
    old_path = REPO_ROOT / "pam" / "report.py"
    new_defined = _top_level_definitions(ast.parse(new_path.read_text(encoding="utf-8")))
    old_defined = _top_level_definitions(ast.parse(old_path.read_text(encoding="utf-8")))
    # The positive half: every moved name IS defined in the new module.
    assert new_defined >= MOVED
    # ONE DEFINITION: the old module defines none of them again.
    assert not old_defined & MOVED, f"report.py still defines {sorted(old_defined & MOVED)}"
    # H2 deleted `EXACT_ONLY`; neither module may define it again.
    revived = (new_defined | old_defined) & DELETED
    assert not revived, f"{sorted(revived)} was deleted by H2 and is defined again"
    # NO RE-EXPORT: nothing in pam/, tools/ or tests/ reaches a moved name through report.py,
    # by `from pam.report import X` or by `import pam.report as m; m.X`.
    sources = (
        sorted((REPO_ROOT / "pam").rglob("*.py"))
        + sorted((REPO_ROOT / "tools").glob("*.py"))
        + sorted((REPO_ROOT / "tests").glob("*.py"))
    )
    assert new_path in sources and len(sources) > 100, "the walk stopped reaching the tree"
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        through_old = _imported_from(tree, "pam.report") & MOVED
        for alias in _aliases_of(tree, "pam.report"):
            through_old |= _attributes_of(tree, alias) & MOVED
        assert not through_old, f"{path.name} reaches {sorted(through_old)} through report.py"


# --- H2: look-alikes ----------------------------------------------------------------------

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
BASE = (LIBRARY / "phash_pattern_base.png").read_bytes()
NEAR = (LIBRARY / "phash_pattern_near.png").read_bytes()  # 2 bits from BASE (test_phash.py)
FAR = (LIBRARY / "phash_pattern_far.png").read_bytes()  # 64 bits from BASE
RAW = (LIBRARY / "raw_preview.ARW").read_bytes()
RAW_PREVIEW = build_tiny_library.raw_preview_payloads("raw_preview.ARW")["PreviewImage"]


def _resized_jpeg(payload: bytes, factor: int) -> bytes:
    """The same picture at another size and in another format: new bytes, one shot."""
    with Image.open(io.BytesIO(payload)) as image:
        rgb = image.convert("RGB")
    larger = rgb.resize((rgb.width * factor, rgb.height * factor))
    out = io.BytesIO()
    larger.save(out, "JPEG", quality=90)
    return out.getvalue()


def _archive(tmp_path: Path, files: dict[str, bytes], toml: str | None = None) -> Path:
    """Scan with the fake exiftool, then the real `pam enrich phash`."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for name, payload in files.items():
        put(root, name, content=payload)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    if toml is not None:
        ws = workspace_if_exists(root)
        assert ws is not None
        ws.config_path.write_text(toml, encoding="utf-8")
    run_enrich("phash", root)
    return root


def _fingerprinted(root: Path) -> set[str]:
    """Which files have a live phash fact - the precondition, read through the suite's SQL."""
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = sqlite3.connect(ws.db_path)
    try:
        rows = conn.execute(
            "SELECT f.rel_path FROM files f JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.kind = 'phash' AND i.superseded_by IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


H2_ARCHIVE = {
    "base.png": BASE,
    "base_large.JPG": _resized_jpeg(BASE, 2),  # the same shot at twice the size
    "far.png": FAR,
    "far_copy.png": FAR,  # an exact duplicate whose content looks like nothing else
    "DSC_5.ARW": RAW,  # a RAW and its JPEG: one companion unit, one picture
    "DSC_5.JPG": RAW_PREVIEW,
    "broken.JPG": b"not a picture at all",  # an image with no fingerprint
    "clip.MOV": b"a video, never compared",
}


def test_look_alikes_are_grouped_by_content_and_a_companion_unit_alone_is_not_one(tmp_path):
    root = _archive(tmp_path, H2_ARCHIVE)
    # Preconditions, or the expectations below prove nothing: both companion members and the
    # exact pair really have fingerprints (the RAW's comes from its embedded preview through
    # exiftool - absent exiftool this is RED, never skipped), and the broken file has none.
    assert _fingerprinted(root) == set(H2_ARCHIVE) - {"broken.JPG", "clip.MOV"}

    report = run_report_duplicates(root)

    assert [g.rel_paths for g in report.groups] == [("far.png", "far_copy.png")]
    # base + its resized copy: one group. The exact pair is ONE content, so it is not a
    # look-alike of itself. The RAW+JPEG unit is one shot pam keeps together: not reported.
    assert report.look_alikes == (("base.png", "base_large.JPG"),)
    assert (report.fingerprinted, report.images) == (6, 7)


def test_the_output_is_h2s_copy(tmp_path, capsys):
    root = _archive(tmp_path, H2_ARCHIVE)
    capsys.readouterr()
    assert main(["report", "duplicates", "--archive", str(root)]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert "Exact duplicates - byte-for-byte identical: 1 group, 2 files" in lines
    assert (
        "Look-alikes - photos that look the same (the same shot at two sizes, or edited"
        " and unedited): 1 group, 2 files"
    ) in lines
    assert "  1. base.png, base_large.JPG" in lines
    assert (
        "pam compares the picture fingerprints `pam enrich phash` makes: 6 of 7 images have"
        " one. Run pam enrich phash for the rest."
    ) in lines
    assert "Videos are not compared. pam deletes nothing - this is a list for you." in lines
    assert not any("DSC_5" in line for line in lines), "a companion unit is not a look-alike"


def test_a_group_reaching_outside_one_companion_unit_is_reported_whole(tmp_path):
    """The negative control for the companion rule: it drops a group that IS one unit,
    never a group that merely CONTAINS one."""
    root = _archive(
        tmp_path,
        {
            "DSC_5.ARW": RAW,
            "DSC_5.JPG": RAW_PREVIEW,
            "print.JPG": _resized_jpeg(RAW_PREVIEW, 2),
        },
    )
    assert _fingerprinted(root) == {"DSC_5.ARW", "DSC_5.JPG", "print.JPG"}

    assert run_report_duplicates(root).look_alikes == (("DSC_5.ARW", "DSC_5.JPG", "print.JPG"),)


def test_with_every_image_fingerprinted_the_coverage_sentence_asks_for_nothing(
    tmp_path, capsys
):
    root = _archive(tmp_path, {"base.png": BASE, "far.png": FAR})
    capsys.readouterr()
    main(["report", "duplicates", "--archive", str(root)])
    lines = capsys.readouterr().out.splitlines()

    assert (
        "pam compares the picture fingerprints `pam enrich phash` makes: 2 of 2 images"
        " have one."
    ) in lines
    assert "  pam found no look-alikes among the photos it compared." in lines


def test_a_missing_files_content_is_not_compared(tmp_path):
    root = _archive(tmp_path, {"base.png": BASE, "base_large.JPG": _resized_jpeg(BASE, 2)})
    assert run_report_duplicates(root).look_alikes == (("base.png", "base_large.JPG"),)

    (root / "base_large.JPG").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    report = run_report_duplicates(root)
    assert report.look_alikes == ()
    assert (report.fingerprinted, report.images) == (1, 1)


def test_the_distance_is_the_configured_near_dupe_distance(tmp_path):
    """BASE and NEAR are 2 bits apart (`tests/test_phash.py`, from the library's builder):
    grouped at the default 8, not at a configured 1."""
    files = {"base.png": BASE, "near.png": NEAR}
    assert run_report_duplicates(_archive(tmp_path / "a", files)).look_alikes == (
        ("base.png", "near.png"),
    )
    toml = "[enrich.phash]\nnear_dupe_distance = 1\n"
    narrow = _archive(tmp_path / "b", files, toml=toml)
    assert run_report_duplicates(narrow).look_alikes == ()


# --- Task 9 review: closing tests (M13 review of H2) ---------------------------------------


def test_the_old_module_does_not_carry_a_moved_name_at_runtime():
    """M2507: a re-export in `report.py` that nobody imports through is still a second import
    path. Read the old module's NAMESPACE, not its consumers."""
    import pam.report as report_mod

    carried = MOVED & set(vars(report_mod))
    assert not carried, f"pam.report still carries {sorted(carried)}"


def test_a_placeholders_content_is_neither_compared_nor_counted(tmp_path):
    """M2501/M2502: a file that became a cloud placeholder AFTER it was fingerprinted (the
    sync client freed its space) keeps its live phash fact. H2: look-alikes are expanded to
    present, NON-PLACEHOLDER files, and the coverage counts what pam could compare."""
    root = _archive(tmp_path, {"base.png": BASE, "base_large.JPG": _resized_jpeg(BASE, 2)})
    assert run_report_duplicates(root).look_alikes == (("base.png", "base_large.JPG"),)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = sqlite3.connect(ws.db_path)
    try:
        with conn:
            changed = conn.execute(
                "UPDATE files SET placeholder = 1 WHERE rel_path = 'base_large.JPG'"
            ).rowcount
    finally:
        conn.close()
    assert changed == 1

    report = run_report_duplicates(root)
    assert report.look_alikes == ()
    assert (report.fingerprinted, report.images) == (1, 1)


def test_two_companion_units_that_look_alike_are_reported(tmp_path):
    """M2498: the companion rule drops a group that is ONE unit. Two units - a burst shot
    twice in RAW+JPEG - are two shots, and every file of both is listed."""
    root = _archive(
        tmp_path,
        {
            "DSC_5.ARW": RAW,
            "DSC_5.JPG": RAW_PREVIEW,
            "DSC_6.ARW": RAW,
            "DSC_6.JPG": _resized_jpeg(RAW_PREVIEW, 2),
        },
    )
    assert _fingerprinted(root) == {"DSC_5.ARW", "DSC_5.JPG", "DSC_6.ARW", "DSC_6.JPG"}

    assert run_report_duplicates(root).look_alikes == (
        ("DSC_5.ARW", "DSC_5.JPG", "DSC_6.ARW", "DSC_6.JPG"),
    )


def test_groups_are_ordered_by_first_path_and_paths_within_a_group_are_sorted(tmp_path):
    """M2505/M2506: the docstring's "sorted paths, ordered by first path". Content order
    (sha256) is arbitrary, so the same two groups are built under both namings - in one of
    them content order and path order must disagree. The second file of each group is added
    by a LATER scan under a name that sorts first, so catalog order is not path order."""
    for first, second in (("a", "m"), ("m", "a")):
        root = _archive(tmp_path / first, {f"{first}_z.png": BASE, f"{second}_z.png": FAR})
        put(root, f"{first}_b.JPG", content=_resized_jpeg(BASE, 2))
        put(root, f"{second}_b.JPG", content=_resized_jpeg(FAR, 2))
        run_scan(root, exiftool_command=FAKE_COMMAND)
        run_enrich("phash", root)

        expected = tuple(
            sorted(
                [(f"{first}_b.JPG", f"{first}_z.png"), (f"{second}_b.JPG", f"{second}_z.png")]
            )
        )
        assert run_report_duplicates(root).look_alikes == expected, (first, second)

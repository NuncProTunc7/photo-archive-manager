"""M12 Task 10, the unit half: `[enrich.ocr]` (P10), the newest-reading read (P7/I15), and
`naming/dates.py` (P9, P11) called directly.

EVERY EXPECTATION IS SPELLED BY HAND FROM THE SPEC - design 6.1 rev 27, 6.4 rev 27 and 11 -
and never produced by calling `resolve_date`, `find_date` or a production constant. The
sentences are written out here in full; a test that formatted `dates.OCR_READ` would agree with
any value of it (M6's ENRICHERS, M8's M922/M924). The end-to-end half, driven through `pam plan`
and the review API, is `tests/test_ocr_naming.py`.
"""

import json
import logging
import sqlite3

import pytest
from archives import FAKE_COMMAND, put

from pam.catalog import repo
from pam.catalog.db import connect
from pam.config import Config, TierBounds, load_config
from pam.errors import ConfigError
from pam.naming.consistency import score
from pam.naming.dates import in_folders, ocr_date_deductions, resolve_date
from pam.naming.ocr_dates import OcrLine
from pam.naming.template import EmittedToken
from pam.naming.tokens import TokenFact
from pam.paths import workspace_for
from pam.scan.runner import run_scan

# Design 11's shipped `[tiers] ocr` row, spelled from the spec: floor 0.5, low 0.7, high 0.9.
BOUNDS = TierBounds(0.5, 0.7, 0.9)
SCANS = ("Scans",)
MTIME = "2024-05-01T12:00:00"


def write_toml(tmp_path, body):
    path = tmp_path / "pam.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- P10: config


def test_the_ocr_defaults_are_design_11s(tmp_path):
    """`folders = []` (EMPTY - pam never guesses which pictures are scans) and `workers = 1`."""
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg.ocr_folders == ()
    assert cfg.ocr_workers == 1


def test_folders_are_kept_exactly_as_written(tmp_path):
    """Not lower-cased and not stripped of a dot like an extension: "." is an entry."""
    cfg = load_config(
        write_toml(tmp_path, '[enrich.ocr]\nfolders = ["Scans", ".", "Family\\\\Old Scans"]\n')
    )
    assert cfg.ocr_folders == ("Scans", ".", "Family\\Old Scans")


def test_workers_override_and_its_range(tmp_path):
    assert load_config(write_toml(tmp_path, "[enrich.ocr]\nworkers = 4\n")).ocr_workers == 4
    assert load_config(write_toml(tmp_path, "[enrich.ocr]\nworkers = 1\n")).ocr_workers == 1
    assert load_config(write_toml(tmp_path, "[enrich.ocr]\nworkers = 32\n")).ocr_workers == 32
    for bad in (0, 33):
        with pytest.raises(ConfigError, match="ocr workers must be between 1 and 32"):
            load_config(write_toml(tmp_path, f"[enrich.ocr]\nworkers = {bad}\n"))


# Each of P10's rules, with the phrase its refusal must carry. Hand-written, and asserted
# non-empty below: an emptied parametrize is SKIPPED, not failed (M743).
REFUSED_FOLDERS = (
    ('""', "is empty"),
    ('"   "', "is empty"),
    ('"C:/Scans"', "names a drive"),
    ('"c:Scans"', "names a drive"),
    ('"/Scans"', "starts with a separator"),
    ('"\\\\Scans"', "starts with a separator"),
    ('"Scans/../Other"', "contains '..'"),
    ('"Scans\\\\..\\\\Other"', "contains '..'"),
    ('".."', "contains '..'"),
)
assert len(REFUSED_FOLDERS) >= 9


@pytest.mark.parametrize(("entry", "phrase"), REFUSED_FOLDERS)
def test_each_p10_rule_is_its_own_refusal_naming_the_entry(tmp_path, entry, phrase):
    with pytest.raises(ConfigError) as caught:
        load_config(write_toml(tmp_path, f"[enrich.ocr]\nfolders = [{entry}]\n"))
    assert phrase in str(caught.value)
    assert "[enrich.ocr] folders entry" in str(caught.value)


def test_legal_entries_load(tmp_path):
    body = '[enrich.ocr]\nfolders = ["Scans/2019", "Scans\\\\2020", "./Old", ".", "a..b"]\n'
    assert load_config(write_toml(tmp_path, body)).ocr_folders == (
        "Scans/2019",
        "Scans\\2020",
        "./Old",
        ".",
        "a..b",
    )


def test_folders_must_be_a_list_of_strings(tmp_path):
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load_config(write_toml(tmp_path, '[enrich.ocr]\nfolders = "Scans"\n'))


# --------------------------------------------------------------------------- P10: in_folders

# (rel_path, folders, covered), spelled from design 11: by path COMPONENT, case-insensitive,
# "." the whole archive, '/' or '\' separators.
FOLDER_ROWS = (
    ("Scans/a.jpg", ("Scans",), True),
    ("scans/b.jpg", ("Scans",), True),
    ("SCANS/b.jpg", ("scans",), True),
    ("Scans-old/c.jpg", ("Scans",), False),
    ("ScansX/c.jpg", ("Scans",), False),
    ("Scans/2019/d.jpg", ("Scans",), True),
    ("Other/Scans/e.jpg", ("Scans",), False),
    ("a.jpg", ("Scans",), False),
    ("Scans", ("Scans",), False),  # a FILE named Scans at the root is in no folder
    ("a.jpg", (".",), True),
    ("x/y.jpg", (".",), True),
    ("Scans/2019/a.jpg", ("Scans\\2019",), True),
    ("Scans/a.jpg", ("Scans/2019",), False),
    ("Scans/a.jpg", ("Scans/",), True),
    ("Scans/a.jpg", ("./Scans",), True),
    ("Scans/a.jpg", (), False),
    ("Scans/a.jpg", ("",), False),  # blank matches nothing - never the whole archive
    ("Scans/a.jpg", ("Other", "SCANS"), True),
)
assert len(FOLDER_ROWS) >= 18


@pytest.mark.parametrize(("rel_path", "folders", "covered"), FOLDER_ROWS)
def test_in_folders_is_by_component_and_case_insensitive(rel_path, folders, covered):
    assert in_folders(rel_path, folders) is covered


# --------------------------------------------------------------------------- the read (I15)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def catalog(tmp_path):
    """Two scanned files, and a connection to their catalog. Facts are the suite's own SQL."""
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "a.png")
    put(root, "b.png")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    ids = {
        str(r["rel_path"]): (int(r["id"]), str(r["sha256"]))
        for r in conn.execute("SELECT id, rel_path, sha256 FROM files")
    }
    yield conn, ids
    conn.close()


def add_ocr(conn, sha256, value, *, version="d1", kind="ocr_text", dead=False):
    cur = conn.execute(
        "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
        " model_version, generated_at) VALUES (?, ?, ?, NULL, 'ppocr-v4', ?, ?)",
        (sha256, kind, value if isinstance(value, str) else json.dumps(value), version, "2026"),
    )
    fact_id = int(cur.lastrowid)
    if dead:
        conn.execute("UPDATE inferred_facts SET superseded_by = ? WHERE id = ?", (fact_id,) * 2)
    conn.commit()
    return fact_id


def read(conn):
    handler = ListHandler()
    logger = logging.getLogger("pam.tests.ocr_read")
    logger.propagate = False
    logger.handlers[:] = [handler]
    return repo.ocr_readings_by_file_id(conn, logger), handler.messages


def lines(*pairs):
    return {"lines": [{"text": text, "score": score} for text, score in pairs]}


def test_the_HIGHEST_id_is_the_reading(catalog):
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    add_ocr(conn, sha, lines(("1998-07-04", 0.8)), version="first")
    newer = add_ocr(conn, sha, lines(("nothing", 0.9)), version="second")
    got, _ = read(conn)
    assert got[file_id] == repo.OcrReading(newer, (OcrLine("nothing", 0.9),))


def test_the_newer_reading_wins_even_when_its_identity_sorts_LOWER(catalog):
    """A digest has no order (P7): the identity string never decides, the id does."""
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    add_ocr(conn, sha, lines(("old", 0.8)), version="zzzz")
    newer = add_ocr(conn, sha, lines(("new", 0.8)), version="aaaa")
    assert read(conn)[0][file_id].fact_id == newer


def test_a_MALFORMED_newest_row_gives_NO_reading_and_never_the_older_one(catalog):
    """Design 6.1 rev 27: selected FIRST, validated SECOND (pass 2, A1). Logged."""
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    add_ocr(conn, sha, lines(("1998-07-04", 0.8)), version="first")
    bad = add_ocr(conn, sha, {"text": "RECEIPT"}, version="second")
    got, messages = read(conn)
    assert file_id not in got
    assert messages == [f"ocr_text fact {bad} is malformed; file {file_id} has no OCR reading"]


def test_a_superseded_row_is_not_live(catalog):
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    older = add_ocr(conn, sha, lines(("kept", 0.8)), version="first")
    add_ocr(conn, sha, lines(("dead", 0.8)), version="second", dead=True)
    assert read(conn)[0][file_id].fact_id == older


def test_an_empty_line_list_is_a_reading(catalog):
    """A photograph with no text in it is covered (P7) - a reading with no lines."""
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    fact = add_ocr(conn, sha, {"lines": []})
    assert read(conn)[0] == {file_id: repo.OcrReading(fact, ())}


def test_only_the_ocr_text_kind_is_read_and_each_file_gets_its_own(catalog):
    conn, ids = catalog
    a_id, a_sha = ids["a.png"]
    b_id, b_sha = ids["b.png"]
    add_ocr(conn, a_sha, lines(("caption-ish", 0.8)), kind="caption")
    b_fact = add_ocr(conn, b_sha, lines(("1998-07-04", 1)))
    got, messages = read(conn)
    assert got == {b_id: repo.OcrReading(b_fact, (OcrLine("1998-07-04", 1.0),))}
    assert messages == []
    assert isinstance(got[b_id].lines[0].score, float)


# Every shape design 6.4 rev 27 refuses, hand-written: `lines` a list, each entry EXACTLY
# `text` (str) and `score` (a number, not a bool, in [0, 1]).
MALFORMED = (
    ("rev 1's shape", {"text": "RECEIPT"}),
    ("lines is not a list", {"lines": "1998-07-04"}),
    ("an extra top-level key", {"lines": [], "date": "1998-07-04"}),
    ("not an object", ["1998-07-04"]),
    ("an entry that is not an object", {"lines": ["1998-07-04"]}),
    ("an entry with an extra key", {"lines": [{"text": "x", "score": 0.5, "box": 1}]}),
    ("an entry missing its score", {"lines": [{"text": "x"}]}),
    ("a text that is not a string", {"lines": [{"text": 1998, "score": 0.5}]}),
    ("a boolean score", {"lines": [{"text": "x", "score": True}]}),
    ("a string score", {"lines": [{"text": "x", "score": "0.5"}]}),
    ("a score above 1", {"lines": [{"text": "x", "score": 1.5}]}),
    ("a score below 0", {"lines": [{"text": "x", "score": -0.1}]}),
    ("not JSON at all", "{lines:"),
)
assert len(MALFORMED) >= 13


@pytest.mark.parametrize(("why", "value"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_a_malformed_row_is_skipped_with_a_log_line(catalog, why, value):
    conn, ids = catalog
    file_id, sha = ids["a.png"]
    fact = add_ocr(conn, sha, value)
    got, messages = read(conn)
    assert got == {}, why
    assert messages == [f"ocr_text fact {fact} is malformed; file {file_id} has no OCR reading"]


# --------------------------------------------------------------------------- P9: resolve_date


def reading(*pairs, fact_id=7):
    return repo.OcrReading(fact_id, tuple(OcrLine(text, score) for text, score in pairs))


def resolve(**kw):
    args = dict(
        capture_dt=MTIME,
        capture_dt_source="file_mtime",
        rel_path="Scans/a.png",
        reading=reading(("1998-07-04", 0.8)),
        bounds=BOUNDS,
        folders=SCANS,
    )
    args.update(kw)
    return resolve_date(**args)


def test_all_four_conditions_hold_the_ocr_date_is_the_effective_date():
    got = resolve()
    assert got.capture_dt == "1998-07-04T00:00:00"
    assert got.source == "ocr"
    assert got.fact_id == 7
    assert got.tier == "medium"
    assert got.found is not None and got.found.confidence == 0.8
    assert got.reasons == (
        'pam read "1998-07-04" in the photo as 1998-07-04 (80% confidence).',
    )


@pytest.mark.parametrize("source", ["exif", "filename", "qt_utc", "qt_local", "none", None])
def test_condition_1_alone_false_any_other_source_is_kept(source):
    got = resolve(capture_dt_source=source)
    assert (got.capture_dt, got.source, got.fact_id, got.reasons) == (
        MTIME,
        source or "none",
        None,
        (),
    )


def test_condition_2_alone_false_out_of_the_folders():
    got = resolve(rel_path="Screenshots/a.png")
    assert (got.capture_dt, got.source, got.reasons) == (MTIME, "file_mtime", ())


def test_condition_3_alone_false_the_only_date_is_below_the_floor_and_SILENT():
    got = resolve(reading=reading(("1998-07-04", 0.45)))
    assert (got.capture_dt, got.source, got.reasons) == (MTIME, "file_mtime", ())


def test_condition_3_the_floor_is_the_configured_one():
    got = resolve(reading=reading(("1998-07-04", 0.45)), bounds=TierBounds(0.4, 0.7, 0.9))
    assert got.source == "ocr" and got.tier == "low"


def test_condition_4_alone_false_a_date_LATER_than_the_file_is_not_used_and_said():
    got = resolve(reading=reading(("2024-05-02", 0.8)))
    assert (got.capture_dt, got.source, got.fact_id) == (MTIME, "file_mtime", None)
    assert got.reasons == (
        'pam read "2024-05-02" in the picture as 2024-05-02, which is later than the file'
        " itself, so it did not use it.",
    )


def test_condition_4_a_date_EQUAL_to_the_files_is_used():
    """Not LATER than: the same day is not later."""
    got = resolve(reading=reading(("2024-05-01", 0.8)))
    assert got.source == "ocr" and got.capture_dt == "2024-05-01T00:00:00"


def test_no_reading_no_upgrade():
    assert resolve(reading=None).source == "file_mtime"


# (score, tier, the provenance sentence's percentage). Design 6.1 rev 27: `low` below
# `[tiers] ocr.low` (0.7), else `medium` - NEVER `high`, not even at 0.99.
TIER_ROWS = (
    (0.5, "low", 50),
    (0.6, "low", 60),
    (0.7, "medium", 70),
    (0.8, "medium", 80),
    (0.9, "medium", 90),
    (0.99, "medium", 99),
    (1.0, "medium", 100),
)
assert len(TIER_ROWS) >= 7


@pytest.mark.parametrize(("line_score", "tier", "pct"), TIER_ROWS)
def test_the_tier_and_the_provenance_sentence_at_every_score(line_score, tier, pct):
    got = resolve(reading=reading(("1998-07-04", line_score)))
    assert got.tier == tier
    assert got.reasons == (
        f'pam read "1998-07-04" in the photo as 1998-07-04 ({pct}% confidence).',
    )


def test_the_sentence_quotes_the_matched_text_not_the_whole_line():
    got = resolve(reading=reading(("Printed July 4, 1998 by Kodak", 0.8)))
    assert got.reasons == (
        'pam read "July 4, 1998" in the photo as 1998-07-04 (80% confidence).',
    )


# --------------------------------------------------------------------------- P11: the score

OCR_ROW = "Date read from text in the photo - no camera or filename date."
BELOW_HIGH = "That reading is below pam's high-confidence mark ([tiers] ocr high = 0.9)."


def test_both_deductions_below_the_high_mark():
    got = resolve(reading=reading(("1998-07-04", 0.6)))
    assert ocr_date_deductions(got, BOUNDS) == ((10, OCR_ROW), (10, BELOW_HIGH))


@pytest.mark.parametrize("line_score", [0.9, 0.99])
def test_at_or_above_the_high_mark_only_the_source_row(line_score):
    got = resolve(reading=reading(("1998-07-04", line_score)))
    assert ocr_date_deductions(got, BOUNDS) == ((10, OCR_ROW),)


def test_no_deduction_unless_p9_applied():
    assert ocr_date_deductions(resolve(capture_dt_source="exif"), BOUNDS) == ()
    assert ocr_date_deductions(resolve(reading=reading(("2025-01-01", 0.6))), BOUNDS) == ()


def test_the_high_mark_is_the_configured_one():
    got = resolve(reading=reading(("1998-07-04", 0.9)))
    bounds = TierBounds(0.5, 0.7, 0.95)
    assert ocr_date_deductions(got, bounds) == (
        (10, OCR_ROW),
        (10, "That reading is below pam's high-confidence mark ([tiers] ocr high = 0.95)."),
    )


DATE = EmittedToken("date", "date", "1998-07-04", True)
PEOPLE = EmittedToken("people", "people", "Ann", True)
SEQ = EmittedToken("seq", "seq", "001", True)


def _facts():
    return {
        "date": TokenFact("1998-07-04", "inferred_facts", 7, "low", confidence=0.6),
        "seq": TokenFact("001", "planner", None, "high"),
        "people": TokenFact("Ann", "persons", 1, "high"),
    }


def test_the_deductions_are_charged_BEFORE_the_clamp():
    """100 - 10 - 10 + 5 = 85. After the clamp it would be min(100, 105) - 20 = 80."""
    points, reasons = score(
        _facts(),
        [],
        date_source="ocr",
        emitted=(DATE, PEOPLE, SEQ),
        date_deductions=((10, OCR_ROW), (10, BELOW_HIGH)),
    )
    assert (points, reasons) == (85, [OCR_ROW, BELOW_HIGH])


def test_the_people_bonus_on_a_100_still_clamps():
    assert score(
        _facts(), [], date_source="exif", emitted=(DATE, PEOPLE, SEQ), date_deductions=()
    ) == (100, [])


def test_no_date_token_no_deduction():
    """N2: a name with no date carries no claim about one."""
    assert score(
        _facts(),
        [],
        date_source="ocr",
        emitted=(SEQ,),
        date_deductions=((10, OCR_ROW), (10, BELOW_HIGH)),
    ) == (100, [])


def test_date_deductions_is_required():
    with pytest.raises(TypeError):
        score(_facts(), [], date_source="ocr", emitted=(DATE,))  # type: ignore[call-arg]


def test_config_has_no_ocr_defaults_that_differ_from_the_dataclass():
    """The dataclass defaults ARE the no-file defaults - one source for both."""
    assert Config().ocr_folders == () and Config().ocr_workers == 1

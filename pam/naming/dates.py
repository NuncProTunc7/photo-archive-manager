"""A file's EFFECTIVE date at plan time: design 6.1 rev 27's OCR upgrade (P9), pure.

`ocr_date` is the one source in design 6.1's hierarchy that is resolved at PLAN time and
never written to `capture` (vision 6a.2: inferred facts never overwrite objective data).
`resolve_date` decides it, and BOTH of `pam plan`'s consumers call it - step 0's event
partition for every file, and the name for each unit's primary - so every file is grouped
under the date its name carries (I3). Nothing here touches the catalog: the reading arrives
from `repo.ocr_readings_by_file_id`, the bounds and the folders from `Config`.
"""

import re
from dataclasses import dataclass, replace

from pam.catalog import repo
from pam.config import TierBounds
from pam.naming.consistency import TRUST_BELOW_HIGH, TRUST_OCR_DATE
from pam.naming.ocr_dates import FoundDate, find_date

# Design 6.1 rev 27 condition 4's disclosure, verbatim: a date the picture shows that is
# LATER than the file holding it is a misread or a date not about the picture.
OCR_AFTER_FILE = (
    'pam read "{text}" in the picture as {date}, which is later than the file itself,'
    " so it did not use it."
)
# Design 6.4 rev 27 (P11, the gate's B6): EVERY emitted OCR date carries its confidence,
# whatever it is, first in `trust_reasons_json` and separate from every deduction.
OCR_READ = 'pam read "{text}" in the photo as {date} ({pct}% confidence).'
# Design 6.4 rev 27's two score rows' sentences, verbatim.
OCR_DATE_REASON = "Date read from text in the photo - no camera or filename date."
OCR_BELOW_HIGH = "That reading is below pam's high-confidence mark ([tiers] ocr high = {high})."

# The one `capture_dt_source` design 6.1 lets an OCR date replace (condition 1).
_UPGRADABLE = "file_mtime"
_SEPARATORS = re.compile(r"[\\/]")


@dataclass(frozen=True)
class ResolvedDate:
    capture_dt: str | None
    source: str  # the file's capture_dt_source, or "ocr" when P9 applied
    fact_id: int | None  # the ocr_text reading's id, iff source == "ocr"
    found: FoundDate | None  # iff source == "ocr"
    tier: str | None  # "low" | "medium" iff source == "ocr"
    reasons: tuple[str, ...]  # condition 4's disclosure, or P11's provenance sentence


def _parts(path: str) -> list[str]:
    return [part.casefold() for part in _SEPARATORS.split(path) if part not in ("", ".")]


def in_folders(rel_path: str, folders: tuple[str, ...]) -> bool:
    """P10: is `rel_path`'s FOLDER under one of `folders`? Case-insensitive, by COMPONENT.

    By component and never by string prefix: `Scans` covers `Scans/a.jpg` and
    `scans/2019/b.jpg`, and does NOT cover `Scans-old/c.jpg` (pass 2, B2). "." is the whole
    archive (it has no components). An entry that is blank is matched to NOTHING rather
    than to the whole archive - `config._check_ocr_folders` refuses one at load, and this
    is the reading that fails closed if one ever got here.
    """
    directory = _parts(rel_path)[:-1]
    for folder in folders:
        if not folder.strip():
            continue
        parts = _parts(folder)
        if directory[: len(parts)] == parts:
            return True
    return False


def resolve_date(
    *,
    capture_dt: str | None,
    capture_dt_source: str | None,
    rel_path: str,
    reading: repo.OcrReading | None,
    bounds: TierBounds,
    folders: tuple[str, ...],
) -> ResolvedDate:
    """P9: the file's EFFECTIVE date. The ONE function both of `pam plan`'s callers use.

    An OCR date is used only when all four of design 6.1 rev 27's conditions hold: the
    source is `file_mtime`; the path is under `[enrich.ocr] folders` as configured NOW; the
    newest reading's lines at or above `[tiers] ocr.floor` yield a date under P8; and that
    date is not LATER than the file's own modification date. Tier `low` below
    `[tiers] ocr.low`, else `medium` - never `high`.

    THE RESOLVED `capture_dt` IS `YYYY-MM-DDT00:00:00`, the form `scan/objective.py::
    parse_filename_date` writes for the other day-precision source, so every `capture_dt`
    the planner and step 0 hold has one shape.
    """
    base = ResolvedDate(capture_dt, capture_dt_source or "none", None, None, None, ())
    if reading is None or capture_dt_source != _UPGRADABLE:
        return base
    if not in_folders(rel_path, folders):
        return base
    found = find_date(reading.lines, floor=bounds.floor)
    if found is None:
        return base
    if capture_dt is None or found.date > capture_dt[:10]:
        sentence = OCR_AFTER_FILE.format(text=found.text, date=found.date)
        return replace(base, reasons=(sentence,))
    tier = "low" if found.confidence < bounds.low else "medium"
    pct = round(found.confidence * 100)
    read = OCR_READ.format(text=found.text, date=found.date, pct=pct)
    return ResolvedDate(f"{found.date}T00:00:00", "ocr", reading.fact_id, found, tier, (read,))


def ocr_date_deductions(
    resolved: ResolvedDate, bounds: TierBounds
) -> tuple[tuple[int, str], ...]:
    """P11's two score rows, as (points, sentence). Empty unless P9 applied.

    `date read from text in the photo -10`, always; and the table's `token confidence <
    high bound -10`, when the line's score is STRICTLY below `[tiers] ocr.high`. The caller
    (`consistency.score`) charges them only when the date token reached the name (N2), and
    before its clamp.
    """
    if resolved.source != "ocr" or resolved.found is None:
        return ()
    rows = [(TRUST_OCR_DATE, OCR_DATE_REASON)]
    if resolved.found.confidence < bounds.high:
        rows.append((TRUST_BELOW_HIGH, OCR_BELOW_HIGH.format(high=bounds.high)))
    return tuple(rows)


def resolve_files(
    files: list[repo.PlanFile],
    readings: dict[int, repo.OcrReading],
    *,
    bounds: TierBounds,
    folders: tuple[str, ...],
) -> tuple[list[repo.PlanFile], dict[int, ResolvedDate]]:
    """`pam plan`'s naming half of P9: every file resolved, and the upgraded ones replaced.

    Returns the files with each UPGRADED file's `capture_dt` and `capture_dt_source`
    (`ocr`) replaced - every other `PlanFile` exactly as it was read - and the
    `ResolvedDate` of every file by id, which `resolve_tokens` and `assess_unit` read for a
    unit's primary. The planner calls this BEFORE `_units`, so `{seq}` sees the effective
    date too.
    """
    dates = {
        f.id: resolve_date(
            capture_dt=f.capture_dt,
            capture_dt_source=f.capture_dt_source,
            rel_path=f.rel_path,
            reading=readings.get(f.id),
            bounds=bounds,
            folders=folders,
        )
        for f in files
    }
    resolved = [
        replace(f, capture_dt=dates[f.id].capture_dt, capture_dt_source="ocr")
        if dates[f.id].source == "ocr"
        else f
        for f in files
    ]
    return resolved, dates

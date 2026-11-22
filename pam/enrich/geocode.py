"""Design 8.1's `geocode` enricher, and design 8.3's event labels. No pixels at all.

WHY THIS ENRICHER READS THE CATALOG AND NOT THE FILE. Design 8.1 rev 3: "**geocode**
serves videos: it reads GPS from the `capture` row, not pixels, so `{place}` and event
membership work for videos for free." That single sentence is the whole shape of this
module - it never opens an image, never touches the `PILImageLoader` the Protocol hands
it, and depends on neither Pillow nor pillow-heif. A cloud placeholder is served too and
must be: its coordinates were read at scan time and re-reading the file would hydrate it
(vision R2), which is exactly what NOT opening the file guarantees.

`FileRecord` IS `repo.FileRow` AND CARRIES NO GPS, and the runner hands `wants()` a
`FileRecord` and no connection. The coordinates are therefore looked up ONCE, before the
run, by `repo.gps_by_file_id` - a query in `catalog/repo/capture.py`, the module that
already owns the `capture` table, so no guard list moved - and handed to the enricher at
construction. `wants()` is then a dict membership test, which matters because the runner
asks it for every uncached file on every run, forever, and does not cache a refusal.

`reverse_geocoder` IS IMPORTED INSIDE FUNCTIONS, NOT AT MODULE SCOPE, on Task 8's
measured precedent. Measured here, best of seven: `pam --version` costs 0.181 s, and
`import reverse_geocoder` adds 0.32 s of that again - it pulls `scipy` eagerly, and it is
the heaviest of design 1's four packages. Its 7.8 MB dataset is separately lazy and costs
another ~0.28 s on the first query. Nothing in `pam scan`, `pam plan`, `pam apply`,
`pam undo`, `pam review` or `pam doctor` needs a place name at import time, so
`tests/test_deps.py::EAGER_IMPORT_CLOSURE` stays `{click, pam}` and
`tests/test_geocode.py` re-proves the deferral for this file by name.

`mode=1` IS MANDATORY AND IS A CORRECTNESS RULE, NOT A PERFORMANCE ONE. `search()`
defaults to `mode=2`, which fans the query out over `multiprocessing`; driven from a
library module on Windows there is no `__main__` guard to add, the spawned children die
with a bootstrapping `RuntimeError`, and `search()` returns THE DATASET'S FIRST ROW for
every coordinate with exit code 0 and nothing raised. Measured twice: London and San
Francisco both resolved to *El Tarter, Andorra*. That writes a wrong place name into a
filename, which is the one thing this product must not do.
`tests/test_deps.py::test_no_call_into_reverse_geocoder_under_pam_uses_its_default_mode`
is an AST guard over `pam/` with a planted-source control; the local alias below is named
`rg` because that is one of the receivers the guard recognises, and renaming it would hide
these calls from it.

`verbose=False` IS NOT COSMETIC EITHER. `reverse_geocoder` prints "Loading formatted
geocoded file..." to **stdout** when it builds its index, and `pam plan`'s stdout is the
user's summary. `search()` is `@singleton`-decorated, so the FIRST call in the process
fixes both `mode` and `verbose` for every later one and the warm-up below is what makes
that first call ours.
"""

import importlib
import importlib.util
import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from pam.enrich.base import FileRecord, InferredFact, PILImageLoader

NAME = "geocode"

# The identity the runner's cache and resume are keyed on. The "model" here is the
# DATASET: `rg_cities1000.csv` is what turns a coordinate into a name, and a different
# dataset is what would move the answers. It is not `reverse_geocoder.__version__`, for
# the reason `phash` gives about `imagehash.__version__` - a patch release that changes
# no place name would invalidate every cached fact in the archive.
MODEL_NAME = "rg_cities1000"
MODEL_VERSION = "1.0"

# Per-file cost for the ETA's prior (design 8.1). A query against a cKDTree already in
# memory is microseconds; the runner replaces this with a measured moving average after
# twenty files, so it only has to be the right order of magnitude. The dataset load is
# paid once for the whole run and is not a per-file cost.
COST_ESTIMATE_MS = 1

# (distribution, import name), following `phash._REQUIREMENTS`. The distribution name is
# what a user types to install it, so it is what `available()` must say - the two happen
# to be spelled the same here and the pair is kept anyway, because a message that says
# "pam doctor lists it" has to name the same thing `pam doctor` does.
_DISTRIBUTION, _MODULE = "reverse_geocoder", "reverse_geocoder"

# The vocabulary `events.label_source` may hold (migration 0006). Declared here because
# this module is the only writer of 'geocode'; `repo.events.LABEL_SOURCES` is the table
# the catalog side reads and this constant is what a caller of this module passes on.
LABEL_SOURCE = "geocode"

_LOAD_LOCK = threading.Lock()
_warmed = False


class GeocodeUnavailableError(RuntimeError):
    """`reverse_geocoder` is missing, or its offline dataset is. Carries the reason."""


def _import() -> Any:
    """Import `reverse_geocoder`, naming the DISTRIBUTION rather than raising ImportError."""
    try:
        return importlib.import_module(_MODULE)
    except ImportError as error:
        raise GeocodeUnavailableError(
            f"{_DISTRIBUTION} is not installed ({error}); pam doctor lists it"
        ) from error


def _dataset_present() -> bool:
    """Is the offline CSV on disk? Answered WITHOUT importing the package.

    `pam.doctor` already owns this question and `pam doctor` already prints the answer,
    so this reuses doctor's two names rather than growing a second reading of where the
    file lives - two artefacts spelling the same path would agree with each other and
    prove nothing about either. The reason it is asked at all is vision S4: with the CSV
    missing, `reverse_geocoder` DOWNLOADS it from `download.geonames.org` over plain
    HTTP, so "the geocoder is offline" is true exactly while the file exists.
    """
    from pam.doctor import _geocoder_dataset

    try:
        spec = importlib.util.find_spec("reverse_geocoder")
    except (ImportError, ValueError):  # a broken or half-removed installation
        return False
    if spec is None:
        return False
    dataset = _geocoder_dataset(spec)
    return dataset is not None and dataset.is_file()


def _geocoder() -> Any:
    """The imported module, with its dataset already built. Safe to call from a thread.

    `reverse_geocoder.search` is `@singleton`-decorated: the first call in the process
    constructs the index and every later call reuses it, IGNORING the `mode` and
    `verbose` it is passed. The warm-up here is what makes that first call ours - it
    fixes `mode=1` and silences the loader's stdout line for the whole process - and the
    lock is because the runner calls `enrich()` from a `ThreadPoolExecutor` and four
    threads racing into a 7.8 MB index build would each pay for it.
    """
    global _warmed
    rg = _import()
    with _LOAD_LOCK:
        if not _warmed:
            if not _dataset_present():
                raise GeocodeUnavailableError(
                    "reverse_geocoder's offline dataset is missing, so a lookup would"
                    " download it over the network; pam doctor says how to restore it"
                )
            try:
                rg.search([(0.0, 0.0)], mode=1, verbose=False)
            except Exception as error:  # a corrupt CSV, a broken scipy, a read failure
                raise GeocodeUnavailableError(
                    f"reverse_geocoder could not build its offline index ({error})"
                ) from error
            _warmed = True
    return rg


def _name_of(result: Mapping[str, Any]) -> str:
    """The place name design 6.1 asks for: "city or named place"."""
    return str(result.get("name") or "").strip()


def place_names(
    coordinates: Sequence[tuple[float, float]],
) -> list[str]:
    """One place name per coordinate, in order. Empty string where there is no name.

    Batched deliberately: `search` takes a list, the index build is the expensive part
    and is paid once, and design 8.3's labels are computed for every event of a plan at
    the same moment. `enrich()` calls it with a list of one, which is the same code path
    the batch takes and not a second one.
    """
    if not coordinates:
        return []
    rg = _geocoder()
    points = [(float(lat), float(lon)) for lat, lon in coordinates]
    results = rg.search(points, mode=1, verbose=False)
    return [_name_of(result) for result in results]


def event_labels(
    centroids: Sequence[tuple[float, float] | None],
    *,
    logger: logging.Logger | None = None,
) -> list[str | None] | None:
    """Design 8.3's labels: "the geocoded place of the event's GPS centroid, else empty".

    Returns one entry per centroid - a name, or `None` where the event has no GPS at all
    - or `None` for the WHOLE LIST when the geocoder is unavailable. The two are
    different instructions to `repo.replace_events` and must not be conflated: a list
    says "these are the labels, authoritatively, clear the ones that are None", while
    `None` says "this run learned nothing about labels, leave what is there alone". An
    archive planned on a machine without `reverse_geocoder` keeps the labels a machine
    with it wrote, and `pam plan` still works - which is design 6.1's graceful
    degradation, not a silent failure: an event with an empty label emits no `{event}`
    token at all.
    """
    log = logger or logging.getLogger("pam")
    positioned = [(index, c) for index, c in enumerate(centroids) if c is not None]
    if not positioned:
        return [None] * len(centroids)
    try:
        names = place_names([c for _, c in positioned])
    except GeocodeUnavailableError as error:
        log.info("event labels skipped: %s", error)
        return None
    labels: list[str | None] = [None] * len(centroids)
    for (index, _), name in zip(positioned, names, strict=True):
        labels[index] = name or None
    return labels


class GeocodeEnricher:
    """Design 8.1's `geocode`. Satisfies `enrich.Enricher`; the runner owns the rest."""

    name = NAME
    model_name = MODEL_NAME
    model_version = MODEL_VERSION

    def __init__(
        self,
        coordinates: Mapping[int, tuple[float, float]],
        logger: logging.Logger | None = None,
    ) -> None:
        """`coordinates` is `repo.gps_by_file_id(conn)` - file id -> (lat, lon).

        Passed in rather than looked up here because an enricher gets no connection
        (`enrich/base.py`: `_enrich_one` is module-level with `co_freevars == ()`, and
        `check_same_thread=True` is the backstop). A file absent from this mapping has no
        GPS, which is the same answer `wants()` gives for a file with only one of the two
        columns populated - `repo.gps_by_file_id` requires both.
        """
        self._coordinates = coordinates
        self._log = logger or logging.getLogger("pam")

    def available(self) -> tuple[bool, str]:
        """Design 8.1's dependency check. A missing package is a sentence, never a crash.

        The dataset is checked as well as the package, and separately, because they fail
        differently: no package is an ImportError, and no dataset is a network request
        (vision S4). `pam doctor` prints the same two lines; this is the check the run
        itself makes before it starts.
        """
        try:
            _geocoder()
        except GeocodeUnavailableError as error:
            return False, str(error)
        return True, f"{MODEL_NAME} offline dataset loaded; no network"

    def cost_estimate_ms(self) -> int:
        return COST_ESTIMATE_MS

    def wants(self, file: FileRecord) -> bool:
        """Design 8.1's own example, verbatim: "geocode: has GPS".

        NO MEDIA-TYPE TEST, and that is the point of this enricher rather than an
        omission from it - design 8.1 rev 3 gives videos to geocode by name. No
        placeholder test either: nothing here opens the file, so there is no hydration to
        refuse, and a placeholder whose coordinates the scanner already recorded is a
        placeholder pam can name without touching a byte of it.
        """
        return file.id in self._coordinates

    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]:
        """One `place` fact. `image` is accepted to satisfy the Protocol and never used.

        The kind is `place`, not `geocode` - design 6.1's token is `{place}` and design
        6.4's validation rule names `place` - and `repo.enrichment._FACT_KINDS` maps this
        enricher to it. No confidence: a nearest-city lookup in an offline table is a
        measurement, not an inference, and design 6.4 says confidence is NULL or in
        [0, 1]. NULL is the honest value.

        A coordinate the dataset cannot name yields NO fact rather than an empty one.
        Design 6.4 would drop an empty `place` at the boundary anyway ("`place` tokens
        must be non-empty after sanitization"), but a dropped fact is counted and logged
        as malformed output, and a name the dataset simply does not have is not
        malformed - it is an absence, and `wants()` cannot know it in advance.
        """
        coordinate = self._coordinates.get(file.id)
        if coordinate is None:
            return []
        (name,) = place_names([coordinate])
        if not name:
            self._log.info("geocode: no place name for %s", file.rel_path)
            return []
        return [InferredFact(kind="place", value={"place": name}, confidence=None)]

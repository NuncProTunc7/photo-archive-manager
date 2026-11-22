"""Design 8.1's `ocr` enricher (rev 27, P7; rev 28, Q2): the text in a scanned photograph.

WHAT IT EMITS - EXACTLY ONE FACT PER FILE READ. `ocr_text`, `{"lines": [{"text", "score"},
...]}` in the engine's reading order, POSSIBLY EMPTY, `confidence` NULL. It is the reading
AND the per-file coverage marker (I8): a photograph OCR looked at and found no text in is
covered and is not read again. NO DATE IS COMPUTED HERE - the date is derived at plan time
from the stored lines (P7, P8, P9), so a grammar change or a new floor re-reads nothing, and
no score filter is applied here or in the engine (I16): the floor is the plan's.

AN ENGINE THAT RAISES EMITS NOTHING. The exception reaches the runner, which counts the file
failed and writes no fact, so the next run reads it again (I8). That includes a failed
initialisation: the engine is lazy (R10) and stays uninitialised after a failure, so the next
file's read retries it.

ONE LAZY `ocr_model.Engine` PER ENRICHER (I17, R10), built here and initialised by its first
`read` under its own lock. A run that reads nothing builds no session. Decoding, `upright` and
`convert("RGB")` happen HERE, outside that lock (R9: `pil_image_loader` returns the raster as
decoded, un-rotated, and `upright` converts no mode), so several workers overlap their decoding
while the engine is called one image at a time.

`wants()` IS FOUR OPERANDS, ALL REQUIRED (P7): an image (an ALLOWLIST, as faces'), not a cloud
placeholder (opening one hydrates it - vision R2), dated by `file_mtime` (design 6.1's
hierarchy lets an OCR date replace that and nothing else), and under `[enrich.ocr] folders`
(P10 - opt-in, never inferred). The `file_mtime` set is read by the builder before the run,
on `geocode`'s precedent: an enricher is handed no connection.
"""

import logging
import threading
from collections.abc import Callable
from contextlib import closing
from typing import Any

from pam.enrich import ocr_model
from pam.enrich.base import FileRecord, InferredFact, PILImageLoader
from pam.enrich.upright import upright
from pam.naming.dates import in_folders

NAME = "ocr"

# The ETA's prior (design 1 rev 29, K8, ruling R42-E): the median page at P-1's 2000-px working
# size was 384 ms; 400 is its round-up. The runner replaces it with a measurement after twenty
# files, so it only has to be the right order of magnitude.
COST_ESTIMATE_MS = 400


class OcrEnricher:
    """Design 8.1's `ocr`. Satisfies `enrich.Enricher`; the runner owns everything else.

    Construction is cheap and total: the identity is a digest of constants (Q2) and the engine
    stores its arguments. `available()` is where a machine with no extra or no pack gets its
    sentence - Q4's, verbatim, from `ocr_model.describe`.
    """

    name = NAME

    def __init__(
        self,
        model_dir: str,
        folders: tuple[str, ...],
        mtime_file_ids: frozenset[int],
        logger: logging.Logger | None = None,
        *,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._model_dir = model_dir
        self._folders = tuple(folders)
        self._mtime_file_ids = frozenset(mtime_file_ids)
        self._log = logger or logging.getLogger("pam")
        self.model_name, self.model_version = ocr_model.identity(model_dir)
        self._engine = ocr_model.Engine(model_dir, session_factory=session_factory)
        self._lock = threading.Lock()
        # N9's shape (design 12 rev 26, Q4): `model_name model_version` once a read has
        # SUCCEEDED, for the closing block's `  model:` line; None while nothing was loaded.
        self.loaded_model: str | None = None

    def available(self) -> tuple[bool, str]:
        return ocr_model.describe(self._model_dir)

    def cost_estimate_ms(self) -> int:
        return COST_ESTIMATE_MS

    def wants(self, file: FileRecord) -> bool:
        """P7's four operands. Each is a reason NOT to open the file, and none opens it."""
        return (
            file.media_type == "image"
            and not file.placeholder
            and file.id in self._mtime_file_ids
            and in_folders(file.rel_path, self._folders)
        )

    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]:
        """One `ocr_text` fact. Decoded, turned upright and made RGB OUTSIDE the engine's lock."""
        with image(file) as decoded:
            frame = upright(decoded).raster
            try:
                with closing(frame.convert("RGB")) as rgb:  # Image.__exit__ is `pass`
                    lines = self._engine.read(rgb)
            finally:
                if frame is not decoded:
                    frame.close()
        self._note_receipt()
        return [
            InferredFact(
                kind="ocr_text",
                value={"lines": [{"text": line.text, "score": line.score} for line in lines]},
            )
        ]

    def _note_receipt(self) -> None:
        """N9's receipt, logged ONCE and only after a read has succeeded (the engine's own
        receipt is published only by a successful initialisation)."""
        with self._lock:
            if self.loaded_model is None:
                self._log.info("enrich ocr: %s", self._engine.receipt())
                self.loaded_model = f"{self.model_name} {self.model_version}"

"""Spec 8.1's enrichment: the interface, the runner, and (from M10) the enrichers.

Flat re-exports, following `pam.catalog.repo`: a caller does `from pam import enrich`
and then `enrich.run_enricher(...)`. Every enricher M10-M12 adds imports `Enricher`,
`FileRecord` and `InferredFact` from here rather than from `base`, so the interface has
one name in the codebase even if the module it lives in is ever split.

AND THE ENRICHERS THEMSELVES ARE NAMED HERE (`m9.md` C4). The sentence above says every
enricher imports its interface from this module; an enricher the front door does not name
back is the convention contradicting itself on its first two instances. `CaptionEnricher`
joined them in M10 Task 8, `FacesEnricher` in M11 and `OcrEnricher` in M12 Task 13.

`FACT_SCHEMAS` is deliberately NOT re-exported. It is spec 6.4's validation table and
its only production reader is the runner; a front-door name would invite an enricher to
consult the schema it is supposed to satisfy, and a test to build its expectation from
the very table it is checking - the vacuous oracle this project has now found in a
query (M5's `repo.open_plan`), a constant (M6's `ENRICHERS`) and a partition (M8).
Import it from `pam.enrich.validate` where that is what you actually mean.
"""

from pam.enrich.base import COMMIT_BATCH as COMMIT_BATCH
from pam.enrich.base import Enricher as Enricher
from pam.enrich.base import EnrichSummary as EnrichSummary
from pam.enrich.base import FileRecord as FileRecord
from pam.enrich.base import InferredFact as InferredFact
from pam.enrich.base import PILImageLoader as PILImageLoader
from pam.enrich.base import Progress as Progress
from pam.enrich.base import run_enricher as run_enricher
from pam.enrich.base import version_key as version_key
from pam.enrich.caption import CaptionEnricher as CaptionEnricher
from pam.enrich.eta import ALPHA as ALPHA
from pam.enrich.eta import PRIOR_FILES as PRIOR_FILES
from pam.enrich.eta import Eta as Eta
from pam.enrich.faces import FacesEnricher as FacesEnricher
from pam.enrich.geocode import GeocodeEnricher as GeocodeEnricher
from pam.enrich.ocr import OcrEnricher as OcrEnricher
from pam.enrich.phash import PhashEnricher as PhashEnricher

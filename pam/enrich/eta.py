"""Spec 8.1's ETA: the enricher's prior, then an EWMA of what actually happened.

Split out of `base.py` rather than inlined into the runner for one reason: an ETA
folded into a loop can only be tested through that loop, and a test that drives the
runner and then asserts the ETA equals what the runner computed is the vacuous oracle
this project keeps finding (M6's `ENRICHERS`, M8's partition lesson). Here the two
constants are pinned against the spec sentence and the recurrence is checked against
numbers worked out by hand.

Spec 8.1, quoted so the constants below have a source and not a preference:
"ETA uses `cost_estimate_ms` only as the prior; after 20 processed files it switches
to an exponentially weighted moving average (alpha = 0.1) of observed per-file times."

And spec 8.1 rev 16, quoted for the same reason, because rev 15 left the formula to
inference and this module inferred it wrongly:
"The remaining time is `per_file_ms x files_remaining / min(workers, files_remaining)`.
`files_remaining` counts only files the runner does not already know it will skip - a
file with no `sha256`, and a file already covered by a non-superseded fact from this
`(name, model_name, model_version)`. ... the ETA is a display, never a contract, and it
must never be the input to a timeout, a retry or a batch size."
"""

from dataclasses import dataclass, field

# Spec 8.1. Both are the spec's numbers, not tuned values, and neither is derived from
# the other or from anything in the code.
PRIOR_FILES = 20
ALPHA = 0.1


@dataclass
class Eta:
    """Per-file cost, and the remaining time derived from it.

    `observed` counts files whose time was actually MEASURED, which is the only reading
    of "processed files" that makes the sentence coherent: a file the cache skipped and
    a file `wants()` rejected produce no per-file time to average, so counting them
    would move the switchover without ever moving the average.

    The average is seeded with the FIRST observation rather than with the prior. Spec
    8.1 says the EWMA is "of observed per-file times"; seeding it with
    `cost_estimate_ms` would make the first twenty averages a blend of the prior and
    the observations, and the prior is exactly what the switch exists to stop using.
    """

    prior_ms: int
    observed: int = 0
    _ewma_ms: float = field(default=0.0, repr=False)

    def observe(self, elapsed_ms: float) -> None:
        """Record one measured per-file time."""
        self._ewma_ms = (
            elapsed_ms
            if self.observed == 0
            else ALPHA * elapsed_ms + (1.0 - ALPHA) * self._ewma_ms
        )
        self.observed += 1

    def per_file_ms(self) -> float:
        """The prior until `PRIOR_FILES` files have been measured, the EWMA after.

        "after 20 processed files" is read as `>= 20`: at the twentieth measurement the
        average is over twenty observations and the prior has served its purpose. The
        boundary is the one thing here a mutation can move by one without changing any
        arithmetic, so it is pinned by a test at 19 and at 20 rather than only in the
        middle of a run.
        """
        return float(self.prior_ms) if self.observed < PRIOR_FILES else self._ewma_ms

    def remaining_ms(self, files_remaining: int, workers: int) -> float:
        """Spec 8.1 rev 16: `per_file_ms x files_remaining / min(workers, files_remaining)`.

        The division by the worker count IS the spec's formula as of rev 16. It was not
        as of rev 15, and this docstring used to argue that adding it "would be this
        module inventing a formula the normative section does not contain" - true when
        it was written, false the moment rev 16 was adopted, and the suite stayed green
        over the contradiction. `files_remaining` is the caller's job: the runner counts
        only files it does not already know it will skip (no `sha256`, or already
        covered), which is the half of rev 16 that lives in `base.py`.

        `min(workers, files_remaining)` is zero on the last tick of every run, so the
        zero case returns before the division rather than after it. That is arithmetic,
        not a policy: no files left is no time left.
        """
        remaining = max(0, files_remaining)
        if remaining == 0:
            return 0.0
        return remaining * self.per_file_ms() / min(workers, remaining)

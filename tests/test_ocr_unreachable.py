"""M13 Task 7 (C7; design 8.1, S6(b)): two geometry branches of the OCR pipeline cannot fire.

P-2's `big_h <= 0 or big_w <= 0` refusal (`ocr_detect.det_size` returns None) and P-6's
post-unclip drop (`boxes` skips a box whose `min(w, h) + 2d < MIN_SIZE + 2`) are steps the
reference has, and pam keeps them. At the table's constants neither can fire inside the domain
P-1 admits. S6 says a test pins that, so the day a constant changes the branch's reachability is
re-decided by a RED test and not by a quiet change in what pam reads.

EVERY LITERAL IS SPELLED HERE FROM DESIGN 8.1's TABLE (30, 2000, 736, 4096, 3, 1.6) and the
module's constants are asserted equal to them, so a constant change turns this red instead of
silently widening or shrinking the sweep. The expectation is computed by SECOND IMPLEMENTATIONS in
exact `Fraction` arithmetic, never by calling the production symbol that is being judged.

HOW THE BRANCH IS SPIED, WITHOUT TOUCHING PRODUCTION. `det_size` returns None on exactly one path
(the `<= 0` branch) and otherwise returns a tuple or raises, so a None IS the branch having fired.
For P-6 the production call itself is driven: `boxes()` over real blobs must keep every component
that clears P-5's size floor and P-5's score floor, so a P-6 drop would show as a missing box.
"""

import math
from fractions import Fraction

import pytest

from pam.enrich import ocr_detect
from pam.enrich.ocr_detect import OcrInputError, boxes, components, det_size, dilate

# --- design 8.1's table, as literals (S6) ----------------------------------------------------
SPEC_MIN_SIDE = 30
SPEC_MAX_SIDE = 2000
SPEC_DET_LIMIT = 736
SPEC_DET_MAX_SIDE = 4096
SPEC_MIN_SIZE = 3
SPEC_UNCLIP = Fraction(16, 10)  # 1.6


def test_the_modules_constants_are_the_specs_literals():
    """A constant change must turn THIS red; the sweeps below are built from the literals."""
    assert ocr_detect.MIN_SIDE == SPEC_MIN_SIDE
    assert ocr_detect.MAX_SIDE == SPEC_MAX_SIDE
    assert ocr_detect.DET_LIMIT == SPEC_DET_LIMIT
    assert ocr_detect.DET_MAX_SIDE == SPEC_DET_MAX_SIDE
    assert ocr_detect.MIN_SIZE == SPEC_MIN_SIZE
    assert ocr_detect.UNCLIP == float(SPEC_UNCLIP)


def grid(low, high):
    """Step 1 from `low` to 64, then step 37 up to `high`, both ends included."""
    values = list(range(low, 65)) + list(range(64 + 37, high, 37)) + [high]
    return sorted(set(values))


SIDES = grid(SPEC_MIN_SIDE, SPEC_MAX_SIDE)
CONTOUR_SIDES = grid(SPEC_MIN_SIZE, 4096)
assert len(SIDES) > 80 and SIDES[0] == 30 and SIDES[-1] == 2000, "the P-2 grid is wrong"
assert len(CONTOUR_SIDES) > 100 and CONTOUR_SIDES[0] == 3, "the P-6 grid is wrong"


def expected_det(h, w):
    """P-2 by a second implementation: exact rationals, truncate THEN round half-even to 32."""
    low = min(h, w)
    r = Fraction(SPEC_DET_LIMIT, low) if low < SPEC_DET_LIMIT else Fraction(1)
    h2, w2 = math.floor(h * r), math.floor(w * r)
    return 32 * round(Fraction(h2, 32)), 32 * round(Fraction(w2, 32))


def test_P2_never_takes_its_nonpositive_branch_in_the_reachable_domain():
    raised = 0
    returned = 0
    for h in SIDES:
        for w in SIDES:
            big_h, big_w = expected_det(h, w)
            # The independent half: the exact arithmetic says both sides are positive.
            assert big_h > 0 and big_w > 0, (h, w)
            try:
                got = det_size(h, w)
            except OcrInputError:
                raised += 1
                # ...and the refusal is the DET_MAX_SIDE one, which the exact arithmetic predicts.
                assert max(big_h, big_w) > SPEC_DET_MAX_SIDE, (h, w)
                continue
            returned += 1
            # `det_size` returns None only on the `<= 0` branch: this IS the spy.
            assert got is not None, f"P-2's <= 0 branch fired at {(h, w)}"
            # M2486 (F3): the second implementation must be load-bearing, not only its sign -
            # round-before-truncate left the partition, and this test, unchanged.
            assert got == (big_h, big_w), (h, w, got)
            assert max(big_h, big_w) <= SPEC_DET_MAX_SIDE, (h, w)
    # The sweep must keep exercising thin inputs, or "never fires" is vacuous at the edge.
    assert raised > 0, "no input raised OcrInputError: the thin end of the domain is unswept"
    assert returned > 0


def test_the_thin_corner_really_raises_rather_than_returning_none():
    with pytest.raises(OcrInputError):
        det_size(30, 2000)
    with pytest.raises(OcrInputError):
        det_size(2000, 30)
    assert det_size(30, 30) == (736, 736)


def test_P6_the_post_unclip_floor_is_met_by_every_box_that_passes_the_size_floor():
    checked = 0
    for w in CONTOUR_SIDES:
        for h in CONTOUR_SIDES:
            d = Fraction(w * h) * SPEC_UNCLIP / (2 * (w + h))
            assert min(w, h) + 2 * d >= SPEC_MIN_SIZE + 2, (w, h)
            checked += 1
    assert checked == len(CONTOUR_SIDES) ** 2 and checked > 10000


def test_P6_boxes_keeps_every_component_that_clears_the_size_and_score_floors():
    """The production call itself: a P-6 drop would be a box missing from `boxes()`."""
    import numpy

    kept_total = 0
    for side in range(1, 13):
        for tall in (side, side + 3):
            prob = numpy.zeros((40, 60), dtype=numpy.float64)
            prob[5 : 5 + tall, 7 : 7 + side] = 0.9
            comps = components(dilate(prob > 0.3))
            expected = 0
            for c in comps:
                w, h = c.x1 - c.x0, c.y1 - c.y0
                if min(w, h) < SPEC_MIN_SIZE:
                    continue
                score = float(numpy.mean(prob[c.y0 : c.y1 + 1, c.x0 : c.x1 + 1]))
                if score >= 0.5:
                    expected += 1
            got = boxes(prob, (40, 60), (40, 60))
            assert len(got) == expected, (side, tall)
            kept_total += len(got)
    assert kept_total > 0, "no blob was ever kept; the comparison above proved nothing"

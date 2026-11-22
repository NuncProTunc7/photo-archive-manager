"""Design 8.1 rev 28's OCR pipeline, P-1 to P-11, row by row (M12 Task 12 Part B, Step B5).

UNMARKED, AND IT NEVER NEEDS THE PACK. Every function here is a pure function on arrays, and
`numpy` and Pillow are already in every install (Pillow is a runtime dependency; numpy arrives
with imagehash and reverse_geocoder). So every row of the table is measured on every push.

EVERY EXPECTATION IS A LITERAL WRITTEN FROM THE TABLE, NEVER COMPUTED BY CALLING THE FUNCTION UNDER
TEST. Where a number is derived - an unclip distance, a scaled coordinate, a normalised pixel -
the hand arithmetic is in the comment beside it. CLAUDE.md: "an oracle built from the definition
it checks is a survivor waiting to happen". The maps are built from exact binary fractions (0.5,
0.875, 0.8125, 1.0) so every mean below is exact in float64 and every boundary is a real boundary.

R42-C: THE SOLID-COLOUR TEST IS THE SOLE GUARDIAN OF `DET_CHANNELS`. Eleven of the thirteen OCR
fixtures are exactly grey, so no fixture's score moves when the detector's channel order is
swapped. `test_a_pure_red_image_is_channel_2_in_both_tensors` is therefore the only thing in the
repository that can see that swap, and it reads the MODULE CONSTANTS rather than a hard-coded
"BGR", so each constant's swap is killed on its own.
"""

import numpy
import pytest
from PIL import Image

from pam.enrich import ocr_detect, ocr_recognize
from pam.enrich.ocr_detect import Box, Component, OcrInputError
from pam.errors import PamError
from pam.naming.ocr_dates import OcrLine

# --- P-1: working_size ------------------------------------------------------------------------

WORKING_SIZES = [
    # (h, w) -> target (h, w), or None for "no lines"
    ((29, 100), None),  # min 29 < MIN_SIDE 30: no lines, not an error
    ((30, 100), (30, 100)),  # exactly MIN_SIDE: kept, no resize
    ((2000, 1500), (2000, 1500)),  # exactly MAX_SIDE: not "> MAX_SIDE", unchanged
    ((4000, 1000), (2000, 500)),  # s = 2000/4000 = 0.5 -> (int(2000.0), int(500.0))
    ((100, 4000), (50, 2000)),  # s = 0.5 -> (50, 2000)
    # s = 2000/5000 = 0.4 -> (int(40 * 0.4), 2000) = (16, 2000): the TARGET's short side is
    # below MIN_SIDE, so the answer is "no lines" - checked before any resize (P-1)
    ((40, 5000), None),
]
assert WORKING_SIZES


@pytest.mark.parametrize(("shape", "expected"), WORKING_SIZES)
def test_working_size_is_p1_bounded_and_checked_before_any_resize(shape, expected):
    assert ocr_detect.working_size(*shape) == expected


def test_working_image_resizes_to_the_target_and_passes_an_in_bounds_image_through():
    big = Image.new("RGB", (4000, 100), (255, 255, 255))  # (w, h): h=100, w=4000
    assert ocr_detect.working_image(big).size == (2000, 50)  # s = 0.5, Pillow's (w, h)
    small = Image.new("RGB", (200, 100), (255, 255, 255))
    assert ocr_detect.working_image(small) is small  # skipped when the target is the size
    thin = Image.new("RGB", (100, 29), (255, 255, 255))
    assert ocr_detect.working_image(thin) is None


# --- P-2: det_size ----------------------------------------------------------------------------

DET_SIZES = [
    # (h, w) -> (H, W)
    # exactly DET_LIMIT: r = 1.0; H = round(736/32)*32 = 23*32 = 736;
    # W = round(1000/32 = 31.25)*32 = 31*32 = 992 (rounds DOWN to the multiple of 32)
    ((736, 1000), (736, 992)),
    # above DET_LIMIT: r = 1.0; H = round(25.0)*32 = 800; W = 992
    ((800, 1000), (800, 992)),
    # BELOW DET_LIMIT, upscaled: r = 736/368 = 2.0 -> h' = 736, w' = 1000 -> (736, 992)
    ((368, 500), (736, 992)),
    # rounds UP to the multiple of 32: r = 1.0; W = round(1010/32 = 31.5625)*32 = 32*32 = 1024
    ((736, 1010), (736, 1024)),
    # THE REFERENCE'S TRUNCATION CASE: r = 736/31 = 23.7419...; h' = int(31r) = 736,
    # w' = int(60r) = int(1424.516...) = 1424 (TRUNCATE FIRST); W = round(1424/32 = 44.5)*32 =
    # 44*32 = 1408 (Python's round, ties to even). Rounding 1424.516/32 = 44.516 directly would
    # give 45*32 = 1440.
    ((31, 60), (736, 1408)),
    # DET_MAX_SIDE's boundary, inclusive: W = round(4112/32 = 128.5)*32 = 128*32 = 4096, and
    # 4096 is not "> DET_MAX_SIDE"
    ((736, 4112), (736, 4096)),
]
assert DET_SIZES


@pytest.mark.parametrize(("shape", "expected"), DET_SIZES)
def test_det_size_is_the_references_arithmetic(shape, expected):
    assert ocr_detect.det_size(*shape) == expected


@pytest.mark.parametrize(
    "shape",
    [
        # r = 736/30 = 24.533...; w' = int(2000r) = int(49066.66...) = 49066;
        # W = round(49066/32 = 1533.31)*32 = 1533*32 = 49056 > 4096
        (30, 2000),
        # r = 1.0; W = round(4113/32 = 128.53)*32 = 129*32 = 4128 > 4096
        (736, 4113),
    ],
)
def test_a_thin_image_is_a_per_file_failure_and_never_no_text(shape):
    with pytest.raises(OcrInputError) as raised:
        ocr_detect.det_size(*shape)
    assert isinstance(raised.value, PamError)


def test_the_det_max_side_refusal_comes_before_any_resize():
    """R1: "the check runs BEFORE any resize or allocation". A resize spy that fires would mean
    a 49056-pixel-wide image had been allocated before it was refused."""

    class Spy:
        height, width = 30, 2000
        resized = False

        def resize(self, *args, **kwargs):  # pragma: no cover - must never run
            Spy.resized = True
            raise AssertionError("det_tensor resized before refusing")

    with pytest.raises(OcrInputError):
        ocr_detect.det_tensor(Spy(), ocr_detect.DET_CHANNELS)
    assert Spy.resized is False


# --- R42-C: the channel orders, on a solid colour ---------------------------------------------


def test_a_pure_red_image_is_channel_2_in_both_tensors():
    """K5(a) as a literal, and the SOLE guardian of `DET_CHANNELS` (R42-C).

    A pure-red (255, 0, 0) RGB image, in BGR order, is (0, 0, 255): after `(x/255 - 0.5)/0.5`
    channel 2 is (1 - 0.5)/0.5 = +1.0 and channels 0-1 are (0 - 0.5)/0.5 = -1.0. The MODULE
    CONSTANTS are passed, never a literal, so swapping either one alone turns this red.
    """
    red = Image.new("RGB", (64, 64), (255, 0, 0))
    det = ocr_detect.det_tensor(red, ocr_detect.DET_CHANNELS)
    # det_size(64, 64): r = 736/64 = 11.5 -> (736, 736)
    assert det.shape == (1, 3, 736, 736)
    assert det.dtype == numpy.float32
    assert (det[0, 2] == 1.0).all()
    assert (det[0, 0:2] == -1.0).all()

    piece = numpy.zeros((24, 48, 3), dtype=numpy.uint8)
    piece[:, :, 0] = 255  # RGB red
    rec = ocr_recognize.rec_tensor(piece, ocr_recognize.REC_CHANNELS)
    # ratio 48/24 = 2.0: W_in = max(320, int(96)) = 320; w_r = min(ceil(96), 320) = 96
    assert rec.shape == (1, 3, 48, 320)
    assert rec.dtype == numpy.float32
    assert (rec[0, 2, :, :96] == 1.0).all()
    assert (rec[0, 0:2, :, :96] == -1.0).all()
    assert (rec[0, :, :, 96:] == 0.0).all()


# --- P-3: dilate ------------------------------------------------------------------------------


def test_dilate_grows_each_pixel_right_and_down_and_nowhere_else():
    """`D[y,x] = B[y,x] | B[y-1,x] | B[y,x-1] | B[y-1,x-1]`, out-of-range false.

    Worked by hand: (0,0) grows to (0,1), (1,0), (1,1); (0,3) grows to (1,3) only (its right
    neighbour is off the edge); (2,2) grows to (2,3), (3,2), (3,3); (3,0) grows to (3,1).
    """
    bitmap = numpy.array(
        [
            [1, 0, 0, 1],
            [0, 0, 0, 0],
            [0, 0, 1, 0],
            [1, 0, 0, 0],
        ],
        dtype=bool,
    )
    expected = numpy.array(
        [
            [1, 1, 0, 1],
            [1, 1, 0, 1],
            [0, 0, 1, 1],
            [1, 1, 1, 1],
        ],
        dtype=bool,
    )
    assert (ocr_detect.dilate(bitmap) == expected).all()


# --- P-4: components --------------------------------------------------------------------------


def _bits(rows):
    return numpy.array(rows, dtype=bool)


def test_a_diagonal_only_touch_joins_one_component():
    # row 0 run [0,1), row 1 run [1,2): a0=1 <= b1=1 and b0=0 <= a1=2, so they join (8-conn)
    assert ocr_detect.components(_bits([[1, 0], [0, 1]])) == [Component(0, 0, 1, 1)]


def test_two_runs_one_column_apart_are_separate():
    # row 0 [0,1), row 1 [2,3): a0=2 <= b1=1 is false, so two components, in raster order
    assert ocr_detect.components(_bits([[1, 0, 0], [0, 0, 1]])) == [
        Component(0, 0, 0, 0),
        Component(2, 1, 2, 1),
    ]


def test_a_u_shape_is_one_component_its_two_arms_joined_by_a_later_row():
    # arms: runs 0 (row 0, [0,1)) and 1 (row 0, [2,3)); the base (row 2, [0,3)) joins both
    assert ocr_detect.components(_bits([[1, 0, 1], [1, 0, 1], [1, 1, 1]])) == [
        Component(0, 0, 2, 2)
    ]


def test_components_come_in_raster_order_of_their_first_pixel():
    # (2, 0) is first in raster order even though (0, 1) is further left
    assert ocr_detect.components(_bits([[0, 0, 1], [1, 0, 0]])) == [
        Component(2, 0, 2, 0),
        Component(0, 1, 0, 1),
    ]


def test_only_the_first_max_candidates_components_are_kept_in_raster_order():
    """1001 isolated pixels: 500 on row 0, 501 on row 2 (row 1 empty, so none touch). The
    first 1000 in raster order are row 0's 500 and row 2's first 500; (1000, 2) is dropped."""
    bitmap = numpy.zeros((3, 1001), dtype=bool)
    bitmap[0, 0:1000:2] = True
    bitmap[2, 0:1001:2] = True
    expected = [Component(x, 0, x, 0) for x in range(0, 1000, 2)] + [
        Component(x, 2, x, 2) for x in range(0, 1000, 2)
    ]
    assert len(expected) == 1000
    assert ocr_detect.components(bitmap) == expected


# --- P-3, P-5 to P-7: boxes -------------------------------------------------------------------


def _drop_rule_map():
    """A 60 x 100 map in which each drop rule fires exactly once. Shapes are row/col INCLUSIVE."""
    p = numpy.zeros((60, 100), dtype=numpy.float32)
    # A, the control, KEPT: core rows 5-8, cols 5-14 at 1.0
    p[5:9, 5:15] = 1.0
    # B, dropped by MIN_SIZE: core rows 5-6, cols 30-59. Dilated rows 5-7 -> h = 2 < 3. (Were
    # MIN_SIZE not checked it would SURVIVE: w = 30, d = 30*2*1.6/(2*32) = 1.5, and
    # min + 2d = 2 + 3 = 5, which P-6 keeps; score 60/93 = 0.645.)
    p[5:7, 30:60] = 1.0
    # C, score EXACTLY 0.5, KEPT: core rows 20-22, cols 5-7 (9 cells: eight 0.875 and one 1.0,
    # sum 8.0); dilated rows 20-23, cols 5-8 = 16 cells, the other 7 at 0.0 -> 8/16 = 0.5
    p[20:23, 5:8] = 0.875
    p[20, 5] = 1.0
    # D, score JUST BELOW, dropped: the same with 0.8125 for the 1.0 -> 7.9375/16 = 0.49609375
    p[20:23, 20:23] = 0.875
    p[20, 20] = 0.8125
    # E, the 0.3 strip: core rows 35-44, cols 5-24 at 1.0, with cols 25-26 EXACTLY 0.3 - which
    # is not "> 0.3", so the strip is not binarised and does not extend the component
    p[35:45, 5:25] = 1.0
    p[35:45, 25:27] = numpy.float32(0.3)
    return p


def test_each_drop_rule_fires_exactly_once_and_the_survivors_are_boxed_by_hand():
    """P-3 then P-5 to P-7, with map and working image the same size (scale 1).

    A: dilated rows 5-9, cols 5-15 -> x0=5, x1=15, y0=5, y1=9, w=10, h=4; score 40/55 = 0.727.
       d = 10*4*1.6 / (2*14) = 64/28 = 2.2857142857; the box is (5-d, 5-d, 15+d, 9+d) =
       (2.714, 2.714, 17.286, 11.286) -> round -> (3, 3, 17, 11).
    C: dilated rows 20-23, cols 5-8 -> w = h = 3, score exactly 0.5 (KEPT). d = 3*3*1.6/(2*6)
       = 1.2; (5-1.2, 20-1.2, 8+1.2, 23+1.2) = (3.8, 18.8, 9.2, 24.2) -> (4, 19, 9, 24).
    E: dilated rows 35-45, cols 5-25 -> w = 20, h = 10 (the 0.3 strip at col 25 is inside the
       dilation, NOT a component pixel); d = 20*10*1.6/(2*30) = 5.3333; (5-d, 35-d, 25+d, 45+d)
       = (-0.333, 29.667, 30.333, 50.333) -> (-0 -> 0, 30, 30, 50).
    B (min size) and D (score 0.496) are dropped. Order is P-4's: A (row 5), C (row 20), E.
    """
    found = ocr_detect.boxes(_drop_rule_map(), (60, 100), (60, 100))
    assert found == [Box(3, 3, 17, 11), Box(4, 19, 9, 24), Box(0, 30, 30, 50)]


def test_scale_back_rounds_half_integers_to_even():
    """P-7 at a half-integer, map 40 x 40 onto a working image 20 x 20 (scale 0.5).

    Core rows 11-15, cols 13-17 -> dilated rows 11-16, cols 13-18: x0=13, x1=18, y0=11, y1=16,
    w = h = 5, d = 5*5*1.6/(2*10) = 2.0 exactly. Unclipped: (11, 9, 20, 18). Scaled by 20/40:
    (5.5, 4.5, 10.0, 9.0). Ties to even: (6, 4, 10, 9). Truncation would give 5 for x0; round
    half UP would give 5 for y0 - so both wrong readings are visible here.
    """
    p = numpy.zeros((40, 40), dtype=numpy.float32)
    p[11:16, 13:18] = 1.0
    assert ocr_detect.boxes(p, (40, 40), (20, 20)) == [Box(6, 4, 10, 9)]


def test_a_box_is_clipped_at_every_image_border():
    """Top-left: core rows 0-4, cols 0-4 -> dilated 0-5, w = h = 5, d = 2.0 -> (-2, -2, 7, 7)
    -> clipped (0, 0, 7, 7). Bottom-right: core rows 35-39, cols 35-39 -> dilation goes off the
    edge, so the extent stays 35-39, w = h = 4, d = 4*4*1.6/(2*8) = 1.6 -> (33.4, 33.4, 40.6,
    40.6) -> (33, 33, 41, 41) -> clipped to the 40-pixel image: (33, 33, 40, 40)."""
    p = numpy.zeros((40, 40), dtype=numpy.float32)
    p[0:5, 0:5] = 1.0
    p[35:40, 35:40] = 1.0
    assert ocr_detect.boxes(p, (40, 40), (40, 40)) == [Box(0, 0, 7, 7), Box(33, 33, 40, 40)]


def test_the_post_unclip_size_rule_either_side_of_its_boundary(monkeypatch):
    """P-6's own drop, `min(w, h) + 2d < MIN_SIZE + 2`, on both sides of its boundary.

    AT THE TABLE'S CONSTANTS THIS RULE CANNOT FIRE - measured by hand, not asserted by a
    docstring: with m = min(w, h) >= MIN_SIZE = 3 and M = max(w, h) >= m,
    d = 1.6mM / (2(m + M)) >= 0.4m >= 1.2, so m + 2d >= 5.4, never < 5. It is reachable only for
    MIN_SIZE < 2.5, so the row is driven with MIN_SIZE lowered to 2 (read at call time by P-5 and
    P-6 alike, so the bound becomes 4). And EXACT equality is unreachable on integer extents:
    m = 2 needs d = 1, i.e. 1.6M = M + 2, M = 10/3. So "at its boundary" is the two integer
    widths that straddle it:
      w = 3, h = 2: d = 3*2*1.6/(2*5) = 0.96; 2 + 1.92 = 3.92 < 4 -> DROPPED (score 6/12 = 0.5)
      w = 4, h = 2: d = 4*2*1.6/(2*6) = 1.0667; 2 + 2.133 = 4.133 -> KEPT (score 8/15 = 0.533)
    The kept box: dilated cols 20-24, rows 2-4 -> (20 - d, 2 - d, 24 + d, 4 + d) =
    (18.93, 0.93, 25.07, 5.07) -> (19, 1, 25, 5).
    """
    monkeypatch.setattr(ocr_detect, "MIN_SIZE", 2)
    p = numpy.zeros((10, 40), dtype=numpy.float32)
    p[2:4, 5:8] = 1.0  # core 2 x 3 -> dilated rows 2-4, cols 5-8: w = 3, h = 2
    p[2:4, 20:24] = 1.0  # core 2 x 4 -> dilated rows 2-4, cols 20-24: w = 4, h = 2
    assert ocr_detect.boxes(p, (10, 40), (10, 40)) == [Box(19, 1, 25, 5)]


def test_at_the_tables_min_size_neither_of_those_components_survives_p5():
    """The control under the test above: at MIN_SIZE = 3 both are dropped by P-5 (h = 2 < 3),
    so what the test above measures is P-6 and not a size rule that let them through."""
    p = numpy.zeros((10, 40), dtype=numpy.float32)
    p[2:4, 5:8] = 1.0
    p[2:4, 20:24] = 1.0
    assert ocr_detect.boxes(p, (10, 40), (10, 40)) == []


# --- P-8: reading_order -----------------------------------------------------------------------


def test_reading_order_is_order_pngs_geometry_as_numbers():
    """Row one: ROLL at x0 = 40, y0 = 43, and FILM at x0 = 400 drawn 3 px HIGHER, y0 = 40.
    Row two: the date at x0 = 40, y0 = 120. Sorted by (y0, x0): FILM, ROLL, date. The swap pass:
    i = 0, j = 0: |43 - 40| = 3 < ORDER_TOL 10 and 40 < 400, so swap -> ROLL, FILM, date.
    i = 1, j = 1: |120 - 40| = 80, not < 10: break. A plain sort gets row one backwards."""
    roll, film, date = Box(40, 43, 200, 90), Box(400, 40, 560, 87), Box(40, 120, 500, 170)
    assert ocr_detect.reading_order([film, date, roll]) == [roll, film, date]


def test_equal_keys_keep_their_p4_order():
    """Stable: two boxes with the same (y0, x0) come out in the order they went in."""
    first, second = Box(5, 5, 10, 10), Box(5, 5, 20, 20)
    assert ocr_detect.reading_order([first, second]) == [first, second]
    assert ocr_detect.reading_order([second, first]) == [second, first]


# --- P-9: crop --------------------------------------------------------------------------------


def _numbered(h, w):
    """An image whose every pixel says where it came from: value 10*y + x in all channels."""
    grid = (10 * numpy.arange(h)[:, None] + numpy.arange(w)[None, :]).astype(numpy.uint8)
    return numpy.repeat(grid[:, :, None], 3, axis=2)


def test_a_crop_is_half_open_over_the_box():
    # Box X0=2, X1=5 is columns 2, 3, 4 (THREE); Y0=1, Y1=3 is rows 1, 2. 2/3 < 1.5: no turn.
    piece = ocr_recognize.crop(_numbered(6, 8), Box(2, 1, 5, 3))
    assert piece.shape == (2, 3, 3)
    assert piece[:, :, 0].tolist() == [[12, 13, 14], [22, 23, 24]]


def test_a_zero_width_or_zero_height_box_is_skipped():
    assert ocr_recognize.crop(_numbered(6, 8), Box(4, 1, 4, 3)) is None
    assert ocr_recognize.crop(_numbered(6, 8), Box(1, 2, 4, 2)) is None


def test_a_crop_exactly_rotate_ratio_tall_is_turned_counter_clockwise():
    """15 high, 10 wide: 15/10 = 1.5 = ROTATE_RATIO, so it turns. `rot90(k=1)` is a quarter
    turn counter-clockwise: the TOP-RIGHT pixel (y=0, x=9) becomes the top-left, and the result
    is 10 high and 15 wide."""
    array = numpy.zeros((15, 10, 3), dtype=numpy.uint8)
    array[0, 9] = 200  # top-right
    piece = ocr_recognize.crop(array, Box(0, 0, 10, 15))
    assert piece.shape == (10, 15, 3)
    assert piece[0, 0, 0] == 200


def test_a_crop_just_below_rotate_ratio_is_not_turned():
    # 14 high, 10 wide: 1.4 < 1.5
    piece = ocr_recognize.crop(numpy.zeros((14, 10, 3), dtype=numpy.uint8), Box(0, 0, 10, 14))
    assert piece.shape == (14, 10, 3)


# --- P-10: rec_tensor -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("h", "w", "w_in", "w_r"),
    [
        # short: ratio 48/24 = 2.0; W_in = max(320, int(96)) = 320; w_r = min(ceil(96), 320) = 96
        (24, 48, 320, 96),
        # long: ratio 100/10 = 10.0; W_in = max(320, int(480)) = 480; w_r = min(480, 480) = 480
        (10, 100, 480, 480),
        # fractional: ratio 50/7 = 7.142857; 48 * ratio = 342.857; W_in = max(320, 342) = 342;
        # w_r = min(ceil(342.857) = 343, 342) = 342 - the min() is what keeps the resize inside
        (7, 50, 342, 342),
    ],
)
def test_rec_tensor_shape_arithmetic_and_zero_padding(h, w, w_in, w_r):
    piece = numpy.full((h, w, 3), 255, dtype=numpy.uint8)  # white: +1.0 wherever it lands
    tensor = ocr_recognize.rec_tensor(piece, ocr_recognize.REC_CHANNELS)
    assert tensor.shape == (1, 3, 48, w_in)
    assert (tensor[0, :, :, :w_r] == 1.0).all()
    assert (tensor[0, :, :, w_r:] == 0.0).all()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, -1.0),  # (0/255 - 0.5)/0.5 = -1
        (128, 1 / 255),  # (128/255 - 0.5)/0.5 = (256 - 255)/255 = 1/255 = 0.0039215686
        (255, 1.0),  # (1 - 0.5)/0.5 = +1
    ],
)
def test_rec_tensor_normalises_a_pixel_to_x_over_255_minus_half_over_half(value, expected):
    piece = numpy.full((24, 48, 3), value, dtype=numpy.uint8)  # uniform: the resize keeps it
    tensor = ocr_recognize.rec_tensor(piece, ocr_recognize.REC_CHANNELS)
    assert numpy.allclose(tensor[0, :, :, :96], expected, rtol=0, atol=1e-6)


# --- P-11: charset and ctc_decode -------------------------------------------------------------

# A four-character charset, written by hand: blank, "a", "b", "c", then P-11's trailing space.
CHARS = ("<blank>", "a", "b", "c", " ")


def test_charset_is_blank_then_the_metadata_lines_then_a_space():
    assert ocr_recognize.charset("a\nb\nc") == CHARS  # 3 lines + 2 = 5
    assert len(ocr_recognize.charset("x\ny\nz")) == 3 + 2


def _probs(rows):
    """Rows of [T, 5] probabilities, float32 as the recogniser emits them."""
    return numpy.array(rows, dtype=numpy.float32)


def test_repeats_collapse_and_the_score_is_the_mean_of_the_kept_probabilities():
    """argmax per step: 1, 1, 2 -> "a" (kept, 0.5), "a" (a repeat, NOT kept), "b" (kept, 1.0).
    Text "ab"; score = mean(0.5, 1.0) = 0.75 - and max(0.5, 1.0) = 1.0, so max != mean."""
    line = ocr_recognize.ctc_decode(
        _probs(
            [
                [0.125, 0.5, 0.125, 0.125, 0.125],
                [0.0, 0.75, 0.25, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0, 0.0],
            ]
        ),
        CHARS,
    )
    assert line == OcrLine("ab", 0.75)


def test_a_blank_between_two_equal_letters_keeps_both():
    # argmax 1, 0, 1 -> "a", blank (dropped), "a" (differs from the previous index, 0): "aa"
    line = ocr_recognize.ctc_decode(
        _probs([[0, 1.0, 0, 0, 0], [1.0, 0, 0, 0, 0], [0, 0.5, 0.25, 0.25, 0]]), CHARS
    )
    assert line == OcrLine("aa", 0.75)  # mean(1.0, 0.5)


def test_leading_and_trailing_spaces_are_stripped_but_still_scored():
    # argmax 4, 1, 4 -> " a " -> strip -> "a"; score = mean(0.5, 1.0, 0.75) = 0.75
    line = ocr_recognize.ctc_decode(
        _probs(
            [
                [0.125, 0.125, 0.125, 0.125, 0.5],
                [0.0, 1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.125, 0.125, 0.75],
            ]
        ),
        CHARS,
    )
    assert line == OcrLine("a", 0.75)


def test_an_all_blank_row_is_no_line():
    assert ocr_recognize.ctc_decode(_probs([[1.0, 0, 0, 0, 0]] * 3), CHARS) is None


def test_a_line_that_is_only_spaces_is_no_line():
    # argmax 4, 0, 4 -> "  " -> strip -> "": P-11 says there is no line
    rows = [[0, 0, 0, 0, 1.0], [1.0, 0, 0, 0, 0], [0, 0, 0, 0, 1.0]]
    assert ocr_recognize.ctc_decode(_probs(rows), CHARS) is None


def test_argmax_takes_the_first_index_on_an_exact_tie():
    # 0.5 at "a" (1) and at "b" (2): numpy.argmax gives the FIRST, so "a"
    line = ocr_recognize.ctc_decode(_probs([[0.0, 0.5, 0.5, 0.0, 0.0]]), CHARS)
    assert line == OcrLine("a", 0.5)

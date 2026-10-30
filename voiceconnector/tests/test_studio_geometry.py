"""Géométrie du Studio : pas du séquenceur, glissés de potards et de faders (sans Qt)."""

import pytest

from core import studio_geometry as sg


# --------------------------------------------------------------------------- #
# Répartition des pas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("count", [1, 4, 16, 32, 48, 64])
def test_cells_fill_the_width_in_order_without_overlap(count):
    layout = sg.StepLayout(700, count)
    assert len(layout.cells) == count
    assert layout.cells[0].left == 0 and layout.cells[-1].right == pytest.approx(700, abs=1e-6)
    for before, after in zip(layout.cells, layout.cells[1:], strict=False):
        assert after.left >= before.right + sg.STEP_GAP - 1e-9
    assert len({round(c.width, 6) for c in layout.cells}) == 1


def test_groups_of_four_are_separated_a_bit_more():
    layout = sg.StepLayout(700, 16)
    inside = layout.cells[1].left - layout.cells[0].right
    between = layout.cells[4].left - layout.cells[3].right
    assert inside == pytest.approx(sg.STEP_GAP) and between == pytest.approx(sg.STEP_GAP + sg.GROUP_GAP)
    assert [c.beat for c in layout.cells[:9]] == [0, 0, 0, 0, 1, 1, 1, 1, 2]


def test_margins_are_kept_on_both_sides():
    layout = sg.StepLayout(300, 16, margin=10)
    assert layout.cells[0].left == 10 and layout.cells[-1].right == pytest.approx(290)
    assert layout.content_width == pytest.approx(300)


def test_a_narrow_row_keeps_every_step_at_the_minimum_size():
    layout = sg.StepLayout(40, 64)
    assert all(c.width == sg.MIN_CELL for c in layout.cells)
    assert layout.content_width > 40                                  # plus large que la place : la ligne défile


def test_the_minimum_size_is_adjustable():
    layout = sg.StepLayout(40, 16, min_cell=8)
    assert all(c.width == 8 for c in layout.cells)
    assert layout.content_width == pytest.approx(16 * 8 + 15 * sg.STEP_GAP + 3 * sg.GROUP_GAP)
    assert sg.StepLayout(40, 16, min_cell=-4).min_cell == 1.0


def test_no_steps_no_cells():
    layout = sg.StepLayout(300, 0)
    assert layout.cells == [] and layout.index_at(10) is None and layout.span(0, 100) == []
    assert layout.content_width == 0.0 and sg.StepLayout(-5, 4).width == 0.0


# --------------------------------------------------------------------------- #
# Où est le pas ?
# --------------------------------------------------------------------------- #
def test_every_cell_center_finds_its_own_step():
    layout = sg.StepLayout(640, 32)
    for cell in layout.cells:
        assert layout.index_at(cell.center) == cell.index
        assert layout.index_at(cell.left + 0.1) == cell.index and layout.index_at(cell.right - 0.1) == cell.index


def test_a_click_in_a_gap_goes_to_the_nearest_step():
    layout = sg.StepLayout(640, 16)
    first, second = layout.cells[3], layout.cells[4]                  # séparés par un grand espace
    middle = (first.right + second.left) / 2
    assert layout.index_at(first.right + 0.5) == 3 and layout.index_at(second.left - 0.5) == 4
    assert layout.index_at(middle - 0.01) == 3 and layout.index_at(middle + 0.01) == 4


def test_outside_the_row_there_is_no_step():
    layout = sg.StepLayout(640, 16)
    assert layout.index_at(-20) is None and layout.index_at(700) is None
    assert layout.index_at(-1) == 0 and layout.index_at(641) == 15      # à un espace près : les bords comptent


# --------------------------------------------------------------------------- #
# Glissé
# --------------------------------------------------------------------------- #
def test_a_drag_covers_every_step_in_between_in_both_directions():
    layout = sg.StepLayout(640, 16)
    c = layout.cells
    assert layout.span(c[2].center, c[6].center) == [2, 3, 4, 5, 6]
    assert layout.span(c[6].center, c[2].center) == [6, 5, 4, 3, 2]
    assert layout.span(c[5].center, c[5].center) == [5]


def test_a_drag_leaving_the_row_reaches_the_first_or_last_step():
    layout = sg.StepLayout(640, 16)
    c = layout.cells
    assert layout.span(c[10].center, 900) == [10, 11, 12, 13, 14, 15]
    assert layout.span(c[3].center, -50) == [3, 2, 1, 0]
    assert layout.span(-80, c[2].center) == [0, 1, 2]
    assert layout.span(900, c[13].center) == [15, 14, 13]
    assert layout.span(-90, -50) == [] and layout.span(900, 950) == []         # tout du même côté : rien
    assert layout.span(-50, 900) == list(range(16)) and layout.span(900, -50) == list(range(15, -1, -1))


def test_painting_lights_every_step_crossed_when_the_first_one_was_off():
    layout = sg.StepLayout(640, 16)
    painter = sg.StepPainter(layout, [False] * 16)
    c = layout.cells
    assert painter.press(c[1].center) == [(1, True)] and painter.active
    assert painter.move(c[5].center) == [(2, True), (3, True), (4, True), (5, True)]
    assert painter.move(c[5].center) == []                            # rien de nouveau
    assert painter.move(c[3].center) == []                            # en revenant : déjà allumés, on ne touche à rien
    painter.release()
    assert not painter.active and painter.move(c[9].center) == []


def test_painting_erases_when_the_first_step_was_on():
    layout = sg.StepLayout(640, 16)
    steps = [i % 2 == 0 for i in range(16)]                          # 0, 2, 4… allumés
    painter = sg.StepPainter(layout, steps)
    c = layout.cells
    assert painter.press(c[2].center) == [(2, False)]
    assert painter.move(c[6].center) == [(4, False), (6, False)]      # les pas déjà éteints ne sont pas renvoyés
    assert painter.steps[:8] == [True, False, False, False, False, False, False, False]


def test_a_press_outside_the_row_does_nothing():
    painter = sg.StepPainter(sg.StepLayout(640, 16), [False] * 16)
    assert painter.press(-50) == [] and not painter.active


def test_painting_a_longer_row_than_the_data_extends_it():
    layout = sg.StepLayout(640, 16)
    painter = sg.StepPainter(layout, [True] * 4)
    assert painter.press(layout.cells[8].center) == [(8, True)] and len(painter.steps) == 9


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def test_the_playhead_loops_over_the_pattern_length():
    assert sg.playhead_step(0.0, 16) == 0 and sg.playhead_step(3.9, 16) == 3
    assert sg.playhead_step(16.0, 16) == 0 and sg.playhead_step(37.5, 16) == 5
    assert sg.playhead_step(-1.0, 16) == -1 and sg.playhead_step(5.0, 0) == -1


# --------------------------------------------------------------------------- #
# Potards, faders
# --------------------------------------------------------------------------- #
def test_dragging_up_raises_the_value_and_down_lowers_it():
    assert sg.drag_value(0.5, 30) > 0.5 > sg.drag_value(0.5, -30)
    assert sg.drag_value(0.5, sg.KNOB_SPAN) == 1.0 and sg.drag_value(0.5, -sg.KNOB_SPAN) == 0.0
    assert sg.drag_value(0.2, 15) == pytest.approx(0.3)


def test_shift_makes_the_drag_five_times_finer():
    coarse = sg.drag_value(0.5, 10) - 0.5
    fine = sg.drag_value(0.5, 10, fine=True) - 0.5
    assert fine == pytest.approx(coarse * sg.FINE)


def test_a_signed_range_scales_with_its_width():
    assert sg.drag_value(0.0, sg.KNOB_SPAN / 2, low=-1.0, high=1.0) == pytest.approx(1.0)
    assert sg.drag_value(0.0, -999, low=-1.0, high=1.0) == -1.0


def test_the_wheel_moves_by_notches_and_stays_in_range():
    assert sg.wheel_value(0.5, 1) == pytest.approx(0.52) and sg.wheel_value(0.5, -2) == pytest.approx(0.46)
    assert sg.wheel_value(0.99, 5) == 1.0 and sg.wheel_value(0.01, -5) == 0.0
    assert sg.wheel_value(0.0, 1, low=-1.0, high=1.0) == pytest.approx(0.04)
    assert sg.wheel_value(0.5, 1, fine=True) == pytest.approx(0.504)


def test_the_pan_sticks_to_the_center():
    assert sg.snap_center(0.03) == 0.0 and sg.snap_center(-0.03) == 0.0
    assert sg.snap_center(0.2) == 0.2 and sg.snap_center(-0.5) == -0.5
    assert sg.snap_center(0.5, 0.0, 1.0) == 0.5 and sg.snap_center(0.51, 0.0, 1.0, 0.1) == 0.5


def test_a_fader_maps_height_and_value_both_ways():
    assert sg.fader_value(10, 10, 110) == 1.0 and sg.fader_value(110, 10, 110) == 0.0
    assert sg.fader_value(60, 10, 110) == pytest.approx(0.5)
    assert sg.fader_value(-50, 10, 110) == 1.0 and sg.fader_value(500, 10, 110) == 0.0
    for value in (0.0, 0.25, 0.8, 1.0):
        assert sg.fader_value(sg.fader_y(value, 10, 110), 10, 110) == pytest.approx(value)
    assert sg.fader_y(7.0, 10, 110) == 10.0


def test_value_texts():
    assert sg.percent_text(0.784) == "78" and sg.percent_text(2.0) == "100" and sg.percent_text(-1) == "0"
    assert sg.pan_text(0.0) == "C" and sg.pan_text(0.004) == "C"
    assert sg.pan_text(-0.4) == "G 40" and sg.pan_text(1.0) == "D 100" and sg.pan_text(9.0) == "D 100"

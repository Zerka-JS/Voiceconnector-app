"""Composants du Studio : voyants, boutons de canal, pas, potards, faders. Les gestes sont joués par
les méthodes publiques (`press_at`, `drag_to`…) ; test_studio_events.py rejoue les mêmes avec de
vraies souris."""

import pytest

from core import studio_geometry as sg
from ui import studio_style as st
from ui import studio_widgets as sw


def record(signal):
    values = []
    signal.connect(lambda *args: values.append(args if len(args) != 1 else args[0]))
    return values


# --------------------------------------------------------------------------- #
# Voyant
# --------------------------------------------------------------------------- #
def test_led_toggles_and_tells_what_the_user_wants(qapp):
    led = sw.LedButton(tip="Muet")
    changes = record(led.toggled)
    assert not led.is_on()
    led.click_primary()
    led.click_primary()
    assert changes == [True, False] and not led.is_on()


def test_led_set_on_never_emits(qapp):
    led = sw.LedButton("S", color=st.YELLOW)
    changes = record(led.toggled)
    led.set_on(True)
    led.set_on(True)
    assert led.is_on() and changes == []
    led.set_color(st.RED)
    led.set_on(False)
    assert not led.is_on() and changes == []


def test_activity_led_ignores_tiny_changes(qapp):
    led = sw.ActivityLed()
    led.set_level(0.01)
    assert led.level() == 0.0
    led.set_level(0.8)
    assert led.level() == 0.8
    led.set_level(5.0)
    assert led.level() == 1.0
    led.set_level(-1.0)
    assert led.level() == 0.0


# --------------------------------------------------------------------------- #
# Bouton de canal
# --------------------------------------------------------------------------- #
def test_name_button_shows_what_it_is_given(qapp):
    button = sw.NameButton()
    clicks = record(button.clicked)
    button.set_data("Kick", "#ff0000", True)
    assert button.text() == "Kick" and button.is_selected() and button.toolTip() == "Kick"
    button.clicked.emit()
    assert clicks == [()]
    button.set_data("Kick", "#ff0000", False, dim=True)
    assert not button.is_selected()


# --------------------------------------------------------------------------- #
# Ligne de pas
# --------------------------------------------------------------------------- #
@pytest.fixture
def row(qapp):
    widget = sw.StepRow()
    widget.resize(640, sw.STEP_HEIGHT)
    widget.set_data([False] * 16, 16, "#6b7a74", -1)
    return widget


def centers(widget):
    return [cell.center for cell in sg.StepLayout(widget.width(), widget.count(), margin=1.0,
                                                  min_cell=sw.STEP_VISIBLE_MIN).cells]


def test_step_row_takes_data_without_emitting(row):
    changes = record(row.stepChanged)
    row.set_data([True, False, True] + [False] * 13, 16, "#ff8800", 2)
    assert row.steps()[:3] == [True, False, True] and row.playhead() == 2 and changes == []
    assert len(row.steps()) == 16


def test_step_row_pads_and_truncates_to_the_count(row):
    row.set_data([True] * 5, 8, "#ff8800", -1)
    assert row.steps() == [True] * 5 + [False] * 3 and row.count() == 8
    row.set_data([True] * 40, 16, "#ff8800", -1)
    assert row.steps() == [True] * 16


def test_a_click_toggles_one_step(row):
    changes = record(row.stepChanged)
    x = centers(row)
    row.press_at(x[3])
    row.release()
    assert changes == [(3, True)] and row.steps()[3]
    row.press_at(x[3])
    row.release()
    assert changes[-1] == (3, False) and not row.steps()[3]


def test_a_drag_paints_a_run_of_steps(row):
    changes = record(row.stepChanged)
    x = centers(row)
    row.press_at(x[2])
    row.move_to(x[6])
    row.release()
    assert changes == [(2, True), (3, True), (4, True), (5, True), (6, True)]
    assert row.steps()[2:7] == [True] * 5
    row.move_to(x[9])                                                 # le geste est fini : la souris ne peint plus
    assert row.steps()[9] is False and len(changes) == 5


def test_a_drag_erases_when_it_starts_on_a_lit_step(row):
    row.set_data([True] * 8 + [False] * 8, 16, "#6b7a74", -1)
    changes = record(row.stepChanged)
    x = centers(row)
    row.press_at(x[1])
    row.move_to(x[10])
    row.release()
    assert [c for c in changes if not c[1]] == [(i, False) for i in range(1, 8)]
    assert (8, True) not in changes and row.steps()[8:] == [False] * 8


def test_data_from_the_daw_does_not_undo_a_gesture_in_progress(row):
    x = centers(row)
    row.press_at(x[2])
    row.move_to(x[4])
    row.set_data([False] * 16, 16, "#6b7a74", 5)                      # l'écho du logiciel, encore à l'ancien état
    assert row.steps()[2:5] == [True, True, True] and row.playhead() == 5
    row.release()
    row.set_data([False] * 16, 16, "#6b7a74", 5)
    assert row.steps()[2:5] == [False] * 3


def test_a_press_outside_the_steps_does_nothing(row):
    changes = record(row.stepChanged)
    row.press_at(-40)
    row.move_to(100)
    assert changes == []


def test_the_row_asks_for_the_room_its_steps_need(qapp):
    widget = sw.StepRow()
    widget.set_data([False] * 16, 16, "#6b7a74", -1)
    small, preferred = widget.minimum_width(), widget.preferred_width()
    widget.set_data([False] * 64, 64, "#6b7a74", -1)
    assert widget.minimum_width() > small * 3 and widget.preferred_width() > preferred * 3
    assert widget.minimum_width() < widget.preferred_width()
    assert widget.minimum_width() == int(sg.StepLayout(0, 64, margin=1.0, min_cell=sw.STEP_VISIBLE_MIN).content_width)


# --------------------------------------------------------------------------- #
# Potard
# --------------------------------------------------------------------------- #
def test_knob_drag_changes_the_value_and_tells(qapp):
    knob = sw.MiniKnob(default=0.5)
    changes = record(knob.valueChanged)
    knob.press_at(100)
    knob.drag_to(70)
    assert knob.value() == pytest.approx(0.5 + 30 / sg.KNOB_SPAN, abs=1e-3) and len(changes) == 1
    knob.drag_to(40, fine=True)
    assert knob.value() == pytest.approx(0.5 + 60 / sg.KNOB_SPAN * sg.FINE, abs=2e-3)
    knob.release()
    knob.drag_to(0)
    assert len(changes) == 2                                          # relâché : plus de réglage


def test_knob_ignores_the_daw_while_it_is_turned(qapp):
    knob = sw.MiniKnob(default=0.5)
    changes = record(knob.valueChanged)
    knob.press_at(100)
    knob.drag_to(80)
    mine = knob.value()
    knob.set_value(0.1)
    assert knob.value() == mine and knob.is_dragging()
    knob.release()
    knob.set_value(0.1)
    assert knob.value() == pytest.approx(0.1) and len(changes) == 1   # set_value n'émet jamais


def test_knob_wheel_and_double_click(qapp):
    knob = sw.MiniKnob(default=0.78)
    changes = record(knob.valueChanged)
    knob.turn(3)
    assert knob.value() == pytest.approx(0.78 + 3 * sg.WHEEL_STEP, abs=1e-3)
    knob.reset()
    assert knob.value() == pytest.approx(0.78) and changes[-1] == pytest.approx(0.78)
    knob.reset()
    assert len(changes) == 2                                          # déjà à la valeur par défaut : pas d'ordre


def test_pan_knob_is_bipolar_and_sticks_to_the_center(qapp):
    knob = sw.MiniKnob(low=-1.0, high=1.0, default=0.0, bipolar=True)
    changes = record(knob.valueChanged)
    knob.press_at(100)
    knob.drag_to(100 - 40)                                            # +0,53 sur une plage de 2
    assert knob.value() == pytest.approx(40 / sg.KNOB_SPAN * 2, abs=1e-2)
    knob.drag_to(100 - 1)                                             # presque au centre : « colle » à 0
    assert knob.value() == 0.0
    knob.release()
    assert changes[-1] == 0.0
    assert "C" in knob.toolTip()
    knob.set_value(-0.5)
    assert "G 50" in knob.toolTip()


def test_knob_never_leaves_its_range(qapp):
    knob = sw.MiniKnob(default=0.5)
    knob.press_at(0)
    knob.drag_to(-10000)                                              # vers le haut : la valeur monte
    assert knob.value() == 1.0
    knob.drag_to(10000)
    assert knob.value() == 0.0
    knob.release()
    knob.set_value(9.0)
    assert knob.value() == 1.0


# --------------------------------------------------------------------------- #
# Fader
# --------------------------------------------------------------------------- #
@pytest.fixture
def fader(qapp):
    widget = sw.StudioFader(default=0.8)
    widget.resize(sw.FADER_WIDTH, 200)
    widget.set_value(0.5)
    return widget


def test_fader_is_grabbed_by_its_thumb_without_jumping(fader):
    changes = record(fader.valueChanged)
    y = fader.thumb_y()
    fader.press_at(y + 6)                                             # sur le curseur, un peu de côté
    assert changes == [] and fader.value() == 0.5
    fader.drag_to(y + 6 - 40)
    top, bottom = fader._track()
    assert fader.value() == pytest.approx(0.5 + 40 / (bottom - top), abs=2e-3)
    fader.release()


def test_fader_jumps_when_clicked_elsewhere_on_the_track(fader):
    changes = record(fader.valueChanged)
    top, bottom = fader._track()
    fader.press_at(top + 5)
    assert fader.value() == pytest.approx(1 - 5 / (bottom - top), abs=2e-3) and len(changes) == 1
    fader.drag_to(bottom + 50)
    assert fader.value() == 0.0
    fader.release()


def test_fader_fine_drag_is_finer(fader):
    y = fader.thumb_y()
    fader.press_at(y)
    fader.drag_to(y - 30, fine=True)
    top, bottom = fader._track()
    assert fader.value() == pytest.approx(0.5 + 30 / (bottom - top) * sg.FINE, abs=2e-3)


def test_fader_set_value_and_level_never_emit(fader):
    changes = record(fader.valueChanged)
    fader.set_value(0.9)
    fader.set_level(0.6)
    assert fader.value() == 0.9 and fader.level() == 0.6 and changes == []
    fader.press_at(fader.thumb_y())
    fader.set_value(0.1)
    assert fader.value() == 0.9                                       # en cours de saisie : l'écran de l'utilisateur fait foi
    fader.release()


def test_fader_wheel_and_double_click(fader):
    changes = record(fader.valueChanged)
    fader.turn(-5)
    assert fader.value() == pytest.approx(0.4, abs=1e-3)
    fader.reset()
    assert fader.value() == 0.8 and len(changes) == 2


def test_fader_level_is_clamped_and_filtered(fader):
    fader.set_level(0.005)
    assert fader.level() == 0.0
    fader.set_level(7)
    assert fader.level() == 1.0

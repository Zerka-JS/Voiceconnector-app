"""Design matériel VoiceConnector : matrice, solo, mute/eject all, réglages mémorisés."""

from PySide6.QtWidgets import QMessageBox

from conftest import pump
from test_ui import click, drag


def test_matrix_toggles_route_and_follows_engine(qapp, mixer):
    window, vm, _ = mixer
    initial_state = vm._strips[0].routes[0]
    rect = window.matrix._cell_rect(0, 0)
    click(window.matrix, (rect.center().x(), rect.center().y()))
    pump(qapp, 0.1)
    assert vm._strips[0].routes[0] != initial_state
    if not initial_state:
        assert window.strips[0].knobs[0].isActive()
        assert window.matrix.state(0, 0) is True


def test_route_button_updates_matrix(qapp, mixer):
    window, vm, _ = mixer
    knob = window.strips[1].knobs[1]
    center = (knob.width() / 2, knob.height() / 2)
    before = window.matrix.state(1, 1)
    click(knob, center)
    pump(qapp, 0.1)
    assert window.matrix.state(1, 1) != before


def test_solo_mutes_the_others_then_restores(qapp, mixer):
    window, vm, _ = mixer
    window.strips[1].solo.click()
    pump(qapp, 0.1)
    assert not vm._strips[1].mute
    assert all(vm._strips[i].mute for i in range(len(window.strips)) if i != 1)
    window.strips[1].solo.click()
    pump(qapp, 0.1)
    assert not all(vm._strips[i].mute for i in range(len(window.strips)) if i != 1)


def test_mute_all_toggles(qapp, mixer):
    window, vm, _ = mixer
    window.master.mute_all.click()
    pump(qapp, 0.1)
    assert all(s.mute for s in vm._strips)
    window.master.mute_all.click()
    pump(qapp, 0.1)
    assert not any(s.mute for s in vm._strips)


def test_eject_all_disconnects_every_route(qapp, mixer, monkeypatch):
    window, vm, _ = mixer
    window.strips[0].knobs[0].toggled.emit(True)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window.master.eject_all.click()
    pump(qapp, 0.1)
    assert not any(any(s.routes) for s in vm._strips)


def test_trim_is_remembered_in_config(qapp, mixer):
    window, _vm, _ = mixer
    strip = next(s for s in window.strips if s.trim is not None)
    center = (strip.trim.width() / 2, strip.trim.height() / 2)
    drag(strip.trim, center, (center[0], center[1] - 40))
    pump(qapp, 0.05)
    assert strip.trim.value() > 0
    assert window._cfg["ui"]["hardware"]["trim"][str(strip.index)] > 0


def test_eq_knob_emits_and_persists(qapp, mixer):
    window, _vm, _ = mixer
    dial = window.master.eq["bass"]
    center = (dial.width() / 2, dial.height() / 2)
    drag(dial, center, (center[0], center[1] - 40))
    pump(qapp, 0.05)
    assert dial.value() > 0 and window._cfg["ui"]["hardware"]["eq"]["bass"] > 0


def test_outputs_a1_follows_master_knob(qapp, mixer):
    window, vm, _ = mixer
    window.master.knob._user_set(-10.0)
    pump(qapp, 0.1)
    assert vm._headphone == -10.0
    assert abs(window.outputs.outputs[0].fader.value() + 10.0) < 0.01


def test_logo_asset_present():
    from core import config

    assert config.resource_path("assets/voiceconnector_logo.png").is_file()
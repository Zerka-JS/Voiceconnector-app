"""L'interface de la console : Solo, MUTE ALL / EJECT ALL, casque, Compr. / Gate, musique en cours, Studio.

Console en démo (Voicemeeter simulé). Les décisions elles-mêmes sont testées sans Qt ailleurs
(test_session_coordinator, test_layout_manager, test_media…) ; ici, on vérifie que la fenêtre les applique."""

import pytest

pytest.importorskip("PySide6")

from conftest import pump  # noqa: E402


def mute_states(window, vm) -> dict[int, bool]:
    """État « coupé » de chaque tranche affichée, tel que le moteur le connaît."""
    return {strip.index: vm._strips[strip.index].mute for strip in window.strips}


def routes_in_use(window, vm) -> list[int]:
    """Tranches encore envoyées vers au moins un bus."""
    return [strip.index for strip in window.strips if any(vm._strips[strip.index].routes)]


@pytest.fixture
def with_player(qapp, cfg):
    """Console en démo dont le lecteur du Master reçoit de la (fausse) musique."""
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui.main_window import MainWindow

    media = DemoMedia()
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=DemoReaper(cfg), processes=DemoProcesses(),
                        persist=False, apps=DemoAppVolumes(), media=media)
    window.resize(1920, 1040)
    window.show()
    pump(qapp, 0.2)
    yield window, media
    window.close()


# --------------------------------------------------------------------------- #
# Solo
# --------------------------------------------------------------------------- #
def test_solo_mutes_the_other_strips_then_gives_them_back(mixer, qapp):
    window, vm, _ = mixer
    before = mute_states(window, vm)
    kept = window.strips[2]
    kept.solo.setChecked(True)
    pump(qapp, 0.1)
    assert mute_states(window, vm) == {index: index != kept.index for index in before}
    kept.solo.setChecked(False)
    pump(qapp, 0.1)
    assert mute_states(window, vm) == before              # y compris le micro, déjà coupé avant le solo


def test_two_solos_keep_both_strips_audible(mixer, qapp):
    window, vm, _ = mixer
    first, second = window.strips[1], window.strips[3]
    first.solo.setChecked(True)
    second.solo.setChecked(True)
    pump(qapp, 0.1)
    states = mute_states(window, vm)
    assert not states[first.index] and not states[second.index]
    assert all(muted for index, muted in states.items() if index not in (first.index, second.index))


# --------------------------------------------------------------------------- #
# MUTE ALL / EJECT ALL
# --------------------------------------------------------------------------- #
def test_mute_all_mutes_everything_then_unmutes_everything(mixer, qapp):
    window, vm, _ = mixer
    pill = window.master.mute_all
    assert not pill.is_lit()
    pill.click()
    pump(qapp, 0.1)
    assert all(mute_states(window, vm).values()) and pill.is_lit()
    pill.click()
    pump(qapp, 0.1)
    assert not any(mute_states(window, vm).values()) and not pill.is_lit()


def test_mute_all_follows_the_strips_one_by_one(mixer, qapp):
    window, vm, _ = mixer
    for strip in window.strips:
        strip.mute.setChecked(True)
    pump(qapp, 0.1)
    assert window.master.mute_all.is_lit()                # tout est coupé à la main : le pill s'allume
    window.strips[0].mute.setChecked(False)
    pump(qapp, 0.1)
    assert not window.master.mute_all.is_lit()


def test_mute_all_ends_a_solo(mixer, qapp):
    window, vm, _ = mixer
    window.strips[1].solo.setChecked(True)
    window.master.mute_all.click()
    pump(qapp, 0.1)
    assert not window.strips[1].solo.isChecked() and window._solo == set()
    assert all(mute_states(window, vm).values())


def test_eject_all_disconnects_every_route_after_confirmation(mixer, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, vm, _ = mixer
    asked = []

    def yes(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(yes))
    assert routes_in_use(window, vm)
    window.master.eject_all.click()
    pump(qapp, 0.1)
    assert asked and routes_in_use(window, vm) == []


def test_eject_all_does_nothing_when_the_question_is_refused(mixer, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, vm, _ = mixer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    before = routes_in_use(window, vm)
    window.master.eject_all.click()
    pump(qapp, 0.1)
    assert before and routes_in_use(window, vm) == before


# --------------------------------------------------------------------------- #
# Casque, compresseur, gate
# --------------------------------------------------------------------------- #
def test_the_master_knob_drives_the_headphones(mixer, qapp):
    window, vm, _ = mixer
    window.master.knob._user_set(-10.0)
    pump(qapp, 0.1)
    assert vm._headphone == -10.0


def test_compressor_and_gate_are_remembered_by_the_interface(mixer, qapp):
    window, vm, _ = mixer
    strip = window.strips[0]
    strip.comp._user_set(6.0)
    strip.gate._user_set(2.0)
    pump(qapp, 0.1)
    store = window._cfg["ui"]["hardware"]
    assert store["comp"][str(strip.index)] == 6.0 and store["gate"][str(strip.index)] == 2.0
    strip.set_comp_gate(0.0, 0.0)                         # la tranche est reconstruite : on relit la mémoire
    window._restore_dynamics()
    assert strip.comp.value() == 6.0 and strip.gate.value() == 2.0


# --------------------------------------------------------------------------- #
# Musique en cours
# --------------------------------------------------------------------------- #
def test_the_player_stays_hidden_when_windows_gives_no_media_controls(mixer):
    window, _vm, _ = mixer                                # moteur média « indisponible » par défaut
    assert window.master.media.isHidden()


def test_the_player_follows_the_music(with_player, qapp):
    window, media = with_player
    player = window.master.media
    assert player.isVisible() and player.title.text() == "Néons de minuit"
    assert player.artist.text() == "Les Ondes" and player.play.is_playing()
    media.next()
    assert player.title.text() == "Ligne de basse"
    player.previous.click()                               # les boutons commandent le lecteur de Windows
    assert media.current.title == "Néons de minuit" and player.title.text() == "Néons de minuit"
    player.next.click()
    assert media.current.title == "Ligne de basse"


def test_the_play_button_pauses_and_resumes_the_music(with_player, qapp):
    window, media = with_player
    player = window.master.media
    player.play.click()
    pump(qapp, 0.1)
    assert media.current.playing is False and not player.play.is_playing()
    player.play.click()
    pump(qapp, 0.1)
    assert media.current.playing is True and player.play.is_playing()


def test_the_player_can_be_hidden_from_the_settings(with_player, cfg):
    window, _media = with_player
    cfg.setdefault("ui", {})["show_media"] = False
    window.apply_appearance()
    assert window.master.media.isHidden()
    cfg["ui"]["show_media"] = True
    window.apply_appearance()
    assert window.master.media.isVisible()


# --------------------------------------------------------------------------- #
# Studio
# --------------------------------------------------------------------------- #
def test_the_studio_button_opens_one_studio_window_and_closing_it_frees_the_slot(mixer, qapp):
    window, _vm, _ = mixer
    button = window.action_bar.studio_button
    assert not button.isHidden() and window._studio is None   # toujours là, même sans logiciel de musique
    button.click()
    pump(qapp, 0.2)
    studio = window._studio
    assert studio is not None and studio.isVisible()
    button.click()                                        # un second clic ramène la même fenêtre
    assert window._studio is studio
    studio.close()
    pump(qapp, 0.2)
    assert window._studio is None


def test_the_logo_and_the_mark_ship_with_the_app():
    from core import config

    for name in ("voiceconnector_logo.png", "voiceconnector_mark.png"):
        assert config.resource_path(f"assets/{name}").is_file()

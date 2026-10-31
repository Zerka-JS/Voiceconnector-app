"""Interface des deux modes, des dispositions, des thèmes globaux et du bouton « Mode musical ».

Console en démo qui « voit » FL Studio, Discord et OBS (comme `--demo`) : mode A avec Voicemeeter
simulé, mode B avec l'audio de Windows simulé. Les décisions elles-mêmes sont testées sans Qt
(test_session_coordinator, test_layout_manager, test_mixer_router…) ; ici, on vérifie que la fenêtre
les applique."""

import pytest

pytest.importorskip("PySide6")

from conftest import pump  # noqa: E402


def build_window(qapp, cfg, standalone: bool):
    """Fenêtre principale en démo : (fenêtre, console, session). `fl_sync_available=False` : aucun port MIDI
    n'est jamais ouvert pendant les tests."""
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter, demo_detector
    from core.detect_service import DetectService
    from core.session_coordinator import SessionCoordinator
    from core.standalone_ctrl import create_standalone
    from core.system_audio import DemoSystemAudio
    from ui.main_window import MainWindow

    detector = DetectService(demo_detector())
    if standalone:
        cfg.setdefault("session", {})["mode"] = "standalone"
        session = SessionCoordinator(cfg, host_kind="none", mode="standalone", fl_sync_available=False)
        first = session.start(detector.scan_now())
        vm, host = create_standalone(cfg, first.profile, DemoSystemAudio())
    else:
        vm, host = DemoVoicemeeter(cfg), DemoReaper(cfg)
        session = SessionCoordinator(cfg, host_kind=host.kind, fl_sync_available=False)
        session.start(detector.scan_now())
    window = MainWindow(cfg=cfg, vm=vm, reaper=host, processes=DemoProcesses(), persist=False,
                        apps=DemoAppVolumes(), session=session, detector=detector)
    window.resize(1920, 1040)
    window.show()
    pump(qapp, 0.25)
    return window, vm, session


@pytest.fixture
def console(qapp, cfg):
    """Mode A (Voicemeeter simulé), FL Studio suivi."""
    window, vm, session = build_window(qapp, cfg, standalone=False)
    yield window, vm, session
    window.close()


@pytest.fixture
def autonomous(qapp, cfg):
    """Mode B (sans Voicemeeter), FL Studio suivi."""
    window, vm, session = build_window(qapp, cfg, standalone=True)
    yield window, vm, session
    window.close()


def follow(qapp, window, key: str) -> None:
    window._apply_change(window._session.select_app(key))
    pump(qapp, 0.1)


def shown_widgets(window) -> list:
    return [window.body.itemAt(i).widget() for i in range(window.body.count())]


# --------------------------------------------------------------------------- #
# Bouton « Mode musical »
# --------------------------------------------------------------------------- #
def test_the_music_button_exists_only_for_a_music_software(qapp, console):
    window, _, session = console
    button = window.action_bar.music_button
    assert session.active.key == "fl_studio" and button.isVisible()
    for key in ("discord", "zoom", "spotify", "obs", "audacity"):
        follow(qapp, window, key)
        assert not button.isVisible() and not button.isChecked(), key
    for key in ("ableton", "reaper", "fl_studio"):
        follow(qapp, window, key)
        assert button.isVisible(), key


def test_a_voice_software_cannot_turn_the_music_mode_on(qapp, console):
    window, _, session = console
    follow(qapp, window, "discord")
    window._on_music_toggled(True)
    pump(qapp, 0.05)
    assert not session.music_on and not window.action_bar.music_button.isChecked()
    assert session.preset == "standard"


def test_the_music_mode_ends_when_the_music_software_goes_away(qapp, console):
    window, _, session = console
    window._on_music_toggled(True)
    assert session.music_on and window.action_bar.music_button.isChecked()
    follow(qapp, window, "discord")
    assert not session.music_on and not window.action_bar.music_button.isVisible()
    assert session.preset == "standard"


def test_the_music_mode_switches_to_the_studio_layout_and_back(qapp, console):
    window, _, session = console
    assert session.preset == "standard" and shown_widgets(window)[-1] is window.master
    window._on_music_toggled(True)
    pump(qapp, 0.1)
    assert session.music_on and session.preset == "fl_studio" and window.action_bar.music_button.isChecked()
    assert shown_widgets(window)[0] is window.master            # Master à gauche dans la disposition studio
    assert not window._matrix_panel.isVisible()
    window._on_music_toggled(False)
    pump(qapp, 0.1)
    assert not session.music_on and session.preset == "standard"
    assert shown_widgets(window)[-1] is window.master and window._matrix_panel.isVisible()


def test_the_music_mode_frees_the_voice_processing_and_gives_it_back(qapp, console):
    window, vm, _ = console
    inserts = set(vm.inserts)
    assert 0 in inserts and vm._strips[0].dynamics == (3.0, 1.5)   # le micro de la démo est compressé
    window._on_music_toggled(True)
    pump(qapp, 0.1)
    freed = set(window._release.strips)
    assert 0 in freed                                           # le micro est un canal vocal
    assert vm._strips[0].dynamics == (0.0, 0.0)
    assert vm.inserts == inserts - freed and not vm.inserts & freed   # plus d'insert : moins de latence
    assert not window.strips[0]._fx_section.isEnabled()
    window._on_music_toggled(False)
    pump(qapp, 0.1)
    assert vm._strips[0].dynamics == (3.0, 1.5) and vm.inserts == inserts   # réglages d'origine rétablis
    assert window.strips[0]._fx_section.isEnabled()


# --------------------------------------------------------------------------- #
# Dispositions
# --------------------------------------------------------------------------- #
def test_each_layout_arranges_the_console(qapp, console):
    window, _, session = console
    expected = {"standard": ("right", True), "fl_studio": ("left", False),
                "streaming": ("right", True), "podcast": ("right", False)}
    for key, (side, matrix) in expected.items():
        window._apply_change(session.set_layout(key))
        pump(qapp, 0.1)
        plan = window._plan
        assert session.preset == key and plan.master_side == side, key
        assert window._matrix_panel.isVisible() == matrix, key
        assert ({s.index for s in window.strips if s.isVisible()}
                == {p.index for p in plan.strips if p.visible}), key
        assert (shown_widgets(window)[0] is window.master) == (side == "left"), key


def test_the_layouts_style_their_strips(qapp, console):
    window, _, session = console
    window._apply_change(session.set_layout("fl_studio"))
    visible = [window.strips[p.index] for p in window._plan.strips if p.visible]
    assert visible and all(s.shows("wide_pan") and not s.shows("pad") and s.vu.precision for s in visible)
    window._apply_change(session.set_layout("podcast"))
    visible = [window.strips[p.index] for p in window._plan.strips if p.visible]
    assert visible and all(s.shows("dynamics") and not s.shows("wide_pan") for s in visible)
    window._apply_change(session.set_layout("standard"))
    assert all(s.shows("pad") and not s.vu.precision for s in window.strips if s.isVisible())


def test_the_settings_choose_the_layout_and_the_followed_software(qapp, console):
    from ui.settings_dialog import SETTINGS_TAB_LAYOUT, SettingsDialog

    window, _, session = console
    dialog = SettingsDialog(window._cfg, window._processes, window._reaper, window._vm, window,
                            persist=False, session=session, detector=window._detector,
                            start_tab=SETTINGS_TAB_LAYOUT, live=window._live_state)
    dialog.sessionChanged.connect(window._apply_change)
    try:
        tab = dialog.layout_tab
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Mode et thème"
        tab.layout_choice.setCurrentIndex(tab.layout_choice.findData("streaming"))
        assert session.preset == "streaming" and session.layouts.selector_value == "streaming"
        assert list(tab.role_choices) == ["mic", "game", "discord", "music"]
        tab.layout_choice.setCurrentIndex(tab.layout_choice.findData("auto"))
        assert session.layouts.selector_value == "auto" and session.preset == "fl_studio"
        tab.app_choice.setCurrentIndex(tab.app_choice.findData("discord"))
        assert session.active.key == "discord" and session.preset == "streaming"
        assert not window.action_bar.music_button.isVisible()
        tab.app_choice.setCurrentIndex(tab.app_choice.findData("auto"))
        assert session.active.key == "fl_studio" and session.preset == "fl_studio"
    finally:
        dialog.done(0)


def test_a_slot_can_be_given_to_a_chosen_channel(qapp, console):
    from ui.settings_dialog import SettingsDialog

    window, _, session = console
    dialog = SettingsDialog(window._cfg, window._processes, window._reaper, window._vm, window,
                            persist=False, session=session, detector=window._detector,
                            live=window._live_state)
    dialog.sessionChanged.connect(window._apply_change)
    try:
        tab = dialog.layout_tab
        tab.layout_choice.setCurrentIndex(tab.layout_choice.findData("streaming"))
        combo = tab.role_choices["game"]
        combo.setCurrentIndex(combo.findData(3))
        assert session.layouts.explicit_roles("streaming")["game"] == 3
        assert window._plan.roles["game"] == 3
        combo.setCurrentIndex(0)                               # « Automatique »
        assert "game" not in session.layouts.explicit_roles("streaming")
    finally:
        dialog.done(0)


# --------------------------------------------------------------------------- #
# Thèmes globaux
# --------------------------------------------------------------------------- #
def test_the_four_global_themes_from_the_layout_tab(qapp, console):
    from core.themes import GLOBAL_THEME_ORDER, GLOBAL_THEMES
    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, _ = console
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            session=window._session, detector=window._detector)
    dialog.appearanceChanged.connect(window.apply_appearance)
    try:
        tab = dialog.layout_tab
        assert list(tab.theme_cards) == list(GLOBAL_THEME_ORDER) == ["retro", "cyberpunk", "flat", "slate"]
        for key in GLOBAL_THEME_ORDER:
            tab.theme_cards[key].click()
            pump(qapp, 0.05)
            spec = GLOBAL_THEMES[key]
            assert window._cfg["ui"]["theme"] == key and styles.current_theme() == key
            assert styles.global_spec() is not None and styles.global_spec().key == key
            assert styles.GREEN_NEON == spec.palette["GREEN_NEON"] and styles.BG_WINDOW == spec.palette["BG_WINDOW"]
            assert [k for k, card in tab.theme_cards.items() if card.isChecked()] == [key]
            assert not any(swatch.isChecked() for swatch in dialog.theme_swatches.values())
            assert spec.name in dialog.theme_label.text()
        dialog.theme_swatches["orange"].click()                # une couleur d'accent remplace le thème global
        assert styles.global_spec() is None and styles.current_theme() == "orange"
        assert not any(card.isChecked() for card in tab.theme_cards.values())
    finally:
        dialog.set_theme("studio")
        dialog.done(0)
    assert styles.GREEN_NEON == "#3b82f6"


# --------------------------------------------------------------------------- #
# Mode A : application vocale
# --------------------------------------------------------------------------- #
def test_the_voice_route_is_applied_only_on_request(qapp, console):
    window, vm, session = console
    follow(qapp, window, "discord")
    route = window._voice_route()
    assert route is not None and route.app.key == "discord" and route.transport == "bus"
    assert route.commands()
    before = {s: vm._strips[s].routes for s in range(len(vm._strips))}
    window._apply_voice_route()
    pump(qapp, 0.1)
    for command in route.commands():
        assert vm._strips[command.strip].routes[command.route] == command.enabled
    assert any(vm._strips[s].routes != before[s] for s in before) or not route.pending(None)
    assert vm.optional_writes == {}                            # rien de VBAN en mode « bus »


def test_the_vban_stream_is_sent_through_the_optional_parameters(qapp, console):
    window, vm, session = console
    follow(qapp, window, "obs")
    session.session.set_transport("obs", "vban")
    session.session.set_vban({"index": 1, "name": "Stream", "ip": "192.168.1.20", "port": 6981})
    route = window._voice_route()
    assert route.transport == "vban" and route.vban.name == "Stream" and route.commands() == []
    window._apply_voice_route()
    assert vm.optional_writes and dict(route.vban_params()).items() <= vm.optional_writes.items()


def test_a_running_voice_software_is_fed_even_when_another_one_is_followed(qapp, console):
    window, _, _ = console
    follow(qapp, window, "spotify")                            # Discord et OBS tournent dans la démo
    route = window._voice_route()
    assert route is not None and route.app.is_voice_app


def test_nothing_to_feed_without_a_voice_software(qapp, console):
    window, _, session = console
    window._detector.stop()                                    # on fixe ce qui « tourne »
    session.detected = [d for d in session.detected if not d.app.is_voice_app]
    follow(qapp, window, "spotify")
    assert window._voice_route() is None
    window._apply_voice_route()                                # un message, aucune erreur


def test_the_voice_group_of_the_settings_follows_the_chosen_software(qapp, console):
    from ui.settings_dialog import SettingsDialog

    window, vm, session = console
    follow(qapp, window, "discord")
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            session=session, detector=window._detector, live=window._live_state)
    routed = []
    dialog.routeRequested.connect(lambda: routed.append(True))
    try:
        tab = dialog.layout_tab
        assert not tab.voice_group.isHidden() and "Discord" in tab.voice_status.text()
        assert tab.apply_route.isEnabled()
        tab.apply_route.click()
        assert routed == [True]
        tab.voice_app_choice.setCurrentIndex(tab.voice_app_choice.findData("obs"))
        assert session.session.voice_app == "obs" and "OBS" in tab.voice_status.text()
        for button in tab.transport_buttons.buttons():
            if button.property("transport") == "vban":
                button.setChecked(True)
        assert session.session.transport("obs") == "vban" and not tab.vban_row.isHidden()
        assert not tab.apply_route.isEnabled()                 # le flux VBAN se règle dans Voicemeeter
    finally:
        dialog.done(0)


# --------------------------------------------------------------------------- #
# Mode B : autonome
# --------------------------------------------------------------------------- #
def test_the_standalone_console_has_the_channels_of_the_followed_software(qapp, autonomous):
    window, vm, session = autonomous
    assert session.standalone and window._standalone
    assert len(window.strips) == len(vm.profile.channels) >= 3
    assert "FL Studio" in vm.profile.name
    follow(qapp, window, "discord")
    assert "Discord" in vm.profile.name and len(window.strips) == len(vm.profile.channels)
    assert session.preset == "streaming" and not window.action_bar.music_button.isVisible()
    follow(qapp, window, "ableton")
    assert "Ableton" in vm.profile.name and session.preset == "fl_studio"
    assert window.action_bar.music_button.isVisible()


def test_the_standalone_console_hides_what_belongs_to_voicemeeter(qapp, autonomous):
    window, _, _ = autonomous
    assert not window.master.tape.isVisible()
    assert all(not strip.shows("routing") for strip in window.strips if strip.isVisible())
    assert all(strip.apps is None for strip in window.strips)   # volumes par application : ceux de Windows


def test_the_standalone_faders_drive_the_windows_volumes(qapp, autonomous):
    window, vm, _ = autonomous
    vm.set_gain(1, -6.0)
    vm.set_mute(1, True)
    vm._tick()
    pump(qapp, 0.05)
    strip = window._params.strips[1]
    assert strip.mute is True and strip.gain == pytest.approx(-6.0, abs=0.2)
    assert window.strips[1].fader.value() == pytest.approx(-6.0, abs=0.2)


def test_renaming_a_standalone_channel_is_remembered_for_the_profile(qapp, autonomous):
    window, vm, session = autonomous
    window._on_renamed(0, "Ma régie")
    assert session.session.overrides(vm.profile.name)[0]["name"] == "Ma régie"


def test_the_standalone_settings_offer_the_profile_not_the_voice_routing(qapp, autonomous):
    from ui.settings_dialog import SettingsDialog

    window, vm, session = autonomous
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            session=session, detector=window._detector, live=window._live_state)
    dialog.sessionChanged.connect(window._apply_change)
    try:
        tab = dialog.layout_tab
        assert not tab.profile_box.isHidden() and tab.voice_group.isHidden()
        assert vm.profile.name in tab.profile_label.text()
    finally:
        dialog.done(0)


def test_a_standalone_profile_can_be_exported_and_imported_again(qapp, autonomous, tmp_path):
    import json

    from core.profile_import import load_profile
    from ui.settings_dialog import SettingsDialog

    window, vm, session = autonomous
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            session=session, detector=window._detector, live=window._live_state)
    dialog.sessionChanged.connect(window._apply_change)
    try:
        tab = dialog.layout_tab
        path = tmp_path / "studio.json"
        assert tab.export_profile(str(path))
        saved = load_profile(path)
        assert saved.name == vm.profile.name and len(saved.channels) == len(vm.profile.channels)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = "Mon studio"
        path.write_text(json.dumps(data), encoding="utf-8")
        tab.set_profile(str(path))                             # le profil importé remplace l'automatique
        assert vm.profile.name == "Mon studio" and len(window.strips) == len(saved.channels)
        assert session.session.profile == str(path)
        tab.set_profile("")                                    # retour au profil choisi d'après le logiciel
        assert vm.profile.name != "Mon studio" and session.session.profile == ""
        assert not tab.export_profile(str(tmp_path / "absent" / "p.json"))      # dossier inexistant
    finally:
        dialog.done(0)


def test_the_fl_studio_setup_can_be_checked_and_installed(qapp, console, tmp_path, monkeypatch):
    from core import fl_sync
    from ui import layout_settings
    from ui.settings_dialog import SettingsDialog

    class Midi:
        def get_output_names(self):
            return ["VoiceConnector FL 1"]

        def get_input_names(self):
            return ["VoiceConnector VC 2"]

    monkeypatch.setattr(layout_settings, "fl_setup_checks",
                        lambda cfg: fl_sync.fl_setup_checks(cfg, documents=tmp_path, mido_module=Midi()))
    monkeypatch.setattr(layout_settings, "install_script", lambda: fl_sync.install_script(tmp_path))
    window, vm, session = console
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False, session=session,
                            detector=window._detector)
    try:
        tab = dialog.layout_tab
        assert "✘ Script FL Studio : pas encore installé" in tab.fl_status.text()
        assert "✔ Port MIDI virtuel « VoiceConnector FL »" in tab.fl_status.text()
        assert tab.install_script_button.text() == "Installer le script FL Studio"
        tab.install_script_button.click()
        assert fl_sync.script_status(tmp_path)[0] == fl_sync.SCRIPT_CURRENT
        assert "✔ Script FL Studio : installé et à jour" in tab.fl_status.text()
        assert "Script installé" in tab.fl_status.text() and tab.install_script_button.text().startswith("Réinstaller")
    finally:
        dialog.done(0)


def test_the_mode_choice_asks_for_a_restart(qapp, console):
    from ui.settings_dialog import SettingsDialog

    window, vm, session = console
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            session=session, detector=window._detector)
    asked = []
    dialog.restartRequested.connect(lambda: asked.append(True))
    try:
        tab = dialog.layout_tab
        assert tab.restart_button.isHidden()
        for button in tab.mode_buttons.buttons():
            if button.property("mode") == "standalone":
                button.setChecked(True)
        assert window._cfg["session"]["mode"] == "standalone" and not tab.restart_button.isHidden()
        tab.restart_button.click()
        assert asked == [True]
    finally:
        window._cfg["session"]["mode"] = "voicemeeter"
        dialog.done(0)

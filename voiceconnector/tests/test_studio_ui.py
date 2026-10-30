"""La fenêtre Studio : ce qu'elle montre d'un logiciel (démonstration, Mackie Control), ce qu'un clic demande
au logiciel, et l'aide de configuration. Les gestes des composants sont dans test_studio_widgets.py."""

import pytest

from core import studio_session as ss
from core.daw_model import (CAP_BANK, CAP_TRACK_ARM, CAP_TRACK_MIX, CAP_TRANSPORT, DawLink, DawState, DawTrack,
                            DawTransport)
from core.demo_daw import DemoDaw
from core.fl_sync import SetupCheck
from ui import studio_window as sw


class NoPorts:
    """Une bibliothèque MIDI qui ne voit aucun port : le diagnostic ne dépend pas de la machine de test."""

    @staticmethod
    def get_output_names():
        return []

    @staticmethod
    def get_input_names():
        return []


class MixerLink(DawLink):
    """Un logiciel de type Mackie Control : des pistes à mixer, groupe par groupe, et rien d'autre."""

    key = "stub"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.state = DawState(
            daw="Cubase / Nuendo", project="Mon morceau", connected=True,
            transport=DawTransport(tempo=120.0, ticks=0),
            tracks=[DawTrack(index=i, name=f"Piste {i + 1}", volume=0.5) for i in range(8)],
            master=DawTrack(index=8, name="Master", volume=0.8),
            capabilities=frozenset({CAP_TRANSPORT, CAP_TRACK_MIX, CAP_TRACK_ARM, CAP_BANK}))

    def play_pause(self) -> None:
        self.calls.append(("play_pause",))
        self.state.transport.playing = not self.state.transport.playing

    def set_track_volume(self, track: int, value: float) -> None:
        self.calls.append(("set_track_volume", track, value))

    def bank(self, delta: int) -> None:
        self.calls.append(("bank", delta))


@pytest.fixture
def session(qapp):
    session = ss.StudioSession({}, scan=lambda: [], demo_factory=DemoDaw)
    yield session
    session.close()


@pytest.fixture
def window(session):
    win = sw.StudioWindow(session, cfg={}, interval_ms=1000)
    yield win
    win.close()


def use_link(session, link):
    """La session est déjà ouverte (sinon la fenêtre la rouvrirait) : `link` joue le logiciel relié."""
    session.open()
    session.link = link
    session.mode, session.target = ss.MODE_LIVE, ss.target_for("cubase")


def mixer_window(session):
    """La même fenêtre devant un logiciel qui ne donne que des pistes (comme Cubase en Mackie Control)."""
    use_link(session, MixerLink())
    return sw.StudioWindow(session, cfg={}, interval_ms=1000)


# --------------------------------------------------------------------------- #
# Textes de l'aide
# --------------------------------------------------------------------------- #
def test_the_software_list_offers_auto_then_every_daw_then_the_demo():
    choices = sw.software_choices()
    assert choices[0] == (sw.AUTO_LABEL, "auto") and choices[-1] == (sw.DEMO_LABEL, ss.DEMO_KEY)
    assert [key for _, key in choices[1:-1]] == [target.key for target in ss.TARGETS]
    assert len({key for _, key in choices}) == len(choices)


def test_the_checks_say_ok_or_to_do_and_escape_what_the_software_wrote():
    text = sw.checks_html([SetupCheck(True, "Port prêt"), SetupCheck(False, "Script <absent> & introuvable")])
    assert "OK" in text and "À faire" in text
    assert "Port prêt" in text and "&lt;absent&gt; &amp; introuvable" in text and "<absent>" not in text


def test_the_steps_are_numbered_from_one():
    text = sw.steps_html(["Ouvre le logiciel", "Ajoute la surface"])
    assert text.index("1.") < text.index("Ouvre") < text.index("2.") < text.index("Ajoute")


# --------------------------------------------------------------------------- #
# Fenêtre devant la démonstration
# --------------------------------------------------------------------------- #
def test_without_any_software_the_window_shows_the_demo_and_says_why(window, session):
    assert session.opened and session.demo
    assert "démonstration" in window.banner.text().lower() and not window.banner.isHidden()
    assert not window.rack.isHidden() and not window.playlist.isHidden()
    assert window.mixer.isHidden() and window.empty.isHidden()
    assert "démonstration" in window.windowTitle().lower()
    assert not window.configure.isHidden()                              # on explique comment relier un vrai logiciel


def test_the_demo_project_fills_the_transport_and_the_rack(window, session):
    state = session.link.state
    assert window.project.text() == state.project
    assert window.rack.rows and len(window.rack.rows) == len(state.channels)
    assert window.playlist.rows and len(window.playlist.rows) == len(state.tracks)


def test_play_asks_the_software_and_the_window_follows(window, session):
    assert not session.link.state.transport.playing
    window.toggle_play()
    assert session.link.state.transport.playing
    window.toggle_play()
    assert not session.link.state.transport.playing


def test_a_step_clicked_in_the_rack_reaches_the_software(window, session):
    channel = session.link.state.channels[0]
    before = channel.step(3)
    window.rack.stepChanged.emit(channel.index, 3, not before)
    assert session.link.state.channel(channel.index).step(3) is (not before)


def test_a_muted_channel_is_muted_in_the_software(window, session):
    channel = session.link.state.channels[1]
    window.rack.muteRequested.emit(channel.index, True)
    assert session.link.state.channel(channel.index).muted


def test_a_refused_order_never_brings_the_window_down(window, session):
    session.link.set_channel_volume = lambda *args: (_ for _ in ()).throw(RuntimeError("refusé"))
    window.rack.volumeChanged.emit(0, 0.4)                              # ne lève rien
    window.refresh()


def test_choosing_a_software_reopens_the_link_and_remembers_it(session):
    saved = []
    cfg = {}
    session._cfg = cfg
    win = sw.StudioWindow(session, cfg=cfg, persist=lambda: saved.append(dict(cfg.get("studio", {}))),
                          interval_ms=1000)
    try:
        row = win.software.findData(ss.DEMO_KEY)
        win.software.setCurrentIndex(row)
        win._on_software(row)
        assert session.selected == ss.DEMO_KEY and session.demo
        assert saved and saved[-1]["daw"] == ss.DEMO_KEY
        assert "démonstration" in win.banner.text().lower()
    finally:
        win.close()


def test_the_geometry_is_saved_when_the_window_closes(session):
    cfg = {}
    closed = []
    win = sw.StudioWindow(session, cfg=cfg, persist=lambda: None, interval_ms=1000)
    win.closed.connect(lambda: closed.append(True))
    win.close()
    assert closed == [True]
    assert cfg["studio"]["geometry"]
    assert not session.opened                                           # la liaison est libérée avec la fenêtre


def test_the_saved_geometry_is_restored(qapp):
    first = ss.StudioSession({}, demo_factory=DemoDaw)
    cfg = {}
    win = sw.StudioWindow(first, cfg=cfg, interval_ms=1000)
    win.resize(790, 520)
    win.close()
    second = ss.StudioSession({}, demo_factory=DemoDaw)
    again = sw.StudioWindow(second, cfg=cfg, interval_ms=1000)
    try:
        assert (again.width(), again.height()) == (790, 520)
    finally:
        again.close()


def test_the_tick_redraws_only_when_something_changed(window, session, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "refresh", lambda: calls.append("refresh"))
    monkeypatch.setattr(session, "poll", lambda: 0)
    window.tick()
    assert calls == []
    monkeypatch.setattr(session, "poll", lambda: ss.CHANGED)
    window.tick()
    assert calls == ["refresh"]
    rebinds = []
    monkeypatch.setattr(window, "_rebind", lambda: rebinds.append(True))
    monkeypatch.setattr(session, "poll", lambda: ss.SWITCHED | ss.CHANGED)
    window.tick()
    assert rebinds == [True] and calls == ["refresh"]                   # un changement de logiciel repart de zéro


# --------------------------------------------------------------------------- #
# Fenêtre devant un logiciel qui ne donne que des pistes
# --------------------------------------------------------------------------- #
def test_a_track_mixer_replaces_the_rack_and_the_playlist(session):
    win = mixer_window(session)
    try:
        assert not win.mixer.isHidden()
        assert win.body.isHidden() and win.empty.isHidden()
        assert win.banner.isHidden()                                    # relié : rien à dire
        assert "Cubase" in win.windowTitle() and "démonstration" not in win.windowTitle().lower()
    finally:
        win.close()


def test_the_mixer_faders_and_banks_reach_the_software(session):
    win = mixer_window(session)
    try:
        win.mixer.volumeChanged.emit(2, 0.7)
        win.mixer.bankRequested.emit(1)
        win.toggle_play()
        assert win.session.link.calls == [("set_track_volume", 2, 0.7), ("bank", 1), ("play_pause",)]
    finally:
        win.close()


def test_a_software_without_rack_nor_tracks_says_so(session):
    link = MixerLink()
    link.state.capabilities = frozenset({CAP_TRANSPORT})
    use_link(session, link)
    win = sw.StudioWindow(session, cfg={}, interval_ms=1000)
    try:
        assert not win.empty.isHidden() and win.mixer.isHidden() and win.body.isHidden()
    finally:
        win.close()


def test_a_software_that_goes_away_shows_the_waiting_banner(session):
    win = mixer_window(session)
    try:
        session.link.mark_offline("Cubase est fermé.")
        win.refresh()
        assert not win.banner.isHidden() and "Cubase est fermé" in win.banner.text()
    finally:
        win.close()


# --------------------------------------------------------------------------- #
# Aide à la configuration
# --------------------------------------------------------------------------- #
def test_the_setup_dialog_explains_the_chosen_software(session, tmp_path):
    dialog = sw.StudioSetupDialog(session, documents=tmp_path, source=tmp_path / "absent.py", mido_module=NoPorts())
    try:
        assert dialog.target.key == "fl_studio"                         # rien n'est visé : on explique FL Studio
        assert "À faire" in dialog.checks.text()
        assert "1." in dialog.steps.text() and "loopMIDI" in dialog.ports.text()
        assert not dialog.install.isHidden()
        row = dialog.software.findData("cubase")
        dialog.software.setCurrentIndex(row)
        dialog.refresh()
        assert dialog.target.key == "cubase" and dialog.install.isHidden()
        assert "VoiceConnector DAW" in dialog.ports.text()
    finally:
        dialog.close()


def test_the_setup_dialog_installs_the_flstudio_script(session, tmp_path):
    source = tmp_path / "device_VoiceConnector.py"
    source.write_text("# script\n", encoding="utf-8")
    documents = tmp_path / "Documents"
    documents.mkdir()
    dialog = sw.StudioSetupDialog(session, documents=documents, source=source, mido_module=NoPorts())
    try:
        assert dialog.install_script()
        assert "Script installé" in dialog.result.text()
        installed = list(documents.rglob("device_VoiceConnector.py"))
        assert installed and installed[0].read_text(encoding="utf-8") == "# script\n"
    finally:
        dialog.close()


def test_the_setup_dialog_reports_a_script_that_cannot_be_installed(session, tmp_path):
    dialog = sw.StudioSetupDialog(session, documents=tmp_path, source=tmp_path / "absent.py", mido_module=NoPorts())
    try:
        assert not dialog.install_script()
        assert "Installation impossible" in dialog.result.text()
    finally:
        dialog.close()

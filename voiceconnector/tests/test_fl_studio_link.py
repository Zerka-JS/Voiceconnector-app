"""Le Studio et FL Studio : le vrai script (assets/flstudio) chargé contre un faux projet de FL Studio
dialogue avec la vraie liaison `FlStudioLink`, par un faux câble MIDI (sans Qt, sans MIDI)."""

import pytest
from fl_rig import FakeClock, FakeFlChannel, FakeFlPattern, FakeFlProject, FlStudioRig

from core import fl_sync
from core import studio_proto as sp
from core.daw_model import CAP_PLAYLIST, CAP_RACK, CAP_STEPS, fl_color_to_hex
from core.fl_studio_link import FlStudioLink, open_fl_studio_link, steps_for_length
from core.fl_sync import TransportError

STEP = 0.02                                     # FL Studio appelle OnIdle toutes les ~20 ms


class Cable:
    """Le câble MIDI virtuel entre VoiceConnector et FL Studio : on peut le couper."""

    def __init__(self, rig: FlStudioRig) -> None:
        self.rig, self.cut = rig, False
        self.sent: list[bytes] = []                 # tout ce que VoiceConnector a envoyé

    @property
    def is_open(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        self.sent.append(data)
        if not self.cut:
            self.rig.send(data)

    def refreshes(self) -> list[int]:
        """Les demandes de renvoi (refresh) envoyées, avec leur portée."""
        frames = [fl_sync.parse(raw) for raw in self.sent]
        return [f.data[0] for f in frames if f is not None and f.command == sp.CMD_ST_REFRESH]

    def receive(self) -> list[bytes]:
        messages = self.rig.receive()
        return [] if self.cut else messages

    def close(self) -> None:
        pass


class World:
    """FL Studio (le script et un faux projet) et la liaison du Studio, reliés par un câble MIDI."""

    def __init__(self, monkeypatch, api_version: int = 34, **options) -> None:
        self.clock = FakeClock()
        self.project = FakeFlProject(api_version)
        self.rig = FlStudioRig(monkeypatch, self.project, self.clock)
        self.cable = Cable(self.rig)
        self.link = FlStudioLink(self.cable, clock=self.clock, **options)
        self.link.start()

    @property
    def state(self):
        return self.link.state

    def run(self, seconds: float = 1.0) -> bool:
        """Le temps passe : FL Studio appelle OnIdle, VoiceConnector relit son port MIDI."""
        changed = False
        for _ in range(int(round(seconds / STEP))):
            self.rig.idle(STEP)
            changed |= self.link.poll()
        return changed

    def connect(self) -> "World":
        self.run(0.3)
        assert self.state.connected
        return self

    def on_steps(self, channel: int) -> list[int]:
        return [i for i, on in enumerate(self.state.channel(channel).steps) if on]


@pytest.fixture
def world(monkeypatch, capsys):
    """Fabrique de mondes ; à la fin du test, le script de FL Studio n'a écrit aucune erreur."""
    def build(api_version: int = 34, **options) -> World:
        return World(monkeypatch, api_version, **options)

    yield build
    printed = capsys.readouterr().out
    assert "VoiceConnector (" not in printed, printed


# --------------------------------------------------------------------------- #
# Connexion
# --------------------------------------------------------------------------- #
def test_the_link_connects_and_names_the_software(world):
    w = world()
    assert not w.state.connected and "FL Studio" in w.state.message
    w.run(0.2)
    assert w.state.connected and w.state.message == ""
    assert w.state.daw == "FL Studio 21.2.3" and w.link.fl_version == "21.2.3"
    assert w.state.project == "Maquette"
    assert w.state.can(CAP_RACK) and w.state.can(CAP_STEPS) and w.state.can(CAP_PLAYLIST)
    assert w.rig.has_meters                       # sans cet appel, FL Studio n'appelle jamais OnUpdateMeters


def test_the_whole_project_arrives_after_connecting(world):
    w = world().connect()
    s, t = w.state, w.state.transport
    assert (t.playing, t.recording, t.song_mode, t.metronome) == (False, False, False, False)
    assert t.tempo == pytest.approx(115.0) and (t.ppq, t.beats_per_bar, t.ticks) == (96, 4, 0)
    assert [c.name for c in s.channels] == ["Kick", "Snare", "Hi-Hat", "Basse « sub »"]
    kick = s.channel(0)
    assert kick.color == fl_color_to_hex(0x2040F0) and kick.kind == "sampler" and kick.selected
    assert s.channel(3).kind == "plugin" and not s.channel(3).selected
    assert w.on_steps(0) == [0, 4, 8, 12] and len(kick.steps) == 16
    assert w.on_steps(2) == [0, 2, 4, 6, 8, 10, 12, 14]
    assert [(p.index, p.name, p.length_steps) for p in s.patterns] == [(1, "Pattern 1", 16), (2, "Refrain", 32)]
    assert s.pattern == 1 and s.step_count == 16
    assert len(s.tracks) == 8 and [t.name for t in s.tracks[:3]] == ["Pads", "Basse", "Track 3"]


def test_commands_before_the_connection_do_nothing(world):
    w = world()
    w.link.play_pause()
    w.link.set_tempo(90)
    w.link.toggle_step(0, 1)
    assert not w.state.transport.playing and w.state.transport.tempo == 0.0
    w.run(0.3)
    assert not w.project.playing and w.project.tempo == 115.0 and w.project.calls == []


def test_closing_fl_studio_disconnects_at_once_and_it_reconnects(world):
    w = world().connect()
    w.rig.script.OnDeInit()                       # FL Studio se ferme
    w.run(0.04)
    assert not w.state.connected and "fermé" in w.state.message
    w.rig.script.OnInit()                         # il est relancé, le script est rechargé
    w.run(2.0)
    assert w.state.connected and len(w.state.channels) == 4 and w.on_steps(0) == [0, 4, 8, 12]


def test_a_silent_script_is_declared_gone_after_twelve_seconds(world):
    w = world().connect()
    w.cable.cut = True                            # FL Studio se fige, ou le port virtuel disparaît
    for _ in range(int(11 / STEP)):
        w.clock.advance(STEP)
        w.link.poll()
    assert w.state.connected
    for _ in range(int(2 / STEP)):
        w.clock.advance(STEP)
        w.link.poll()
    assert not w.state.connected and "ne répond plus" in w.state.message
    w.cable.cut = False                           # le câble revient : la liaison se rétablit seule
    w.run(2.0)
    assert w.state.connected and w.on_steps(0) == [0, 4, 8, 12]


def test_stopping_the_link_makes_the_script_go_quiet(world):
    w = world().connect()
    w.link.stop()
    assert not w.state.connected
    w.rig.receive()
    for _ in range(40):
        w.rig.idle(STEP)
    assert w.rig.outbox == []                     # le script n'envoie plus rien au Studio


def test_a_closed_port_does_not_crash_the_link(world):
    w = world().connect()
    w.cable.send = lambda data: (_ for _ in ()).throw(OSError("port fermé"))
    w.link.play_pause()
    w.link.set_tempo(100)
    w.run(0.2)                                    # aucune exception


def test_project_name_comes_from_the_window_title(world):
    w = world().connect()
    w.project.title = "Démo été - FL Studio 21"
    w.run(1.2)
    assert w.state.project == "Démo été"
    w.project.title = "FL Studio 21"              # aucun projet nommé
    w.run(1.2)
    assert w.state.project == ""


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def test_play_pause_stop_and_the_position_moves_with_the_song(world):
    w = world().connect()
    w.link.play_pause()
    assert w.state.transport.playing              # tout de suite, sans attendre FL Studio
    w.run(2.0)
    assert w.project.playing and w.state.transport.playing
    expected = 2.0 * 115.0 / 60.0 * 96
    assert w.project.ticks == pytest.approx(expected, rel=0.1)
    assert w.state.transport.ticks == pytest.approx(w.project.ticks, abs=15)
    assert w.state.transport.bar_beat_tick()[0] == 2          # un peu plus de 4 temps jouées : mesure 2
    w.link.play_pause()                           # pause : la position reste
    w.run(0.6)
    assert not w.project.playing and not w.state.transport.playing and w.state.transport.ticks > 100
    w.link.stop_playback()                        # arrêt : retour au début
    w.run(0.6)
    assert w.project.ticks == 0 and w.state.transport.ticks == 0


def test_record_song_mode_and_metronome_follow_fl_studio(world):
    w = world().connect()
    w.link.record()
    w.link.toggle_song_mode()
    w.link.toggle_metronome()
    w.run(0.8)
    assert (w.project.recording, w.project.song_mode, w.project.metronome) == (True, True, True)
    t = w.state.transport
    assert (t.recording, t.song_mode, t.metronome) == (True, True, True)
    w.project.recording = w.project.metronome = False                   # changé dans FL Studio même
    w.run(0.8)
    assert (t.recording, t.song_mode, t.metronome) == (False, True, False)


def test_tempo_is_written_once_per_turn_and_clamped(world):
    w = world().connect()
    for bpm in (100, 101, 102, 130.25):
        w.link.set_tempo(bpm)
    w.run(0.1)
    assert w.project.calls_named("setTempo") == [(130250,)]             # seule la dernière valeur part
    assert w.project.tempo == pytest.approx(130.25)
    w.run(1.5)
    assert w.state.transport.tempo == pytest.approx(130.25)
    w.link.set_tempo(2)
    w.run(0.1)
    assert w.project.tempo == pytest.approx(10.0)
    w.link.set_tempo(5000)
    w.run(0.1)
    assert w.project.tempo == pytest.approx(999.0)


def test_tempo_changed_in_fl_studio_is_shown(world):
    w = world().connect()
    w.project.tempo = 87.5
    w.run(0.8)
    assert w.state.transport.tempo == pytest.approx(87.5)


def test_seek_and_rewind(world):
    w = world().connect()
    w.link.seek(8.0)                              # 8 noires = 768 ticks (96 par noire)
    w.run(0.1)
    assert w.project.ticks == 768
    assert w.state.transport.position_text() == "003:01:00"
    w.run(1.0)
    assert w.state.transport.ticks == 768
    w.link.rewind()
    w.run(0.2)
    assert w.project.ticks == 0 and w.state.transport.ticks == 0


def test_a_different_ppq_and_time_signature_are_read(world):
    w = world().connect()
    w.project.ppq, w.project.beats_per_bar = 960, 3
    w.project.ticks = 960 * 3 * 2 + 960               # mesure 3, temps 2
    w.run(0.8)
    t = w.state.transport
    assert (t.ppq, t.beats_per_bar) == (960, 3) and t.bar_beat_tick() == (3, 2, 0)


# --------------------------------------------------------------------------- #
# Channel rack et pas du séquenceur
# --------------------------------------------------------------------------- #
def test_toggling_a_step_changes_the_project_without_flicker(world):
    w = world().connect()
    w.link.toggle_step(1, 1)                      # caisse claire, pas 2 : éteint → allumé
    assert w.state.channel(1).steps[1] is True    # visible tout de suite
    w.run(0.05)
    assert w.project.channels[1].steps[1] == {1, 4, 12}
    assert w.project.undo == ["VoiceConnector : pas"]
    assert w.project.calls_named("setGridBit") == [(1, 1, True, True)]
    w.run(1.5)                                    # FL Studio relit : toujours allumé
    assert w.state.channel(1).steps[1] is True
    w.link.toggle_step(1, 1)
    w.run(1.5)
    assert w.project.channels[1].steps[1] == {4, 12} and w.on_steps(1) == [4, 12]


def test_setting_a_step_to_its_current_value_sends_nothing(world):
    w = world().connect()
    w.link.toggle_step(0, 0, True)                # déjà allumé
    w.link.toggle_step(0, 1, False)               # déjà éteint
    w.link.toggle_step(0, 99)                     # hors de la grille
    w.link.toggle_step(77, 0)                     # canal inconnu
    w.run(0.2)
    assert w.project.calls == []


def test_several_quick_edits_are_all_kept(world):
    w = world().connect()
    for step in (1, 2, 3, 5):
        w.link.toggle_step(1, step)
    w.run(2.0)
    assert w.project.channels[1].steps[1] == {1, 2, 3, 4, 5, 12}
    assert w.on_steps(1) == [1, 2, 3, 4, 5, 12]


def test_steps_of_another_pattern_or_length_arriving_late_are_ignored(world):
    w = world().connect()
    other_pattern = sp.steps_data(0, 2, [True] * 16)
    w.rig.outbox.append(other_pattern)             # les pas du pattern 2, alors que le pattern 1 est affiché
    w.run(0.02)
    assert w.on_steps(0) == [0, 4, 8, 12]
    w.link.set_step_count(32)
    w.run(1.0)
    assert len(w.state.channel(0).steps) == 32 and w.on_steps(0) == [0, 4, 8, 12]
    w.rig.outbox.append(sp.steps_data(0, 1, [True] * 16))      # une ligne de 16 pas calculée avant le changement
    w.run(0.02)
    assert w.on_steps(0) == [0, 4, 8, 12]


def test_if_fl_studio_refuses_a_step_the_screen_goes_back_to_the_truth(world, monkeypatch):
    import sys

    w = world().connect()
    monkeypatch.setattr(sys.modules["channels"], "setGridBit", lambda *args: None)      # FL Studio ignore l'ordre
    w.link.toggle_step(1, 1)
    assert w.state.channel(1).steps[1] is True
    w.run(2.0)
    assert w.state.channel(1).steps[1] is False and w.on_steps(1) == [4, 12]


def test_if_fl_studio_refuses_a_fader_move_the_screen_goes_back_to_the_truth(world, monkeypatch):
    import sys

    w = world().connect()
    monkeypatch.setattr(sys.modules["channels"], "setChannelVolume", lambda *args: None)
    w.link.set_channel_volume(2, 0.1)
    assert w.state.channel(2).volume == pytest.approx(0.1)
    w.run(2.0)
    assert w.state.channel(2).volume == pytest.approx(0.78125, abs=1e-3)


def test_after_an_edit_the_link_asks_fl_studio_for_the_real_state_of_that_part_only(world):
    w = world().connect()
    assert w.cable.refreshes() == []
    w.link.set_channel_mute(1, True)
    w.run(0.5)
    assert w.cable.refreshes() == []                  # la garde n'est pas passée
    w.run(0.6)
    assert w.cable.refreshes() == [sp.SCOPE_RACK]
    w.link.toggle_step(0, 1)
    w.link.set_track_mute(2, True)
    w.run(1.5)
    assert w.cable.refreshes() == [sp.SCOPE_RACK, sp.SCOPE_PLAYLIST, sp.SCOPE_STEPS]       # chacune à son heure


def test_a_burst_of_edits_is_verified_once_after_the_last_one(world):
    w = world().connect()
    for volume in (0.1, 0.2, 0.3, 0.4, 0.5):
        w.link.set_channel_volume(0, volume)
        w.run(0.2)
    w.run(1.5)
    assert w.cable.refreshes() == [sp.SCOPE_RACK]
    assert w.state.channel(0).volume == pytest.approx(0.5, abs=1e-3)


def test_everything_is_asked_again_every_minute_in_case_a_message_was_lost(world):
    w = world().connect()
    w.run(59.0)
    assert w.cable.refreshes() == []
    w.run(2.0)
    assert w.cable.refreshes() == [sp.SCOPE_ALL]
    w.project.channels[1].steps[1].add(7)             # un message « pas » s'est perdu : la relecture le rattrape
    w.rig.script._sent.pop(("steps", 1), None)
    w.run(60.0)
    assert w.cable.refreshes() == [sp.SCOPE_ALL, sp.SCOPE_ALL]
    assert 7 in [i for i, on in enumerate(w.state.channel(1).steps) if on]


def test_steps_changed_in_fl_studio_are_shown(world):
    w = world().connect()
    w.project.channels[1].steps[1].add(8)
    w.run(1.0)
    assert w.on_steps(1) == [4, 8, 12]
    w.project.channels[1].steps[1].clear()
    w.run(1.0)
    assert w.on_steps(1) == []


def test_channel_mixing_goes_to_fl_studio_and_back(world):
    w = world().connect()
    w.link.set_channel_volume(0, 0.5)
    w.link.set_channel_pan(0, -0.5)
    w.link.set_channel_mute(1, True)
    w.link.set_channel_solo(2, True)
    w.run(0.1)
    kick, snare, hat = w.project.channels[:3]
    assert kick.volume == pytest.approx(0.5, abs=1e-3) and kick.pan == pytest.approx(-0.5, abs=1e-3)
    assert snare.muted and hat.solo
    assert w.project.calls_named("setChannelVolume")[-1][-1] is True      # indices globaux (API 33 et plus)
    w.link.set_channel_mute(1, True)              # déjà muet : pas de nouvelle bascule
    w.link.set_channel_solo(2, True)
    w.run(0.1)
    assert len(w.project.calls_named("muteChannel")) == 1 and len(w.project.calls_named("soloChannel")) == 1
    w.run(1.5)
    assert w.state.channel(0).volume == pytest.approx(0.5, abs=1e-3)
    assert w.state.channel(1).muted and w.state.channel(2).solo
    w.link.set_channel_mute(1, False)
    w.run(1.0)
    assert not snare.muted and not w.state.channel(1).muted


def test_a_fader_moved_in_fl_studio_is_shown(world):
    w = world().connect()
    w.project.channels[2].volume = 0.4
    w.project.channels[2].pan = 0.25
    w.project.channels[3].muted = True
    w.run(1.0)
    assert w.state.channel(2).volume == pytest.approx(0.4, abs=1e-3)
    assert w.state.channel(2).pan == pytest.approx(0.25, abs=1e-3)
    assert w.state.channel(3).muted


def test_a_late_value_from_fl_studio_does_not_undo_a_fresh_edit(world):
    w = world().connect()
    w.link.set_channel_volume(2, 0.9)
    w.run(0.04)
    late = sp.channel(2, "Hi-Hat", 0x40C060, 0.5, 0.0, False, False, False, kind=0)
    w.rig.outbox.append(late)                     # une valeur ancienne arrive après l'ordre
    w.run(0.04)
    assert w.state.channel(2).volume == pytest.approx(0.9, abs=1e-3)
    w.run(0.6)                                    # la garde est passée : FL Studio redevient la référence
    w.rig.outbox.append(late)
    w.run(0.04)
    assert w.state.channel(2).volume == pytest.approx(0.5, abs=1e-3)


def test_selecting_a_channel(world):
    w = world().connect()
    w.link.select_channel(2)
    assert [c.selected for c in w.state.channels] == [False, False, True, False]
    w.run(0.8)
    assert w.project.calls_named("selectOneChannel") == [(2, True)]
    assert [c.selected for c in w.project.channels] == [False, False, True, False]
    assert [c.selected for c in w.state.channels] == [False, False, True, False]
    w.link.select_channel(55)                     # inconnu : sans effet
    w.run(0.1)
    assert len(w.project.calls_named("selectOneChannel")) == 1


def test_a_renamed_or_recoloured_channel_is_updated(world):
    w = world().connect()
    w.project.channels[0].name = "Kick 909"
    w.project.channels[0].color = 0x00FF00
    w.run(0.5)
    assert w.state.channel(0).name == "Kick 909" and w.state.channel(0).color == fl_color_to_hex(0x00FF00)


def test_fl_studio_can_flag_a_changed_channel_to_get_it_read_at_once(world):
    w = world()
    w.project.channels = [FakeFlChannel(f"Canal {i + 1}") for i in range(40)]
    w.connect()
    w.run(1.0)
    w.project.channels[30].name = "Voix"
    w.run(0.04)
    assert w.state.channel(30).name == "Canal 31"          # le tour de rôle n'y est pas encore passé
    w.project.channels[31].name = "Choeurs"
    w.rig.script.OnDirtyChannel(31)                        # FL Studio prévient : relecture immédiate du rack
    w.run(0.04)
    assert w.state.channel(31).name == "Choeurs" and w.state.channel(30).name == "Voix"


def test_channels_added_and_removed_in_fl_studio(world):
    w = world().connect()
    w.project.channels.append(FakeFlChannel("Cloche", 0x123456))
    w.run(1.0)
    assert [c.name for c in w.state.channels][-1] == "Cloche" and len(w.state.channels) == 5
    del w.project.channels[1:3]
    w.run(1.0)
    assert [c.name for c in w.state.channels] == ["Kick", "Basse « sub »", "Cloche"]


def test_channel_activity_is_shown(world):
    w = world().connect()
    w.project.channels[0].activity = 1.0
    w.run(0.4)
    assert w.state.channel(0).level == pytest.approx(1.0)
    w.project.channels[0].activity = 0.0
    w.run(0.4)
    assert w.state.channel(0).level == 0.0
    w.link.play_pause()                           # en lecture, l'activité est relue plus souvent
    w.run(0.2)
    w.project.channels[2].activity = 0.5
    w.run(0.12)
    assert w.state.channel(2).level == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------------------- #
# Nombre de pas et patterns
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("length,expected", [(1, 16), (16, 16), (17, 32), (32, 32), (33, 48), (64, 64), (200, 64)])
def test_steps_for_length(length, expected):
    assert steps_for_length(length) == expected


def test_selecting_a_pattern_shows_its_own_steps_and_length(world):
    w = world().connect()
    w.project.channels[0].steps[2] = {1, 3, 5, 17, 31}
    w.link.select_pattern(2)
    assert w.state.pattern == 2 and w.state.step_count == 32           # « Refrain » dure 32 pas
    w.run(1.0)
    assert w.project.pattern == 2 and w.project.calls_named("jumpToPattern") == [(2,)]
    assert w.on_steps(0) == [1, 3, 5, 17, 31] and len(w.state.channel(0).steps) == 32
    w.link.select_pattern(1)
    w.run(1.0)
    assert w.on_steps(0) == [0, 4, 8, 12] and w.state.step_count == 16
    w.link.select_pattern(77)                     # n'existe pas dans la liste : sans effet
    w.run(0.2)
    assert len(w.project.calls_named("jumpToPattern")) == 2


def test_a_pattern_chosen_in_fl_studio_updates_the_grid_without_stale_rows(world):
    w = world().connect()
    w.project.channels[0].steps[2] = {1, 3, 5}
    w.project.pattern = 2                         # le clic se fait dans FL Studio
    w.run(1.0)
    assert w.state.pattern == 2 and w.state.step_count == 32
    assert w.on_steps(0) == [1, 3, 5] and len(w.state.channel(0).steps) == 32
    assert w.on_steps(1) == []                    # la caisse claire n'a rien dans ce pattern


def test_the_script_announces_a_pattern_before_its_steps(world):
    w = world().connect()
    w.project.channels[0].steps[2] = {2}
    w.project.pattern = 2
    w.rig.receive()
    w.rig.idle(STEP)
    events = [sp.decode(fl_sync.parse(raw)) for raw in w.rig.receive()]
    kinds = [type(e).__name__ for e in events if e is not None]
    assert "PatternList" in kinds and "StepsData" in kinds
    assert kinds.index("PatternList") < kinds.index("StepsData")


def test_a_fixed_step_count_is_kept_when_the_pattern_changes(world):
    w = world(steps=16).connect()
    assert w.state.step_count == 16 and w.link.steps_choice == 16
    w.link.select_pattern(2)
    w.run(1.0)
    assert w.state.step_count == 16 and len(w.state.channel(0).steps) == 16


def test_choosing_the_number_of_steps(world):
    w = world().connect()
    w.link.set_step_count(48)
    w.run(1.0)
    assert w.state.step_count == 48 and all(len(c.steps) == 48 for c in w.state.channels)
    assert w.on_steps(0) == [0, 4, 8, 12]
    w.project.channels[0].steps[1].add(40)
    w.run(1.0)
    assert w.on_steps(0) == [0, 4, 8, 12, 40]
    w.link.set_step_count(0)                      # automatique : suit la longueur du pattern (16)
    w.run(1.0)
    assert w.state.step_count == 16 and w.link.steps_choice == 0
    w.link.set_step_count(500)                    # trop grand : ramené à la limite
    assert w.state.step_count == 64


def test_new_patterns_and_names_are_shown(world):
    w = world().connect()
    w.project.patterns[3] = FakeFlPattern("Intro", 0x808080, beats=2)
    w.rig.script.OnRefresh(1024)                  # HW_Dirty_Patterns : repris sans attendre le tour de rôle
    w.run(0.06)
    assert [p.index for p in w.state.patterns] == [1, 2, 3]
    assert w.state.patterns[2].name == "Intro" and w.state.patterns[2].length_steps == 8
    w.project.patterns[3].name = "Pont"
    w.run(1.0)
    assert w.state.patterns[2].name == "Pont"
    del w.project.patterns[3]
    w.run(1.0)
    assert [p.index for p in w.state.patterns] == [1, 2]


def test_the_selected_pattern_is_listed_even_when_still_empty(world):
    w = world().connect()
    w.project.pattern = 5                         # jamais modifié : « par défaut » pour FL Studio
    w.run(1.0)
    assert w.state.pattern == 5 and [p.index for p in w.state.patterns] == [1, 2, 5]


def test_loading_another_project_resends_everything(world):
    w = world().connect()
    w.project.channels = [FakeFlChannel("Piano", 0x808000)]
    w.project.title = "Autre - FL Studio 21"
    w.project.patterns = {1: FakeFlPattern("Intro")}
    w.rig.script.OnProjectLoad(100)               # PL_LoadOk
    w.run(0.3)
    assert [c.name for c in w.state.channels] == ["Piano"] and w.state.project == "Autre"
    assert [p.name for p in w.state.patterns] == ["Intro"]


# --------------------------------------------------------------------------- #
# Playlist
# --------------------------------------------------------------------------- #
def test_playlist_tracks_mute_and_solo(world):
    w = world().connect()
    w.link.set_track_mute(2, True)
    w.link.set_track_solo(1, True)
    w.run(0.1)
    assert w.project.tracks[2].muted and w.project.tracks[1].solo
    assert w.state.track(2).muted and w.state.track(1).solo
    w.link.set_track_mute(2, True)                # déjà muette
    w.run(0.1)
    assert len(w.project.calls_named("muteTrack")) == 1
    w.run(1.0)
    assert w.state.track(2).muted
    w.link.set_track_mute(2, False)
    w.run(1.0)
    assert not w.project.tracks[2].muted and not w.state.track(2).muted
    w.link.set_track_mute(99, True)               # piste inconnue : sans effet
    w.run(0.1)
    assert len(w.project.calls_named("muteTrack")) == 2


def test_playlist_names_colours_and_count_follow_fl_studio(world):
    w = world().connect()
    w.project.tracks[12].name = "Voix"
    w.project.tracks[12].color = 0x0000FF
    w.project.tracks[1].muted = True              # coupée dans FL Studio même
    w.run(1.0)
    assert len(w.state.tracks) == 12 and w.state.track(12).name == "Voix"
    assert w.state.track(12).color == "#ff0000" and w.state.track(1).muted
    w.project.tracks[12].name = "Track 12"        # ramenée au nom par défaut : la liste raccourcit
    w.run(1.0)
    assert len(w.state.tracks) == 8


def test_playlist_activity_lights_up_while_playing(world):
    w = world().connect()
    w.link.play_pause()
    w.project.tracks[1].activity = 0.8
    w.run(0.4)
    assert w.state.track(1).active and w.state.track(1).level == pytest.approx(0.8, abs=0.01)
    assert not w.state.track(2).active
    w.project.tracks[1].activity = 0.0
    w.run(0.4)
    assert not w.state.track(1).active and w.state.track(1).level == 0.0


# --------------------------------------------------------------------------- #
# Anciennes versions de FL Studio, filtre de groupe, gros projets
# --------------------------------------------------------------------------- #
def test_the_channel_group_filter_does_not_hide_channels_with_the_recent_api(world):
    w = world()
    w.project.group = [2, 3]                      # le channel rack n'affiche qu'un groupe
    w.connect()
    assert [c.name for c in w.state.channels] == ["Kick", "Snare", "Hi-Hat", "Basse « sub »"]
    w.link.toggle_step(1, 1)                      # indice global : la caisse claire, pas le groupe visible
    w.run(0.2)
    assert w.project.channels[1].steps[1] == {1, 4, 12}


def test_an_old_api_works_with_the_visible_channels(world):
    w = world(api_version=30)
    w.project.group = [2, 3]
    w.connect()
    assert [c.name for c in w.state.channels] == ["Hi-Hat", "Basse « sub »"]
    w.link.toggle_step(0, 1)
    w.link.set_channel_volume(1, 0.25)
    w.link.set_channel_mute(1, True)
    w.link.select_channel(1)
    w.run(0.3)
    assert w.project.channels[2].steps[1] >= {1}
    assert w.project.channels[3].volume == pytest.approx(0.25, abs=1e-3) and w.project.channels[3].muted
    assert w.project.calls_named("setGridBit") == [(0, 1, True)]          # pas d'argument « indice global »
    assert w.project.calls_named("selectOneChannel") == [(1,)]


def test_an_api_without_channel_types_still_shows_the_rack(world):
    w = world(api_version=15).connect()
    assert len(w.state.channels) == 4 and {c.kind for c in w.state.channels} == {"sampler"}
    assert [p.index for p in w.state.patterns] == [1, 2]       # pas de isPatternDefault : repli sur patternCount


def test_a_big_rack_is_limited_and_the_work_per_idle_stays_small(world):
    w = world(channels=40)
    w.project.channels = [FakeFlChannel(f"Canal {i + 1}") for i in range(120)]
    w.connect()
    w.run(1.0)
    assert len(w.state.channels) == 40 and w.state.channels[-1].index == 39
    worst = 0
    for _ in range(150):
        before = w.project.api_calls
        w.rig.idle(STEP)
        worst = max(worst, w.project.api_calls - before)
        w.link.poll()
    assert worst < 400, worst                     # FL Studio ne doit jamais se figer à cause du Studio


def test_a_project_with_few_patterns_does_not_scan_the_whole_pattern_list_each_time(world):
    w = world().connect()
    before = w.project.api_calls
    w.run(2.0)
    per_second = (w.project.api_calls - before) / 2
    assert per_second < 2500, per_second          # (un balayage des 999 patterns toutes les 0,5 s en ajouterait 2000)


# --------------------------------------------------------------------------- #
# Ouverture
# --------------------------------------------------------------------------- #
def test_open_returns_a_started_link(world):
    w = world()
    link, reason = open_fl_studio_link(lambda: w.cable, {"studio": {"steps": 32, "channels": 10, "tracks": 12}})
    assert reason == "" and link is not None and link.started
    assert link.steps_choice == 32


def test_open_reads_odd_settings_without_failing(world):
    w = world()
    link, reason = open_fl_studio_link(lambda: w.cable, {"studio": {"steps": "abc", "channels": None,
                                                                    "tracks": "99999"}})
    assert reason == "" and link.steps_choice == 0
    link, _ = open_fl_studio_link(lambda: w.cable, {"studio": "pas un dictionnaire"})
    assert link is not None
    link, _ = open_fl_studio_link(lambda: w.cable, None)
    assert link is not None


def test_open_reports_why_it_cannot(world):
    def missing():
        raise TransportError("Port MIDI « VoiceConnector FL » introuvable (loopMIDI ?)")

    def broken():
        raise RuntimeError("boom")

    link, reason = open_fl_studio_link(missing, {})
    assert link is None and "loopMIDI" in reason
    link, reason = open_fl_studio_link(broken, {})
    assert link is None and "boom" in reason

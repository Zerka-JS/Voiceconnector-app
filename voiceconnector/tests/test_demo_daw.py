"""Le logiciel de musique simulé du Studio : transport, pas du séquenceur, patterns, playlist (sans Qt)."""

import pytest
from fl_rig import FakeClock

from core import demo_daw
from core.daw_model import (
    CAP_PLAYLIST,
    CAP_RACK,
    CAP_STEPS,
    CAP_TEMPO_WRITE,
    MAX_TEMPO,
    MIN_TEMPO,
)
from core.demo_daw import DemoDaw


def make(**options) -> tuple[DemoDaw, FakeClock]:
    clock = FakeClock()
    daw = DemoDaw(clock=clock, **options)
    daw.start()
    return daw, clock


def run(daw: DemoDaw, clock: FakeClock, seconds: float, step: float = 0.02) -> None:
    for _ in range(int(round(seconds / step))):
        clock.advance(step)
        daw.poll()


def lit(daw: DemoDaw, channel: int) -> list[int]:
    return [i for i, on in enumerate(daw.state.channel(channel).steps) if on]


# --------------------------------------------------------------------------- #
# Les données de la démonstration
# --------------------------------------------------------------------------- #
def test_the_demo_patterns_are_well_formed():
    names = {name for name, *_ in demo_daw.CHANNELS}
    for number, _, _, length, motifs in demo_daw.PATTERNS:
        assert length in (16, 32)
        for channel, motif in motifs.items():
            assert channel in names, (number, channel)
            assert len(motif) == length and set(motif) <= {"x", "."}, (number, channel)
    assert {number for number, *_ in demo_daw.PATTERNS} == {1, 2, 3}
    assert set(demo_daw.ARRANGEMENT) == set(range(len(demo_daw.TRACKS)))
    assert all(0 <= start < end <= demo_daw.SONG_BARS for clips in demo_daw.ARRANGEMENT.values()
               for start, end in clips)


# --------------------------------------------------------------------------- #
# État de départ
# --------------------------------------------------------------------------- #
def test_the_demo_starts_connected_with_a_full_project():
    daw, _ = make()
    s = daw.state
    assert s.connected and s.daw == "Démonstration" and "aucun logiciel" in s.message
    assert [c.name for c in s.channels] == ["Kick", "Caisse claire", "Clap", "Charley", "Basse", "Piano"]
    assert [(p.index, p.name, p.length_steps) for p in s.patterns] == [(1, "Batterie", 16), (2, "Basse", 16),
                                                                        (3, "Mélodie", 32)]
    assert s.pattern == 1 and s.step_count == 16 and len(s.tracks) == 8
    assert [t.name for t in s.tracks[:3]] == ["Pads", "Automation", "Basse"]
    assert s.transport.tempo == pytest.approx(118.0) and not s.transport.playing
    assert s.can(CAP_RACK) and s.can(CAP_STEPS) and s.can(CAP_PLAYLIST) and s.can(CAP_TEMPO_WRITE)
    assert lit(daw, 0) == [0, 4, 8, 12] and lit(daw, 3)[:3] == [0, 2, 4]
    assert s.channel(0).selected and not s.channel(1).selected


def test_a_demo_that_is_not_started_does_not_move():
    clock = FakeClock()
    daw = DemoDaw(clock=clock)
    daw.play_pause()
    clock.advance(1.0)
    assert daw.poll() is False and daw.state.transport.ticks == 0


def test_stopping_the_link_disconnects_it():
    daw, _ = make()
    daw.stop()
    assert not daw.state.connected
    daw.close()


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def test_playback_advances_with_the_tempo():
    daw, clock = make(tempo=120.0)
    daw.play_pause()
    run(daw, clock, 1.0)
    assert daw.state.transport.ticks == pytest.approx(2 * 96, abs=2)           # 120 bpm : 2 noires par seconde
    daw.play_pause()                                                              # pause : la position reste
    kept = daw.state.transport.ticks
    run(daw, clock, 1.0)
    assert daw.state.transport.ticks == kept and not daw.state.transport.playing


def test_poll_tells_when_something_moved():
    daw, clock = make()
    clock.advance(0.1)
    assert daw.poll() is False                       # rien ne bouge à l'arrêt
    daw.play_pause()
    clock.advance(0.1)
    assert daw.poll() is True


def test_the_pattern_loops_and_the_song_runs_longer():
    daw, clock = make(tempo=120.0)
    daw.play_pause()
    run(daw, clock, 3.0)                              # 6 noires : le pattern (4 noires) a rebouclé
    assert 0 <= daw.state.transport.ticks < 4 * 96
    daw.stop_playback()
    daw.toggle_song_mode()
    daw.play_pause()
    run(daw, clock, 3.0)
    assert daw.state.transport.ticks == pytest.approx(6 * 96, abs=3)          # en mode morceau, la lecture continue
    daw.seek(63.9)
    run(daw, clock, 0.5)                              # fin de l'arrangement : il recommence
    assert daw.state.transport.ticks < 96 * 8


def test_stop_returns_to_the_start_and_rewind_too():
    daw, clock = make()
    daw.play_pause()
    run(daw, clock, 1.0)
    daw.stop_playback()
    assert not daw.state.transport.playing and daw.state.transport.ticks == 0
    daw.seek(2.5)
    assert daw.state.transport.ticks == 240 and daw.state.transport.position_text() == "001:03:48"
    daw.rewind()
    assert daw.state.transport.ticks == 0


def test_seek_wraps_inside_the_loop_and_never_goes_negative():
    daw, _ = make()
    daw.seek(-5)
    assert daw.state.transport.ticks == 0
    daw.seek(5.0)                                      # le pattern dure 4 noires
    assert daw.state.transport.ticks == 96


def test_a_long_freeze_does_not_make_the_playhead_jump():
    daw, clock = make(tempo=120.0)
    daw.play_pause()
    clock.advance(30.0)                                # l'ordinateur s'est endormi
    daw.poll()
    assert daw.state.transport.ticks <= 96 * 1.1 + 2   # une demi-seconde au plus


def test_record_song_mode_and_metronome_toggle():
    daw, _ = make()
    t = daw.state.transport
    daw.record()
    daw.toggle_song_mode()
    daw.toggle_metronome()
    assert (t.recording, t.song_mode, t.metronome) == (True, True, True)
    daw.record()
    daw.toggle_song_mode()
    daw.toggle_metronome()
    assert (t.recording, t.song_mode, t.metronome) == (False, False, False)


def test_tempo_is_clamped_and_rounded():
    daw, _ = make()
    daw.set_tempo(2)
    assert daw.state.transport.tempo == MIN_TEMPO
    daw.set_tempo(5000)
    assert daw.state.transport.tempo == MAX_TEMPO
    daw.set_tempo(87.12349)
    assert daw.state.transport.tempo == 87.123


# --------------------------------------------------------------------------- #
# Channel rack
# --------------------------------------------------------------------------- #
def test_toggling_steps_changes_only_the_current_pattern():
    daw, _ = make()
    daw.toggle_step(1, 1)
    assert lit(daw, 1) == [1, 4, 12]
    daw.toggle_step(1, 1)
    assert lit(daw, 1) == [4, 12]
    daw.toggle_step(1, 2, True)
    daw.toggle_step(1, 2, True)                         # déjà allumé : reste allumé
    daw.toggle_step(0, 0, False)
    assert lit(daw, 1) == [2, 4, 12] and lit(daw, 0) == [4, 8, 12]
    daw.select_pattern(2)
    assert lit(daw, 1) == [] and lit(daw, 0) == [0, 4, 8, 12]
    daw.select_pattern(1)
    assert lit(daw, 1) == [2, 4, 12]                    # le pattern 1 a gardé ses modifications


def test_steps_out_of_range_or_unknown_channels_are_ignored():
    daw, _ = make()
    daw.toggle_step(1, 99)
    daw.toggle_step(1, -1)
    daw.toggle_step(77, 0)
    assert lit(daw, 1) == [4, 12] and len(daw.state.channels) == 6


def test_the_number_of_steps_follows_the_pattern_or_the_choice():
    daw, _ = make()
    daw.select_pattern(3)                               # « Mélodie » dure 32 pas
    assert daw.state.step_count == 32 and all(len(c.steps) == 32 for c in daw.state.channels)
    assert lit(daw, 5)[:4] == [0, 6, 8, 12]
    daw.set_step_count(48)
    assert daw.state.step_count == 48 and daw.steps_choice == 48
    daw.select_pattern(1)
    assert daw.state.step_count == 48 and lit(daw, 0) == [0, 4, 8, 12]
    daw.toggle_step(0, 40)
    assert lit(daw, 0) == [0, 4, 8, 12, 40]
    daw.set_step_count(16)
    assert lit(daw, 0) == [0, 4, 8, 12]
    daw.set_step_count(0)                               # automatique
    assert daw.steps_choice == 0 and daw.state.step_count == 16
    daw.set_step_count(500)
    assert daw.state.step_count == 64


def test_a_fixed_choice_at_creation():
    daw, _ = make(steps=32)
    assert daw.state.step_count == 32 and daw.steps_choice == 32


def test_unknown_patterns_are_ignored():
    daw, _ = make()
    daw.select_pattern(42)
    assert daw.state.pattern == 1


def test_channel_mixing_is_clamped():
    daw, _ = make()
    daw.set_channel_volume(0, 1.7)
    daw.set_channel_pan(0, -3)
    assert daw.state.channel(0).volume == 1.0 and daw.state.channel(0).pan == -1.0
    daw.set_channel_volume(0, -1)
    daw.set_channel_pan(0, 0.3)
    assert daw.state.channel(0).volume == 0.0 and daw.state.channel(0).pan == pytest.approx(0.3)
    daw.set_channel_mute(1, True)
    daw.set_channel_solo(2, True)
    assert daw.state.channel(1).muted and daw.state.channel(2).solo
    daw.set_channel_mute(1, False)
    assert not daw.state.channel(1).muted
    daw.set_channel_volume(99, 0.5)                     # inconnu : sans effet


def test_selecting_a_channel_is_exclusive():
    daw, _ = make()
    daw.select_channel(3)
    assert [c.selected for c in daw.state.channels] == [False, False, False, True, False, False]
    daw.select_channel(99)
    assert daw.state.selected_channel().index == 3


def test_channels_light_up_when_their_step_plays():
    daw, clock = make(tempo=120.0)
    daw.play_pause()
    assert daw.state.channel(0).level == 1.0              # le pas 0 (kick) sonne dès le départ
    run(daw, clock, 0.3)
    assert daw.state.channel(0).level == 0.0              # le kick s'est éteint (aucun pas 1 ou 2)
    daw.stop_playback()
    daw.seek(0.9)                                         # juste avant le pas 4 : kick, caisse claire, clap, charley
    daw.play_pause()
    assert daw.state.channel(0).level == 0.0              # le pas 3 est vide : rien ne sonne au départ
    run(daw, clock, 0.12)
    assert daw.state.channel(0).level > 0.0 and daw.state.channel(1).level > 0.0
    assert daw.state.channel(4).level == 0.0              # la basse ne joue pas dans ce pattern


def test_notes_are_not_lit_while_stopped():
    daw, clock = make()
    run(daw, clock, 1.0)
    assert all(c.level == 0.0 for c in daw.state.channels)


# --------------------------------------------------------------------------- #
# Playlist
# --------------------------------------------------------------------------- #
def test_the_arrangement_lights_the_playlist_in_song_mode_only():
    daw, clock = make(tempo=120.0)
    daw.play_pause()
    run(daw, clock, 0.5)
    assert not any(t.active for t in daw.state.tracks)      # mode pattern : la playlist ne joue pas
    daw.stop_playback()
    daw.toggle_song_mode()
    daw.play_pause()
    run(daw, clock, 0.5)                                    # mesure 1 : Pads, Accords, Ambiance
    active = [t.name for t in daw.state.tracks if t.active]
    assert active == ["Pads", "Accords", "Ambiance"]
    assert all(0.3 < t.level <= 0.9 for t in daw.state.tracks if t.active)
    assert all(t.level == 0.0 for t in daw.state.tracks if not t.active)
    daw.seek(4 * 3 + 0.5)                                   # mesure 4 : Basse (dès la mesure 3) et Automation arrivent
    run(daw, clock, 0.1)
    assert {t.name for t in daw.state.tracks if t.active} >= {"Pads", "Basse", "Accords", "Ambiance"}
    daw.play_pause()                                        # pause : plus rien ne s'allume
    run(daw, clock, 0.1)
    assert not any(t.active for t in daw.state.tracks)


def test_track_mute_and_solo():
    daw, _ = make()
    daw.set_track_mute(2, True)
    daw.set_track_solo(3, True)
    assert daw.state.track(2).muted and daw.state.track(3).solo
    daw.set_track_mute(2, False)
    assert not daw.state.track(2).muted
    daw.set_track_mute(99, True)                            # inconnue : sans effet
    assert len(daw.state.tracks) == 8


def test_two_demos_with_the_same_clock_behave_the_same():
    results = []
    for _ in range(2):
        daw, clock = make(tempo=133.0)
        daw.toggle_song_mode()
        daw.play_pause()
        run(daw, clock, 2.0)
        results.append((daw.state.transport.ticks, [(t.active, round(t.level, 6)) for t in daw.state.tracks]))
    assert results[0] == results[1]

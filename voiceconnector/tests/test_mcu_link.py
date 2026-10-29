"""Liaison Mackie Control : VoiceConnector face à un logiciel de musique simulé qui parle MCU
(sans Qt, sans MIDI)."""

import pytest
from mcu_rig import McuRig

from core import mcu_proto as mcu
from core.daw_model import (CAP_BANK, CAP_METERS, CAP_PLAYLIST, CAP_RACK, CAP_SEEK, CAP_STEPS, CAP_TEMPO,
                            CAP_TRACK_ARM, CAP_TRACK_MIX, CAP_TRANSPORT)
from core.mcu_link import GUARD_S, PAN_TRIES, McuLink


@pytest.fixture
def rig():
    rig = McuRig(McuLink, daw="Cubase")
    rig.link.start()
    rig.run(0.3)
    return rig


def names(link):
    return [t.name for t in link.state.tracks]


# --------------------------------------------------------------------------- #
# Connexion
# --------------------------------------------------------------------------- #
def test_the_link_introduces_itself_and_waits():
    rig = McuRig(McuLink, daw="Cubase")
    assert not rig.link.state.connected and "Cubase" in rig.link.state.message
    rig.link.start()
    assert rig.daw.link.sent == [mcu.host_query()]                    # « me voici », rien d'autre
    assert not rig.link.state.connected


def test_the_handshake_connects_and_the_whole_state_arrives(rig):
    state = rig.link.state
    assert state.connected and state.message == "" and rig.daw.confirmed and rig.daw.queries == 1
    assert names(rig.link) == [f"Trk {i}" for i in range(1, 9)]
    assert all(t.volume == pytest.approx(0.5, abs=1e-3) for t in state.tracks)
    assert state.master.volume == pytest.approx(0.8, abs=1e-3)
    assert state.tracks[0].selected and not state.tracks[1].selected
    assert state.transport.time_text == "001.01.01.000" and state.transport.position_text() == "001.01.01.000"


def test_the_capabilities_say_what_mackie_control_can_do(rig):
    state = rig.link.state
    for capability in (CAP_TRANSPORT, CAP_PLAYLIST, CAP_TRACK_ARM, CAP_TRACK_MIX, CAP_BANK, CAP_METERS):
        assert state.can(capability)
    for capability in (CAP_RACK, CAP_STEPS, CAP_TEMPO, CAP_SEEK):
        assert not state.can(capability)
    assert state.song_mode_label == "Boucle" and state.daw == "Cubase"


def test_any_valid_message_connects_without_a_handshake():
    rig = McuRig(McuLink)
    rig.link.start()
    rig.daw.link.sent.clear()
    rig.daw.push(mcu.led(mcu.PLAY, mcu.LED_ON))
    assert rig.link.poll() is True
    assert rig.link.state.connected and rig.link.state.transport.playing


def test_garbage_does_not_connect():
    rig = McuRig(McuLink)
    rig.link.start()
    rig.daw.push(b"\xf0\x7d\x01\xf7", b"\xb0\x20\x01", b"")
    assert rig.link.poll() is False and not rig.link.state.connected


def test_a_device_query_makes_the_surface_present_itself_again(rig):
    before = rig.daw.queries
    rig.daw.push(mcu.device_query())
    rig.run(0.2)
    assert rig.daw.queries == before + 1


def test_going_offline_disconnects(rig):
    rig.daw.push(mcu.go_offline())
    assert rig.link.poll() is True
    assert not rig.link.state.connected and "coupé" in rig.link.state.message
    rig.daw.push_everything()
    rig.run(0.1)
    assert rig.link.state.connected                                  # il revient dès qu'il reparle


def test_closed_ports_are_noticed(rig):
    rig.daw.link.is_open = False
    assert rig.link.poll() is True and not rig.link.state.connected
    assert rig.link.poll() is False


def test_a_reading_error_is_swallowed(rig):
    def boom():
        raise OSError("lecture impossible")

    rig.daw.link.receive = boom
    assert rig.link.poll() is False


def test_poll_without_start_does_nothing():
    rig = McuRig(McuLink)
    rig.daw.push(mcu.led(mcu.PLAY, mcu.LED_ON))
    assert rig.link.poll() is False and not rig.link.state.connected


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def test_play_then_stop_follow_the_daw(rig):
    rig.link.play_pause()
    rig.run(0.2)
    assert rig.daw.playing and rig.link.state.transport.playing
    rig.link.play_pause()                                            # déjà en lecture : arrêt
    rig.run(0.2)
    assert not rig.daw.playing and not rig.link.state.transport.playing
    assert rig.daw.pressed == [mcu.PLAY, mcu.STOP]


def test_every_press_is_released(rig):
    rig.link.record()
    rig.link.toggle_metronome()
    rig.link.toggle_song_mode()
    rig.run(0.3)
    assert rig.daw.pressed == [mcu.RECORD, mcu.CLICK, mcu.CYCLE]
    assert rig.daw.released == [mcu.RECORD, mcu.CLICK, mcu.CYCLE]
    state = rig.link.state.transport
    assert state.recording and state.metronome and state.song_mode


def test_the_screen_only_changes_when_the_daw_confirms(rig):
    rig.link.play_pause()
    assert not rig.link.state.transport.playing                      # rien n'est affiché avant le voyant du logiciel
    rig.run(0.2)
    assert rig.link.state.transport.playing


def test_rewind_presses_the_rewind_button(rig):
    rig.link.rewind()
    rig.run(0.2)
    assert rig.daw.pressed == [mcu.REWIND]


def test_unsupported_orders_are_harmless(rig):
    rig.daw.link.sent.clear()
    rig.link.set_tempo(140.0)
    rig.link.seek(12.0)
    rig.link.toggle_step(0, 1)
    rig.link.set_channel_volume(0, 0.2)
    rig.link.select_pattern(2)
    rig.run(0.1)
    assert rig.daw.pressed == [] and not rig.daw.touched


def test_the_time_display_follows_the_daw(rig):
    rig.daw.push(*mcu.display("002.03.01.012"))
    rig.run(0.1)
    assert rig.link.state.transport.position_text() == "002.03.01.012"
    assert not rig.link.state.transport.known_position


# --------------------------------------------------------------------------- #
# Pistes
# --------------------------------------------------------------------------- #
def test_mute_solo_arm_and_select_toggle_only_when_needed(rig):
    rig.link.set_track_mute(2, True)
    rig.link.set_track_solo(3, True)
    rig.link.set_track_arm(1, True)
    rig.link.select_track(4)
    rig.run(0.3)
    tracks = rig.link.state.tracks
    assert tracks[2].muted and tracks[3].solo and tracks[1].armed and tracks[4].selected and not tracks[0].selected
    rig.daw.pressed.clear()
    rig.link.set_track_mute(2, True)                                 # déjà muet : pas d'appui
    rig.link.set_track_solo(3, True)
    rig.link.set_track_arm(1, True)
    rig.link.select_track(4)
    rig.run(0.2)
    assert rig.daw.pressed == []
    rig.link.set_track_mute(2, False)
    rig.run(0.2)
    assert not rig.link.state.tracks[2].muted and rig.daw.pressed == [mcu.MUTE + 2]


def test_orders_for_a_track_outside_the_bank_are_ignored(rig):
    rig.link.set_track_mute(12, True)
    rig.link.set_track_volume(40, 0.1)
    rig.link.set_track_pan(-1, 0.5)
    rig.run(0.2)
    assert rig.daw.pressed == [] and not rig.daw.touched


def test_a_fader_move_is_a_touch_a_move_and_a_release(rig):
    rig.link.set_track_volume(1, 0.9)
    assert not rig.daw.touched                                       # rien n'est parti avant que le logiciel lise
    rig.daw.step()
    assert rig.daw.touched == {1} and rig.daw.tracks[1]["fader"] == pytest.approx(0.9, abs=1e-3)
    rig.run(0.1)
    assert 1 in rig.daw.touched
    assert rig.link.state.tracks[1].volume == pytest.approx(0.9, abs=1e-3)       # affiché tout de suite
    rig.run(0.6)
    assert 1 not in rig.daw.touched                                  # le doigt se lève quand on lâche


def test_a_fast_drag_is_coalesced_and_ends_on_the_last_value(rig):
    rig.daw.link.sent.clear()
    for step in range(30):
        rig.link.set_track_volume(0, step / 30)
        rig.clock.advance(0.002)
        rig.link.poll()
    rig.run(0.2)
    assert rig.daw.tracks[0]["fader"] == pytest.approx(29 / 30, abs=1e-3)
    assert len(rig.daw.fader_moves) <= 6                             # 30 mouvements en 60 ms : quelques messages


def test_the_echo_of_our_own_write_does_not_move_the_fader_back(rig):
    rig.link.set_track_volume(0, 0.2)
    rig.daw.push(mcu.fader_position(0, 0.6))                        # l'écho d'une valeur plus ancienne
    rig.link.poll()
    assert rig.link.state.tracks[0].volume == pytest.approx(0.2, abs=1e-3)
    rig.clock.advance(GUARD_S + 0.1)
    rig.daw.push(mcu.fader_position(0, 0.6))                        # plus tard : le logiciel fait foi
    rig.link.poll()
    assert rig.link.state.tracks[0].volume == pytest.approx(0.6, abs=1e-3)


def test_a_fader_moved_in_the_daw_is_shown(rig):
    rig.daw.push(mcu.fader_position(3, 0.1), mcu.fader_position(mcu.MASTER, 0.3))
    assert rig.link.poll() is True
    assert rig.link.state.tracks[3].volume == pytest.approx(0.1, abs=1e-3)
    assert rig.link.state.master.volume == pytest.approx(0.3, abs=1e-3)
    assert rig.link.poll() is False


def test_the_master_fader_has_its_own_touch(rig):
    rig.link.set_master_volume(0.4)
    rig.run(0.1)
    assert rig.daw.master == pytest.approx(0.4, abs=1e-3) and mcu.MASTER in rig.daw.touched
    rig.run(0.6)
    assert not rig.daw.touched


def test_the_pan_is_reached_by_turning_the_pot(rig):
    assert rig.link.state.tracks[2].pan == 0.0
    rig.link.set_track_pan(2, 1.0)
    rig.run(1.0)
    assert rig.daw.tracks[2]["pan"] == mcu.RING_LEDS
    assert rig.link.state.tracks[2].pan == pytest.approx(1.0)
    rig.link.set_track_pan(2, -0.4)
    rig.run(1.0)
    assert rig.daw.tracks[2]["pan"] == mcu.pan_to_ring(-0.4)
    assert rig.link.state.tracks[2].pan == pytest.approx(mcu.ring_to_pan(mcu.pan_to_ring(-0.4)))


def test_turning_the_pot_gives_up_when_the_daw_does_not_follow(rig):
    rig.daw.vpot_works = False                                       # le potard ne fait rien dans ce logiciel
    rig.link.set_track_pan(1, 1.0)
    rig.run(4.0)
    assert 0 < len(rig.daw.vpots) <= PAN_TRIES and all(strip == 1 for strip, _ in rig.daw.vpots)
    assert not rig.link._pan_goal                                    # abandonné après quelques essais
    rig.daw.link.sent.clear()
    rig.run(1.0)
    assert len(rig.daw.vpots) <= PAN_TRIES                           # et plus rien ne part ensuite


def test_meters_show_the_levels(rig):
    rig.daw.push(mcu.meter(2, 0.5), mcu.meter(4, 1.0))
    rig.link.poll()
    assert rig.link.state.tracks[2].level == pytest.approx(0.5, abs=0.05)
    assert rig.link.state.tracks[4].level == 1.0
    rig.daw.push(mcu.meter(4, 0.0))
    rig.link.poll()
    assert rig.link.state.tracks[4].level == 0.0


# --------------------------------------------------------------------------- #
# Groupes de huit pistes
# --------------------------------------------------------------------------- #
def test_the_next_bank_shows_the_next_eight_tracks(rig):
    rig.link.bank(1)
    rig.run(0.3)
    assert names(rig.link) == [f"Trk {i}" for i in range(9, 17)]
    assert rig.link.state.track_offset == 8
    assert [t.index for t in rig.link.state.tracks] == list(range(8, 16))
    rig.link.set_track_mute(9, True)                                 # la piste 9 est la deuxième du groupe
    rig.run(0.2)
    assert rig.daw.tracks[9]["mute"] and rig.link.state.track(9).muted
    rig.link.bank(-1)
    rig.run(0.3)
    assert rig.link.state.track_offset == 0 and names(rig.link)[0] == "Trk 1"


def test_a_bank_the_daw_refuses_does_not_move_the_offset(rig):
    rig.daw.accept_banks = False
    rig.link.bank(1)
    rig.run(1.5)
    assert rig.link.state.track_offset == 0 and names(rig.link)[0] == "Trk 1"
    rig.link.bank(-1)                                                # déjà au début : le logiciel ne bouge pas
    rig.daw.accept_banks = True
    rig.run(1.5)
    assert rig.link.state.track_offset == 0


def test_the_last_bank_can_have_empty_strips():
    rig = McuRig(McuLink, tracks=10)
    rig.link.start()
    rig.run(0.3)
    rig.link.bank(1)
    rig.run(0.3)
    assert names(rig.link)[:2] == ["Trk 9", "Trk 10"] and names(rig.link)[2:] == [""] * 6
    rig.link.bank(1)                                                 # plus de piste au-delà
    rig.run(1.5)
    assert rig.link.state.track_offset == 8


# --------------------------------------------------------------------------- #
# Fermeture, ports, garde-fous
# --------------------------------------------------------------------------- #
def test_closing_lifts_the_fingers_and_closes_the_ports(rig):
    rig.link.set_track_volume(0, 0.7)
    rig.link.play_pause()
    rig.daw.step()
    assert 0 in rig.daw.touched
    rig.link.close()
    rig.daw.step()
    assert not rig.daw.touched and rig.daw.link.is_open is False
    assert rig.daw.released.count(mcu.PLAY) == 1                     # le bouton en attente est relâché
    assert not rig.link.state.connected


def test_a_failing_port_never_raises(rig):
    rig.daw.link.fail = True
    rig.link.play_pause()
    rig.link.set_track_volume(0, 0.3)
    rig.link.set_track_pan(0, 0.5)
    rig.run(0.5)
    rig.link.close()


def test_the_keepalive_is_off_by_default_and_optional():
    rig = McuRig(McuLink)
    rig.link.start()
    rig.run(1.0)
    assert rig.daw.keepalives == 0
    other = McuRig(McuLink, keepalive_s=0.3)
    other.link.start()
    other.run(1.0)
    assert 2 <= other.daw.keepalives <= 4


def test_leds_of_unrelated_buttons_are_ignored(rig):
    rig.daw.push(mcu.led(mcu.SCRUB, mcu.LED_ON), mcu.led(0x2A, mcu.LED_ON))
    assert rig.link.poll() is False


def test_a_flashing_play_led_is_not_playing(rig):
    rig.daw.push(mcu.led(mcu.PLAY, mcu.LED_ON))
    rig.link.poll()
    assert rig.link.state.transport.playing
    rig.daw.push(mcu.led(mcu.PLAY, mcu.LED_FLASH))
    rig.link.poll()
    assert not rig.link.state.transport.playing


def test_names_that_did_not_change_do_not_count_as_a_new_bank(rig):
    rig.daw.accept_banks = False
    rig.link.bank(1)
    rig.daw.push(mcu.lcd(0, "".join(rig.daw._names())))             # le logiciel renvoie les mêmes noms
    rig.run(0.3)
    assert rig.link.state.track_offset == 0
    assert rig.link.state.tracks[0].index == 0


def test_a_flashing_arm_led_counts_as_armed(rig):
    rig.daw.push(mcu.led(mcu.REC + 5, mcu.LED_FLASH))
    rig.link.poll()
    assert rig.link.state.tracks[5].armed

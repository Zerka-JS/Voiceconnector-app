"""Synchronisation du mixer de FL Studio : protocole, cœur, liaison avec la console, et le
vrai script de FL Studio exécuté contre un faux mixer (sans Qt, sans FL Studio, sans MIDI)."""

import sys
import types
from pathlib import Path

import pytest
from fl_rig import SCRIPT, Event, FakeClock, FakeMixer, FakeTransport, FlStudioRig  # noqa: F401

from core import fl_sync as fl
from core.fl_sync import (FlStripSync, FlSyncCore, MidoTransport, StripCommand, TransportError,
                          db_to_fl_volume, find_port, fl_volume_to_db, frame, parse, role_tracks)
from core.mixer_model import StripParams


def connected_core(tracks=(0, 1, 2)):
    clock, transport = FakeClock(), FakeTransport()
    core = FlSyncCore(transport, watch=tracks, clock=clock)
    core.start()
    core.poll()
    transport.inbox.append(fl.ack(126))
    core.poll()
    transport.sent.clear()
    return core, transport, clock


# --------------------------------------------------------------------------- #
# Conversions
# --------------------------------------------------------------------------- #
def test_fl_fader_law():
    assert fl_volume_to_db(0.8) == pytest.approx(0.0)
    assert fl_volume_to_db(1.0) == pytest.approx(fl.FL_MAX_DB) and 5.5 < fl.FL_MAX_DB < 5.7
    assert fl_volume_to_db(0.0) == fl.SILENCE_DB
    assert fl_volume_to_db(0.4) < fl_volume_to_db(0.6) < fl_volume_to_db(0.8)
    for db in (-60.0, -24.0, -6.0, 0.0, 3.0, 5.0):
        assert fl_volume_to_db(db_to_fl_volume(db)) == pytest.approx(db, abs=1e-6)


def test_volume_is_limited_to_the_top_of_the_fader():
    assert db_to_fl_volume(12.0) == 1.0 and db_to_fl_volume(40.0) == 1.0
    assert db_to_fl_volume(-100.0) == 0.0 and db_to_fl_volume(-99.5) == 0.0
    assert 0.0 < db_to_fl_volume(-60.0) < 0.1


def test_14_bit_values():
    for value in (0.0, 0.25, 0.8, 1.0):
        assert fl.from14(*fl.to14(value)) == pytest.approx(value, abs=1 / fl.MAX14)
    assert fl.to14(0.0) == (0, 0) and fl.to14(1.0) == (127, 127) and fl.to14(7.0) == (127, 127)
    assert fl.to14(-3.0) == (0, 0)
    assert all(b <= 0x7F for b in fl.to14(0.123456))


def test_pan_is_centered_exactly():
    assert fl.pan_to14(0.0) == (64, 0) and fl.pan_from14(64, 0) == 0.0
    assert fl.pan_from14(*fl.pan_to14(-1.0)) == -1.0 and fl.pan_from14(*fl.pan_to14(1.0)) == 1.0
    for pan in (-0.73, -0.1, 0.05, 0.5, 0.99):
        assert fl.pan_from14(*fl.pan_to14(pan)) == pytest.approx(pan, abs=2 / fl.MAX14)
    assert fl.pan_to14(5.0) == fl.pan_to14(1.0) and fl.pan_to14(-5.0) == fl.pan_to14(-1.0)


def test_meter_levels():
    assert fl.level_to7(0.0) == 0 and fl.level_from7(0) == fl.SILENCE_DB
    assert fl.level_to7(1.0) == 127 and fl.level_from7(127) == pytest.approx(0.0)
    assert fl.level_to7(5.0) == 127                                        # au-delà de 0 dB : plafonné
    assert fl.level_from7(fl.level_to7(0.1)) == pytest.approx(-20.0, abs=0.5)
    assert fl.level_to7(1e-4) == 0 or fl.level_to7(1e-4) < 5               # sous −60 dB : silence ou presque
    assert fl.level_to7(0.001) < fl.level_to7(0.01) < fl.level_to7(0.5)


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def test_frames_are_sysex_with_the_voiceconnector_header():
    message = frame(0x10, (1, 2, 3))
    assert message == bytes([0xF0, 0x7D, 0x56, 0x43, 0x10, 1, 2, 3, 0xF7])
    assert parse(message) == fl.Frame(0x10, bytes([1, 2, 3]))


def test_frames_only_carry_7_bit_data():
    with pytest.raises(ValueError):
        frame(0x10, (0x80,))
    with pytest.raises(ValueError):
        frame(0x80)


def test_parse_accepts_messages_with_or_without_sysex_markers():
    body = bytes([0x7D, 0x56, 0x43, 0x01, 1, 1])
    assert parse(body) == parse(b"\xF0" + body + b"\xF7") == fl.Frame(0x01, bytes([1, 1]))
    assert parse([0xF0, 0x7D, 0x56, 0x43, 0x0F, 0xF7]) == fl.Frame(0x0F, b"")


@pytest.mark.parametrize("raw", [
    b"", b"\xF0\xF7", b"\xF0\x7D\x56\xF7",                     # trop court
    bytes([0xF0, 0x41, 0x56, 0x43, 0x01, 0xF7]),               # autre fabricant
    bytes([0xF0, 0x7D, 0x56, 0x44, 0x01, 0xF7]),               # autre signature
    bytes([0xF0, 0x7D, 0x56, 0x43, 0x01, 0x90, 0xF7]),         # octet de données invalide
])
def test_parse_ignores_foreign_or_broken_messages(raw):
    assert parse(raw) is None


def test_watch_message_is_sorted_unique_and_limited():
    assert parse(fl.watch([3, 1, 3, 200, -1, 2])).data == bytes([3, 1, 2, 3])
    assert parse(fl.watch(range(100))).data[0] == fl.MAX_WATCH


def test_messages_from_fl_studio_are_decoded():
    track = fl.decode_from_fl(parse(fl.track_dump(2, 0.8, -0.5, True, False, "Drums")))
    assert track.track == 2 and track.volume == pytest.approx(0.8, abs=1e-4)
    assert track.pan == pytest.approx(-0.5, abs=1e-3) and track.muted and not track.solo
    assert track.name == "Drums"
    assert fl.decode_from_fl(parse(fl.ack(126))) == fl.Ack(fl.PROTOCOL_VERSION, 126)
    assert fl.decode_from_fl(parse(fl.bye())) == fl.Goodbye()
    assert fl.decode_from_fl(parse(fl.set_mute(4, True))) == fl.TrackUpdate(4, muted=True)
    assert fl.decode_from_fl(parse(fl.set_solo(4, False))) == fl.TrackUpdate(4, solo=False)
    assert fl.decode_from_fl(parse(fl.set_volume(1, 1.0))).volume == 1.0
    assert fl.decode_from_fl(parse(fl.set_pan(1, 0.0))).pan == 0.0
    meters = fl.decode_from_fl(parse(fl.meters([(0, 1.0, 0.0), (3, 0.1, 0.1)])))
    assert meters.entries[0] == (0, pytest.approx(0.0), fl.SILENCE_DB) and meters.entries[1][0] == 3


@pytest.mark.parametrize("command, data", [
    (fl.CMD_ACK, b"\x01"), (fl.CMD_TRACK, b"\x01\x02"), (fl.CMD_VOLUME, b"\x01"),
    (fl.CMD_METERS, b"\x01\x02"), (0x55, b"\x01\x02\x03"),
])
def test_malformed_messages_from_fl_studio_are_ignored(command, data):
    assert fl.decode_from_fl(fl.Frame(command, data)) is None


def test_track_names_are_ascii_and_short():
    update = fl.decode_from_fl(parse(fl.track_dump(1, 0.5, 0, False, False, "Batterie Éléphant " * 4)))
    assert len(update.name) <= 24 and all(ord(c) < 128 for c in update.name)


# --------------------------------------------------------------------------- #
# Cœur
# --------------------------------------------------------------------------- #
def test_nothing_is_sent_before_start():
    transport = FakeTransport()
    core = FlSyncCore(transport, clock=FakeClock())
    core.poll()
    assert transport.sent == [] and not core.connected


def test_hello_then_ack_connects_and_sets_the_watch_list():
    clock, transport = FakeClock(), FakeTransport()
    core = FlSyncCore(transport, watch=(4, 0, 1, 1), clock=clock)
    assert core.watched == (0, 1, 4)
    core.start()
    result = core.poll()
    assert transport.commands() == [fl.CMD_HELLO] and not result.connection_changed
    assert parse(transport.sent[0]).data == bytes([fl.PROTOCOL_VERSION, 1])           # état complet demandé
    transport.inbox.append(fl.ack(126))
    result = core.poll()
    assert core.connected and result.connection_changed and core.track_count == 126
    assert transport.commands()[-1] == fl.CMD_WATCH
    assert parse(transport.sent[-1]).data == bytes([3, 0, 1, 4])
    assert not core.poll().connection_changed


def test_hello_is_repeated_until_fl_studio_answers():
    clock, transport = FakeClock(), FakeTransport()
    core = FlSyncCore(transport, clock=clock)
    core.start()
    core.poll()
    clock.advance(fl.RETRY_S / 2)
    core.poll()
    assert transport.commands().count(fl.CMD_HELLO) == 1
    clock.advance(fl.RETRY_S)
    core.poll()
    assert transport.commands().count(fl.CMD_HELLO) == 2


def test_state_comes_from_fl_studio_and_changes_are_reported():
    core, transport, clock = connected_core()
    transport.inbox += [fl.track_dump(1, 0.5, 0.25, False, True, "Drums"), fl.track_dump(2, 0.8, 0, False, False)]
    result = core.poll()
    everything = {"volume", "pan", "mute", "solo", "name"}
    assert result.changed[1] == everything and result.changed[2] == everything     # première fois : tout est neuf
    state = core.state(1)
    assert state.known and state.name == "Drums" and state.solo and not state.muted
    assert state.volume == pytest.approx(0.5, abs=1e-4) and state.pan == pytest.approx(0.25, abs=1e-3)
    assert state.gain_db == pytest.approx(fl_volume_to_db(0.5), abs=1e-2)
    # un message identique ne change rien ; un nouveau volume si
    transport.inbox += [fl.set_volume(1, 0.5), fl.set_volume(1, 0.6)]
    assert core.poll().changed == {1: {"volume"}}
    # un second état complet ne signale que ce qui a changé (la quantification sur 14 bits ne compte pas)
    transport.inbox.append(fl.track_dump(2, 0.8, 0, True, False, "Synths"))
    assert core.poll().changed == {2: {"mute", "name"}}


def test_unknown_or_foreign_messages_are_skipped():
    core, transport, _ = connected_core()
    transport.inbox += [b"\xF0\x41\x10\xF7", b"", fl.frame(0x55, (1, 2, 3)), fl.set_mute(1, True)]
    assert core.poll().changed == {1: {"mute"}}


def test_writes_are_coalesced_and_sent_on_flush():
    core, transport, _ = connected_core()
    for volume in (0.1, 0.2, 0.3):
        core.write_volume(1, volume)
    core.write_mute(1, True)
    core.write_pan(2, -0.5)
    core.write_solo(2, True)
    assert transport.sent == []
    assert core.flush() == 4
    sent = [parse(m) for m in transport.sent]
    volume = next(m for m in sent if m.command == fl.CMD_VOLUME)
    assert fl.from14(volume.data[1], volume.data[2]) == pytest.approx(0.3, abs=1e-4)      # seule la dernière
    assert sorted(m.command for m in sent) == sorted([fl.CMD_VOLUME, fl.CMD_MUTE, fl.CMD_PAN, fl.CMD_SOLO])
    assert core.flush() == 0
    state = core.state(1)
    assert state.volume == 0.3 and state.muted and core.state(2).solo       # état local aussitôt à jour


def test_writes_while_disconnected_are_dropped():
    clock, transport = FakeClock(), FakeTransport()
    core = FlSyncCore(transport, clock=clock)
    core.write_volume(1, 0.5)
    assert core.flush() == 0 and transport.sent == []
    core.start()
    core.poll()
    transport.inbox.append(fl.ack(126))
    core.poll()
    assert core.flush() == 0                         # rien n'est rejoué à la connexion : FL Studio fait foi


def test_echo_of_our_own_write_is_ignored_for_a_moment():
    core, transport, clock = connected_core()
    core.write_volume(1, 0.7)
    core.flush()
    transport.inbox.append(fl.set_volume(1, 0.4))             # FL renvoie une ancienne valeur : écho
    assert core.poll().changed == {}
    assert core.state(1).volume == 0.7
    clock.advance(fl.ECHO_GUARD_S + 0.05)
    transport.inbox.append(fl.set_volume(1, 0.4))
    assert core.poll().changed == {1: {"volume"}} and core.state(1).volume == pytest.approx(0.4, abs=1e-4)


def test_other_fields_are_not_guarded():
    core, transport, _ = connected_core()
    core.write_volume(1, 0.7)
    transport.inbox.append(fl.set_mute(1, True))
    assert core.poll().changed == {1: {"mute"}}


def test_keep_alive_and_timeout():
    core, transport, clock = connected_core()
    clock.advance(fl.PING_S + 0.1)
    core.poll()
    assert transport.commands() == [fl.CMD_HELLO] and parse(transport.sent[0]).data[1] == 0   # simple ping
    transport.inbox.append(fl.ack(126))
    core.poll()
    assert core.connected
    clock.advance(fl.TIMEOUT_S + 1)
    result = core.poll()
    assert not core.connected and result.connection_changed
    transport.sent.clear()
    clock.advance(fl.RETRY_S + 0.1)
    core.poll()
    assert transport.commands()[-1] == fl.CMD_HELLO and parse(transport.sent[-1]).data[1] == 1


def test_goodbye_from_fl_studio_disconnects_at_once():
    core, transport, _ = connected_core()
    transport.inbox.append(fl.track_dump(1, 0.5, 0, False, False, "Drums"))
    core.poll()
    assert core.state(1).known
    transport.inbox.append(fl.bye())
    result = core.poll()
    assert not core.connected and result.connection_changed and not core.state(1).known


def test_meters_update_the_peak_levels():
    core, transport, _ = connected_core()
    transport.inbox.append(fl.meters([(1, 0.5, 0.05)]))
    result = core.poll()
    assert result.changed == {}                          # les niveaux ne sont pas des « changements »
    left, right = core.state(1).peak_db
    assert left == pytest.approx(-6.0, abs=0.6) and right == pytest.approx(-26.0, abs=0.6)


def test_set_watch_while_connected_tells_fl_studio():
    core, transport, _ = connected_core()
    core.set_watch([5, 3])
    assert core.watched == (3, 5) and transport.commands() == [fl.CMD_WATCH]


def test_transport_failures_never_raise():
    core, transport, clock = connected_core()
    transport.fail = True
    core.write_volume(1, 0.2)
    core.flush()
    clock.advance(fl.PING_S + 1)
    core.poll()
    core.set_watch([1])
    core.stop()
    assert not core.connected

    class Broken(FakeTransport):
        def receive(self):
            raise OSError("boom")

    broken = FlSyncCore(Broken(), clock=FakeClock())
    broken.start()
    assert broken.poll().changed == {}


def test_stop_says_goodbye_once():
    core, transport, _ = connected_core()
    core.stop()
    assert transport.commands() == [fl.CMD_BYE] and not core.connected
    core.stop()
    assert transport.commands() == [fl.CMD_BYE]


# --------------------------------------------------------------------------- #
# Ports MIDI
# --------------------------------------------------------------------------- #
def test_close_says_goodbye_and_closes_the_port():
    core, transport, _ = connected_core()
    core.close()
    assert transport.commands() == [fl.CMD_BYE] and not transport.is_open and not core.connected
    core.close()                                                      # deux fois : sans erreur
    assert transport.commands() == [fl.CMD_BYE]


def test_find_port_matches_loopmidi_names():
    names = ["Microsoft GS Wavetable Synth 0", "VoiceConnector FL 1", "VoiceConnector VC 2"]
    assert find_port(names, "VoiceConnector FL") == "VoiceConnector FL 1"
    assert find_port(names, "voiceconnector vc") == "VoiceConnector VC 2"
    assert find_port(names, "VoiceConnector VC 2") == "VoiceConnector VC 2"
    assert find_port(names, "Autre") is None and find_port(names, "  ") is None
    assert find_port(["VoiceConnector FL", "VoiceConnector FL 1"], "VoiceConnector FL") == "VoiceConnector FL"


def fake_mido(outputs, inputs):
    """Un faux module mido, avec la forme de l'API réelle."""
    log = types.SimpleNamespace(opened=[], sent=[], callbacks=[])

    class Message:
        def __init__(self, type_, data=()):
            self.type, self.data = type_, tuple(data)

    class Port:
        def __init__(self, name, callback=None):
            self.name, self.closed = name, False
            log.opened.append(name)
            if callback is not None:
                log.callbacks.append(callback)

        def send(self, message):
            log.sent.append(message)

        def close(self):
            self.closed = True

    module = types.ModuleType("mido")
    module.Message = Message
    module.get_output_names = lambda: list(outputs)
    module.get_input_names = lambda: list(inputs)
    module.open_output = lambda name: Port(name)
    module.open_input = lambda name, callback=None: Port(name, callback)
    return module, log


def test_mido_transport_opens_the_right_ports_and_exchanges_sysex(monkeypatch):
    module, log = fake_mido(["VoiceConnector FL 1", "Autre"], ["VoiceConnector VC 2"])
    monkeypatch.setitem(sys.modules, "mido", module)
    transport = MidoTransport()
    assert log.opened == ["VoiceConnector FL 1", "VoiceConnector VC 2"] and transport.is_open
    transport.send(fl.set_mute(1, True))
    sent = log.sent[0]
    assert sent.type == "sysex" and sent.data == (0x7D, 0x56, 0x43, fl.CMD_MUTE, 1, 1)   # sans F0 / F7
    # réception : le callback de python-rtmidi tourne dans un autre thread
    log.callbacks[0](module.Message("sysex", (0x7D, 0x56, 0x43, fl.CMD_BYE)))
    log.callbacks[0](module.Message("note_on", ()))
    assert transport.receive() == [fl.bye()] and transport.receive() == []
    transport.close()
    assert not transport.is_open
    transport.send(fl.bye())                                  # fermé : ignoré
    assert len(log.sent) == 1


def test_mido_transport_explains_missing_ports(monkeypatch):
    module, _ = fake_mido(["Autre"], ["Autre"])
    monkeypatch.setitem(sys.modules, "mido", module)
    with pytest.raises(TransportError, match="loopMIDI"):
        MidoTransport()
    module, _ = fake_mido(["VoiceConnector FL"], ["Autre"])
    monkeypatch.setitem(sys.modules, "mido", module)
    with pytest.raises(TransportError, match="VoiceConnector VC"):
        MidoTransport()


def test_mido_transport_explains_a_missing_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "mido", None)             # « import mido » échoue
    with pytest.raises(TransportError, match="mido"):
        MidoTransport()


def test_connect_fl_gives_a_started_core_or_the_reason(monkeypatch):
    transport = FakeTransport()
    monkeypatch.setattr(fl, "open_transport", lambda cfg: transport)
    core, error = fl.connect_fl({}, watch=(0, 2))
    assert error == "" and core.watched == (0, 2) and not core.connected
    core.poll()
    assert transport.commands() == [fl.CMD_HELLO]                         # le dialogue est lancé

    def missing(cfg):
        raise TransportError("Port MIDI virtuel introuvable : « VoiceConnector FL ». Crée-le avec loopMIDI.")

    monkeypatch.setattr(fl, "open_transport", missing)
    assert fl.connect_fl({}) == (None, "Port MIDI virtuel introuvable : « VoiceConnector FL ». Crée-le avec loopMIDI.")

    def broken(cfg):
        raise OSError("pilote MIDI absent")

    monkeypatch.setattr(fl, "open_transport", broken)
    core, error = fl.connect_fl({})
    assert core is None and "pilote MIDI absent" in error


def test_open_transport_reads_the_port_names_from_the_config(monkeypatch):
    module, log = fake_mido(["Mon port A"], ["Mon port B"])
    monkeypatch.setitem(sys.modules, "mido", module)
    fl.open_transport({"flsync": {"out_port": "port a", "in_port": "port b"}})
    assert log.opened == ["Mon port A", "Mon port B"]


# --------------------------------------------------------------------------- #
# Liaison pistes ↔ tranches
# --------------------------------------------------------------------------- #
def strip(gain=0.0, mute=False, pan=None):
    pads = (("pan", pan, 0.5),) if pan is not None else ()
    return StripParams(gain, mute, (), (), pads)


def linked_sync():
    core, transport, clock = connected_core(tracks=(0, 1, 2))
    transport.inbox += [fl.track_dump(0, 0.8, 0, False, False, "Master"),
                        fl.track_dump(1, 0.8, 0, False, False, "Drums"),
                        fl.track_dump(2, 0.8, 0, False, False, "Synths")]
    core.poll()
    return FlStripSync(core, {0: 0, 3: 1, 4: 2}), core, transport


def test_role_tracks_follow_the_studio_template_and_the_config():
    roles = {"master": 5, "drums": 6, "synths": 7, "fx": 1, "mic": 0}
    assert role_tracks({}, roles) == {5: 0, 6: 1, 7: 2, 1: 3, 0: 4}
    assert role_tracks({"flsync": {"tracks": {"drums": 10, "fx": "12", "mic": "x", "synths": 999}}},
                       roles) == {5: 0, 6: 10, 7: 2, 1: 12, 0: 4}
    assert role_tracks(None, {"unknown": 3}) == {}
    # deux rôles ne peuvent pas suivre la même piste
    assert role_tracks({"flsync": {"tracks": {"fx": 1}}}, {"drums": 6, "fx": 1}) == {6: 1}


def test_pushing_writes_only_what_differs():
    sync, core, transport = linked_sync()
    transport.sent.clear()
    assert sync.push([strip(0.0), strip(), strip(), strip(0.0), strip(0.0)]) == 0           # tout est déjà pareil
    assert sync.push([strip(), strip(), strip(), strip(-6.0, True, pan=0.25), strip(0.0, False, pan=0.0)]) == 3
    assert core.flush() == 3
    sent = [parse(m) for m in transport.sent]
    assert {(m.command, m.data[0]) for m in sent} == {(fl.CMD_VOLUME, 1), (fl.CMD_MUTE, 1), (fl.CMD_PAN, 1)}
    pan = next(m for m in sent if m.command == fl.CMD_PAN)
    assert fl.pan_from14(pan.data[1], pan.data[2]) == pytest.approx(0.5, abs=1e-3)          # pad x 0,25 → pan 0,5
    assert sync.push([strip(), strip(), strip(), strip(-6.0, True, pan=0.25), strip()]) == 0   # plus rien à écrire


def test_gain_above_the_top_of_the_fader_is_not_resent():
    sync, core, _ = linked_sync()
    assert sync.push([strip(), strip(), strip(), strip(12.0), strip()]) == 1
    assert core.state(1).volume == 1.0
    assert sync.push([strip(), strip(), strip(), strip(12.0), strip()]) == 0
    assert sync.push([strip(), strip(), strip(), strip(fl.FL_MAX_DB), strip()]) == 0


def test_a_track_pulled_down_to_nothing_is_not_written_back_as_minus_60_db():
    core, transport, _ = connected_core((1,))
    transport.inbox.append(fl.track_dump(1, 0.0, 0.0, False, False, "Drums"))
    core.poll()
    sync = FlStripSync(core, {0: 1})
    assert sync.push([strip(-60.0)]) == 0                     # fader au minimum = piste à −∞ : pareil


def test_push_waits_for_the_connection_and_for_known_tracks():
    sync, core, transport = linked_sync()
    core.connected = False
    assert sync.push([strip(-3.0)] * 5) == 0
    core.connected = True
    core.state(1).known = False
    assert sync.push([strip(), strip(), strip(), strip(-9.0), strip()]) == 0
    assert sync.push([strip()]) == 0                           # moins de tranches que de liaisons : ignoré


def test_solo_is_forwarded_for_linked_strips_only():
    sync, core, transport = linked_sync()
    transport.sent.clear()
    sync.push_solo(3, True)
    sync.push_solo(1, True)                                    # tranche non liée
    core.flush()
    assert [(parse(m).command, parse(m).data) for m in transport.sent] == [(fl.CMD_SOLO, bytes([1, 1]))]


def test_pulling_turns_fl_changes_into_strip_commands():
    sync, core, transport = linked_sync()
    transport.inbox += [fl.set_volume(1, 0.5), fl.set_mute(2, True), fl.set_pan(1, -0.5),
                        fl.set_solo(2, True), fl.set_mute(9, True)]              # piste 9 : non liée
    result = core.poll()
    commands = sync.pull(result.changed)
    assert StripCommand("gain", 3, round(fl_volume_to_db(0.5), 1)) in commands
    assert StripCommand("mute", 4, True) in commands and StripCommand("solo", 4, True) in commands
    pan = next(c for c in commands if c.kind == "pan")
    assert pan.strip == 3 and pan.value == pytest.approx(-0.25, abs=1e-3)
    assert all(c.strip in (0, 3, 4) for c in commands)


def test_pulled_gain_stays_inside_the_console_range():
    sync, core, transport = linked_sync()
    transport.inbox.append(fl.set_volume(1, 0.0))
    commands = sync.pull(core.poll().changed)
    assert commands == [StripCommand("gain", 3, fl.GAIN_RANGE_DB[0])]


def test_no_feedback_loop_between_console_and_fl_studio():
    sync, core, transport = linked_sync()
    transport.inbox.append(fl.set_volume(1, 0.5))
    commands = sync.pull(core.poll().changed)
    db = commands[0].value
    # la console applique l'ordre, puis ses paramètres reviennent : rien à renvoyer à FL Studio
    assert sync.push([strip(), strip(), strip(), strip(db), strip()]) == 0


def test_snapshot_gives_every_known_linked_track():
    sync, core, _ = linked_sync()
    snapshot = sync.snapshot()
    assert {c.strip for c in snapshot} == {0, 3, 4} and {c.kind for c in snapshot} == {"gain", "mute", "pan", "solo"}
    core.state(2).known = False
    assert {c.strip for c in sync.snapshot()} == {0, 3}


def test_strip_sync_sets_the_watch_list():
    core, transport, _ = connected_core()
    FlStripSync(core, {0: 7, 1: 2})
    assert core.watched == (2, 7) and parse(transport.sent[-1]).data == bytes([2, 2, 7])


# --------------------------------------------------------------------------- #
# Le vrai script de FL Studio, contre un faux mixer
# --------------------------------------------------------------------------- #
@pytest.fixture
def rig(monkeypatch):
    return FlStudioRig(monkeypatch)


def run(rig, tracks=(0, 1, 2)):
    clock = FakeClock()
    core = FlSyncCore(rig, watch=tracks, clock=clock)
    core.start()
    core.poll()          # HELLO → ACK + état des pistes par défaut
    core.poll()          # ACK → WATCH
    core.poll()          # états des pistes suivies
    return core, clock


def test_script_loads_and_names_itself_for_fl_studio():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "# name=VoiceConnector"
    for entry in ("def OnInit", "def OnSysEx", "def OnRefresh", "def OnUpdateMeters", "def OnIdle"):
        assert entry in text


def test_script_ignores_foreign_sysex(rig):
    event = Event(bytes([0xF0, 0x41, 0x10, 0x42, 0xF7]))
    rig.script.OnSysEx(event)
    assert not event.handled and rig.outbox == []


def test_script_answers_hello_and_dumps_the_watched_tracks(rig):
    rig.mixer.tracks[1].update(volume=0.5, name="Drums", mute=True)
    core, _ = run(rig, tracks=(0, 1, 2))
    assert core.connected and core.track_count == 126
    state = core.state(1)
    assert state.known and state.name == "Drums" and state.muted
    assert state.volume == pytest.approx(0.5, abs=1e-4)
    assert core.state(2).known and core.state(2).name == "Insert 2"
    assert not core.state(5).known                                 # piste non suivie


def test_vc_writes_reach_the_fl_mixer(rig):
    core, _ = run(rig)
    core.write_volume(1, 0.35)
    core.write_pan(1, -0.4)
    core.write_mute(2, True)
    core.write_solo(2, True)
    core.flush()
    assert rig.mixer.tracks[1]["volume"] == pytest.approx(0.35, abs=1e-4)
    assert rig.mixer.tracks[1]["pan"] == pytest.approx(-0.4, abs=1e-3)
    assert rig.mixer.tracks[2]["mute"] and rig.mixer.tracks[2]["solo"]
    core.write_mute(2, False)                                      # la bascule est idempotente
    core.write_mute(2, False)
    core.flush()
    assert not rig.mixer.tracks[2]["mute"]


def test_fl_changes_reach_vc_and_our_own_writes_are_not_echoed(rig):
    core, clock = run(rig)
    core.write_volume(1, 0.35)
    core.flush()
    rig.script.OnRefresh(0)                    # FL Studio signale le changement qu'on vient de faire
    assert rig.outbox == [] or all(parse(m).command == fl.CMD_METERS for m in rig.outbox)
    # l'utilisateur bouge un fader et coupe une piste dans FL Studio
    rig.mixer.tracks[2]["volume"] = 0.6
    rig.mixer.tracks[2]["mute"] = True
    rig.script.OnRefresh(0)
    clock.advance(1.0)
    result = core.poll()
    assert result.changed == {2: {"volume", "mute"}}
    assert core.state(2).volume == pytest.approx(0.6, abs=1e-4) and core.state(2).muted


def test_idle_is_a_safety_net_for_changes_without_refresh(rig, monkeypatch):
    core, clock = run(rig)
    rig.mixer.tracks[1]["pan"] = 0.5
    monkeypatch.setattr(rig.script.time, "monotonic", lambda: 1000.0)
    rig.script.OnIdle()
    clock.advance(1.0)
    assert core.poll().changed == {1: {"pan"}}


def test_meters_are_sent_for_watched_tracks_only(rig, monkeypatch):
    core, _ = run(rig, tracks=(0, 1))
    rig.mixer.tracks[1]["peak"] = (1.0, 0.1)
    rig.mixer.tracks[5]["peak"] = (1.0, 1.0)
    monkeypatch.setattr(rig.script.time, "monotonic", lambda: 2000.0)
    rig.script.OnUpdateMeters()
    core.poll()
    assert core.state(1).peak_db[0] == pytest.approx(0.0) and core.state(1).peak_db[1] == pytest.approx(-20.0, abs=0.5)
    assert core.state(5).peak_db == (fl.SILENCE_DB, fl.SILENCE_DB)


def test_script_stays_quiet_until_vc_says_hello_and_after_bye(rig, monkeypatch):
    rig.mixer.tracks[1]["volume"] = 0.3
    rig.script.OnRefresh(0)
    rig.script.OnIdle()
    rig.script.OnUpdateMeters()
    assert rig.outbox == []
    core, _ = run(rig)
    core.stop()                                                    # BYE
    rig.outbox.clear()
    rig.mixer.tracks[1]["volume"] = 0.9
    rig.script.OnRefresh(0)
    assert rig.outbox == []


def test_script_says_goodbye_when_it_is_unloaded(rig):
    core, _ = run(rig)
    rig.script.OnDeInit()
    assert core.poll().connection_changed and not core.connected


def test_script_default_watch_list_and_unknown_tracks(rig):
    rig.send(fl.hello(full=True))
    dumped = [fl.decode_from_fl(parse(m)) for m in rig.receive()]
    assert isinstance(dumped[0], fl.Ack)
    assert [u.track for u in dumped[1:]] == [0, 1, 2, 3, 4]        # liste par défaut
    rig.send(fl.watch([6, 7, 100]))                                # 100 : piste que le faux mixer n'a pas
    assert [fl.decode_from_fl(parse(m)).track for m in rig.receive()] == [6, 7]
    rig.send(fl.set_volume(100, 0.5))                              # piste inconnue : ignorée sans erreur
    assert rig.receive() == []


def test_script_codec_matches_the_core_codec(rig):
    for value in (0.0, 0.123, 0.5, 0.8, 1.0):
        assert rig.script._to14(value) == fl.to14(value)
        assert rig.script._from14(*fl.to14(value)) == fl.from14(*fl.to14(value))
    for pan in (-1.0, -0.37, 0.0, 0.5, 1.0):
        assert rig.script._pan_to14(pan) == fl.pan_to14(pan)
        assert rig.script._pan_from14(*fl.pan_to14(pan)) == fl.pan_from14(*fl.pan_to14(pan))
    for peak in (0.0, 1e-5, 0.001, 0.1, 0.5, 1.0, 3.0):
        assert rig.script._level_to7(peak) == fl.level_to7(peak)
    for name in ("MANUFACTURER", "VERSION", "MAX14", "PAN_CENTER", "MAX_TRACK", "METER_FLOOR_DB"):
        core_name = {"MANUFACTURER": "MANUFACTURER_ID", "VERSION": "PROTOCOL_VERSION"}.get(name, name)
        assert getattr(rig.script, name) == getattr(fl, core_name)
    assert rig.script.SIGNATURE == fl.SIGNATURE
    for name in ("CMD_HELLO", "CMD_WATCH", "CMD_ACK", "CMD_BYE", "CMD_VOLUME", "CMD_PAN", "CMD_MUTE",
                 "CMD_SOLO", "CMD_TRACK", "CMD_METERS"):
        assert getattr(rig.script, name) == getattr(fl, name)
    assert rig.script.DEFAULT_WATCH == (0, 1, 2, 3, 4) == tuple(range(5))


def test_full_round_trip_with_the_console_link(rig):
    """Console → FL Studio → console, avec la liaison tranche ↔ piste."""
    core, clock = run(rig, tracks=(0, 1, 2))
    sync = FlStripSync(core, {0: 0, 3: 1, 4: 2})
    core.poll()
    core.poll()
    clock.advance(1.0)
    assert sync.push([strip(), strip(), strip(), strip(-12.0), strip(0.0, True)]) == 2
    core.flush()
    assert rig.mixer.tracks[1]["volume"] == pytest.approx(db_to_fl_volume(-12.0), abs=1e-4)
    assert rig.mixer.tracks[2]["mute"]
    rig.mixer.tracks[0]["volume"] = 0.7                              # on bouge le Master dans FL Studio
    rig.script.OnRefresh(0)
    clock.advance(1.0)
    commands = sync.pull(core.poll().changed)
    assert commands == [StripCommand("gain", 0, round(fl_volume_to_db(0.7), 1))]


# --------------------------------------------------------------------------- #
# Installation du script
# --------------------------------------------------------------------------- #
def test_script_directory_is_the_fl_studio_hardware_folder(tmp_path):
    expected = tmp_path / "Image-Line" / "FL Studio" / "Settings" / "Hardware" / "VoiceConnector"
    assert fl.script_dir(tmp_path) == expected


def test_install_copies_the_script_and_reports_its_state(tmp_path):
    status, target = fl.script_status(tmp_path)
    assert status == fl.SCRIPT_MISSING and target.name == "device_VoiceConnector.py"
    installed = fl.install_script(tmp_path)
    assert installed == target and installed.read_bytes() == SCRIPT.read_bytes()
    assert fl.script_status(tmp_path)[0] == fl.SCRIPT_CURRENT
    installed.write_text("# ancienne version", encoding="utf-8")
    assert fl.script_status(tmp_path)[0] == fl.SCRIPT_OUTDATED
    fl.install_script(tmp_path)                                    # mise à jour par-dessus
    assert fl.script_status(tmp_path)[0] == fl.SCRIPT_CURRENT


def test_install_without_a_source_file_fails_cleanly(tmp_path):
    with pytest.raises(FileNotFoundError):
        fl.install_script(tmp_path, source=tmp_path / "absent.py")


def test_installed_script_is_the_one_shipped_with_the_app():
    assert SCRIPT.is_file() and fl.SCRIPT_RESOURCE.endswith("device_VoiceConnector.py")
    assert (Path(fl.__file__).resolve().parent.parent / fl.SCRIPT_RESOURCE).is_file()


def test_documents_directory_has_a_fallback():
    assert fl.documents_dir().name  # n'importe quel dossier, mais jamais d'exception


def test_mido_availability_follows_the_installed_modules(monkeypatch):
    import importlib.util as util

    real = util.find_spec
    monkeypatch.setattr(util, "find_spec", lambda name, *a, **k: object() if name in ("mido", "rtmidi") else real(name))
    assert fl.mido_available()
    monkeypatch.setattr(util, "find_spec", lambda name, *a, **k: None if name == "rtmidi" else object())
    assert not fl.mido_available()


# --------------------------------------------------------------------------- #
# Diagnostic de l'installation
# --------------------------------------------------------------------------- #
class FakeMido:
    def __init__(self, outputs=(), inputs=()):
        self._outputs, self._inputs = list(outputs), list(inputs)

    def get_output_names(self):
        return self._outputs

    def get_input_names(self):
        return self._inputs


class BrokenMido:
    def get_output_names(self):
        raise OSError("pilote MIDI absent")

    def get_input_names(self):
        return []


def checks_for(tmp_path, mido_module, cfg=None, installed=False):
    if installed:
        fl.install_script(tmp_path)
    return fl.fl_setup_checks(cfg, documents=tmp_path, mido_module=mido_module)


def test_everything_ready_means_every_check_passes(tmp_path):
    mido = FakeMido(["VoiceConnector FL 1", "Autre"], ["VoiceConnector VC 2"])
    checks = checks_for(tmp_path, mido, installed=True)
    assert [c.ok for c in checks] == [True, True, True, True]
    assert "VoiceConnector FL 1" in checks[1].text and "VoiceConnector VC 2" in checks[2].text


def test_missing_loopmidi_ports_are_reported_with_the_fix(tmp_path):
    checks = checks_for(tmp_path, FakeMido(["Autre"], []), installed=True)
    assert [c.ok for c in checks] == [True, False, False, True]
    assert "loopMIDI" in checks[1].text and "« VoiceConnector FL »" in checks[1].text
    assert "« VoiceConnector VC »" in checks[2].text


def test_ports_named_in_the_config_are_the_ones_checked(tmp_path):
    cfg = {"flsync": {"out_port": "Mon port A", "in_port": "Mon port B"}}
    checks = checks_for(tmp_path, FakeMido(["Mon port A"], ["Mon port B"]), cfg, installed=True)
    assert [c.ok for c in checks] == [True, True, True, True]


def test_an_unreadable_midi_driver_is_a_failed_check_not_an_error(tmp_path):
    checks = checks_for(tmp_path, BrokenMido(), installed=True)
    assert checks[0].ok and not checks[1].ok and "illisibles" in checks[1].text and len(checks) == 3


def test_the_script_check_follows_the_installation(tmp_path):
    mido = FakeMido(["VoiceConnector FL"], ["VoiceConnector VC"])
    missing = checks_for(tmp_path, mido)[-1]
    assert not missing.ok and "pas encore installé" in missing.text
    fl.install_script(tmp_path)
    assert checks_for(tmp_path, mido)[-1].ok
    fl.script_status(tmp_path)[1].write_text("# ancien", encoding="utf-8")
    old = checks_for(tmp_path, mido)[-1]
    assert not old.ok and "ancien" in old.text


def test_without_the_midi_library_the_check_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "mido_available", lambda: False)
    checks = fl.fl_setup_checks({}, documents=tmp_path)
    assert not checks[0].ok and "pip install mido" in checks[0].text and len(checks) == 2

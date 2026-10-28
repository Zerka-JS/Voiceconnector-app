"""Protocole du Studio avec FL Studio : nombres sur 7 bits, textes accentués, chaque message
encodé puis décodé, et messages mal formés (sans Qt)."""

import pytest

from core import fl_sync
from core import studio_proto as sp
from core.daw_model import fl_color_to_hex, hex_to_fl_color


def decoded(raw: bytes):
    message = fl_sync.parse(raw)
    assert message is not None, raw
    return sp.decode(message)


# --------------------------------------------------------------------------- #
# Nombres et textes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("count,value", [(2, 0), (2, 127), (2, 128), (2, 16383), (3, 115000), (3, 999000),
                                         (4, 0), (4, 123456), (4, 0xFFFFFF)])
def test_pack_round_trips_and_stays_on_seven_bits(count, value):
    packed = sp.pack(value, count)
    assert len(packed) == count and all(b <= 0x7F for b in packed)
    assert sp.unpack(packed) == value


def test_pack_clamps_instead_of_overflowing():
    assert sp.unpack(sp.pack(-5, 2)) == 0
    assert sp.unpack(sp.pack(10 ** 9, 2)) == 16383


def test_unit_and_pan_values_round_trip_with_one_step_of_precision():
    for value in (0.0, 0.25, 0.78125, 1.0):
        assert sp.from_unit14(sp.unit14(value)) == pytest.approx(value, abs=1 / 16383)
    for pan in (-1.0, -0.5, 0.0, 0.3, 1.0):
        assert sp.from_pan14(sp.pan14(pan)) == pytest.approx(pan, abs=1 / 8191)
    assert sp.from_unit14(sp.unit14(7)) == 1.0          # hors limites : ramené dans 0 → 1
    assert sp.from_pan14(sp.pan14(-9)) == -1.0 and sp.pan14(0) == (64, 0)      # centre = 8192


@pytest.mark.parametrize("text", ["Kick", "Hi-Hat 2", "Mélodie été", "Piano – Ré", "日本語", "😀 Bass",
                                  "Basse « sub »"])
def test_text_round_trips_with_accents_and_symbols(text):
    encoded = sp.encode_text(text)
    assert all(b <= 0x7F for b in encoded)
    assert sp.decode_text(encoded) == text


def test_text_is_limited_and_drops_control_characters():
    assert len(sp.decode_text(sp.encode_text("x" * 100))) == sp.MAX_TEXT
    assert sp.decode_text(sp.encode_text("a\tb\nc")) == "abc"
    assert sp.decode_text(sp.encode_text("long nom", limit=4)) == "long"


def test_a_truncated_escape_sequence_is_ignored_not_fatal():
    encoded = sp.encode_text("é")
    assert sp.decode_text(encoded[:-1]) == ""           # séquence coupée en route
    assert sp.decode_text(bytes([0x7F, 0, 0, 0x01])) == "?"     # code de contrôle : remplacé, pas de crash
    assert sp.decode_text(bytes([0x7F, *sp.pack(0xD800, 3)])) == "?"      # moitié de paire de substitution
    assert sp.decode_text(bytes([0x41, 0x7F])) == "A"                    # échappement coupé en fin de texte


def test_fl_colors_are_converted_both_ways():
    assert fl_color_to_hex(0x0000FF) == "#ff0000"           # FL : 0x00BBGGRR
    assert fl_color_to_hex(0xFF0000) == "#0000ff"
    assert fl_color_to_hex(0x336699) == "#996633"
    assert hex_to_fl_color("#996633") == 0x336699 and hex_to_fl_color("pas une couleur") == 0
    for value in (0x000000, 0x123456, 0xFFFFFF):
        assert hex_to_fl_color(fl_color_to_hex(value)) == value


# --------------------------------------------------------------------------- #
# Messages vers FL Studio : tout tient sur 7 bits et a la bonne forme
# --------------------------------------------------------------------------- #
def test_every_command_to_fl_is_valid_sysex():
    messages = [sp.hello(), sp.hello(False, 32, 80, 40), sp.bye(), sp.refresh(), sp.step_count(32),
                sp.transport(sp.ACTION_PLAY), sp.tempo(115.5), sp.seek(123456), sp.step(3, 15, True),
                sp.channel_volume(3, 0.8), sp.channel_pan(3, -0.4), sp.channel_mute(3, True),
                sp.channel_solo(3, False), sp.channel_select(3), sp.pattern_select(12),
                sp.track_mute(5, True), sp.track_solo(5, False)]
    for raw in messages:
        assert raw[0] == 0xF0 and raw[-1] == 0xF7 and all(b <= 0x7F for b in raw[1:-1])
        frame = fl_sync.parse(raw)
        assert frame is not None and 0x30 <= frame.command <= 0x3F


def test_refresh_names_the_part_to_resend():
    assert fl_sync.parse(sp.refresh()).data == bytes((sp.SCOPE_ALL,))
    assert fl_sync.parse(sp.refresh(sp.SCOPE_STEPS | sp.SCOPE_RACK)).data == bytes((6,))
    assert fl_sync.parse(sp.refresh(0)).data == bytes((sp.SCOPE_ALL,))            # rien de précis : tout
    assert fl_sync.parse(sp.refresh(255)).data == bytes((sp.SCOPE_ALL,))


def test_commands_carry_their_values():
    assert fl_sync.parse(sp.tempo(115.5)).data == bytes(sp.pack(115500, 3))
    assert fl_sync.parse(sp.seek(300)).data == bytes(sp.pack(300, 4))
    assert fl_sync.parse(sp.step(1000, 99, True)).data == bytes((*sp.pack(1000, 2), sp.MAX_STEP, 1))
    assert fl_sync.parse(sp.hello(True, 200, 500, 500)).data == bytes((sp.STUDIO_VERSION, sp.HELLO_FULL, 64, 127, 127))
    assert fl_sync.parse(sp.hello(False)).data[1] == 0
    assert fl_sync.parse(sp.step_count(0)).data == bytes((1,))


# --------------------------------------------------------------------------- #
# Messages de FL Studio : encodés par le script, décodés ici
# --------------------------------------------------------------------------- #
def test_ack_and_project_name():
    event = decoded(sp.ack(sp.ACK_GLOBAL_INDEX, "21.2.3"))
    assert event == sp.StudioAck(sp.STUDIO_VERSION, sp.ACK_GLOBAL_INDEX, "21.2.3")
    assert decoded(sp.project("Été 2026 – démo")) == sp.ProjectName("Été 2026 – démo")


def test_transport_state():
    event = decoded(sp.state(True, False, True, True, 115.0, 96 * 9 + 5, 96, 4))
    assert event == sp.TransportState(True, False, True, True, 115.0, 96 * 9 + 5, 96, 4)
    event = decoded(sp.state(False, True, False, False, 87.125, 0, 960, 3))
    assert (event.playing, event.recording, event.song_mode, event.metronome) == (False, True, False, False)
    assert event.tempo == pytest.approx(87.125) and event.ppq == 960 and event.beats_per_bar == 3


def test_channel_info_carries_everything_the_rack_shows():
    raw = sp.channel(7, "Kick « 808 »", 0x2040F0, 0.75, -0.25, True, False, True, kind=2)
    event = decoded(raw)
    assert (event.index, event.name, event.color) == (7, "Kick « 808 »", fl_color_to_hex(0x2040F0))
    assert event.volume == pytest.approx(0.75, abs=1e-3) and event.pan == pytest.approx(-0.25, abs=1e-3)
    assert (event.muted, event.solo, event.selected, event.kind) == (True, False, True, "plugin")
    assert decoded(sp.channel(300, "x", 0, 1.0, 0.0, False, True, False, kind=9)).kind == "sampler"   # type inconnu
    assert decoded(sp.channel_count(300)) == sp.ChannelCount(300)


def test_steps_are_packed_seven_per_byte():
    cells = [i % 4 == 0 for i in range(16)]
    raw = sp.steps_data(2, 5, cells)
    event = decoded(raw)
    assert event == sp.StepsData(2, 5, tuple(cells))
    assert len(fl_sync.parse(raw).data) == 5 + 3                      # 16 pas : 3 octets de bits
    full = [True] * 64
    assert decoded(sp.steps_data(0, 1, full)).steps == tuple(full)
    assert decoded(sp.steps_data(0, 1, [])).steps == ()


def test_patterns():
    event = decoded(sp.pattern(4, "Refrain", 0x0000FF, 32, True))
    assert event == sp.PatternInfo(4, "Refrain", "#ff0000", 32, True)
    event = decoded(sp.pattern_list(4, [1, 2, 4, 300]))
    assert event == sp.PatternList(4, (1, 2, 4, 300))
    assert decoded(sp.pattern_list(0, [])) == sp.PatternList(0, ())


def test_playlist_tracks_and_activity():
    event = decoded(sp.playlist_track(3, "Basse", 0x336699, False, True, False))
    assert event == sp.PlaylistTrack(3, "Basse", "#996633", False, True, False)
    assert decoded(sp.playlist_count(6)) == sp.PlaylistCount(6)
    event = decoded(sp.activity(sp.CMD_ST_PL_ACTIVITY, 1, [0.0, 0.5, 1.0]))
    assert event.target == "tracks" and event.first == 1
    assert event.levels == pytest.approx((0.0, 64 / 127, 1.0))
    event = decoded(sp.activity(sp.CMD_ST_CH_ACTIVITY, 0, [1.0]))
    assert event.target == "channels" and event.levels == (1.0,)


def test_goodbye_is_the_shared_one():
    assert isinstance(decoded(fl_sync.bye()), sp.FlGoodbye)


@pytest.mark.parametrize("command", [sp.CMD_ST_ACK, sp.CMD_ST_STATE, sp.CMD_ST_CHANNEL, sp.CMD_ST_STEPS,
                                     sp.CMD_ST_PATTERN, sp.CMD_ST_PATTERN_LIST, sp.CMD_ST_PL_TRACK,
                                     sp.CMD_ST_PL_ACTIVITY, sp.CMD_ST_CHANNEL_COUNT, sp.CMD_ST_PL_COUNT])
def test_truncated_messages_are_ignored(command):
    assert sp.decode(fl_sync.Frame(command, b"")) is None
    assert sp.decode(fl_sync.Frame(command, bytes((1,)))) is None


def test_unknown_and_mixer_messages_are_not_studio_events():
    assert sp.decode(fl_sync.Frame(0x7E, b"\x01\x02")) is None
    assert sp.decode(fl_sync.parse(fl_sync.ack(5))) is None             # ACK du mixer (0x03)
    assert sp.decode(fl_sync.parse(fl_sync.meters([(0, 0.5, 0.5)]))) is None

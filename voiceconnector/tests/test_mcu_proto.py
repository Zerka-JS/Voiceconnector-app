"""Protocole Mackie Control : encodage des ordres de la surface, décodage de ce que renvoie le logiciel."""

import pytest

from core import mcu_proto as mcu


# --------------------------------------------------------------------------- #
# Valeurs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [0.0, 0.25, 0.5, 0.8, 1.0])
def test_a_fader_position_survives_the_round_trip(value):
    assert mcu.from_position(mcu.to_position(value)) == pytest.approx(value, abs=1 / mcu.MAX14)


def test_fader_positions_are_clamped():
    assert mcu.to_position(-3.0) == 0 and mcu.to_position(7.0) == mcu.MAX14
    assert mcu.from_position(-5) == 0.0 and mcu.from_position(10 ** 6) == 1.0


def test_a_relative_move_encodes_the_direction_in_bit_six():
    assert mcu.relative(3) == 3
    assert mcu.relative(-3) == 0x43
    assert mcu.relative(500) == 63 and mcu.relative(-500) == 0x40 | 63
    assert mcu.relative(0) == 0


def test_the_ring_gives_the_pan_and_back():
    assert mcu.ring_to_pan(mcu.RING_CENTER) == 0.0
    assert mcu.ring_to_pan(1) == -1.0 and mcu.ring_to_pan(mcu.RING_LEDS) == 1.0
    assert mcu.ring_to_pan(0) == 0.0                                  # couronne éteinte : pas de panoramique
    assert mcu.ring_to_pan(3, mcu.RING_WRAP) == 0.0                   # ce n'est pas un panoramique
    for pan in (-1.0, -0.4, 0.0, 0.4, 1.0):
        assert mcu.ring_to_pan(mcu.pan_to_ring(pan)) == pytest.approx(pan, abs=0.11)
    assert mcu.pan_to_ring(-9) == 1 and mcu.pan_to_ring(9) == mcu.RING_LEDS


# --------------------------------------------------------------------------- #
# De la surface vers le logiciel
# --------------------------------------------------------------------------- #
def test_buttons_are_notes():
    assert mcu.button(mcu.PLAY, True) == bytes((0x90, 0x5E, 0x7F))
    assert mcu.button(mcu.PLAY, False) == bytes((0x90, 0x5E, 0x00))
    assert mcu.button(mcu.MUTE + 3, True)[1] == 0x13


def test_faders_are_14_bit_pitch_bend_on_the_strip_channel():
    message = mcu.fader(2, 1.0)
    assert message == bytes((0xE2, 0x7F, 0x7F))
    assert mcu.fader(0, 0.0) == bytes((0xE0, 0, 0))
    assert mcu.fader(mcu.MASTER, 0.5)[0] == 0xE8


def test_the_pot_and_the_wheel_are_relative_controllers():
    assert mcu.vpot(1, 2) == bytes((0xB0, 0x11, 0x02))
    assert mcu.vpot(7, -1) == bytes((0xB0, 0x17, 0x41))
    assert mcu.jog(-2) == bytes((0xB0, 0x3C, 0x42))


def test_the_surface_introduces_itself_with_a_serial_and_a_challenge():
    message = mcu.host_query()
    assert message[:6] == bytes((0xF0, 0x00, 0x00, 0x66, 0x14, 0x01)) and message[-1] == 0xF7
    assert len(message) == 6 + 7 + 4 + 1
    assert all(b < 0x80 for b in message[1:-1])
    assert mcu.host_confirm()[5] == mcu.CMD_HOST_CONFIRM and len(mcu.host_confirm()) == 6 + 7 + 1


# --------------------------------------------------------------------------- #
# Du logiciel vers la surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state", [mcu.LED_OFF, mcu.LED_FLASH, mcu.LED_ON])
def test_leds_round_trip(state):
    assert mcu.parse(mcu.led(mcu.MUTE + 2, state)) == mcu.Led(mcu.MUTE + 2, state)


def test_a_note_off_is_an_extinguished_led():
    assert mcu.parse(bytes((0x80, 0x5E, 0x40))) == mcu.Led(0x5E, mcu.LED_OFF)


def test_faders_round_trip_on_every_strip_and_the_master():
    for strip in range(mcu.STRIPS + 1):
        event = mcu.parse(mcu.fader_position(strip, 0.37))
        assert isinstance(event, mcu.Fader) and event.strip == strip
        assert event.value == pytest.approx(0.37, abs=1 / mcu.MAX14)
    assert mcu.parse(bytes((0xE9, 0, 0))) is None                     # canal 9 : pas un fader


def test_rings_round_trip():
    assert mcu.parse(mcu.ring(3, 9, mcu.RING_BOOST_CUT, True)) == mcu.Ring(3, 9, mcu.RING_BOOST_CUT, True)
    assert mcu.parse(mcu.ring(0, 0)) == mcu.Ring(0, 0, mcu.RING_SINGLE, False)


def test_meters_round_trip_and_ignore_overload_codes():
    assert mcu.parse(mcu.meter(5, 1.0)) == mcu.Meter(5, 1.0)
    assert mcu.parse(mcu.meter(2, 0.0)) == mcu.Meter(2, 0.0)
    assert mcu.parse(bytes((0xD0, 0x2E))) is None                     # surcharge : pas un niveau
    assert mcu.parse(bytes((0xD0, 0x7C))) == mcu.Meter(7, 1.0)


def test_the_screen_carries_seven_characters_per_strip():
    event = mcu.parse(mcu.lcd(7, "Basse  Piano  "))
    assert event == mcu.Lcd(7, "Basse  Piano  ")
    assert mcu.parse(mcu.lcd(0, "é")).text == " "                    # tout ce qui n'est pas ASCII devient un espace


def test_the_time_display_reads_digit_by_digit():
    cells = [mcu.parse(m) for m in mcu.display("001.02.03.004")]
    assert all(isinstance(c, mcu.Digit) for c in cells) and len(cells) == 10
    assert mcu.display_text(cells) == "001.02.03.004"
    assert mcu.parse(mcu.digit(0, "5")) == mcu.Digit(0, "5", False)
    assert mcu.parse(mcu.digit(3, "9", True)) == mcu.Digit(3, "9", True)
    assert mcu.parse(mcu.digit(9, "A")) == mcu.Digit(9, "A", False)


def test_a_short_text_is_right_aligned_on_the_display():
    cells = [mcu.parse(m) for m in mcu.display("1.2.3")]
    assert mcu.display_text(cells) == "1.2.3"
    assert mcu.parse(mcu.display("1.2.3")[-1]).char == "3" and mcu.parse(mcu.display("1.2.3")[0]).char == " "


def test_the_connection_handshake_messages():
    assert mcu.parse(mcu.device_query()) == mcu.DeviceQuery()
    reply = mcu.parse(mcu.host_reply(bytes(range(7)), bytes(4)))
    assert reply == mcu.HostReply(bytes(range(7)))
    assert mcu.parse(mcu.go_offline()) == mcu.GoOffline()


@pytest.mark.parametrize("garbage", [b"", b"\x90", b"\xf0\x7d\x01\xf7", b"\xf0\x00\x00\x66\x14\xf7",
                                     bytes((0xF0, 0x00, 0x00, 0x66, 0x14, 0x12, 0, 0x41)),     # sans F7
                                     bytes((0xF0, 0x00, 0x00, 0x66, 0x09, 0x00, 0xF7)),        # autre modèle
                                     bytes((0xB0, 0x20, 0x10)), bytes((0xA0, 1, 2)), bytes((0xF8,))])
def test_what_is_not_mackie_control_is_ignored(garbage):
    assert mcu.parse(garbage) is None


def test_parsing_never_raises_on_anything():
    for value in (None, 12, "texte", object()):
        assert mcu.parse(value) is None
    assert mcu.parse([0x90, 0x5E, 0x7F]) == mcu.Led(0x5E, mcu.LED_ON)
    assert mcu.parse([0x90, 300, 5]) is None                          # valeur hors octet : jamais d'exception

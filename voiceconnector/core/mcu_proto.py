"""
Protocole Mackie Control (MCU) : le « langage » des surfaces de contrôle que comprennent Cubase /
Nuendo, Studio One, Ableton Live, REAPER, Bitwig, Reason, Cakewalk, Ardour… (sans Qt, sans MIDI).

VoiceConnector joue le rôle de la surface : il envoie des appuis de boutons, des mouvements de fader
et de potard, et il reçoit du logiciel (le « host ») l'état des voyants, des faders, des afficheurs
et des indicateurs de niveau.

Messages de la surface vers le logiciel
    - bouton : note-on, numéro de note = bouton, vélocité 0x7F (appui) ou 0 (relâché) ;
    - fader : pitch-bend 14 bits sur le canal MIDI 0 à 7 (tranche) ou 8 (général) ;
    - potard : contrôleur 0x10 + tranche, valeur relative (bit 6 = sens, bits 0–5 = crans).

Messages du logiciel vers la surface
    - voyants : note-on, vélocité 0 (éteint), 1 (clignote) ou 0x7F (allumé) ;
    - faders : pitch-bend ; couronne du potard : contrôleur 0x30 + tranche ;
    - niveaux : channel-pressure (tranche × 16 + niveau 0 à 12) ;
    - afficheur de temps : contrôleurs 0x40 à 0x49 (un chiffre chacun, le plus à droite d'abord) ;
    - écran LCD : SysEx `F0 00 00 66 14 12 <position> <texte> F7` (2 lignes de 56 caractères,
      7 par tranche).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

STRIPS = 8                                # tranches d'une surface
MASTER = 8                                # canal MIDI du fader général
MAX14 = 0x3FFF

SYSEX_HEADER = (0x00, 0x00, 0x66)         # fabricant (Mackie) ; l'octet suivant est le modèle
MODEL_MCU = 0x14
MODELS = (0x10, 0x14, 0x15)               # Logic Control, Mackie Control, extension
DEFAULT_SERIAL = bytes((0x56, 0x43, 0x4F, 0x4E, 0x53, 0x4F, 0x4C))   # « VCONSOL » : identifie VoiceConnector
DEFAULT_CHALLENGE = bytes((0x01, 0x02, 0x03, 0x04))

# --------------------------------------------------------------------------- #
# Boutons (numéros de note)
# --------------------------------------------------------------------------- #
REC = 0x00                                # + tranche : armement à l'enregistrement
SOLO = 0x08
MUTE = 0x10
SELECT = 0x18
VSELECT = 0x20                            # appui sur le potard
BANK_LEFT, BANK_RIGHT = 0x2E, 0x2F
CHANNEL_LEFT, CHANNEL_RIGHT = 0x30, 0x31
CYCLE = 0x56
CLICK = 0x59
REWIND, FAST_FORWARD = 0x5B, 0x5C
STOP, PLAY, RECORD = 0x5D, 0x5E, 0x5F
SCRUB = 0x65
FADER_TOUCH = 0x68                        # + tranche : doigt posé sur le fader
MASTER_TOUCH = 0x70

LED_OFF, LED_FLASH, LED_ON = 0, 1, 2

# Couronne lumineuse du potard
RING_SINGLE, RING_BOOST_CUT, RING_WRAP, RING_SPREAD = 0, 1, 2, 3
RING_LEDS = 11
RING_CENTER = 6                           # position du milieu (panoramique centré)

LCD_COLUMNS = 7                           # caractères par tranche et par ligne
LCD_ROW = 56                              # caractères d'une ligne
LCD_SIZE = 2 * LCD_ROW
DISPLAY_DIGITS = 10                       # afficheur de temps
METER_STEPS = 12                          # niveau maximal d'un indicateur de la surface

ACTIVE_SENSING = bytes((0xFE,))

# Commandes SysEx
CMD_DEVICE_QUERY = 0x00                   # logiciel → surface : « qui est là ? »
CMD_HOST_QUERY = 0x01                     # surface → logiciel : numéro de série et défi
CMD_HOST_REPLY = 0x02                     # logiciel → surface : réponse au défi
CMD_HOST_CONFIRM = 0x03                   # surface → logiciel : la liaison est établie
CMD_LCD = 0x12
CMD_GO_OFFLINE = 0x0F


# --------------------------------------------------------------------------- #
# Valeurs
# --------------------------------------------------------------------------- #
def to_position(value: float) -> int:
    """Position d'un fader (0 → 1) en pitch-bend 14 bits."""
    return int(round(max(0.0, min(1.0, float(value))) * MAX14))


def from_position(position: int) -> float:
    return max(0, min(MAX14, int(position))) / MAX14


def relative(delta: int) -> int:
    """Crans d'un potard ou de la molette : bit 6 = sens inverse, bits 0–5 = nombre de crans."""
    delta = int(delta)
    ticks = min(63, abs(delta))
    return (0x40 | ticks) if delta < 0 else ticks


def ring_to_pan(position: int, mode: int = RING_SINGLE) -> float:
    """Panoramique (−1 → +1) lu sur la couronne du potard ; 0 pour un mode qui n'est pas un panoramique."""
    if mode not in (RING_SINGLE, RING_BOOST_CUT) or position <= 0:
        return 0.0
    return max(-1.0, min(1.0, (min(RING_LEDS, position) - RING_CENTER) / (RING_LEDS - RING_CENTER)))


def pan_to_ring(pan: float) -> int:
    """Position de la couronne (1 → 11) la plus proche d'un panoramique (−1 → +1)."""
    return int(max(1, min(RING_LEDS, round(RING_CENTER + max(-1.0, min(1.0, float(pan))) * (RING_LEDS - RING_CENTER)))))


# --------------------------------------------------------------------------- #
# De la surface vers le logiciel
# --------------------------------------------------------------------------- #
def button(note: int, pressed: bool) -> bytes:
    return bytes((0x90, note & 0x7F, 0x7F if pressed else 0x00))


def fader(strip: int, value: float) -> bytes:
    position = to_position(value)
    return bytes((0xE0 | (strip & 0x0F), position & 0x7F, position >> 7))


def vpot(strip: int, delta: int) -> bytes:
    return bytes((0xB0, 0x10 + (strip & 0x07), relative(delta)))


def jog(delta: int) -> bytes:
    return bytes((0xB0, 0x3C, relative(delta)))


def sysex(command: int, data: Iterable[int] = (), model: int = MODEL_MCU) -> bytes:
    return bytes((0xF0, *SYSEX_HEADER, model, command, *(b & 0x7F for b in data), 0xF7))


def host_query(serial: bytes = DEFAULT_SERIAL, challenge: bytes = DEFAULT_CHALLENGE) -> bytes:
    """« Me voici » : la surface se présente au logiciel (il répond par `CMD_HOST_REPLY`)."""
    return sysex(CMD_HOST_QUERY, tuple(serial[:7]) + tuple(challenge[:4]))


def host_confirm(serial: bytes = DEFAULT_SERIAL) -> bytes:
    return sysex(CMD_HOST_CONFIRM, tuple(serial[:7]))


# --------------------------------------------------------------------------- #
# Du logiciel vers la surface (ce que VoiceConnector décode ; les fonctions d'encodage
# servent à simuler un logiciel dans les tests)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Led:
    note: int
    state: int                            # LED_OFF, LED_FLASH ou LED_ON


@dataclass(frozen=True)
class Fader:
    strip: int                            # 0 à 7 ; MASTER pour le fader général
    value: float                          # 0 → 1


@dataclass(frozen=True)
class Ring:
    strip: int
    position: int                         # 0 (éteinte) à 11
    mode: int
    center: bool


@dataclass(frozen=True)
class Digit:
    index: int                            # 0 = chiffre le plus à droite
    char: str
    dot: bool


@dataclass(frozen=True)
class Meter:
    strip: int
    level: float                          # 0 → 1


@dataclass(frozen=True)
class Lcd:
    offset: int
    text: str


@dataclass(frozen=True)
class DeviceQuery:
    pass


@dataclass(frozen=True)
class HostReply:
    serial: bytes


@dataclass(frozen=True)
class GoOffline:
    pass


Event = Led | Fader | Ring | Digit | Meter | Lcd | DeviceQuery | HostReply | GoOffline


def digit_char(value: int) -> str:
    """Caractère d'un chiffre de l'afficheur : 0x30–0x39 = « 0 »–« 9 », 0x01–0x1A = « A »–« Z »."""
    code = value & 0x3F
    return chr(code + 0x40 if code < 0x20 else code)


def parse(message: bytes | bytearray | Sequence[int]) -> Event | None:
    """Décode un message MIDI venu du logiciel ; None pour tout ce que VoiceConnector ignore."""
    try:
        data = bytes(message)
    except (TypeError, ValueError):
        return None
    if not data:
        return None
    status = data[0]
    if status == 0xF0:
        return _parse_sysex(data)
    kind, channel = status & 0xF0, status & 0x0F
    if kind in (0x90, 0x80) and len(data) >= 3:
        velocity = data[2] if kind == 0x90 else 0
        state = LED_OFF if velocity == 0 else (LED_FLASH if velocity == 1 else LED_ON)
        return Led(data[1] & 0x7F, state)
    if kind == 0xE0 and len(data) >= 3 and channel <= MASTER:
        return Fader(channel, from_position((data[2] & 0x7F) << 7 | (data[1] & 0x7F)))
    if kind == 0xB0 and len(data) >= 3:
        controller, value = data[1] & 0x7F, data[2] & 0x7F
        if 0x30 <= controller < 0x30 + STRIPS:
            return Ring(controller - 0x30, value & 0x0F, (value >> 4) & 0x03, bool(value & 0x40))
        if 0x40 <= controller <= 0x4B:
            return Digit(controller - 0x40, digit_char(value), bool(value & 0x40))
        return None
    if kind == 0xD0 and len(data) >= 2:
        strip, level = (data[1] >> 4) & 0x07, data[1] & 0x0F
        return Meter(strip, level / METER_STEPS) if level <= METER_STEPS else None
    return None


def _parse_sysex(data: bytes) -> Event | None:
    if len(data) < 7 or data[-1] != 0xF7 or tuple(data[1:4]) != SYSEX_HEADER or data[4] not in MODELS:
        return None
    command, body = data[5], data[6:-1]
    if command == CMD_DEVICE_QUERY:
        return DeviceQuery()
    if command == CMD_HOST_REPLY:
        return HostReply(bytes(body[:7]))
    if command == CMD_GO_OFFLINE:
        return GoOffline()
    if command == CMD_LCD and body:
        text = "".join(chr(b) if 0x20 <= b < 0x7F else " " for b in body[1:])
        return Lcd(body[0], text)
    return None


def display_text(digits: dict[int, Digit] | Iterable[Digit]) -> str:
    """Texte de l'afficheur de temps, de gauche à droite (« 001.01.01.000 »)."""
    cells = digits if isinstance(digits, dict) else {d.index: d for d in digits}
    parts = []
    for index in range(DISPLAY_DIGITS - 1, -1, -1):
        cell = cells.get(index)
        if cell is not None:
            parts.append(cell.char + ("." if cell.dot and index else ""))
    return "".join(parts).strip()


# --- Encodage côté logiciel (tests) -----------------------------------------
def led(note: int, state: int) -> bytes:
    return bytes((0x90, note & 0x7F, {LED_OFF: 0x00, LED_FLASH: 0x01}.get(state, 0x7F)))


def fader_position(strip: int, value: float) -> bytes:
    return fader(strip, value)


def ring(strip: int, position: int, mode: int = RING_SINGLE, center: bool = False) -> bytes:
    return bytes((0xB0, 0x30 + strip, (position & 0x0F) | ((mode & 0x03) << 4) | (0x40 if center else 0)))


def digit(index: int, char: str, dot: bool = False) -> bytes:
    code = ord(char.upper()[:1] or " ")
    value = code - 0x40 if 0x41 <= code <= 0x5A else code & 0x3F
    return bytes((0xB0, 0x40 + index, value | (0x40 if dot else 0)))


def display(text: str) -> list[bytes]:
    """Les contrôleurs qui affichent `text` (jusqu'à 10 chiffres, points compris)."""
    cells: list[tuple[str, bool]] = []
    for char in text:
        if char == "." and cells:
            cells[-1] = (cells[-1][0], True)
        else:
            cells.append((char, False))
    cells = cells[-DISPLAY_DIGITS:]
    cells = [(" ", False)] * (DISPLAY_DIGITS - len(cells)) + cells
    return [digit(DISPLAY_DIGITS - 1 - position, char, dot) for position, (char, dot) in enumerate(cells)]


def meter(strip: int, level: float) -> bytes:
    steps = int(round(max(0.0, min(1.0, level)) * METER_STEPS))
    return bytes((0xD0, ((strip & 0x07) << 4) | steps))


def lcd(offset: int, text: str) -> bytes:
    return sysex(CMD_LCD, [offset] + [ord(c) if 0x20 <= ord(c) < 0x7F else 0x20 for c in text])


def device_query() -> bytes:
    return sysex(CMD_DEVICE_QUERY)


def host_reply(serial: bytes = DEFAULT_SERIAL, response: bytes = bytes(4)) -> bytes:
    return sysex(CMD_HOST_REPLY, tuple(serial[:7]) + tuple(response[:4]))


def go_offline() -> bytes:
    return sysex(CMD_GO_OFFLINE, (0x7F,))

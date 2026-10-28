"""
Protocole du Studio avec le script de FL Studio : transport, channel rack, patterns et playlist
(sans Qt).

C'est le même canal SysEx que la synchronisation du mixer (core/fl_sync.py : même en-tête
`F0 7D 56 43`, mêmes ports virtuels loopMIDI) avec d'autres commandes (0x30 → 0x6F) :

    VoiceConnector → FL                                 FL → VoiceConnector
    0x30 HELLO   [version, drapeaux, pas, canaux, pistes]  0x50 ACK        [version, drapeaux, version de FL…]
    0x31 BYE                                               0x51 PROJET     [nom…]
    0x32 REFRESH (tout renvoyer)                           0x52 ÉTAT       [drapeaux, tempo, ticks, ppq, temps/mesure]
    0x33 PAS        [nombre de pas]                        0x53 CANAL      [n°, drapeaux, type, vol, pan, couleur, nom…]
    0x34 TRANSPORT  [action]                               0x54 NB CANAUX  [nombre]
    0x35 TEMPO      [bpm × 1000]                           0x55 PAS-DONNÉES [canal, pattern, nombre, bits…]
    0x36 POSITION   [ticks]                                0x56 PATTERN    [n°, drapeaux, couleur, longueur, nom…]
    0x37 PAS        [canal, pas, 0|1]                      0x57 LISTE PATTERNS [choisi, nombre, n°…]
    0x38 VOLUME CANAL / 0x39 PAN CANAL                     0x58 PISTE      [n°, drapeaux, couleur, nom…]
    0x3A MUET CANAL / 0x3B SOLO CANAL                      0x59 NB PISTES  [nombre]
    0x3C CHOIX CANAL / 0x3D CHOIX PATTERN                  0x5A ACTIVITÉ PISTES [première, nombre, niveaux…]
    0x3E MUET PISTE / 0x3F SOLO PISTE                      0x5B ACTIVITÉ CANAUX [première, nombre, niveaux…]

Octets de données sur 7 bits : nombres en groupes de 7 bits (poids fort d'abord), couleurs de FL
Studio (0x00BBGGRR) sur 4 octets, textes en UTF-8 « protégé » (voir `encode_text`). Le script de FL
Studio (assets/flstudio/device_VoiceConnector.py) n'importe rien de VoiceConnector : il porte sa
propre copie de ces fonctions, que tests/test_fl_studio_link.py vérifie contre celle-ci.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from core.daw_model import fl_color_to_hex
from core.fl_sync import Frame, frame

STUDIO_VERSION = 1

# --- VoiceConnector → FL -----------------------------------------------------
CMD_ST_HELLO, CMD_ST_BYE, CMD_ST_REFRESH, CMD_ST_STEP_COUNT = 0x30, 0x31, 0x32, 0x33
CMD_ST_TRANSPORT, CMD_ST_TEMPO, CMD_ST_SEEK, CMD_ST_STEP = 0x34, 0x35, 0x36, 0x37
CMD_ST_CH_VOLUME, CMD_ST_CH_PAN, CMD_ST_CH_MUTE, CMD_ST_CH_SOLO = 0x38, 0x39, 0x3A, 0x3B
CMD_ST_CH_SELECT, CMD_ST_PATTERN_SELECT = 0x3C, 0x3D
CMD_ST_PL_MUTE, CMD_ST_PL_SOLO = 0x3E, 0x3F

# --- FL → VoiceConnector -----------------------------------------------------
CMD_ST_ACK, CMD_ST_PROJECT, CMD_ST_STATE = 0x50, 0x51, 0x52
CMD_ST_CHANNEL, CMD_ST_CHANNEL_COUNT, CMD_ST_STEPS = 0x53, 0x54, 0x55
CMD_ST_PATTERN, CMD_ST_PATTERN_LIST = 0x56, 0x57
CMD_ST_PL_TRACK, CMD_ST_PL_COUNT = 0x58, 0x59
CMD_ST_PL_ACTIVITY, CMD_ST_CH_ACTIVITY = 0x5A, 0x5B
CMD_ST_BYE_FROM_FL = 0x0F                    # le même « au revoir » que la synchronisation du mixer

# Actions de transport (commande 0x34)
ACTION_PLAY, ACTION_STOP, ACTION_RECORD, ACTION_SONG_MODE, ACTION_METRONOME, ACTION_REWIND = 1, 2, 3, 4, 5, 6

# Drapeaux
HELLO_FULL = 0x01                            # tout renvoyer (sinon : simple « je suis là »)
ACK_GLOBAL_INDEX = 0x01                      # le script utilise les numéros de canaux globaux
STATE_PLAYING, STATE_RECORDING, STATE_SONG, STATE_METRONOME = 0x01, 0x02, 0x04, 0x08
FLAG_MUTE, FLAG_SOLO, FLAG_SELECTED = 0x01, 0x02, 0x04
PATTERN_SELECTED = 0x01

# Ce qu'une demande de renvoi (refresh) peut viser
SCOPE_TRANSPORT, SCOPE_RACK, SCOPE_STEPS, SCOPE_PATTERNS, SCOPE_PLAYLIST = 1, 2, 4, 8, 16
SCOPE_ALL = 31

MAX_CHANNELS = 127                           # canaux affichés (un octet de limite dans HELLO)
MAX_TRACKS = 127
MAX_STEP = 63
MAX14 = 16383
PAN_CENTER = 8192
MAX_TEXT = 24
ESCAPE = 0x7F

CHANNEL_KINDS = {0: "sampler", 1: "plugin", 2: "plugin", 3: "layer", 4: "audio", 5: "automation"}


# --------------------------------------------------------------------------- #
# Nombres et textes sur 7 bits
# --------------------------------------------------------------------------- #
def pack(value: int, count: int) -> tuple[int, ...]:
    """Entier en `count` groupes de 7 bits, poids fort d'abord (2 : 14 bits, 3 : 21, 4 : 28)."""
    value = max(0, min((1 << (7 * count)) - 1, int(value)))
    return tuple((value >> (7 * shift)) & 0x7F for shift in range(count - 1, -1, -1))


def unpack(data: Sequence[int]) -> int:
    result = 0
    for byte in data:
        result = (result << 7) | (byte & 0x7F)
    return result


def unit14(value: float) -> tuple[int, ...]:
    """0 → 1 sur 14 bits."""
    return pack(round(max(0.0, min(1.0, float(value))) * MAX14), 2)


def from_unit14(data: Sequence[int]) -> float:
    return unpack(data[:2]) / MAX14


def pan14(pan: float) -> tuple[int, ...]:
    """−1 → +1 sur 14 bits, centre = 8192."""
    pan = max(-1.0, min(1.0, float(pan)))
    raw = PAN_CENTER + round(pan * (8191 if pan >= 0 else 8192))
    return pack(max(0, min(MAX14, raw)), 2)


def from_pan14(data: Sequence[int]) -> float:
    raw = unpack(data[:2])
    return max(-1.0, (raw - PAN_CENTER) / (8191 if raw >= PAN_CENTER else 8192))


def encode_text(text: str, limit: int = MAX_TEXT) -> bytes:
    """Texte en octets de 7 bits : les caractères ASCII visibles passent tels quels, les autres
    (accents, symboles) sont écrits 0x7F suivi de leur code Unicode sur 3 octets."""
    out = bytearray()
    for char in str(text)[:limit]:
        code = ord(char)
        if 0x20 <= code < ESCAPE:
            out.append(code)
        elif code >= ESCAPE:
            out.append(ESCAPE)
            out.extend(pack(code, 3))
        # les caractères de contrôle sont ignorés
    return bytes(out)


def decode_text(data: Iterable[int]) -> str:
    raw = list(data)
    chars: list[str] = []
    i = 0
    while i < len(raw):
        byte = raw[i]
        if byte == ESCAPE:
            if i + 3 >= len(raw):
                break                                   # séquence coupée
            code = unpack(raw[i + 1:i + 4])
            chars.append(chr(code) if 0x20 <= code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "?")
            i += 4
        else:
            if byte >= 0x20:
                chars.append(chr(byte))
            i += 1
    return "".join(chars)


# --------------------------------------------------------------------------- #
# Messages VoiceConnector → FL
# --------------------------------------------------------------------------- #
def hello(full: bool = True, steps: int = 16, channels: int = 64, tracks: int = 32) -> bytes:
    return frame(CMD_ST_HELLO, (STUDIO_VERSION, HELLO_FULL if full else 0,
                                max(1, min(MAX_STEP + 1, steps)),
                                max(1, min(MAX_CHANNELS, channels)),
                                max(1, min(MAX_TRACKS, tracks))))


def bye() -> bytes:
    return frame(CMD_ST_BYE)


def refresh(scope: int = SCOPE_ALL) -> bytes:
    """Demande à FL Studio de renvoyer ce qu'il a déjà envoyé (tout, ou seulement une partie)."""
    return frame(CMD_ST_REFRESH, (scope & SCOPE_ALL or SCOPE_ALL,))


def step_count(steps: int) -> bytes:
    return frame(CMD_ST_STEP_COUNT, (max(1, min(MAX_STEP + 1, steps)),))


def transport(action: int) -> bytes:
    return frame(CMD_ST_TRANSPORT, (action,))


def tempo(bpm: float) -> bytes:
    return frame(CMD_ST_TEMPO, pack(round(bpm * 1000), 3))


def seek(ticks: int) -> bytes:
    return frame(CMD_ST_SEEK, pack(ticks, 4))


def step(channel: int, position: int, value: bool) -> bytes:
    return frame(CMD_ST_STEP, (*pack(channel, 2), max(0, min(MAX_STEP, position)), 1 if value else 0))


def channel_volume(channel: int, volume: float) -> bytes:
    return frame(CMD_ST_CH_VOLUME, (*pack(channel, 2), *unit14(volume)))


def channel_pan(channel: int, pan: float) -> bytes:
    return frame(CMD_ST_CH_PAN, (*pack(channel, 2), *pan14(pan)))


def channel_mute(channel: int, muted: bool) -> bytes:
    return frame(CMD_ST_CH_MUTE, (*pack(channel, 2), 1 if muted else 0))


def channel_solo(channel: int, solo: bool) -> bytes:
    return frame(CMD_ST_CH_SOLO, (*pack(channel, 2), 1 if solo else 0))


def channel_select(channel: int) -> bytes:
    return frame(CMD_ST_CH_SELECT, pack(channel, 2))


def pattern_select(pattern: int) -> bytes:
    return frame(CMD_ST_PATTERN_SELECT, pack(pattern, 2))


def track_mute(track: int, muted: bool) -> bytes:
    return frame(CMD_ST_PL_MUTE, (*pack(track, 2), 1 if muted else 0))


def track_solo(track: int, solo: bool) -> bytes:
    return frame(CMD_ST_PL_SOLO, (*pack(track, 2), 1 if solo else 0))


# --------------------------------------------------------------------------- #
# Messages FL → VoiceConnector (ce que le script envoie ; sert aussi aux tests)
# --------------------------------------------------------------------------- #
def ack(flags: int = ACK_GLOBAL_INDEX, fl_version: str = "") -> bytes:
    return frame(CMD_ST_ACK, (STUDIO_VERSION, flags, *encode_text(fl_version, 16)))


def project(name: str) -> bytes:
    return frame(CMD_ST_PROJECT, encode_text(name, 40))


def state(playing: bool, recording: bool, song_mode: bool, metronome: bool, tempo_bpm: float,
          ticks: int, ppq: int, beats_per_bar: int) -> bytes:
    flags = ((STATE_PLAYING if playing else 0) | (STATE_RECORDING if recording else 0)
             | (STATE_SONG if song_mode else 0) | (STATE_METRONOME if metronome else 0))
    return frame(CMD_ST_STATE, (flags, *pack(round(tempo_bpm * 1000), 3), *pack(ticks, 4),
                                *pack(ppq, 2), max(1, min(0x7F, beats_per_bar))))


def channel(index: int, name: str, color: int, volume: float, pan: float, muted: bool, solo: bool,
            selected: bool, kind: int = 0) -> bytes:
    flags = (FLAG_MUTE if muted else 0) | (FLAG_SOLO if solo else 0) | (FLAG_SELECTED if selected else 0)
    return frame(CMD_ST_CHANNEL, (*pack(index, 2), flags, max(0, min(0x7F, kind)), *unit14(volume),
                                  *pan14(pan), *pack(color, 4), *encode_text(name)))


def channel_count(count: int) -> bytes:
    return frame(CMD_ST_CHANNEL_COUNT, pack(count, 2))


def steps_data(channel_index: int, pattern: int, cells: Sequence[bool]) -> bytes:
    cells = list(cells)[:MAX_STEP + 1]
    packed = []
    for start in range(0, len(cells), 7):
        packed.append(sum(1 << bit for bit, on in enumerate(cells[start:start + 7]) if on))
    return frame(CMD_ST_STEPS, (*pack(channel_index, 2), *pack(pattern, 2), len(cells), *packed))


def pattern(index: int, name: str, color: int, length_steps: int, selected: bool) -> bytes:
    return frame(CMD_ST_PATTERN, (*pack(index, 2), PATTERN_SELECTED if selected else 0, *pack(color, 4),
                                  *pack(length_steps, 2), *encode_text(name)))


def pattern_list(selected: int, indices: Sequence[int]) -> bytes:
    indices = list(indices)[:60]
    return frame(CMD_ST_PATTERN_LIST, (*pack(max(0, selected), 2), len(indices),
                                       *(b for i in indices for b in pack(i, 2))))


def playlist_track(index: int, name: str, color: int, muted: bool, solo: bool, selected: bool) -> bytes:
    flags = (FLAG_MUTE if muted else 0) | (FLAG_SOLO if solo else 0) | (FLAG_SELECTED if selected else 0)
    return frame(CMD_ST_PL_TRACK, (*pack(index, 2), flags, *pack(color, 4), *encode_text(name)))


def playlist_count(count: int) -> bytes:
    return frame(CMD_ST_PL_COUNT, pack(count, 2))


def activity(command: int, first: int, levels: Sequence[float]) -> bytes:
    """Niveaux 0 → 1 de plusieurs canaux ou pistes consécutifs (commande 0x5A ou 0x5B)."""
    levels = list(levels)[:100]
    return frame(command, (*pack(first, 2), len(levels),
                           *(max(0, min(127, round(level * 127))) for level in levels)))


# --------------------------------------------------------------------------- #
# Décodage des messages de FL Studio
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StudioAck:
    version: int
    flags: int
    fl_version: str = ""


@dataclass(frozen=True)
class ProjectName:
    name: str


@dataclass(frozen=True)
class TransportState:
    playing: bool
    recording: bool
    song_mode: bool
    metronome: bool
    tempo: float
    ticks: int
    ppq: int
    beats_per_bar: int


@dataclass(frozen=True)
class ChannelInfo:
    index: int
    name: str
    color: str
    volume: float
    pan: float
    muted: bool
    solo: bool
    selected: bool
    kind: str


@dataclass(frozen=True)
class ChannelCount:
    count: int


@dataclass(frozen=True)
class StepsData:
    channel: int
    pattern: int
    steps: tuple[bool, ...]


@dataclass(frozen=True)
class PatternInfo:
    index: int
    name: str
    color: str
    length_steps: int
    selected: bool


@dataclass(frozen=True)
class PatternList:
    selected: int
    indices: tuple[int, ...]


@dataclass(frozen=True)
class PlaylistTrack:
    index: int
    name: str
    color: str
    muted: bool
    solo: bool
    selected: bool


@dataclass(frozen=True)
class PlaylistCount:
    count: int


@dataclass(frozen=True)
class Activity:
    target: str                                   # « tracks » ou « channels »
    first: int
    levels: tuple[float, ...]


@dataclass(frozen=True)
class FlGoodbye:
    """FL Studio se ferme (ou décharge le script)."""


StudioEvent = (StudioAck | ProjectName | TransportState | ChannelInfo | ChannelCount | StepsData
               | PatternInfo | PatternList | PlaylistTrack | PlaylistCount | Activity | FlGoodbye)


def decode(message: Frame) -> StudioEvent | None:
    """Message du script décodé ; None s'il est inconnu, trop court ou fait pour la synchronisation du mixer."""
    cmd, data = message.command, bytes(message.data)
    try:
        if cmd == CMD_ST_BYE_FROM_FL:
            return FlGoodbye()
        if cmd == CMD_ST_ACK and len(data) >= 2:
            return StudioAck(data[0], data[1], decode_text(data[2:]))
        if cmd == CMD_ST_PROJECT:
            return ProjectName(decode_text(data))
        if cmd == CMD_ST_STATE and len(data) >= 11:
            flags = data[0]
            return TransportState(bool(flags & STATE_PLAYING), bool(flags & STATE_RECORDING),
                                  bool(flags & STATE_SONG), bool(flags & STATE_METRONOME),
                                  unpack(data[1:4]) / 1000.0, unpack(data[4:8]), unpack(data[8:10]), data[10])
        if cmd == CMD_ST_CHANNEL and len(data) >= 12:
            flags = data[2]
            return ChannelInfo(unpack(data[0:2]), decode_text(data[12:]), fl_color_to_hex(unpack(data[8:12])),
                               from_unit14(data[4:6]), from_pan14(data[6:8]),
                               bool(flags & FLAG_MUTE), bool(flags & FLAG_SOLO), bool(flags & FLAG_SELECTED),
                               CHANNEL_KINDS.get(data[3], "sampler"))
        if cmd == CMD_ST_CHANNEL_COUNT and len(data) >= 2:
            return ChannelCount(unpack(data[0:2]))
        if cmd == CMD_ST_STEPS and len(data) >= 5:
            count = data[4]
            bits = data[5:]
            cells = tuple(bool(bits[i // 7] >> (i % 7) & 1) if i // 7 < len(bits) else False
                          for i in range(count))
            return StepsData(unpack(data[0:2]), unpack(data[2:4]), cells)
        if cmd == CMD_ST_PATTERN and len(data) >= 9:
            return PatternInfo(unpack(data[0:2]), decode_text(data[9:]), fl_color_to_hex(unpack(data[3:7])),
                               unpack(data[7:9]), bool(data[2] & PATTERN_SELECTED))
        if cmd == CMD_ST_PATTERN_LIST and len(data) >= 3:
            count = min(data[2], (len(data) - 3) // 2)
            return PatternList(unpack(data[0:2]), tuple(unpack(data[3 + 2 * i:5 + 2 * i]) for i in range(count)))
        if cmd == CMD_ST_PL_TRACK and len(data) >= 7:
            flags = data[2]
            return PlaylistTrack(unpack(data[0:2]), decode_text(data[7:]), fl_color_to_hex(unpack(data[3:7])),
                                 bool(flags & FLAG_MUTE), bool(flags & FLAG_SOLO), bool(flags & FLAG_SELECTED))
        if cmd == CMD_ST_PL_COUNT and len(data) >= 2:
            return PlaylistCount(unpack(data[0:2]))
        if cmd in (CMD_ST_PL_ACTIVITY, CMD_ST_CH_ACTIVITY) and len(data) >= 3:
            count = min(data[2], len(data) - 3)
            return Activity("tracks" if cmd == CMD_ST_PL_ACTIVITY else "channels", unpack(data[0:2]),
                            tuple(data[3 + i] / 127.0 for i in range(count)))
    except IndexError:
        return None
    return None

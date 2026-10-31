"""
Synchronisation du mixer de FL Studio : faders, muet, solo et panoramique (sans Qt).

FL Studio n'a pas d'API réseau ; il a des scripts MIDI (Python) qui lisent et
écrivent son mixer. VoiceConnector installe un script de ce type
(assets/flstudio/device_VoiceConnector.py) et lui parle par MIDI SysEx, sur deux ports
MIDI virtuels (loopMIDI) :

    VoiceConnector ──► « VoiceConnector FL » ──► FL Studio (le script)
    VoiceConnector ◄── « VoiceConnector VC » ◄── FL Studio (le script)

Messages (SysEx, identifiant 0x7D = usage libre, puis « VC ») :

    F0 7D 56 43 <commande> <données…> F7        (octets de données sur 7 bits)

    VoiceConnector → FL                          FL → VoiceConnector
    0x01 HELLO  [version, complet]               0x03 ACK     [version, nb de pistes]
    0x0F BYE                                     0x20 PISTE   [piste, vol(2), pan(2), drapeaux, nom…]
    0x02 WATCH  [n, pistes…]                     0x21 NIVEAUX [(piste, gauche, droite)…]
    0x10 VOLUME [piste, vol(2)]   ┐
    0x11 PAN    [piste, pan(2)]   │ dans les deux sens : un réglage change
    0x12 MUET   [piste, 0|1]      │
    0x13 SOLO   [piste, 0|1]      ┘

Valeurs sur 14 bits (2 octets de 7 bits). Volume : 0 → 1 comme FL Studio (0,8 = 0 dB).
Panoramique : −1 → +1, centre = 8192. Niveaux : 7 bits, 0 = silence, 127 = 0 dB (échelle
en dB de −60 à 0). Un volume de FL Studio est converti en dB avec la loi de son
fader : dB = 58 × log10(volume / 0,8) (0,8 → 0 dB, 1,0 → +5,6 dB).

Ce module contient : l'encodage des messages, `FlSyncCore` (état des pistes, écritures
groupées, détection de la connexion), `FlStripSync` (liaison pistes ↔ tranches
Voicemeeter) et `MidoTransport` (le transport réel, mido + python-rtmidi, importé
seulement à l'ouverture : ce sont des dépendances facultatives).
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import queue
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from core import config
from core.mixer_model import SILENCE_DB, StripParams

log = logging.getLogger(__name__)

# --- protocole ---------------------------------------------------------------
MANUFACTURER_ID = 0x7D
SIGNATURE = (0x56, 0x43)                        # « VC »
PROTOCOL_VERSION = 1
CMD_HELLO, CMD_WATCH, CMD_ACK, CMD_BYE = 0x01, 0x02, 0x03, 0x0F
CMD_VOLUME, CMD_PAN, CMD_MUTE, CMD_SOLO = 0x10, 0x11, 0x12, 0x13
CMD_TRACK, CMD_METERS = 0x20, 0x21
MAX_TRACK = 126                                  # Master (0) + 125 inserts
MAX_WATCH = 32
MAX14 = 16383
PAN_CENTER = 8192
FLAG_MUTE, FLAG_SOLO = 0x01, 0x02
METER_FLOOR_DB = -60.0

FL_UNITY = 0.8                                   # position du fader de FL Studio à 0 dB
FL_SLOPE = 58.0                                  # dB = 58 × log10(volume / 0,8)
FL_MAX_DB = FL_SLOPE * math.log10(1.0 / FL_UNITY)    # ≈ +5,6 dB : le haut du fader

# --- réglages par défaut -----------------------------------------------------
DEFAULT_OUT_PORT = "VoiceConnector FL"           # VoiceConnector écrit ici, FL lit
DEFAULT_IN_PORT = "VoiceConnector VC"            # FL écrit ici, VoiceConnector lit
DEFAULT_ROLE_TRACKS = {"master": 0, "drums": 1, "synths": 2, "fx": 3, "mic": 4}
PING_S, RETRY_S, TIMEOUT_S = 3.0, 1.5, 12.0
ECHO_GUARD_S = 0.35                              # après une écriture, on garde notre valeur
GAIN_TOLERANCE_DB = 0.15
PAN_TOLERANCE = 0.01


# --------------------------------------------------------------------------- #
# Conversions
# --------------------------------------------------------------------------- #
def fl_volume_to_db(volume: float) -> float:
    """Position du fader de FL Studio (0 → 1, 0,8 = 0 dB) en dB."""
    if volume <= 1e-4:
        return SILENCE_DB
    return max(SILENCE_DB, FL_SLOPE * math.log10(volume / FL_UNITY))


def db_to_fl_volume(db: float) -> float:
    """dB en position de fader de FL Studio ; limité au haut du fader (+5,6 dB)."""
    if db <= -99.0:
        return 0.0
    return max(0.0, min(1.0, FL_UNITY * 10.0 ** (float(db) / FL_SLOPE)))


def to14(value: float) -> tuple[int, int]:
    """0 → 1 en deux octets de 7 bits (poids fort, poids faible)."""
    raw = max(0, min(MAX14, int(round(float(value) * MAX14))))
    return raw >> 7, raw & 0x7F


def from14(high: int, low: int) -> float:
    return (((high & 0x7F) << 7) | (low & 0x7F)) / MAX14


def pan_to14(pan: float) -> tuple[int, int]:
    pan = max(-1.0, min(1.0, float(pan)))
    raw = PAN_CENTER + int(round(pan * (8191 if pan >= 0 else 8192)))
    raw = max(0, min(MAX14, raw))
    return raw >> 7, raw & 0x7F


def pan_from14(high: int, low: int) -> float:
    raw = ((high & 0x7F) << 7) | (low & 0x7F)
    return max(-1.0, (raw - PAN_CENTER) / (8191 if raw >= PAN_CENTER else 8192))


def level_to7(peak: float) -> int:
    """Crête (amplitude, 0 → 1) en niveau 7 bits : 0 = silence, 127 = 0 dB."""
    if peak <= 1e-6:
        return 0
    db = max(METER_FLOOR_DB, min(0.0, 20.0 * math.log10(peak)))
    return int(round(127 * (db - METER_FLOOR_DB) / -METER_FLOOR_DB))


def level_from7(value: int) -> float:
    """Niveau 7 bits en dB (0 → silence)."""
    value = value & 0x7F
    return SILENCE_DB if value == 0 else METER_FLOOR_DB + (-METER_FLOOR_DB) * value / 127.0


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def frame(command: int, data: Iterable[int] = ()) -> bytes:
    """Message SysEx complet (F0 … F7)."""
    body = bytes(data)
    if not 0 <= command <= 0x7F or any(b > 0x7F for b in body):
        raise ValueError("octets de données sur 7 bits seulement")
    return bytes((0xF0, MANUFACTURER_ID, *SIGNATURE, command)) + body + b"\xF7"


@dataclass(frozen=True)
class Frame:
    command: int
    data: bytes


def parse(message: bytes | bytearray | Sequence[int]) -> Frame | None:
    """Décode un message SysEx de VoiceConnector (avec ou sans F0 / F7) ; None sinon."""
    raw = bytes(message)
    if raw[:1] == b"\xF0":
        raw = raw[1:]
    if raw[-1:] == b"\xF7":
        raw = raw[:-1]
    if len(raw) < 4 or raw[0] != MANUFACTURER_ID or tuple(raw[1:3]) != SIGNATURE:
        return None
    if any(b > 0x7F for b in raw):
        return None
    return Frame(raw[3], raw[4:])


def hello(full: bool = True) -> bytes:
    return frame(CMD_HELLO, (PROTOCOL_VERSION, 1 if full else 0))


def bye() -> bytes:
    return frame(CMD_BYE)


def watch(tracks: Iterable[int]) -> bytes:
    unique = sorted({t for t in tracks if 0 <= t <= MAX_TRACK})[:MAX_WATCH]
    return frame(CMD_WATCH, (len(unique), *unique))


def set_volume(track: int, volume: float) -> bytes:
    return frame(CMD_VOLUME, (track, *to14(volume)))


def set_pan(track: int, pan: float) -> bytes:
    return frame(CMD_PAN, (track, *pan_to14(pan)))


def set_mute(track: int, muted: bool) -> bytes:
    return frame(CMD_MUTE, (track, 1 if muted else 0))


def set_solo(track: int, solo: bool) -> bytes:
    return frame(CMD_SOLO, (track, 1 if solo else 0))


def ack(track_count: int) -> bytes:
    return frame(CMD_ACK, (PROTOCOL_VERSION, max(0, min(0x7F, track_count))))


def track_dump(track: int, volume: float, pan: float, muted: bool, solo: bool, name: str = "") -> bytes:
    flags = (FLAG_MUTE if muted else 0) | (FLAG_SOLO if solo else 0)
    text = name.encode("ascii", "replace")[:24]
    return frame(CMD_TRACK, (track, *to14(volume), *pan_to14(pan), flags, *text))


def meters(entries: Iterable[tuple[int, float, float]]) -> bytes:
    """Niveaux (piste, crête gauche, crête droite) en amplitude."""
    data: list[int] = []
    for track, left, right in entries:
        data += [track, level_to7(left), level_to7(right)]
    return frame(CMD_METERS, data)


@dataclass(frozen=True)
class Ack:
    version: int
    tracks: int


@dataclass(frozen=True)
class TrackUpdate:
    """Ce que FL Studio vient de dire d'une piste ; None = champ non concerné."""

    track: int
    volume: float | None = None
    pan: float | None = None
    muted: bool | None = None
    solo: bool | None = None
    name: str | None = None


@dataclass(frozen=True)
class Meters:
    entries: tuple[tuple[int, float, float], ...]       # (piste, gauche dB, droite dB)


@dataclass(frozen=True)
class Goodbye:
    """FL Studio se ferme (ou décharge le script)."""


def decode_from_fl(message: Frame) -> Ack | TrackUpdate | Meters | Goodbye | None:
    """Message de FL Studio décodé ; None s'il est inconnu ou mal formé."""
    data, cmd = message.data, message.command
    try:
        if cmd == CMD_BYE:
            return Goodbye()
        if cmd == CMD_ACK and len(data) >= 2:
            return Ack(data[0], data[1])
        if cmd == CMD_TRACK and len(data) >= 6:
            name = bytes(data[6:]).decode("ascii", "replace")
            return TrackUpdate(data[0], from14(data[1], data[2]), pan_from14(data[3], data[4]),
                               bool(data[5] & FLAG_MUTE), bool(data[5] & FLAG_SOLO), name)
        if cmd == CMD_VOLUME and len(data) >= 3:
            return TrackUpdate(data[0], volume=from14(data[1], data[2]))
        if cmd == CMD_PAN and len(data) >= 3:
            return TrackUpdate(data[0], pan=pan_from14(data[1], data[2]))
        if cmd == CMD_MUTE and len(data) >= 2:
            return TrackUpdate(data[0], muted=bool(data[1]))
        if cmd == CMD_SOLO and len(data) >= 2:
            return TrackUpdate(data[0], solo=bool(data[1]))
        if cmd == CMD_METERS and len(data) >= 3:
            usable = len(data) - len(data) % 3
            return Meters(tuple((data[i], level_from7(data[i + 1]), level_from7(data[i + 2]))
                                for i in range(0, usable, 3)))
    except IndexError:
        return None
    return None


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class Transport(Protocol):
    """Canal MIDI SysEx vers FL Studio (réel : MidoTransport ; tests : un faux)."""

    is_open: bool

    def send(self, data: bytes) -> None: ...

    def receive(self) -> list[bytes]:
        """Messages reçus depuis le dernier appel (sans attendre)."""
        ...

    def close(self) -> None: ...


class TransportError(RuntimeError):
    """Ports MIDI indisponibles (mido absent, ports loopMIDI non créés…)."""


def find_port(names: Sequence[str], wanted: str) -> str | None:
    """Port dont le nom contient `wanted` (loopMIDI ajoute un numéro au nom)."""
    wanted = wanted.strip().lower()
    if not wanted:
        return None
    exact = [n for n in names if n.lower() == wanted]
    if exact:
        return exact[0]
    return next((n for n in names if wanted in n.lower()), None)


class MidoTransport:
    """Transport réel : mido + python-rtmidi (dépendances facultatives)."""

    def __init__(self, out_port: str = DEFAULT_OUT_PORT, in_port: str = DEFAULT_IN_PORT) -> None:
        try:
            import mido
        except Exception as exc:
            raise TransportError("Le module « mido » n'est pas installé (pip install mido "
                                 f"python-rtmidi) : {exc}") from exc
        self._mido = mido
        self.is_open = False
        self._queue: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        try:
            out_name = find_port(mido.get_output_names(), out_port)
            in_name = find_port(mido.get_input_names(), in_port)
        except Exception as exc:
            raise TransportError(f"Ports MIDI illisibles : {exc}") from exc
        if out_name is None or in_name is None:
            missing = " et ".join(n for n, found in ((f"« {out_port} »", out_name),
                                                     (f"« {in_port} »", in_name)) if found is None)
            raise TransportError(f"Port MIDI virtuel introuvable : {missing}. Crée-le avec loopMIDI.")
        try:
            self._out = mido.open_output(out_name)
            self._in = mido.open_input(in_name, callback=self._on_message)
        except Exception as exc:
            raise TransportError(f"Ouverture des ports MIDI impossible : {exc}") from exc
        self.is_open = True
        self.out_name, self.in_name = out_name, in_name
        log.info("Synchronisation FL Studio : écriture sur « %s », lecture sur « %s ».",
                 out_name, in_name)

    def _on_message(self, message) -> None:          # thread de python-rtmidi
        if message.type == "sysex":
            self._queue.put(bytes((0xF0, *message.data, 0xF7)))

    def send(self, data: bytes) -> None:
        if not self.is_open:
            return
        payload = list(data)
        if payload[:1] == [0xF0]:
            payload = payload[1:]
        if payload[-1:] == [0xF7]:
            payload = payload[:-1]
        self._out.send(self._mido.Message("sysex", data=payload))

    def receive(self) -> list[bytes]:
        messages = []
        while True:
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                return messages

    def close(self) -> None:
        self.is_open = False
        for port in (getattr(self, "_in", None), getattr(self, "_out", None)):
            try:
                if port is not None:
                    port.close()
            except Exception as exc:
                log.debug("Fermeture d'un port MIDI : %s", exc)


def mido_available() -> bool:
    """La bibliothèque MIDI (mido + python-rtmidi, facultatives) est-elle installée ?
    Sans elle, la synchronisation du mixer de FL Studio n'est pas proposée."""
    from importlib.util import find_spec
    try:
        return find_spec("mido") is not None and find_spec("rtmidi") is not None
    except (ImportError, ValueError):
        return False


def open_transport(cfg: Mapping) -> MidoTransport:
    """Transport d'après la config (flsync.out_port / flsync.in_port)."""
    section = cfg.get("flsync", {}) if isinstance(cfg.get("flsync"), dict) else {}
    return MidoTransport(str(section.get("out_port") or DEFAULT_OUT_PORT),
                         str(section.get("in_port") or DEFAULT_IN_PORT))


# --------------------------------------------------------------------------- #
# Cœur : état des pistes et dialogue avec le script
# --------------------------------------------------------------------------- #
@dataclass
class FlTrackState:
    volume: float = FL_UNITY        # échelle FL (0,8 = 0 dB)
    pan: float = 0.0                # −1 → +1
    muted: bool = False
    solo: bool = False
    name: str = ""
    peak_db: tuple[float, float] = (SILENCE_DB, SILENCE_DB)
    known: bool = False             # FL Studio a déjà donné la valeur de cette piste

    @property
    def gain_db(self) -> float:
        return fl_volume_to_db(self.volume)


@dataclass(frozen=True)
class PollResult:
    changed: dict[int, frozenset[str]] = field(default_factory=dict)   # piste → champs changés par FL
    connection_changed: bool = False


FIELDS = ("volume", "pan", "mute", "solo", "name")
QUANTUM = 1.5 / MAX14                            # écart de quantification d'une valeur sur 14 bits


def _same(old, new) -> bool:
    """Valeurs égales ; les nombres à un pas de quantification près (14 bits) sont égaux."""
    if isinstance(old, float) and isinstance(new, float):
        return abs(old - new) <= QUANTUM
    return old == new


class FlSyncCore:
    """Dialogue avec le script de FL Studio. Appelé régulièrement par `poll()` /
    `flush()` (QTimer dans l'interface) ; aucune attente, aucun thread."""

    def __init__(self, transport: Transport, *, watch: Iterable[int] = (0, 1, 2, 3, 4),
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._transport = transport
        self._clock = clock
        self._watch = tuple(sorted({t for t in watch if 0 <= t <= MAX_TRACK}))[:MAX_WATCH]
        self._tracks: dict[int, FlTrackState] = {}
        self._pending: dict[tuple[int, str], bytes] = {}
        self._guard: dict[tuple[int, str], float] = {}
        self.connected = False
        self.track_count = 0
        self._last_rx = 0.0
        self._next_hello = 0.0
        self._started = False

    # ------------------------------------------------------------ cycle de vie
    @property
    def watched(self) -> tuple[int, ...]:
        return self._watch

    def start(self) -> None:
        self._started = True
        self._next_hello = 0.0

    def stop(self) -> None:
        if self._started and self._transport.is_open:
            try:
                self._transport.send(bye())
            except Exception as exc:
                log.debug("BYE non envoyé : %s", exc)
        self._started = False
        self.connected = False

    def close(self) -> None:
        """Dit au revoir à FL Studio et ferme les ports MIDI (appelé une fois, à la fin)."""
        self.stop()
        try:
            self._transport.close()
        except Exception as exc:
            log.debug("Fermeture du transport MIDI : %s", exc)

    def set_watch(self, tracks: Iterable[int]) -> None:
        """Pistes à suivre (les autres ne sont pas relues par FL Studio)."""
        self._watch = tuple(sorted({t for t in tracks if 0 <= t <= MAX_TRACK}))[:MAX_WATCH]
        if self.connected:
            self._safe_send(watch(self._watch))

    def state(self, track: int) -> FlTrackState:
        state = self._tracks.get(track)
        if state is None:
            state = self._tracks[track] = FlTrackState()
        return state

    # ------------------------------------------------------------- VoiceConnector → FL
    def _write(self, track: int, field_name: str, message: bytes) -> None:
        key = (track, field_name)
        self._pending[key] = message                  # seule la dernière valeur compte
        self._guard[key] = self._clock() + ECHO_GUARD_S

    def write_volume(self, track: int, volume: float) -> None:
        volume = max(0.0, min(1.0, float(volume)))
        self.state(track).volume = volume
        self._write(track, "volume", set_volume(track, volume))

    def write_pan(self, track: int, pan: float) -> None:
        pan = max(-1.0, min(1.0, float(pan)))
        self.state(track).pan = pan
        self._write(track, "pan", set_pan(track, pan))

    def write_mute(self, track: int, muted: bool) -> None:
        self.state(track).muted = bool(muted)
        self._write(track, "mute", set_mute(track, muted))

    def write_solo(self, track: int, solo: bool) -> None:
        self.state(track).solo = bool(solo)
        self._write(track, "solo", set_solo(track, solo))

    def flush(self) -> int:
        """Envoie les écritures en attente (connecté seulement) ; renvoie leur nombre."""
        if not self.connected or not self._pending:
            if not self.connected:
                self._pending.clear()       # FL Studio fait foi à la connexion : rien à rejouer
            return 0
        pending, self._pending = self._pending, {}
        for message in pending.values():
            self._safe_send(message)
        return len(pending)

    # ------------------------------------------------------------- FL → VoiceConnector
    def poll(self) -> PollResult:
        """Lit ce que FL Studio a envoyé, gère la connexion ; renvoie les changements venus de FL."""
        now = self._clock()
        changed: dict[int, set[str]] = {}
        was_connected = self.connected
        try:
            incoming = self._transport.receive()
        except Exception as exc:
            log.debug("Lecture MIDI impossible : %s", exc)
            incoming = []
        for raw in incoming:
            message = parse(raw)
            event = decode_from_fl(message) if message is not None else None
            if event is None:
                continue
            self._last_rx = now
            self._handle(event, now, changed)
        if self._started:
            self._keep_alive(now)
        return PollResult({t: frozenset(f) for t, f in changed.items()}, self.connected != was_connected)

    def _handle(self, event, now: float, changed: dict[int, set[str]]) -> None:
        if isinstance(event, Goodbye):
            self.connected = False
            self._next_hello = now + RETRY_S
            for state in self._tracks.values():
                state.known = False
            return
        if isinstance(event, Ack):
            self.track_count = event.tracks
            if not self.connected:
                self.connected = True
                self._safe_send(watch(self._watch))
            return
        if isinstance(event, Meters):
            for track, left, right in event.entries:
                self.state(track).peak_db = (left, right)
            return
        track = event.track
        state = self.state(track)
        full = None not in (event.volume, event.pan, event.muted, event.solo)    # état complet d'une piste
        first = full and not state.known                 # première fois : tout est nouveau pour la console
        if full:
            state.known = True
        for field_name, attr, value in (("volume", "volume", event.volume), ("pan", "pan", event.pan),
                                        ("mute", "muted", event.muted), ("solo", "solo", event.solo),
                                        ("name", "name", event.name)):
            if value is None:
                continue
            if not first and _same(getattr(state, attr), value):
                continue
            if self._guard.get((track, field_name), 0.0) > now:
                continue                             # écho de notre propre écriture
            setattr(state, attr, value)
            changed.setdefault(track, set()).add(field_name)

    def _keep_alive(self, now: float) -> None:
        if self.connected:
            if now - self._last_rx > TIMEOUT_S:
                log.info("FL Studio ne répond plus (synchronisation interrompue).")
                self.connected = False
                self._next_hello = now
            elif now >= self._next_hello:
                self._safe_send(hello(full=False))
                self._next_hello = now + PING_S
        if not self.connected and now >= self._next_hello:
            self._safe_send(hello(full=True))
            self._next_hello = now + RETRY_S

    def _safe_send(self, message: bytes) -> None:
        try:
            self._transport.send(message)
        except Exception as exc:                     # port fermé, câble MIDI virtuel supprimé…
            log.debug("Envoi MIDI impossible : %s", exc)


# --------------------------------------------------------------------------- #
# Liaison pistes de FL Studio ↔ tranches de la console
# --------------------------------------------------------------------------- #
KIND_GAIN, KIND_MUTE, KIND_PAN, KIND_SOLO = "gain", "mute", "pan", "solo"
GAIN_RANGE_DB = (-60.0, 12.0)


@dataclass(frozen=True)
class StripCommand:
    """Ordre à appliquer à une tranche de la console (gain en dB, muet, pan −0,5 → 0,5, solo)."""

    kind: str
    strip: int
    value: float | bool


def role_tracks(cfg: Mapping | None, roles: Mapping[str, int]) -> dict[int, int]:
    """Tranche → piste FL : par rôle, d'après flsync.tracks (sinon la convention du
    gabarit : Master = 0, Bus Batterie = 1, Synths = 2, FX = 3, Micro = 4)."""
    section = (cfg or {}).get("flsync", {})
    custom = section.get("tracks", {}) if isinstance(section, dict) else {}
    table = dict(DEFAULT_ROLE_TRACKS)
    if isinstance(custom, dict):
        for role, track in custom.items():
            try:
                number = int(track)
            except (TypeError, ValueError):
                continue
            if 0 <= number <= MAX_TRACK:
                table[str(role)] = number
    links: dict[int, int] = {}
    for role, strip in roles.items():
        if role in table and table[role] not in links.values():
            links[strip] = table[role]
    return links


class FlStripSync:
    """Fait suivre aux tranches de la console les pistes de FL Studio, et inversement.

    Sans état propre : tout se décide en comparant la tranche à l'état de la
    piste, donc aucun aller-retour infini (une valeur déjà égale n'est jamais renvoyée)."""

    def __init__(self, core: FlSyncCore, links: Mapping[int, int]) -> None:
        self.core = core
        self.links = dict(links)                      # tranche → piste
        self._by_track = {track: strip for strip, track in self.links.items()}
        core.set_watch(self.links.values())

    @property
    def tracks(self) -> tuple[int, ...]:
        return tuple(sorted(self.links.values()))

    # ---- console → FL Studio
    def push(self, strips: Sequence[StripParams]) -> int:
        """Écrit dans FL Studio ce qui diffère ; renvoie le nombre d'écritures."""
        if not self.core.connected:
            return 0
        written = 0
        for strip, track in self.links.items():
            if strip >= len(strips):
                continue
            params, state = strips[strip], self.core.state(track)
            if not state.known:
                continue
            target = min(params.gain, FL_MAX_DB)
            # Le fader de la console s'arrête à −60 dB : une piste coupée net (−∞) y est déjà
            current = max(GAIN_RANGE_DB[0], state.gain_db)
            if abs(target - current) > GAIN_TOLERANCE_DB:
                self.core.write_volume(track, db_to_fl_volume(params.gain))
                written += 1
            if params.mute != state.muted:
                self.core.write_mute(track, params.mute)
                written += 1
            pad = params.pad("pan")
            if pad is not None:
                pan = max(-1.0, min(1.0, pad[0] * 2.0))
                if abs(pan - state.pan) > PAN_TOLERANCE:
                    self.core.write_pan(track, pan)
                    written += 1
        return written

    def push_solo(self, strip: int, solo: bool) -> None:
        track = self.links.get(strip)
        if track is not None and self.core.connected:
            self.core.write_solo(track, solo)

    # ---- FL Studio → console
    def pull(self, changed: Mapping[int, Iterable[str]]) -> list[StripCommand]:
        """Ordres pour la console d'après ce que FL Studio vient de changer."""
        commands: list[StripCommand] = []
        for track, fields in changed.items():
            strip = self._by_track.get(track)
            if strip is None:
                continue
            state = self.core.state(track)
            fields = set(fields)
            if "volume" in fields:
                db = max(GAIN_RANGE_DB[0], min(GAIN_RANGE_DB[1], state.gain_db))
                commands.append(StripCommand(KIND_GAIN, strip, round(db, 1)))
            if "mute" in fields:
                commands.append(StripCommand(KIND_MUTE, strip, state.muted))
            if "pan" in fields:
                commands.append(StripCommand(KIND_PAN, strip, round(state.pan / 2.0, 3)))
            if "solo" in fields:
                commands.append(StripCommand(KIND_SOLO, strip, state.solo))
        return commands

    def snapshot(self) -> list[StripCommand]:
        """Tout l'état connu des pistes liées (à la connexion : FL Studio fait foi)."""
        every = {track: FIELDS for track in self.links.values() if self.core.state(track).known}
        return self.pull(every)


def connect_fl(cfg: Mapping, watch: Iterable[int] = DEFAULT_ROLE_TRACKS.values()
               ) -> tuple[FlSyncCore | None, str]:
    """Ouvre les ports MIDI et lance la synchronisation : (cœur, « ») ou (None, raison).

    Ne lève jamais : sans mido, sans loopMIDI ou sans script dans FL Studio, la console
    marche comme avant, et la raison est affichée à l'utilisateur."""
    try:
        transport = open_transport(cfg)
    except TransportError as exc:
        log.info("Synchronisation FL Studio indisponible : %s", exc)
        return None, str(exc)
    except Exception as exc:                     # bibliothèque MIDI défaillante
        log.warning("Synchronisation FL Studio : ports MIDI inutilisables : %s", exc)
        return None, f"Ports MIDI inutilisables : {exc}"
    core = FlSyncCore(transport, watch=watch)
    core.start()
    return core, ""


# --------------------------------------------------------------------------- #
# Installation du script dans FL Studio
# --------------------------------------------------------------------------- #
SCRIPT_NAME = "device_VoiceConnector.py"
SCRIPT_RESOURCE = "assets/flstudio/" + SCRIPT_NAME
HARDWARE_PARTS = ("Image-Line", "FL Studio", "Settings", "Hardware", "VoiceConnector")
SCRIPT_MISSING, SCRIPT_OUTDATED, SCRIPT_CURRENT = "missing", "outdated", "current"


def documents_dir() -> Path:
    """Dossier Documents de l'utilisateur (Windows : celui du registre, qui suit OneDrive)."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
            return Path(os.path.expandvars(str(value)))
        except OSError:
            pass
    return Path.home() / "Documents"


def script_dir(documents: Path | None = None) -> Path:
    """Dossier où FL Studio cherche les scripts MIDI de VoiceConnector."""
    return Path(documents or documents_dir()).joinpath(*HARDWARE_PARTS)


def script_status(documents: Path | None = None, source: Path | None = None) -> tuple[str, Path]:
    """(état, chemin) du script installé : absent, périmé (autre contenu) ou à jour."""
    target = script_dir(documents) / SCRIPT_NAME
    if not target.is_file():
        return SCRIPT_MISSING, target
    origin = source or config.resource_path(SCRIPT_RESOURCE)
    try:
        same = target.read_bytes() == origin.read_bytes()
    except OSError:
        same = False
    return (SCRIPT_CURRENT if same else SCRIPT_OUTDATED), target


def install_script(documents: Path | None = None, source: Path | None = None) -> Path:
    """Copie le script dans le dossier de FL Studio (créé au besoin) ; renvoie son chemin.

    Lève OSError si la copie est impossible (dossier protégé, source absente)."""
    origin = source or config.resource_path(SCRIPT_RESOURCE)
    if not origin.is_file():
        raise FileNotFoundError(f"Script introuvable : {origin}")
    target = script_dir(documents) / SCRIPT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, target)
    log.info("Script FL Studio installé : %s", target)
    return target


# --------------------------------------------------------------------------- #
# Diagnostic de l'installation (Réglages → Mode et thème)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SetupCheck:
    ok: bool
    text: str


def fl_setup_checks(cfg: Mapping | None = None, *, documents: Path | None = None,
                    source: Path | None = None, mido_module: Any = None) -> list[SetupCheck]:
    """Ce qui est prêt, et ce qui manque, pour la synchronisation du mixer de FL Studio.

    Ne modifie rien et n'ouvre aucun port : bibliothèque MIDI, ports virtuels loopMIDI, script de
    FL Studio. `mido_module` : un module de remplacement (tests)."""
    section = (cfg or {}).get("flsync", {})
    section = section if isinstance(section, dict) else {}
    out_wanted = str(section.get("out_port") or DEFAULT_OUT_PORT)
    in_wanted = str(section.get("in_port") or DEFAULT_IN_PORT)
    checks: list[SetupCheck] = []

    library = mido_module
    if library is None and mido_available():
        try:
            library = importlib.import_module("mido")
        except Exception as exc:                                 # module cassé : même diagnostic que l'absence
            log.debug("mido inutilisable : %s", exc)
    if library is None:
        checks.append(SetupCheck(False, "Bibliothèque MIDI absente (mido et python-rtmidi) : « pip install mido "
                                        "python-rtmidi » ; elle est incluse dans VoiceConnector.exe."))
    else:
        checks.append(SetupCheck(True, "Bibliothèque MIDI (mido, python-rtmidi) : installée."))
        try:
            outputs, inputs = list(library.get_output_names()), list(library.get_input_names())
        except Exception as exc:
            checks.append(SetupCheck(False, f"Ports MIDI illisibles : {exc}"))
        else:
            for wanted, names, role in ((out_wanted, outputs, "écriture"), (in_wanted, inputs, "lecture")):
                found = find_port(names, wanted)
                checks.append(SetupCheck(found is not None, f"Port MIDI virtuel « {wanted} » ({role}) : "
                                         + (f"trouvé ({found})." if found else "introuvable, crée-le avec loopMIDI.")))

    status, target = script_status(documents, source)
    texts = {SCRIPT_CURRENT: f"Script FL Studio : installé et à jour ({target}).",
             SCRIPT_OUTDATED: "Script FL Studio : installé mais ancien, réinstalle-le.",
             SCRIPT_MISSING: "Script FL Studio : pas encore installé."}
    checks.append(SetupCheck(status == SCRIPT_CURRENT, texts[status]))
    return checks

# name=VoiceConnector
"""
Script MIDI de FL Studio pour VoiceConnector : synchronise le mixer de FL Studio (volume,
panoramique, muet, solo, niveaux) avec la console VoiceConnector, dans les deux sens.

Installation (VoiceConnector peut la faire pour toi : Réglages -> Layout et Apparence ->
« Installer le script FL Studio ») :

1. Copie ce fichier dans  Documents\\Image-Line\\FL Studio\\Settings\\Hardware\\VoiceConnector\\
   (le dossier doit s'appeler VoiceConnector).
2. Crée deux ports MIDI virtuels avec loopMIDI : « VoiceConnector FL » et « VoiceConnector VC ».
3. Dans FL Studio : Options -> MIDI settings.
   - Entrée : « VoiceConnector FL », active-la, Controller type = « VoiceConnector », Port = 1.
   - Sortie : « VoiceConnector VC », active-la, avec le MÊME numéro de port (1).

Protocole : voir core/fl_sync.py (SysEx F0 7D 56 43 <commande> <données> F7). Ce fichier
n'importe rien de VoiceConnector : FL Studio le charge seul.
"""

import math
import time

import device
import midi
import mixer

MANUFACTURER = 0x7D
SIGNATURE = (0x56, 0x43)
VERSION = 1

CMD_HELLO, CMD_WATCH, CMD_ACK, CMD_BYE = 0x01, 0x02, 0x03, 0x0F
CMD_VOLUME, CMD_PAN, CMD_MUTE, CMD_SOLO = 0x10, 0x11, 0x12, 0x13
CMD_TRACK, CMD_METERS = 0x20, 0x21

MAX14 = 16383
PAN_CENTER = 8192
MAX_TRACK = 126
METER_FLOOR_DB = -60.0
DEFAULT_WATCH = (0, 1, 2, 3, 4)
IDLE_PUSH_S = 0.12
METERS_EVERY_S = 0.05

_active = False                 # VoiceConnector a dit bonjour et n'est pas parti
_watch = list(DEFAULT_WATCH)
_cache = {}                     # piste -> (volume, pan, muté, solo) tel que VoiceConnector le connaît
_last_push = 0.0
_last_meters = 0.0


# --------------------------------------------------------------------------- #
# Codage (le même que core/fl_sync.py)
# --------------------------------------------------------------------------- #
def _to14(value):
    raw = max(0, min(MAX14, int(round(float(value) * MAX14))))
    return raw >> 7, raw & 0x7F


def _from14(high, low):
    return (((high & 0x7F) << 7) | (low & 0x7F)) / float(MAX14)


def _pan_to14(pan):
    pan = max(-1.0, min(1.0, float(pan)))
    raw = PAN_CENTER + int(round(pan * (8191 if pan >= 0 else 8192)))
    raw = max(0, min(MAX14, raw))
    return raw >> 7, raw & 0x7F


def _pan_from14(high, low):
    raw = ((high & 0x7F) << 7) | (low & 0x7F)
    return max(-1.0, (raw - PAN_CENTER) / float(8191 if raw >= PAN_CENTER else 8192))


def _level_to7(peak):
    if peak <= 1e-6:
        return 0
    db = max(METER_FLOOR_DB, min(0.0, 20.0 * math.log10(peak)))
    return int(round(127 * (db - METER_FLOOR_DB) / -METER_FLOOR_DB))


def _send(command, data=()):
    try:
        device.midiOutSysex(bytes((0xF0, MANUFACTURER) + SIGNATURE + (command,) + tuple(data) + (0xF7,)))
    except Exception:
        pass                     # port de sortie non configuré : rien à faire


# --------------------------------------------------------------------------- #
# Lecture / écriture du mixer de FL Studio
# --------------------------------------------------------------------------- #
def _track_count():
    try:
        return max(1, min(0x7F, int(mixer.trackCount())))
    except Exception:
        return 0x7F


def _read(track):
    """(volume, pan, muté, solo) de la piste, ou None si FL Studio ne la connaît pas."""
    try:
        return (float(mixer.getTrackVolume(track)), float(mixer.getTrackPan(track)),
                bool(mixer.isTrackMuted(track)), bool(mixer.isTrackSolo(track)))
    except Exception:
        return None


def _name(track):
    try:
        return str(mixer.getTrackName(track))
    except Exception:
        return ""


def _dump(track):
    """Envoie tout l'état d'une piste (et le retient)."""
    state = _read(track)
    if state is None:
        return
    _cache[track] = state
    volume, pan, muted, solo = state
    flags = (1 if muted else 0) | (2 if solo else 0)
    name = _name(track).encode("ascii", "replace")[:24]
    _send(CMD_TRACK, (track,) + _to14(volume) + _pan_to14(pan) + (flags,) + tuple(name))


def _push_changes():
    """Envoie à VoiceConnector ce qui a changé dans FL Studio depuis le dernier envoi."""
    for track in _watch:
        state = _read(track)
        if state is None:
            continue
        old = _cache.get(track)
        if old is None:
            _dump(track)
            continue
        if abs(state[0] - old[0]) > 1e-4:
            _send(CMD_VOLUME, (track,) + _to14(state[0]))
        if abs(state[1] - old[1]) > 1e-4:
            _send(CMD_PAN, (track,) + _pan_to14(state[1]))
        if state[2] != old[2]:
            _send(CMD_MUTE, (track, 1 if state[2] else 0))
        if state[3] != old[3]:
            _send(CMD_SOLO, (track, 1 if state[3] else 0))
        _cache[track] = state


def _set_volume(track, volume):
    try:
        mixer.setTrackVolume(track, max(0.0, min(1.0, volume)))
    except Exception:
        pass


def _set_pan(track, pan):
    try:
        mixer.setTrackPan(track, max(-1.0, min(1.0, pan)))
    except Exception:
        pass


def _set_mute(track, muted):
    try:
        if bool(mixer.isTrackMuted(track)) != muted:
            mixer.muteTrack(track)          # bascule
    except Exception:
        pass


def _set_solo(track, solo):
    try:
        if bool(mixer.isTrackSolo(track)) != solo:
            mixer.soloTrack(track)          # bascule
    except Exception:
        pass


def _remember(track):
    """Après une écriture de VoiceConnector : le retient pour ne pas la lui renvoyer."""
    state = _read(track)
    if state is not None:
        _cache[track] = state


# --------------------------------------------------------------------------- #
# Messages de VoiceConnector
# --------------------------------------------------------------------------- #
def _handle(command, data):
    global _active, _watch
    if command == CMD_HELLO:
        _active = True
        _send(CMD_ACK, (VERSION, _track_count()))
        if len(data) < 2 or data[1]:
            _cache.clear()
            for track in _watch:
                _dump(track)
    elif command == CMD_BYE:
        _active = False
    elif command == CMD_WATCH and data:
        count = min(data[0], len(data) - 1)
        tracks = sorted(set(t for t in data[1:1 + count] if t <= MAX_TRACK))
        _watch = tracks or list(DEFAULT_WATCH)
        _cache.clear()
        for track in _watch:
            _dump(track)
    elif command == CMD_VOLUME and len(data) >= 3:
        _set_volume(data[0], _from14(data[1], data[2]))
        _remember(data[0])
    elif command == CMD_PAN and len(data) >= 3:
        _set_pan(data[0], _pan_from14(data[1], data[2]))
        _remember(data[0])
    elif command == CMD_MUTE and len(data) >= 2:
        _set_mute(data[0], bool(data[1]))
        _remember(data[0])
    elif command == CMD_SOLO and len(data) >= 2:
        _set_solo(data[0], bool(data[1]))
        _remember(data[0])


# --------------------------------------------------------------------------- #
# Points d'entrée appelés par FL Studio
# --------------------------------------------------------------------------- #
def OnInit():  # noqa: N802
    global _active, _watch
    _active = False
    _watch = list(DEFAULT_WATCH)
    _cache.clear()


def OnDeInit():  # noqa: N802
    global _active
    if _active:
        _send(CMD_BYE)          # FL Studio se ferme (ou le script est déchargé) : VoiceConnector le sait tout de suite
    _active = False


def OnSysEx(event):  # noqa: N802
    raw = bytes(event.sysex)
    if raw[:1] == b"\xF0":
        raw = raw[1:]
    if raw[-1:] == b"\xF7":
        raw = raw[:-1]
    if len(raw) < 4 or raw[0] != MANUFACTURER or tuple(raw[1:3]) != SIGNATURE:
        return
    event.handled = True
    _handle(raw[3], tuple(raw[4:]))


def OnRefresh(flags):  # noqa: N802
    if _active:
        _push_changes()


def OnIdle():  # noqa: N802
    """Filet de sécurité : certains changements du mixer n'appellent pas OnRefresh."""
    global _last_push
    if not _active:
        return
    now = time.monotonic()
    if now - _last_push >= IDLE_PUSH_S:
        _last_push = now
        _push_changes()


def OnUpdateMeters():  # noqa: N802
    global _last_meters
    if not _active:
        return
    now = time.monotonic()
    if now - _last_meters < METERS_EVERY_S:
        return
    _last_meters = now
    data = []
    left_mode = getattr(midi, "PEAK_L", 0)
    right_mode = getattr(midi, "PEAK_R", 1)
    for track in _watch:
        try:
            left = float(mixer.getTrackPeaks(track, left_mode))
            right = float(mixer.getTrackPeaks(track, right_mode))
        except Exception:
            continue
        data += [track, _level_to7(left), _level_to7(right)]
    if data:
        _send(CMD_METERS, data)

# name=VoiceConnector
"""
Script MIDI de FL Studio pour VoiceConnector. Il fait deux choses, dans les deux sens :

- synchroniser le mixer de FL Studio (volume, panoramique, muet, solo, niveaux) avec la console
  VoiceConnector ;
- relier le Studio de VoiceConnector à FL Studio : transport et tempo, channel rack (canaux,
  pas du séquenceur), patterns et pistes de la playlist.

Installation (VoiceConnector peut la faire pour toi : Réglages -> Layout et Apparence ->
« Installer le script FL Studio ») :

1. Copie ce fichier dans  Documents\\Image-Line\\FL Studio\\Settings\\Hardware\\VoiceConnector\\
   (le dossier doit s'appeler VoiceConnector).
2. Crée deux ports MIDI virtuels avec loopMIDI : « VoiceConnector FL » et « VoiceConnector VC ».
3. Dans FL Studio : Options -> MIDI settings.
   - Entrée : « VoiceConnector FL », active-la, Controller type = « VoiceConnector », Port = 1.
   - Sortie : « VoiceConnector VC », active-la, avec le MÊME numéro de port (1).

Protocole : voir core/fl_sync.py (mixer) et core/studio_proto.py (Studio). Messages SysEx
F0 7D 56 43 <commande> <données> F7. Ce fichier n'importe rien de VoiceConnector : FL Studio le
charge seul. Aucune erreur ne remonte à FL Studio : un appel d'API qui échoue est simplement ignoré
(la première erreur de chaque endroit est écrite dans la sortie des scripts de FL Studio).
"""

import math
import time

import device
import midi
import mixer

try:                                        # modules de FL Studio utilisés par le Studio
    import channels
    import general
    import patterns
    import playlist
    import transport
    import ui
except ImportError:                         # ancienne version de FL Studio : le mixer seul fonctionne
    channels = general = patterns = playlist = transport = ui = None

MANUFACTURER = 0x7D
SIGNATURE = (0x56, 0x43)
VERSION = 1

CMD_HELLO, CMD_WATCH, CMD_ACK, CMD_BYE = 0x01, 0x02, 0x03, 0x0F
CMD_VOLUME, CMD_PAN, CMD_MUTE, CMD_SOLO = 0x10, 0x11, 0x12, 0x13
CMD_TRACK, CMD_METERS = 0x20, 0x21

# Studio (voir core/studio_proto.py)
STUDIO_VERSION = 1
ST_HELLO, ST_BYE, ST_REFRESH, ST_STEP_COUNT = 0x30, 0x31, 0x32, 0x33
ST_TRANSPORT, ST_TEMPO, ST_SEEK, ST_STEP = 0x34, 0x35, 0x36, 0x37
ST_CH_VOLUME, ST_CH_PAN, ST_CH_MUTE, ST_CH_SOLO = 0x38, 0x39, 0x3A, 0x3B
ST_CH_SELECT, ST_PATTERN_SELECT, ST_PL_MUTE, ST_PL_SOLO = 0x3C, 0x3D, 0x3E, 0x3F
ST_ACK, ST_PROJECT, ST_STATE, ST_CHANNEL, ST_CHANNEL_COUNT, ST_STEPS = 0x50, 0x51, 0x52, 0x53, 0x54, 0x55
ST_PATTERN, ST_PATTERN_LIST, ST_PL_TRACK, ST_PL_COUNT = 0x56, 0x57, 0x58, 0x59
ST_PL_ACTIVITY, ST_CH_ACTIVITY = 0x5A, 0x5B
ACTION_PLAY, ACTION_STOP, ACTION_RECORD, ACTION_SONG_MODE, ACTION_METRONOME, ACTION_REWIND = 1, 2, 3, 4, 5, 6
SCOPE_TRANSPORT, SCOPE_RACK, SCOPE_STEPS, SCOPE_PATTERNS, SCOPE_PLAYLIST, SCOPE_ALL = 1, 2, 4, 8, 16, 31
_SCOPE_OF = {"state": 1, "project": 1, "channel_count": 2, "channel": 2, "ch_activity": 2, "ch_activity_any": 2,
             "steps": 4, "pattern_list": 8, "pattern": 8, "playlist_count": 16, "track": 16, "pl_activity": 16,
             "pl_activity_any": 16}

MAX14 = 16383
PAN_CENTER = 8192
MAX_TRACK = 126
METER_FLOOR_DB = -60.0
DEFAULT_WATCH = (0, 1, 2, 3, 4)
IDLE_PUSH_S = 0.12
METERS_EVERY_S = 0.05

STATE_EVERY_PLAYING_S = 0.05                # position envoyée 20 fois par seconde pendant la lecture
STATE_EVERY_IDLE_S = 0.25                   # à l'arrêt : un changement est repris en 1/4 de seconde
STATE_HEARTBEAT_S = 1.0                     # et au moins une fois par seconde (VoiceConnector le sait vivant)
PATTERNS_EVERY_S = 0.5
PLAYLIST_EVERY_S = 0.4
ACTIVITY_EVERY_S = 0.06
PROJECT_EVERY_S = 1.0
RACK_CYCLE_S = 0.4                          # relecture complète du channel rack (les canaux, un peu à chaque OnIdle)
STEPS_CYCLE_S = 0.5                         # relecture complète des pas du séquenceur
RACK_PER_TICK = 4                           # jamais plus de canaux relus à chaque OnIdle (~20 ms)
STEP_ROWS_PER_TICK = 2                      # ni de lignes de pas
ACTIVITY_IDLE_S = 0.25                      # activité des canaux à l'arrêt (16 fois par seconde en lecture)
MIN_PLAYLIST_TRACKS = 8
MAX_STEP = 64

_active = False                 # VoiceConnector a dit bonjour (mixer) et n'est pas parti
_watch = list(DEFAULT_WATCH)
_cache = {}                     # piste -> (volume, pan, muté, solo) tel que VoiceConnector le connaît
_last_push = 0.0
_last_meters = 0.0

_studio = False                 # le Studio a dit bonjour et n'est pas parti
_st_steps = 16
_st_rack_limit = 64
_st_pl_limit = 32
_GLOBAL = False                 # numéros de canaux globaux (API 33 et plus)
_errors = set()
_sent = {}                      # ce que VoiceConnector sait déjà du Studio (clé -> valeur envoyée)
_clock = {}                     # dernière heure de chaque travail périodique
_force = {"all": False, "rack": False, "steps": False}
_cursor = {"rack": 0, "steps": 0}


def _fail(where, exc):
    """Ne fait jamais remonter d'erreur à FL Studio ; écrit la première de chaque endroit."""
    if where not in _errors:
        _errors.add(where)
        try:
            print("VoiceConnector (%s) : %s" % (where, exc))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Codage (le même que core/fl_sync.py et core/studio_proto.py)
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


def _pack(value, count):
    """Entier en `count` groupes de 7 bits, poids fort d'abord."""
    value = max(0, min((1 << (7 * count)) - 1, int(value)))
    return tuple((value >> (7 * shift)) & 0x7F for shift in range(count - 1, -1, -1))


def _unpack(data):
    result = 0
    for byte in data:
        result = (result << 7) | (byte & 0x7F)
    return result


def _text(value, limit=24):
    """Texte en octets de 7 bits : ASCII visible tel quel, le reste en 0x7F + code Unicode (3 octets)."""
    out = []
    for char in str(value)[:limit]:
        code = ord(char)
        if 0x20 <= code < 0x7F:
            out.append(code)
        elif code >= 0x7F:
            out.append(0x7F)
            out.extend(_pack(code, 3))
    return tuple(out)


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
# Studio : lecture du transport, du channel rack, des patterns et de la playlist
# --------------------------------------------------------------------------- #
def _studio_ready():
    return channels is not None and transport is not None and patterns is not None and playlist is not None


def _count_channels():
    try:
        return max(0, int(channels.channelCount(True) if _GLOBAL else channels.channelCount()))
    except Exception as exc:
        _fail("channelCount", exc)
        return 0


def _read_channel(i):
    """(nom, couleur, volume, pan, muet, solo, sélectionné, type) d'un canal, ou None."""
    try:
        if _GLOBAL:
            info = (str(channels.getChannelName(i, True)), int(channels.getChannelColor(i, True)),
                    float(channels.getChannelVolume(i, False, True)), float(channels.getChannelPan(i, True)),
                    bool(channels.isChannelMuted(i, True)), bool(channels.isChannelSolo(i, True)),
                    bool(channels.isChannelSelected(i, True)))
        else:
            info = (str(channels.getChannelName(i)), int(channels.getChannelColor(i)),
                    float(channels.getChannelVolume(i)), float(channels.getChannelPan(i)),
                    bool(channels.isChannelMuted(i)), bool(channels.isChannelSolo(i)),
                    bool(channels.isChannelSelected(i)))
    except Exception as exc:
        _fail("canal", exc)
        return None
    kind = 0                                    # getChannelType n'existe qu'à partir de l'API 19
    try:
        kind = int(channels.getChannelType(i, True) if _GLOBAL else channels.getChannelType(i))
    except Exception:
        pass
    return info + (kind,)


def _read_steps(i, count):
    try:
        if _GLOBAL:
            return tuple(bool(channels.getGridBit(i, k, True)) for k in range(count))
        return tuple(bool(channels.getGridBit(i, k)) for k in range(count))
    except Exception as exc:
        _fail("pas", exc)
        return None


def _read_activity(i):
    try:
        return float(channels.getActivityLevel(i, True) if _GLOBAL else channels.getActivityLevel(i))
    except Exception:
        return 0.0


def _project_name():
    try:
        title = str(ui.getProgTitle())
    except Exception:
        return ""
    cut = title.lower().rfind(" - fl studio")
    if cut > 0:
        return title[:cut].strip()
    return "" if title.lower().startswith("fl studio") else title.strip()      # « FL Studio 21 » : aucun projet nommé


def _read_transport():
    """(lecture, enregistrement, morceau, métronome, tempo, ticks, ppq, temps par mesure)."""
    playing = bool(transport.isPlaying())
    recording = bool(transport.isRecording())
    song = False
    try:
        song = int(transport.getLoopMode()) == 1
    except Exception:
        pass
    metronome = False
    try:
        metronome = bool(general.getUseMetronome())
    except Exception:
        pass
    tempo = 0.0
    try:
        tempo = float(mixer.getCurrentTempo())
        if tempo > 1000:                     # certaines versions donnent le tempo × 1000
            tempo /= 1000.0
    except Exception:
        try:
            tempo = general.processRECEvent(midi.REC_Tempo, 0, midi.REC_GetValue) / 1000.0
        except Exception:
            pass
    ppq = 96
    try:
        ppq = max(1, int(general.getRecPPQ()))
    except Exception:
        pass
    per_bar = 4
    try:
        per_bar = max(1, int(general.getRecPPB()) // ppq)
    except Exception:
        pass
    ticks = 0
    try:
        ticks = int(transport.getSongPos(getattr(midi, "SONGLENGTH_ABSTICKS", 2)))
    except Exception:
        pass
    return playing, recording, song, metronome, round(tempo, 3), max(0, ticks), ppq, per_bar


def _once(key, value, force=False):
    """Vrai quand `value` diffère de ce qui a été envoyé sous `key` (et le retient)."""
    if force or _sent.get(key) != value:
        _sent[key] = value
        return True
    return False


def _every(key, now, seconds):
    if now - _clock.get(key, -1e9) >= seconds:
        _clock[key] = now
        return True
    return False


def _scan_project(now):
    if _every("project", now, PROJECT_EVERY_S):
        name = _project_name()
        if _once("project", name):
            _send(ST_PROJECT, _text(name, 40))


def _is_playing():
    state = _sent.get("state")
    return bool(state and state[0])


def _scan_transport(now):
    interval = STATE_EVERY_PLAYING_S if _is_playing() else STATE_EVERY_IDLE_S
    if not _every("state", now, interval):
        return
    try:
        state = _read_transport()
    except Exception as exc:
        _fail("transport", exc)
        return
    changed = _once("state", state)
    beat = now - _clock.get("state_sent", -1e9) >= STATE_HEARTBEAT_S
    if changed or beat:
        _clock["state_sent"] = now
        playing, recording, song, metronome, tempo, ticks, ppq, per_bar = state
        flags = (1 if playing else 0) | (2 if recording else 0) | (4 if song else 0) | (8 if metronome else 0)
        _send(ST_STATE, (flags,) + _pack(int(round(tempo * 1000)), 3) + _pack(ticks, 4) + _pack(ppq, 2) + (per_bar,))


def _budget(key, now, count, cycle, cap):
    """Éléments à relire à ce tour pour parcourir `count` éléments en `cycle` secondes (jamais plus de `cap`)."""
    last = _clock.get(key)
    _clock[key] = now
    if last is None:
        return min(cap, count)
    return max(1, min(cap, count, int(math.ceil(count * (now - last) / cycle))))


def _scan_rack(now, count, everything):
    if _once("channel_count", count):
        _send(ST_CHANNEL_COUNT, _pack(count, 2))
    if count <= 0:
        return
    budget = count if everything else _budget("rack_turn", now, count, RACK_CYCLE_S, RACK_PER_TICK)
    for _ in range(budget):
        i = _cursor["rack"] % count
        _cursor["rack"] = i + 1
        info = _read_channel(i)
        if info is None:
            continue
        if _once(("channel", i), info):
            name, color, volume, pan, muted, solo, selected, kind = info
            flags = (1 if muted else 0) | (2 if solo else 0) | (4 if selected else 0)
            _send(ST_CHANNEL, _pack(i, 2) + (flags, max(0, min(0x7F, kind))) + _to14(volume) + _pan_to14(pan)
                  + _pack(color, 4) + _text(name))


def _scan_steps(now, count, everything):
    if count <= 0:
        return
    try:
        pattern = int(patterns.patternNumber())
    except Exception:
        pattern = 0
    budget = count if everything else _budget("steps_turn", now, count, STEPS_CYCLE_S, STEP_ROWS_PER_TICK)
    for _ in range(budget):
        i = _cursor["steps"] % count
        _cursor["steps"] = i + 1
        cells = _read_steps(i, _st_steps)
        if cells is None:
            continue
        if _once(("steps", i), (pattern, cells)):
            packed = []
            for start in range(0, len(cells), 7):
                packed.append(sum(1 << bit for bit, on in enumerate(cells[start:start + 7]) if on))
            _send(ST_STEPS, _pack(i, 2) + _pack(max(0, pattern), 2) + (len(cells),) + tuple(packed))


def _pattern_indices(current):
    found = []
    try:
        limit = min(int(patterns.patternMax()), 999)
    except Exception:
        limit = 99
    try:
        wanted = max(1, min(int(patterns.patternCount()), 60))      # on s'arrête quand on les a tous trouvés
    except Exception:
        wanted = 60
    try:
        for i in range(1, limit + 1):
            if not patterns.isPatternDefault(i):
                found.append(i)
                if len(found) >= wanted:
                    break
    except Exception:                        # ancienne API : pas de isPatternDefault
        try:
            found = list(range(1, min(max(int(patterns.patternCount()), current), 60) + 1))
        except Exception as exc:
            _fail("patterns", exc)
    if current > 0 and current not in found:
        found.append(current)
    return sorted(set(found))[:60]


def _pattern_changed():
    """Le pattern choisi n'est plus celui que VoiceConnector connaît."""
    known = _sent.get("pattern_list")
    try:
        return known is None or int(patterns.patternNumber()) != known[0]
    except Exception:
        return False


def _scan_patterns(now, force=False):
    if not (force or _every("patterns", now, PATTERNS_EVERY_S)):
        return
    try:
        current = int(patterns.patternNumber())
    except Exception as exc:
        _fail("pattern", exc)
        return
    indices = _pattern_indices(current)
    if _once("pattern_list", (current, tuple(indices))):
        _send(ST_PATTERN_LIST, _pack(max(0, current), 2) + (len(indices),)
              + tuple(b for i in indices for b in _pack(i, 2)))
    for i in indices:
        try:
            name = str(patterns.getPatternName(i))
            color = int(patterns.getPatternColor(i))
            length = int(patterns.getPatternLength(i)) * 4
        except Exception as exc:
            _fail("pattern", exc)
            continue
        info = (name, color, length, i == current)
        if _once(("pattern", i), info):
            _send(ST_PATTERN, _pack(i, 2) + (1 if i == current else 0,) + _pack(color, 4)
                  + _pack(length, 2) + _text(name))


def _playlist_count():
    """Pistes à montrer : jusqu'à la dernière renommée, au moins MIN_PLAYLIST_TRACKS."""
    last = 0
    for i in range(1, _st_pl_limit + 1):
        try:
            if str(playlist.getTrackName(i)) != "Track %d" % i:
                last = i
        except Exception as exc:
            _fail("playlist", exc)
            break
    return max(1, min(_st_pl_limit, max(last, MIN_PLAYLIST_TRACKS)))


def _scan_playlist(now, force=False):
    if not (force or _every("playlist", now, PLAYLIST_EVERY_S)):
        return
    count = _playlist_count()
    if _once("playlist_count", count):
        _send(ST_PL_COUNT, _pack(count, 2))
    for i in range(1, count + 1):
        try:
            info = (str(playlist.getTrackName(i)), int(playlist.getTrackColor(i)),
                    bool(playlist.isTrackMuted(i)), bool(playlist.isTrackSolo(i)),
                    bool(playlist.isTrackSelected(i)))
        except Exception as exc:
            _fail("playlist", exc)
            return
        if _once(("track", i), info):
            name, color, muted, solo, selected = info
            flags = (1 if muted else 0) | (2 if solo else 0) | (4 if selected else 0)
            _send(ST_PL_TRACK, _pack(i, 2) + (flags,) + _pack(color, 4) + _text(name))


def _track_activity(i):
    try:
        return float(playlist.getTrackActivityLevelVis(i))
    except Exception:
        try:
            return float(playlist.getTrackActivityLevel(i))
        except Exception:
            return 0.0


def _scan_activity(now, playing):
    """Activité des pistes de la playlist (clip sous le curseur) et des canaux (notes jouées)."""
    if not _every("activity", now, ACTIVITY_EVERY_S if playing else ACTIVITY_IDLE_S):
        return
    count = _sent.get("playlist_count") or 0
    if count:
        levels = tuple(max(0, min(127, int(round(_track_activity(i) * 127)))) for i in range(1, count + 1))
        if (playing or any(levels) or _sent.get("pl_activity_any")) and _once("pl_activity", levels):
            _sent["pl_activity_any"] = any(levels)
            _send(ST_PL_ACTIVITY, _pack(1, 2) + (len(levels),) + levels)
    rack = _sent.get("channel_count") or 0
    if rack:
        levels = tuple(max(0, min(127, int(round(_read_activity(i) * 127)))) for i in range(min(rack, 100)))
        if (playing or any(levels) or _sent.get("ch_activity_any")) and _once("ch_activity", levels):
            _sent["ch_activity_any"] = any(levels)
            _send(ST_CH_ACTIVITY, _pack(0, 2) + (len(levels),) + levels)


def _studio_idle(now):
    """Un tour de relecture (appelé depuis OnIdle, toutes les ~20 ms) : travail borné à chaque appel."""
    if not _studio or not _studio_ready():
        return
    force = _force["all"]
    whole_rack = force or _force["rack"]
    whole_steps = force or _force["steps"]
    _force["all"] = _force["rack"] = _force["steps"] = False
    _scan_transport(now)
    _scan_project(now)
    count = min(_count_channels(), _st_rack_limit)
    _scan_rack(now, count, whole_rack)
    # Les patterns avant les pas : VoiceConnector doit savoir quel pattern est choisi avant de recevoir
    # ses pas (il ignore les pas d'un autre pattern). Un changement de pattern est repris tout de suite.
    _scan_patterns(now, force or _pattern_changed())
    _scan_steps(now, count, whole_steps)
    _scan_playlist(now, force)
    _scan_activity(now, _is_playing())


def _studio_reset(scope=SCOPE_ALL):
    """Renvoie à VoiceConnector ce qu'il sait déjà (tout, ou la partie demandée) : FL Studio fait foi."""
    if scope & SCOPE_ALL == SCOPE_ALL:
        _sent.clear()
        _clock.clear()
        _cursor["rack"] = _cursor["steps"] = 0
        _force["all"] = True
        return
    for key in list(_sent):
        family = key[0] if isinstance(key, tuple) else key
        if _SCOPE_OF.get(family, SCOPE_ALL) & scope:
            del _sent[key]
    if scope & SCOPE_TRANSPORT:
        _clock.pop("state", None)
        _clock.pop("project", None)
    if scope & SCOPE_PATTERNS:
        _clock.pop("patterns", None)
    if scope & SCOPE_PLAYLIST:
        _clock.pop("playlist", None)
    if scope & SCOPE_RACK:
        _force["rack"] = True
    if scope & SCOPE_STEPS:
        _force["steps"] = True


# --------------------------------------------------------------------------- #
# Studio : ordres de VoiceConnector
# --------------------------------------------------------------------------- #
def _studio_action(action):
    if action == ACTION_PLAY:
        transport.start()
    elif action == ACTION_STOP:
        transport.stop()
    elif action == ACTION_RECORD:
        transport.record()
    elif action == ACTION_SONG_MODE:
        transport.setLoopMode()
    elif action == ACTION_METRONOME:
        transport.globalTransport(getattr(midi, "FPT_Metronome", 110), 1)
    elif action == ACTION_REWIND:
        transport.setSongPos(0, getattr(midi, "SONGLENGTH_ABSTICKS", 2))
    _clock.pop("state", None)                # relire tout de suite : l'écho arrive en une vingtaine de ms


def _studio_set_step(channel, step, value):
    try:
        general.saveUndo("VoiceConnector : pas", getattr(midi, "UF_PR", 2))
    except Exception:
        pass
    if _GLOBAL:
        channels.setGridBit(channel, step, bool(value), True)
    else:
        channels.setGridBit(channel, step, bool(value))
    _sent.pop(("steps", channel), None)


def _studio_channel_volume(channel, volume):
    if _GLOBAL:
        channels.setChannelVolume(channel, volume, 0, True)
    else:
        channels.setChannelVolume(channel, volume)


def _studio_channel_pan(channel, pan):
    if _GLOBAL:
        channels.setChannelPan(channel, pan, 0, True)
    else:
        channels.setChannelPan(channel, pan)


def _studio_channel_mute(channel, muted):
    current = bool(channels.isChannelMuted(channel, True) if _GLOBAL else channels.isChannelMuted(channel))
    if current != muted:
        channels.muteChannel(channel, -1, True) if _GLOBAL else channels.muteChannel(channel)


def _studio_channel_solo(channel, solo):
    current = bool(channels.isChannelSolo(channel, True) if _GLOBAL else channels.isChannelSolo(channel))
    if current != solo:
        channels.soloChannel(channel, True) if _GLOBAL else channels.soloChannel(channel)


def _studio_channel_select(channel):
    channels.selectOneChannel(channel, True) if _GLOBAL else channels.selectOneChannel(channel)


def _studio_track_mute(track, muted):
    if bool(playlist.isTrackMuted(track)) != muted:
        playlist.muteTrack(track)


def _studio_track_solo(track, solo):
    if bool(playlist.isTrackSolo(track)) != solo:
        playlist.soloTrack(track)


def _handle_studio(command, data):
    global _studio, _st_steps, _st_rack_limit, _st_pl_limit
    if not _studio_ready():
        return
    if command == ST_HELLO:
        was = _studio
        _studio = True
        if len(data) >= 5:
            _st_steps = max(1, min(MAX_STEP, data[2]))
            _st_rack_limit = max(1, min(0x7F, data[3]))
            _st_pl_limit = max(1, min(0x7F, data[4]))
        version = ""
        try:
            version = str(ui.getVersion())
        except Exception:
            pass
        _send(ST_ACK, (STUDIO_VERSION, 1 if _GLOBAL else 0) + _text(version, 16))
        if not was or (data and len(data) >= 2 and data[1] & 1):
            _studio_reset()
        return
    if command == ST_BYE:
        _studio = False
        return
    if not _studio:
        return
    if command == ST_REFRESH:
        _studio_reset(data[0] if data else SCOPE_ALL)
    elif command == ST_STEP_COUNT and data:
        _st_steps = max(1, min(MAX_STEP, data[0]))
        for key in [k for k in _sent if isinstance(k, tuple) and k[0] == "steps"]:
            del _sent[key]
    elif command == ST_TRANSPORT and data:
        _studio_action(data[0])
    elif command == ST_TEMPO and len(data) >= 3:
        bpm = _unpack(data[0:3])
        general.processRECEvent(midi.REC_Tempo, bpm, midi.REC_Control | midi.REC_UpdateControl)
        _clock.pop("state", None)
    elif command == ST_SEEK and len(data) >= 4:
        transport.setSongPos(_unpack(data[0:4]), getattr(midi, "SONGLENGTH_ABSTICKS", 2))
        _clock.pop("state", None)
    elif command == ST_STEP and len(data) >= 4:
        _studio_set_step(_unpack(data[0:2]), data[2], data[3])
    elif command == ST_CH_VOLUME and len(data) >= 4:
        _studio_channel_volume(_unpack(data[0:2]), _from14(data[2], data[3]))
    elif command == ST_CH_PAN and len(data) >= 4:
        _studio_channel_pan(_unpack(data[0:2]), _pan_from14(data[2], data[3]))
    elif command == ST_CH_MUTE and len(data) >= 3:
        _studio_channel_mute(_unpack(data[0:2]), bool(data[2]))
    elif command == ST_CH_SOLO and len(data) >= 3:
        _studio_channel_solo(_unpack(data[0:2]), bool(data[2]))
    elif command == ST_CH_SELECT and len(data) >= 2:
        _studio_channel_select(_unpack(data[0:2]))
    elif command == ST_PATTERN_SELECT and len(data) >= 2:
        patterns.jumpToPattern(_unpack(data[0:2]))
        _sent.pop("pattern_list", None)
        for key in [k for k in _sent if isinstance(k, tuple) and k[0] == "steps"]:
            del _sent[key]
    elif command == ST_PL_MUTE and len(data) >= 3:
        _studio_track_mute(_unpack(data[0:2]), bool(data[2]))
    elif command == ST_PL_SOLO and len(data) >= 3:
        _studio_track_solo(_unpack(data[0:2]), bool(data[2]))
    else:
        return
    if command in (ST_CH_VOLUME, ST_CH_PAN, ST_CH_MUTE, ST_CH_SOLO, ST_CH_SELECT):
        _force["rack"] = True                 # un canal a pu changer : relire sans attendre le tour de rôle


# --------------------------------------------------------------------------- #
# Messages de VoiceConnector
# --------------------------------------------------------------------------- #
def _handle(command, data):
    global _active, _watch
    if 0x30 <= command <= 0x3F:
        try:
            _handle_studio(command, data)
        except Exception as exc:
            _fail("ordre %02X" % command, exc)
        return
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
    global _active, _watch, _studio, _GLOBAL
    _active = False
    _studio = False
    _watch = list(DEFAULT_WATCH)
    _cache.clear()
    _sent.clear()
    _clock.clear()
    try:
        device.setHasMeters()            # sans cet appel, FL Studio n'appelle jamais OnUpdateMeters
    except Exception:
        pass
    try:
        _GLOBAL = int(general.getVersion()) >= 33        # numéros de canaux « globaux » (API 33 et plus)
    except Exception:
        _GLOBAL = False


def OnDeInit():  # noqa: N802
    global _active, _studio
    if _active or _studio:
        _send(CMD_BYE)          # FL Studio se ferme (ou le script est déchargé) : VoiceConnector le sait tout de suite
    _active = False
    _studio = False


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
    if _studio:
        _clock.pop("state", None)
        if flags & getattr(midi, "HW_Dirty_Patterns", 1024):
            _clock.pop("patterns", None)
        if flags & (getattr(midi, "HW_Dirty_Names", 16384) | getattr(midi, "HW_Dirty_Colors", 8192)
                    | getattr(midi, "HW_Dirty_Tracks", 2048)):
            _clock.pop("playlist", None)
            _force["rack"] = True
        if flags & getattr(midi, "HW_Dirty_FocusedWindow", 32):
            _force["rack"] = True


def OnDirtyChannel(index):  # noqa: N802
    if _studio:
        _force["rack"] = True


def OnProjectLoad(status):  # noqa: N802
    if _studio and status == getattr(midi, "PL_LoadOk", 100):
        _studio_reset()


def OnIdle():  # noqa: N802
    """Filet de sécurité : certains changements du mixer n'appellent pas OnRefresh ; et relecture
    du Studio (transport, channel rack, patterns, playlist)."""
    global _last_push
    now = time.monotonic()
    if _active and now - _last_push >= IDLE_PUSH_S:
        _last_push = now
        _push_changes()
    if _studio:
        try:
            _studio_idle(now)
        except Exception as exc:
            _fail("studio", exc)


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

"""Faux monde pour les tests de la synchronisation FL Studio et du Studio : une horloge, un transport
MIDI de test et le vrai script de FL Studio chargé contre un faux mixer et un faux projet (channel
rack, patterns, playlist, transport) qui respectent les signatures de l'API de FL Studio (sans Qt,
sans FL Studio, sans MIDI). Partagé par test_fl_sync.py, test_standalone_engine.py et
test_fl_studio_link.py."""

import importlib.util
import inspect
import math
import sys
import types
from pathlib import Path

from core import fl_sync as fl
from core.fl_sync import parse

SCRIPT = Path(fl.__file__).resolve().parent.parent / "assets" / "flstudio" / "device_VoiceConnector.py"


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeTransport:
    """Transport de test : on y dépose ce que « FL Studio » envoie, on y lit ce que VC envoie."""

    def __init__(self) -> None:
        self.is_open = True
        self.sent: list[bytes] = []
        self.inbox: list[bytes] = []
        self.fail = False

    def send(self, data: bytes) -> None:
        if self.fail:
            raise OSError("port fermé")
        self.sent.append(bytes(data))

    def receive(self) -> list[bytes]:
        messages, self.inbox = self.inbox, []
        return messages

    def close(self) -> None:
        self.is_open = False

    def commands(self) -> list[int]:
        return [parse(m).command for m in self.sent]


class FakeMixer:
    def __init__(self, project=None) -> None:
        self.project = project
        self.tracks = {t: {"volume": 0.8, "pan": 0.0, "mute": False, "solo": False,
                           "name": f"Insert {t}" if t else "Master", "peak": (0.0, 0.0)}
                       for t in range(0, 8)}

    def trackCount(self):  # noqa: N802
        return 126

    def getCurrentTempo(self, asInt=False):  # noqa: N802
        tempo = self.project.tempo if self.project is not None else 120.0
        return int(round(tempo * 1000)) if asInt else tempo

    def getTrackVolume(self, t):  # noqa: N802
        return self.tracks[t]["volume"]

    def getTrackPan(self, t):  # noqa: N802
        return self.tracks[t]["pan"]

    def isTrackMuted(self, t):  # noqa: N802
        return self.tracks[t]["mute"]

    def isTrackSolo(self, t):  # noqa: N802
        return self.tracks[t]["solo"]

    def getTrackName(self, t):  # noqa: N802
        return self.tracks[t]["name"]

    def getTrackPeaks(self, t, mode):  # noqa: N802
        return self.tracks[t]["peak"][mode]

    def setTrackVolume(self, t, v, *_):  # noqa: N802
        self.tracks[t]["volume"] = v

    def setTrackPan(self, t, v, *_):  # noqa: N802
        self.tracks[t]["pan"] = v

    def muteTrack(self, t, *_):  # noqa: N802
        self.tracks[t]["mute"] = not self.tracks[t]["mute"]

    def soloTrack(self, t, *_):  # noqa: N802
        self.tracks[t]["solo"] = not self.tracks[t]["solo"]


# --------------------------------------------------------------------------- #
# Le projet : channel rack, patterns, playlist et transport de FL Studio
# --------------------------------------------------------------------------- #
class FakeFlChannel:
    """Un canal du channel rack : réglages, et pas du séquenceur pattern par pattern."""

    def __init__(self, name: str, color: int = 0x5A6B7C, kind: int = 2, volume: float = 0.78125,
                 pan: float = 0.0) -> None:
        self.name, self.color, self.kind = name, color, kind
        self.volume, self.pan = volume, pan
        self.muted = self.solo = self.selected = False
        self.steps: dict[int, set[int]] = {}            # numéro de pattern -> pas allumés
        self.activity = 0.0


class FakeFlPattern:
    def __init__(self, name: str, color: int = 0x2040F0, beats: int = 4) -> None:
        self.name, self.color, self.beats = name, color, beats


class FakeFlTrack:
    def __init__(self, number: int) -> None:
        self.name, self.color = f"Track {number}", 0x484848
        self.muted = self.solo = self.selected = False
        self.activity = 0.0


MIDI_CONSTANTS = {
    "PEAK_L": 0, "PEAK_R": 1,
    "REC_UpdateValue": 1, "REC_GetValue": 2, "REC_UpdateControl": 16,
    "REC_Control": 1 | 4 | (512 | 64) | 128 | 8 | 256,      # UpdateValue | ShowHint | InitStore | SetChanged | …
    "REC_Tempo": 0x4000 * 0x10000 + 5,
    "UF_PR": 2, "FPT_Metronome": 110, "SONGLENGTH_ABSTICKS": 2,
    "HW_Dirty_FocusedWindow": 32, "HW_Dirty_Patterns": 1024, "HW_Dirty_Tracks": 2048,
    "HW_Dirty_Colors": 8192, "HW_Dirty_Names": 16384,
    "PL_Start": 0, "PL_LoadOk": 100, "PL_LoadError": 101,
}


MUTATING_CALLS = frozenset({
    "setGridBit", "setChannelVolume", "setChannelPan", "muteChannel", "soloChannel", "selectOneChannel",
    "jumpToPattern", "muteTrack", "soloTrack", "start", "stop", "record", "setLoopMode", "globalTransport",
    "setSongPos"})


def _legacy(function):
    """La même fonction d'API avant la version 33 : elle n'a pas encore le dernier argument
    (`useGlobalIndex`, `globalCount`), et FL Studio refuse un argument de trop."""
    limit = len(inspect.signature(function).parameters) - 1

    def old(*args):
        if len(args) > limit:
            raise TypeError(f"{function.__name__}() takes at most {limit} positional arguments "
                            f"but {len(args)} were given")
        return function(*args)

    old.__name__ = function.__name__
    return old


class FakeFlProject:
    """Un projet de FL Studio en mémoire, vu par les modules `transport`, `general`, `channels`,
    `patterns`, `playlist` et `ui` de l'API de scripts MIDI : mêmes noms, arguments positionnels (avec
    `useGlobalIndex` à partir de l'API 33), mêmes unités (tempo × 1000 pour les événements REC,
    pistes de la playlist numérotées à partir de 1, couleurs 0x00BBGGRR)."""

    def __init__(self, api_version: int = 34) -> None:
        self.api_version = api_version
        self.title = "Maquette - FL Studio 21"
        self.version = "21.2.3"
        self.tempo = 115.0
        self.playing = self.recording = self.song_mode = self.metronome = False
        self.ppq, self.beats_per_bar = 96, 4
        self.ticks = 0
        self.channels = [FakeFlChannel("Kick", 0x2040F0, kind=0), FakeFlChannel("Snare", 0x30A0E0, kind=0),
                         FakeFlChannel("Hi-Hat", 0x40C060, kind=0), FakeFlChannel("Basse « sub »", 0xC04080)]
        self.channels[0].selected = True
        self.channels[0].steps[1] = {0, 4, 8, 12}
        self.channels[1].steps[1] = {4, 12}
        self.channels[2].steps[1] = {0, 2, 4, 6, 8, 10, 12, 14}
        self.group: list[int] | None = None             # filtre du channel rack : indices globaux visibles
        self.pattern = 1
        self.patterns = {1: FakeFlPattern("Pattern 1"), 2: FakeFlPattern("Refrain", 0x2020FF, beats=8)}
        self.tracks = {n: FakeFlTrack(n) for n in range(1, 501)}
        self.tracks[1].name, self.tracks[2].name = "Pads", "Basse"
        self.undo: list[str] = []
        self.calls: list[tuple] = []                    # appels qui modifient le projet : (nom, arguments passés)
        self.api_calls = 0                              # tous les appels d'API faits par le script

    # ----------------------------------------------------------------- aide
    def advance(self, seconds: float) -> None:
        """Fait avancer la lecture (le temps passe dans FL Studio)."""
        if self.playing:
            self.ticks += int(round(seconds * self.tempo / 60.0 * self.ppq))

    def _visible(self) -> list[int]:
        return list(range(len(self.channels))) if self.group is None else list(self.group)

    def _channel(self, index: int, use_global: bool) -> FakeFlChannel:
        order = list(range(len(self.channels))) if use_global else self._visible()
        if not 0 <= index < len(order):
            raise IndexError(f"channel index {index} out of range")
        return self.channels[order[index]]

    def _track(self, index: int) -> FakeFlTrack:
        if index not in self.tracks:
            raise ValueError(f"playlist track {index} out of range (1-{len(self.tracks)})")
        return self.tracks[index]

    def _log(self, name: str, *args) -> None:
        self.calls.append((name, args))

    def calls_named(self, name: str) -> list[tuple]:
        return [args for called, args in self.calls if called == name]

    # ------------------------------------------------------------- modules
    def modules(self) -> dict[str, types.ModuleType]:
        p = self

        def counted(function):
            journal = function.__name__ in MUTATING_CALLS

            def call(*args):                          # l'API de FL Studio n'accepte que des arguments positionnels
                p.api_calls += 1
                if journal:
                    p.calls.append((function.__name__, args))        # les arguments passés par le script
                return function(*args)
            call.__name__ = function.__name__
            return call

        def make(name, functions):
            module = types.ModuleType(name)
            for function in functions:
                setattr(module, function.__name__, counted(function))
            return module

        # --- channels
        def channelCount(globalCount=False):  # noqa: N802
            return len(p.channels) if globalCount else len(p._visible())

        def getChannelName(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).name

        def getChannelColor(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).color

        def getChannelVolume(index, mode=False, useGlobalIndex=False):  # noqa: N802
            volume = p._channel(index, useGlobalIndex).volume
            return 20 * math.log10(max(volume, 1e-6)) if mode else volume

        def getChannelPan(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).pan

        def isChannelMuted(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).muted

        def isChannelSolo(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).solo

        def isChannelSelected(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).selected

        def getChannelType(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).kind

        def getGridBit(index, position, useGlobalIndex=False):  # noqa: N802
            return position in p._channel(index, useGlobalIndex).steps.get(p.pattern, set())

        def getActivityLevel(index, useGlobalIndex=False):  # noqa: N802
            return p._channel(index, useGlobalIndex).activity

        def setGridBit(index, position, value, useGlobalIndex=False):  # noqa: N802
            cells = p._channel(index, useGlobalIndex).steps.setdefault(p.pattern, set())
            (cells.add if value else cells.discard)(position)

        def setChannelVolume(index, volume, pickupMode=0, useGlobalIndex=False):  # noqa: N802
            p._channel(index, useGlobalIndex).volume = float(volume)

        def setChannelPan(index, pan, pickupMode=0, useGlobalIndex=False):  # noqa: N802
            p._channel(index, useGlobalIndex).pan = float(pan)

        def muteChannel(index, value=-1, useGlobalIndex=False):  # noqa: N802
            channel = p._channel(index, useGlobalIndex)
            channel.muted = (not channel.muted) if value == -1 else bool(value)

        def soloChannel(index, useGlobalIndex=False):  # noqa: N802
            channel = p._channel(index, useGlobalIndex)
            channel.solo = not channel.solo

        def selectOneChannel(index, useGlobalIndex=False):  # noqa: N802
            target = p._channel(index, useGlobalIndex)
            for channel in p.channels:
                channel.selected = channel is target

        channel_functions = [channelCount, getChannelName, getChannelColor, getChannelVolume, getChannelPan,
                             isChannelMuted, isChannelSolo, isChannelSelected, getChannelType, getGridBit,
                             getActivityLevel, setGridBit, setChannelVolume, setChannelPan, muteChannel,
                             soloChannel, selectOneChannel]
        if p.api_version < 19:                       # getChannelType date de l'API 19
            channel_functions.remove(getChannelType)
        if p.api_version < 33:
            channel_functions = [_legacy(f) for f in channel_functions]

        # --- patterns
        def patternNumber():  # noqa: N802
            return p.pattern

        def patternCount():  # noqa: N802
            return len(p.patterns)

        def patternMax():  # noqa: N802
            return 999

        def _pattern_index(index):
            if not 1 <= index <= 999:
                raise ValueError(f"pattern {index} out of range")
            return index

        def getPatternName(index):  # noqa: N802
            pattern = p.patterns.get(_pattern_index(index))
            return pattern.name if pattern else f"Pattern {index}"

        def getPatternColor(index):  # noqa: N802
            pattern = p.patterns.get(_pattern_index(index))
            return pattern.color if pattern else 0x484848

        def getPatternLength(index):  # noqa: N802
            pattern = p.patterns.get(_pattern_index(index))
            return pattern.beats if pattern else 4

        def isPatternDefault(index):  # noqa: N802
            return _pattern_index(index) not in p.patterns

        def jumpToPattern(index):  # noqa: N802
            p.pattern = _pattern_index(index)

        pattern_functions = [patternNumber, patternCount, patternMax, getPatternName, getPatternColor,
                             getPatternLength, isPatternDefault, jumpToPattern]
        if p.api_version < 23:                       # isPatternDefault date de l'API 23
            pattern_functions.remove(isPatternDefault)

        # --- playlist
        def getTrackName(index):  # noqa: N802
            return p._track(index).name

        def getTrackColor(index):  # noqa: N802
            return p._track(index).color

        def isTrackMuted(index):  # noqa: N802
            return p._track(index).muted

        def isTrackSolo(index):  # noqa: N802
            return p._track(index).solo

        def isTrackSelected(index):  # noqa: N802
            return p._track(index).selected

        def getTrackActivityLevel(index):  # noqa: N802
            return p._track(index).activity

        def getTrackActivityLevelVis(index):  # noqa: N802
            return p._track(index).activity

        def muteTrack(index, value=-1):  # noqa: N802
            track = p._track(index)
            track.muted = (not track.muted) if value == -1 else bool(value)

        def soloTrack(index, value=-1, inGroup=False):  # noqa: N802
            track = p._track(index)
            track.solo = (not track.solo) if value == -1 else bool(value)

        # --- transport
        def start():
            p.playing = not p.playing                # lecture / pause

        def stop():
            p.playing = False
            p.ticks = 0

        def isPlaying():  # noqa: N802
            return p.playing

        def record():
            p.recording = not p.recording

        def isRecording():  # noqa: N802
            return p.recording

        def getLoopMode():  # noqa: N802
            return 1 if p.song_mode else 0

        def setLoopMode():  # noqa: N802
            p.song_mode = not p.song_mode

        def globalTransport(command, value, pmeflags=0, flags=0):  # noqa: N802
            if command == MIDI_CONSTANTS["FPT_Metronome"]:
                p.metronome = not p.metronome
            return 0

        def getSongPos(mode=-1):  # noqa: N802
            if mode == MIDI_CONSTANTS["SONGLENGTH_ABSTICKS"]:
                return int(p.ticks)
            return p.ticks / p.ppq                    # temps fractionnaires (en noires)

        def setSongPos(position, mode=-1):  # noqa: N802
            p.ticks = int(position) if mode == MIDI_CONSTANTS["SONGLENGTH_ABSTICKS"] else int(position * p.ppq)

        # --- general
        def getVersion():  # noqa: N802
            return p.api_version

        def getUseMetronome():  # noqa: N802
            return p.metronome

        def getRecPPQ():  # noqa: N802
            return p.ppq

        def getRecPPB():  # noqa: N802
            return p.ppq * p.beats_per_bar

        def saveUndo(undoName, flags, update=True):  # noqa: N802,N803
            p.undo.append(undoName)

        def processRECEvent(eventId, value, flags):  # noqa: N802,N803
            if eventId != MIDI_CONSTANTS["REC_Tempo"]:
                return 0
            if flags & MIDI_CONSTANTS["REC_GetValue"]:
                return int(round(p.tempo * 1000))
            p._log("setTempo", value)
            p.tempo = value / 1000.0
            return 0

        # --- ui
        def getProgTitle():  # noqa: N802
            return p.title

        def getVersion_ui(mode=4):  # noqa: N802
            parts = [int(x) for x in p.version.split(".")]
            return parts[mode] if mode < 3 else p.version
        getVersion_ui.__name__ = "getVersion"

        return {
            "channels": make("channels", channel_functions),
            "patterns": make("patterns", pattern_functions),
            "playlist": make("playlist", [getTrackName, getTrackColor, isTrackMuted, isTrackSolo,
                                          isTrackSelected, getTrackActivityLevel, getTrackActivityLevelVis,
                                          muteTrack, soloTrack]),
            "transport": make("transport", [start, stop, isPlaying, record, isRecording, getLoopMode,
                                            setLoopMode, globalTransport, getSongPos, setSongPos]),
            "general": make("general", [getVersion, getUseMetronome, getRecPPQ, getRecPPB, saveUndo,
                                        processRECEvent]),
            "ui": make("ui", [getProgTitle, getVersion_ui]),
        }


class Event:
    def __init__(self, data: bytes) -> None:
        self.sysex, self.handled = data, False


class FlStudioRig:
    """Le script chargé comme le ferait FL Studio, avec un faux mixer et un faux port MIDI.

    Sans `project`, seul le mixer existe (ancienne version de FL Studio : le Studio reste muet) ; avec
    un `FakeFlProject`, le script voit aussi le transport, le channel rack, les patterns et la playlist.
    Avec une `clock`, le script lit son horloge (`time.monotonic`) dans cette horloge de test."""

    def __init__(self, monkeypatch, project: FakeFlProject | None = None, clock: FakeClock | None = None) -> None:
        self.project = project
        self.clock = clock
        self.mixer = FakeMixer(project)
        self.outbox: list[bytes] = []
        self.has_meters = False
        device = types.ModuleType("device")
        device.midiOutSysex = self.outbox.append
        device.setHasMeters = self._set_has_meters
        midi = types.ModuleType("midi")
        for name, value in MIDI_CONSTANTS.items():
            setattr(midi, name, value)
        mixer = types.ModuleType("mixer")
        for name in dir(self.mixer):
            if name[0].islower() and callable(getattr(self.mixer, name)):
                setattr(mixer, name, getattr(self.mixer, name))
        modules = {"device": device, "midi": midi, "mixer": mixer}
        if project is not None:
            modules.update(project.modules())
        for name in ("channels", "general", "patterns", "playlist", "transport", "ui"):
            if name in modules:
                monkeypatch.setitem(sys.modules, name, modules[name])
            elif name != "ui":                  # (« ui » est aussi le paquet de VoiceConnector : on n'y touche pas)
                monkeypatch.delitem(sys.modules, name, raising=False)
        for name in ("device", "midi", "mixer"):
            monkeypatch.setitem(sys.modules, name, modules[name])
        spec = importlib.util.spec_from_file_location("device_VoiceConnector_under_test", SCRIPT)
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        if clock is not None:
            monkeypatch.setattr(self.script.time, "monotonic", clock)
        self.script.OnInit()
        self.is_open = True

    def _set_has_meters(self) -> None:
        self.has_meters = True

    # --- Ce que fait FL Studio
    def idle(self, seconds: float = 0.02) -> None:
        """Un `OnIdle` de FL Studio (toutes les ~20 ms) ; le temps passe aussi dans le projet."""
        if self.clock is not None:
            self.clock.advance(seconds)
        if self.project is not None:
            self.project.advance(seconds)
        self.script.OnIdle()

    # --- Transport (côté VoiceConnector)
    def send(self, data: bytes) -> None:
        event = Event(data)
        self.script.OnSysEx(event)
        assert event.handled

    def receive(self) -> list[bytes]:
        messages, self.outbox[:] = list(self.outbox), []
        return messages

    def close(self) -> None:
        self.is_open = False

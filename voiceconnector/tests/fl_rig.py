"""Faux monde pour les tests de la synchronisation FL Studio : un horloge, un transport MIDI
de test et le vrai script de FL Studio chargé contre un faux mixer (sans Qt, sans FL Studio,
sans MIDI). Partagé par test_fl_sync.py et test_standalone_engine.py."""

import importlib.util
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
    def __init__(self) -> None:
        self.tracks = {t: {"volume": 0.8, "pan": 0.0, "mute": False, "solo": False,
                           "name": f"Insert {t}" if t else "Master", "peak": (0.0, 0.0)}
                       for t in range(0, 8)}

    def trackCount(self):  # noqa: N802
        return 126

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


class Event:
    def __init__(self, data: bytes) -> None:
        self.sysex, self.handled = data, False


class FlStudioRig:
    """Le script chargé comme le ferait FL Studio, avec un faux mixer et un faux port MIDI."""

    def __init__(self, monkeypatch) -> None:
        self.mixer = FakeMixer()
        self.outbox: list[bytes] = []
        device = types.ModuleType("device")
        device.midiOutSysex = self.outbox.append
        midi = types.ModuleType("midi")
        midi.PEAK_L, midi.PEAK_R = 0, 1
        mixer = types.ModuleType("mixer")
        for name in dir(self.mixer):
            if name[0].islower() and callable(getattr(self.mixer, name)):
                setattr(mixer, name, getattr(self.mixer, name))
        for name, module in (("device", device), ("midi", midi), ("mixer", mixer)):
            monkeypatch.setitem(sys.modules, name, module)
        spec = importlib.util.spec_from_file_location("device_VoiceConnector_under_test", SCRIPT)
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        self.script.OnInit()
        self.is_open = True

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

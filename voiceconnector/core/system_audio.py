"""
Audio système pour le mode autonome (sans Voicemeeter) : volume, muet et niveau des
applications, du micro par défaut et de la sortie par défaut de Windows.

Même principe que app_volume.py (sessions audio Windows via pycaw), mais sur le
périphérique de sortie par DÉFAUT et sans Voicemeeter :

- WindowsSystemAudio : le vrai (Core Audio via pycaw / comtypes, Windows) ;
- DemoSystemAudio    : applications simulées (--demo, tests) ;
- NullSystemAudio    : indisponible (autre système, pycaw absent).

Aucun Qt ici. Les appels COM se font dans le thread de l'interface (là où COM est
initialisé). Les volumes sont ceux du mélangeur de Windows : une échelle linéaire
de 0 à 1 (1 = 0 dB) ; une application ne peut donc pas être amplifiée.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass, replace
from typing import Callable

from core.app_detector import known_app, process_stem

log = logging.getLogger(__name__)

SOURCE_APP = "app"              # session audio d'une application
SOURCE_CAPTURE = "capture"      # micro par défaut de Windows
SOURCE_MASTER = "master"        # sortie par défaut de Windows (volume général)
SYSTEM_KEY = "system"           # sons système Windows (pid 0)
SILENCE_DB = -100.0
SESSION_EXPIRED = 2


def scalar_to_db(volume: float) -> float:
    """Volume Windows (0 → 1, linéaire) en dB (1 → 0 dB)."""
    return 20.0 * math.log10(volume) if volume > 1e-5 else SILENCE_DB


def db_to_scalar(db: float) -> float:
    """dB en volume Windows ; limité à 1 (0 dB) : Windows n'amplifie pas."""
    return max(0.0, min(1.0, 10.0 ** (float(db) / 20.0)))


def source_matches(source_key: str, process_key: str) -> bool:
    """Le processus (clé de session : « discord.exe ») est-il la source demandée ?

    `source_key` est une clé du catalogue (« ableton » : tous ses exécutables, y
    compris ceux à numéro de version) ou un nom de processus (« chrome.exe »)."""
    source_key, process_key = str(source_key or "").strip(), str(process_key or "").strip()
    if not source_key or not process_key:
        return False
    app = known_app(source_key.lower())
    if app is not None:
        return app.matches(process_key)
    return process_stem(source_key) == process_stem(process_key)


@dataclass(frozen=True)
class SourceState:
    volume: float = 1.0         # 0 → 1 (échelle linéaire de Windows)
    muted: bool = False
    peak: float = 0.0           # crête 0 → 1 (0 si inconnue)
    present: bool = False       # la source existe (application en cours, micro branché)
    name: str = ""              # nom affiché (applications)


class SystemAudio:
    """Interface commune. `key` : clé de session (« discord.exe », « system »)."""

    available = False
    reason = ""

    def app_states(self) -> dict[str, SourceState]:
        """Applications qui ont une session audio sur la sortie par défaut, par clé."""
        return {}

    def set_app_volume(self, key: str, volume: float) -> None:
        pass

    def set_app_mute(self, key: str, muted: bool) -> None:
        pass

    def endpoint_state(self, kind: str) -> SourceState:
        """État du micro (SOURCE_CAPTURE) ou de la sortie (SOURCE_MASTER) par défaut."""
        return SourceState()

    def set_endpoint_volume(self, kind: str, volume: float) -> None:
        pass

    def set_endpoint_mute(self, kind: str, muted: bool) -> None:
        pass


class NullSystemAudio(SystemAudio):
    def __init__(self, reason: str = "Disponible sous Windows seulement.") -> None:
        self.reason = reason


class DemoSystemAudio(SystemAudio):
    """Applications, micro et sortie simulés, avec des niveaux animés."""

    available = True
    APPS = (("discord.exe", "Discord", 0.85), ("spotify.exe", "Spotify", 0.45),
            ("chrome.exe", "Google Chrome", 0.6), (SYSTEM_KEY, "Sons système", 1.0))

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0 = clock()
        self._apps = {key: SourceState(volume, False, 0.0, True, name) for key, name, volume in self.APPS}
        self._endpoints = {SOURCE_CAPTURE: SourceState(0.8, False, 0.0, True, "Microphone"),
                           SOURCE_MASTER: SourceState(0.7, False, 0.0, True, "Haut-parleurs")}

    def _animated(self, state: SourceState, n: int) -> SourceState:
        t = self._clock() - self._t0
        level = 0.0 if state.muted else state.volume * (0.55 + 0.4 * math.sin(t * (2.1 + n) + n))
        return replace(state, peak=max(0.0, min(1.0, level)))

    def app_states(self) -> dict[str, SourceState]:
        return {key: self._animated(state, n) for n, (key, state) in enumerate(self._apps.items())}

    def set_app_volume(self, key: str, volume: float) -> None:
        if key in self._apps:
            self._apps[key] = replace(self._apps[key], volume=max(0.0, min(1.0, volume)))

    def set_app_mute(self, key: str, muted: bool) -> None:
        if key in self._apps:
            self._apps[key] = replace(self._apps[key], muted=bool(muted))

    def endpoint_state(self, kind: str) -> SourceState:
        state = self._endpoints.get(kind)
        return self._animated(state, 7 if kind == SOURCE_CAPTURE else 8) if state else SourceState()

    def set_endpoint_volume(self, kind: str, volume: float) -> None:
        if kind in self._endpoints:
            self._endpoints[kind] = replace(self._endpoints[kind], volume=max(0.0, min(1.0, volume)))

    def set_endpoint_mute(self, kind: str, muted: bool) -> None:
        if kind in self._endpoints:
            self._endpoints[kind] = replace(self._endpoints[kind], muted=bool(muted))


class WindowsSystemAudio(SystemAudio):
    """Sessions audio Windows (Core Audio via pycaw) de la sortie par défaut, micro
    et sortie par défaut. Chaque appel est protégé : un périphérique débranché ou
    un service audio arrêté ne doit jamais faire planter l'interface."""

    available = True

    def __init__(self) -> None:
        # Import ici, dans le thread de l'interface : comtypes y initialise COM
        import comtypes
        from pycaw.api.endpointvolume import IAudioEndpointVolume, IAudioMeterInformation
        from pycaw.utils import AudioUtilities

        self._comtypes = comtypes
        self._utilities = AudioUtilities
        self._endpoint_iface = IAudioEndpointVolume
        self._meter_iface = IAudioMeterInformation
        self._controls: dict[str, list] = {}            # clé → ISimpleAudioVolume de ses sessions
        self._names: dict[int, tuple[str, str]] = {}    # pid → (clé, nom)
        self._endpoints: dict[str, tuple[object, object]] = {}   # genre → (volume, mesure)

    # ------------------------------------------------------------ sessions
    def app_states(self) -> dict[str, SourceState]:
        try:
            sessions = self._utilities.GetAllSessions()
        except Exception as exc:        # service audio arrêté, pas de sortie…
            log.debug("Sessions audio illisibles : %s", exc)
            return {}
        groups: dict[str, dict] = {}
        controls: dict[str, list] = {}
        for session in sessions:
            try:
                if session.State == SESSION_EXPIRED:
                    continue
                key, name = self._describe(session.ProcessId)
                volume = session.SimpleAudioVolume
                level, muted = float(volume.GetMasterVolume()), bool(volume.GetMute())
                try:
                    peak = float(session._ctl.QueryInterface(self._meter_iface).GetPeakValue())
                except Exception:
                    peak = 0.0
            except Exception as exc:
                log.debug("Session audio ignorée : %s", exc)
                continue
            group = groups.setdefault(key, {"name": name, "volume": level, "muted": True, "peak": 0.0})
            group["volume"] = max(group["volume"], level)
            group["muted"] = group["muted"] and muted           # muet si toutes ses sessions le sont
            group["peak"] = max(group["peak"], peak)
            controls.setdefault(key, []).append(volume)
        self._controls = controls
        return {key: SourceState(g["volume"], g["muted"], g["peak"], True, g["name"])
                for key, g in groups.items()}

    def _describe(self, pid: int) -> tuple[str, str]:
        if pid == 0:
            return SYSTEM_KEY, "Sons système"
        cached = self._names.get(pid)
        if cached is not None:
            return cached
        import psutil

        base = f"pid {pid}"
        exe = ""
        try:
            process = psutil.Process(pid)
            base = process.name()
            exe = process.exe()
        except psutil.Error:
            pass
        from core.app_volume import display_name

        described = (base.lower(), display_name(exe, base))
        if len(self._names) > 512:
            self._names.clear()
        self._names[pid] = described
        return described

    def set_app_volume(self, key: str, volume: float) -> None:
        for control in self._controls.get(key, []):
            try:
                control.SetMasterVolume(max(0.0, min(1.0, float(volume))), None)
            except Exception as exc:     # session terminée entre-temps
                log.debug("Volume de %s non réglé : %s", key, exc)

    def set_app_mute(self, key: str, muted: bool) -> None:
        for control in self._controls.get(key, []):
            try:
                control.SetMute(1 if muted else 0, None)
            except Exception as exc:
                log.debug("Mute de %s non réglé : %s", key, exc)

    # ------------------------------------------------- micro et sortie par défaut
    def _endpoint(self, kind: str):
        """(volume, mesure) du périphérique par défaut ; mis en cache, refait après une erreur."""
        cached = self._endpoints.get(kind)
        if cached is not None:
            return cached
        from ctypes import POINTER, cast

        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole

        flow = EDataFlow.eCapture if kind == SOURCE_CAPTURE else EDataFlow.eRender
        enumerator = self._comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, self._comtypes.CLSCTX_INPROC_SERVER)
        device = enumerator.GetDefaultAudioEndpoint(flow.value, ERole.eMultimedia.value)
        volume = cast(device.Activate(self._endpoint_iface._iid_, self._comtypes.CLSCTX_ALL, None),
                      POINTER(self._endpoint_iface))
        meter = cast(device.Activate(self._meter_iface._iid_, self._comtypes.CLSCTX_ALL, None),
                     POINTER(self._meter_iface))
        self._endpoints[kind] = (volume, meter)
        return volume, meter

    def endpoint_state(self, kind: str) -> SourceState:
        try:
            volume, meter = self._endpoint(kind)
            return SourceState(float(volume.GetMasterVolumeLevelScalar()), bool(volume.GetMute()),
                               float(meter.GetPeakValue()), True)
        except Exception as exc:        # pas de micro, périphérique changé…
            log.debug("Périphérique %s illisible : %s", kind, exc)
            self._endpoints.pop(kind, None)
            return SourceState()

    def set_endpoint_volume(self, kind: str, volume: float) -> None:
        try:
            self._endpoint(kind)[0].SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(volume))), None)
        except Exception as exc:
            log.debug("Volume de %s non réglé : %s", kind, exc)
            self._endpoints.pop(kind, None)

    def set_endpoint_mute(self, kind: str, muted: bool) -> None:
        try:
            self._endpoint(kind)[0].SetMute(1 if muted else 0, None)
        except Exception as exc:
            log.debug("Mute de %s non réglé : %s", kind, exc)
            self._endpoints.pop(kind, None)


def create_system_audio(cfg: dict | None = None) -> SystemAudio:
    """Le moteur réel sous Windows, sinon un moteur indisponible (jamais d'exception)."""
    if sys.platform != "win32":
        return NullSystemAudio()
    try:
        return WindowsSystemAudio()
    except Exception as exc:
        log.warning("Audio système indisponible : %s", exc)
        return NullSystemAudio(f"Indisponible : {exc}")

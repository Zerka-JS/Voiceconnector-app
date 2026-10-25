"""
Moteur du mode autonome (B) : la console sans Voicemeeter (logique pure, sans Qt).

Chaque canal du profil est relié à une source :

- `app`      : session audio Windows d'une application (volume, muet, niveau) ;
- `capture`  : micro par défaut de Windows ;
- `master`   : volume général de la sortie par défaut de Windows ;
- `fl_track` : piste du mixer de FL Studio (volume, muet, solo, panoramique, niveau),
               par la synchronisation MIDI de fl_sync.py.

L'interface parle à ce moteur comme à VoicemeeterController : mêmes méthodes
(`set_gain`, `set_mute`, `set_pad`, `set_headphone_gain`…), mêmes objets
(`MixerParams`, `MixerLevels`). Ce qui n'existe pas sans Voicemeeter (routage vers des
bus, niveaux d'envoi, inserts) est sans effet ; `caps` / `strip_caps` le disent à la
disposition, qui masque alors ces commandes.

Règles :

- Une consigne (gain, muet) donnée pour une source absente est gardée et appliquée
  quand la source apparaît (Discord lancé après VoiceConnector, par exemple).
- Valeur de départ `None` dans le profil = on ne touche pas au son de la source.
- Les sessions Windows ne s'amplifient pas : au-delà de 0 dB, la valeur est ramenée à 0 dB.
- Pistes de FL Studio : FL Studio fait foi à la connexion ; ensuite chaque côté suit
  l'autre (l'écho d'une écriture est filtré par FlSyncCore).
- Tout se passe dans le thread de l'interface (c'est là que COM est initialisé).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Protocol

from core.fl_sync import FL_MAX_DB, FL_UNITY, FlTrackState, PollResult, db_to_fl_volume
from core.layout_manager import APP_SESSION_CAPS, FL_TRACK_CAPS, FULL_CAPS, EngineCaps, StripInfo
from core.mixer_model import (GAIN_MAX_DB, GAIN_MIN_DB, PAD_X_RANGE, SILENCE_DB, MixerLevels,
                              MixerParams, StripParams, amp_to_db, clamp_gain)
from core.profile_import import SOURCE_FL_TRACK, ChannelSource, Profile, ProfileChannel
from core.system_audio import (SOURCE_APP, SOURCE_CAPTURE, SOURCE_MASTER, SourceState, SystemAudio,
                               db_to_scalar, scalar_to_db, source_matches)

log = logging.getLogger(__name__)

SYSTEM_MAX_DB = 0.0              # Windows n'amplifie pas une application
PAN_MODE = "pan"                 # seul mode d'Intellipan : le panoramique des pistes de FL Studio
MAX_NAME = 30


class FlLink(Protocol):
    """Ce que le moteur utilise de FlSyncCore (et de DemoFlLink)."""

    connected: bool

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def poll(self) -> PollResult: ...

    def flush(self) -> int: ...

    def state(self, track: int) -> FlTrackState: ...

    def set_watch(self, tracks: Iterable[int]) -> None: ...

    def write_volume(self, track: int, volume: float) -> None: ...

    def write_pan(self, track: int, pan: float) -> None: ...

    def write_mute(self, track: int, muted: bool) -> None: ...

    def write_solo(self, track: int, solo: bool) -> None: ...


@dataclass(frozen=True)
class _Reading:
    """Ce que la source dit d'elle-même, dans les unités de la console."""

    gain_db: float
    muted: bool
    peak_db: tuple[float, float]
    pan: float = 0.0             # position de l'Intellipan (−0,5 → +0,5), pistes FL
    solo: bool = False


@dataclass
class _Channel:
    spec: ProfileChannel
    gain: float | None           # consigne en dB (None : aucune, on suit la source)
    mute: bool | None
    pan: float = 0.0
    gain_dirty: bool = False     # consigne pas encore appliquée à la source
    mute_dirty: bool = False
    present: bool = False
    keys: tuple[str, ...] = ()   # sessions Windows qui correspondent (sources `app`)
    reading: _Reading | None = None

    @property
    def is_fl(self) -> bool:
        return self.spec.source.type == SOURCE_FL_TRACK

    @property
    def track(self) -> int:
        return self.spec.source.track


def _clamp_db(db: float) -> float:
    return max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(db)))


def source_caps(source: ChannelSource) -> EngineCaps:
    """Commandes qui agissent vraiment sur ce genre de source."""
    return FL_TRACK_CAPS if source.type == SOURCE_FL_TRACK else APP_SESSION_CAPS


def source_label(source: ChannelSource) -> str:
    """Description lisible de la source d'un canal (info-bulle, menu)."""
    if source.type == SOURCE_APP:
        return source.key or "Application à choisir"
    if source.type == SOURCE_CAPTURE:
        return "Micro par défaut de Windows"
    if source.type == SOURCE_MASTER:
        return "Sortie par défaut de Windows"
    return "FL Studio · Master" if source.track == 0 else f"FL Studio · piste {source.track}"


class StandaloneEngine:
    """La console du mode autonome. `poll()` est appelé régulièrement par l'interface."""

    supports_sends = False
    persistent_state = False         # Windows garde ses volumes : rien à sauvegarder dans la config

    def __init__(self, profile: Profile, audio: SystemAudio, fl: FlLink | None = None, *,
                 fl_error: str = "", clock: Callable[[], float] = time.monotonic) -> None:
        self.audio = audio
        self.fl = fl
        self.fl_error = fl_error             # pourquoi la synchronisation FL Studio est absente
        self._clock = clock
        self._headphone = 0.0
        self._master_present = False
        self._master_peak = SILENCE_DB
        self._profile = profile
        self._channels: list[_Channel] = []
        self.set_profile(profile)

    # ------------------------------------------------------------------ profil
    @property
    def profile(self) -> Profile:
        """Le profil en cours, avec les noms et applications choisis depuis la console."""
        return replace(self._profile, channels=tuple(c.spec for c in self._channels))

    def set_profile(self, profile: Profile) -> None:
        """Nouvelle liste de canaux (le nombre de tranches peut changer : l'interface les refait)."""
        self._profile = profile
        # Les valeurs du profil sont des consignes pour Windows ; FL Studio, lui, fait foi.
        self._channels = [
            _Channel(spec, spec.gain, spec.mute,
                     gain_dirty=spec.gain is not None and spec.source.type != SOURCE_FL_TRACK,
                     mute_dirty=spec.mute is not None and spec.source.type != SOURCE_FL_TRACK)
            for spec in profile.channels
        ]
        if self.fl is not None:
            self.fl.set_watch(sorted({c.track for c in self._channels if c.is_fl}))

    def attach_fl(self, fl: FlLink | None, error: str = "") -> None:
        """Branche (ou, avec None, débranche) la synchronisation avec FL Studio en cours de route :
        le logiciel suivi a changé et le nouveau profil a des pistes de FL Studio (ou n'en a plus).
        L'ancienne liaison est fermée ; les canaux FL se relisent au prochain tour."""
        if self.fl is not None and self.fl is not fl:
            self.fl.close()
        self.fl, self.fl_error = fl, "" if fl is not None else error
        if fl is not None:
            fl.set_watch(sorted({c.track for c in self._channels if c.is_fl}))
        for channel in self._channels:
            if channel.is_fl:
                channel.present, channel.reading = False, None

    def assign_app(self, strip: int, key: str) -> None:
        """Relie un canal à une autre application (nom de processus ou clé du catalogue)."""
        channel = self._channel(strip)
        if channel is None or not key.strip():
            return
        channel.spec = replace(channel.spec, source=ChannelSource(SOURCE_APP, key.strip()))
        channel.present, channel.keys, channel.reading = False, (), None
        channel.gain_dirty = channel.gain is not None
        channel.mute_dirty = channel.mute is not None

    # ------------------------------------------------------ description (UI)
    @property
    def strip_count(self) -> int:
        return len(self._channels)

    @property
    def names(self) -> list[str]:
        return [c.spec.name for c in self._channels]

    @property
    def edition(self):          # pas d'édition de Voicemeeter ici
        return None

    def is_physical(self, strip: int) -> bool:
        return self._channels[strip].spec.physical

    def virtual_input(self, strip: int) -> int | None:
        return None             # pas de VAIO / AUX : l'appli de la tranche est son entrée

    def pad_modes(self, strip: int) -> tuple[str, ...]:
        return (PAN_MODE,) if self._channels[strip].is_fl else ()

    def strip_caps(self, strip: int) -> EngineCaps:
        return source_caps(self._channels[strip].spec.source)

    @property
    def caps(self) -> EngineCaps:
        """Ce que toute la console sait faire (l'intersection de ses canaux)."""
        caps = FULL_CAPS
        for channel in self._channels:
            caps = caps & source_caps(channel.spec.source)
        return caps

    def strip_infos(self) -> list[StripInfo]:
        """Les canaux tels que la disposition les voit (rôles du profil : jamais devinés)."""
        return [StripInfo(i, c.spec.physical, c.spec.name, c.spec.role, fixed=True,
                          caps=source_caps(c.spec.source))
                for i, c in enumerate(self._channels)]

    def source_label(self, strip: int) -> str:
        return source_label(self._channels[strip].spec.source)

    def max_gain_db(self, strip: int) -> float:
        """Plus haut gain possible : 0 dB pour Windows, le haut du fader pour FL Studio."""
        return FL_MAX_DB if self._channels[strip].is_fl else SYSTEM_MAX_DB

    def presence(self) -> tuple[bool, ...]:
        """Source présente (application lancée, micro branché, piste suivie) de chaque canal."""
        return tuple(c.present for c in self._channels)

    def native_solo(self, strip: int) -> bool:
        """Le solo de cette tranche est celui de FL Studio (les autres tranches ne sont pas coupées)."""
        channel = self._channels[strip]
        return channel.is_fl and self.fl is not None and self.fl.connected

    @property
    def fl_connected(self) -> bool:
        return self.fl is not None and self.fl.connected

    @property
    def uses_fl(self) -> bool:
        return any(c.is_fl for c in self._channels)

    # ------------------------------------------------------------ commandes
    def _channel(self, strip: int) -> _Channel | None:
        return self._channels[strip] if 0 <= strip < len(self._channels) else None

    def set_gain(self, strip: int, db: float) -> None:
        channel = self._channel(strip)
        if channel is None:
            return
        channel.gain = clamp_gain(db)
        channel.gain_dirty = True
        self._apply(channel)

    def set_mute(self, strip: int, muted: bool) -> None:
        channel = self._channel(strip)
        if channel is None:
            return
        channel.mute = bool(muted)
        channel.mute_dirty = True
        self._apply(channel)

    def set_solo(self, strip: int, solo: bool) -> None:
        channel = self._channel(strip)
        if channel is not None and channel.is_fl and self.fl is not None:
            self.fl.write_solo(channel.track, bool(solo))

    def set_pad(self, strip: int, mode: str, x: float, y: float) -> None:
        channel = self._channel(strip)
        if channel is None or not channel.is_fl or mode != PAN_MODE:
            return
        channel.pan = round(max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], float(x))), 3)
        if self.fl is not None:
            self.fl.write_pan(channel.track, channel.pan * 2.0)

    def set_headphone_gain(self, db: float) -> None:
        """Le fader MASTER règle le volume général de la sortie par défaut de Windows."""
        self._headphone = min(SYSTEM_MAX_DB, _clamp_db(db))
        self.audio.set_endpoint_volume(SOURCE_MASTER, db_to_scalar(self._headphone))

    def set_label(self, strip: int, text: str) -> None:
        channel = self._channel(strip)
        text = text.strip()[:MAX_NAME]
        if channel is not None and text:
            channel.spec = replace(channel.spec, name=text)

    def set_route(self, strip: int, route: int, enabled: bool) -> None:
        pass                    # pas de bus sans Voicemeeter

    def set_send(self, strip: int, route: int, db: float) -> None:
        pass

    def set_buffering(self, samples: int) -> None:
        pass                    # le tampon est celui du pilote audio de Windows

    def set_wdm_exclusive(self, exclusive: bool) -> None:
        pass

    def set_inserts(self, strips) -> None:
        pass                    # pas d'insert : aucun effet à libérer

    def apply_state(self, state: dict) -> None:
        pass                    # les volumes sont ceux de Windows

    # -------------------------------------------------------- application
    def _apply(self, channel: _Channel) -> None:
        """Envoie à la source les consignes en attente (si elle est là)."""
        if not channel.present:
            return
        source = channel.spec.source
        if channel.gain_dirty and channel.gain is not None:
            db = channel.gain if channel.is_fl else min(channel.gain, SYSTEM_MAX_DB)
            if source.type == SOURCE_APP:
                for key in channel.keys:
                    self.audio.set_app_volume(key, db_to_scalar(db))
            elif source.type == SOURCE_FL_TRACK:
                if self.fl is not None:
                    self.fl.write_volume(source.track, db_to_fl_volume(db))
            else:
                self.audio.set_endpoint_volume(source.type, db_to_scalar(db))
            channel.gain_dirty = False
            if channel.reading is not None:
                channel.reading = replace(channel.reading, gain_db=db)
        if channel.mute_dirty and channel.mute is not None:
            if source.type == SOURCE_APP:
                for key in channel.keys:
                    self.audio.set_app_mute(key, channel.mute)
            elif source.type == SOURCE_FL_TRACK:
                if self.fl is not None:
                    self.fl.write_mute(source.track, channel.mute)
            else:
                self.audio.set_endpoint_mute(source.type, channel.mute)
            channel.mute_dirty = False
            if channel.reading is not None:
                channel.reading = replace(channel.reading, muted=channel.mute)

    # ------------------------------------------------------------ lecture
    def _read(self, channel: _Channel, apps: dict[str, SourceState]
              ) -> tuple[_Reading | None, tuple[str, ...]]:
        source = channel.spec.source
        if source.type == SOURCE_FL_TRACK:
            return self._read_fl(channel), ()
        if source.type == SOURCE_APP:
            keys = tuple(key for key in apps if source_matches(source.key, key))
            if not keys:
                return None, ()
            states = [apps[key] for key in keys]
            state = SourceState(max(s.volume for s in states), all(s.muted for s in states),
                                max(s.peak for s in states), True)
            return _from_system(state), keys
        state = self.audio.endpoint_state(source.type)
        return (_from_system(state) if state.present else None), ()

    def _read_fl(self, channel: _Channel) -> _Reading | None:
        if self.fl is None or not self.fl.connected:
            return None
        track = self.fl.state(channel.track)
        if not track.known:
            return None
        return _Reading(_clamp_db(track.gain_db), track.muted, track.peak_db,
                        round(max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], track.pan / 2.0)), 3),
                        track.solo)

    def poll(self) -> tuple[MixerParams, MixerLevels]:
        """Relit toutes les sources, applique les consignes en attente, et renvoie l'état."""
        if self.fl is not None:
            self.fl.poll()
        apps = self.audio.app_states() if any(
            c.spec.source.type == SOURCE_APP for c in self._channels) else {}
        for channel in self._channels:
            reading, keys = self._read(channel, apps)
            appeared = reading is not None and not channel.present
            channel.present, channel.keys, channel.reading = reading is not None, keys, reading
            if appeared and not channel.is_fl:
                # La source vient d'apparaître : elle reçoit les consignes (le profil, ou ce
                # qui avait été réglé pour elle). FL Studio, lui, fait foi.
                channel.gain_dirty = channel.gain is not None
                channel.mute_dirty = channel.mute is not None
            if reading is not None:
                self._apply(channel)
                reading = channel.reading or reading
                if channel.is_fl:
                    channel.pan = reading.pan
                elif not channel.gain_dirty and channel.gain is not None:
                    channel.gain = round(reading.gain_db, 2)     # Windows a changé le volume : on le suit
                if not channel.mute_dirty and channel.mute is not None:
                    channel.mute = reading.muted
        if self.fl is not None:
            self.fl.flush()          # après les écritures de ce tour
        master = self.audio.endpoint_state(SOURCE_MASTER)
        self._master_present = master.present
        if master.present:
            self._headphone = min(SYSTEM_MAX_DB, _clamp_db(scalar_to_db(master.volume)))
        return self._params(), self._levels(master)

    def _params(self) -> MixerParams:
        strips = []
        for channel in self._channels:
            reading = channel.reading if channel.present else None
            gain = reading.gain_db if reading else (channel.gain if channel.gain is not None else 0.0)
            muted = reading.muted if reading else bool(channel.mute)
            pads = ((PAN_MODE, reading.pan if reading else channel.pan, 0.0),) if channel.is_fl else ()
            strips.append(StripParams(round(_clamp_db(gain), 2), muted, (), (), pads))
        return MixerParams(tuple(strips), round(self._headphone, 2))

    def _levels(self, master: SourceState) -> MixerLevels:
        strips = []
        floor = (SILENCE_DB, SILENCE_DB)
        for channel in self._channels:
            reading = channel.reading if channel.present else None
            strips.append(floor if reading is None or reading.muted else reading.peak_db)
        level = amp_to_db(master.peak) if master.present else SILENCE_DB
        for channel in self._channels:               # la sortie de FL Studio (ASIO) ne passe pas par Windows
            if channel.is_fl and channel.track == 0 and channel.reading is not None:
                level = max(level, *channel.reading.peak_db)
        return MixerLevels(tuple(strips), (level, level))

    # -------------------------------------------------------------- état
    def solo_states(self) -> dict[int, bool]:
        """Solo de FL Studio, par tranche (affichage seulement)."""
        return {i: c.reading.solo for i, c in enumerate(self._channels)
                if c.is_fl and c.reading is not None}

    def status(self) -> str:
        """Une ligne pour la barre d'état : ce qui marche, ce qui manque."""
        notes = []
        if not self.audio.available:
            notes.append(f"Audio Windows indisponible : {self.audio.reason}" if self.audio.reason
                         else "Audio Windows indisponible")
        if self.uses_fl:
            if self.fl is None:
                notes.append(f"FL Studio non synchronisé : {self.fl_error}" if self.fl_error
                             else "FL Studio non synchronisé")
            elif not self.fl.connected:
                notes.append("FL Studio : en attente du script VoiceConnector")
        total = len(self._channels)
        active = sum(c.present for c in self._channels)
        text = f"{active} source{'s' if active > 1 else ''} sur {total} active{'s' if active > 1 else ''}"
        return " · ".join([text, *notes])

    def shutdown(self) -> None:
        """Fin de session : FL Studio est prévenu et les ports MIDI sont fermés."""
        if self.fl is not None:
            self.fl.close()


def _from_system(state: SourceState) -> _Reading:
    """Un état Windows (volume 0 → 1, crête 0 → 1) dans les unités de la console."""
    db = scalar_to_db(state.volume)
    peak = SILENCE_DB if state.muted else amp_to_db(state.peak)
    return _Reading(_clamp_db(db), state.muted, (peak, peak))


# --------------------------------------------------------------------------- #
# Pistes de FL Studio simulées (--demo, auto-test)
# --------------------------------------------------------------------------- #
class DemoFlLink:
    """Mêmes méthodes que FlSyncCore, avec des pistes simulées aux niveaux animés."""

    DEFAULT_NAMES = ("Master", "Bus Batterie", "Synths", "FX", "Micro")

    def __init__(self, names: Iterable[str] = DEFAULT_NAMES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0 = clock()
        self.connected = True
        self.track_count = 0
        self._tracks: dict[int, FlTrackState] = {}
        for track, name in enumerate(names):
            self._tracks[track] = FlTrackState(volume=FL_UNITY, name=name, known=True)
        self.track_count = len(self._tracks)
        self.writes: list[tuple[str, int, float | bool]] = []

    def start(self) -> None:
        self.connected = True

    def stop(self) -> None:
        self.connected = False

    close = stop

    def set_watch(self, tracks: Iterable[int]) -> None:
        pass

    def state(self, track: int) -> FlTrackState:
        state = self._tracks.get(track)
        if state is None:
            state = self._tracks[track] = FlTrackState(name=f"Insert {track}", known=True)
        return state

    def poll(self) -> PollResult:
        t = self._clock() - self._t0
        for track, state in self._tracks.items():
            base = -22.0 + 9.0 * math.sin(t * (1.1 + track * 0.41) + track)
            level = SILENCE_DB if state.muted else base + state.gain_db
            state.peak_db = (level, level - 1.0)
        return PollResult()

    def flush(self) -> int:
        return 0

    def write_volume(self, track: int, volume: float) -> None:
        self.state(track).volume = max(0.0, min(1.0, float(volume)))
        self.writes.append(("volume", track, volume))

    def write_pan(self, track: int, pan: float) -> None:
        self.state(track).pan = max(-1.0, min(1.0, float(pan)))
        self.writes.append(("pan", track, pan))

    def write_mute(self, track: int, muted: bool) -> None:
        self.state(track).muted = bool(muted)
        self.writes.append(("mute", track, muted))

    def write_solo(self, track: int, solo: bool) -> None:
        self.state(track).solo = bool(solo)
        self.writes.append(("solo", track, solo))

"""
Liaison du Studio avec FL Studio : le vrai transport, le vrai channel rack, les vrais patterns
et la vraie playlist du projet ouvert (sans Qt).

Le dialogue passe par le script MIDI de FL Studio (assets/flstudio/device_VoiceConnector.py), sur
les mêmes ports virtuels que la synchronisation du mixer (core/fl_sync.py) ; le format des
messages est décrit dans core/studio_proto.py. `FlStudioLink` :

- dit bonjour au script (et le redit toutes les 3 s) tant qu'il ne répond pas, et se déclare
  déconnecté s'il se tait 12 s ou s'il dit au revoir ;
- tient à jour un `DawState` d'après ce que le script envoie ;
- traduit les ordres du Studio (jouer, changer un pas, baisser un canal…) en messages ; les
  réglages continus (volume, panoramique, tempo, position) sont regroupés : seule la dernière
  valeur de chaque tour part ;
- applique tout de suite l'ordre à l'état affiché (le Studio répond sans attendre FL Studio) et
  ignore pendant un instant les valeurs de FL Studio qui arriveraient avec du retard sur ce réglage.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Mapping

from core import fl_sync
from core import studio_proto as sp
from core.daw_model import (
    CAP_METERS,
    CAP_METRONOME,
    CAP_PATTERNS,
    CAP_PLAYLIST,
    CAP_RACK,
    CAP_RECORD,
    CAP_SEEK,
    CAP_SONG_MODE,
    CAP_STEPS,
    CAP_TEMPO,
    CAP_TEMPO_WRITE,
    CAP_TRANSPORT,
    MAX_STEPS,
    MAX_TEMPO,
    MIN_TEMPO,
    DawChannel,
    DawLink,
    DawPattern,
    DawTrack,
    clamp,
    resize_steps,
    steps_for_length,
)
from core.fl_sync import Transport, TransportError

log = logging.getLogger(__name__)

PING_S, RETRY_S, TIMEOUT_S = 3.0, 1.5, 12.0
GUARD_S = 0.45                           # après un ordre, les valeurs de FL Studio qui arrivent en retard sont ignorées
STEP_GUARD_S = 0.7
VERIFY_DELAY_S = 0.3                     # la garde passée, on redemande à FL Studio ce qu'il en est vraiment
REFRESH_EVERY_S = 60.0                   # et, au cas où un message se serait perdu, tout est redemandé de temps en temps
DEFAULT_CHANNELS, DEFAULT_TRACKS = 64, 32
AUTO_STEPS = 0                           # nombre de pas : suit la longueur du pattern

CAPABILITIES = frozenset({CAP_TRANSPORT, CAP_RECORD, CAP_SONG_MODE, CAP_METRONOME, CAP_TEMPO, CAP_TEMPO_WRITE,
                          CAP_SEEK, CAP_RACK, CAP_STEPS, CAP_PATTERNS, CAP_PLAYLIST, CAP_METERS})


def online(method):
    """Ordre ignoré tant que FL Studio n'est pas connecté : rien ne partirait, et l'écran mentirait."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self.state.connected:
            return method(self, *args, **kwargs)
        return None

    return wrapper


class FlStudioLink(DawLink):
    key = "fl"
    label = "FL Studio"

    def __init__(self, transport: Transport, *, steps: int = AUTO_STEPS, channels: int = DEFAULT_CHANNELS,
                 tracks: int = DEFAULT_TRACKS, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._transport = transport
        self._clock = clock
        self._channel_limit = max(1, min(sp.MAX_CHANNELS, channels))
        self._track_limit = max(1, min(sp.MAX_TRACKS, tracks))
        self._steps_choice = AUTO_STEPS if steps <= 0 else int(clamp(steps, 1, MAX_STEPS))
        self._pending: dict[Any, bytes] = {}
        self._guard: dict[Any, float] = {}
        self._verify: dict[int, float] = {}               # partie du projet -> heure où la redemander à FL Studio
        self._next_refresh = 0.0
        self._last_rx = 0.0
        self._next_hello = 0.0
        self.fl_version = ""
        state = self.state
        state.daw = self.label
        state.capabilities = CAPABILITIES
        state.step_count = self._steps_choice or 16
        state.message = "En attente de FL Studio…"

    # ------------------------------------------------------------ cycle de vie
    @property
    def steps_choice(self) -> int:
        """Nombre de pas demandé (0 : automatique, d'après la longueur du pattern)."""
        return self._steps_choice

    def start(self) -> None:
        super().start()
        self._next_hello = 0.0

    def stop(self) -> None:
        if self.started and self._transport.is_open:
            self._safe_send(sp.bye())
        super().stop()
        self.state.message = "Liaison arrêtée."

    def close(self) -> None:
        self.stop()
        try:
            self._transport.close()
        except Exception as exc:
            log.debug("Fermeture du transport MIDI : %s", exc)

    # -------------------------------------------------------------------- poll
    def poll(self) -> bool:
        now = self._clock()
        changed = False
        try:
            incoming = self._transport.receive()
        except Exception as exc:
            log.debug("Lecture MIDI impossible : %s", exc)
            incoming = []
        for raw in incoming:
            message = fl_sync.parse(raw)
            event = sp.decode(message) if message is not None else None
            if event is None:
                continue
            self._last_rx = now
            changed |= self._apply(event, now)
        if self.started:
            changed |= self._keep_alive(now)
        self._flush()
        self._verify_due(now)
        return changed

    def _verify_due(self, now: float) -> None:
        """FL Studio fait foi : après nos ordres (et de temps en temps), on lui redemande l'état réel,
        pour que l'écran ne reste pas faux si FL Studio a refusé un ordre ou si un message s'est perdu."""
        if not self.state.connected:
            self._verify.clear()
            return
        ready = [scope for scope, due in self._verify.items() if due <= now]
        scope = 0
        for part in ready:
            scope |= part
            del self._verify[part]
        if now >= self._next_refresh:
            self._next_refresh = now + REFRESH_EVERY_S
            scope = sp.SCOPE_ALL
        if scope:
            self._safe_send(sp.refresh(scope))

    def _expect(self, scope: int, seconds: float) -> None:
        due = self._clock() + seconds + VERIFY_DELAY_S
        for part in (sp.SCOPE_TRANSPORT, sp.SCOPE_RACK, sp.SCOPE_STEPS, sp.SCOPE_PATTERNS, sp.SCOPE_PLAYLIST):
            if scope & part:
                self._verify[part] = max(self._verify.get(part, 0.0), due)

    def _keep_alive(self, now: float) -> bool:
        state = self.state
        changed = False
        if state.connected:
            if now - self._last_rx > TIMEOUT_S:
                log.info("FL Studio ne répond plus (Studio interrompu).")
                state.connected = False
                state.message = "FL Studio ne répond plus."
                self._next_hello = now
                changed = True
            elif now >= self._next_hello:
                self._safe_send(self._hello(full=False))
                self._next_hello = now + PING_S
        if not state.connected and now >= self._next_hello:
            self._safe_send(self._hello(full=True))
            self._next_hello = now + RETRY_S
        return changed

    def _hello(self, full: bool) -> bytes:
        return sp.hello(full, self.state.step_count, self._channel_limit, self._track_limit)

    # ----------------------------------------------------------- FL → Studio
    def _guarded(self, key: Any, now: float) -> bool:
        return self._guard.get(key, 0.0) > now

    def _apply(self, event: Any, now: float) -> bool:
        state = self.state
        if isinstance(event, sp.FlGoodbye):
            was = state.connected
            state.connected = False
            state.message = "FL Studio s'est fermé."
            self._next_hello = now + RETRY_S
            return was
        if isinstance(event, sp.StudioAck):
            changed = not state.connected
            if changed:
                self._next_refresh = now + REFRESH_EVERY_S
            state.connected = True
            state.message = ""
            self.fl_version = event.fl_version
            state.daw = f"{self.label} {event.fl_version}".strip()
            return changed
        if not state.connected:                              # un message sans ACK : la liaison est établie
            state.connected = True
            state.message = ""
        if isinstance(event, sp.ProjectName):
            return self._set(state, "project", event.name)
        if isinstance(event, sp.TransportState):
            return self._apply_transport(event, now)
        if isinstance(event, sp.ChannelCount):
            before = len(state.channels)
            state.channels = [c for c in state.channels if c.index < event.count]
            return len(state.channels) != before
        if isinstance(event, sp.ChannelInfo):
            return self._apply_channel(event, now)
        if isinstance(event, sp.StepsData):
            return self._apply_steps(event, now)
        if isinstance(event, sp.PatternList):
            return self._apply_pattern_list(event)
        if isinstance(event, sp.PatternInfo):
            return self._apply_pattern(event)
        if isinstance(event, sp.PlaylistCount):
            return self._apply_track_count(event.count)
        if isinstance(event, sp.PlaylistTrack):
            return self._apply_track(event)
        if isinstance(event, sp.Activity):
            return self._apply_activity(event)
        return False

    @staticmethod
    def _set(target: Any, attribute: str, value: Any) -> bool:
        if getattr(target, attribute) == value:
            return False
        setattr(target, attribute, value)
        return True

    def _apply_transport(self, event: sp.TransportState, now: float) -> bool:
        transport = self.state.transport
        changed = False
        for attribute, value in (("playing", event.playing), ("recording", event.recording),
                                 ("song_mode", event.song_mode), ("metronome", event.metronome),
                                 ("ppq", event.ppq), ("beats_per_bar", event.beats_per_bar)):
            if not self._guarded(("transport", attribute), now):
                changed |= self._set(transport, attribute, value)
        if event.tempo > 0 and not self._guarded("tempo", now):
            changed |= self._set(transport, "tempo", event.tempo)
        if not self._guarded("seek", now):
            changed |= self._set(transport, "ticks", event.ticks)
        return changed

    def _channel(self, index: int) -> DawChannel:
        channels = self.state.channels
        found = next((c for c in channels if c.index == index), None)
        if found is not None:
            return found
        found = DawChannel(index, name=f"Canal {index + 1}", steps=[False] * self.state.step_count)
        channels.append(found)
        channels.sort(key=lambda c: c.index)
        return found

    def _apply_channel(self, event: sp.ChannelInfo, now: float) -> bool:
        channel = self._channel(event.index)
        changed = False
        for attribute, value in (("name", event.name), ("color", event.color), ("kind", event.kind)):
            changed |= self._set(channel, attribute, value)
        i = event.index
        if not self._guarded(("ch_volume", i), now):
            changed |= self._set(channel, "volume", event.volume)
        if not self._guarded(("ch_pan", i), now):
            changed |= self._set(channel, "pan", event.pan)
        if not self._guarded(("ch_mute", i), now):
            changed |= self._set(channel, "muted", event.muted)
        if not self._guarded(("ch_solo", i), now):
            changed |= self._set(channel, "solo", event.solo)
        if not self._guarded("ch_select", now):
            changed |= self._set(channel, "selected", event.selected)
        return changed

    def _apply_steps(self, event: sp.StepsData, now: float) -> bool:
        state = self.state
        if state.pattern not in (-1, event.pattern) and event.pattern > 0:
            return False                                      # pas d'un autre pattern, arrivés trop tard
        if self._guarded(("steps", event.channel), now) or len(event.steps) != state.step_count:
            return False                                      # ligne calculée pour un autre nombre de pas : elle va être renvoyée
        channel = self._channel(event.channel)
        cells = resize_steps(event.steps, state.step_count)
        if channel.steps == cells:
            return False
        channel.steps = cells
        return True

    def _apply_pattern_list(self, event: sp.PatternList) -> bool:
        state = self.state
        known = {p.index: p for p in state.patterns}
        patterns = [known.get(i) or DawPattern(i, name=f"Pattern {i}") for i in event.indices]
        pattern = event.selected if event.selected > 0 else -1
        changed = [p.index for p in patterns] != [p.index for p in state.patterns] or pattern != state.pattern
        state.patterns, state.pattern = patterns, pattern
        return self._refresh_steps_choice() or changed

    def _apply_pattern(self, event: sp.PatternInfo) -> bool:
        state = self.state
        pattern = next((p for p in state.patterns if p.index == event.index), None)
        changed = False
        if pattern is None:
            pattern = DawPattern(event.index)
            state.patterns.append(pattern)
            state.patterns.sort(key=lambda p: p.index)
            changed = True
        for attribute, value in (("name", event.name), ("color", event.color), ("length_steps", event.length_steps)):
            changed |= self._set(pattern, attribute, value)
        if event.selected:
            changed |= self._set(state, "pattern", event.index)
        return self._refresh_steps_choice() or changed

    def _refresh_steps_choice(self) -> bool:
        """Nombre de pas affiché : celui qui est demandé, ou (automatique) celui du pattern en cours."""
        state = self.state
        if self._steps_choice:
            wanted = self._steps_choice
        else:
            pattern = state.current_pattern()
            wanted = steps_for_length(pattern.length_steps) if pattern is not None else 16
        if wanted == state.step_count:
            return False
        state.step_count = wanted
        for channel in state.channels:
            channel.steps = resize_steps(channel.steps, wanted)
        self._safe_send(sp.step_count(wanted))
        return True

    def _apply_track_count(self, count: int) -> bool:
        state = self.state
        before = [t.index for t in state.tracks]
        tracks = {t.index: t for t in state.tracks if 1 <= t.index <= count}
        for index in range(1, count + 1):
            tracks.setdefault(index, DawTrack(index, name=f"Piste {index}"))
        state.tracks = [tracks[i] for i in sorted(tracks)]
        return [t.index for t in state.tracks] != before

    def _apply_track(self, event: sp.PlaylistTrack) -> bool:
        state = self.state
        track = state.track(event.index)
        changed = False
        if track is None:
            track = DawTrack(event.index)
            state.tracks.append(track)
            state.tracks.sort(key=lambda t: t.index)
            changed = True
        for attribute, value in (("name", event.name), ("color", event.color), ("muted", event.muted),
                                 ("solo", event.solo), ("selected", event.selected)):
            changed |= self._set(track, attribute, value)
        return changed

    def _apply_activity(self, event: sp.Activity) -> bool:
        changed = False
        if event.target == "tracks":
            for offset, level in enumerate(event.levels):
                track = self.state.track(event.first + offset)
                if track is not None:
                    changed |= self._set(track, "level", level)
                    changed |= self._set(track, "active", level > 0)
        else:
            for offset, level in enumerate(event.levels):
                channel = self.state.channel(event.first + offset)
                if channel is not None:
                    changed |= self._set(channel, "level", level)
        return changed

    # ----------------------------------------------------------- Studio → FL
    def _write(self, key: Any, message: bytes, guard: str | tuple | None = None, seconds: float = GUARD_S,
               scope: int = 0) -> None:
        """Ordre continu : seule la dernière valeur de chaque réglage part au prochain tour."""
        self._pending[key] = message
        if guard is not None:
            self._guard[guard] = self._clock() + seconds
        if scope:
            self._expect(scope, seconds)

    def _flush(self) -> None:
        if not self._pending:
            return
        if not self.state.connected:
            self._pending.clear()                             # FL Studio fait foi à la connexion : rien à rejouer
            return
        pending, self._pending = self._pending, {}
        for message in pending.values():
            self._safe_send(message)

    def _now_send(self, message: bytes, guard: Any = None, seconds: float = GUARD_S, scope: int = 0) -> None:
        if guard is not None:
            self._guard[guard] = self._clock() + seconds
        if scope:
            self._expect(scope, seconds)
        if self.state.connected:
            self._safe_send(message)

    def _safe_send(self, message: bytes) -> None:
        try:
            self._transport.send(message)
        except Exception as exc:                              # port fermé, câble MIDI virtuel supprimé…
            log.debug("Envoi MIDI impossible : %s", exc)

    # --- transport
    @online
    def play_pause(self) -> None:
        self._now_send(sp.transport(sp.ACTION_PLAY), ("transport", "playing"), scope=sp.SCOPE_TRANSPORT)
        self.state.transport.playing = not self.state.transport.playing

    @online
    def stop_playback(self) -> None:
        self._now_send(sp.transport(sp.ACTION_STOP), ("transport", "playing"), scope=sp.SCOPE_TRANSPORT)
        self.state.transport.playing = False

    @online
    def record(self) -> None:
        self._now_send(sp.transport(sp.ACTION_RECORD), ("transport", "recording"), scope=sp.SCOPE_TRANSPORT)
        self.state.transport.recording = not self.state.transport.recording

    @online
    def toggle_song_mode(self) -> None:
        self._now_send(sp.transport(sp.ACTION_SONG_MODE), ("transport", "song_mode"), scope=sp.SCOPE_TRANSPORT)
        self.state.transport.song_mode = not self.state.transport.song_mode

    @online
    def toggle_metronome(self) -> None:
        self._now_send(sp.transport(sp.ACTION_METRONOME), ("transport", "metronome"), scope=sp.SCOPE_TRANSPORT)
        self.state.transport.metronome = not self.state.transport.metronome

    @online
    def set_tempo(self, bpm: float) -> None:
        bpm = round(clamp(bpm, MIN_TEMPO, MAX_TEMPO), 3)
        self.state.transport.tempo = bpm
        self._write("tempo", sp.tempo(bpm), "tempo", 0.8, sp.SCOPE_TRANSPORT)

    @online
    def seek(self, beats: float) -> None:
        transport = self.state.transport
        ticks = max(0, round(beats * transport.ppq))
        transport.ticks = ticks
        self._write("seek", sp.seek(ticks), "seek", 0.6, sp.SCOPE_TRANSPORT)

    @online
    def rewind(self) -> None:
        self._now_send(sp.transport(sp.ACTION_REWIND), "seek", 0.6, sp.SCOPE_TRANSPORT)
        self.state.transport.ticks = 0

    # --- channel rack
    def set_step_count(self, steps: int) -> None:
        """Nombre de pas affichés : 16, 32, 48 ou 64 ; 0 pour qu'il suive la longueur du pattern."""
        self._steps_choice = AUTO_STEPS if steps <= 0 else int(clamp(steps, 1, MAX_STEPS))
        if not self._refresh_steps_choice() and self.state.connected:
            self._safe_send(sp.step_count(self.state.step_count))

    @online
    def toggle_step(self, channel: int, step: int, value: bool | None = None) -> None:
        target = self.state.channel(channel)
        if target is None or not 0 <= step < self.state.step_count:
            return
        target.steps = resize_steps(target.steps, self.state.step_count)
        wanted = (not target.steps[step]) if value is None else bool(value)
        if target.steps[step] == wanted:
            return
        target.steps[step] = wanted
        self._now_send(sp.step(channel, step, wanted), ("steps", channel), STEP_GUARD_S, sp.SCOPE_STEPS)

    @online
    def set_channel_volume(self, channel: int, value: float) -> None:
        target = self.state.channel(channel)
        if target is None:
            return
        target.volume = clamp(value, 0.0, 1.0)
        self._write(("volume", channel), sp.channel_volume(channel, target.volume), ("ch_volume", channel),
                    scope=sp.SCOPE_RACK)

    @online
    def set_channel_pan(self, channel: int, value: float) -> None:
        target = self.state.channel(channel)
        if target is None:
            return
        target.pan = clamp(value, -1.0, 1.0)
        self._write(("pan", channel), sp.channel_pan(channel, target.pan), ("ch_pan", channel), scope=sp.SCOPE_RACK)

    @online
    def set_channel_mute(self, channel: int, muted: bool) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.muted = bool(muted)
            self._now_send(sp.channel_mute(channel, muted), ("ch_mute", channel), scope=sp.SCOPE_RACK)

    @online
    def set_channel_solo(self, channel: int, solo: bool) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.solo = bool(solo)
            self._now_send(sp.channel_solo(channel, solo), ("ch_solo", channel), scope=sp.SCOPE_RACK)

    @online
    def select_channel(self, channel: int) -> None:
        if self.state.channel(channel) is None:
            return
        for other in self.state.channels:
            other.selected = other.index == channel
        self._now_send(sp.channel_select(channel), "ch_select", 0.6, sp.SCOPE_RACK)

    @online
    def select_pattern(self, pattern: int) -> None:
        if not any(p.index == pattern for p in self.state.patterns):
            return
        self.state.pattern = pattern
        self._refresh_steps_choice()
        self._now_send(sp.pattern_select(pattern), scope=sp.SCOPE_PATTERNS | sp.SCOPE_STEPS)

    # --- playlist
    @online
    def set_track_mute(self, track: int, muted: bool) -> None:
        target = self.state.track(track)
        if target is not None:
            target.muted = bool(muted)
            self._now_send(sp.track_mute(track, muted), scope=sp.SCOPE_PLAYLIST)

    @online
    def set_track_solo(self, track: int, solo: bool) -> None:
        target = self.state.track(track)
        if target is not None:
            target.solo = bool(solo)
            self._now_send(sp.track_solo(track, solo), scope=sp.SCOPE_PLAYLIST)


def _setting(section: Mapping, key: str, default: int, low: int = 0, high: int = 10 ** 6) -> int:
    """Entier d'un réglage du fichier de configuration ; la valeur par défaut si elle est absente ou mal écrite."""
    try:
        return int(clamp(int(float(section.get(key, default))), low, high))
    except (TypeError, ValueError, OverflowError):
        return default


def open_fl_studio_link(opener: Callable[[], Transport], cfg: Mapping | None = None
                        ) -> tuple[FlStudioLink | None, str]:
    """Ouvre le transport et lance la liaison : (liaison, « ») ou (None, raison).

    Ne lève jamais : sans mido, sans loopMIDI ou sans script dans FL Studio, le Studio affiche la
    raison et reste utilisable en démonstration."""
    section = (cfg or {}).get("studio", {})
    if not isinstance(section, Mapping):
        section = {}
    try:
        transport = opener()
    except TransportError as exc:
        log.info("Studio FL Studio indisponible : %s", exc)
        return None, str(exc)
    except Exception as exc:                                  # bibliothèque MIDI défaillante
        log.warning("Studio FL Studio : ports MIDI inutilisables : %s", exc)
        return None, f"Ports MIDI inutilisables : {exc}"
    link = FlStudioLink(transport, steps=_setting(section, "steps", AUTO_STEPS, 0, MAX_STEPS),
                        channels=_setting(section, "channels", DEFAULT_CHANNELS, 1, sp.MAX_CHANNELS),
                        tracks=_setting(section, "tracks", DEFAULT_TRACKS, 1, sp.MAX_TRACKS))
    link.start()
    return link, ""

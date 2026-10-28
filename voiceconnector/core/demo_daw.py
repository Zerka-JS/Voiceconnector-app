"""
Un logiciel de musique simulé, pour essayer le Studio sans rien installer (sans Qt).

`DemoDaw` se comporte comme une vraie liaison (core/daw_model.py) : un transport qui avance avec
le tempo, un channel rack de six canaux dont les pas se modifient, trois patterns de longueurs
différentes, une playlist de huit pistes dont l'arrangement s'allume pendant la lecture. Rien n'est
envoyé à aucun logiciel : c'est ce que montre le Studio tant que FL Studio, Cubase ou les autres
ne sont pas reliés, et ce que les tests de la fenêtre utilisent.
"""

from __future__ import annotations

import math
import time
from typing import Callable

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

CAPABILITIES = frozenset({CAP_TRANSPORT, CAP_RECORD, CAP_SONG_MODE, CAP_METRONOME, CAP_TEMPO, CAP_TEMPO_WRITE,
                          CAP_SEEK, CAP_RACK, CAP_STEPS, CAP_PATTERNS, CAP_PLAYLIST, CAP_METERS})

DEMO_MESSAGE = "Démonstration : aucun logiciel de musique n'est piloté."
PPQ = 96
SONG_BARS = 16                      # l'arrangement de la démonstration dure 16 mesures, puis recommence
NOTE_DECAY_PER_S = 4.0              # un canal qui vient de jouer s'éteint en un quart de seconde

# (nom, couleur, type, volume, panoramique)
CHANNELS = (
    ("Kick", "#e0603a", "sampler", 0.80, 0.0),
    ("Caisse claire", "#e0a43a", "sampler", 0.72, 0.05),
    ("Clap", "#d8c84a", "sampler", 0.66, -0.10),
    ("Charley", "#7ac06a", "sampler", 0.55, 0.25),
    ("Basse", "#4aa0d8", "plugin", 0.78, 0.0),
    ("Piano", "#a070d8", "plugin", 0.70, -0.15),
)

# (numéro, nom, couleur, longueur en pas, {canal: motif}) ; « x » = pas allumé
PATTERNS = (
    (1, "Batterie", "#e0603a", 16, {
        "Kick": "x...x...x...x...",
        "Caisse claire": "....x.......x...",
        "Clap": "....x.......x..x",
        "Charley": "x.x.x.x.x.x.x.xx",
    }),
    (2, "Basse", "#4aa0d8", 16, {
        "Kick": "x...x...x...x...",
        "Basse": "x..x..x...x..x..",
    }),
    (3, "Mélodie", "#a070d8", 32, {
        "Basse": "x.......x.......x.......x.......",
        "Piano": "x.....x.x...x...x.....x.x...x.x.",
    }),
)

TRACKS = (
    ("Pads", "#a070d8"), ("Automation", "#6a8f8a"), ("Basse", "#4aa0d8"), ("Accords", "#d8a04a"),
    ("Ambiance", "#4ac0a0"), ("Foley", "#c07a5a"), ("Lead", "#e0603a"), ("Effets", "#8a8ad8"),
)

# piste (à partir de 0) -> clips de la playlist, en mesures [début, fin[
ARRANGEMENT = {
    0: ((0, 16),), 1: ((4, 12),), 2: ((2, 16),), 3: ((0, 8), (12, 16)),
    4: ((0, 16),), 5: ((6, 7), (10, 11)), 6: ((8, 16),), 7: ((7, 8), (15, 16)),
}


def _cells(pattern: str, length: int) -> list[bool]:
    return resize_steps([char == "x" for char in pattern], length)


class DemoDaw(DawLink):
    key = "demo"
    label = "Démonstration"

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, steps: int = 0,
                 tempo: float = 118.0) -> None:
        super().__init__()
        self._clock = clock
        self._last = clock()
        self._beats = 0.0                                   # position depuis le début, en noires
        self._steps_choice = max(0, min(MAX_STEPS, int(steps)))
        self._grids: dict[tuple[int, int], list[bool]] = {}      # (pattern, canal) -> pas allumés
        state = self.state
        state.daw = self.label
        state.project = "Projet de démonstration"
        state.capabilities = CAPABILITIES
        state.message = DEMO_MESSAGE
        state.transport.tempo = round(clamp(tempo, MIN_TEMPO, MAX_TEMPO), 3)
        state.transport.ppq = PPQ
        state.transport.ticks = 0
        state.channels = [DawChannel(i, name=name, color=color, kind=kind, volume=volume, pan=pan,
                                     selected=i == 0)
                          for i, (name, color, kind, volume, pan) in enumerate(CHANNELS)]
        state.patterns = [DawPattern(number, name=name, color=color, length_steps=length)
                          for number, name, color, length, _ in PATTERNS]
        state.pattern = PATTERNS[0][0]
        state.tracks = [DawTrack(i + 1, name=name, color=color) for i, (name, color) in enumerate(TRACKS)]
        names = [name for name, *_ in CHANNELS]
        for number, _, _, length, motifs in PATTERNS:
            for index, name in enumerate(names):
                self._grids[(number, index)] = _cells(motifs.get(name, ""), length)
        self._apply_step_count()

    # ------------------------------------------------------------ cycle de vie
    @property
    def steps_choice(self) -> int:
        """Nombre de pas demandé (0 : automatique, d'après la longueur du pattern)."""
        return self._steps_choice

    def start(self) -> None:
        super().start()
        self._last = self._clock()
        self.state.connected = True
        self.state.message = DEMO_MESSAGE

    # -------------------------------------------------------------------- poll
    def poll(self) -> bool:
        """Fait avancer la démonstration ; renvoie True quand ce qui est affiché a bougé."""
        now = self._clock()
        elapsed = max(0.0, min(now - self._last, 0.5))       # un long gel ne fait pas sauter la lecture
        self._last = now
        if not self.started:
            return False
        transport = self.state.transport
        changed = False
        if transport.playing and elapsed > 0.0:
            self._advance(elapsed * transport.tempo / 60.0)
            changed = True
        return self._update_levels(elapsed) or changed

    def _loop_beats(self) -> float:
        """Durée avant que la lecture reboucle : le pattern en mode pattern, l'arrangement en mode morceau."""
        if self.state.transport.song_mode:
            return SONG_BARS * self.state.transport.beats_per_bar
        pattern = self.state.current_pattern()
        return (pattern.length_steps if pattern else 16) / 4.0

    def _advance(self, beats: float) -> None:
        before = self._beats
        loop = self._loop_beats()
        after = (before + beats) % loop if loop > 0 else 0.0
        self._beats = after
        self._sync_ticks()
        first = int(before * 4) + 1                          # les pas (16es de note) franchis, à partir du suivant
        if after >= before:
            crossed = range(first, int(after * 4) + 1)
        else:                                                # la lecture vient de reboucler
            crossed = [*range(first, int(loop * 4)), *range(0, int(after * 4) + 1)]
        self._light(crossed)

    def _sync_ticks(self) -> None:
        self.state.transport.ticks = int(self._beats * PPQ)

    def _light(self, steps) -> None:
        """Les canaux dont un pas vient d'être franchi s'allument (comme l'activité de FL Studio)."""
        pattern = self.state.current_pattern()
        if pattern is None:
            return
        length = max(1, pattern.length_steps)
        for step in steps:
            for channel in self.state.channels:
                cells = self._grids.get((pattern.index, channel.index))
                if cells and cells[(step % length) % len(cells)]:
                    channel.level = 1.0

    def _update_levels(self, elapsed: float) -> bool:
        changed = False
        for channel in self.state.channels:
            level = max(0.0, channel.level - elapsed * NOTE_DECAY_PER_S)
            if level != channel.level:
                channel.level, changed = level, True
        transport = self.state.transport
        bar = int(self._beats // max(1, transport.beats_per_bar))
        for index, track in enumerate(self.state.tracks):
            active = (transport.playing and transport.song_mode
                      and any(start <= bar < end for start, end in ARRANGEMENT.get(index, ())))
            level = 0.35 + 0.5 * abs(math.sin(self._beats * 1.7 + index)) if active else 0.0
            if track.active != active or abs(track.level - level) > 1e-6:
                track.active, track.level, changed = active, level, True
        return changed

    # --------------------------------------------------------------- transport
    def play_pause(self) -> None:
        transport = self.state.transport
        transport.playing = not transport.playing
        self._last = self._clock()
        if transport.playing:
            self._light([int(self._beats * 4)])                # le pas sous le curseur sonne dès le départ

    def stop_playback(self) -> None:
        self.state.transport.playing = False
        self._beats = 0.0
        self._sync_ticks()

    def record(self) -> None:
        self.state.transport.recording = not self.state.transport.recording

    def toggle_song_mode(self) -> None:
        transport = self.state.transport
        transport.song_mode = not transport.song_mode
        self._beats %= self._loop_beats()
        self._sync_ticks()

    def toggle_metronome(self) -> None:
        self.state.transport.metronome = not self.state.transport.metronome

    def set_tempo(self, bpm: float) -> None:
        self.state.transport.tempo = round(clamp(bpm, MIN_TEMPO, MAX_TEMPO), 3)

    def seek(self, beats: float) -> None:
        self._beats = max(0.0, float(beats)) % self._loop_beats()
        self._sync_ticks()

    def rewind(self) -> None:
        self.seek(0.0)

    # ------------------------------------------------------------ channel rack
    def _apply_step_count(self) -> None:
        state = self.state
        if self._steps_choice:
            wanted = self._steps_choice
        else:
            pattern = state.current_pattern()
            wanted = steps_for_length(pattern.length_steps) if pattern is not None else 16
        state.step_count = wanted
        for channel in state.channels:
            cells = self._grids.setdefault((state.pattern, channel.index), [])
            channel.steps = resize_steps(cells, wanted)

    def set_step_count(self, steps: int) -> None:
        self._steps_choice = 0 if steps <= 0 else int(clamp(steps, 1, MAX_STEPS))
        self._apply_step_count()

    def toggle_step(self, channel: int, step: int, value: bool | None = None) -> None:
        target = self.state.channel(channel)
        if target is None or not 0 <= step < self.state.step_count:
            return
        cells = self._grids.setdefault((self.state.pattern, channel), [])
        cells[:] = resize_steps(cells, max(len(cells), step + 1))
        wanted = (not cells[step]) if value is None else bool(value)
        cells[step] = wanted
        target.steps = resize_steps(cells, self.state.step_count)

    def set_channel_volume(self, channel: int, value: float) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.volume = clamp(value, 0.0, 1.0)

    def set_channel_pan(self, channel: int, value: float) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.pan = clamp(value, -1.0, 1.0)

    def set_channel_mute(self, channel: int, muted: bool) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.muted = bool(muted)

    def set_channel_solo(self, channel: int, solo: bool) -> None:
        target = self.state.channel(channel)
        if target is not None:
            target.solo = bool(solo)

    def select_channel(self, channel: int) -> None:
        if self.state.channel(channel) is None:
            return
        for other in self.state.channels:
            other.selected = other.index == channel

    def select_pattern(self, pattern: int) -> None:
        if not any(p.index == pattern for p in self.state.patterns):
            return
        self.state.pattern = pattern
        self._apply_step_count()
        self._beats %= self._loop_beats()
        self._sync_ticks()

    # ----------------------------------------------------------------- pistes
    def set_track_mute(self, track: int, muted: bool) -> None:
        target = self.state.track(track)
        if target is not None:
            target.muted = bool(muted)

    def set_track_solo(self, track: int, solo: bool) -> None:
        target = self.state.track(track)
        if target is not None:
            target.solo = bool(solo)

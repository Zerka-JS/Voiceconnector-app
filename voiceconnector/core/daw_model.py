"""
Modèle d'un logiciel de musique (DAW) piloté à distance : ce que le Studio affiche, et les ordres
qu'il peut envoyer (sans Qt).

Le Studio ne parle jamais à FL Studio, Cubase ou Ableton directement : il lit un `DawState` et
appelle les méthodes d'un `DawLink`. Chaque liaison traduit :

- `FlStudioLink` (core/fl_studio_link.py) : le script embarqué dans FL Studio, en SysEx ;
- `McuLink` (core/mcu_link.py) : le protocole Mackie Control, compris par Cubase / Nuendo,
  Studio One, Ableton Live, REAPER, Logic, Bitwig… ;
- `DemoDaw` (core/demo_daw.py) : un logiciel simulé, pour essayer la fenêtre sans rien installer.

Chaque liaison déclare ses `capabilities` : le Studio grise ce qu'elle ne sait pas faire (Mackie
Control ne connaît ni channel rack ni pas de séquenceur, par exemple).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# --------------------------------------------------------------------------- #
# Capacités d'une liaison
# --------------------------------------------------------------------------- #
CAP_TRANSPORT = "transport"        # lecture / arrêt
CAP_RECORD = "record"              # enregistrement
CAP_SONG_MODE = "song_mode"        # bascule pattern / morceau (FL Studio) ou cycle (Mackie Control)
CAP_METRONOME = "metronome"
CAP_TEMPO = "tempo"                # le tempo est connu
CAP_TEMPO_WRITE = "tempo_write"    # le tempo peut être changé d'ici
CAP_SEEK = "seek"                  # la position peut être déplacée
CAP_RACK = "rack"                  # channel rack : canaux (nom, couleur, muet, solo, volume, pan)
CAP_STEPS = "steps"                # pas du séquenceur, modifiables
CAP_PATTERNS = "patterns"          # liste et choix des patterns
CAP_PLAYLIST = "playlist"          # pistes de la playlist / du projet
CAP_TRACK_ARM = "track_arm"        # armement à l'enregistrement des pistes
CAP_TRACK_MIX = "track_mix"        # volume / panoramique des pistes
CAP_BANK = "bank"                  # pistes par groupes de 8 (Mackie Control)
CAP_METERS = "meters"              # niveaux des pistes

DEFAULT_CHANNEL_COLOR = "#6b7a74"
DEFAULT_VOLUME = 0.78125           # volume par défaut d'un canal de FL Studio (1000 / 1280)
MIN_TEMPO, MAX_TEMPO = 10.0, 999.0
STEP_CHOICES = (16, 32, 48, 64)
MAX_STEPS = 64


# --------------------------------------------------------------------------- #
# Couleurs et temps
# --------------------------------------------------------------------------- #
def fl_color_to_hex(value: int) -> str:
    """Couleur de FL Studio (0x--BBGGRR) en « #rrggbb »."""
    value = int(value) & 0xFFFFFF
    red, green, blue = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
    return f"#{red:02x}{green:02x}{blue:02x}"


def hex_to_fl_color(value: str) -> int:
    """« #rrggbb » en couleur de FL Studio (0x00BBGGRR) ; 0 si la valeur est invalide."""
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        return 0
    try:
        red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return 0
    return (blue << 16) | (green << 8) | red


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_tempo(tempo: float) -> str:
    """« 115.000 » comme FL Studio ; « — » quand le logiciel ne donne pas son tempo."""
    return f"{tempo:.3f}" if tempo and tempo > 0 else "—"


def format_seconds(seconds: float) -> str:
    """« 1:05.25 » (minutes, secondes, centièmes)."""
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60.0)
    return f"{int(minutes)}:{rest:05.2f}"


@dataclass
class DawTransport:
    """Lecture, enregistrement, tempo et position."""

    playing: bool = False
    recording: bool = False
    song_mode: bool = False          # morceau entier (sinon : pattern seul, ou « cycle » désactivé)
    metronome: bool = False
    tempo: float = 0.0               # battements par minute ; 0 = inconnu
    ticks: int = -1                  # position depuis le début, en ticks ; -1 = inconnue
    ppq: int = 96                    # ticks par noire
    beats_per_bar: int = 4
    time_text: str = ""              # affichage du logiciel (timecode Mackie Control)

    @property
    def beats(self) -> float:
        """Position en noires depuis le début (0 si inconnue)."""
        return max(0, self.ticks) / self.ppq if self.ppq > 0 else 0.0

    @property
    def known_position(self) -> bool:
        return self.ticks >= 0

    def bar_beat_tick(self) -> tuple[int, int, int]:
        """(mesure, temps, tick) en partant de 1 pour la mesure et le temps."""
        ticks = max(0, self.ticks)
        ppq = max(1, self.ppq)
        per_bar = ppq * max(1, self.beats_per_bar)
        bar, rest = divmod(ticks, per_bar)
        beat, tick = divmod(rest, ppq)
        return bar + 1, beat + 1, tick

    def position_text(self) -> str:
        """« 003:02:48 » (mesure, temps, tick) ; le texte du logiciel quand la position est inconnue."""
        if not self.known_position:
            return self.time_text or "—"
        bar, beat, tick = self.bar_beat_tick()
        return f"{bar:03d}:{beat:02d}:{tick:0{len(str(max(1, self.ppq - 1)))}d}"

    def step_position(self, steps_per_beat: int = 4) -> float:
        """Position en pas du séquenceur (16es de note par défaut)."""
        return self.beats * steps_per_beat

    def seconds(self) -> float | None:
        """Position en secondes, quand le tempo et la position sont connus."""
        if not self.known_position or self.tempo <= 0:
            return None
        return self.beats * 60.0 / self.tempo


# --------------------------------------------------------------------------- #
# Éléments affichés
# --------------------------------------------------------------------------- #
@dataclass
class DawChannel:
    """Une ligne du channel rack."""

    index: int
    name: str = ""
    color: str = DEFAULT_CHANNEL_COLOR
    muted: bool = False
    solo: bool = False
    selected: bool = False
    volume: float = DEFAULT_VOLUME   # 0 → 1 (échelle du logiciel)
    pan: float = 0.0                 # −1 → +1
    kind: str = "sampler"            # sampler | plugin | layer | audio | automation
    level: float = 0.0               # activité (note jouée à l'instant) 0 → 1
    steps: list[bool] = field(default_factory=list)

    def step(self, position: int) -> bool:
        return 0 <= position < len(self.steps) and self.steps[position]


@dataclass
class DawPattern:
    index: int
    name: str = ""
    color: str = DEFAULT_CHANNEL_COLOR
    length_steps: int = 16           # longueur en pas (16es de note)


@dataclass
class DawTrack:
    """Une piste de la playlist (FL Studio) ou une tranche du projet (Mackie Control)."""

    index: int
    name: str = ""
    color: str = DEFAULT_CHANNEL_COLOR
    muted: bool = False
    solo: bool = False
    armed: bool = False
    selected: bool = False
    volume: float = -1.0             # 0 → 1 ; −1 = inconnu
    pan: float = 0.0
    level: float = 0.0               # niveau (ou activité) 0 → 1
    active: bool = False             # un clip joue sous le curseur de lecture


@dataclass
class DawState:
    """Tout ce que le Studio affiche d'un logiciel de musique."""

    daw: str = ""                    # « FL Studio », « Cubase / Nuendo »…
    project: str = ""
    connected: bool = False
    message: str = ""                # raison quand la liaison n'est pas établie
    transport: DawTransport = field(default_factory=DawTransport)
    channels: list[DawChannel] = field(default_factory=list)
    patterns: list[DawPattern] = field(default_factory=list)
    pattern: int = -1                # pattern choisi (index), −1 = aucun
    step_count: int = 16             # pas affichés par canal
    tracks: list[DawTrack] = field(default_factory=list)
    track_offset: int = 0            # première piste affichée (Mackie Control : groupe de 8)
    master: DawTrack | None = None   # fader général, quand le logiciel le donne (Mackie Control)
    capabilities: frozenset[str] = frozenset()
    song_mode_label: str = "Morceau" # nom du bouton CAP_SONG_MODE (« Boucle » pour Mackie Control)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def channel(self, index: int) -> DawChannel | None:
        return next((c for c in self.channels if c.index == index), None)

    def track(self, index: int) -> DawTrack | None:
        return next((t for t in self.tracks if t.index == index), None)

    def current_pattern(self) -> DawPattern | None:
        return next((p for p in self.patterns if p.index == self.pattern), None)

    def selected_channel(self) -> DawChannel | None:
        return next((c for c in self.channels if c.selected), None)


# --------------------------------------------------------------------------- #
# Liaison : ce que le Studio peut demander
# --------------------------------------------------------------------------- #
class DawLink:
    """Base d'une liaison avec un logiciel de musique. Les ordres que la liaison ne comprend
    pas sont sans effet (le Studio les grise d'après `state.capabilities`).

    Cycle de vie : `start()` → `poll()` à intervalle régulier (QTimer) → `close()`. `poll()` ne
    bloque jamais ; il renvoie True quand l'état affiché a changé."""

    key = "none"
    label = "Aucune liaison"

    def __init__(self) -> None:
        self.state = DawState()
        self.started = False

    # ------------------------------------------------------------ cycle de vie
    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.state.connected = False

    def close(self) -> None:
        """Dit au revoir au logiciel et libère les ports MIDI (appelé une fois, à la fin)."""
        self.stop()

    def poll(self) -> bool:
        return False

    # --------------------------------------------------------------- transport
    def play_pause(self) -> None: ...
    def stop_playback(self) -> None: ...
    def record(self) -> None: ...
    def toggle_song_mode(self) -> None: ...
    def toggle_metronome(self) -> None: ...
    def set_tempo(self, bpm: float) -> None: ...
    def seek(self, beats: float) -> None: ...
    def rewind(self) -> None: ...

    # ------------------------------------------------------------ channel rack
    def set_step_count(self, steps: int) -> None:
        self.state.step_count = int(clamp(steps, 1, MAX_STEPS))

    def toggle_step(self, channel: int, step: int, value: bool | None = None) -> None: ...
    def set_channel_volume(self, channel: int, value: float) -> None: ...
    def set_channel_pan(self, channel: int, value: float) -> None: ...
    def set_channel_mute(self, channel: int, muted: bool) -> None: ...
    def set_channel_solo(self, channel: int, solo: bool) -> None: ...
    def select_channel(self, channel: int) -> None: ...
    def select_pattern(self, pattern: int) -> None: ...

    # ----------------------------------------------------------------- pistes
    def set_track_mute(self, track: int, muted: bool) -> None: ...
    def set_track_solo(self, track: int, solo: bool) -> None: ...
    def set_track_arm(self, track: int, armed: bool) -> None: ...
    def set_track_volume(self, track: int, value: float) -> None: ...
    def set_track_pan(self, track: int, value: float) -> None: ...
    def select_track(self, track: int) -> None: ...
    def bank(self, delta: int) -> None: ...


def steps_for_length(length_steps: int) -> int:
    """Nombre de pas à afficher pour un pattern de `length_steps` pas : 16, 32, 48 ou 64."""
    if length_steps <= 16:
        return 16
    return int(min(MAX_STEPS, ((length_steps + 15) // 16) * 16))


def resize_steps(steps: Iterable[bool], count: int) -> list[bool]:
    """Pas d'un canal ramenés à `count` cases (complétés ou tronqués)."""
    cells = [bool(s) for s in steps][:count]
    return cells + [False] * (count - len(cells))

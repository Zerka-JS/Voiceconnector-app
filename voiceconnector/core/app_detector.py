"""
Détection des logiciels audio et des stations audio numériques (DAW).

Ce module n'utilise ni Qt ni Windows : tout ce qui décide (quel logiciel est
actif ? faut-il proposer le mode musical ?) se teste sans interface. Seule la
liste des processus vient du système (psutil), et elle s'injecte.

Trois notions :

- le catalogue (KNOWN_APPS) : les logiciels que VoiceConnector reconnaît, leur
  catégorie, leur disposition conseillée, et surtout `has_music_production` :
  vrai pour une station audio (FL Studio, Ableton, REAPER, Cubase…), faux pour
  une application seulement vocale ou de diffusion (Discord, Zoom, Spotify,
  OBS, Audacity…) ;
- la détection (AppDetector) : quels logiciels du catalogue tournent ou sont
  installés ;
- le logiciel actif (ActiveApp) : celui que l'interface suit, choisi par
  l'utilisateur ou, à défaut, le mieux classé parmi ceux qui tournent. C'est
  lui qui décide de l'affichage du bouton « Mode musical »
  (voir music_mode_available).

Exemple :
    detector = AppDetector()
    active = resolve_active_app(detector.scan(), selected="auto")
    if music_mode_available(active):
        ...  # le bouton Mode musical est affiché
"""

from __future__ import annotations

import enum
import glob
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

AUTO = "auto"                     # valeur de session.app : suivre la détection


class AppKind(str, enum.Enum):
    DAW = "daw"                       # station audio numérique (production musicale)
    AUDIO_EDITOR = "audio_editor"     # éditeur audio / voix
    VIDEO_EDITOR = "video_editor"     # montage vidéo (mixeur audio intégré)
    BROADCAST = "broadcast"           # logiciel de streaming / de diffusion
    VOICE_CHAT = "voice_chat"         # chat vocal, visioconférence
    MEDIA_PLAYER = "media_player"     # lecteur de musique ou de vidéo


KIND_LABELS = {
    AppKind.DAW: "Station audio (DAW)",
    AppKind.AUDIO_EDITOR: "Éditeur audio",
    AppKind.VIDEO_EDITOR: "Montage vidéo",
    AppKind.BROADCAST: "Streaming",
    AppKind.VOICE_CHAT: "Chat vocal",
    AppKind.MEDIA_PLAYER: "Lecteur multimédia",
}

# Applications « voix / analyse » du mode A (Discord, OBS, TeamSpeak, Audacity…) :
# celles vers lesquelles on aiguille la voix et que l'on écoute ou analyse.
VOICE_KINDS = frozenset({AppKind.VOICE_CHAT, AppKind.BROADCAST, AppKind.AUDIO_EDITOR})

SYNC_FL_SYSEX = "fl_sysex"        # synchronisation du mixer via le script MIDI de FL Studio


@dataclass(frozen=True)
class KnownApp:
    key: str                              # identifiant stable (config session.app)
    name: str                             # nom affiché
    kind: AppKind
    exe_names: tuple[str, ...] = ()       # noms de processus exacts (casse et « .exe » ignorés)
    exe_patterns: tuple[str, ...] = ()    # expressions régulières pour les noms versionnés
    has_music_production: bool = False    # partie musicale / DAW → bouton Mode musical
    layout: str = "standard"              # disposition conseillée (layout_manager)
    sync: str | None = None               # protocole de synchro du mixer, si géré
    install_dirs: tuple[str, ...] = ()    # dossiers d'installation (variables %…% et « * »)
    priority: int = 50                    # plus petit = prioritaire en détection automatique
    _stems: frozenset[str] = field(init=False, repr=False, compare=False, default=frozenset())
    _regexes: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        # Noms normalisés et expressions compilées une fois pour toutes (frozen : via object)
        object.__setattr__(self, "_stems", frozenset(process_stem(n) for n in self.exe_names))
        object.__setattr__(self, "_regexes",
                           tuple(re.compile(p, re.IGNORECASE) for p in self.exe_patterns))

    @property
    def is_daw(self) -> bool:
        return self.kind is AppKind.DAW

    @property
    def is_voice_app(self) -> bool:
        return self.kind in VOICE_KINDS

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    def matches(self, process_name: str) -> bool:
        """Ce nom de processus est-il celui du logiciel ? (casse et « .exe » ignorés)"""
        stem = process_stem(process_name)
        if not stem:
            return False
        return stem in self._stems or any(pattern.search(stem) for pattern in self._regexes)


def process_stem(name: str) -> str:
    """Nom de processus normalisé : minuscules, sans « .exe » (Windows) ni espaces en bord."""
    return str(name or "").strip().lower().removesuffix(".exe")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
_PF = r"%ProgramFiles%"
_PF86 = r"%ProgramFiles(x86)%"

KNOWN_APPS: tuple[KnownApp, ...] = (
    # --- Stations audio numériques : bouton Mode musical -----------------------
    KnownApp("fl_studio", "FL Studio", AppKind.DAW,
             exe_names=("FL64.exe", "FL.exe", "FL64 (scaled).exe", "FL (scaled).exe"),
             has_music_production=True, layout="fl_studio", sync=SYNC_FL_SYSEX,
             install_dirs=(_PF + r"\Image-Line\FL Studio*", _PF86 + r"\Image-Line\FL Studio*"),
             priority=10),
    KnownApp("ableton", "Ableton Live", AppKind.DAW,
             exe_patterns=(r"^ableton live( \d+)?( (suite|standard|intro|lite|trial|beta))?$",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Ableton\Live*",), priority=11),
    KnownApp("reaper", "REAPER", AppKind.DAW, exe_names=("reaper.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\REAPER*", _PF86 + r"\REAPER*"), priority=12),
    KnownApp("cubase", "Cubase", AppKind.DAW,
             exe_patterns=(r"^cubase( pro| artist| elements| le| ai)?[ _]?\d*(\.\d+)?(_64)?$",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Steinberg\Cubase*",), priority=13),
    KnownApp("nuendo", "Nuendo", AppKind.DAW,
             exe_patterns=(r"^nuendo[ _]?\d*(\.\d+)?(_64)?$",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Steinberg\Nuendo*",), priority=14),
    KnownApp("studio_one", "Studio One", AppKind.DAW, exe_names=("Studio One.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\PreSonus\Studio One*",), priority=15),
    KnownApp("logic", "Logic Pro", AppKind.DAW,
             exe_names=("Logic Pro X", "Logic Pro", "Logic Pro.exe"),
             has_music_production=True, layout="fl_studio", priority=16),
    KnownApp("bitwig", "Bitwig Studio", AppKind.DAW, exe_names=("Bitwig Studio.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Bitwig Studio*",), priority=17),
    KnownApp("pro_tools", "Pro Tools", AppKind.DAW, exe_names=("ProTools.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Avid\Pro Tools*",), priority=18),
    KnownApp("reason", "Reason", AppKind.DAW, exe_names=("Reason.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Reason Studios\Reason*",), priority=19),
    KnownApp("cakewalk", "Cakewalk", AppKind.DAW, exe_names=("Cakewalk.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Cakewalk\Cakewalk Core",), priority=20),
    KnownApp("ardour", "Ardour", AppKind.DAW, exe_patterns=(r"^ardour\d*$",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\Ardour*",), priority=21),
    KnownApp("lmms", "LMMS", AppKind.DAW, exe_names=("lmms.exe",),
             has_music_production=True, layout="fl_studio",
             install_dirs=(_PF + r"\LMMS",), priority=22),

    # --- Streaming / diffusion ---------------------------------------------------
    KnownApp("obs", "OBS Studio", AppKind.BROADCAST,
             exe_names=("obs64.exe", "obs32.exe", "obs.exe"), layout="streaming",
             install_dirs=(_PF + r"\obs-studio", _PF86 + r"\obs-studio"), priority=40),
    KnownApp("streamlabs", "Streamlabs", AppKind.BROADCAST,
             exe_names=("Streamlabs Desktop.exe", "Streamlabs OBS.exe", "slobs.exe"),
             layout="streaming", install_dirs=(_PF + r"\Streamlabs*",), priority=41),
    KnownApp("vmix", "vMix", AppKind.BROADCAST, exe_names=("vMix64.exe", "vMix.exe"),
             layout="streaming", priority=42),
    KnownApp("xsplit", "XSplit", AppKind.BROADCAST,
             exe_names=("XSplit.Core.exe", "XSplit.Gamecaster.exe"), layout="streaming",
             priority=43),
    KnownApp("twitch_studio", "Twitch Studio", AppKind.BROADCAST,
             exe_names=("Twitch Studio.exe",), layout="streaming", priority=44),

    # --- Éditeurs audio et montage vidéo (voix, podcast) ---------------------------
    KnownApp("audacity", "Audacity", AppKind.AUDIO_EDITOR, exe_names=("audacity.exe",),
             layout="podcast", install_dirs=(_PF + r"\Audacity", _PF86 + r"\Audacity"),
             priority=45),
    KnownApp("audition", "Adobe Audition", AppKind.AUDIO_EDITOR,
             exe_patterns=(r"^adobe audition( cc)?( \d{4})?$",), layout="podcast",
             install_dirs=(_PF + r"\Adobe\Adobe Audition*",), priority=46),
    KnownApp("ocenaudio", "ocenaudio", AppKind.AUDIO_EDITOR, exe_names=("ocenaudio.exe",),
             layout="podcast", priority=47),
    KnownApp("premiere", "Adobe Premiere Pro", AppKind.VIDEO_EDITOR,
             exe_patterns=(r"^adobe premiere pro",), layout="podcast",
             install_dirs=(_PF + r"\Adobe\Adobe Premiere Pro*",), priority=60),
    KnownApp("resolve", "DaVinci Resolve", AppKind.VIDEO_EDITOR, exe_names=("Resolve.exe",),
             layout="podcast", install_dirs=(_PF + r"\Blackmagic Design\DaVinci Resolve",),
             priority=61),

    # --- Chat vocal / visioconférence --------------------------------------------------
    KnownApp("discord", "Discord", AppKind.VOICE_CHAT,
             exe_names=("Discord.exe", "DiscordPTB.exe", "DiscordCanary.exe",
                        "DiscordDevelopment.exe"),
             layout="streaming", install_dirs=(r"%LOCALAPPDATA%\Discord",), priority=50),
    KnownApp("teamspeak3", "TeamSpeak 3", AppKind.VOICE_CHAT,
             exe_names=("ts3client_win64.exe", "ts3client_win32.exe"), layout="streaming",
             install_dirs=(_PF + r"\TeamSpeak 3 Client", _PF86 + r"\TeamSpeak 3 Client"),
             priority=51),
    KnownApp("teamspeak", "TeamSpeak", AppKind.VOICE_CHAT, exe_names=("TeamSpeak.exe",),
             layout="streaming", install_dirs=(r"%LOCALAPPDATA%\TeamSpeak*",), priority=52),
    KnownApp("mumble", "Mumble", AppKind.VOICE_CHAT, exe_names=("mumble.exe",),
             layout="streaming", install_dirs=(_PF + r"\Mumble*",), priority=53),
    KnownApp("zoom", "Zoom", AppKind.VOICE_CHAT, exe_names=("Zoom.exe",), layout="podcast",
             install_dirs=(_PF + r"\Zoom", r"%APPDATA%\Zoom"), priority=54),
    KnownApp("teams", "Microsoft Teams", AppKind.VOICE_CHAT,
             exe_names=("ms-teams.exe", "Teams.exe"), layout="podcast", priority=55),
    KnownApp("skype", "Skype", AppKind.VOICE_CHAT, exe_names=("Skype.exe",), layout="podcast",
             priority=56),

    # --- Lecteurs ------------------------------------------------------------------------
    KnownApp("spotify", "Spotify", AppKind.MEDIA_PLAYER, exe_names=("Spotify.exe",),
             layout="streaming", install_dirs=(r"%APPDATA%\Spotify",), priority=70),
    KnownApp("vlc", "VLC", AppKind.MEDIA_PLAYER, exe_names=("vlc.exe",), layout="streaming",
             install_dirs=(_PF + r"\VideoLAN\VLC", _PF86 + r"\VideoLAN\VLC"), priority=71),
    KnownApp("foobar2000", "foobar2000", AppKind.MEDIA_PLAYER, exe_names=("foobar2000.exe",),
             layout="streaming", priority=72),
    KnownApp("itunes", "Apple Music / iTunes", AppKind.MEDIA_PLAYER,
             exe_names=("iTunes.exe", "AppleMusic.exe"), layout="streaming", priority=73),
)

APPS_BY_KEY: Mapping[str, KnownApp] = {app.key: app for app in KNOWN_APPS}


def known_app(key: str | None) -> KnownApp | None:
    """Le logiciel du catalogue de cette clé (None si inconnue, vide ou « auto »)."""
    return APPS_BY_KEY.get(str(key or "").strip().lower())


def music_apps() -> tuple[KnownApp, ...]:
    return tuple(app for app in KNOWN_APPS if app.has_music_production)


# --------------------------------------------------------------------------- #
# Processus
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProcessInfo:
    name: str
    exe: str = ""
    pid: int = 0


def psutil_processes() -> list[ProcessInfo]:
    """Processus en cours d'après psutil (liste vide si psutil est absent)."""
    try:
        import psutil
    except ImportError:
        return []
    result: list[ProcessInfo] = []
    try:
        for proc in psutil.process_iter(["name", "exe", "pid"]):
            try:
                info = proc.info
            except (psutil.Error, OSError):
                continue
            name = info.get("name") or ""
            if name:
                result.append(ProcessInfo(name, info.get("exe") or "", int(info.get("pid") or 0)))
    except (psutil.Error, OSError) as exc:          # énumération impossible : on ne bloque rien
        log.debug("Liste des processus illisible : %s", exc)
    return result


@dataclass(frozen=True)
class DetectedApp:
    app: KnownApp
    pids: tuple[int, ...] = ()
    exe: str = ""             # chemin de l'exécutable, s'il est connu

    @property
    def key(self) -> str:
        return self.app.key


@dataclass(frozen=True)
class InstalledApp:
    app: KnownApp
    folder: str


class AppDetector:
    """Cherche dans le catalogue les logiciels qui tournent ou sont installés.

    `processes` : fonction sans argument qui renvoie les processus (psutil par
    défaut ; injectée dans les tests). `environ` : variables d'environnement
    pour développer %ProgramFiles%… (os.environ par défaut)."""

    def __init__(self, catalog: Sequence[KnownApp] = KNOWN_APPS,
                 processes: Callable[[], Iterable[ProcessInfo]] = psutil_processes,
                 environ: Mapping[str, str] | None = None) -> None:
        self._catalog = tuple(catalog)
        self._processes = processes
        self._environ = environ if environ is not None else os.environ
        self._last: tuple[str, ...] | None = None

    @property
    def catalog(self) -> tuple[KnownApp, ...]:
        return self._catalog

    def scan(self) -> list[DetectedApp]:
        """Logiciels du catalogue en cours d'exécution, le plus prioritaire d'abord."""
        try:
            processes = list(self._processes())
        except Exception as exc:        # la détection ne doit jamais faire tomber l'interface
            log.warning("Détection des logiciels impossible : %s", exc)
            return []
        pids: dict[str, list[int]] = {}
        exes: dict[str, str] = {}
        apps: dict[str, KnownApp] = {}
        for process in processes:
            for app in self._catalog:
                if app.matches(process.name):
                    apps.setdefault(app.key, app)
                    pids.setdefault(app.key, [])
                    if process.pid:
                        pids[app.key].append(process.pid)
                    if process.exe and not exes.get(app.key):
                        exes[app.key] = process.exe
                    break                  # un processus = un logiciel
        detected = [DetectedApp(app, tuple(pids[key]), exes.get(key, "")) for key, app in apps.items()]
        detected.sort(key=lambda d: (d.app.priority, d.app.name.lower()))
        return detected

    def refresh(self) -> tuple[list[DetectedApp], bool]:
        """(logiciels en cours, changement depuis le dernier appel ?)"""
        detected = self.scan()
        keys = tuple(d.key for d in detected)
        changed = keys != self._last
        self._last = keys
        return detected, changed

    def installed(self) -> list[InstalledApp]:
        """Logiciels dont un dossier d'installation connu existe sur ce PC."""
        result = []
        for app in self._catalog:
            folder = self.find_install_dir(app)
            if folder is not None:
                result.append(InstalledApp(app, str(folder)))
        return result

    def find_install_dir(self, app: KnownApp) -> Path | None:
        for pattern in app.install_dirs:
            # Valeurs d'environnement échappées (crochets…), « * » du motif conservés
            expanded = expand_env(pattern, self._environ, transform=glob.escape)
            if expanded is None:
                continue
            expanded = expanded.replace("\\", os.sep)        # motifs écrits à la Windows
            try:
                matches = sorted(glob.glob(expanded), reverse=True)   # version la plus récente d'abord
            except OSError:
                matches = []
            for match in matches:
                if os.path.isdir(match):
                    return Path(match)
        return None


_ENV_VARIABLE = re.compile(r"%([^%]+)%")


def expand_env(pattern: str, environ: Mapping[str, str],
               transform: Callable[[str], str] | None = None) -> str | None:
    """Développe les %VARIABLES% de `pattern` ; None si l'une d'elles est absente
    (l'emplacement n'existe pas sur cette machine). `transform` est appliqué à
    chaque valeur insérée (glob.escape pour une recherche avec « * »)."""
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        value = environ.get(match.group(1))
        if value is None:
            missing = True
            return ""
        return transform(value) if transform is not None else value

    expanded = _ENV_VARIABLE.sub(replace, pattern)
    return None if missing else expanded


# --------------------------------------------------------------------------- #
# Logiciel actif
# --------------------------------------------------------------------------- #
SOURCE_SELECTED = "selected"      # choisi par l'utilisateur
SOURCE_DETECTED = "detected"      # le mieux classé parmi ceux qui tournent
SOURCE_NONE = "none"


@dataclass(frozen=True)
class ActiveApp:
    """Le logiciel que l'interface suit : un choix de l'utilisateur ou la détection."""

    app: KnownApp | None = None
    source: str = SOURCE_NONE
    running: bool = False

    @property
    def key(self) -> str:
        return self.app.key if self.app is not None else ""

    @property
    def name(self) -> str:
        return self.app.name if self.app is not None else "Aucun logiciel reconnu"

    @property
    def has_music_production(self) -> bool:
        return self.app is not None and self.app.has_music_production

    @property
    def suggested_layout(self) -> str | None:
        return self.app.layout if self.app is not None else None


NO_APP = ActiveApp()


def resolve_active_app(detected: Sequence[DetectedApp], selected: str | None = AUTO) -> ActiveApp:
    """Logiciel actif.

    - `selected` désigne un logiciel du catalogue : c'est lui, qu'il tourne ou
      non (on peut préparer l'interface avant de lancer FL Studio) ;
    - sinon (« auto », vide ou clé inconnue) : le logiciel en cours le mieux
      classé (stations audio d'abord), ou aucun.
    """
    chosen = known_app(selected) if str(selected or AUTO).strip().lower() != AUTO else None
    if chosen is not None:
        return ActiveApp(chosen, SOURCE_SELECTED, any(d.key == chosen.key for d in detected))
    if detected:
        best = min(detected, key=lambda d: (d.app.priority, d.app.name.lower()))
        return ActiveApp(best.app, SOURCE_DETECTED, True)
    return NO_APP


def music_mode_available(active: ActiveApp | KnownApp | None) -> bool:
    """Le bouton « Mode musical » doit-il être affiché ?

    Règle stricte : seulement si le logiciel sélectionné ou détecté a une partie
    musique / DAW (FL Studio, Ableton, REAPER, Cubase…). Jamais pour une
    application seulement vocale ou de diffusion (Discord, Zoom, Spotify, OBS…),
    ni quand aucun logiciel n'est reconnu."""
    app = active.app if isinstance(active, ActiveApp) else active
    return bool(app is not None and app.has_music_production)


def voice_apps(detected: Sequence[DetectedApp]) -> list[DetectedApp]:
    """Applications « voix / analyse » en cours (mode A : Discord, OBS, TeamSpeak, Audacity…)."""
    return [d for d in detected if d.app.is_voice_app]


@dataclass(frozen=True)
class MusicButtonState:
    """État du bouton « Mode musical » de la barre d'actions."""

    visible: bool
    enabled: bool = True
    checked: bool = False
    tooltip: str = ""


HIDDEN_MUSIC_BUTTON = MusicButtonState(visible=False, enabled=False, checked=False)


def music_button_state(active: ActiveApp, music_on: bool, sync_connected: bool = False,
                       sync_supported: bool | None = None) -> MusicButtonState:
    """Ce que la barre d'actions doit montrer pour le bouton « Mode musical ».

    Caché (et jamais coché) sans logiciel musical. `sync_supported` : le
    logiciel actif sait synchroniser son mixer (None : d'après le catalogue)."""
    if not music_mode_available(active):
        return HIDDEN_MUSIC_BUTTON
    app = active.app
    assert app is not None
    if sync_supported is None:
        sync_supported = app.sync is not None
    lines = [f"Mode musical — {app.name}"]
    if not active.running:
        lines.append(f"{app.name} n'est pas détecté : la synchronisation démarrera dès son lancement.")
    if sync_supported:
        lines.append("Synchronisation du mixer : connectée." if sync_connected
                     else "Synchronisation du mixer : en attente de " + app.name + ".")
    else:
        lines.append("Disposition studio et retour ASIO ; pas de synchronisation du mixer "
                     f"pour {app.name} pour l'instant.")
    return MusicButtonState(visible=True, enabled=True, checked=bool(music_on),
                            tooltip="\n".join(lines))


@dataclass
class ActiveAppTracker:
    """Suit le logiciel actif au fil des détections et signale les changements.

    La fenêtre principale appelle `update()` à intervalle régulier (QTimer)
    avec le résultat de AppDetector.scan() et le choix de session.app."""

    selected: str = AUTO
    active: ActiveApp = field(default_factory=lambda: NO_APP)

    def update(self, detected: Sequence[DetectedApp], selected: str | None = None) -> bool:
        """Met à jour le logiciel actif ; True s'il a changé (logiciel, source ou état)."""
        if selected is not None:
            self.selected = selected or AUTO
        new = resolve_active_app(detected, self.selected)
        changed = new != self.active
        self.active = new
        return changed

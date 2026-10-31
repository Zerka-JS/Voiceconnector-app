"""
Profils du mode autonome (B) : la liste des canaux de la console quand Voicemeeter
n'est pas là (logique pure, sans Qt).

Un profil décrit les canaux (nom, rôle, source du son) et, en option, le logiciel
audio qu'il accompagne et le thème à utiliser. Deux origines :

- un fichier JSON importé (Réglages → Mode et Apparence → « Importer un profil… »),
  et exportable de la même façon ;
- un profil automatique, choisi d'après le logiciel détecté ou sélectionné
  (FL Studio → Master, Bus Batterie, Synths, FX, Micro ; OBS / Discord →
  Micro, Game, Discord, Music ; Audacity / Zoom → Voix, Invité, Musique…).

Format du fichier :

    {
      "format": "voiceconnector-profile", "version": 1,
      "name": "Mon studio", "software": "fl_studio", "theme": "slate",
      "channels": [
        {"name": "Master",   "role": "master", "source": {"type": "fl_track", "track": 0}},
        {"name": "Discord",  "role": "discord", "source": {"type": "app", "key": "discord.exe"}},
        {"name": "Micro",    "role": "mic", "source": {"type": "capture"}, "gain": -3.0, "mute": false}
      ]
    }

Types de source : `app` (session audio d'une application : nom de processus ou clé du
catalogue ; clé vide = à choisir dans la console), `capture` (micro par défaut de
Windows), `master` (volume général de la sortie par défaut), `fl_track` (piste du mixer
de FL Studio, 0 = Master, via la synchronisation du mode musical).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from core import themes
from core.app_detector import SYNC_FL_SYSEX, ActiveApp, AppKind, KnownApp, known_app
from core.layout_manager import (ALL_ROLES, ROLE_DISCORD, ROLE_DRUMS, ROLE_FX, ROLE_GAME, ROLE_GUEST,
                                 ROLE_MASTER, ROLE_MIC, ROLE_MUSIC, ROLE_SYNTHS, ROLE_VOICE)
from core.system_audio import SOURCE_APP, SOURCE_CAPTURE, SOURCE_MASTER

log = logging.getLogger(__name__)

SOURCE_FL_TRACK = "fl_track"
SOURCE_TYPES = (SOURCE_APP, SOURCE_CAPTURE, SOURCE_MASTER, SOURCE_FL_TRACK)
PROFILE_FORMAT = "voiceconnector-profile"
PROFILE_VERSION = 1
MAX_CHANNELS = 24
MAX_NAME = 30                   # limite des noms de canaux dans l'interface
# Logiciels de voix : le profil automatique est Voix, Invité / Appel, Musique (podcast, appel, montage)
VOICE_PROFILE_APPS = frozenset({"audacity", "audition", "ocenaudio", "premiere", "resolve",
                                "zoom", "teams", "skype"})
FL_TRACK_MAX = 126              # Master (0) + 125 inserts
GAIN_RANGE_DB = (-60.0, 12.0)
MAX_FILE_BYTES = 1_000_000


class ProfileError(ValueError):
    """Profil illisible ou invalide ; le message est destiné à l'utilisateur."""


@dataclass(frozen=True)
class ChannelSource:
    type: str
    key: str = ""               # app : nom de processus ou clé du catalogue ; « » = à choisir
    track: int = 0              # fl_track : numéro de la piste

    def __post_init__(self) -> None:
        if self.type not in SOURCE_TYPES:
            raise ProfileError(f"Source « {self.type} » inconnue ({', '.join(SOURCE_TYPES)}).")


@dataclass(frozen=True)
class ProfileChannel:
    name: str
    source: ChannelSource
    role: str | None = None
    gain: float | None = None   # dB, appliqué quand la source apparaît ; None : on n'y touche pas
    mute: bool | None = None    # idem

    @property
    def physical(self) -> bool:
        """Une entrée (micro) est rangée avec les entrées matérielles de la console."""
        return self.source.type == SOURCE_CAPTURE


@dataclass(frozen=True)
class Profile:
    name: str
    channels: tuple[ProfileChannel, ...]
    software: str = ""          # clé du catalogue de logiciels (facultatif)
    theme: str = ""             # clé de thème conseillé (facultatif)

    @property
    def uses_fl_sync(self) -> bool:
        return any(c.source.type == SOURCE_FL_TRACK for c in self.channels)

    def with_assignment(self, index: int, key: str) -> Profile:
        """Même profil, avec l'application du canal `index` choisie à la main."""
        channels = list(self.channels)
        old = channels[index]
        channels[index] = replace(old, source=ChannelSource(SOURCE_APP, key))
        return replace(self, channels=tuple(channels))


# --------------------------------------------------------------------------- #
# Lecture / écriture
# --------------------------------------------------------------------------- #
def parse_profile(data: Any) -> Profile:
    """Profil d'après le contenu d'un fichier JSON déjà décodé (lève ProfileError)."""
    if not isinstance(data, dict):
        raise ProfileError("Le profil doit contenir un objet JSON.")
    fmt = data.get("format")
    if fmt is not None and fmt != PROFILE_FORMAT:
        raise ProfileError(f"Ce n'est pas un profil VoiceConnector (format « {fmt} »).")
    version = data.get("version", PROFILE_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProfileError("Version de profil invalide.")
    if version > PROFILE_VERSION:
        log.warning("Profil de version %s (cette version de VoiceConnector lit la %s) : "
                    "lecture au mieux.", version, PROFILE_VERSION)
    raw = data.get("channels")
    if not isinstance(raw, list) or not raw:
        raise ProfileError("Le profil ne contient aucun canal (« channels »).")
    if len(raw) > MAX_CHANNELS:
        raise ProfileError(f"Trop de canaux : {len(raw)} (maximum {MAX_CHANNELS}).")
    channels = tuple(_parse_channel(item, n) for n, item in enumerate(raw, start=1))
    name = str(data.get("name") or "Profil importé").strip()[:MAX_NAME] or "Profil importé"
    software = str(data.get("software") or "").strip().lower()
    theme = str(data.get("theme") or "").strip().lower()
    return Profile(
        name=name, channels=channels,
        software=software if known_app(software) is not None else "",
        theme=theme if themes.global_theme(theme) is not None else "")


def _parse_channel(item: Any, number: int) -> ProfileChannel:
    where = f"Canal {number}"
    if not isinstance(item, dict):
        raise ProfileError(f"{where} : un objet est attendu.")
    source = item.get("source")
    if not isinstance(source, dict):
        raise ProfileError(f"{where} : « source » manquante.")
    kind = str(source.get("type") or "").strip().lower()
    if kind not in SOURCE_TYPES:
        raise ProfileError(f"{where} : source « {kind or '?'} » inconnue ({', '.join(SOURCE_TYPES)}).")
    track = 0
    if kind == SOURCE_FL_TRACK:
        try:
            track = int(source.get("track", -1))
        except (TypeError, ValueError):
            track = -1
        if not 0 <= track <= FL_TRACK_MAX:
            raise ProfileError(f"{where} : piste FL Studio invalide (0 à {FL_TRACK_MAX}).")
    key = str(source.get("key") or "").strip() if kind == SOURCE_APP else ""
    name = str(item.get("name") or "").strip()[:MAX_NAME] or f"Canal {number}"
    role = str(item.get("role") or "").strip().lower() or None
    if role not in ALL_ROLES:
        role = None
    gain: float | None = None
    if "gain" in item:
        try:
            gain = max(GAIN_RANGE_DB[0], min(GAIN_RANGE_DB[1], float(item["gain"])))
        except (TypeError, ValueError):
            gain = None
    mute = bool(item["mute"]) if "mute" in item else None
    return ProfileChannel(name, ChannelSource(kind, key, track), role, gain, mute)


def profile_to_dict(profile: Profile) -> dict:
    """Forme JSON d'un profil (inverse de parse_profile)."""
    channels = []
    for channel in profile.channels:
        source: dict[str, Any] = {"type": channel.source.type}
        if channel.source.type == SOURCE_APP:
            source["key"] = channel.source.key
        elif channel.source.type == SOURCE_FL_TRACK:
            source["track"] = channel.source.track
        item: dict[str, Any] = {"name": channel.name}
        if channel.role:
            item["role"] = channel.role
        item["source"] = source
        if channel.gain is not None:
            item["gain"] = round(channel.gain, 2)
        if channel.mute is not None:
            item["mute"] = bool(channel.mute)
        channels.append(item)
    data: dict[str, Any] = {"format": PROFILE_FORMAT, "version": PROFILE_VERSION, "name": profile.name}
    for field_name in ("software", "theme"):
        if getattr(profile, field_name):
            data[field_name] = getattr(profile, field_name)
    data["channels"] = channels
    return data


def load_profile(path: str | Path) -> Profile:
    """Lit un fichier de profil (lève ProfileError avec un message clair)."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ProfileError("Fichier trop volumineux pour un profil.")
        text = path.read_text(encoding="utf-8-sig")
    except ProfileError:
        raise
    except OSError as exc:
        raise ProfileError(f"Fichier illisible : {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ProfileError("Le fichier n'est pas du texte UTF-8.") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProfileError(f"JSON invalide : {exc}") from exc
    return parse_profile(data)


def save_profile(profile: Profile, path: str | Path) -> None:
    """Écrit un profil (fichier temporaire puis remplacement : jamais à moitié écrit)."""
    import os

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(profile_to_dict(profile), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Profils automatiques
# --------------------------------------------------------------------------- #
def _app(name: str, key: str, role: str | None = None) -> ProfileChannel:
    return ProfileChannel(name, ChannelSource(SOURCE_APP, key), role)


MIC = ProfileChannel("Micro", ChannelSource(SOURCE_CAPTURE), ROLE_MIC)
MASTER = ProfileChannel("Master", ChannelSource(SOURCE_MASTER), ROLE_MASTER)


def _fl_profile(software: str) -> Profile:
    tracks = ((ROLE_MASTER, "Master", 0), (ROLE_DRUMS, "Bus Batterie", 1),
              (ROLE_SYNTHS, "Synths", 2), (ROLE_FX, "FX", 3))
    channels = tuple(ProfileChannel(label, ChannelSource(SOURCE_FL_TRACK, track=track), role)
                     for role, label, track in tracks) + (MIC,)
    return Profile("FL Studio (pistes du mixer)", channels, software)


def default_profile(app: KnownApp | None, *, fl_sync: bool = True) -> Profile:
    """Profil automatique d'un logiciel (ou générique sans logiciel).

    `fl_sync` : la synchronisation avec les pistes de FL Studio est utilisable
    (sinon FL Studio est traité comme une application ordinaire)."""
    if app is None:
        return Profile("Général", (
            MASTER, MIC, _app("Discord", "discord.exe", ROLE_DISCORD),
            _app("Musique", "spotify.exe", ROLE_MUSIC), _app("Navigateur", "chrome.exe"),
            _app("Sons système", "system")), "")
    if app.sync == SYNC_FL_SYSEX and fl_sync:
        return _fl_profile(app.key)
    if app.kind is AppKind.DAW:
        return Profile(f"{app.name} (volume général)", (
            MASTER, ProfileChannel(app.name, ChannelSource(SOURCE_APP, app.key)), MIC,
            _app("Discord", "discord.exe", ROLE_DISCORD)), app.key)
    if app.key in VOICE_PROFILE_APPS:
        return Profile(f"{app.name} (voix)", (
            ProfileChannel("Voix", ChannelSource(SOURCE_CAPTURE), ROLE_VOICE),
            _app("Invité / Appel", app.key if app.kind is AppKind.VOICE_CHAT else "", ROLE_GUEST),
            _app("Musique / Jingles", "spotify.exe", ROLE_MUSIC)), app.key)
    suffix = {AppKind.BROADCAST: "direct", AppKind.VOICE_CHAT: "vocal"}.get(app.kind, "musique")
    return Profile(f"{app.name} ({suffix})", (
        MIC, _app("Game", "", ROLE_GAME), _app("Discord", "discord.exe", ROLE_DISCORD),
        _app("Music", "spotify.exe", ROLE_MUSIC)), app.key)


def apply_overrides(profile: Profile, overrides: Mapping[int, Mapping[str, str]]) -> Profile:
    """Le profil avec les choix faits depuis la console : canaux renommés, application choisie
    pour un canal « application » (clé `name` / `app` ; les numéros hors profil sont ignorés)."""
    channels = list(profile.channels)
    for index, item in overrides.items():
        if not isinstance(index, int) or not 0 <= index < len(channels) or not isinstance(item, Mapping):
            continue
        channel = channels[index]
        name = str(item.get("name") or "").strip()[:MAX_NAME]
        key = str(item.get("app") or "").strip()
        if name:
            channel = replace(channel, name=name)
        if key and channel.source.type == SOURCE_APP:
            channel = replace(channel, source=ChannelSource(SOURCE_APP, key))
        channels[index] = channel
    return replace(profile, channels=tuple(channels))


def resolve_profile(path: str, active: ActiveApp, *, fl_sync: bool = True) -> tuple[Profile, str]:
    """Profil à utiliser en mode B, avec une note à afficher s'il y a lieu.

    Un fichier choisi par l'utilisateur l'emporte (même si le logiciel change) ;
    illisible, il est remplacé par le profil automatique du logiciel actif."""
    note = ""
    if path:
        try:
            return load_profile(path), ""
        except ProfileError as exc:
            note = f"Profil « {Path(path).name} » ignoré : {exc}"
            log.warning(note)
    return default_profile(active.app, fl_sync=fl_sync), note

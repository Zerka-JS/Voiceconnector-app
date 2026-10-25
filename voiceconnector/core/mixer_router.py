"""
Routage vers l'application vocale (mode A, Voicemeeter) — logique pure, sans Qt.

L'application « voix / analyse » (Discord, OBS, TeamSpeak, Audacity, Zoom…) reçoit le son
de la console de deux façons, au choix pour chaque application (config
`session.voice_transport`) :

- `bus`  : un bus virtuel de Voicemeeter (B1, B2, B3) que l'application choisit comme
           micro (« Voicemeeter Out B1 ») ; le plus simple et le plus fiable ;
- `vban` : un flux VBAN émis par Voicemeeter (`vban.outstream[n]`) vers un récepteur
           VBAN, sur cet ordinateur ou un autre. EXPÉRIMENTAL : le flux est configuré par
           l'API Remote, mais le bus qu'il transporte se choisit dans la fenêtre VBAN de
           Voicemeeter, et l'application vocale ne lit pas le VBAN elle-même.

Ce module décide (`plan_voice_route`) ; l'interface applique le résultat par le
contrôleur (`set_route`, écritures VBAN facultatives). Rien n'est modifié sans un clic :
la console ne touche jamais d'elle-même aux routes que l'utilisateur a réglées.

Routage « sans écho » (mix-minus) : vers un chat vocal, on envoie la voix (micro) mais
jamais le son que le chat lui-même apporte (rôles Discord / Invité), sinon chacun
s'entendrait en retour. Vers un logiciel de diffusion ou d'enregistrement, ce son fait
partie de ce qu'on veut capter.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.app_detector import (AUTO, VOICE_KINDS, ActiveApp, AppKind, DetectedApp, KnownApp,
                               known_app)
from core.layout_manager import (ALL_ROLES, ROLE_DISCORD, ROLE_GUEST, ROLE_MASTER, ROLE_MIC,
                                 ROLE_VOICE, LayoutPlan)
from core.mixer_model import MixerParams

log = logging.getLogger(__name__)

TRANSPORT_BUS, TRANSPORT_VBAN = "bus", "vban"
TRANSPORTS = (TRANSPORT_BUS, TRANSPORT_VBAN)

DEFAULT_VBAN_NAME = "VoiceConnector"
DEFAULT_VBAN_IP = "127.0.0.1"
DEFAULT_VBAN_PORT = 6980
MAX_VBAN_NAME = 16                  # longueur maximale d'un nom de flux VBAN (octets ASCII)
MAX_VBAN_STREAMS = 8                # Potato (Banana : 4)

# Bus conseillé par type d'application quand la matrice n'a pas de bouton à son nom
DEFAULT_BUS = {AppKind.VOICE_CHAT: "B1", AppKind.BROADCAST: "B2", AppKind.AUDIO_EDITOR: "B2"}

# Rôles dont le son est envoyé vers l'application, selon son type
VOICE_FEED = frozenset({ROLE_MIC, ROLE_VOICE})
ECHO_ROLES = frozenset({ROLE_DISCORD, ROLE_GUEST})       # le son que le chat apporte lui-même

# Où régler le micro et la sortie dans chaque application
SETTINGS_PATH = {
    "discord": "Paramètres utilisateur → Voix et vidéo",
    "teamspeak3": "Outils → Options → Capture et Lecture",
    "teamspeak": "Paramètres → Audio",
    "mumble": "Configurer → Paramètres → Entrée / Sortie audio",
    "zoom": "Paramètres → Audio",
    "teams": "Paramètres → Appareils",
    "skype": "Paramètres → Audio et vidéo",
    "obs": "Paramètres → Audio, ou la source « Capture de l'entrée audio »",
    "streamlabs": "Paramètres → Audio, ou la source « Capture de l'entrée audio »",
    "audacity": "Modifier → Préférences → Périphériques",
}


# --------------------------------------------------------------------------- #
# Flux VBAN
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VbanStream:
    """Flux VBAN sortant de Voicemeeter (`vban.outstream[index]`)."""

    index: int = 0
    name: str = DEFAULT_VBAN_NAME
    ip: str = DEFAULT_VBAN_IP
    port: int = DEFAULT_VBAN_PORT

    @classmethod
    def from_mapping(cls, data: Any, max_streams: int = MAX_VBAN_STREAMS) -> VbanStream:
        """Flux d'après la config (`session.vban`) ; toute valeur invalide devient la valeur par défaut."""
        data = data if isinstance(data, Mapping) else {}
        try:
            index = int(data.get("index", 0))
        except (TypeError, ValueError):
            index = 0
        if not 0 <= index < max_streams:
            index = 0
        name = "".join(c for c in str(data.get("name") or "") if 32 < ord(c) < 127)[:MAX_VBAN_NAME]
        ip = str(data.get("ip") or DEFAULT_VBAN_IP).strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            ip = DEFAULT_VBAN_IP
        try:
            port = int(data.get("port", DEFAULT_VBAN_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_VBAN_PORT
        if not 1024 <= port <= 65535:
            port = DEFAULT_VBAN_PORT
        return cls(index, name or DEFAULT_VBAN_NAME, ip, port)

    @property
    def prefix(self) -> str:
        return f"vban.outstream[{self.index}]"

    def params(self) -> tuple[tuple[str, float | str], ...]:
        """Paramètres de l'API Remote qui allument le flux (le nom, l'adresse et le port d'abord)."""
        return (("vban.Enable", 1.0), (f"{self.prefix}.name", self.name), (f"{self.prefix}.ip", self.ip),
                (f"{self.prefix}.port", float(self.port)), (f"{self.prefix}.on", 1.0))

    def off_params(self) -> tuple[tuple[str, float | str], ...]:
        """Éteint ce flux seulement (le VBAN général reste activé : d'autres flux peuvent l'utiliser)."""
        return ((f"{self.prefix}.on", 0.0),)

    def describe(self) -> str:
        return f"flux VBAN n°{self.index + 1} « {self.name} » vers {self.ip}:{self.port}"


# --------------------------------------------------------------------------- #
# Application vocale
# --------------------------------------------------------------------------- #
def choose_voice_app(setting: str | None, active: ActiveApp,
                     detected: Sequence[DetectedApp] = ()) -> KnownApp | None:
    """Application vocale à alimenter.

    `setting` : une clé du catalogue (Discord, OBS…) ou « auto ». Automatique : le logiciel
    suivi s'il est vocal, sinon l'application vocale en cours la mieux classée, sinon aucune."""
    chosen = known_app(setting) if str(setting or AUTO).strip().lower() != AUTO else None
    if chosen is not None and chosen.is_voice_app:
        return chosen
    if active.app is not None and active.app.is_voice_app:
        return active.app
    running = [d for d in detected if d.app.is_voice_app]
    if running:
        return min(running, key=lambda d: (d.app.priority, d.app.name.lower())).app
    return None


def _bus_number(bus: str) -> int:
    return int(bus[1:]) if len(bus) > 1 and bus[1:].isdigit() else 0


def recording_device(bus: str) -> str:
    """Nom du périphérique d'enregistrement Windows qui porte ce bus (B1, B2, B3)."""
    return f"Voicemeeter Out {bus.upper()}"


LEGACY_DEVICES = {"B1": "Voicemeeter Output", "B2": "Voicemeeter Aux Output"}


def pick_bus(app: KnownApp, routing: Sequence[Mapping[str, Any]], buses: Sequence[str],
             preferred: str | None = None) -> tuple[str, int | None]:
    """(bus, numéro du bouton de la matrice) à utiliser pour cette application.

    Dans l'ordre : le bus choisi à la main (`preferred`) ; le bouton de la matrice qui porte le
    nom de l'application (« Discord », « Obs ») ; le bus conseillé pour ce type d'application.
    Un bus absent de l'édition de Voicemeeter est remplacé par son premier bus B."""
    def knob_of(bus: str) -> int | None:
        return next((i for i, r in enumerate(routing) if str(r.get("bus", "")).upper() == bus), None)

    wanted = str(preferred or "").upper()
    if wanted not in buses:
        wanted = ""
    if not wanted:
        names = {app.key.lower(), app.name.lower(), *(s.lower().removesuffix(".exe") for s in app.exe_names)}
        for route in routing:
            label = str(route.get("label", "")).strip().lower()
            bus = str(route.get("bus", "")).upper()
            if label and bus in buses and any(label in name or name in label for name in names):
                wanted = bus
                break
    if not wanted:
        wanted = DEFAULT_BUS.get(app.kind, "B1")
        if wanted not in buses:
            wanted = next((b for b in buses if b.startswith("B")), buses[0] if buses else "B1")
    return wanted, knob_of(wanted)


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RouteCommand:
    strip: int
    route: int                  # numéro du bouton de la matrice
    enabled: bool


@dataclass(frozen=True)
class VoiceRoute:
    app: KnownApp
    transport: str
    bus: str
    route: int | None                       # bouton de la matrice qui commande ce bus (None : aucun)
    feed: tuple[int, ...]                   # tranches à envoyer vers l'application
    block: tuple[int, ...]                  # tranches à ne PAS envoyer (écho)
    device: str                             # périphérique à choisir comme micro dans l'application
    vban: VbanStream | None = None
    steps: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def commands(self) -> list[RouteCommand]:
        """Routes à régler sur la console (bus seulement) : envoyer `feed`, couper `block`."""
        if self.transport != TRANSPORT_BUS or self.route is None:
            return []
        return ([RouteCommand(s, self.route, True) for s in self.feed]
                + [RouteCommand(s, self.route, False) for s in self.block])

    def pending(self, params: MixerParams | None) -> list[RouteCommand]:
        """Les ordres qui changeraient vraiment quelque chose (rien : le routage est déjà conforme)."""
        if params is None:
            return self.commands()
        pending = []
        for command in self.commands():
            if command.strip >= len(params.strips):
                continue
            routes = params.strips[command.strip].routes
            if command.route < len(routes) and routes[command.route] != command.enabled:
                pending.append(command)
        return pending

    def vban_params(self) -> tuple[tuple[str, float | str], ...]:
        return self.vban.params() if self.transport == TRANSPORT_VBAN and self.vban is not None else ()

    @property
    def target(self) -> str:
        """Où va le son, en une expression."""
        if self.transport == TRANSPORT_VBAN and self.vban is not None:
            return self.vban.describe()
        return f"bus {self.bus} (« {self.device} »)"

    def summary(self) -> str:
        return f"{self.app.name} ← {self.target}"


def _feed_and_block(app: KnownApp, plan: LayoutPlan | None, physical: Sequence[bool]
                    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Tranches à envoyer / à couper pour cette application."""
    if plan is not None and plan.roles:
        by_role = plan.roles                                   # rôle → tranche
        if app.kind is AppKind.BROADCAST:
            feed = [s for role, s in by_role.items() if role != ROLE_MASTER and role in ALL_ROLES]
            block: list[int] = []
        else:
            feed = [s for role, s in by_role.items() if role in VOICE_FEED]
            block = [s for role, s in by_role.items() if role in ECHO_ROLES] \
                if app.kind is AppKind.VOICE_CHAT else []
        return tuple(sorted(set(feed))), tuple(sorted(set(block) - set(feed)))
    # Disposition d'origine : sans rôles, les entrées matérielles (micros) sont la voix
    return tuple(i for i, is_physical in enumerate(physical) if is_physical), ()


def plan_voice_route(app: KnownApp | None, transport: str = TRANSPORT_BUS, *,
                     routing: Sequence[Mapping[str, Any]] = (), buses: Sequence[str] = ("B1", "B2"),
                     preferred_bus: str | None = None, plan: LayoutPlan | None = None,
                     physical: Sequence[bool] = (), vban: VbanStream | None = None,
                     strip_names: Sequence[str] = ()) -> VoiceRoute | None:
    """Plan du routage vers l'application vocale (None : pas d'application vocale)."""
    if app is None or app.kind not in VOICE_KINDS:
        return None
    transport = transport if transport in TRANSPORTS else TRANSPORT_BUS
    bus, knob = pick_bus(app, routing, buses, preferred_bus)
    feed, block = _feed_and_block(app, plan, physical)
    device = recording_device(bus)
    stream = vban or VbanStream()
    where = SETTINGS_PATH.get(app.key, "les paramètres audio de l'application")

    def names(indexes: Sequence[int]) -> str:
        return ", ".join(strip_names[i] if i < len(strip_names) and strip_names[i] else f"tranche {i + 1}"
                         for i in indexes) or "aucune tranche"

    steps: list[str] = []
    warnings: list[str] = []
    if transport == TRANSPORT_BUS:
        legacy = LEGACY_DEVICES.get(bus)
        older = f" (« {legacy} » avec les anciennes versions de Voicemeeter)" if legacy else ""
        steps.append(f"Dans {app.name} ({where}), choisis comme micro / entrée : « {device} »{older}.")
        if app.kind is AppKind.VOICE_CHAT:
            steps.append(f"Choisis comme sortie / haut-parleurs : « Voicemeeter Input (VAIO) », pour que "
                         f"la voix de {app.name} arrive dans la console (tranche VAIO).")
        if knob is not None:
            steps.append(f"Le bus {bus} est le bouton « {routing[knob].get('label', bus)} » de la matrice : "
                         f"« Appliquer le routage » y envoie {names(feed)}"
                         + (f" et coupe {names(block)} (pas d'écho)." if block else "."))
        else:
            warnings.append(f"Le bus {bus} n'a pas de bouton dans la matrice : ajoute-le dans "
                            f"config.json → voicemeeter → routing pour le commander d'ici, ou règle-le "
                            f"dans Voicemeeter.")
        if bus.startswith("A"):
            warnings.append(f"{bus} est une sortie matérielle : {app.name} ne peut la capter que si "
                            f"Voicemeeter l'envoie vers un câble virtuel (VB-Cable) ; un bus B est plus simple.")
        if not feed:
            warnings.append("Aucune tranche de voix (micro) n'est reconnue : choisis les rôles des "
                            "tranches dans Réglages → Layout et Apparence.")
    else:
        steps.append(f"Voicemeeter émettra le {stream.describe()}.")
        steps.append("Dans Voicemeeter, ouvre le menu VBAN et choisis, dans la colonne « Route » de ce flux, "
                     "le bus à envoyer.")
        steps.append(f"Sur l'ordinateur de {app.name}, installe un récepteur VBAN (VB-Audio VBAN Receptor) "
                     f"réglé sur le flux « {stream.name} » (port {stream.port}), puis choisis son "
                     f"périphérique comme micro dans {app.name} ({where}).")
        warnings.append("VBAN : expérimental. Les réglages du flux sont envoyés par l'API Remote de "
                        "Voicemeeter ; vérifie dans sa fenêtre VBAN qu'ils sont bien pris en compte.")
    return VoiceRoute(app, transport, bus, knob, feed, block, device, stream if transport == TRANSPORT_VBAN
                      else None, tuple(steps), tuple(warnings))


def describe_status(route: VoiceRoute | None, params: MixerParams | None) -> str:
    """Une ligne pour les réglages : où va le son, et si la console est déjà conforme."""
    if route is None:
        return "Aucune application vocale à alimenter."
    if route.transport == TRANSPORT_VBAN:
        return route.summary() + " (expérimental)"
    if route.route is None:
        return f"{route.summary()} — bus à régler dans Voicemeeter"
    pending = route.pending(params)
    if not pending:
        return f"{route.summary()} — routage conforme"
    return f"{route.summary()} — {len(pending)} route{'s' if len(pending) > 1 else ''} à régler"

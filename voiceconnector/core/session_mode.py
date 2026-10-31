"""
Modes de fonctionnement de VoiceConnector et mode musical (logique pure, sans Qt).

Deux modes, au choix dans Réglages → « Mode et thème » (config `session.mode`) :

- A, « voicemeeter » : Voicemeeter fait le mixage. VoiceConnector le pilote, et
  alimente une application de voix ou d'analyse (Discord, OBS, TeamSpeak,
  Audacity…) par un bus (B1, B2…) ou, en option, par un flux VBAN (voir mixer_router).
- B, « standalone » : sans Voicemeeter. Les canaux viennent d'un profil (importé,
  ou choisi d'après le logiciel détecté) : sessions audio Windows par application,
  micro, pistes du mixer de FL Studio (voir standalone_engine).

Le mode musical (bouton « Mode musical » de la barre d'actions) n'existe QUE si le
logiciel suivi a une partie musique / DAW (FL Studio, Ableton, REAPER, Cubase…) :
jamais pour une application seulement vocale (Discord, Zoom, Spotify…). Activé :

1. la console prend la disposition studio (FL Studio / MAO) ;
2. les traitements voix inutiles sont libérés (voir `music_release`) : l'insert
   Voicemeeter (un aller-retour de plus par l'ASIO) et le compresseur / gate de
   ces canaux sont coupés, ce qui réduit la latence ;
3. les faders, muets, solos et panoramiques sont synchronisés avec les pistes du
   mixer de FL Studio (voir fl_sync), avec ou sans Voicemeeter.

Config (section `session`) :
    mode              « voicemeeter » (défaut) ou « standalone »
    app               logiciel suivi : « auto » (détection) ou une clé du catalogue
    profile           profil du mode autonome : chemin d'un fichier, ou « » (automatique)
    voice_app         application vocale alimentée en mode A : « auto » ou une clé
    voice_transport   {clé d'application: « bus » ou « vban »}
    voice_bus         {clé d'application: bus choisi à la main (« B2 »)}
    vban              flux VBAN sortant : {index, name, ip, port} (expérimental)
    overrides         {nom du profil: {numéro de canal: {name, app}}} : choix faits dans la console (mode B)
    music_mode        dernier état du mode musical (rétabli au démarrage si possible)
    detect_interval_s délai entre deux détections de logiciels (secondes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, MutableMapping

from core.app_detector import AUTO, ActiveApp, MusicButtonState, known_app, music_button_state
from core.layout_manager import (MODE_STANDALONE, MODE_VOICEMEETER, ROLE_DISCORD, ROLE_GUEST,
                                 ROLE_MIC, ROLE_VOICE, LayoutManager, LayoutPlan)
from core.mixer_router import TRANSPORT_BUS, TRANSPORT_VBAN, TRANSPORTS  # noqa: F401  (réexportés)

log = logging.getLogger(__name__)

MODES = (MODE_VOICEMEETER, MODE_STANDALONE)
DEFAULT_MODE = MODE_VOICEMEETER
MODE_LABELS = {
    MODE_VOICEMEETER: "A — Voicemeeter + application vocale",
    MODE_STANDALONE: "B — Autonome (sans Voicemeeter)",
}
MODE_DESCRIPTIONS = {
    MODE_VOICEMEETER: ("Voicemeeter mixe le son. VoiceConnector le pilote et alimente une application "
                       "de voix ou d'analyse (Discord, OBS, TeamSpeak, Audacity…) par un bus ou un flux VBAN."),
    MODE_STANDALONE: ("Aucun Voicemeeter : les canaux viennent d'un profil (importé ou choisi d'après le "
                      "logiciel détecté) : applications, micro, pistes de FL Studio. Le logiciel détecté "
                      "réorganise la console."),
}
DEFAULT_DETECT_S = 3.0
MIN_DETECT_S, MAX_DETECT_S = 1.0, 60.0

FLSTUDIO_HOST = "flstudio"      # = core.effects.HOST_FLSTUDIO (ce module reste sans Qt ; un test le vérifie)

# Canaux dont le traitement voix est libéré en mode musical
VOCAL_ROLES = frozenset({ROLE_MIC, ROLE_VOICE, ROLE_GUEST, ROLE_DISCORD})


# --------------------------------------------------------------------------- #
# Réglages de session
# --------------------------------------------------------------------------- #
@dataclass
class SessionSettings:
    """Lecture / écriture validées de la section `session` de la config."""

    cfg: MutableMapping[str, Any]

    def _section(self) -> MutableMapping[str, Any]:
        return self.cfg.setdefault("session", {})

    # ------------------------------------------------------------------ mode
    @property
    def mode(self) -> str:
        value = str(self._section().get("mode") or DEFAULT_MODE).lower()
        return value if value in MODES else DEFAULT_MODE

    def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            log.warning("Mode « %s » inconnu ignoré.", mode)
            return self.mode
        self._section()["mode"] = mode
        return mode

    @property
    def standalone(self) -> bool:
        return self.mode == MODE_STANDALONE

    # ----------------------------------------------------- logiciel suivi
    @property
    def app(self) -> str:
        """Clé du logiciel suivi, ou « auto » (détection)."""
        return _known_or_auto(self._section().get("app"))

    def set_app(self, key: str | None) -> str:
        self._section()["app"] = _known_or_auto(key)
        return self.app

    @property
    def voice_app(self) -> str:
        return _known_or_auto(self._section().get("voice_app"))

    def set_voice_app(self, key: str | None) -> str:
        self._section()["voice_app"] = _known_or_auto(key)
        return self.voice_app

    def transport(self, app_key: str) -> str:
        table = self._section().get("voice_transport")
        value = table.get(app_key) if isinstance(table, dict) else None
        return value if value in TRANSPORTS else TRANSPORT_BUS

    def set_transport(self, app_key: str, transport: str) -> str:
        if transport not in TRANSPORTS:
            log.warning("Transport « %s » inconnu ignoré.", transport)
            return self.transport(app_key)
        table = self._section().setdefault("voice_transport", {})
        table[app_key] = transport
        return transport

    def voice_bus(self, app_key: str) -> str | None:
        """Bus choisi à la main pour cette application vocale (None : choix automatique)."""
        table = self._section().get("voice_bus")
        value = str(table.get(app_key) or "").upper() if isinstance(table, dict) else ""
        return value if len(value) == 2 and value[0] in "AB" and value[1].isdigit() else None

    def set_voice_bus(self, app_key: str, bus: str | None) -> None:
        table = self._section().setdefault("voice_bus", {})
        if bus:
            table[app_key] = str(bus).upper()
        else:
            table.pop(app_key, None)

    @property
    def vban(self) -> dict[str, Any]:
        """Flux VBAN sortant (index, name, ip, port), tel qu'enregistré ; validé par VbanStream."""
        value = self._section().get("vban")
        return dict(value) if isinstance(value, dict) else {}

    def set_vban(self, values: dict[str, Any]) -> None:
        self._section()["vban"] = dict(values)

    # ----------------------------------------------------------- profil
    @property
    def profile(self) -> str:
        return str(self._section().get("profile") or "")

    def set_profile(self, path: str | None) -> None:
        self._section()["profile"] = str(path or "")

    def overrides(self, profile_name: str) -> dict[int, dict[str, str]]:
        """Choix faits depuis la console pour ce profil : {canal: {name, app}} (mode autonome)."""
        table = self._section().get("overrides")
        raw = table.get(profile_name) if isinstance(table, dict) else None
        result: dict[int, dict[str, str]] = {}
        for key, item in (raw.items() if isinstance(raw, dict) else ()):
            if str(key).isdigit() and isinstance(item, dict):
                result[int(key)] = {k: str(v) for k, v in item.items() if k in ("name", "app") and v}
        return result

    def set_override(self, profile_name: str, index: int, *, name: str | None = None,
                     app: str | None = None) -> None:
        table = self._section().setdefault("overrides", {}).setdefault(profile_name, {})
        item = table.setdefault(str(int(index)), {})
        for field_name, value in (("name", name), ("app", app)):
            if value is not None:
                if str(value).strip():
                    item[field_name] = str(value).strip()
                else:
                    item.pop(field_name, None)
        if not item:
            del table[str(int(index))]

    # -------------------------------------------------------- mode musical
    @property
    def music_mode(self) -> bool:
        return bool(self._section().get("music_mode", False))

    def remember_music_mode(self, on: bool) -> None:
        self._section()["music_mode"] = bool(on)

    # -------------------------------------------------------- détection
    @property
    def detect_interval_s(self) -> float:
        try:
            value = float(self._section().get("detect_interval_s", DEFAULT_DETECT_S))
        except (TypeError, ValueError):
            value = DEFAULT_DETECT_S
        return max(MIN_DETECT_S, min(MAX_DETECT_S, value))


def _known_or_auto(key: Any) -> str:
    text = str(key or AUTO).strip().lower()
    if text == AUTO:
        return AUTO
    app = known_app(text)
    return app.key if app is not None else AUTO


# --------------------------------------------------------------------------- #
# Mode musical
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MusicTransition:
    """Résultat d'une demande ou d'un événement sur le mode musical."""

    on: bool                    # état après la transition
    changed: bool               # l'état a-t-il changé ?
    reason: str = ""            # « toggle », « unavailable », « app_lost », « restore »
    message: str = ""           # texte pour l'utilisateur (toast), s'il y a lieu


class MusicModeController:
    """État du mode musical : l'interface l'interroge et lui transmet les événements.

    Règles : activable seulement si le logiciel actif a une partie musicale ;
    quitté d'office quand le logiciel suivi n'en a plus (FL Studio fermé alors
    qu'il était détecté, ou remplacé par Discord) ; l'état est mémorisé dans la
    config, mais rétabli au démarrage seulement si le logiciel est là."""

    def __init__(self, session: SessionSettings, layouts: LayoutManager) -> None:
        self.session = session
        self.layouts = layouts
        self._on = False

    @property
    def on(self) -> bool:
        return self._on

    def _apply(self, on: bool, active: ActiveApp) -> None:
        self._on = on
        self.layouts.music_mode = on
        self.layouts.active = active
        self.session.remember_music_mode(on)

    def available(self, active: ActiveApp) -> bool:
        return active.has_music_production

    def restore(self, active: ActiveApp) -> MusicTransition:
        """Au démarrage : rétablit le dernier état si le logiciel musical est présent."""
        self.layouts.active = active
        if self.session.music_mode and self.available(active):
            self._apply(True, active)
            return MusicTransition(True, True, "restore",
                                   f"Mode musical rétabli ({active.name}).")
        if self.session.music_mode:
            self.session.remember_music_mode(False)
        return MusicTransition(False, False)

    def request(self, on: bool, active: ActiveApp) -> MusicTransition:
        """Demande de l'utilisateur (clic sur le bouton)."""
        if on and not self.available(active):
            self._apply(False, active)
            return MusicTransition(False, self._on, "unavailable",
                                   "Le mode musical n'est proposé qu'avec un logiciel de production "
                                   "musicale (FL Studio, Ableton, REAPER, Cubase…).")
        changed = on != self._on
        self._apply(on, active)
        if not changed:
            return MusicTransition(on, False, "toggle")
        message = (f"Mode musical : disposition studio, traitements voix libérés ({active.name})."
                   if on else "Mode musical désactivé : disposition et traitements d'avant rétablis.")
        return MusicTransition(on, True, "toggle", message)

    def app_changed(self, active: ActiveApp) -> MusicTransition:
        """Le logiciel suivi a changé : on quitte le mode musical s'il n'a plus de raison d'être."""
        self.layouts.active = active
        if self._on and not self.available(active):
            self._apply(False, active)
            why = ("n'est plus détecté" if active.app is None
                   else f"ne s'applique pas à {active.name}")
            return MusicTransition(False, True, "app_lost", f"Mode musical quitté : le logiciel musical {why}.")
        return MusicTransition(self._on, False)

    def button(self, active: ActiveApp, sync_connected: bool = False,
               sync_supported: bool | None = None) -> MusicButtonState:
        """État du bouton de la barre d'actions (caché sans logiciel musical)."""
        return music_button_state(active, self._on, sync_connected, sync_supported)


# --------------------------------------------------------------------------- #
# Libération des traitements voix
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MusicRelease:
    """Canaux dont le traitement voix est libéré tant que le mode musical est actif."""

    strips: frozenset[int] = frozenset()
    host_conflict: bool = False     # FL Studio est à la fois moteur d'effets et logiciel de production

    def __contains__(self, index: object) -> bool:
        return index in self.strips


NO_RELEASE = MusicRelease()


def music_release(plan: LayoutPlan, *, host_kind: str, active: ActiveApp) -> MusicRelease:
    """Canaux à libérer en mode musical.

    - les canaux de rôle vocal (micro, voix, invité, Discord) et ceux que la
      disposition studio ne montre pas : leur insert Voicemeeter (un aller-retour
      ASIO de plus) et leur compresseur / gate sont coupés ;
    - tous les canaux si FL Studio est à la fois le moteur d'effets (insert
      « Voicemeeter Insert Virtual ASIO ») et le logiciel de production : il ne
      peut pas utiliser deux pilotes ASIO, donc aucun canal ne doit rester
      aiguillé vers l'insert (il serait muet)."""
    indexes = {s.index for s in plan.strips}
    conflict = host_kind == FLSTUDIO_HOST and active.key == "fl_studio"
    if conflict:
        return MusicRelease(frozenset(indexes), True)
    vocal = {s.index for s in plan.strips if s.role in VOCAL_ROLES}
    return MusicRelease(frozenset(vocal | set(plan.hidden)), False)


def direct_return_help(release: MusicRelease, active: ActiveApp) -> list[str]:
    """Consignes pour le retour ASIO direct de la production (affichées au passage en mode musical).

    VoiceConnector ne peut pas changer le pilote audio de FL Studio : il libère le
    chemin d'insert et explique le réglage à faire dans le logiciel."""
    name = active.name if active.app is not None else "le logiciel musical"
    lines = [f"Dans {name} : choisis ton pilote ASIO (ex. « FL Studio ASIO » ou celui de ton interface) "
             "pour un retour direct, sans passer par Voicemeeter."]
    count = len(release.strips)
    if release.host_conflict:
        lines.append("FL Studio ne peut pas être à la fois moteur d'effets et logiciel de production : "
                     "les inserts Voicemeeter sont libérés, les voix passent sans effets FL Studio.")
    elif count:
        lines.append(("1 canal de voix passe" if count == 1 else f"{count} canaux de voix passent")
                     + " sans insert ni compresseur / gate : moins de latence.")
    return lines


@dataclass
class DynamicsStash:
    """Compresseur / gate des canaux libérés : remis à 0 dans le moteur pendant le
    mode musical, puis rétablis à la sortie (les potentiomètres gardent leurs valeurs)."""

    saved: dict[int, tuple[float, float]] = field(default_factory=dict)

    def release(self, strips, current: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float]]:
        """Mémorise les réglages de `strips` ; renvoie ce qu'il faut envoyer au moteur (zéros)."""
        sent = {}
        for index in strips:
            if index not in self.saved:
                self.saved[index] = current.get(index, (0.0, 0.0))
            sent[index] = (0.0, 0.0)
        return sent

    def restore(self) -> dict[int, tuple[float, float]]:
        """Réglages à renvoyer au moteur ; vide la mémoire."""
        restored, self.saved = dict(self.saved), {}
        return restored

    def sync(self, strips, current: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float]]:
        """Met la mémoire à jour pour les canaux à libérer MAINTENANT (`strips`) ; renvoie ce qu'il
        faut envoyer au moteur : des zéros pour les canaux qui viennent d'être libérés, et les
        réglages d'avant pour ceux qui ne le sont plus (la disposition a changé en mode musical)."""
        wanted = set(strips)
        sent = {index: self.saved.pop(index) for index in sorted(set(self.saved) - wanted)}
        sent.update(self.release(sorted(wanted - set(self.saved)), current))
        return sent

    def holds(self, index: int) -> bool:
        """Ce canal est-il libéré (son compresseur / gate ne doivent pas être envoyés au moteur) ?"""
        return index in self.saved

    def remember(self, index: int, comp: float | None = None, gate: float | None = None) -> bool:
        """Un potentiomètre a bougé sur un canal libéré : la nouvelle valeur sera rétablie à la sortie
        du mode musical. Renvoie vrai si le canal est libéré (rien ne doit alors partir au moteur)."""
        if index not in self.saved:
            return False
        old_comp, old_gate = self.saved[index]
        self.saved[index] = (old_comp if comp is None else float(comp),
                             old_gate if gate is None else float(gate))
        return True

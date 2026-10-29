"""
Le Studio : quel logiciel de musique il pilote, par quel canal, et que faire quand ça ne marche pas
(sans Qt).

Trois façons de piloter un logiciel :
    - FL Studio : un script MIDI installé dans FL Studio (SysEx), qui donne channel rack, pas du
      séquenceur, patterns, playlist, tempo et position ;
    - Mackie Control : Cubase / Nuendo, Studio One, Ableton Live, REAPER, Bitwig, Reason, Cakewalk,
      Ardour… se comportent comme avec une vraie surface de contrôle (pistes, faders, transport) ;
    - la démonstration : un projet fictif, pour essayer la fenêtre sans rien installer.

`StudioSession` choisit, ouvre et surveille la liaison. Elle n'échoue jamais : quand le logiciel n'est
pas lancé, ou que les ports MIDI manquent, la fenêtre reste utilisable en démonstration, dit pourquoi,
et se relie toute seule dès que c'est possible.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core import fl_sync
from core.app_detector import AUTO, DetectedApp, known_app
from core.daw_model import DawLink
from core.demo_daw import DemoDaw
from core.fl_studio_link import open_fl_studio_link
from core.fl_sync import SetupCheck, TransportError
from core.mcu_link import McuLink
from core.midi_ports import mcu_ports, mcu_setup_checks, open_mcu_transport
from core.transport_hub import TransportHub

log = logging.getLogger(__name__)

BACKEND_FL = "fl"
BACKEND_MCU = "mcu"
BACKEND_DEMO = "demo"

DEMO_KEY = "demo"
RETRY_S = 3.0                                  # on regarde si le logiciel est lancé, et on retente la liaison
UNAVAILABLE_RETRY_S = 6.0                      # idem quand les ports MIDI manquaient
MISSED_SCANS = 2                               # détections sans le logiciel avant de le dire fermé

MODE_LIVE = "live"                             # liaison avec le vrai logiciel (réponde-t-il ou non)
MODE_DEMO = "demo"
DEMO_CHOSEN = "chosen"                         # l'utilisateur veut la démonstration
DEMO_NOT_RUNNING = "not_running"               # aucun logiciel de musique n'est lancé
DEMO_UNAVAILABLE = "unavailable"               # le logiciel tourne, mais la liaison MIDI est impossible

CHANGED = 1                                    # `poll()` : l'état à afficher a changé
SWITCHED = 2                                   # `poll()` : la liaison a été remplacée (ouvrir les vues de nouveau)


# --------------------------------------------------------------------------- #
# Les logiciels que le Studio sait piloter
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StudioTarget:
    key: str                                   # clé du catalogue de logiciels (app_detector), ou « demo »
    name: str
    backend: str
    steps: tuple[str, ...] = ()                # aide de configuration, une étape par ligne

    @property
    def real(self) -> bool:
        return self.backend != BACKEND_DEMO


def _mcu_steps(*menu: str) -> tuple[str, ...]:
    """Étapes communes à tous les logiciels Mackie Control : `menu` dit où ajouter la surface."""
    return (*menu,
            "Entrée MIDI de la surface : « VoiceConnector DAW ».",
            "Sortie MIDI de la surface : « VoiceConnector Retour ».",
            "Les ports « VoiceConnector DAW » et « VoiceConnector Retour » se créent une fois avec loopMIDI.",
            "Laisse le potard de chaque piste sur le panoramique (réglage par défaut).")


FL_STEPS = (
    "Crée deux ports MIDI virtuels avec loopMIDI : « VoiceConnector FL » et « VoiceConnector VC ».",
    "Clique sur « Installer le script FL Studio » (copie device_VoiceConnector.py dans "
    "Documents\\Image-Line\\FL Studio\\Settings\\Hardware\\VoiceConnector).",
    "Dans FL Studio, Options → Paramètres MIDI : entrée « VoiceConnector FL » (activée, type de contrôleur "
    "« VoiceConnector », port 1) et sortie « VoiceConnector VC » (activée, même port 1).",
    "Redémarre FL Studio si besoin : le Studio affiche alors le channel rack et la playlist de ton projet.",
)

TARGETS: tuple[StudioTarget, ...] = (
    StudioTarget("fl_studio", "FL Studio", BACKEND_FL, FL_STEPS),
    StudioTarget("cubase", "Cubase", BACKEND_MCU, _mcu_steps(
        "Dans Cubase : Studio → Configuration des studios, puis « + » → Mackie Control.")),
    StudioTarget("nuendo", "Nuendo", BACKEND_MCU, _mcu_steps(
        "Dans Nuendo : Studio → Configuration des studios, puis « + » → Mackie Control.")),
    StudioTarget("studio_one", "Studio One", BACKEND_MCU, _mcu_steps(
        "Dans Studio One : Options → Périphériques externes → Ajouter → Mackie → Control.")),
    StudioTarget("ableton", "Ableton Live", BACKEND_MCU, _mcu_steps(
        "Dans Live : Options → Préférences → Link, Tempo & MIDI → Surface de contrôle : MackieControl.")),
    StudioTarget("reaper", "REAPER", BACKEND_MCU, _mcu_steps(
        "Dans REAPER : Options → Préférences → Contrôle/OSC/web → Ajouter → Mackie Control Universal.")),
    StudioTarget("bitwig", "Bitwig Studio", BACKEND_MCU, _mcu_steps(
        "Dans Bitwig : Réglages → Contrôleurs → Ajouter un contrôleur → Mackie → Mackie Control.")),
    StudioTarget("reason", "Reason", BACKEND_MCU, _mcu_steps(
        "Dans Reason : Édition → Préférences → Surfaces de contrôle → Ajouter → Mackie → Control.")),
    StudioTarget("cakewalk", "Cakewalk", BACKEND_MCU, _mcu_steps(
        "Dans Cakewalk : Édition → Préférences → MIDI → Surfaces de contrôle → Ajouter → Mackie Control.")),
    StudioTarget("ardour", "Ardour", BACKEND_MCU, _mcu_steps(
        "Dans Ardour : Édition → Préférences → Surfaces de contrôle → Mackie Control (activer).")),
)
DEMO_TARGET = StudioTarget(DEMO_KEY, "Démonstration", BACKEND_DEMO,
                           ("Un projet fictif pour essayer le Studio : rien à installer.",))
TARGETS_BY_KEY = {target.key: target for target in (*TARGETS, DEMO_TARGET)}


def target_for(key: str | None) -> StudioTarget | None:
    return TARGETS_BY_KEY.get(str(key or "").strip().lower())


def normalize_choice(value: Any) -> str:
    """Le choix de `studio.daw` : « auto », « demo » ou la clé d'un logiciel pris en charge."""
    key = str(value or "").strip().lower()
    return key if key in TARGETS_BY_KEY else AUTO


def choose_target(selected: str, detected: Sequence[DetectedApp] = ()) -> tuple[StudioTarget | None, bool]:
    """(logiciel visé, lancé ?) : le choix de l'utilisateur, ou le premier logiciel pris en charge que
    la détection voit tourner (None : « auto » et aucun logiciel en vue)."""
    choice = normalize_choice(selected)
    running = {app.key for app in detected}
    if choice != AUTO:
        target = TARGETS_BY_KEY[choice]
        return target, target.key in running or not target.real
    candidates = [(known_app(app.key), app) for app in detected if app.key in TARGETS_BY_KEY]
    candidates = [(known, app) for known, app in candidates if known is not None]
    if not candidates:
        return None, False
    best = min(candidates, key=lambda pair: (pair[0].priority, TARGETS.index(TARGETS_BY_KEY[pair[1].key])))
    return TARGETS_BY_KEY[best[1].key], True


def studio_settings(cfg: Mapping | None) -> dict:
    section = (cfg or {}).get("studio", {})
    return dict(section) if isinstance(section, Mapping) else {}


def setup_checks(target: StudioTarget, cfg: Mapping | None = None, *, documents: Path | None = None,
                 source: Path | None = None, mido_module: Any = None) -> list[SetupCheck]:
    """Ce qui est prêt, et ce qui manque, pour relier ce logiciel (ne modifie rien, n'ouvre aucun port)."""
    if target.backend == BACKEND_FL:
        return fl_sync.fl_setup_checks(cfg, documents=documents, source=source, mido_module=mido_module)
    if target.backend == BACKEND_MCU:
        return mcu_setup_checks(cfg, mido_module=mido_module)
    return [SetupCheck(True, "Démonstration : rien à configurer.")]


# --------------------------------------------------------------------------- #
# La session
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StudioStatus:
    kind: str                                  # live | waiting | demo | error
    text: str                                  # bandeau affiché au-dessus du Studio ("" : rien à dire)
    configurable: bool                         # proposer « Configurer… »


class StudioSession:
    """Le logiciel que la fenêtre Studio pilote. `link` est toujours utilisable (démonstration au besoin)."""

    def __init__(self, cfg: Mapping | None = None, *,
                 scan: Callable[[], Sequence[DetectedApp]] | None = None,
                 hub: TransportHub | None = None,
                 mcu_opener: Callable[[], Any] | None = None,
                 demo_factory: Callable[[], DemoDaw] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg = cfg if cfg is not None else {}
        self._scan = scan or (lambda: ())
        self._own_hub = hub is None
        self._hub = hub if hub is not None else TransportHub(lambda: fl_sync.open_transport(self._cfg))
        self._mcu_opener = mcu_opener or (lambda: open_mcu_transport(self._cfg))
        self._demo_factory = demo_factory or DemoDaw
        self._clock = clock
        settings = studio_settings(self._cfg)
        self._selected = normalize_choice(settings.get("daw", AUTO))
        self._next_check = 0.0
        self._missed = 0
        self._marked_offline = False
        self._last_reason = ""
        self.target: StudioTarget = DEMO_TARGET
        self.mode = MODE_DEMO
        self.demo_reason = DEMO_NOT_RUNNING
        self.reason = ""
        self.link: DawLink = DawLink()
        self._opened = False

    # --------------------------------------------------------------- choix
    @property
    def selected(self) -> str:
        return self._selected

    @property
    def demo(self) -> bool:
        return self.mode == MODE_DEMO

    def select(self, key: str) -> None:
        """Change le logiciel piloté : la liaison est rouverte."""
        self._selected = normalize_choice(key)
        if isinstance(self._cfg, dict):
            self._cfg.setdefault("studio", {})["daw"] = self._selected
        self.open()

    def reconnect(self) -> None:
        self.open()

    # ------------------------------------------------------- cycle de vie
    def open(self) -> None:
        """(Re)choisit le logiciel et ouvre la liaison ; la démonstration prend le relais au besoin."""
        self._close_link()
        self._opened = True
        self._missed = 0
        detected = self._detected()
        target, running = choose_target(self._selected, detected)
        explicit = self._selected != AUTO
        if self._selected == DEMO_KEY:
            self._use_demo(DEMO_TARGET, DEMO_CHOSEN)
        elif target is None:
            self._use_demo(DEMO_TARGET, DEMO_NOT_RUNNING)
        elif explicit or running:
            self._open_live(target)
        else:
            self._use_demo(target, DEMO_NOT_RUNNING)
        self._next_check = self._clock() + self._retry_delay()

    def close(self) -> None:
        self._close_link()
        self._opened = False

    def _close_link(self) -> None:
        link, self.link = self.link, DawLink()
        try:
            link.close()
        except Exception as exc:
            log.debug("Fermeture de la liaison du Studio : %s", exc)

    def _detected(self) -> Sequence[DetectedApp]:
        try:
            return list(self._scan() or ())
        except Exception as exc:                 # jamais fatale : le Studio marche sans détection
            log.debug("Détection des logiciels : %s", exc)
            return []

    def _retry_delay(self) -> float:
        return UNAVAILABLE_RETRY_S if self.demo and self.demo_reason == DEMO_UNAVAILABLE else RETRY_S

    # ------------------------------------------------------ liaisons
    def _use_demo(self, target: StudioTarget, why: str, reason: str = "") -> None:
        self.mode, self.demo_reason, self.reason = MODE_DEMO, why, reason
        self.target = target
        demo = self._demo_factory()
        demo.start()
        self.link = demo

    def _open_live(self, target: StudioTarget) -> None:
        link, reason = self._make_link(target)
        if link is None:
            self._note_reason(target, reason)
            self._use_demo(target, DEMO_UNAVAILABLE, reason)
            return
        self._last_reason = ""
        self.mode, self.demo_reason, self.reason = MODE_LIVE, "", ""
        self.target = target
        self.link = link
        self._marked_offline = False

    def _note_reason(self, target: StudioTarget, reason: str) -> None:
        if reason != self._last_reason:                  # dit une fois, pas à chaque nouvel essai
            log.info("Studio : liaison avec %s impossible : %s", target.name, reason)
        self._last_reason = reason

    def _try_live(self, target: StudioTarget) -> bool:
        """Tente la vraie liaison sans toucher à la démonstration en cours ; vrai si elle a pris le relais."""
        link, reason = self._make_link(target)
        if link is None:
            self._note_reason(target, reason)
            if self.demo and self.demo_reason == DEMO_UNAVAILABLE:
                self.reason = reason
            return False
        previous, self.link = self.link, link
        try:
            previous.close()
        except Exception as exc:
            log.debug("Fermeture de la démonstration : %s", exc)
        self._last_reason = ""
        self.mode, self.demo_reason, self.reason = MODE_LIVE, "", ""
        self.target = target
        self._marked_offline = False
        self._missed = 0
        return True

    def _make_link(self, target: StudioTarget) -> tuple[DawLink | None, str]:
        try:
            if target.backend == BACKEND_FL:
                return open_fl_studio_link(self._hub.open, self._cfg)
            section = studio_settings(self._cfg)
            transport = self._mcu_opener()
            try:
                keepalive = float(section.get("mcu_keepalive_s") or 0.0)
            except (TypeError, ValueError):
                keepalive = 0.0
            link = McuLink(transport, daw=target.name, keepalive_s=keepalive)
            link.start()
            return link, ""
        except TransportError as exc:
            return None, str(exc)
        except Exception as exc:                  # bibliothèque MIDI défaillante
            log.warning("Studio : liaison MIDI inutilisable : %s", exc)
            return None, f"Ports MIDI inutilisables : {exc}"

    # --------------------------------------------------------------- temps
    def poll(self) -> int:
        """À appeler souvent (QTimer, ~30 ms). Renvoie CHANGED et / ou SWITCHED (0 : rien à redessiner)."""
        if not self._opened:
            return 0
        flags = CHANGED if self.link.poll() else 0
        now = self._clock()
        if now >= self._next_check:
            self._next_check = now + self._retry_delay()
            flags |= self._check()
        return flags

    def _check(self) -> int:
        """Le logiciel est-il (encore) lancé ? Se relie quand il apparaît, le dit quand il disparaît."""
        detected = self._detected()
        if self.demo:
            return self._check_demo(detected)
        if self.target.key in {app.key for app in detected}:
            self._missed = 0
            if self._marked_offline:                      # il était donné pour fermé : on se présente de nouveau
                self._marked_offline = False
                refresh = getattr(self.link, "refresh", None)
                if callable(refresh):
                    refresh()
            return 0
        self._missed += 1
        if self._missed >= MISSED_SCANS and self.link.state.connected:
            self._marked_offline = True
            return CHANGED if self.link.mark_offline(f"{self.target.name} est fermé.") else 0
        return 0

    def _check_demo(self, detected: Sequence[DetectedApp]) -> int:
        """Démonstration en cours : le logiciel est-il apparu (mode auto) ou les ports sont-ils prêts ?"""
        target, _ = choose_target(self._selected, detected)
        if target is None or not target.real:                  # rien en vue, ou démonstration voulue
            return 0
        return (SWITCHED | CHANGED) if self._try_live(target) else 0

    # --------------------------------------------------------------- statut
    def status(self) -> StudioStatus:
        if self.demo:
            if self.demo_reason == DEMO_CHOSEN:
                return StudioStatus("demo", "Démonstration : un projet fictif pour essayer le Studio.", False)
            if self.demo_reason == DEMO_UNAVAILABLE:
                return StudioStatus("error", f"{self.target.name} : {self.reason.rstrip('.')}. "
                                              "Démonstration en attendant.", True)
            return StudioStatus("demo", "Aucun logiciel de musique n'est lancé : voici une démonstration. La fenêtre "
                                        "se reliera toute seule dès que FL Studio, Cubase, Ableton… démarre.", True)
        state = self.link.state
        if state.connected:
            return StudioStatus("live", "", False)
        return StudioStatus("waiting", state.message or f"En attente de {self.target.name}…", True)

    @property
    def guide_target(self) -> StudioTarget:
        """Le logiciel dont on explique la configuration : le visé, ou FL Studio quand rien n'est visé (démo)."""
        return self.target if self.target.real else TARGETS[0]

    def checks(self) -> list[SetupCheck]:
        return setup_checks(self.guide_target, self._cfg)

    def help_steps(self) -> tuple[str, ...]:
        return self.guide_target.steps

    def ports_text(self) -> str:
        """Les ports MIDI virtuels que le logiciel visé doit voir (pour l'aide)."""
        if self.guide_target.backend == BACKEND_FL:
            out_port, in_port = fl_sync_ports(self._cfg)
            return f"« {out_port} » (VoiceConnector → FL Studio) et « {in_port} » (FL Studio → VoiceConnector)"
        out_port, in_port = mcu_ports(self._cfg)
        return f"« {out_port} » (VoiceConnector → logiciel) et « {in_port} » (logiciel → VoiceConnector)"


def fl_sync_ports(cfg: Mapping | None) -> tuple[str, str]:
    section = (cfg or {}).get("flsync", {})
    section = section if isinstance(section, Mapping) else {}
    return (str(section.get("out_port") or fl_sync.DEFAULT_OUT_PORT),
            str(section.get("in_port") or fl_sync.DEFAULT_IN_PORT))

"""
Coordinateur de session : le « cerveau » de la fenêtre principale (logique pure, sans Qt).

La fenêtre n'a qu'à lui transmettre les événements (une détection de logiciels, un clic sur
« Mode musical », un choix dans les réglages) et à appliquer ce qu'il répond :

    detected → SessionChange(active_changed, preset_changed, music, profile)

Il réunit les briques déjà testées chacune de son côté :

- le logiciel suivi (`ActiveAppTracker`, choix `session.app`) ;
- la disposition effective (`LayoutManager` : mode musical, suivi du logiciel, choix manuel) ;
- le mode musical (`MusicModeController`) : n'existe QUE pour un logiciel qui a une partie
  musique / DAW, et est quitté d'office quand ce logiciel disparaît ;
- en mode B (autonome), le profil des canaux d'après le logiciel suivi (`profile_import`).

Tant que le logiciel suivi ne change pas, rien ne bouge : la fenêtre ne réarrange la console
que lorsqu'une réponse le demande.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping, Sequence

from core.app_detector import (ActiveApp, ActiveAppTracker, DetectedApp, MusicButtonState,
                               voice_apps)
from core.layout_manager import (FULL_CAPS, MODE_STANDALONE, MODE_VOICEMEETER, EngineCaps,
                                 LayoutManager, LayoutPlan, StripInfo)
from core.profile_import import Profile, apply_overrides, resolve_profile
from core.session_mode import (NO_RELEASE, MusicModeController, MusicRelease, MusicTransition,
                               SessionSettings, direct_return_help, music_release)


@dataclass(frozen=True)
class SessionChange:
    """Ce que la fenêtre doit refaire après un événement."""

    active_changed: bool = False             # logiciel suivi (ou son état « en cours ») différent
    preset_changed: bool = False             # disposition effective différente : réarranger la console
    music: MusicTransition | None = None     # le mode musical a changé (ou n'a pas pu l'être) : message
    profile: Profile | None = None           # mode B : nouvelle liste de canaux à charger
    note: str = ""                           # remarque sur le profil (fichier illisible…)

    @property
    def any(self) -> bool:
        return bool(self.active_changed or self.preset_changed or self.music or self.profile)


NO_CHANGE = SessionChange()


def strip_infos(vm: Any, names: Sequence[str] = ()) -> list[StripInfo]:
    """Les canaux de la console tels que la disposition les voit, avec leurs noms."""
    infos = list(vm.strip_infos())
    named = []
    for info in infos:
        name = names[info.index] if info.index < len(names) and not info.name else info.name
        named.append(info if name == info.name else StripInfo(
            info.index, info.physical, name, info.role, info.fixed, info.caps))
    return named


class SessionCoordinator:
    """État de session d'une console : mode, logiciel suivi, mode musical, disposition."""

    def __init__(self, cfg: MutableMapping[str, Any], *, host_kind: str = "none",
                 mode: str | None = None, fl_sync_available: bool = True) -> None:
        self.cfg = cfg
        self.session = SessionSettings(cfg)
        self.mode = mode if mode in (MODE_VOICEMEETER, MODE_STANDALONE) else self.session.mode
        self.layouts = LayoutManager(cfg, mode=self.mode)
        self.music = MusicModeController(self.session, self.layouts)
        self.tracker = ActiveAppTracker(selected=self.session.app)
        self.host_kind = host_kind                   # moteur d'effets (« flstudio » : conflit ASIO en mode musical)
        self.fl_sync_available = fl_sync_available   # la liaison MIDI est utilisable (mido présent)
        self.sync_connected = False                  # la synchronisation du mixer répond
        self.sync_supported: bool | None = None      # None : d'après le catalogue
        self.detected: list[DetectedApp] = []
        self.profile: Profile | None = None          # mode B : profil en cours
        self.profile_note = ""
        self.started = False                         # la première détection a été faite (voir start)
        self._preset = self.layouts.effective_preset()

    # ------------------------------------------------------------------ état
    @property
    def standalone(self) -> bool:
        return self.mode == MODE_STANDALONE

    @property
    def active(self) -> ActiveApp:
        return self.tracker.active

    @property
    def preset(self) -> str:
        """La disposition appliquée en ce moment."""
        return self._preset

    @property
    def music_on(self) -> bool:
        return self.music.on

    def voice_candidates(self) -> list[DetectedApp]:
        """Applications « voix / analyse » en cours (Discord, OBS, TeamSpeak, Audacity…)."""
        return voice_apps(self.detected)

    # ---------------------------------------------------------------- départ
    def start(self, detected: Sequence[DetectedApp]) -> SessionChange:
        """Première détection, au démarrage : logiciel suivi, mode musical rétabli s'il y a lieu."""
        self.started = True
        self.detected = list(detected)
        self.tracker.update(self.detected, self.session.app)
        transition = self.music.restore(self.active)
        self._preset = self.layouts.effective_preset()
        profile, note = self._new_profile(force=True)
        return SessionChange(True, True, transition if transition.changed else None, profile, note)

    # ------------------------------------------------------------ événements
    def on_detected(self, detected: Sequence[DetectedApp]) -> SessionChange:
        """Résultat d'une détection (toutes les quelques secondes)."""
        self.detected = list(detected)
        return self._after_active(self.tracker.update(self.detected, self.session.app))

    def select_app(self, key: str | None) -> SessionChange:
        """Choix du logiciel suivi dans les réglages (« auto » : la détection décide)."""
        self.session.set_app(key)
        return self._after_active(self.tracker.update(self.detected, self.session.app))

    def toggle_music(self, on: bool | None = None) -> SessionChange:
        """Clic sur « Mode musical » (ou demande explicite)."""
        wanted = (not self.music.on) if on is None else bool(on)
        transition = self.music.request(wanted, self.active)
        preset_changed = self._refresh_preset()
        return SessionChange(False, preset_changed, transition if transition.message else None)

    def set_layout(self, choice: str) -> SessionChange:
        """Choix de la disposition (barre d'actions, réglages) : « auto » ou une disposition."""
        self.layouts.select(choice)
        return SessionChange(False, self._refresh_preset())

    def set_follow_software(self, follow: bool) -> SessionChange:
        self.layouts.set_follow_software(follow)
        return SessionChange(False, self._refresh_preset())

    def reload_profile(self) -> SessionChange:
        """Nouveau profil choisi (fichier importé ou retiré) : mode B seulement."""
        profile, note = self._new_profile(force=True)
        return SessionChange(False, False, None, profile, note)

    # ----------------------------------------------------------- utilitaires
    def _after_active(self, changed: bool) -> SessionChange:
        music = None
        if changed:
            transition = self.music.app_changed(self.active)
            music = transition if transition.changed else None
        preset_changed = self._refresh_preset()
        profile, note = self._new_profile() if changed else (None, "")
        return SessionChange(changed, preset_changed, music, profile, note)

    def _refresh_preset(self) -> bool:
        preset = self.layouts.effective_preset()
        changed = preset != self._preset
        self._preset = preset
        return changed

    def standalone_profile(self) -> tuple[Profile, str]:
        """Profil du mode B pour le logiciel suivi, avec les choix déjà faits depuis la console."""
        profile, note = resolve_profile(self.session.profile, self.active, fl_sync=self.fl_sync_available)
        return apply_overrides(profile, self.session.overrides(profile.name)), note

    def _new_profile(self, force: bool = False) -> tuple[Profile | None, str]:
        if not self.standalone:
            return None, ""
        profile, note = self.standalone_profile()
        if not force and profile == self.profile:
            return None, ""
        self.profile, self.profile_note = profile, note
        return profile, note

    # ------------------------------------------------------ interface (barre)
    def button(self) -> MusicButtonState:
        """État du bouton « Mode musical » : caché sans logiciel musical, jamais coché alors."""
        return self.music.button(self.active, self.sync_connected, self.sync_supported)

    def plan(self, vm: Any, names: Sequence[str] = ()) -> LayoutPlan:
        """Plan de la disposition pour les canaux de cette console."""
        caps: EngineCaps = getattr(vm, "caps", FULL_CAPS)
        return self.layouts.plan(strip_infos(vm, names), caps)

    # ------------------------------------------------------- mode musical
    def release(self, plan: LayoutPlan) -> MusicRelease:
        """Canaux dont le traitement voix est libéré : seulement en mode A et en mode musical
        (le mode B n'a ni insert ni compresseur à libérer)."""
        if not self.music.on or self.standalone:
            return NO_RELEASE
        return music_release(plan, host_kind=self.host_kind, active=self.active)

    def help_lines(self, plan: LayoutPlan) -> list[str]:
        """Consignes à afficher en passant en mode musical (retour ASIO direct, canaux libérés)."""
        return direct_return_help(self.release(plan), self.active)

    @property
    def wants_fl_sync(self) -> bool:
        """La synchronisation avec le mixer de FL Studio est utile : mode musical actif et logiciel
        qui sait la faire (mode A), ou profil du mode B qui contient des pistes de FL Studio."""
        app = self.active.app
        if not self.fl_sync_available or app is None or app.sync is None:
            return False
        return self.music.on and not self.standalone

"""
Gestion des dispositions (layouts) et de l'apparence (thème) de la console.

Tout est ici de la logique pure (aucun Qt) : l'interface lit le résultat et
l'applique. Deux notions :

1. Les dispositions (LayoutPreset), au choix dans Réglages → « Layout et
   Apparence » ou dans la barre d'actions :
   - standard  : la console d'origine (entrées matérielles | entrées virtuelles) ;
   - fl_studio : FL Studio / MAO : Master, Bus Batterie, Synths, FX ; potentiomètres
                 de panoramique larges, VU-mètres haute précision ; le Master à gauche
                 comme dans le mixer de FL Studio ;
   - streaming : Streaming / Broadcast : Micro, Game, Discord / Vocal, Music ;
   - podcast   : Podcasting / Voix : clarté de la voix, égaliseur rapide,
                 Compresseur et Gate intégrés à chaque canal.

   Une disposition décrit des emplacements (SlotSpec : un rôle et un libellé).
   LayoutManager.plan() affecte chaque canal de la console à un emplacement (selon
   son type, matériel ou virtuel, et les choix de l'utilisateur) et produit un
   LayoutPlan : groupes de tranches, canaux masqués, style de chaque tranche.
   L'interface réarrange les tranches déjà construites d'après ce plan.

2. Le choix effectif de la disposition (LayoutManager.effective_preset) :
   - mode musical actif → disposition studio (fl_studio), puis retour à la
     disposition d'avant en le quittant (rien n'est écrasé : c'est un calcul) ;
   - sinon, si « suivre le logiciel » est actif → la disposition conseillée du
     logiciel actif (FL Studio → fl_studio, OBS / Discord → streaming…) ;
   - sinon la disposition choisie par l'utilisateur.

Config (section `layout`) :
    preset           disposition choisie (« standard » par défaut)
    follow_software  true / false ; null = automatique (oui en mode B, non en mode A)
    show_unassigned  garder visibles les canaux sans emplacement (groupe « AUTRES »)
    roles            {disposition: {rôle: index de canal}} : affectations manuelles
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Sequence

from core import themes
from core.app_detector import NO_APP, ActiveApp

log = logging.getLogger(__name__)

# Dispositions
STANDARD = "standard"
FL_STUDIO = "fl_studio"
STREAMING = "streaming"
PODCAST = "podcast"
AUTO = "auto"                       # valeur du sélecteur : suivre le logiciel détecté
DEFAULT_PRESET = STANDARD

# Modes de fonctionnement
MODE_VOICEMEETER = "voicemeeter"    # mode A : Voicemeeter + application de voix / d'analyse
MODE_STANDALONE = "standalone"      # mode B : autonome, sans Voicemeeter

# Rôles des emplacements (le même vocabulaire sert aux profils importés)
ROLE_MASTER, ROLE_DRUMS, ROLE_SYNTHS, ROLE_FX = "master", "drums", "synths", "fx"
ROLE_MIC, ROLE_GAME, ROLE_DISCORD, ROLE_MUSIC = "mic", "game", "discord", "music"
ROLE_VOICE, ROLE_GUEST = "voice", "guest"
ALL_ROLES = frozenset({ROLE_MASTER, ROLE_DRUMS, ROLE_SYNTHS, ROLE_FX, ROLE_MIC, ROLE_GAME,
                       ROLE_DISCORD, ROLE_MUSIC, ROLE_VOICE, ROLE_GUEST})

KIND_PHYSICAL, KIND_VIRTUAL, KIND_ANY = "physical", "virtual", "any"

# Titres des groupes de la disposition standard (ceux de la console d'origine)
GROUP_HARDWARE = "HARDWARE INPUTS"
GROUP_VIRTUAL = "VC-AUDIO / SYSTEM-CONNECTOR"
GROUP_OTHERS = "AUTRES"
GROUP_INPUTS = "ENTRÉES"                # titres de la disposition standard en mode autonome
GROUP_APPS = "APPLICATIONS"


PAN_PAD, PAN_WIDE, PAN_NONE = "pad", "wide", "none"
VU_NORMAL, VU_PRECISION = "normal", "precision"


@dataclass(frozen=True)
class StripStyle:
    """Ce qu'une tranche montre et comment elle se présente."""

    pan: str = PAN_PAD          # « pad » : Intellipan carré ; « wide » : curseur large ; « none »
    vu: str = VU_NORMAL         # « normal » ou « precision » (haute précision)
    trim: bool = True           # trim (entrées matérielles) ou Compr. + Gate (entrées virtuelles)
    dynamics: bool = False      # Compr. + Gate aussi sur les entrées matérielles
    eq: bool = False            # égaliseur rapide 3 bandes + bouton « Clarté »
    fx: bool = True             # zone d'effets
    routing: bool = True        # routage rapide (boutons ronds A1…B3)

    def __post_init__(self) -> None:
        if self.pan not in (PAN_PAD, PAN_WIDE, PAN_NONE):
            raise ValueError(f"pan inconnu : {self.pan}")
        if self.vu not in (VU_NORMAL, VU_PRECISION):
            raise ValueError(f"vu inconnu : {self.vu}")


@dataclass(frozen=True)
class EngineCaps:
    """Ce que le moteur audio sait faire : une tranche ne montre que des
    commandes qui agissent vraiment (le mode autonome n'a ni routage ni trim)."""

    routing: bool = True        # bus A / B : matrice et routage rapide
    pan: bool = True            # panoramique par canal
    trim: bool = True           # trim, compresseur et gate par canal
    eq: bool = True             # égaliseur par canal
    fx: bool = True             # chaîne d'effets (REAPER, FL Studio, Equalizer APO)

    def __and__(self, other: EngineCaps) -> EngineCaps:
        """Ce que deux moteurs savent faire ensemble (canaux de sources différentes)."""
        return EngineCaps(self.routing and other.routing, self.pan and other.pan,
                          self.trim and other.trim, self.eq and other.eq, self.fx and other.fx)


FULL_CAPS = EngineCaps()
# Mode autonome : sessions audio Windows par application (volume, muet, niveau) …
APP_SESSION_CAPS = EngineCaps(routing=False, pan=False, trim=False, eq=False, fx=False)
# … ou pistes du mixer de FL Studio (volume, muet, solo, panoramique)
FL_TRACK_CAPS = EngineCaps(routing=False, pan=True, trim=False, eq=False, fx=False)


def style_for(style: StripStyle, caps: EngineCaps) -> StripStyle:
    """Le style de la disposition, restreint à ce que le moteur sait faire."""
    return StripStyle(
        pan=style.pan if caps.pan else PAN_NONE,
        vu=style.vu,
        trim=style.trim and caps.trim,
        dynamics=style.dynamics and caps.trim,
        eq=style.eq and caps.eq,
        fx=style.fx and caps.fx,
        routing=style.routing and caps.routing)


@dataclass(frozen=True)
class SlotSpec:
    role: str
    label: str                  # libellé affiché sur la tranche (« Bus Batterie »)
    kind: str = KIND_ANY        # type de canal préféré (physical, virtual, any)


@dataclass(frozen=True)
class GroupSpec:
    title: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class LayoutPreset:
    key: str
    name: str
    description: str
    slots: tuple[SlotSpec, ...] = ()
    groups: tuple[GroupSpec, ...] = ()      # vide : regroupement matériel / virtuel d'origine
    style: StripStyle = StripStyle()
    master_side: str = "right"              # « right » ou « left »
    show_matrix: bool = True
    show_outputs: bool = True
    hide_unassigned: bool = False           # masquer les canaux sans emplacement

    def slot(self, role: str) -> SlotSpec | None:
        return next((s for s in self.slots if s.role == role), None)


LAYOUT_PRESETS: dict[str, LayoutPreset] = {
    STANDARD: LayoutPreset(
        STANDARD, "Standard",
        "La console d'origine : entrées matérielles puis entrées virtuelles, matrice de routage."),
    FL_STUDIO: LayoutPreset(
        FL_STUDIO, "FL Studio / MAO",
        "Master à gauche, Bus Batterie, Synths, FX ; panoramique large et VU-mètres haute précision.",
        slots=(SlotSpec(ROLE_MASTER, "Master", KIND_VIRTUAL),
               SlotSpec(ROLE_DRUMS, "Bus Batterie", KIND_VIRTUAL),
               SlotSpec(ROLE_SYNTHS, "Synths", KIND_VIRTUAL),
               SlotSpec(ROLE_FX, "FX", KIND_VIRTUAL),
               SlotSpec(ROLE_MIC, "Micro", KIND_PHYSICAL)),
        groups=(GroupSpec("MASTER", (ROLE_MASTER,)),
                GroupSpec("BUS", (ROLE_DRUMS, ROLE_SYNTHS, ROLE_FX)),
                GroupSpec("ENTRÉE", (ROLE_MIC,))),
        style=StripStyle(pan=PAN_WIDE, vu=VU_PRECISION),
        master_side="left", show_matrix=False, hide_unassigned=True),
    STREAMING: LayoutPreset(
        STREAMING, "Streaming / Broadcast",
        "Micro, Game, Discord / Vocal, Music : l'essentiel d'un direct.",
        slots=(SlotSpec(ROLE_MIC, "Micro", KIND_PHYSICAL),
               SlotSpec(ROLE_GAME, "Game", KIND_VIRTUAL),
               SlotSpec(ROLE_DISCORD, "Discord / Vocal", KIND_VIRTUAL),
               SlotSpec(ROLE_MUSIC, "Music", KIND_VIRTUAL)),
        groups=(GroupSpec("STREAMING / BROADCAST",
                          (ROLE_MIC, ROLE_GAME, ROLE_DISCORD, ROLE_MUSIC)),),
        hide_unassigned=True),
    PODCAST: LayoutPreset(
        PODCAST, "Podcasting / Voix",
        "Voix claire : égaliseur rapide, Compresseur et Gate intégrés à chaque canal.",
        slots=(SlotSpec(ROLE_VOICE, "Voix", KIND_PHYSICAL),
               SlotSpec(ROLE_GUEST, "Invité / Appel", KIND_VIRTUAL),
               SlotSpec(ROLE_MUSIC, "Musique / Jingles", KIND_VIRTUAL)),
        groups=(GroupSpec("PODCASTING / VOIX", (ROLE_VOICE, ROLE_GUEST, ROLE_MUSIC)),),
        style=StripStyle(pan=PAN_NONE, dynamics=True, eq=True),
        show_matrix=False, hide_unassigned=True),
}
PRESET_ORDER = (STANDARD, FL_STUDIO, STREAMING, PODCAST)


def layout_choices() -> list[tuple[str, str]]:
    """(clé, libellé) pour les sélecteurs : « Auto » puis chaque disposition."""
    return [(AUTO, "Auto (selon le logiciel)")] + [(k, LAYOUT_PRESETS[k].name) for k in PRESET_ORDER]


# --------------------------------------------------------------------------- #
# Plan d'arrangement
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StripInfo:
    """Un canal de la console, tel que le plan le voit."""

    index: int
    physical: bool
    name: str = ""
    role: str | None = None     # rôle déclaré (profil importé) : prioritaire sur l'automatique
    fixed: bool = False         # rôle définitif (profil) : le canal n'est jamais affecté automatiquement
    caps: EngineCaps | None = None      # ce que sait faire CE canal (sinon : le moteur entier)


@dataclass(frozen=True)
class PlannedStrip:
    index: int
    role: str | None = None
    label: str | None = None    # libellé de l'emplacement, affiché sur la tranche
    style: StripStyle = StripStyle()
    visible: bool = True


@dataclass(frozen=True)
class PlannedGroup:
    title: str
    strips: tuple[int, ...]


@dataclass(frozen=True)
class LayoutPlan:
    preset: str
    groups: tuple[PlannedGroup, ...]
    strips: tuple[PlannedStrip, ...]        # un par canal, dans l'ordre des index
    master_side: str = "right"
    show_matrix: bool = True
    show_outputs: bool = True

    def strip(self, index: int) -> PlannedStrip:
        for planned in self.strips:
            if planned.index == index:
                return planned
        raise KeyError(index)

    @property
    def order(self) -> tuple[int, ...]:
        """Canaux affichés, de gauche à droite."""
        return tuple(i for group in self.groups for i in group.strips)

    @property
    def hidden(self) -> tuple[int, ...]:
        return tuple(s.index for s in self.strips if not s.visible)

    @property
    def roles(self) -> dict[str, int]:
        return {s.role: s.index for s in self.strips if s.role}


def _kind_matches(kind: str, physical: bool) -> bool:
    return kind == KIND_ANY or (kind == KIND_PHYSICAL) == physical


def assign_roles(slots: Sequence[SlotSpec], strips: Sequence[StripInfo],
                 explicit: Mapping[str, int] | None = None) -> dict[str, int]:
    """Affecte les canaux aux emplacements : {rôle: index de canal}.

    Ordre de priorité : 1. choix manuels de l'utilisateur et rôles déclarés par un
    profil ; 2. un canal du type préféré (matériel / virtuel), dans l'ordre ;
    3. à défaut, n'importe quel canal libre (le type n'est qu'une préférence).
    Les canaux « fixed » (rôle définitif d'un profil) ne passent jamais par 2 et 3."""
    free = [s.index for s in strips]
    physical = {s.index: s.physical for s in strips}
    fixed = {s.index for s in strips if s.fixed}
    assigned: dict[str, int] = {}

    def take(role: str, index: int) -> None:
        assigned[role] = index
        free.remove(index)

    def automatic() -> list[int]:
        return [i for i in free if i not in fixed]

    declared: dict[str, int] = {}
    for info in strips:
        if info.role:
            declared.setdefault(info.role, info.index)      # le premier canal qui le déclare
    for slot in slots:
        index = (explicit or {}).get(slot.role)
        if index is None or index not in free:
            index = declared.get(slot.role)
        if index is not None and index in free and slot.role not in assigned:
            take(slot.role, index)
    for slot in slots:
        if slot.role in assigned:
            continue
        match = next((i for i in automatic() if _kind_matches(slot.kind, physical[i])), None)
        if match is not None:
            take(slot.role, match)
    for slot in slots:
        left = automatic()
        if slot.role not in assigned and left:
            take(slot.role, left[0])
    return assigned


def build_plan(preset: LayoutPreset, strips: Sequence[StripInfo], *,
               explicit_roles: Mapping[str, int] | None = None,
               show_unassigned: bool = False, caps: EngineCaps = FULL_CAPS,
               titles: tuple[str, str] = (GROUP_HARDWARE, GROUP_VIRTUAL)) -> LayoutPlan:
    """Plan d'une disposition pour les canaux donnés.

    `caps` : ce que sait faire le moteur (sans routage, comme en mode autonome,
    la matrice et le routage rapide sont masqués). `titles` : titres des deux
    groupes de la disposition standard (entrées matérielles, entrées virtuelles)."""
    show_matrix = preset.show_matrix and caps.routing

    def style(info: StripInfo) -> StripStyle:
        return style_for(preset.style, info.caps if info.caps is not None else caps)

    if not preset.slots:                                  # disposition d'origine
        hardware = tuple(s.index for s in strips if s.physical)
        virtual = tuple(s.index for s in strips if not s.physical)
        standard_groups = tuple(PlannedGroup(title, items) for title, items in
                                ((titles[0], hardware), (titles[1], virtual)) if items)
        planned = tuple(PlannedStrip(s.index, style=style(s)) for s in strips)
        return LayoutPlan(preset.key, standard_groups, planned, preset.master_side, show_matrix,
                          preset.show_outputs)

    roles = assign_roles(preset.slots, strips, explicit_roles)
    by_index = {index: role for role, index in roles.items()}
    unassigned = [s.index for s in strips if s.index not in by_index]
    groups: list[PlannedGroup] = []
    for group in preset.groups:
        members = tuple(roles[r] for r in group.roles if r in roles)
        if members:
            groups.append(PlannedGroup(group.title, members))
    keep_others = bool(unassigned) and (show_unassigned or not preset.hide_unassigned)
    if keep_others:
        groups.append(PlannedGroup(GROUP_OTHERS, tuple(unassigned)))
    planned_strips = []
    for info in strips:
        role = by_index.get(info.index)
        slot = preset.slot(role) if role else None
        visible = role is not None or keep_others
        planned_strips.append(PlannedStrip(info.index, role, slot.label if slot else None,
                                           style(info), visible))
    return LayoutPlan(preset.key, tuple(groups), tuple(planned_strips), preset.master_side,
                      show_matrix, preset.show_outputs)


# --------------------------------------------------------------------------- #
# Gestionnaire (config → choix effectifs)
# --------------------------------------------------------------------------- #
@dataclass
class LayoutManager:
    """Lit et écrit les choix d'apparence dans la config (sections `ui` et `layout`).

    Ne touche à aucun widget : la fenêtre principale lui demande le thème et le
    plan à appliquer, et l'informe des changements (logiciel actif, mode musical)."""

    cfg: MutableMapping[str, Any]
    mode: str = MODE_VOICEMEETER
    music_mode: bool = False
    active: ActiveApp = field(default_factory=lambda: NO_APP)

    # ------------------------------------------------------------- config
    def _section(self) -> MutableMapping[str, Any]:
        return self.cfg.setdefault("layout", {})

    @property
    def stored_preset(self) -> str:
        key = str(self._section().get("preset") or DEFAULT_PRESET)
        return key if key in LAYOUT_PRESETS else DEFAULT_PRESET

    @property
    def follow_software(self) -> bool:
        """Suivre le logiciel actif : réglage explicite, sinon oui en mode B (la
        console se réorganise d'elle-même), non en mode A."""
        value = self._section().get("follow_software")
        if isinstance(value, bool):
            return value
        return self.mode == MODE_STANDALONE

    @property
    def show_unassigned(self) -> bool:
        """Garder visibles les canaux sans emplacement : par défaut oui en mode B (la
        liste des canaux vient d'un profil, rien n'est à masquer), non en mode A."""
        if self.music_mode and self.active.has_music_production:
            return False        # le mode musical libère ce qui n'a pas sa place dans la disposition studio
        value = self._section().get("show_unassigned")
        if isinstance(value, bool):
            return value
        return self.mode == MODE_STANDALONE

    def set_follow_software(self, follow: bool) -> None:
        self._section()["follow_software"] = bool(follow)

    def set_show_unassigned(self, show: bool) -> None:
        self._section()["show_unassigned"] = bool(show)

    def select(self, choice: str) -> str:
        """Choix du sélecteur de disposition : « auto » (suivre le logiciel) ou une
        disposition (qui désactive le suivi). Renvoie la clé retenue."""
        if choice == AUTO:
            self.set_follow_software(True)
            return AUTO
        if choice not in LAYOUT_PRESETS:
            log.warning("Disposition inconnue « %s » ignorée.", choice)
            return self.selector_value
        self._section()["preset"] = choice
        self.set_follow_software(False)
        return choice

    @property
    def selector_value(self) -> str:
        """Ce que le sélecteur doit afficher : « auto » ou la disposition choisie."""
        return AUTO if self.follow_software else self.stored_preset

    # --------------------------------------------------------- disposition
    def effective_preset(self) -> str:
        """Disposition réellement appliquée (voir le docstring du module)."""
        if self.music_mode and self.active.has_music_production:
            return FL_STUDIO
        if self.follow_software:
            suggested = self.active.suggested_layout
            if suggested in LAYOUT_PRESETS:
                return suggested
        return self.stored_preset

    def explicit_roles(self, preset: str | None = None) -> dict[str, int]:
        roles = self._section().get("roles", {}).get(preset or self.effective_preset(), {})
        result = {}
        for role, index in (roles.items() if isinstance(roles, dict) else ()):
            try:
                result[str(role)] = int(index)
            except (TypeError, ValueError):
                continue
        return result

    def set_role(self, preset: str, role: str, index: int | None) -> None:
        """Affectation manuelle d'un emplacement à un canal (None : automatique)."""
        store = self._section().setdefault("roles", {}).setdefault(preset, {})
        if index is None:
            store.pop(role, None)
        else:
            store[role] = int(index)

    def plan(self, strips: Sequence[StripInfo], caps: EngineCaps = FULL_CAPS) -> LayoutPlan:
        key = self.effective_preset()
        titles = ((GROUP_INPUTS, GROUP_APPS) if self.mode == MODE_STANDALONE
                  else (GROUP_HARDWARE, GROUP_VIRTUAL))
        return build_plan(LAYOUT_PRESETS[key], strips, explicit_roles=self.explicit_roles(key),
                          show_unassigned=self.show_unassigned, caps=caps, titles=titles)

    # --------------------------------------------------------------- thème
    @property
    def theme(self) -> str:
        return str(self.cfg.setdefault("ui", {}).get("theme") or "")

    @property
    def global_theme(self) -> themes.ThemeSpec | None:
        return themes.global_theme(self.theme)

    def set_theme(self, key: str) -> None:
        self.cfg.setdefault("ui", {})["theme"] = key

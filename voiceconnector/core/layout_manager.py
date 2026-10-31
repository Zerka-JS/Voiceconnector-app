"""
Disposition de la console et apparence (thème).

La console n'a plus de choix de « layout » : une seule interface, celle de Reka (tranches
sombres arrondies sur une seule ligne, colonne Master à droite avec le lecteur de musique).
Reste un seul changement de disposition, automatique : le MODE MUSICAL.

Tout est ici de la logique pure (aucun Qt) : l'interface lit le résultat et l'applique.

1. Deux dispositions INTERNES (LayoutPreset) :
   - standard  : toutes les tranches, entrées matérielles d'abord ;
   - fl_studio : l'arrangement studio du mode musical : Master, Bus Batterie, Synths, FX,
                 Micro ; panoramique large et VU-mètres haute précision ; les canaux qui
                 n'ont pas leur place dans l'arrangement sont masqués (et leur traitement
                 voix libéré, voir session_mode.music_release).

   Une disposition décrit des emplacements (SlotSpec : un rôle et un libellé).
   LayoutManager.plan() affecte chaque canal de la console à un emplacement (selon son type,
   matériel ou virtuel, et les choix de l'utilisateur) et produit un LayoutPlan : ordre des
   tranches, canaux masqués, style de chaque tranche. L'interface réarrange les tranches
   déjà construites d'après ce plan.

2. Le choix effectif (LayoutManager.effective_preset) :
   - mode musical actif avec un logiciel de production → fl_studio ;
   - sinon standard. Rien n'est écrasé : c'est un calcul, la sortie du mode musical rend
     donc la console telle qu'elle était.

Config (section `layout`) :
    roles            {disposition: {rôle: index de canal}} : affectations manuelles de
                     l'arrangement studio (Réglages → Mode musical)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Sequence

from core import themes
from core.app_detector import NO_APP, ActiveApp

log = logging.getLogger(__name__)

# Dispositions internes
STANDARD = "standard"
FL_STUDIO = "fl_studio"

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

PAN_PAD, PAN_WIDE, PAN_NONE = "pad", "wide", "none"
VU_NORMAL, VU_PRECISION = "normal", "precision"


@dataclass(frozen=True)
class StripStyle:
    """Ce qu'une tranche montre et comment elle se présente."""

    pan: str = PAN_PAD          # « pad » : Intellipan carré ; « wide » : curseur large ; « none »
    vu: str = VU_NORMAL         # « normal » ou « precision » (haute précision)
    dynamics: bool = True       # potards Compr. + Gate
    fx: bool = True             # zone d'effets
    routing: bool = True        # envois vers les bus (A1…B3)

    def __post_init__(self) -> None:
        if self.pan not in (PAN_PAD, PAN_WIDE, PAN_NONE):
            raise ValueError(f"pan inconnu : {self.pan}")
        if self.vu not in (VU_NORMAL, VU_PRECISION):
            raise ValueError(f"vu inconnu : {self.vu}")


@dataclass(frozen=True)
class EngineCaps:
    """Ce que le moteur audio sait faire : une tranche ne montre que des commandes qui agissent
    vraiment (le mode autonome n'a ni routage, ni compresseur, ni gate)."""

    routing: bool = True        # bus A / B : envois par canal
    pan: bool = True            # panoramique par canal
    dynamics: bool = True       # compresseur et gate par canal
    fx: bool = True             # chaîne d'effets (REAPER, FL Studio, Equalizer APO)

    def __and__(self, other: EngineCaps) -> EngineCaps:
        """Ce que deux moteurs savent faire ensemble (canaux de sources différentes)."""
        return EngineCaps(self.routing and other.routing, self.pan and other.pan,
                          self.dynamics and other.dynamics, self.fx and other.fx)


FULL_CAPS = EngineCaps()
# Mode autonome : sessions audio Windows par application (volume, muet, niveau) …
APP_SESSION_CAPS = EngineCaps(routing=False, pan=False, dynamics=False, fx=False)
# … ou pistes du mixer de FL Studio (volume, muet, solo, panoramique)
FL_TRACK_CAPS = EngineCaps(routing=False, pan=True, dynamics=False, fx=False)


def style_for(style: StripStyle, caps: EngineCaps) -> StripStyle:
    """Le style de la disposition, restreint à ce que le moteur sait faire."""
    return StripStyle(
        pan=style.pan if caps.pan else PAN_NONE,
        vu=style.vu,
        dynamics=style.dynamics and caps.dynamics,
        fx=style.fx and caps.fx,
        routing=style.routing and caps.routing)


@dataclass(frozen=True)
class SlotSpec:
    role: str
    label: str                  # libellé affiché sur la tranche (« Bus Batterie »)
    kind: str = KIND_ANY        # type de canal préféré (physical, virtual, any)


@dataclass(frozen=True)
class LayoutPreset:
    key: str
    name: str
    description: str
    slots: tuple[SlotSpec, ...] = ()        # vide : toutes les tranches, matériel d'abord
    style: StripStyle = StripStyle()

    def slot(self, role: str) -> SlotSpec | None:
        return next((s for s in self.slots if s.role == role), None)


LAYOUT_PRESETS: dict[str, LayoutPreset] = {
    STANDARD: LayoutPreset(
        STANDARD, "Console",
        "Toutes les tranches sur une ligne, les entrées matérielles d'abord."),
    FL_STUDIO: LayoutPreset(
        FL_STUDIO, "Studio (mode musical)",
        "Master, Bus Batterie, Synths, FX, Micro ; panoramique large et VU-mètres haute précision.",
        slots=(SlotSpec(ROLE_MASTER, "Master", KIND_VIRTUAL),
               SlotSpec(ROLE_DRUMS, "Bus Batterie", KIND_VIRTUAL),
               SlotSpec(ROLE_SYNTHS, "Synths", KIND_VIRTUAL),
               SlotSpec(ROLE_FX, "FX", KIND_VIRTUAL),
               SlotSpec(ROLE_MIC, "Micro", KIND_PHYSICAL)),
        style=StripStyle(pan=PAN_WIDE, vu=VU_PRECISION)),
}


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
class LayoutPlan:
    preset: str
    strips: tuple[PlannedStrip, ...]        # un par canal, dans l'ordre des index
    order: tuple[int, ...]                  # canaux affichés, de gauche à droite

    def strip(self, index: int) -> PlannedStrip:
        for planned in self.strips:
            if planned.index == index:
                return planned
        raise KeyError(index)

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
               caps: EngineCaps = FULL_CAPS) -> LayoutPlan:
    """Plan d'une disposition pour les canaux donnés.

    `caps` : ce que sait faire le moteur (sans routage, comme en mode autonome, les envois
    par bus sont masqués)."""

    def style(info: StripInfo) -> StripStyle:
        return style_for(preset.style, info.caps if info.caps is not None else caps)

    if not preset.slots:                                  # console : tout, matériel d'abord
        order = tuple(s.index for s in strips if s.physical) + tuple(s.index for s in strips if not s.physical)
        planned = tuple(PlannedStrip(s.index, style=style(s)) for s in strips)
        return LayoutPlan(preset.key, planned, order)

    roles = assign_roles(preset.slots, strips, explicit_roles)
    by_index = {index: role for role, index in roles.items()}
    order = tuple(roles[slot.role] for slot in preset.slots if slot.role in roles)
    planned_strips = []
    for info in strips:
        role = by_index.get(info.index)
        slot = preset.slot(role) if role else None
        planned_strips.append(PlannedStrip(info.index, role, slot.label if slot else None,
                                           style(info), role is not None))
    return LayoutPlan(preset.key, tuple(planned_strips), order)


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

    # --------------------------------------------------------- disposition
    def effective_preset(self) -> str:
        """Disposition réellement appliquée (voir le docstring du module)."""
        if self.music_mode and self.active.has_music_production:
            return FL_STUDIO
        return STANDARD

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
        return build_plan(LAYOUT_PRESETS[key], strips, explicit_roles=self.explicit_roles(key), caps=caps)

    # --------------------------------------------------------------- thème
    @property
    def theme(self) -> str:
        return str(self.cfg.setdefault("ui", {}).get("theme") or "")

    @property
    def global_theme(self) -> themes.ThemeSpec | None:
        return themes.global_theme(self.theme)

    def set_theme(self, key: str) -> None:
        self.cfg.setdefault("ui", {})["theme"] = key

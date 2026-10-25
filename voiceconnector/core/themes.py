"""
Thèmes globaux de l'interface (données pures, sans Qt).

Un thème global habille toute la console : couleurs de l'interface, peau des
tranches (strips), formes (rayons des coins) et arrière-plan. Les quatre
thèmes proposés dans Réglages → « Layout et Apparence » :

- retro      : Rétro — écran VFD, néons violets, grille « Neon Wave Synth » ;
- cyberpunk  : Futuriste — HUD sombre, cyan électrique et jaune néon ;
- flat       : Épuré — minimaliste, clair, aplats nets (Flat Modern) ;
- slate      : Studio Pro — gris ardoise de studio, accent bleu (Dark Slate).

Ils s'ajoutent aux thèmes de couleur d'accent historiques (ui.theme : vintage,
studio, neon, cyan… ou « #rrggbb »), qui gardent leur peau crème. Le choix est
mémorisé dans la même clé `ui.theme`.

Ce module ne fait que décrire les thèmes et les vérifier (lisibilité WCAG) ;
c'est `ui/styles.py` qui les applique aux variables que lisent les widgets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

# Couleurs d'interface que tout thème fournit en entier (mêmes noms que ui/styles.py)
PALETTE_KEYS = (
    "BG_PANEL", "BG_PILL", "GREEN_NEON", "GREEN_BRIGHT", "GREEN_DIM", "GREEN_TEXT",
    "GREEN_LABEL", "GREEN_SCALE", "BG_WINDOW", "BG_GLOW", "BG_COLUMN", "BG_SECTION",
    "BORDER_COLUMN", "BORDER_SECTION", "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED",
    "BUTTON_BG", "BUTTON_HOVER", "PILL_BG", "PILL_BORDER", "KNOB_CAP_TOP",
    "KNOB_CAP_BOTTOM", "BG_DEEP", "ON_ACCENT", "ON_ACCENT_DARK", "BADGE_OFF",
)
# Peau des tranches (crème d'origine) : fond, filets, touches, encre, bois, châssis
SKIN_KEYS = (
    "CREAM", "CREAM_DARK", "CREAM_EDGE", "CREAM_KEY", "INK", "INK_SOFT",
    "WOOD_LIGHT", "WOOD_DARK", "CHASSIS_TOP", "CHASSIS_BOTTOM",
    "GROUP_BORDER", "BAY_BG", "TRANSPORT_TOP", "TRANSPORT_BOTTOM",
)
# Autres couleurs que le thème peut remplacer (texte de sélection, VU, boutons d'état)
EXTRA_KEYS = (
    "SELECTION_TEXT", "VU_GREEN", "VU_YELLOW", "VU_RED",
    "MUTE_ON", "MUTE_ON_HOVER", "SOLO_ON", "SOLO_ON_HOVER", "TEXT_ON_SOLO",
)
# Rayons des coins (pixels) : colonnes, blocs internes, boutons, champs de saisie
SHAPE_KEYS = ("R_COLUMN", "R_SECTION", "R_BUTTON", "R_INPUT")

DEFAULT_SHAPE = {"R_COLUMN": 14, "R_SECTION": 10, "R_BUTTON": 7, "R_INPUT": 6}

BACKDROPS = ("synthwave", "cyber", "flat", "slate")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_RGBA = re.compile(r"^rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*(0|1|0?\.\d+)\s*\)$")


@dataclass(frozen=True)
class ThemeSpec:
    key: str
    name: str                              # nom affiché
    tagline: str                           # une phrase pour la carte de choix
    dark: bool
    accent: str                            # couleur d'accent (pastille, aperçu)
    palette: Mapping[str, str]             # toutes les PALETTE_KEYS
    skin: Mapping[str, str]                # SKIN_KEYS
    extras: Mapping[str, str] = field(default_factory=dict)
    shape: Mapping[str, int] = field(default_factory=dict)
    backdrop: str = "slate"
    suggested_layout: str | None = None    # disposition qui lui va bien (indication)

    def values(self) -> dict[str, str | int]:
        """Toutes les variables de style que ce thème impose."""
        merged: dict[str, str | int] = {**DEFAULT_SHAPE}
        merged.update(self.palette)
        merged.update(self.skin)
        merged.update(self.extras)
        merged.update(self.shape)
        return merged

    def preview_colors(self) -> dict[str, str]:
        """Quelques couleurs pour dessiner la carte d'aperçu sans appliquer le thème."""
        return {"window": self.palette["BG_WINDOW"], "column": self.palette["BG_COLUMN"],
                "strip": self.skin["CREAM"], "ink": self.skin["INK"],
                "accent": self.accent, "text": self.palette["TEXT_PRIMARY"],
                "border": self.palette["BORDER_COLUMN"]}


# --------------------------------------------------------------------------- #
# Les quatre thèmes
# --------------------------------------------------------------------------- #
RETRO = ThemeSpec(
    key="retro", name="Rétro — VFD / Neon Wave Synth",
    tagline="Écran VFD turquoise, néons violets et grille synthwave.",
    dark=True, accent="#33f0d8", backdrop="synthwave", suggested_layout="streaming",
    palette={
        "BG_WINDOW": "#0a0618", "BG_GLOW": "#2a0f4d", "BG_COLUMN": "#130a29",
        "BG_SECTION": "#0d071f", "BORDER_COLUMN": "#3b2272", "BORDER_SECTION": "#2a1952",
        "BG_PANEL": "#130a29", "BG_PILL": "#1d1038", "PILL_BG": "#1d1038",
        "PILL_BORDER": "#3b2272", "BG_DEEP": "#070312",
        "GREEN_NEON": "#33f0d8", "GREEN_TEXT": "#33f0d8", "GREEN_BRIGHT": "#8bffee",
        "GREEN_DIM": "#1d5f66", "GREEN_LABEL": "#7fb8bd", "GREEN_SCALE": "#2f4f66",
        "TEXT_PRIMARY": "#e6fff9", "TEXT_SECONDARY": "#9fd8d0", "TEXT_MUTED": "#64868f",
        "BUTTON_BG": "#221342", "BUTTON_HOVER": "#2e1a58",
        "KNOB_CAP_TOP": "#4a3a78", "KNOB_CAP_BOTTOM": "#1a1030",
        "ON_ACCENT": "#04181a", "ON_ACCENT_DARK": "#04181a", "BADGE_OFF": "#120a26",
    },
    skin={
        "CREAM": "#1c1136", "CREAM_DARK": "#150c2b", "CREAM_EDGE": "#3b2472",
        "CREAM_KEY": "#2b1a52", "INK": "#7ff7e3", "INK_SOFT": "#5fb9ae",
        "WOOD_LIGHT": "#3b2472", "WOOD_DARK": "#241547",
        "CHASSIS_TOP": "#1a0f33", "CHASSIS_BOTTOM": "#0a0618",
        "GROUP_BORDER": "#4a2a8a", "BAY_BG": "rgba(255, 60, 172, 0.07)",
        "TRANSPORT_TOP": "#3a2468", "TRANSPORT_BOTTOM": "#1f1240",
    },
    extras={
        "SELECTION_TEXT": "#ffffff", "VU_GREEN": "#2ef2d0", "VU_YELLOW": "#ffd23f",
        "VU_RED": "#ff3d8b", "MUTE_ON": "#d6246e", "MUTE_ON_HOVER": "#f0388a",
        "SOLO_ON": "#ffb02e", "SOLO_ON_HOVER": "#ffc65c", "TEXT_ON_SOLO": "#1c1203",
    },
    shape={"R_COLUMN": 6, "R_SECTION": 4, "R_BUTTON": 4, "R_INPUT": 4},
)

CYBERPUNK = ThemeSpec(
    key="cyberpunk", name="Futuriste — Cyberpunk",
    tagline="Interface HUD sombre, cyan électrique, accents jaune néon.",
    dark=True, accent="#00e5ff", backdrop="cyber", suggested_layout="streaming",
    palette={
        "BG_WINDOW": "#04060d", "BG_GLOW": "#0b1f3a", "BG_COLUMN": "#0a1020",
        "BG_SECTION": "#070b16", "BORDER_COLUMN": "#1c3a52", "BORDER_SECTION": "#142c40",
        "BG_PANEL": "#0a1020", "BG_PILL": "#0f182b", "PILL_BG": "#0f182b",
        "PILL_BORDER": "#1f4560", "BG_DEEP": "#02040a",
        "GREEN_NEON": "#00e5ff", "GREEN_TEXT": "#00e5ff", "GREEN_BRIGHT": "#7df3ff",
        "GREEN_DIM": "#0b4a5c", "GREEN_LABEL": "#6fa8bd", "GREEN_SCALE": "#1d4458",
        "TEXT_PRIMARY": "#e8fbff", "TEXT_SECONDARY": "#8fb8c7", "TEXT_MUTED": "#5d7d8c",
        "BUTTON_BG": "#111b30", "BUTTON_HOVER": "#18284a",
        "KNOB_CAP_TOP": "#3c4a5e", "KNOB_CAP_BOTTOM": "#121824",
        "ON_ACCENT": "#001318", "ON_ACCENT_DARK": "#001318", "BADGE_OFF": "#0a1222",
    },
    skin={
        "CREAM": "#0e1627", "CREAM_DARK": "#09101e", "CREAM_EDGE": "#1f4a66",
        "CREAM_KEY": "#152540", "INK": "#c9f4ff", "INK_SOFT": "#6fa8bd",
        "WOOD_LIGHT": "#1a3550", "WOOD_DARK": "#102338",
        "CHASSIS_TOP": "#0b1426", "CHASSIS_BOTTOM": "#04060d",
        "GROUP_BORDER": "#0f6d80", "BAY_BG": "rgba(0, 229, 255, 0.05)",
        "TRANSPORT_TOP": "#233049", "TRANSPORT_BOTTOM": "#101828",
    },
    extras={
        "SELECTION_TEXT": "#ffffff", "VU_GREEN": "#00e5ff", "VU_YELLOW": "#fcee0a",
        "VU_RED": "#ff2a6d", "MUTE_ON": "#c8003a", "MUTE_ON_HOVER": "#ff1f5a",
        "SOLO_ON": "#fcee0a", "SOLO_ON_HOVER": "#fff75a", "TEXT_ON_SOLO": "#1a1700",
    },
    shape={"R_COLUMN": 2, "R_SECTION": 2, "R_BUTTON": 2, "R_INPUT": 2},
)

FLAT = ThemeSpec(
    key="flat", name="Épuré — Minimaliste (Flat Modern)",
    tagline="Clair, aplats nets, coins doux, aucun ornement.",
    dark=False, accent="#2563eb", backdrop="flat", suggested_layout="podcast",
    palette={
        "BG_WINDOW": "#eef1f5", "BG_GLOW": "#ffffff", "BG_COLUMN": "#ffffff",
        "BG_SECTION": "#f4f6f9", "BORDER_COLUMN": "#d9e0ea", "BORDER_SECTION": "#e6ebf2",
        "BG_PANEL": "#ffffff", "BG_PILL": "#eef2f7", "PILL_BG": "#eef2f7",
        "PILL_BORDER": "#d7dee8", "BG_DEEP": "#e8edf4",
        "GREEN_NEON": "#2563eb", "GREEN_TEXT": "#2563eb", "GREEN_BRIGHT": "#1d4ed8",
        "GREEN_DIM": "#c7d8fb", "GREEN_LABEL": "#64748b", "GREEN_SCALE": "#c0c9d6",
        "TEXT_PRIMARY": "#111827", "TEXT_SECONDARY": "#4b5563", "TEXT_MUTED": "#6b7280",
        "BUTTON_BG": "#f1f4f8", "BUTTON_HOVER": "#e4e9f1",
        "KNOB_CAP_TOP": "#f9fafb", "KNOB_CAP_BOTTOM": "#cfd6e0",
        "ON_ACCENT": "#ffffff", "ON_ACCENT_DARK": "#ffffff", "BADGE_OFF": "#e9edf3",
    },
    skin={
        "CREAM": "#ffffff", "CREAM_DARK": "#ffffff", "CREAM_EDGE": "#e3e8ef",
        "CREAM_KEY": "#f3f5f9", "INK": "#111827", "INK_SOFT": "#6b7280",
        "WOOD_LIGHT": "#dfe5ee", "WOOD_DARK": "#d3dae5",
        "CHASSIS_TOP": "#eef1f5", "CHASSIS_BOTTOM": "#e6eaf0",
        "GROUP_BORDER": "#d5dce6", "BAY_BG": "rgba(15, 23, 42, 0.04)",
        "TRANSPORT_TOP": "#f6f8fb", "TRANSPORT_BOTTOM": "#dfe5ee",
    },
    extras={
        "SELECTION_TEXT": "#0f172a", "VU_GREEN": "#16a34a", "VU_YELLOW": "#f59e0b",
        "VU_RED": "#dc2626", "MUTE_ON": "#dc2626", "MUTE_ON_HOVER": "#b91c1c",
        "SOLO_ON": "#f59e0b", "SOLO_ON_HOVER": "#d97706", "TEXT_ON_SOLO": "#1c1203",
    },
    shape={"R_COLUMN": 12, "R_SECTION": 10, "R_BUTTON": 8, "R_INPUT": 8},
)

SLATE = ThemeSpec(
    key="slate", name="Studio Pro — Dark Slate",
    tagline="Gris ardoise de studio, accent bleu, façon station audio.",
    dark=True, accent="#4aa8ff", backdrop="slate", suggested_layout="fl_studio",
    palette={
        "BG_WINDOW": "#0e1521", "BG_GLOW": "#16233a", "BG_COLUMN": "#162030",
        "BG_SECTION": "#101826", "BORDER_COLUMN": "#2d3d57", "BORDER_SECTION": "#223149",
        "BG_PANEL": "#162030", "BG_PILL": "#1b2638", "PILL_BG": "#1b2638",
        "PILL_BORDER": "#2d3d57", "BG_DEEP": "#0a101a",
        "GREEN_NEON": "#4aa8ff", "GREEN_TEXT": "#4aa8ff", "GREEN_BRIGHT": "#8cc8ff",
        "GREEN_DIM": "#1f4a7a", "GREEN_LABEL": "#8fa3bf", "GREEN_SCALE": "#33445f",
        "TEXT_PRIMARY": "#f1f5f9", "TEXT_SECONDARY": "#9fb0c8", "TEXT_MUTED": "#66778f",
        "BUTTON_BG": "#1f2b3f", "BUTTON_HOVER": "#293851",
        "KNOB_CAP_TOP": "#46556e", "KNOB_CAP_BOTTOM": "#18202e",
        "ON_ACCENT": "#071320", "ON_ACCENT_DARK": "#071320", "BADGE_OFF": "#131c2b",
    },
    skin={
        "CREAM": "#1b2638", "CREAM_DARK": "#162030", "CREAM_EDGE": "#2d3d57",
        "CREAM_KEY": "#24324a", "INK": "#e6edf7", "INK_SOFT": "#8fa3bf",
        "WOOD_LIGHT": "#2d3d57", "WOOD_DARK": "#1f2c42",
        "CHASSIS_TOP": "#162030", "CHASSIS_BOTTOM": "#0e1521",
        "GROUP_BORDER": "#0a101a", "BAY_BG": "rgba(148, 163, 184, 0.06)",
        "TRANSPORT_TOP": "#34425c", "TRANSPORT_BOTTOM": "#1c2638",
    },
    extras={
        "SELECTION_TEXT": "#ffffff", "VU_GREEN": "#22c55e", "VU_YELLOW": "#facc15",
        "VU_RED": "#ef4444", "MUTE_ON": "#b91c1c", "MUTE_ON_HOVER": "#dc2626",
        "SOLO_ON": "#f59e0b", "SOLO_ON_HOVER": "#fbbf24", "TEXT_ON_SOLO": "#1c1203",
    },
    shape={"R_COLUMN": 8, "R_SECTION": 6, "R_BUTTON": 6, "R_INPUT": 6},
)

GLOBAL_THEMES: dict[str, ThemeSpec] = {t.key: t for t in (RETRO, CYBERPUNK, FLAT, SLATE)}
GLOBAL_THEME_ORDER = tuple(GLOBAL_THEMES)


def global_theme(key: str | None) -> ThemeSpec | None:
    """Le thème global de cette clé, ou None (thème d'accent, couleur « #rrggbb »…)."""
    return GLOBAL_THEMES.get(str(key or ""))


# --------------------------------------------------------------------------- #
# Lisibilité
# --------------------------------------------------------------------------- #
def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def luminance(color: str) -> float:
    """Luminance relative WCAG d'une couleur « #rrggbb »."""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# (premier plan, fond, contraste minimal) : les paires que l'interface dessine vraiment
CONTRAST_RULES: tuple[tuple[str, str, float], ...] = (
    ("TEXT_PRIMARY", "BG_WINDOW", 7.0), ("TEXT_PRIMARY", "BG_COLUMN", 7.0),
    ("TEXT_PRIMARY", "BG_SECTION", 7.0), ("TEXT_PRIMARY", "BUTTON_BG", 7.0),
    ("TEXT_PRIMARY", "BUTTON_HOVER", 7.0), ("TEXT_PRIMARY", "PILL_BG", 7.0),
    ("TEXT_PRIMARY", "BG_DEEP", 7.0),
    ("TEXT_SECONDARY", "BG_COLUMN", 4.5), ("TEXT_SECONDARY", "BG_SECTION", 4.5),
    ("TEXT_SECONDARY", "BG_DEEP", 4.5), ("TEXT_SECONDARY", "BUTTON_BG", 4.5),
    ("TEXT_MUTED", "BG_COLUMN", 3.0), ("TEXT_MUTED", "BG_SECTION", 3.0),
    ("GREEN_NEON", "BG_COLUMN", 4.5), ("GREEN_NEON", "BG_SECTION", 4.5),
    ("GREEN_NEON", "BG_WINDOW", 4.5),
    ("ON_ACCENT_DARK", "GREEN_NEON", 4.5), ("ON_ACCENT_DARK", "GREEN_BRIGHT", 4.5),
    ("ON_ACCENT", "GREEN_NEON", 4.5),
    ("SELECTION_TEXT", "GREEN_DIM", 4.5),
    ("INK", "CREAM", 7.0), ("INK", "CREAM_DARK", 7.0), ("INK", "CREAM_KEY", 7.0),
    ("INK_SOFT", "CREAM", 4.5), ("INK_SOFT", "CREAM_DARK", 4.5),
    ("TEXT_PRIMARY", "TRANSPORT_TOP", 4.5), ("TEXT_PRIMARY", "TRANSPORT_BOTTOM", 4.5),
    ("TEXT_ON_SOLO", "SOLO_ON", 4.5), ("#ffffff", "MUTE_ON", 4.5),
)
# Fonds qui doivent se distinguer de ce qui les entoure (contraste minimal)
SEPARATION_RULES: tuple[tuple[str, str, float], ...] = (
    ("BG_COLUMN", "BG_WINDOW", 1.03), ("BORDER_COLUMN", "BG_COLUMN", 1.25),
    ("CREAM_EDGE", "CREAM", 1.1), ("BUTTON_BG", "BG_COLUMN", 1.03),
)


def check_theme(spec: ThemeSpec) -> list[str]:
    """Problèmes d'un thème : clés manquantes, couleurs invalides, textes illisibles.
    Liste vide = thème complet et lisible."""
    problems: list[str] = []
    for group, keys, source in (("palette", PALETTE_KEYS, spec.palette),
                                ("peau", SKIN_KEYS, spec.skin)):
        for key in keys:
            if key not in source:
                problems.append(f"{spec.key} : {group} sans {key}")
    unknown = (set(spec.palette) - set(PALETTE_KEYS)) | (set(spec.skin) - set(SKIN_KEYS)) \
        | (set(spec.extras) - set(EXTRA_KEYS)) | (set(spec.shape) - set(SHAPE_KEYS))
    problems.extend(f"{spec.key} : clé inconnue {key}" for key in sorted(unknown))
    if spec.backdrop not in BACKDROPS:
        problems.append(f"{spec.key} : arrière-plan inconnu {spec.backdrop}")
    values = spec.values()
    for key, value in values.items():
        if key in SHAPE_KEYS:
            if not isinstance(value, int) or not 0 <= value <= 30:
                problems.append(f"{spec.key} : rayon invalide {key}={value!r}")
        elif not (isinstance(value, str) and (_HEX.match(value) or _RGBA.match(value))):
            problems.append(f"{spec.key} : couleur invalide {key}={value!r}")
    if problems:
        return problems          # inutile de calculer des contrastes sur des valeurs invalides

    def color(name: str) -> str:
        return name if name.startswith("#") else str(values[name])

    for foreground, background, minimum in CONTRAST_RULES:
        if (foreground not in values and not foreground.startswith("#")) or background not in values:
            continue                                  # règle sans objet (extra absent du thème)
        ratio = contrast_ratio(color(foreground), color(background))
        if ratio < minimum:
            problems.append(f"{spec.key} : {foreground} sur {background} = {ratio:.2f} "
                            f"(minimum {minimum})")
    for upper, lower, minimum in SEPARATION_RULES:
        ratio = contrast_ratio(color(upper), color(lower))
        if ratio < minimum:
            problems.append(f"{spec.key} : {upper} / {lower} trop proches ({ratio:.2f} < {minimum})")
    if (luminance(color("BG_WINDOW")) > 0.4) == spec.dark:
        problems.append(f"{spec.key} : drapeau dark={spec.dark} incohérent avec le fond")
    return problems

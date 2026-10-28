"""
Palette, thèmes de couleur, polices (Inter embarquée) et feuille de style QSS.

Les widgets dessinés à la main (knob, fader, VU, pills) lisent directement
les couleurs ci-dessous (styles.GREEN_NEON…) à chaque dessin ; le reste passe
par la QSS appliquée à l'application.

Thèmes : la palette de référence est verte. Un thème donne une couleur
d'accent ; toutes les teintes vertes (accent, fonds teintés, bordures, textes
gris-vert) tournent vers sa teinte, en gardant leur luminosité. Le rouge du
mute, le jaune des VU et le blanc ne changent pas.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from core import color_scheme, config
from core.color_scheme import (  # noqa: F401 - ré-exportés : les widgets lisent styles.MASTER_SCOPE…
    ELEMENT_LABELS, MASTER_ELEMENTS, MASTER_SCOPE, STRIP_ELEMENTS)
from core.themes import DEFAULT_SHAPE, EXTRA_KEYS, SKIN_KEYS, ThemeSpec, global_theme

log = logging.getLogger(__name__)

# --- Palette de référence --------------------------------------------------
BG_BLACK = "#000000"
BG_PANEL = "#0a1510"      # Fond strip subtil
BG_PILL = "#0a1510"       # Fond des pills FX
GREEN_NEON = "#00ff88"    # Accent principal
GREEN_BRIGHT = "#00d4aa"  # Hover
GREEN_DIM = "#1a4030"     # Éléments inactifs
GREEN_TEXT = "#00ff88"
GREEN_LABEL = "#4d8870"   # Labels discrets
GREEN_SCALE = "#004d2a"   # Graduation dB
WHITE = "#ffffff"
RED_MUTE = "#ff2d2d"
YELLOW_VU = "#ffcc00"

# --- Compléments relevés sur la maquette ------------------------------------
BG_WINDOW = "#040b07"         # fond général (sous le dégradé)
BG_GLOW = "#001a0f"           # halo radial, centre-droit
BG_COLUMN = "#0e1612"         # colonnes (strips, master)
BG_SECTION = "#070e0a"        # blocs internes (effets, knobs, fader)
BORDER_COLUMN = "#16201b"
BORDER_SECTION = "#111a15"
TEXT_PRIMARY = "#e2e8e5"      # noms, valeurs
TEXT_SECONDARY = "#a3ada8"    # labels
TEXT_MUTED = "#5f6b65"
BUTTON_BG = "#1b2a24"
BUTTON_HOVER = "#223630"
PILL_BG = "#15201b"
PILL_BORDER = "#24332c"
KNOB_CAP_TOP = "#3a4642"      # dégradé du bouton des knobs
KNOB_CAP_BOTTOM = "#1c2421"
RED_DIM = "#3a1416"           # LED de saturation éteinte
TEXT_ON_RED = "#2b0707"
BG_DEEP = "#030b06"           # fonds très sombres (badge, bouton projet, Intellipan)
ON_ACCENT = "#00341c"         # encre sur l'accent (badge FX allumé)
ON_ACCENT_DARK = "#00140a"    # texte des boutons principaux
BADGE_OFF = "#0d1a14"         # badge FX éteint

# --- Palette « Dark Studio Pro » (thème par défaut, façon DAW) --------------
# Neutres gris-bleu et accent bleu studio, appliqués tels quels (sans rotation).
STUDIO_PALETTE = {
    "BG_WINDOW": "#0f1115", "BG_GLOW": "#141821", "BG_COLUMN": "#161920",
    "BG_SECTION": "#12151b", "BORDER_COLUMN": "#2a2f3d", "BORDER_SECTION": "#232836",
    "BG_PANEL": "#161920", "BG_PILL": "#1c2029", "BG_DEEP": "#0b0d11",
    "GREEN_NEON": "#3b82f6", "GREEN_TEXT": "#3b82f6", "GREEN_BRIGHT": "#60a5fa",
    "GREEN_DIM": "#1e3a68", "GREEN_LABEL": "#94a3b8", "GREEN_SCALE": "#3a4152",
    "TEXT_PRIMARY": "#f8fafc", "TEXT_SECONDARY": "#94a3b8", "TEXT_MUTED": "#5b6577",
    "BUTTON_BG": "#1f2430", "BUTTON_HOVER": "#272d3b", "PILL_BG": "#1c2029",
    "PILL_BORDER": "#2a2f3d", "KNOB_CAP_TOP": "#3a4150", "KNOB_CAP_BOTTOM": "#1b1f28",
    "ON_ACCENT": "#0b1220", "ON_ACCENT_DARK": "#0b1220", "BADGE_OFF": "#1a1e27",
}

# --- Palette « VoiceConnector » (matériel rétro / rack) -----------------------
# Parties sombres (châssis, matrice, master) : appliquées comme la palette Studio.
VINTAGE_PALETTE = {
    "BG_WINDOW": "#17181a", "BG_GLOW": "#1c1d20", "BG_COLUMN": "#1d1e21",
    "BG_SECTION": "#121315", "BORDER_COLUMN": "#303236", "BORDER_SECTION": "#26282b",
    "BG_PANEL": "#1d1e21", "BG_PILL": "#232528", "BG_DEEP": "#0c0d0e",
    "GREEN_NEON": "#3fcf5a", "GREEN_TEXT": "#3fcf5a", "GREEN_BRIGHT": "#6ee787",
    "GREEN_DIM": "#1e5a2c", "GREEN_LABEL": "#9aa39c", "GREEN_SCALE": "#3d4a40",
    "TEXT_PRIMARY": "#ece7d8", "TEXT_SECONDARY": "#a8a493", "TEXT_MUTED": "#6b6858",
    "BUTTON_BG": "#26282b", "BUTTON_HOVER": "#303338", "PILL_BG": "#232528",
    "PILL_BORDER": "#303236", "KNOB_CAP_TOP": "#4a4c50", "KNOB_CAP_BOTTOM": "#1e1f21",
    "ON_ACCENT": "#06210e", "ON_ACCENT_DARK": "#06210e", "BADGE_OFF": "#18191b",
}

# Couleurs fixes du matériel (indépendantes du thème) : tranches crème, bois, châssis
CREAM = "#e9e0c6"             # fond des tranches
CREAM_DARK = "#d2c7a4"        # bas du dégradé des tranches
CREAM_EDGE = "#a99d7c"        # filets, séparations
CREAM_KEY = "#f4ecd4"         # touches (Solo / Mute) au repos
INK = "#2a2923"               # encre sur crème (titres, graduations principales)
INK_SOFT = "#6a6350"          # encre secondaire sur crème
WOOD_LIGHT = "#dcc9a0"        # joues latérales
WOOD_DARK = "#b89f6e"
CHASSIS_TOP = "#2b2c2f"       # châssis métallique noir mat
CHASSIS_BOTTOM = "#161719"
HW_ORANGE = "#f08a24"
HW_GREEN = "#3fcf5a"
HW_BLUE = "#3b82f6"
HW_YELLOW = "#e8c32a"
HW_RED = "#e5483a"
CAP_COLORS = (HW_ORANGE, HW_BLUE, HW_GREEN, HW_YELLOW, "#d4572b", "#8b5cf6")
_BUS_COLORS = {"B1": HW_GREEN, "B2": HW_BLUE, "B3": HW_YELLOW}


def bus_color(bus: str) -> str:
    """Couleur d'un bus dans la matrice : A* orange, B1 vert, B2 bleu, B3 jaune."""
    bus = str(bus or "").upper()
    if bus in _BUS_COLORS:
        return _BUS_COLORS[bus]
    if bus.startswith("A"):
        return HW_ORANGE
    return CAP_COLORS[sum(map(ord, bus)) % len(CAP_COLORS)]


# Couleurs fixes des VU-mètres et des boutons d'état (indépendantes du thème)
VU_GREEN = "#22c55e"
VU_YELLOW = "#facc15"
VU_RED = "#ef4444"
MUTE_ON = "#b91c1c"          # rouge sombre
MUTE_ON_HOVER = "#dc2626"
SOLO_ON = "#f59e0b"          # ambre
SOLO_ON_HOVER = "#fbbf24"
TEXT_ON_SOLO = "#1c1203"

# --- Peau et formes que les thèmes globaux (core/themes.py) peuvent remplacer ----
# Les valeurs ci-dessous sont celles du matériel d'origine (tranches crème) : les
# thèmes d'accent historiques les gardent ; set_theme() les remet à chaque changement.
SELECTION_TEXT = WHITE        # texte d'une ligne sélectionnée (listes, menus)
GROUP_BORDER = BG_DEEP        # bordure des groupes de tranches (suit BG_DEEP hors thèmes globaux)
BAY_BG = "rgba(60, 50, 20, 0.08)"     # alvéole des boutons de routage
TRANSPORT_TOP = "#4a4c50"     # touches de transport (dégradé)
TRANSPORT_BOTTOM = "#26282b"
R_COLUMN = DEFAULT_SHAPE["R_COLUMN"]      # rayons des coins (px) : colonnes, blocs, boutons, champs
R_SECTION = DEFAULT_SHAPE["R_SECTION"]
R_BUTTON = DEFAULT_SHAPE["R_BUTTON"]
R_INPUT = DEFAULT_SHAPE["R_INPUT"]
R_GROUP = 6                   # groupes de tranches : dérivé de R_SECTION (voir _derive_shape)
_SKIN_BASE = {name: globals()[name] for name in SKIN_KEYS}
_EXTRA_BASE = {name: globals()[name] for name in EXTRA_KEYS}

# --- Thèmes ----------------------------------------------------------------
DEFAULT_THEME = "vintage"
THEMES = {                    # clé (config ui.theme) → (nom affiché, accent)
    "vintage": ("VoiceConnector (rétro)", "#3fcf5a"),
    "studio": ("Dark Studio Pro", "#3b82f6"),
    "neon": ("Vert néon", "#00ff88"),
    "cyan": ("Cyan", "#00e5ff"),
    "bleu": ("Bleu", "#3d8bff"),
    "violet": ("Violet", "#a66bff"),
    "rose": ("Rose", "#ff4fb8"),
    "rouge": ("Rouge", "#ff3b4f"),
    "orange": ("Orange", "#ff9a1f"),
    "or": ("Or", "#ffd23f"),
    "blanc": ("Blanc", "#e9eef0"),
}
# Couleurs qui suivent le thème (les autres sont fixes)
_THEMED = ("BG_PANEL", "BG_PILL", "GREEN_NEON", "GREEN_BRIGHT", "GREEN_DIM", "GREEN_TEXT",
           "GREEN_LABEL", "GREEN_SCALE", "BG_WINDOW", "BG_GLOW", "BG_COLUMN", "BG_SECTION",
           "BORDER_COLUMN", "BORDER_SECTION", "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED",
           "BUTTON_BG", "BUTTON_HOVER", "PILL_BG", "PILL_BORDER", "KNOB_CAP_TOP",
           "KNOB_CAP_BOTTOM", "BG_DEEP", "ON_ACCENT", "ON_ACCENT_DARK", "BADGE_OFF")
_BASE = {name: globals()[name] for name in _THEMED}
MONO_THEMES = ("retro", "cyberpunk")      # thèmes à valeurs chiffrées « afficheur »
MONO_FONT_FAMILIES = ("Consolas", "Cascadia Mono", "Courier New")
MIN_ACCENT_CONTRAST = 4.5     # contraste minimal accent / fond (lisibilité, WCAG AA)
HOVER_TOWARD_WHITE = 0.22     # survol des boutons principaux : accent éclairci
_current_theme = DEFAULT_THEME

DEFAULT_FONT = "Inter"
FONT_FAMILY = DEFAULT_FONT    # police choisie (config ui.font_family), Inter par défaut
FONT_FALLBACK = "Segoe UI"
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")

# --- Styles des widgets dessinés à la main ------------------------------------
FADER_STYLES = {              # clé (config ui.fader_style) → nom affiché
    "console": "Console (capuchon carré)",
    "rond": "Rond",
    "fin": "Fin (barre)",
    "vintage": "Vintage (strié)",
}
VU_STYLES = {                 # clé (config ui.vu_style) → nom affiché
    "segments": "LED fines",
    "led": "LED larges",
    "continu": "Continu",
}
DEFAULT_FADER_STYLE = "console"
DEFAULT_VU_STYLE = "segments"
FADER_STYLE = DEFAULT_FADER_STYLE
VU_STYLE = DEFAULT_VU_STYLE


# --- Couleurs par élément (Réglages → Couleurs) -------------------------------
# Clés de config ui.colors : « mute » (toute la console), « mute@2 » (canal 3 seulement),
# « vu@master » (Master seulement) ; voir core/color_scheme.py. La couleur d'un canal passe
# avant celle de toute la console ; sans couleur, l'élément suit le thème.
COLORS: dict[str, str] = {}   # couleurs choisies (config ui.colors), « #rrggbb »
_panel_opacity = 1.0          # opacité des panneaux en cours (le fond d'un panneau coloré la suit)


def configure(ui_cfg: dict) -> bool:
    """Police, styles de widgets et couleurs par élément d'après la config `ui` ;
    True si quelque chose a changé."""
    global FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS
    family = str(ui_cfg.get("font_family") or DEFAULT_FONT).strip() or DEFAULT_FONT
    fader = ui_cfg.get("fader_style")
    fader = fader if fader in FADER_STYLES else DEFAULT_FADER_STYLE
    vu = ui_cfg.get("vu_style")
    vu = vu if vu in VU_STYLES else DEFAULT_VU_STYLE
    colors = color_scheme.clean(ui_cfg.get("colors"))
    changed = (family, fader, vu, colors) != (FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS)
    FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS = family, fader, vu, colors
    return changed


def color_key(element: str, scope: int | str | None = None) -> str:
    """Clé de ui.colors : « mute » pour toute la console, « mute@2 » pour un canal."""
    return color_scheme.color_key(element, scope)


def element_color(element: str, scope: int | str | None = None,
                  colors: dict | None = None) -> str | None:
    """Couleur choisie pour un élément : celle de son canal (ou du Master), sinon celle de
    toute la console ; None s'il suit le thème. `colors` : une config ui.colors à consulter à
    la place de celle appliquée (l'éditeur montre ainsi un choix avant qu'il soit appliqué)."""
    return color_scheme.resolve(COLORS if colors is None else color_scheme.clean(colors),
                                element, scope)


def element_default(element: str) -> str:
    """Couleur d'un élément quand il suit le thème."""
    return {"mute": MUTE_ON, "solo": SOLO_ON, "pad": RED_MUTE, "name": TEXT_PRIMARY,
            "panel": BG_COLUMN}.get(element, GREEN_NEON)


def accent(element: str, scope: int | str | None = None) -> str:
    """Couleur principale d'un élément (arc d'un knob, remplissage d'un fader…)."""
    return element_color(element, scope) or element_default(element)


def accent_dim(element: str, scope: int | str | None = None) -> str:
    """Teinte sombre assortie (piste d'un knob, bordure éteinte)."""
    custom = element_color(element, scope)
    return color_scheme.mix(custom, BG_SECTION, 0.7) if custom else GREEN_DIM


def accent_bright(element: str, scope: int | str | None = None) -> str:
    """Teinte de survol assortie."""
    custom = element_color(element, scope)
    return color_scheme.mix(custom, WHITE, HOVER_TOWARD_WHITE) if custom else GREEN_BRIGHT


def mix(a: str, b: str, amount: float) -> str:
    """Couleur entre `a` (0) et `b` (1)."""
    return color_scheme.mix(a, b, amount)


def ink_on(value: str) -> str:
    """Encre lisible sur une couleur de fond (texte d'un bouton allumé)."""
    return color_scheme.ink_on(value, ON_ACCENT_DARK, WHITE)


def lit_button_style(button: str, value: str) -> str:
    """QSS d'un bouton à bascule (« muteButton », « soloButton ») allumé dans une couleur choisie.
    Posée sur la tranche elle-même : elle passe avant la feuille de style de l'application."""
    hover = mix(value, WHITE, 0.15)
    return (f"QPushButton#{button}:checked {{ background: {value}; border: 1px solid {hover};"
            f" color: {ink_on(value)}; }}"
            f"QPushButton#{button}:checked:hover {{ background: {hover}; }}")


def mute_style(value: str) -> str:
    """QSS du bouton Muet allumé dans une couleur choisie."""
    return lit_button_style("muteButton", value)


def solo_style(value: str) -> str:
    """QSS du bouton Solo allumé dans une couleur choisie."""
    return lit_button_style("soloButton", value)


def panel_style(value: str, selector: str = "QFrame#column") -> str:
    """QSS du fond d'un panneau (colonne, tranche crème) dans une couleur choisie, avec
    l'opacité des panneaux en cours."""
    return f"{selector} {{ background: {_rgba(value, _panel_opacity)}; }}"


def font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Police de l'interface (Inter, repli Segoe UI) en taille pixel."""
    f = QFont()
    f.setFamilies([FONT_FAMILY, FONT_FALLBACK])
    f.setPixelSize(pixel_size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def color(value: str, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def load_fonts() -> bool:
    """Charge Inter depuis assets/ ; renvoie False si elle est indisponible."""
    loaded = False
    for name in FONT_FILES:
        path = config.resource_path(f"assets/{name}")
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded = True
        else:
            log.warning("Police introuvable ou illisible : %s", path)
    return loaded


def theme_accent(theme: str | None) -> QColor | None:
    """Couleur d'accent d'un thème (clé de THEMES, thème global ou « #rrggbb »), None si invalide."""
    spec = global_theme(theme)
    if spec is not None:
        return QColor(spec.accent)
    if theme in THEMES:
        return QColor(THEMES[theme][1])
    accent = QColor(str(theme or ""))
    return accent if str(theme or "").startswith("#") and accent.isValid() else None


def luminance(color: QColor) -> float:
    """Luminance relative (WCAG) d'une couleur."""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * amount),
                  round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def _rotate(value: str, hue_shift: float, saturation: float) -> str:
    h, s, l, _a = QColor(value).getHslF()
    if h < 0:                        # gris pur : pas de teinte à tourner
        return value
    return QColor.fromHslF((h + hue_shift) % 1.0, max(0.0, min(1.0, s * saturation)), l).name()


def _derive_shape() -> None:
    """Rayon des groupes de tranches : 6 px pour des blocs de 10 px, plus serré si le thème l'est."""
    globals()["R_GROUP"] = max(2, round(R_SECTION * 0.6))


def set_theme(theme: str | None) -> str:
    """Recolore la palette d'après le thème ; renvoie le thème appliqué (le
    thème par défaut si `theme` est inconnu). Appeler ensuite refresh().

    Un thème global (Rétro, Futuriste, Épuré, Studio Pro) impose aussi la peau des
    tranches, les couleurs d'état et la forme des coins ; un thème d'accent
    (historique, ou « #rrggbb ») rétablit celles d'origine."""
    global _current_theme
    spec = global_theme(theme)
    if spec is not None:
        globals().update(spec.values())
        _derive_shape()
        _current_theme = spec.key
        return spec.key
    accent = theme_accent(theme)
    if accent is None:
        theme, accent = DEFAULT_THEME, QColor(THEMES[DEFAULT_THEME][1])
    values = dict(_BASE)
    if theme == "studio":
        values.update(STUDIO_PALETTE)
    elif theme == "vintage":
        values.update(VINTAGE_PALETTE)
    elif theme != "neon":
        ah, as_, al, _ = accent.getHslF()
        bh, bs, _bl, _ = QColor(_BASE["GREEN_NEON"]).getHslF()
        shift = ah - bh if ah >= 0 else 0.0
        saturation = as_ / bs if ah >= 0 and bs > 0 else 0.0
        values = {name: _rotate(value, shift, saturation) for name, value in _BASE.items()}
        # Accent assez clair pour se lire sur les fonds (un bleu pur #0000ff serait
        # presque invisible) : on l'éclaircit jusqu'au contraste voulu
        background = QColor(values["BG_COLUMN"])
        while contrast(accent, background) < MIN_ACCENT_CONTRAST and al < 0.95:
            al = min(0.95, al + 0.02)
            accent = QColor.fromHslF(max(ah, 0.0), as_, al)
        values["GREEN_NEON"] = values["GREEN_TEXT"] = accent.name()
        # Survol des boutons principaux : dérivé de l'accent (le texte sombre
        # reste lisible), et non de la teinte voisine de la palette verte
        values["GREEN_BRIGHT"] = _mix(accent, QColor("#ffffff"), HOVER_TOWARD_WHITE).name()
        if contrast(accent, QColor(values["ON_ACCENT"])) < 4.5:   # « FX » du badge allumé
            values["ON_ACCENT"] = values["ON_ACCENT_DARK"]
    # Peau crème, couleurs d'état et coins d'origine (un thème global précédent les avait changés)
    values.update(_SKIN_BASE)
    values.update(_EXTRA_BASE)
    values.update(DEFAULT_SHAPE)
    values["GROUP_BORDER"] = values["BG_DEEP"]
    globals().update(values)
    _derive_shape()
    _current_theme = theme
    return theme


def global_spec() -> ThemeSpec | None:
    """Le thème global appliqué en ce moment (Rétro, Futuriste, Épuré, Studio Pro), sinon None."""
    return global_theme(_current_theme)


def is_dark() -> bool:
    """Vrai si l'interface est sombre (faux seulement pour le thème global clair)."""
    spec = global_spec()
    return spec.dark if spec is not None else True


def mono_values() -> bool:
    """Valeurs chiffrées en police à chasse fixe (look afficheur VFD / HUD) : Rétro et Futuriste."""
    return _current_theme in MONO_THEMES


def current_theme() -> str:
    return _current_theme


def _rgba(value: str, alpha: float) -> str:
    c = QColor(value)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {max(0.0, min(1.0, alpha)):.3f})"


def build_stylesheet(panel_opacity: float = 1.0) -> str:
    """QSS de l'application, aux couleurs du thème en cours. `panel_opacity`
    < 1 laisse voir l'arrière-plan (image personnalisée) à travers les
    colonnes et leurs blocs."""
    return ((_stylesheet() + _hardware_stylesheet() + _action_bar_stylesheet() + _strip_extras_stylesheet()
             + _theme_stylesheet())
            .replace("@COLUMN_BG@", _rgba(BG_COLUMN, panel_opacity))
            .replace("@SECTION_BG@", _rgba(BG_SECTION, panel_opacity)))


def refresh(app: QApplication, panel_opacity: float = 1.0) -> None:
    """Ré-applique palette et QSS (après un changement de thème ou d'opacité)."""
    global _panel_opacity
    _panel_opacity = panel_opacity
    _apply_palette(app)
    if app.font().family() != FONT_FAMILY:
        app.setFont(font(11))
    stylesheet = build_stylesheet(panel_opacity)
    if app.styleSheet() != stylesheet:   # re-styler toute l'app coûte : seulement si besoin
        app.setStyleSheet(stylesheet)


def set_panel_opacity(app: QApplication, panel_opacity: float) -> None:
    refresh(app, panel_opacity)


def apply_theme(app: QApplication, panel_opacity: float = 1.0, theme: str | None = None) -> None:
    """Charge les polices et applique thème, palette et QSS à toute l'application."""
    global _panel_opacity
    load_fonts()
    app.setFont(font(11))
    set_theme(theme)
    _panel_opacity = panel_opacity
    _apply_palette(app)
    app.setStyleSheet(build_stylesheet(panel_opacity))


def _apply_palette(app: QApplication) -> None:
    palette = app.palette()
    for role, value in (
        (QPalette.ColorRole.Window, BG_WINDOW),
        (QPalette.ColorRole.Base, BG_SECTION),
        (QPalette.ColorRole.AlternateBase, BG_COLUMN),
        (QPalette.ColorRole.WindowText, TEXT_PRIMARY),
        (QPalette.ColorRole.Text, TEXT_PRIMARY),
        (QPalette.ColorRole.ButtonText, TEXT_PRIMARY),
        (QPalette.ColorRole.Button, BUTTON_BG),
        (QPalette.ColorRole.Highlight, GREEN_DIM),
        (QPalette.ColorRole.HighlightedText, SELECTION_TEXT),
        (QPalette.ColorRole.ToolTipBase, BG_COLUMN),
        (QPalette.ColorRole.ToolTipText, TEXT_PRIMARY),
        (QPalette.ColorRole.PlaceholderText, TEXT_MUTED),
    ):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)


def _action_bar_stylesheet() -> str:
    """Barre d'actions : mode, logiciel suivi, disposition et bouton « Mode musical »."""
    return f"""
/* ---- barre d'actions ---- */
QFrame#actionBar {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_SECTION}px;
}}
QLabel#modeChip {{
    background: {BG_SECTION};
    border: 1px solid {GREEN_DIM};
    border-radius: {R_BUTTON}px;
    color: {GREEN_NEON};
    font-size: 10px;
    font-weight: 700;
    padding: 3px 9px;
}}
QLabel#barLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 600;
}}
QPushButton#appChip {{
    background: transparent;
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_BUTTON}px;
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
}}
QPushButton#appChip:hover {{
    background: {BUTTON_BG};
    border-color: {GREEN_DIM};
}}
QComboBox#layoutChoice {{
    min-width: 150px;
    padding: 3px 8px;
}}
QLabel#syncLed {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#syncLed[on="true"] {{
    color: {VU_GREEN};
}}
QPushButton#musicButton {{
    background: {BUTTON_BG};
    border: 1px solid {GREEN_DIM};
    border-radius: {R_BUTTON}px;
    color: {TEXT_PRIMARY};
    font-weight: 700;
    padding: 5px 14px;
}}
QPushButton#musicButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_NEON};
}}
QPushButton#musicButton:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
    color: {ON_ACCENT_DARK};
}}
QPushButton#musicButton:disabled {{
    color: {TEXT_MUTED};
}}
"""


def _strip_extras_stylesheet() -> str:
    """Compléments des tranches : libellé de l'emplacement, touche « Clarté », tranche absente."""
    return f"""
/* ---- tranches : dispositions et mode autonome ---- */
QFrame#stripPanel QLabel#roleLabel {{
    color: {INK_SOFT};
    font-size: 8px;
    font-weight: 700;
}}
QFrame#stripPanel[absent="true"] {{
    background: rgba(0, 0, 0, 0.10);
}}
QFrame#stripPanel QLabel#stripName[absent="true"], QFrame#stripPanel QLabel#roleLabel[absent="true"] {{
    color: {INK_SOFT};
    font-weight: 500;
}}
QFrame#stripPanel QPushButton#clarityButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CREAM_KEY}, stop:1 {CREAM_DARK});
    border: 1px solid {INK_SOFT};
    border-bottom: 2px solid {INK_SOFT};
    border-radius: 4px;
    color: {INK};
    font-size: 9px;
    font-weight: 600;
    padding: 0;
}}
QFrame#stripPanel QPushButton#clarityButton:hover {{
    background: {CREAM_KEY};
    border-color: {INK};
}}
QFrame#stripPanel QPushButton#clarityButton:checked {{
    background: {GREEN_NEON};
    border: 1px solid {INK};
    color: {ON_ACCENT_DARK};
}}
"""


def _theme_stylesheet() -> str:
    """Compléments propres aux thèmes globaux, ajoutés en dernier (à sélecteur égal ils l'emportent)."""
    rules = ""
    if mono_values():                       # valeurs chiffrées façon afficheur (VFD / HUD)
        families = ", ".join(f'"{name}"' for name in MONO_FONT_FAMILIES)
        rules += f"""
/* ---- valeurs en chasse fixe (thèmes Rétro et Futuriste) ---- */
QLabel#faderValue, QLabel#masterValue, QLabel#latencyLabel, QLabel#appPercent {{
    font-family: {families}, monospace;
}}
"""
    return rules


def _stylesheet() -> str:
    return f"""
* {{
    font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
    outline: none;
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
}}
QMainWindow, QDialog {{
    background: {BG_WINDOW};
}}

/* ---- colonnes et blocs ---- */
QFrame#column {{
    background: @COLUMN_BG@;
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_COLUMN}px;
}}
QFrame#section {{
    background: @SECTION_BG@;
    border: 1px solid {BORDER_SECTION};
    border-radius: {R_SECTION}px;
}}
QLabel {{
    background: transparent;
}}
QLabel#sectionTitle {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#knobLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 500;
}}
QLabel#latencyLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 600;
}}
QLabel#latencyLabel[slow="true"] {{
    color: {SOLO_ON};
}}
QLabel#faderValue {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#faderValue[muted="true"] {{
    color: {TEXT_MUTED};
}}
QLabel#stripName {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
}}
QLineEdit#stripNameEdit {{
    background: {BG_SECTION};
    border: 1px solid {GREEN_DIM};
    border-radius: {max(2, R_INPUT - 1)}px;
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    padding: 1px 4px;
    selection-background-color: {GREEN_DIM};
}}
QLabel#masterTitle {{
    color: {TEXT_PRIMARY};
    font-size: 18px;
    font-weight: 600;
}}
QLabel#masterValue {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#masterLabel {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 500;
}}

/* ---- bouton Muet ---- */
QPushButton#muteButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_BUTTON}px;
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#muteButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton#muteButton:checked {{
    background: {MUTE_ON};
    border: 1px solid {MUTE_ON_HOVER};
    color: {WHITE};
}}
QPushButton#muteButton:checked:hover {{
    background: {MUTE_ON_HOVER};
}}

/* ---- bouton Solo ---- */
QPushButton#soloButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_BUTTON}px;
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#soloButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton#soloButton:checked {{
    background: {SOLO_ON};
    border: 1px solid {SOLO_ON_HOVER};
    color: {TEXT_ON_SOLO};
}}
QPushButton#soloButton:checked:hover {{
    background: {SOLO_ON_HOVER};
}}

/* ---- zone d'effets ---- */
QScrollArea#tabScroll, QScrollArea#tabScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollArea#fxScroll, QScrollArea#fxScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0;
    border: none;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0 2px;
    border: none;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {BORDER_COLUMN};
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    min-height: 24px;
}}
QScrollBar::handle:horizontal {{
    min-width: 24px;
}}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{
    background: {GREEN_DIM};
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0;
    height: 0;
}}

/* ---- dialogues ---- */
QDialog QLabel#dialogTitle {{
    color: {TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 600;
}}
QDialog QLabel#hint {{
    color: {TEXT_SECONDARY};
}}
QDialog QLabel#warning {{
    color: {YELLOW_VU};
}}
QDialog QLabel#ok {{
    color: {GREEN_NEON};
}}
QGroupBox {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_SECTION}px;
    margin-top: 18px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT_SECONDARY};
}}
QLineEdit, QComboBox {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_INPUT}px;
    padding: 5px 8px;
    selection-background-color: {GREEN_DIM};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {GREEN_DIM};
}}
QComboBox QAbstractItemView {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    selection-background-color: {GREEN_DIM};
}}
QListWidget {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {max(2, R_SECTION - 2)}px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 5px 6px;
    border-radius: {max(2, R_INPUT - 1)}px;
}}
QListWidget::item:hover {{
    background: {BUTTON_BG};
}}
QListWidget::item:selected {{
    background: {GREEN_DIM};
    color: {SELECTION_TEXT};
}}
QPushButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_BUTTON}px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton:pressed {{
    background: {GREEN_DIM};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QPushButton#primary {{
    background: {GREEN_NEON};
    border: none;
    color: {ON_ACCENT_DARK};
}}
QPushButton#primary:hover {{
    background: {GREEN_BRIGHT};
}}
QPushButton#primary:disabled {{
    background: {GREEN_DIM};
    color: {TEXT_MUTED};
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: {min(4, R_INPUT)}px;
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QCheckBox::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QMenu {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {R_BUTTON + 1}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px 6px 12px;
    border-radius: {max(2, R_INPUT - 1)}px;
}}
QMenu::item:selected {{
    background: {GREEN_DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_COLUMN};
    margin: 4px 6px;
}}
QToolTip {{
    background: {BG_COLUMN};
    color: {TEXT_PRIMARY};
    border: 1px solid {GREEN_DIM};
    border-radius: {min(4, R_INPUT)}px;
    padding: 4px 6px;
}}
QMessageBox {{
    background: {BG_WINDOW};
}}

/* ---- onglets, curseurs, choix (réglages) ---- */
QTabWidget::pane {{
    border: none;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 7px 14px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: {R_BUTTON}px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
    border-color: {GREEN_DIM};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BUTTON_BG};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {GREEN_DIM};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {GREEN_NEON};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::groove:vertical {{
    width: 4px;
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: 2px;
}}
QSlider::add-page:vertical {{
    background: {GREEN_NEON};
    border-radius: 2px;
}}
QSlider::sub-page:vertical {{
    background: {BUTTON_BG};
    border-radius: 2px;
}}
QSlider::handle:vertical {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f8fafc, stop:0.48 #cbd5e1,
                                stop:0.5 {GREEN_NEON}, stop:0.56 #cbd5e1, stop:1 #94a3b8);
    border: 1px solid {BG_DEEP};
    height: 26px;
    width: 18px;
    margin: 0 -8px;
    border-radius: 4px;
}}
QSlider::handle:vertical:hover {{
    border-color: {GREEN_NEON};
}}
QSlider::handle:horizontal:hover {{
    background: {GREEN_BRIGHT};
}}
QRadioButton {{
    spacing: 8px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 7px;
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QRadioButton::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QLabel#credits {{
    color: {TEXT_SECONDARY};
}}

/* ---- volume par application ---- */
QLabel#appMixerTitle {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#appMixerHint {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
}}
QLabel#appName {{
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}
QLabel#appName:disabled {{
    color: {TEXT_MUTED};
}}
QLabel#appPercent {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
}}

/* ---- mises à jour ---- */
QProgressBar {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {min(5, R_INPUT)}px;
    height: 10px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: {GREEN_NEON};
    border-radius: {min(4, R_INPUT)}px;
}}
"""


def _hardware_stylesheet() -> str:
    """Habillage « rack vintage » : groupes crème, touches, clés de transport.
    Ajouté APRÈS la QSS de base : à sélecteur égal, ces règles l'emportent."""
    return f"""
/* ---- groupes et tranches crème ---- */
QFrame#creamGroup {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CREAM}, stop:1 {CREAM_DARK});
    border: 2px solid {GROUP_BORDER};
    border-radius: {R_GROUP}px;
}}
QFrame#darkGroup {{
    background: {BG_DEEP};
    border: 2px solid {BORDER_COLUMN};
    border-radius: {R_GROUP}px;
}}
QFrame#stripPanel {{
    background: transparent;
    border: none;
    border-right: 1px solid {CREAM_EDGE};
    border-radius: 0;
}}
QFrame#stripPanel[last="true"] {{
    border-right: none;
}}
QFrame#bay {{
    background: {BAY_BG};
    border: 1px solid {CREAM_EDGE};
    border-radius: {R_GROUP}px;
}}
QFrame#outputStrip {{
    background: transparent;
    border: none;
    border-right: 1px solid {CREAM_EDGE};
}}
QFrame#outputStrip[last="true"] {{
    border-right: none;
}}
QFrame#stripPanel QLabel#stripName, QFrame#outputStrip QLabel#stripName {{
    color: {INK};
    font-size: 10px;
    font-weight: 700;
}}
QFrame#stripPanel QLabel#knobLabel, QFrame#outputStrip QLabel#knobLabel {{
    color: {INK_SOFT};
    font-size: 9px;
    font-weight: 600;
}}
QFrame#stripPanel QLabel#faderValue, QFrame#outputStrip QLabel#faderValue {{
    color: {INK};
}}
QFrame#stripPanel QLabel#faderValue[muted="true"], QFrame#outputStrip QLabel#faderValue[muted="true"] {{
    color: {INK_SOFT};
}}
QLabel#creamTitle {{
    color: {INK};
    font-size: 10px;
    font-weight: 700;
}}
QLabel#groupTitle {{
    color: {TEXT_SECONDARY};
    font-size: 9px;
    font-weight: 700;
}}
QLabel#matrixTitle {{
    color: {TEXT_PRIMARY};
    font-size: 9px;
    font-weight: 700;
}}

/* ---- touches Solo / Mute sur crème ---- */
QFrame#stripPanel QPushButton#muteButton, QFrame#stripPanel QPushButton#soloButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {CREAM_KEY}, stop:1 {CREAM_DARK});
    border: 1px solid {INK_SOFT};
    border-bottom: 2px solid {INK_SOFT};
    border-radius: 4px;
    color: {INK};
    font-size: 10px;
    padding: 0;
}}
QFrame#stripPanel QPushButton#muteButton:hover, QFrame#stripPanel QPushButton#soloButton:hover {{
    background: {CREAM_KEY};
    border-color: {INK};
}}
QFrame#stripPanel QPushButton#muteButton:checked {{
    background: {MUTE_ON};
    border: 1px solid {INK};
    color: {WHITE};
}}
QFrame#stripPanel QPushButton#soloButton:checked {{
    background: {SOLO_ON};
    border: 1px solid {INK};
    color: {TEXT_ON_SOLO};
}}

/* ---- section Master : touches générales et transport ---- */
QPushButton#toolMute, QPushButton#toolEject {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e0795a, stop:1 #a8432b);
    border: 1px solid #4a170c;
    border-radius: 4px;
    color: {WHITE};
    font-size: 10px;
    font-weight: 700;
    padding: 5px 4px;
}}
QPushButton#toolMute:hover, QPushButton#toolEject:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ee8d70, stop:1 #bb5236);
}}
QPushButton#toolMute:checked {{
    background: {MUTE_ON};
    border-color: {WHITE};
}}
QPushButton#transport {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {TRANSPORT_TOP}, stop:1 {TRANSPORT_BOTTOM});
    border: 1px solid {BG_DEEP};
    border-radius: 3px;
    color: {TEXT_PRIMARY};
    font-size: 9px;
    font-weight: 700;
    padding: 4px 0;
}}
QPushButton#transport:hover {{
    border-color: {TEXT_SECONDARY};
}}
QPushButton#transport:checked {{
    background: {GREEN_DIM};
    border-color: {GREEN_NEON};
}}
QPushButton#transportRec:checked {{
    background: {MUTE_ON};
    border-color: {RED_MUTE};
}}
QLabel#tapeFile {{
    color: {TEXT_SECONDARY};
    font-size: 8px;
}}
"""

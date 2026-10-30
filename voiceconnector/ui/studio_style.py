"""
Apparence du Studio : une fenêtre de studio sombre (graphite et orange), distincte de la console de
VoiceConnector. Les couleurs sont des constantes ; la feuille de style ne s'applique qu'à la fenêtre
Studio (et ses enfants), jamais au reste de l'application.
"""

from __future__ import annotations

from core import color_scheme

# --- Surfaces
BG = "#22262a"                 # fond de la fenêtre
PANEL = "#2b3036"              # barres et panneaux
PANEL_ALT = "#323840"          # panneaux en relief (en-têtes de colonne)
INSET = "#1a1d21"              # fonds enfoncés (champs, pas éteints)
BORDER = "#3c434b"

# --- Texte
TEXT = "#dfe5ea"
TEXT_DIM = "#98a2aa"
TEXT_FAINT = "#5f6870"

# --- Accents
ACCENT = "#ff9d2e"             # orange : lecture, tempo, sélection
ACCENT_DIM = "#6e4413"
GREEN = "#6fdc6c"              # voyant allumé
GREEN_DIM = "#1f3a22"
RED = "#ef4a3a"                # enregistrement, muet
RED_DIM = "#4a1d19"
YELLOW = "#f2c744"             # solo
BLUE = "#4aa3ff"               # armement, curseur
STEP_OFF = ("#3a4047", "#30363c")      # pas éteints : temps pairs / impairs
STEP_PLAYHEAD = "#ffffff"

DEFAULT_CHANNEL = "#6b7a74"

BANNER_STYLES = {                      # état de la liaison → (fond, texte, bord)
    "demo": ("#2b3947", "#cfe3ff", "#44566e"),
    "waiting": ("#3b3321", "#ffe2a3", "#6b5a2b"),
    "error": ("#402523", "#ffc9c4", "#77403b"),
    "live": ("#223a28", "#c9f3d0", "#3d6b48"),
}


def mix(first: str, second: str, amount: float) -> str:
    return color_scheme.mix(first, second, amount)


def step_color(channel_color: str) -> str:
    """Couleur d'un pas allumé : celle du canal, éclaircie ; un canal gris prend l'orange du Studio."""
    base = color_scheme.normalize(channel_color) or DEFAULT_CHANNEL
    return ACCENT if base == DEFAULT_CHANNEL else mix(base, "#ffffff", 0.18)


def banner_style(kind: str) -> str:
    background, text, border = BANNER_STYLES.get(kind, BANNER_STYLES["demo"])
    return (f"QLabel#studioBanner {{ background: {background}; color: {text}; border: 1px solid {border};"
            f" border-radius: 6px; padding: 7px 12px; }}")


def stylesheet() -> str:
    """Feuille de style de la fenêtre Studio."""
    return f"""
QWidget#studioWindow {{ background: {BG}; color: {TEXT}; }}
QWidget#studioWindow QLabel {{ color: {TEXT}; background: transparent; }}
QWidget#studioWindow QToolTip {{ background: #14171a; color: {TEXT}; border: 1px solid {BORDER}; }}
QFrame#studioHeader, QFrame#transportBar, QFrame#rackHeader, QFrame#mixerHeader {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#studioPanel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QLabel#studioTitle {{ color: {ACCENT}; font-weight: 700; letter-spacing: 1px; }}
QLabel#studioProject {{ color: {TEXT_DIM}; }}
QLabel#panelTitle {{ color: {TEXT_DIM}; font-weight: 600; letter-spacing: 1px; }}
QLabel#tempoValue {{ color: {ACCENT}; font-weight: 700; }}
QLabel#positionValue {{ color: {ACCENT}; font-weight: 700; }}
QLabel#timeValue {{ color: {TEXT_DIM}; }}
QLabel#emptyHint {{ color: {TEXT_FAINT}; }}
QWidget#studioWindow QPushButton {{
    background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 12px; }}
QWidget#studioWindow QPushButton:hover {{ background: {mix(PANEL_ALT, '#ffffff', 0.08)}; }}
QWidget#studioWindow QPushButton:pressed {{ background: {INSET}; }}
QWidget#studioWindow QPushButton:disabled {{ color: {TEXT_FAINT}; background: {PANEL}; }}
QPushButton#transportButton {{ min-width: 38px; min-height: 30px; padding: 0; font-size: 15px; }}
QPushButton#transportButton:checked {{ background: {ACCENT}; color: #1b1205; border: 1px solid {mix(ACCENT, '#ffffff', 0.3)}; }}
QPushButton#recordButton:checked {{ background: {RED}; color: #ffffff; border: 1px solid {mix(RED, '#ffffff', 0.3)}; }}
QPushButton#modeButton:checked {{ background: {BLUE}; color: #04101c; border: 1px solid {mix(BLUE, '#ffffff', 0.3)}; }}
QPushButton#bankButton {{ min-width: 30px; padding: 4px 8px; }}
QWidget#studioWindow QComboBox {{
    background: {INSET}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 8px; min-height: 20px; }}
QWidget#studioWindow QComboBox:hover {{ border: 1px solid {ACCENT_DIM}; }}
QWidget#studioWindow QComboBox QAbstractItemView {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM}; selection-color: {TEXT}; }}
QWidget#studioWindow QDoubleSpinBox {{
    background: {INSET}; color: {ACCENT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 3px 6px; font-weight: 700; }}
QWidget#studioWindow QScrollArea {{ background: transparent; border: none; }}
QWidget#studioWindow QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWidget#studioWindow QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QWidget#studioWindow QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 28px; }}
QWidget#studioWindow QScrollBar:horizontal {{ background: {BG}; height: 12px; margin: 0; }}
QWidget#studioWindow QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 28px; }}
QWidget#studioWindow QScrollBar::add-line, QWidget#studioWindow QScrollBar::sub-line {{ width: 0; height: 0; }}
QWidget#studioWindow QSplitter::handle {{ background: {BG}; }}
QWidget#studioWindow QSlider::groove:horizontal {{ background: {INSET}; height: 6px; border-radius: 3px; }}
QWidget#studioWindow QSlider::sub-page:horizontal {{ background: {ACCENT_DIM}; border-radius: 3px; }}
QWidget#studioWindow QSlider::handle:horizontal {{
    background: {ACCENT}; width: 12px; margin: -4px 0; border-radius: 6px; }}
"""

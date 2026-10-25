"""
Arrière-plans des thèmes globaux (Rétro, Futuriste, Épuré, Studio Pro).

Chaque thème de core/themes.py désigne un arrière-plan (`ThemeSpec.backdrop`) ; la fonction
`paint_backdrop` le dessine aux couleurs du thème en cours (`styles`). Le fond est peint une
fois par taille de fenêtre dans un cache (voir Background dans main_window.py), pas à chaque image.
"""

from __future__ import annotations

import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient

from ui import styles

SYNTHWAVE, CYBER, FLAT, SLATE = "synthwave", "cyber", "flat", "slate"
HORIZON = 0.62                      # position de l'horizon du fond « synthwave » (part de la hauteur)
GRID_STEP = 32                      # pas du quadrillage « HUD »
CHEEK = 22                          # joues latérales du fond « Studio Pro »


def paint_backdrop(p: QPainter, kind: str, width: int, height: int) -> None:
    """Peint l'arrière-plan `kind` sur toute la surface (width × height)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(QRectF(0, 0, width, height), QColor(styles.BG_WINDOW))
    painter = _PAINTERS.get(kind, _paint_slate)
    painter(p, width, height)


def _paint_synthwave(p: QPainter, w: int, h: int) -> None:
    """Ciel violet, soleil à moitié couché, grille de néon en perspective."""
    horizon = h * HORIZON
    sky = QLinearGradient(0, 0, 0, horizon)
    sky.setColorAt(0.0, QColor(styles.BG_WINDOW))
    sky.setColorAt(1.0, styles.color(styles.BG_GLOW, 1.0))
    p.fillRect(QRectF(0, 0, w, horizon), sky)

    radius = min(w, h) * 0.2
    sun = QLinearGradient(0, horizon - radius, 0, horizon)
    sun.setColorAt(0.0, QColor("#ffd23f"))
    sun.setColorAt(0.5, QColor("#ff5fa2"))
    sun.setColorAt(1.0, QColor("#a63cff"))
    p.save()
    p.setClipRect(QRectF(0, 0, w, horizon))
    p.setOpacity(0.30)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(sun)
    p.drawEllipse(QPointF(w / 2, horizon), radius, radius)
    p.restore()

    floor = QLinearGradient(0, horizon, 0, h)
    floor.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.9))
    floor.setColorAt(1.0, QColor(styles.BG_DEEP))
    p.fillRect(QRectF(0, horizon, w, h - horizon), floor)

    p.setPen(QPen(styles.color(styles.GROUP_BORDER, 0.85), 1))
    rows = 14
    for i in range(1, rows + 1):                                  # lignes horizontales : plus espacées vers le bas
        t = i / rows
        y = horizon + (h - horizon) * t * t
        p.drawLine(QPointF(0, y), QPointF(w, y))
    lines = 22
    for i in range(-lines, lines + 1):                            # lignes de fuite
        p.drawLine(QPointF(w / 2 + i * 5, horizon), QPointF(w / 2 + i * (w / lines) * 1.25, h))
    p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.55), 1.5))    # filet lumineux de l'horizon
    p.drawLine(QPointF(0, horizon), QPointF(w, horizon))


def _paint_cyber(p: QPainter, w: int, h: int) -> None:
    """Halo bleu nuit, quadrillage HUD, équerres aux coins et lignes de balayage."""
    glow = QRadialGradient(QPointF(w * 0.7, h * 0.3), max(w, h) * 0.7)
    glow.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.95))
    glow.setColorAt(1.0, styles.color(styles.BG_GLOW, 0.0))
    p.fillRect(QRectF(0, 0, w, h), glow)

    p.setPen(QPen(styles.color(styles.BORDER_SECTION, 0.6), 1))
    for x in range(0, w, GRID_STEP):
        p.drawLine(QPointF(x, 0), QPointF(x, h))
    for y in range(0, h, GRID_STEP):
        p.drawLine(QPointF(0, y), QPointF(w, y))

    p.setPen(QPen(QColor(styles.GREEN_NEON), 2))
    arm, margin = 28, 6
    for x, y, dx, dy in ((margin, margin, 1, 1), (w - margin, margin, -1, 1),
                         (margin, h - margin, 1, -1), (w - margin, h - margin, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + dx * arm, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + dy * arm))

    p.setPen(QPen(styles.color("#000000", 0.16), 1))
    for y in range(0, h, 4):
        p.drawLine(QPointF(0, y), QPointF(w, y))


def _paint_flat(p: QPainter, w: int, h: int) -> None:
    """Aplat clair très légèrement dégradé : aucun ornement."""
    fill = QLinearGradient(0, 0, 0, h)
    fill.setColorAt(0.0, QColor(styles.BG_GLOW))
    fill.setColorAt(1.0, QColor(styles.BG_WINDOW))
    p.fillRect(QRectF(0, 0, w, h), fill)


def _paint_slate(p: QPainter, w: int, h: int) -> None:
    """Plaque d'ardoise brossée de station audio, avec des joues sombres sur les côtés."""
    metal = QLinearGradient(0, 0, 0, h)
    metal.setColorAt(0.0, QColor(styles.CHASSIS_TOP))
    metal.setColorAt(1.0, QColor(styles.CHASSIS_BOTTOM))
    p.fillRect(QRectF(0, 0, w, h), metal)
    rng = random.Random(11)
    for y in range(0, h, 2):                                      # brossage : fines lignes peu contrastées
        p.setPen(QPen(QColor(255, 255, 255, rng.choice((0, 0, 3, 5))), 1))
        p.drawLine(QPointF(CHEEK, y), QPointF(w - CHEEK, y))
    for left in (True, False):
        x = 0 if left else w - CHEEK
        cheek = QLinearGradient(x, 0, x + CHEEK, 0)
        cheek.setColorAt(0.0, QColor(styles.WOOD_LIGHT if left else styles.WOOD_DARK))
        cheek.setColorAt(1.0, QColor(styles.WOOD_DARK if left else styles.WOOD_LIGHT))
        p.fillRect(QRectF(x, 0, CHEEK, h), cheek)
        edge = x + CHEEK - 1 if left else x
        p.setPen(QPen(QColor(0, 0, 0, 120), 2))
        p.drawLine(QPointF(edge, 0), QPointF(edge, h))


_PAINTERS = {SYNTHWAVE: _paint_synthwave, CYBER: _paint_cyber, FLAT: _paint_flat, SLATE: _paint_slate}

"""
Knob circulaire custom (paintEvent, antialiasé).

- Strips : clic = routing on/off vers le bus, glisser verticalement = niveau
  d'envoi vers ce bus (Maj = réglage fin), molette = idem.
- Master (casque) : pas de bascule ; le dessin suit la valeur avec une
  interpolation douce, y compris pendant le glisser.

Course de 270° (vide en bas), même loi dB que les faders : 0 dB = butée haute.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles
from ui.fader import FADER_MAX_DB, FADER_MIN_DB, db_to_ratio, format_db, ratio_to_db

START_ANGLE = 225.0      # degrés, sens trigo (225° = en bas à gauche)
SPAN = 270.0
DRAG_PX = 160.0          # pixels de glisser pour toute la course
DRAG_FINE = 0.25
CLICK_SLOP = 3.0         # au-delà, un appui devient un glisser (et plus un clic)
WHEEL_STEP = 0.025
SMOOTH_MS = 16
SMOOTH_FACTOR = 0.28


def _position(db: float) -> float:
    """dB → position sur la course (0 = -60 dB, 1 = +12 dB)."""
    return 1.0 - db_to_ratio(db)


class Knob(QWidget):
    valueChanged = Signal(float)
    toggled = Signal(bool)

    def __init__(self, diameter: int = 30, checkable: bool = True, smooth: bool = False,
                 default_db: float = 0.0, adjustable: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._checkable = checkable
        self._adjustable = adjustable    # False : simple interrupteur (Banana)
        self._smooth = smooth
        self._default = default_db
        self._value = default_db
        self._active = True
        self._shown = _position(default_db)   # position dessinée (interpolée si smooth)
        self._dragging = False
        self._moved = False
        self._press_y = 0.0
        self._press_pos = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(SMOOTH_MS)
        self._timer.timeout.connect(self._animate)
        size = diameter + 8
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, db: float) -> None:  # noqa: N802
        """Valeur venant de Voicemeeter ; ignorée pendant un glisser."""
        if self._dragging or abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()

    def isActive(self) -> bool:  # noqa: N802
        return self._active

    def setActive(self, active: bool) -> None:  # noqa: N802
        if active != self._active:
            self._active = active
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = float(self._diameter)
        ring = max(2.6, d * 0.12)
        center = QPointF(self.width() / 2, self.height() / 2)
        arc_rect = QRectF(center.x() - (d - ring) / 2, center.y() - (d - ring) / 2,
                          d - ring, d - ring)
        pos = max(0.0, min(1.0, self._shown))
        span_value = -SPAN * pos
        arc = int(START_ANGLE * 16)

        if self._active:
            # Halo, piste, puis arc de valeur
            p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.16), ring * 2.0,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(span_value * 16))
            p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.7), ring,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * 16))
            if pos > 0.002:
                p.setPen(QPen(QColor(styles.GREEN_NEON), ring,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(arc_rect, arc, int(span_value * 16))
        else:
            # Éteint : piste à peine visible + point vert au départ de la course
            p.setPen(QPen(styles.color(styles.GREEN_DIM, 0.25), ring * 0.6,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, arc, int(-SPAN * 16))
            start = math.radians(START_ANGLE)
            r = arc_rect.width() / 2
            dot = QPointF(center.x() + r * math.cos(start), center.y() - r * math.sin(start))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(styles.color(styles.GREEN_NEON, 0.85))
            p.drawEllipse(dot, ring * 0.45, ring * 0.45)

        # Bouton central
        cap_r = d / 2 - ring - max(2.0, ring * 0.55)
        gradient = QRadialGradient(center.x() - cap_r * 0.3, center.y() - cap_r * 0.4, cap_r * 1.6)
        gradient.setColorAt(0, QColor(styles.KNOB_CAP_TOP))
        gradient.setColorAt(1, QColor(styles.KNOB_CAP_BOTTOM))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.WHITE, 0.08), 1))
        p.drawEllipse(center, cap_r, cap_r)

        # Indicateur blanc du centre vers le bord
        angle = math.radians(START_ANGLE - SPAN * pos)
        direction = QPointF(math.cos(angle), -math.sin(angle))
        width = max(2.0, d * 0.055)
        p.setPen(QPen(styles.color(styles.WHITE, 0.95 if self._active else 0.75), width,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center + direction * (cap_r * 0.25), center + direction * (cap_r * 0.78))

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging, self._moved = True, False
        self._press_y = event.position().y()
        self._press_pos = _position(self._value)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        dy = self._press_y - event.position().y()
        if not self._moved and abs(dy) < CLICK_SLOP:
            return
        self._moved = True
        if not self._adjustable:
            return
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self._user_set_position(self._press_pos + dy / DRAG_PX * factor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if not self._moved and self._checkable and self.rect().contains(event.position().toPoint()):
            self._active = not self._active
            self._update_tooltip()
            self.update()
            self.toggled.emit(self._active)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Sur un knob de routing, le double-clic serait aussi deux bascules : réservé au master
        if not self._checkable and event.button() == Qt.MouseButton.LeftButton:
            self._user_set(self._default)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self._adjustable:
            event.ignore()
            return
        steps = event.angleDelta().y() / 120
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self._user_set_position(_position(min(self._value, FADER_MAX_DB)) + steps * WHEEL_STEP * factor)
        event.accept()

    def _user_set_position(self, pos: float) -> None:
        self._user_set(round(ratio_to_db(1.0 - max(0.0, min(1.0, pos))), 1))

    def _user_set(self, db: float) -> None:
        db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
        if abs(db - self._value) < 0.005:
            return
        self._value = db
        self._retarget()
        self.valueChanged.emit(db)

    # ----------------------------------------------------------- animation
    def _retarget(self) -> None:
        self._update_tooltip()
        if self._smooth:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._shown = _position(self._value)
            self.update()

    def _animate(self) -> None:
        target = _position(self._value)
        self._shown += (target - self._shown) * SMOOTH_FACTOR
        if abs(target - self._shown) < 0.001:
            self._shown = target
            self._timer.stop()
        self.update()

    def _update_tooltip(self) -> None:
        state = "" if not self._checkable else (" · activé" if self._active else " · coupé")
        if not self._adjustable:
            self.setToolTip(("Envoi activé" if self._active else "Envoi coupé") + " · clic pour basculer")
            return
        self.setToolTip(f"{format_db(self._value)} dB{state}")


# --------------------------------------------------------------------------- #
# Style matériel (rack vintage) : potentiomètres gradués et boutons de routage
# --------------------------------------------------------------------------- #
def paint_dial(p: QPainter, center: QPointF, diameter: float, pos: float, *,
               accent: str | None = None, on_dark: bool = False, active: bool = True,
               ticks: int = 11) -> None:
    """Potentiomètre rotatif de rack : graduations sur 270°, bague colorée
    optionnelle (accent), corps sombre moleté, index blanc.

    `pos` : 0 (butée basse) → 1 (butée haute). `on_dark` : graduations claires
    (potentiomètre posé sur le châssis noir) ou sombres (posé sur crème)."""
    pos = max(0.0, min(1.0, pos))
    r = diameter / 2
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Graduations (les trois repères principaux sont plus longs)
    tick_color = QColor(styles.TEXT_SECONDARY if on_dark else styles.INK_SOFT)
    p.setPen(QPen(tick_color, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i in range(ticks):
        angle = math.radians(START_ANGLE - SPAN * i / (ticks - 1))
        direction = QPointF(math.cos(angle), -math.sin(angle))
        major = i in (0, ticks - 1, (ticks - 1) // 2)
        p.drawLine(center + direction * (r * (0.84 if major else 0.9)), center + direction * r)

    body_r = r * 0.7
    if accent:                                   # bague colorée (trim bleu / vert…)
        ring = QColor(accent if active else styles.TEXT_MUTED)
        p.setPen(QPen(ring, max(2.4, r * 0.13)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, body_r + 1.2, body_r + 1.2)

    # Ombre portée puis corps
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawEllipse(center + QPointF(0, 1.6), body_r, body_r)
    body = QRadialGradient(center.x() - body_r * 0.3, center.y() - body_r * 0.4, body_r * 1.6)
    body.setColorAt(0, QColor("#55575c"))
    body.setColorAt(1, QColor("#1b1c1e"))
    p.setBrush(body)
    p.setPen(QPen(QColor("#0a0a0b"), 1))
    p.drawEllipse(center, body_r, body_r)

    # Moletage du bord
    p.setPen(QPen(QColor(255, 255, 255, 26), 1))
    for i in range(24):
        angle = 2 * math.pi * i / 24
        direction = QPointF(math.cos(angle), -math.sin(angle))
        p.drawLine(center + direction * (body_r * 0.86), center + direction * (body_r * 0.97))

    # Index
    angle = math.radians(START_ANGLE - SPAN * pos)
    direction = QPointF(math.cos(angle), -math.sin(angle))
    p.setPen(QPen(QColor(styles.WHITE if active else styles.TEXT_SECONDARY), max(2.0, r * 0.1),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(center + direction * (body_r * 0.25), center + direction * (body_r * 0.82))


class HardwareKnob(Knob):
    """Knob de la loi dB des faders (master…) dessiné comme un potentiomètre de
    rack. Même comportement que Knob : seul le dessin change."""

    def __init__(self, *args, accent: str | None = None, on_dark: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._accent = accent
        self._on_dark = on_dark

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        paint_dial(p, QPointF(self.width() / 2, self.height() / 2), float(self._diameter),
                   self._shown, accent=self._accent, on_dark=self._on_dark, active=self._active)


class RouteButton(Knob):
    """Bouton rond rétro-éclairé du routage rapide (A1…B3). Clic = routage
    on/off ; glisser = niveau d'envoi, montré par l'arc autour du bouton.
    Même API que Knob (setActive, setValue, toggled, valueChanged…)."""

    def __init__(self, diameter: int = 26, color: str = "#f08a24", adjustable: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(diameter=diameter, checkable=True, adjustable=adjustable, parent=parent)
        self._color = color

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = float(self._diameter)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = d / 2
        lit = QColor(self._color)

        # Arc du niveau d'envoi (autour de la collerette)
        ring_r = r - 1.2
        arc_rect = QRectF(c.x() - ring_r, c.y() - ring_r, 2 * ring_r, 2 * ring_r)
        pos = max(0.0, min(1.0, self._shown))
        p.setPen(QPen(QColor(styles.INK_SOFT).lighter(150), 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(arc_rect, int(START_ANGLE * 16), int(-SPAN * 16))
        if self._active and pos > 0.002:
            p.setPen(QPen(lit, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(arc_rect, int(START_ANGLE * 16), int(-SPAN * 16 * pos))

        # Collerette sombre
        bezel = r - 4.0
        p.setPen(QPen(QColor("#0a0a0b"), 1))
        p.setBrush(QColor("#2b2c2f"))
        p.drawEllipse(c, bezel, bezel)

        # Touche : allumée (halo + dégradé) ou éteinte (teinte très sombre)
        cap = bezel - 2.0
        if self._active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(lit.red(), lit.green(), lit.blue(), 60))
            p.drawEllipse(c, cap + 2.5, cap + 2.5)
            face = QRadialGradient(c.x() - cap * 0.3, c.y() - cap * 0.35, cap * 1.5)
            face.setColorAt(0, lit.lighter(150))
            face.setColorAt(1, lit.darker(130))
        else:
            face = QRadialGradient(c.x() - cap * 0.3, c.y() - cap * 0.35, cap * 1.5)
            face.setColorAt(0, QColor(lit.red(), lit.green(), lit.blue(), 70).lighter(100))
            face.setColorAt(1, QColor("#1a1b1c"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(face)
        p.drawEllipse(c, cap, cap)


class DialKnob(QWidget):
    """Potentiomètre rétro à plage LINÉAIRE (trim, compresseur, gate, EQ).

    Contrairement à Knob (loi dB des faders), la valeur va de `minimum` à
    `maximum`. Glisser verticalement = régler (Maj = fin), molette idem,
    double-clic = valeur par défaut. N'émet valueChanged que sur action de
    l'utilisateur ; setValue() (état venant du moteur) est ignoré pendant un glisser."""

    valueChanged = Signal(float)

    def __init__(self, minimum: float = -12.0, maximum: float = 12.0, default: float = 0.0,
                 diameter: int = 46, accent: str | None = None, on_dark: bool = False,
                 unit: str = "dB", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min, self._max, self._default = float(minimum), float(maximum), float(default)
        self._value = self._default
        self._diameter = diameter
        self._accent = accent
        self._on_dark = on_dark
        self._unit = unit
        self._dragging = False
        self._press_y = 0.0
        self._press_value = 0.0
        size = diameter + 8
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, value: float) -> None:  # noqa: N802
        if self._dragging:
            return
        value = max(self._min, min(self._max, float(value)))
        if abs(value - self._value) >= 0.005:
            self._value = value
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    def _pos(self) -> float:
        return (self._value - self._min) / (self._max - self._min)

    # --------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        paint_dial(p, QPointF(self.width() / 2, self.height() / 2), float(self._diameter),
                   self._pos(), accent=self._accent, on_dark=self._on_dark)

    # ---------------------------------------------------------- interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._press_y = event.position().y()
            self._press_value = self._value

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        delta = (self._press_y - event.position().y()) / DRAG_PX * (self._max - self._min) * factor
        self._user_set(self._press_value + delta)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_set(self._default)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        step = (self._max - self._min) * WHEEL_STEP * factor
        self._user_set(self._value + event.angleDelta().y() / 120 * step)
        event.accept()

    def _user_set(self, value: float) -> None:
        value = round(max(self._min, min(self._max, value)), 2)
        if abs(value - self._value) < 0.005:
            return
        self._value = value
        self._update_tooltip()
        self.update()
        self.valueChanged.emit(value)

    def _update_tooltip(self) -> None:
        unit = f" {self._unit}" if self._unit else ""
        self.setToolTip(f"{self._value:+.1f}{unit} — double-clic : {self._default:+.1f}")

"""
Composants dessinés du Studio : voyants et touches, bouton de canal, ligne de pas du séquenceur,
petit potard, fader avec indicateur de niveau.

Chaque composant sépare le dessin (paintEvent) de la logique (`press_at`, `drag_to`, `click_primary`…) :
les événements de souris ne font que relayer vers ces méthodes, qui se testent sans souris. Tous les
calculs (où tombe un pas, ce que vaut un glissé) sont dans core/studio_geometry.py.

Les composants n'émettent leurs signaux que sur une action de l'utilisateur : mettre à jour leur état
depuis le logiciel de musique (`set_*`) ne renvoie jamais un ordre au logiciel.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from core import studio_geometry as sg
from ui import studio_style as st
from ui import styles

STEP_HEIGHT = 24
STEP_PREFERRED = 15.0          # largeur souhaitée d'un pas
STEP_VISIBLE_MIN = 8.0         # en dessous, la ligne défile plutôt que de rétrécir encore


def _color(value: str, alpha: float = 1.0) -> QColor:
    return styles.color(value, alpha)


def _shift(event) -> bool:
    return bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)


# --------------------------------------------------------------------------- #
# Voyant et touche à bascule
# --------------------------------------------------------------------------- #
class LedButton(QWidget):
    """Voyant (sans texte) ou petite touche (avec une lettre), à bascule.

    `toggled(bool)` : l'état voulu par l'utilisateur ; `secondaryClicked` : clic droit."""

    toggled = Signal(bool)
    secondaryClicked = Signal()

    def __init__(self, label: str = "", *, color: str = st.GREEN, size: int = 18, tip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = False
        self._label = label
        self._color = color
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            self.setToolTip(tip)

    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool) -> None:
        if bool(on) != self._on:
            self._on = bool(on)
            self.update()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def click_primary(self) -> None:
        self._on = not self._on
        self.update()
        self.toggled.emit(self._on)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_primary()
        elif event.button() == Qt.MouseButton.RightButton:
            self.secondaryClicked.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1.5, 1.5, self.width() - 3.0, self.height() - 3.0)
        if self._label:
            p.setPen(QPen(_color(st.mix(self._color, st.BORDER, 0.4) if self._on else st.BORDER), 1))
            p.setBrush(_color(self._color if self._on else st.INSET))
            p.drawRoundedRect(rect, 4, 4)
            p.setFont(styles.font(max(9, self.height() // 2), QFont.Weight.DemiBold))
            p.setPen(_color("#1b1205" if self._on else st.TEXT_DIM))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._label)
            return
        centre = rect.center()
        radius = min(rect.width(), rect.height()) / 2.0
        if self._on:
            glow = _color(self._color, 0.28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(centre, radius + 1.0, radius + 1.0)
        p.setPen(QPen(_color(st.BORDER), 1))
        p.setBrush(_color(self._color if self._on else st.mix(self._color, st.INSET, 0.82)))
        p.drawEllipse(centre, radius - 2.0, radius - 2.0)
        if self._on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_color("#ffffff", 0.55))
            p.drawEllipse(QPointF(centre.x() - radius * 0.22, centre.y() - radius * 0.28), radius * 0.2, radius * 0.16)


class ActivityLed(QWidget):
    """Petit voyant d'activité (un canal qui joue une note, une piste qui sonne) : non interactif."""

    def __init__(self, *, color: str = st.GREEN, size: int = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._color = color
        self.setFixedSize(size, size)

    def level(self) -> float:
        return self._level

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) >= 0.04:
            self._level = level
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(st.mix(self._color, st.INSET, 1.0 - 0.25 - 0.75 * self._level)))
        p.drawEllipse(QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0))


# --------------------------------------------------------------------------- #
# Bouton de canal
# --------------------------------------------------------------------------- #
class NameButton(QWidget):
    """Le nom d'un canal ou d'une piste, avec sa couleur ; un clic le sélectionne."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._color = st.DEFAULT_CHANNEL
        self._selected = False
        self._dim = False
        self.setMinimumSize(96, 24)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, text: str, color: str, selected: bool, dim: bool = False) -> None:
        if (text, color, selected, dim) != (self._text, self._color, self._selected, self._dim):
            self._text, self._color, self._selected, self._dim = text, color, bool(selected), bool(dim)
            self.setToolTip(text)
            self.update()

    def text(self) -> str:
        return self._text

    def is_selected(self) -> bool:
        return self._selected

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        gradient = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        gradient.setColorAt(0.0, _color(st.mix(st.PANEL_ALT, "#ffffff", 0.07)))
        gradient.setColorAt(1.0, _color(st.PANEL_ALT))
        p.setPen(QPen(_color(st.ACCENT if self._selected else st.BORDER), 1.4 if self._selected else 1.0))
        p.setBrush(QBrush(gradient))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(self._color))
        p.drawRoundedRect(QRectF(rect.left() + 1.5, rect.top() + 3.0, 4.0, rect.height() - 6.0), 2, 2)
        font = styles.font(12, QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(_color(st.TEXT_FAINT if self._dim else st.TEXT))
        room = max(0, self.width() - 22)
        text = QFontMetrics(font).elidedText(self._text, Qt.TextElideMode.ElideRight, room)
        p.drawText(QRectF(12.0, 0.0, room + 4.0, float(self.height())),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


# --------------------------------------------------------------------------- #
# Ligne de pas
# --------------------------------------------------------------------------- #
class StepRow(QWidget):
    """Les pas du séquenceur d'un canal. Un clic allume ou éteint un pas ; un glissé peint plusieurs
    pas d'un geste (le premier pas touché décide : allumer ou éteindre)."""

    stepChanged = Signal(int, bool)            # (pas, allumé) : décidé par l'utilisateur

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps: list[bool] = []
        self._count = 16
        self._color = st.DEFAULT_CHANNEL
        self._playhead = -1
        self._painter: sg.StepPainter | None = None
        self.setFixedHeight(STEP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------ état
    def steps(self) -> list[bool]:
        return list(self._steps[:self._count])

    def count(self) -> int:
        return self._count

    def playhead(self) -> int:
        return self._playhead

    def set_data(self, steps: list[bool], count: int, color: str, playhead: int) -> None:
        count = max(0, int(count))
        cells = ([bool(s) for s in steps] + [False] * count)[:count]
        if self._painter is not None:                     # un geste est en cours : l'écran fait foi
            cells = self._steps[:count] + cells[len(self._steps[:count]):]
        if (cells, count, color, playhead) != (self._steps, self._count, self._color, self._playhead):
            resized = count != self._count
            self._steps, self._count, self._color, self._playhead = cells, count, color, int(playhead)
            if resized:
                self.updateGeometry()
            self.update()

    # ------------------------------------------------------------- géométrie
    def _step_layout(self, min_cell: float = sg.MIN_CELL) -> sg.StepLayout:
        return sg.StepLayout(self.width(), self._count, margin=1.0, min_cell=min_cell)

    def preferred_width(self) -> int:
        return int(sg.StepLayout(0, self._count, margin=1.0, min_cell=STEP_PREFERRED).content_width)

    def minimum_width(self) -> int:
        return int(sg.StepLayout(0, self._count, margin=1.0, min_cell=STEP_VISIBLE_MIN).content_width)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.preferred_width(), STEP_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.minimum_width(), STEP_HEIGHT)

    # ----------------------------------------------- gestes (testables sans souris)
    def press_at(self, x: float) -> None:
        self._painter = sg.StepPainter(self._step_layout(STEP_VISIBLE_MIN), self._steps[:self._count])
        self._apply(self._painter.press(x))

    def move_to(self, x: float) -> None:
        if self._painter is not None:
            self._apply(self._painter.move(x))

    def release(self) -> None:
        self._painter = None

    def _apply(self, changes: list[tuple[int, bool]]) -> None:
        for index, value in changes:
            if index >= len(self._steps):
                self._steps.extend([False] * (index + 1 - len(self._steps)))
            self._steps[index] = value
            self.stepChanged.emit(index, value)
        if changes:
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_at(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.move_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.release()

    # ---------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = st.step_color(self._color)
        edge = st.mix(lit, "#ffffff", 0.35)
        top, height = 2.0, self.height() - 4.0
        for cell in self._step_layout(STEP_VISIBLE_MIN).cells:
            on = cell.index < len(self._steps) and self._steps[cell.index]
            rect = QRectF(cell.left, top, cell.width, height)
            p.setPen(QPen(_color(edge if on else "#000000", 1.0 if on else 0.35), 1))
            p.setBrush(_color(lit if on else st.STEP_OFF[cell.beat % 2]))
            p.drawRoundedRect(rect, 3, 3)
            if cell.index == self._playhead:
                p.setPen(QPen(_color(st.STEP_PLAYHEAD), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0), 3, 3)


# --------------------------------------------------------------------------- #
# Petit potard
# --------------------------------------------------------------------------- #
SWEEP = 270.0                  # étendue de l'arc (degrés), de bas-gauche à bas-droite
SWEEP_START = 225.0            # angle du minimum (convention de Qt : 0° à 3 h, sens trigonométrique)


class MiniKnob(QWidget):
    """Potard de volume ou de panoramique d'un canal : glisser (Maj = fin), molette, double-clic = défaut."""

    valueChanged = Signal(float)

    def __init__(self, *, low: float = 0.0, high: float = 1.0, default: float = 0.0, bipolar: bool = False,
                 color: str = st.ACCENT, size: int = 26, tip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._low, self._high, self._default = low, high, default
        self._bipolar = bipolar
        self._color = color
        self._value = default
        self._dragging = False
        self._press_y = 0.0
        self._press_value = default
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tip = tip
        self._refresh_tip()

    # ------------------------------------------------------------------ état
    def value(self) -> float:
        return self._value

    def default(self) -> float:
        return self._default

    def is_dragging(self) -> bool:
        return self._dragging

    def set_value(self, value: float) -> None:
        """Valeur venue du logiciel ; ignorée pendant que l'utilisateur tourne le potard."""
        if self._dragging:
            return
        value = max(self._low, min(self._high, float(value)))
        if abs(value - self._value) >= 0.002:
            self._value = value
            self._refresh_tip()
            self.update()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def _fraction(self) -> float:
        return (self._value - self._low) / max(1e-9, self._high - self._low)

    def _refresh_tip(self) -> None:
        text = sg.pan_text(self._value) if self._bipolar else sg.percent_text(self._value)
        self.setToolTip(f"{self._tip} : {text}" if self._tip else text)

    # ----------------------------------------------- gestes (testables sans souris)
    def press_at(self, y: float) -> None:
        self._dragging = True
        self._press_y, self._press_value = float(y), self._value

    def drag_to(self, y: float, fine: bool = False) -> None:
        if not self._dragging:
            return
        value = sg.drag_value(self._press_value, self._press_y - float(y), low=self._low, high=self._high, fine=fine)
        self._user_set(sg.snap_center(value, self._low, self._high) if self._bipolar else value)

    def release(self) -> None:
        self._dragging = False

    def turn(self, notches: float, fine: bool = False) -> None:
        self._user_set(sg.wheel_value(self._value, notches, low=self._low, high=self._high, fine=fine))

    def reset(self) -> None:
        self._user_set(self._default)

    def _user_set(self, value: float) -> None:
        value = round(max(self._low, min(self._high, value)), 3)
        if abs(value - self._value) < 0.002:
            return
        self._value = value
        self._refresh_tip()
        self.update()
        self.valueChanged.emit(value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_at(event.position().y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.drag_to(event.position().y(), _shift(event))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.release()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.turn(event.angleDelta().y() / 120.0, _shift(event))
        event.accept()

    # ---------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = float(min(self.width(), self.height()))
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        outer = QRectF(centre.x() - size / 2 + 2.5, centre.y() - size / 2 + 2.5, size - 5.0, size - 5.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_color(st.INSET), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(outer, int(SWEEP_START * 16), int(-SWEEP * 16))
        fraction = self._fraction()
        if self._bipolar:
            start, span = 90.0, -SWEEP * (fraction - 0.5)
        else:
            start, span = SWEEP_START, -SWEEP * fraction
        if abs(span) > 0.5:
            p.setPen(QPen(_color(self._color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(outer, int(start * 16), int(span * 16))
        p.setPen(QPen(_color(st.BORDER), 1))
        gradient = QLinearGradient(QPointF(0, outer.top()), QPointF(0, outer.bottom()))
        gradient.setColorAt(0.0, _color(st.mix(st.PANEL_ALT, "#ffffff", 0.12)))
        gradient.setColorAt(1.0, _color(st.INSET))
        p.setBrush(QBrush(gradient))
        inner = outer.adjusted(3.0, 3.0, -3.0, -3.0)
        p.drawEllipse(inner)
        angle = math.radians(SWEEP_START - SWEEP * fraction)
        radius = inner.width() / 2.0
        p.setPen(QPen(_color(st.TEXT), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(inner.center(), QPointF(inner.center().x() + math.cos(angle) * radius * 0.9,
                                           inner.center().y() - math.sin(angle) * radius * 0.9))


# --------------------------------------------------------------------------- #
# Fader avec indicateur de niveau
# --------------------------------------------------------------------------- #
FADER_WIDTH = 56
FADER_MARGIN = 12              # marge haute et basse de la course
METER_WIDTH = 9
THUMB_H = 22


class StudioFader(QWidget):
    """Fader vertical (0 → 1) avec son indicateur de niveau : glisser (Maj = fin), molette, double-clic = défaut."""

    valueChanged = Signal(float)

    def __init__(self, *, default: float = 0.78, color: str = st.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = default
        self._default = default
        self._level = 0.0
        self._color = color
        self._dragging = False
        self._grab = 0.0
        self._press_y = 0.0
        self._press_value = default
        self.setFixedWidth(FADER_WIDTH)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Glisser pour régler · Maj = réglage fin · double-clic = valeur par défaut")

    # ------------------------------------------------------------------ état
    def value(self) -> float:
        return self._value

    def level(self) -> float:
        return self._level

    def is_dragging(self) -> bool:
        return self._dragging

    def set_value(self, value: float) -> None:
        if self._dragging:
            return
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._value) >= 0.002:
            self._value = value
            self.update()

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if abs(level - self._level) >= 0.02:
            self._level = level
            self.update()

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    # ------------------------------------------------------------- géométrie
    def _track(self) -> tuple[float, float]:
        return float(FADER_MARGIN), float(self.height() - FADER_MARGIN)

    def thumb_y(self) -> float:
        top, bottom = self._track()
        return sg.fader_y(self._value, top, bottom)

    # ----------------------------------------------- gestes (testables sans souris)
    def press_at(self, y: float) -> None:
        """Le fader se saisit par son curseur ; ailleurs sur la course, il saute à l'endroit cliqué."""
        self._dragging = True
        self._press_y = float(y)
        near = abs(float(y) - self.thumb_y()) <= THUMB_H / 2.0
        self._grab = self.thumb_y() - float(y) if near else 0.0
        if not near:
            top, bottom = self._track()
            self._user_set(sg.fader_value(float(y), top, bottom))
        self._press_value = self._value

    def drag_to(self, y: float, fine: bool = False) -> None:
        if not self._dragging:
            return
        top, bottom = self._track()
        if fine:
            value = sg.drag_value(self._press_value, self._press_y - float(y), fine=True,
                                  span=bottom - top)
        else:
            value = sg.fader_value(float(y) + self._grab, top, bottom)
        self._user_set(value)

    def release(self) -> None:
        self._dragging = False

    def turn(self, notches: float, fine: bool = False) -> None:
        self._user_set(sg.wheel_value(self._value, notches, fine=fine))

    def reset(self) -> None:
        self._user_set(self._default)

    def _user_set(self, value: float) -> None:
        value = round(max(0.0, min(1.0, value)), 4)
        if abs(value - self._value) < 0.001:
            return
        self._value = value
        self.update()
        self.valueChanged.emit(value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_at(event.position().y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.drag_to(event.position().y(), _shift(event))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.release()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.turn(event.angleDelta().y() / 120.0, _shift(event))
        event.accept()

    # ---------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = self._track()
        meter = QRectF(6.0, top, float(METER_WIDTH), bottom - top)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(st.INSET))
        p.drawRoundedRect(meter, 2, 2)
        if self._level > 0.005:
            height = (bottom - top) * self._level
            gradient = QLinearGradient(QPointF(0, bottom), QPointF(0, top))
            gradient.setColorAt(0.0, _color(st.GREEN))
            gradient.setColorAt(0.7, _color(st.GREEN))
            gradient.setColorAt(0.85, _color(st.YELLOW))
            gradient.setColorAt(1.0, _color(st.RED))
            p.setBrush(QBrush(gradient))
            p.drawRoundedRect(QRectF(meter.left(), bottom - height, meter.width(), height), 2, 2)
        rail_x = meter.right() + 16.0
        p.setPen(QPen(_color(st.INSET), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(rail_x, top), QPointF(rail_x, bottom))
        y = self.thumb_y()
        p.setPen(QPen(_color(self._color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(rail_x, y), QPointF(rail_x, bottom))
        thumb = QRectF(rail_x - 12.0, y - THUMB_H / 2.0, 24.0, float(THUMB_H))
        gradient = QLinearGradient(QPointF(0, thumb.top()), QPointF(0, thumb.bottom()))
        gradient.setColorAt(0.0, _color(st.mix(st.PANEL_ALT, "#ffffff", 0.2)))
        gradient.setColorAt(1.0, _color(st.PANEL))
        p.setPen(QPen(_color(st.mix(st.BORDER, "#ffffff", 0.15)), 1))
        p.setBrush(QBrush(gradient))
        p.drawRoundedRect(thumb, 4, 4)
        p.setPen(QPen(_color(self._color if not self._dragging else "#ffffff"), 2))
        p.drawLine(QPointF(thumb.left() + 4.0, thumb.center().y()), QPointF(thumb.right() - 4.0, thumb.center().y()))


# --------------------------------------------------------------------------- #
# Touches de transport
# --------------------------------------------------------------------------- #
TRANSPORT_KINDS = ("rewind", "play", "stop", "record")


class TransportButton(QWidget):
    """Touche de transport dessinée (retour au début, lecture, arrêt, enregistrement) : pas de symbole
    de police qui manquerait sur un autre PC. Allumée quand le logiciel est dans cet état."""

    clicked = Signal()

    def __init__(self, kind: str, *, tip: str = "", lit_color: str = st.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in TRANSPORT_KINDS:
            raise ValueError(f"touche de transport inconnue : {kind}")
        self._kind = kind
        self._lit = False
        self._lit_color = lit_color
        self._hover = False
        self._down = False
        self.setFixedSize(42, 32)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            self.setToolTip(tip)

    @property
    def kind(self) -> str:
        return self._kind

    def is_lit(self) -> bool:
        return self._lit

    def set_lit(self, lit: bool) -> None:
        if bool(lit) != self._lit:
            self._lit = bool(lit)
            self.update()

    def click(self) -> None:
        if self.isEnabled():
            self.clicked.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._down = True
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._down:
            self._down = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.click()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._down = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        base = self._lit_color if self._lit else st.PANEL_ALT
        if self._down:
            base = st.mix(base, "#000000", 0.25)
        elif self._hover and not self._lit:
            base = st.mix(base, "#ffffff", 0.08)
        p.setPen(QPen(_color(st.mix(base, "#ffffff", 0.25) if self._lit else st.BORDER), 1))
        p.setBrush(_color(base))
        p.drawRoundedRect(rect, 5, 5)
        ink = "#1b1205" if self._lit else (st.TEXT if self.isEnabled() else st.TEXT_FAINT)
        if self._kind == "record" and not self._lit:
            ink = st.RED if self.isEnabled() else st.TEXT_FAINT
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(ink))
        centre = rect.center()
        size = min(rect.width(), rect.height()) * 0.38
        if self._kind == "play":
            p.drawPolygon(QPolygonF([QPointF(centre.x() - size * 0.7, centre.y() - size),
                                     QPointF(centre.x() + size * 0.95, centre.y()),
                                     QPointF(centre.x() - size * 0.7, centre.y() + size)]))
        elif self._kind == "stop":
            p.drawRoundedRect(QRectF(centre.x() - size * 0.85, centre.y() - size * 0.85, size * 1.7, size * 1.7), 2, 2)
        elif self._kind == "record":
            p.drawEllipse(centre, size * 0.95, size * 0.95)
        else:                                                  # rewind : une barre et une pointe vers la gauche
            p.drawRect(QRectF(centre.x() - size * 0.95, centre.y() - size, size * 0.3, size * 2.0))
            p.drawPolygon(QPolygonF([QPointF(centre.x() + size * 0.95, centre.y() - size),
                                     QPointF(centre.x() + size * 0.95, centre.y() + size),
                                     QPointF(centre.x() - size * 0.55, centre.y())]))

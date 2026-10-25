"""
Fader vertical custom (paintEvent, antialiasé) avec échelle dB.

La loi de course (dB → position) est relevée sur la maquette ; elle est
partagée avec les VU-mètres (graduations alignées) et les knobs.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles

# Même plage que Voicemeeter (Strip.Gain, GainLayer, Bus.Gain : -60 à +12 dB)
FADER_MIN_DB = -60.0
FADER_MAX_DB = 12.0
SCALE_MARKS = (12, 6, 0, -6, -12, -24, -48)
# (dB, position depuis le haut de la course, 0 → 1) : la loi relevée sur la
# maquette (0 → -60 dB) occupe les 85 % du bas, la réserve 0 → +12 dB le haut
_HEADROOM = 0.15
TAPER = ((12.0, 0.0), (6.0, _HEADROOM / 2),
         *((db, _HEADROOM + (1 - _HEADROOM) * pos)
           for db, pos in ((0.0, 0.0), (-6.0, 0.152), (-12.0, 0.278), (-24.0, 0.479),
                           (-48.0, 0.729), (-60.0, 1.0))))

THUMB_W, THUMB_H = 22, 22
TRACK_MARGIN = THUMB_H // 2 + 1   # marge haute/basse de la course (à reprendre dans les VU)
SCALE_W = 24                      # colonne des graduations
WIDTH = SCALE_W + 6 + THUMB_W + 2
DRAG_FINE = 0.25                  # Maj enfoncée : déplacement 4× plus fin
WHEEL_STEP_DB = 0.5


def db_to_ratio(db: float) -> float:
    """dB → position dans la course (0 = haut / +12 dB, 1 = bas / -60 dB)."""
    db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
    for (db_hi, pos_hi), (db_lo, pos_lo) in zip(TAPER, TAPER[1:]):
        if db >= db_lo:
            return pos_hi + (db_hi - db) / (db_hi - db_lo) * (pos_lo - pos_hi)
    return 1.0


def ratio_to_db(ratio: float) -> float:
    ratio = max(0.0, min(1.0, ratio))
    for (db_hi, pos_hi), (db_lo, pos_lo) in zip(TAPER, TAPER[1:]):
        if ratio <= pos_lo:
            return db_hi + (ratio - pos_hi) / (pos_lo - pos_hi) * (db_lo - db_hi)
    return FADER_MIN_DB


def format_db(db: float) -> str:
    """Valeur affichée comme dans Voicemeeter : une décimale, bornée à -60 / +12."""
    db = max(FADER_MIN_DB, min(FADER_MAX_DB, db))
    text = f"{db:.1f}"
    return "0.0" if text == "-0.0" else text


class Fader(QWidget):
    """Fader de gain. Glisser = régler (Maj = fin), molette = ±0,5 dB,
    double-clic = 0 dB. N'émet valueChanged que sur action de l'utilisateur."""

    valueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._muted = False
        self._dragging = False
        self._last_y = 0.0
        self._drag_ratio = 0.0
        self.setFixedWidth(WIDTH)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Glisser pour régler · Maj = réglage fin · double-clic = 0 dB")

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._value

    def setValue(self, db: float) -> None:  # noqa: N802 (style Qt)
        """Valeur venant de Voicemeeter ; ignorée pendant que l'utilisateur glisse."""
        if self._dragging or abs(db - self._value) < 0.005:
            return
        self._value = db
        self.update()

    def setMuted(self, muted: bool) -> None:  # noqa: N802
        self._muted = muted
        self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    # ------------------------------------------------------------- géométrie
    def _track(self) -> tuple[float, float]:
        return float(TRACK_MARGIN), float(self.height() - TRACK_MARGIN)

    def _rail_x(self) -> float:
        return SCALE_W + 6 + THUMB_W / 2

    def _y_for(self, db: float) -> float:
        top, bottom = self._track()
        return top + db_to_ratio(db) * (bottom - top)

    def _ratio_at(self, y: float) -> float:
        top, bottom = self._track()
        return (y - top) / max(1.0, bottom - top)

    # -------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = self._track()
        rail_x = self._rail_x()

        # Graduations
        p.setFont(styles.font(8, QFont.Weight.Medium))
        for mark in SCALE_MARKS:
            y = self._y_for(mark)
            label_color = styles.TEXT_SECONDARY if mark == 0 else styles.TEXT_MUTED
            p.setPen(styles.color(label_color, 0.9 if mark == 0 else 0.75))
            p.drawText(QRectF(0, y - 6, SCALE_W - 5, 12),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"+{mark}" if mark > 0 else str(mark))
            p.setPen(QPen(styles.color(styles.GREEN_SCALE), 1))
            p.drawLine(QPointF(SCALE_W - 3, y), QPointF(SCALE_W, y))

        # Rail
        rail_color = "#4b5263" if self._muted else "#5b6577"
        p.setPen(QPen(styles.color(rail_color, 0.85), 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(rail_x, top), QPointF(rail_x, bottom))

        # Remplissage accent sous le curseur (retour visuel de la position)
        cy = self._y_for(self._value)
        if not self._muted:
            p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.9), 2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rail_x, cy), QPointF(rail_x, bottom))

        # Curseur, selon le style choisi (styles.FADER_STYLE)
        style = styles.FADER_STYLE
        if style == "rond":
            thumb, radius = QRectF(rail_x - 9, cy - 9, 18, 18), 9.0
        elif style == "fin":
            thumb, radius = QRectF(rail_x - THUMB_W / 2, cy - 4, THUMB_W, 8), 3.0
        elif style == "vintage":
            thumb, radius = QRectF(rail_x - THUMB_W / 2 + 1, cy - THUMB_H / 2, THUMB_W - 2, THUMB_H), 3.0
        else:
            thumb, radius = QRectF(rail_x - THUMB_W / 2, cy - THUMB_H / 2, THUMB_W, THUMB_H), 6.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.BG_BLACK, 0.45))
        p.drawRoundedRect(thumb.translated(0, 1.5), radius, radius)
        gradient = QLinearGradient(thumb.topLeft(), thumb.bottomLeft())
        if self._muted:
            gradient.setColorAt(0, QColor("#9aa19d"))
            gradient.setColorAt(1, QColor("#6f7672"))
        elif style == "vintage":
            gradient.setColorAt(0, QColor("#d9d4c7"))
            gradient.setColorAt(0.5, QColor("#a8a394"))
            gradient.setColorAt(1, QColor("#7d7869"))
        elif style == "fin":
            gradient.setColorAt(0, QColor(styles.GREEN_BRIGHT))
            gradient.setColorAt(1, QColor(styles.GREEN_NEON))
        else:
            gradient.setColorAt(0, QColor(styles.WHITE))
            gradient.setColorAt(1, QColor("#d3d8de"))
        p.setBrush(gradient)
        p.setPen(QPen(styles.color(styles.WHITE, 0.35), 1))
        p.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if self._dragging and not self._muted:   # halo pendant la saisie
            p.setPen(QPen(styles.color(styles.GREEN_NEON, 0.8), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(thumb.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1, radius + 1)
        index = styles.TEXT_MUTED if self._muted else styles.GREEN_NEON
        if style == "vintage":                   # stries + index central
            p.setPen(QPen(styles.color("#3b382f", 0.6), 1))
            for dy in (-6, -3, 3, 6):
                p.drawLine(QPointF(thumb.left() + 3, cy + dy), QPointF(thumb.right() - 3, cy + dy))
            p.setPen(QPen(QColor(index), 2))
            p.drawLine(QPointF(thumb.left() + 2, cy), QPointF(thumb.right() - 2, cy))
        elif style == "rond":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(index))
            p.drawEllipse(QPointF(rail_x, cy), 3, 3)
        elif style != "fin":
            p.setPen(QPen(QColor(index), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(rail_x - 6, cy), QPointF(rail_x + 6, cy))

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        y = event.position().y()
        if abs(y - self._y_for(self._value)) > THUMB_H / 2 + 2:
            # Clic sur la course : le curseur saute sous la souris
            self._apply_ratio(self._ratio_at(y))
        self._drag_ratio = db_to_ratio(self._value)
        self._last_y = y
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        y = event.position().y()
        factor = DRAG_FINE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        top, bottom = self._track()
        self._drag_ratio += (y - self._last_y) / max(1.0, bottom - top) * factor
        self._drag_ratio = max(0.0, min(1.0, self._drag_ratio))
        self._last_y = y
        self._apply_ratio(self._drag_ratio)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_set(0.0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else WHEEL_STEP_DB
        base = min(self._value, FADER_MAX_DB)
        self._user_set(max(FADER_MIN_DB, min(FADER_MAX_DB, base + steps * step)))
        event.accept()

    def _apply_ratio(self, ratio: float) -> None:
        self._user_set(round(ratio_to_db(ratio), 1))

    def _user_set(self, db: float) -> None:
        if abs(db - self._value) < 0.005:
            return
        self._value = db
        self.update()
        self.valueChanged.emit(db)


class HardwareFader(Fader):
    """Fader de console rétro posé sur une tranche crème : graduations à
    l'encre, fente sombre, capuchon coloré (orange, bleu, vert…) strié.

    Même comportement, même géométrie et mêmes signaux que Fader (glisser,
    Maj = fin, molette, double-clic = 0 dB) : seul le dessin change."""

    CAP_H = THUMB_H + 4

    def __init__(self, cap_color: str = "#f08a24", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cap_color = cap_color

    def setCapColor(self, color: str) -> None:  # noqa: N802
        self._cap_color = color
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = self._track()
        rail_x = self._rail_x()

        # Graduations à l'encre
        p.setFont(styles.font(8, QFont.Weight.Medium))
        for mark in SCALE_MARKS:
            y = self._y_for(mark)
            p.setPen(QColor(styles.INK if mark == 0 else styles.INK_SOFT))
            p.drawText(QRectF(0, y - 6, SCALE_W - 5, 12),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"+{mark}" if mark > 0 else str(mark))
            p.setPen(QPen(QColor(styles.INK_SOFT), 1))
            p.drawLine(QPointF(SCALE_W - 3, y), QPointF(SCALE_W, y))

        # Fente (creusée) et son reflet
        slot = QRectF(rail_x - 2.5, top - 2, 5, bottom - top + 4)
        p.setPen(QPen(QColor("#0b0b0c"), 1))
        p.setBrush(QColor("#1b1b1d"))
        p.drawRoundedRect(slot, 2.5, 2.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRect(QRectF(rail_x + 1.2, top, 0.8, bottom - top))

        # Capuchon coloré
        cy = self._y_for(self._value)
        cap = QRectF(rail_x - THUMB_W / 2, cy - self.CAP_H / 2, THUMB_W, self.CAP_H)
        base = QColor("#8d8c86" if self._muted else self._cap_color)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawRoundedRect(cap.translated(0, 2), 3, 3)
        gradient = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        gradient.setColorAt(0.0, base.lighter(128))
        gradient.setColorAt(0.5, base)
        gradient.setColorAt(1.0, base.darker(135))
        p.setBrush(gradient)
        p.setPen(QPen(QColor("#141414"), 1))
        p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        # Stries de préhension et index central blanc
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        for dy in (-8, -5, 5, 8):
            p.drawLine(QPointF(cap.left() + 3, cy + dy), QPointF(cap.right() - 3, cy + dy))
        p.setPen(QPen(QColor(styles.WHITE), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cap.left() + 3, cy), QPointF(cap.right() - 3, cy))
        if self._dragging and not self._muted:       # halo pendant la saisie
            p.setPen(QPen(QColor(styles.INK), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cap.adjusted(-1.5, -1.5, 1.5, 1.5), 4, 4)

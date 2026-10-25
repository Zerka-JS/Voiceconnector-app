"""
Panoramique large : un curseur horizontal sur toute la largeur de la tranche (disposition
FL Studio / MAO), plus précis que le petit carré de l'Intellipan.

Même convention que le carré en mode « Position » : x va de -0,5 (gauche) à +0,5 (droite), 0 au
centre. Glisser ou cliquer = régler, molette = pas fin (Maj : encore plus fin), double-clic ou
clic droit = retour au centre. `moved` n'est émis que sur une action de l'utilisateur ; la valeur
lue dans le moteur (`set_value`) est ignorée pendant un glisser.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from core.mixer_model import PAD_X_RANGE
from ui import styles

WIDE_PAN_HEIGHT = 38
MARGIN_X = 11.0              # marge de chaque côté de la piste (place des repères G / D)
TRACK_Y = 26.0               # ordonnée de la piste
HANDLE_W, HANDLE_H = 9.0, 17.0
WHEEL_STEP, FINE_STEP = 0.02, 0.004
HALF_RANGE = PAD_X_RANGE[1]


def pan_text(x: float) -> str:
    """Position lisible : « C » au centre, sinon « G 40 » / « D 25 » (en pour cent de la course)."""
    percent = round(abs(x) / HALF_RANGE * 100)
    if percent == 0:
        return "C"
    return f"{'G' if x < 0 else 'D'} {percent}"


class WidePan(QWidget):
    moved = Signal(float)                    # nouvelle position x, choisie par l'utilisateur

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._x = 0.0
        self._dragging = False
        self.setFixedHeight(WIDE_PAN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_tooltip()

    # ------------------------------------------------------------------ API
    def value(self) -> float:
        return self._x

    def set_value(self, x: float) -> None:
        """Position lue dans le moteur (ignorée pendant un glisser, sinon le curseur sauterait)."""
        if self._dragging:
            return
        x = self._clamp(x)
        if x != self._x:
            self._x = x
            self._update_tooltip()
            self.update()

    def isInteracting(self) -> bool:  # noqa: N802
        return self._dragging

    def reset(self) -> None:
        """Retour au centre."""
        self._set_by_user(0.0)

    # ------------------------------------------------------------ géométrie
    @staticmethod
    def _clamp(x: float) -> float:
        return round(max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], float(x))), 3)

    def _track(self) -> tuple[float, float]:
        return MARGIN_X, max(MARGIN_X + 1.0, self.width() - MARGIN_X)

    def _x_to_px(self, x: float) -> float:
        left, right = self._track()
        return left + (x - PAD_X_RANGE[0]) / (PAD_X_RANGE[1] - PAD_X_RANGE[0]) * (right - left)

    def _px_to_x(self, px: float) -> float:
        left, right = self._track()
        fraction = (px - left) / max(1.0, right - left)
        x = PAD_X_RANGE[0] + fraction * (PAD_X_RANGE[1] - PAD_X_RANGE[0])
        return 0.0 if abs(x) < 0.012 else self._clamp(x)        # aimant au centre

    # --------------------------------------------------------------- souris
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._dragging = False
            menu = QMenu(self)
            menu.addAction("Centrer", self.reset)
            menu.exec(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set_by_user(self._px_to_x(event.position().x()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_by_user(self._px_to_x(event.position().x()))
        else:                                # relâchement jamais reçu (menu ouvert) : fin du glisser
            self._dragging = False
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset()

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._set_by_user(self._clamp(self._x + steps * (FINE_STEP if fine else WHEEL_STEP)))
        event.accept()

    def _set_by_user(self, x: float) -> None:
        x = self._clamp(x)
        if x == self._x:
            return
        self._x = x
        self._update_tooltip()
        self.update()
        self.moved.emit(x)

    def _update_tooltip(self) -> None:
        self.setToolTip(f"Panoramique : {pan_text(self._x)}\n"
                        "Glisser pour régler, molette = pas fin (Maj : plus fin),\n"
                        "double-clic ou clic droit = centre.")

    # --------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        whole = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        background = QLinearGradient(0, 0, 0, self.height())
        background.setColorAt(0, QColor(styles.BG_DEEP))
        background.setColorAt(1, QColor(styles.BG_SECTION))
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.setBrush(background)
        p.drawRoundedRect(whole, 7, 7)

        # En-tête : nom de la commande à gauche, position à droite
        p.setFont(styles.font(8, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_SECONDARY))
        p.drawText(QRectF(7, 1, self.width() - 14, 13),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "PAN")
        p.setPen(QColor(styles.GREEN_NEON if self._x else styles.TEXT_MUTED))
        p.drawText(QRectF(7, 1, self.width() - 14, 13),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pan_text(self._x))

        # Piste, repère central, repères G / D
        left, right = self._track()
        centre = self._x_to_px(0.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.GREEN_DIM, 0.9))
        p.drawRoundedRect(QRectF(left, TRACK_Y - 2, right - left, 4), 2, 2)
        p.setPen(QPen(styles.color(styles.GREEN_DIM, 1.0), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(centre, TRACK_Y - 8), QPointF(centre, TRACK_Y + 8))
        p.setFont(styles.font(7, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_MUTED))
        p.drawText(QRectF(1, TRACK_Y - 6, MARGIN_X - 1, 12), Qt.AlignmentFlag.AlignCenter, "G")
        p.drawText(QRectF(self.width() - MARGIN_X, TRACK_Y - 6, MARGIN_X - 1, 12),
                   Qt.AlignmentFlag.AlignCenter, "D")

        # Partie active : du centre à la poignée
        handle_x = self._x_to_px(self._x)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.GREEN_NEON, 0.85))
        p.drawRoundedRect(QRectF(min(centre, handle_x), TRACK_Y - 2, abs(handle_x - centre), 4), 2, 2)

        # Poignée : le même petit cube rouge que l'Intellipan
        glow = 7.0 if self._dragging else 4.0
        p.setBrush(styles.color(styles.RED_MUTE, 0.22))
        p.drawRoundedRect(QRectF(handle_x - HANDLE_W / 2 - glow / 2, TRACK_Y - HANDLE_H / 2 - glow / 2,
                                 HANDLE_W + glow, HANDLE_H + glow), 4, 4)
        cube = QRectF(handle_x - HANDLE_W / 2, TRACK_Y - HANDLE_H / 2, HANDLE_W, HANDLE_H)
        face = QLinearGradient(cube.topLeft(), cube.bottomRight())
        face.setColorAt(0, QColor("#ff7a7a"))
        face.setColorAt(0.45, QColor(styles.RED_MUTE))
        face.setColorAt(1, QColor("#a8121b"))
        p.setPen(QPen(QColor("#3d0508"), 1))
        p.setBrush(face)
        p.drawRoundedRect(cube, 2.2, 2.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(styles.color(styles.WHITE, 0.55))
        p.drawRoundedRect(QRectF(cube.left() + 2, cube.top() + 2, 3.2, 2.2), 1, 1)

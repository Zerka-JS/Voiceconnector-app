"""
Widgets « matériel » du design VoiceConnector : châssis et joues en bois,
logo, matrice de routage rétro-éclairée, VU-mètre à aiguille, afficheur de
spectre, magnéto à cassette, icône de source.

Tous sont dessinés à la main (paintEvent) et lisent leurs couleurs dans
`styles`, comme les knobs et faders existants.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from core import config
from ui import styles

CHEEK_W = 30                 # largeur des joues en bois (gauche et droite)
LOGO_HEIGHT = 84


# --------------------------------------------------------------------------- #
# Châssis
# --------------------------------------------------------------------------- #
def paint_chassis(p: QPainter, width: int, height: int) -> None:
    """Châssis métallique noir mat brossé, joues latérales en bois clair,
    vis aux coins. Peint dans un cache par Background (pas à chaque frame)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    metal = QLinearGradient(0, 0, 0, height)
    metal.setColorAt(0, QColor(styles.CHASSIS_TOP))
    metal.setColorAt(1, QColor(styles.CHASSIS_BOTTOM))
    p.fillRect(0, 0, width, height, metal)

    # Brossage : fines lignes horizontales très peu contrastées
    rng = random.Random(7)
    for y in range(0, height, 2):
        p.setPen(QPen(QColor(255, 255, 255, rng.choice((0, 0, 3, 5, 8))), 1))
        p.drawLine(CHEEK_W, y, width - CHEEK_W, y)

    for left in (True, False):
        x = 0 if left else width - CHEEK_W
        wood = QLinearGradient(x, 0, x + CHEEK_W, 0)
        wood.setColorAt(0, QColor(styles.WOOD_LIGHT if left else styles.WOOD_DARK))
        wood.setColorAt(1, QColor(styles.WOOD_DARK if left else styles.WOOD_LIGHT))
        p.fillRect(x, 0, CHEEK_W, height, wood)
        # Veinage
        for _ in range(26):
            gx = x + rng.uniform(2, CHEEK_W - 2)
            p.setPen(QPen(QColor(90, 60, 20, rng.randint(18, 46)), rng.choice((1, 1, 2))))
            wobble = rng.uniform(-2, 2)
            p.drawLine(QPointF(gx, 0), QPointF(gx + wobble, height))
        # Arête côté châssis
        edge_x = x + CHEEK_W - 1 if left else x
        p.setPen(QPen(QColor(0, 0, 0, 140), 2))
        p.drawLine(edge_x, 0, edge_x, height)
        # Vis
        for sy in (16, height - 16):
            c = QPointF(x + CHEEK_W / 2, sy)
            p.setPen(QPen(QColor("#2a2118"), 1))
            p.setBrush(QColor("#8d8c86"))
            p.drawEllipse(c, 5, 5)
            p.setPen(QPen(QColor("#2a2118"), 1.4))
            p.drawLine(c + QPointF(-3, 0.8), c + QPointF(3, -0.8))


class LogoLabel(QLabel):
    """Logo VoiceConnector (assets/voiceconnector_logo.png) ; repli : le nom en texte."""

    def __init__(self, height: int = LOGO_HEIGHT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logo")
        self.setStyleSheet("background: transparent;")
        pixmap = QPixmap(str(config.resource_path("assets/voiceconnector_logo.png")))
        if pixmap.isNull():
            self.setText("VOICECONNECTOR — VOICE & AUDIO CONNECTIVITY SOFTWARE")
            self.setObjectName("masterTitle")
        else:
            self.setPixmap(pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation))
        self.setToolTip("VoiceConnector — voice & audio connectivity software")


# --------------------------------------------------------------------------- #
# Icône de source (micro / sortie virtuelle)
# --------------------------------------------------------------------------- #
class SourceIcon(QWidget):
    """Petite icône d'entrée : micro (entrée matérielle) ou casque (virtuelle)."""

    def __init__(self, physical: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._physical = physical
        self.setFixedSize(22, 22)
        self.setToolTip("Entrée matérielle" if physical else "Entrée virtuelle")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = QColor(styles.INK)
        p.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx = self.width() / 2
        if self._physical:
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(cx - 3, 2, 6, 10), 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 6, 5, 12, 10), 200 * 16, 140 * 16)
            p.drawLine(QPointF(cx, 15), QPointF(cx, 19))
            p.drawLine(QPointF(cx - 3.5, 19), QPointF(cx + 3.5, 19))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 8, 3, 16, 16), 0, 180 * 16)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(cx - 9, 11, 4, 7), 1.5, 1.5)
            p.drawRoundedRect(QRectF(cx + 5, 11, 4, 7), 1.5, 1.5)


# --------------------------------------------------------------------------- #
# Matrice de routage
# --------------------------------------------------------------------------- #
class LedMatrix(QWidget):
    """Grille d'affectation entrées × sorties, cases carrées rétro-éclairées.

    Colonnes = strips (entrées), lignes = routes vers les bus (« A1-OUT »…).
    Un clic bascule le routage (même signal que les boutons de la strip :
    routeToggled(strip, route, actif)). Orange = bus A, vert = B1, bleu = B2,
    jaune = B3. L'état vient du moteur via set_params()."""

    routeToggled = Signal(int, int, bool)

    CELL = 20
    GAP = 3
    LABEL_W = 58
    HEADER_H = 74
    PAD = 6

    def __init__(self, strip_names: list[str], route_buses: list[str],
                 route_labels: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._names = list(strip_names)
        self._buses = [str(b) for b in route_buses]
        self._labels = list(route_labels) if route_labels else list(self._buses)
        self._state = [[False] * len(self._buses) for _ in self._names]
        self._hover: tuple[int, int] | None = None
        self.setMouseTracking(True)
        width = self.LABEL_W + len(self._names) * (self.CELL + self.GAP) + 2 * self.PAD
        height = self.HEADER_H + len(self._buses) * (self.CELL + self.GAP) + 2 * self.PAD
        self.setFixedSize(width, height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------ API
    def state(self, strip: int, route: int) -> bool:
        return self._state[strip][route]

    def set_state(self, strip: int, route: int, on: bool) -> None:
        if self._state[strip][route] != on:
            self._state[strip][route] = on
            self.update()

    def set_params(self, strips) -> None:
        """strips : les StripParams du moteur (leur champ `routes`)."""
        changed = False
        for i, params in enumerate(strips):
            if i >= len(self._state):
                break
            for r, on in enumerate(params.routes):
                if r < len(self._buses) and self._state[i][r] != bool(on):
                    self._state[i][r] = bool(on)
                    changed = True
        if changed:
            self.update()

    def set_strip_name(self, index: int, name: str) -> None:
        if 0 <= index < len(self._names) and self._names[index] != name:
            self._names[index] = name
            self.update()

    # ------------------------------------------------------------ géométrie
    def _cell_rect(self, strip: int, route: int) -> QRectF:
        x = self.PAD + self.LABEL_W + strip * (self.CELL + self.GAP)
        y = self.PAD + self.HEADER_H + route * (self.CELL + self.GAP)
        return QRectF(x, y, self.CELL, self.CELL)

    def _cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        for s in range(len(self._names)):
            for r in range(len(self._buses)):
                if self._cell_rect(s, r).contains(pos):
                    return s, r
        return None

    # --------------------------------------------------------------- souris
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        cell = self._cell_at(event.position())
        if cell != self._hover:
            self._hover = cell
            if cell is not None:
                s, r = cell
                self.setToolTip(f"{self._names[s]} → {self._labels[r]} ({self._buses[r]})")
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            return
        s, r = cell
        self._state[s][r] = not self._state[s][r]
        self.update()
        self.routeToggled.emit(s, r, self._state[s][r])

    # --------------------------------------------------------------- dessin
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = styles.font(8, QFont.Weight.DemiBold)
        p.setFont(small)
        metrics = p.fontMetrics()

        # En-têtes de colonnes : noms des strips, couchés à 90°
        p.setPen(QColor(styles.TEXT_SECONDARY))
        for s, name in enumerate(self._names):
            cell = self._cell_rect(s, 0)
            text = metrics.elidedText(name.upper(), Qt.TextElideMode.ElideRight, self.HEADER_H - 8)
            p.save()
            p.translate(cell.center().x() + 3, self.PAD + self.HEADER_H - 4)
            p.rotate(-90)
            p.drawText(QPointF(0, 0), text)
            p.restore()

        for r, bus in enumerate(self._buses):
            color = QColor(styles.bus_color(bus))
            row = self._cell_rect(0, r)
            # Étiquette de ligne : « A1-OUT » et pastille de la couleur du bus
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(self.PAD + 4, row.center().y()), 2.5, 2.5)
            p.setPen(QColor(styles.TEXT_PRIMARY))
            p.drawText(QRectF(self.PAD + 10, row.top(), self.LABEL_W - 12, row.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{bus}-OUT")
            for s in range(len(self._names)):
                rect = self._cell_rect(s, r)
                on = self._state[s][r]
                if on:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(color.red(), color.green(), color.blue(), 55))
                    p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4, 4)
                    face = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                    face.setColorAt(0, color.lighter(135))
                    face.setColorAt(1, color.darker(120))
                    p.setBrush(face)
                    p.setPen(QPen(color.darker(160), 1))
                else:
                    p.setBrush(QColor(color.red() // 6 + 14, color.green() // 6 + 14,
                                      color.blue() // 6 + 14))
                    p.setPen(QPen(QColor("#050505"), 1))
                p.drawRoundedRect(rect, 3, 3)
                if self._hover == (s, r):
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(styles.color(styles.WHITE, 0.7), 1.2))
                    p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3.5, 3.5)


# --------------------------------------------------------------------------- #
# VU-mètre à aiguille
# --------------------------------------------------------------------------- #
class AnalogVu(QWidget):
    """VU-mètre analogique : cadran crème, échelle -30 → +3 dB (zone rouge
    au-dessus de 0), aiguille à inertie (montée et retombée amorties)."""

    DB_MIN, DB_MAX = -30.0, 3.0
    SWEEP = 90.0                 # degrés d'excursion, centrée sur la verticale

    def __init__(self, label: str = "VU", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._db = self.DB_MIN
        self.setFixedSize(112, 78)
        self.setToolTip(f"VU {label}")

    def set_level(self, db: float) -> None:
        db = max(self.DB_MIN, min(self.DB_MAX, db))
        # Inertie : l'aiguille rattrape la cible à 30 Hz (≈ temps de réponse d'un VU)
        self._db += (db - self._db) * (0.35 if db > self._db else 0.12)
        self.update()

    def _angle(self, db: float) -> float:
        """dB → angle trigo (degrés) : gauche = 135°, droite = 45°."""
        norm = (max(self.DB_MIN, min(self.DB_MAX, db)) - self.DB_MIN) / (self.DB_MAX - self.DB_MIN)
        return 90.0 + self.SWEEP / 2 - norm * self.SWEEP

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        face = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(QPen(QColor("#0a0a0a"), 2))
        gradient = QLinearGradient(face.topLeft(), face.bottomLeft())
        gradient.setColorAt(0, QColor("#f6edc9"))
        gradient.setColorAt(1, QColor("#d8c999"))
        p.setBrush(gradient)
        p.drawRoundedRect(face, 4, 4)

        pivot = QPointF(face.center().x(), face.bottom() + 14)
        radius = face.height() * 0.98
        clip = QPainterPath()
        clip.addRoundedRect(face.adjusted(2, 2, -2, -2), 3, 3)
        p.setClipPath(clip)

        # Arc d'échelle, zone rouge au-dessus de 0 dB
        arc = QRectF(pivot.x() - radius, pivot.y() - radius, 2 * radius, 2 * radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(styles.INK), 1.4))
        p.drawArc(arc, int(self._angle(0.0) * 16), int((self._angle(self.DB_MIN) - self._angle(0.0)) * 16))
        p.setPen(QPen(QColor("#c0392b"), 3.2))
        p.drawArc(arc, int(self._angle(self.DB_MAX) * 16), int((self._angle(0.0) - self._angle(self.DB_MAX)) * 16))

        # Graduations
        p.setFont(styles.font(7, QFont.Weight.DemiBold))
        for db in (-30, -20, -10, -7, -5, -3, 0, 3):
            a = math.radians(self._angle(db))
            d = QPointF(math.cos(a), -math.sin(a))
            major = db in (-30, -20, -10, 0)
            p.setPen(QPen(QColor("#c0392b" if db > 0 else styles.INK), 1.3))
            p.drawLine(pivot + d * (radius - (7 if major else 4)), pivot + d * radius)
            if major:
                p.drawText(QRectF((pivot + d * (radius - 17)).x() - 10,
                                  (pivot + d * (radius - 17)).y() - 6, 20, 12),
                           Qt.AlignmentFlag.AlignCenter, str(db))

        # Aiguille
        a = math.radians(self._angle(self._db))
        tip = pivot + QPointF(math.cos(a), -math.sin(a)) * (radius - 3)
        p.setPen(QPen(QColor("#8a1c12"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(pivot, tip)
        p.setClipping(False)

        # Embase et libellé
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1a1a1a"))
        p.drawRoundedRect(QRectF(face.center().x() - 16, face.bottom() - 7, 32, 8), 3, 3)
        p.setPen(QColor(styles.INK_SOFT))
        p.setFont(styles.font(8, QFont.Weight.Bold))
        p.drawText(QRectF(face.left() + 5, face.top() + 3, 30, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)


# --------------------------------------------------------------------------- #
# Afficheur de spectre
# --------------------------------------------------------------------------- #
class SpectrumDisplay(QWidget):
    """Barres de spectre. Le moteur ne fournit AUCUN échantillon audio : par
    défaut les barres sont donc une forme indicative (niveau master × pente
    spectrale fixe), pas une vraie analyse FFT. Pour un vrai spectre, appeler
    set_bands() avec des valeurs 0…1 issues d'une FFT."""

    BANDS = 28
    FALL = 0.045                  # retombée par appel (~30 Hz)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shown = [0.0] * self.BANDS
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Spectre indicatif (dérivé du niveau master)")

    def set_levels(self, levels: tuple[float, float]) -> None:
        amplitude = (max(-60.0, min(0.0, max(levels))) + 60.0) / 60.0
        # Pente fixe : plus d'énergie dans les graves que dans les aigus
        self.set_bands([amplitude * (1.0 - 0.7 * (i / (self.BANDS - 1))) ** 1.3
                        for i in range(self.BANDS)])

    def set_bands(self, bands: list[float]) -> None:
        for i in range(self.BANDS):
            target = max(0.0, min(1.0, bands[i] if i < len(bands) else 0.0))
            self._shown[i] = max(target, self._shown[i] - self.FALL)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#050505"), 1))
        p.setBrush(QColor("#0e1011"))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 3, 3)
        inner = QRectF(5, 5, self.width() - 10, self.height() - 10)
        slot = inner.width() / self.BANDS
        p.setPen(Qt.PenStyle.NoPen)
        for i, value in enumerate(self._shown):
            h = max(1.5, value * inner.height())
            tone = 200 + int(30 * value)
            p.setBrush(QColor(tone, tone, tone - 10))
            p.drawRect(QRectF(inner.left() + i * slot + 1, inner.bottom() - h, max(1.0, slot - 2), h))


# --------------------------------------------------------------------------- #
# Magnéto à cassette
# --------------------------------------------------------------------------- #
class Cassette(QWidget):
    """Fenêtre de cassette : deux bobines qui tournent pendant REC / PLAY."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._running = False
        self.setFixedSize(112, 44)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._spin)

    def set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _spin(self) -> None:
        self._angle = (self._angle + 14.0) % 360.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor("#050505"), 1))
        p.setBrush(QColor("#2b2c2f"))
        p.drawRoundedRect(body, 4, 4)
        window = QRectF(14, 8, self.width() - 28, self.height() - 16)
        p.setBrush(QColor("#0c0d0e"))
        p.drawRoundedRect(window, 6, 6)
        for cx in (window.left() + 22, window.right() - 22):
            c = QPointF(cx, window.center().y())
            p.setPen(QPen(QColor("#6b6858"), 1))
            p.setBrush(QColor("#d8d4c4"))
            p.drawEllipse(c, 10, 10)
            p.setBrush(QColor("#17181a"))
            p.drawEllipse(c, 6, 6)
            p.setPen(QPen(QColor("#d8d4c4"), 1.6))
            for k in range(6):
                a = math.radians(self._angle + k * 60)
                p.drawLine(c + QPointF(math.cos(a), math.sin(a)) * 2.5,
                           c + QPointF(math.cos(a), math.sin(a)) * 6)


class TapeDeck(QFrame):
    """Panneau d'enregistrement : cassette, REC / PLAY / STOP / A-B, nom du fichier.

    Ne parle à aucun moteur : il émet des signaux. L'appelant peut remettre les
    touches au repos avec reset() si la demande n'est pas prise en charge."""

    recordToggled = Signal(bool)
    playRequested = Signal()
    stopRequested = Signal()
    abToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("section")
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 6)
        box.setSpacing(6)

        self.cassette = Cassette()
        box.addWidget(self.cassette, 0, Qt.AlignmentFlag.AlignHCenter)

        keys = QHBoxLayout()
        keys.setSpacing(3)
        self.rec = self._key("REC", checkable=True, name="transportRec")
        self.play = self._key("PLAY", checkable=True)
        self.stop = self._key("STOP")
        self.ab = self._key("A-B", checkable=True)
        for key in (self.rec, self.play, self.stop, self.ab):
            keys.addWidget(key)
        box.addLayout(keys)

        self.file_label = QLabel("FILE: —")
        self.file_label.setObjectName("tapeFile")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.file_label)

        self.rec.clicked.connect(self._on_rec)
        self.play.clicked.connect(self._on_play)
        self.stop.clicked.connect(self._on_stop)
        self.ab.toggled.connect(self.abToggled)

    @staticmethod
    def _key(text: str, checkable: bool = False, name: str = "transport") -> QPushButton:
        key = QPushButton(text)
        key.setObjectName(name)
        key.setCheckable(checkable)
        key.setCursor(Qt.CursorShape.PointingHandCursor)
        key.setFixedHeight(22)
        return key

    def set_file(self, name: str) -> None:
        self.file_label.setText(f"FILE: {name}" if name else "FILE: —")

    def reset(self) -> None:
        """Touches au repos, bobines à l'arrêt (sans émettre de signal)."""
        for key in (self.rec, self.play):
            key.blockSignals(True)
            key.setChecked(False)
            key.blockSignals(False)
        self.cassette.set_running(False)

    def _on_rec(self, on: bool) -> None:
        if on:
            self.play.setChecked(False)
        self.cassette.set_running(on)
        self.recordToggled.emit(on)

    def _on_play(self, on: bool) -> None:
        if on:
            self.rec.blockSignals(True)
            self.rec.setChecked(False)
            self.rec.blockSignals(False)
        self.cassette.set_running(on)
        if on:
            self.playRequested.emit()
        else:
            self.stopRequested.emit()

    def _on_stop(self) -> None:
        recording = self.rec.isChecked()
        self.reset()
        if recording:
            self.recordToggled.emit(False)
        self.stopRequested.emit()

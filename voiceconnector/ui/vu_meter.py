"""
VU-mètre stéréo vertical custom (paintEvent, antialiasé) : vert → jaune → rouge.

Même loi de course que le fader (graduations alignées). Balistique simple :
montée instantanée, retombée progressive, maintien du pic, LED de saturation.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import styles
from ui.fader import FADER_MIN_DB, TRACK_MARGIN, db_to_ratio, ratio_to_db

RELEASE_DB_PER_S = 26.0
PEAK_HOLD_S = 1.2
PEAK_FALL_DB_PER_S = 20.0
CLIP_DB = -0.1
CLIP_HOLD_S = 1.5
SEGMENT_PX = 3          # aspect « LED » : une ligne sombre tous les 3 px
PRECISION_PITCH = 3     # VU haute précision : segments plus fins (5 px → 3 px)
SCALE_DB = (0, -6, -12, -24, -48)      # repères de l'échelle du VU haute précision
SCALE_WIDTH = 12        # largeur de la colonne des repères (px)


def _gradient_stop(db: float) -> float:
    """Position d'un niveau dans un dégradé orienté du bas (0) vers le haut (1)."""
    return 1.0 - db_to_ratio(db)


class VuMeter(QWidget):
    def __init__(self, channels: int = 2, bar_width: int = 3, gap: int = 2,
                 margin_top: int = TRACK_MARGIN, margin_bottom: int = TRACK_MARGIN,
                 clip_leds: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._channels = channels
        self._bar_w = bar_width
        self._gap = gap
        self._clip_leds = clip_leds
        self._margin_top = margin_top + (14 if clip_leds else 0)
        self._margin_bottom = margin_bottom
        self._shown = [FADER_MIN_DB] * channels
        self._peaks = [FADER_MIN_DB] * channels
        self._peak_t = [0.0] * channels
        self._clip_until = [0.0] * channels
        self._t = time.monotonic()
        width = channels * bar_width + (channels - 1) * gap + (6 if clip_leds else 0)
        self.setFixedWidth(width)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_levels(self, levels: tuple[float, ...]) -> None:
        now = time.monotonic()
        dt = min(0.25, now - self._t)
        self._t = now
        for i in range(self._channels):
            db = levels[i] if i < len(levels) else FADER_MIN_DB
            self._shown[i] = max(db, self._shown[i] - RELEASE_DB_PER_S * dt, FADER_MIN_DB)
            if db >= self._peaks[i]:
                self._peaks[i], self._peak_t[i] = db, now
            elif now - self._peak_t[i] > PEAK_HOLD_S:
                self._peaks[i] = max(self._shown[i], self._peaks[i] - PEAK_FALL_DB_PER_S * dt)
            if db >= CLIP_DB:
                self._clip_until[i] = now + CLIP_HOLD_S
        self.update()

    def is_clipping(self) -> bool:
        now = time.monotonic()
        return any(until > now for until in self._clip_until)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = float(self._margin_top), float(self.height() - self._margin_bottom)
        height = max(1.0, bottom - top)

        gradient = QLinearGradient(0, bottom, 0, top)
        # Couleur du thème (niveaux normaux) → jaune (zone haute) → rouge (crêtes)
        gradient.setColorAt(0.0, QColor(styles.GREEN_NEON).darker(115))
        gradient.setColorAt(_gradient_stop(-18), QColor(styles.GREEN_NEON))
        gradient.setColorAt(_gradient_stop(-9), QColor(styles.VU_YELLOW))
        gradient.setColorAt(_gradient_stop(-3), QColor(styles.VU_YELLOW))
        gradient.setColorAt(_gradient_stop(-1.5), QColor(styles.VU_RED))
        gradient.setColorAt(1.0, QColor(styles.VU_RED))

        offset = 3 if self._clip_leds else 0
        now = time.monotonic()
        for i in range(self._channels):
            x = offset + i * (self._bar_w + self._gap)
            bar = QRectF(x, top, self._bar_w, height)
            # LED éteintes : le dégradé en très sombre
            p.setOpacity(0.16)
            p.fillRect(bar, gradient)
            # LED allumées
            p.setOpacity(1.0)
            level_y = top + db_to_ratio(self._shown[i]) * height
            if self._shown[i] > FADER_MIN_DB:
                p.fillRect(QRectF(x, level_y, self._bar_w, bottom - level_y), gradient)
            # Pic maintenu
            if self._peaks[i] > FADER_MIN_DB + 1:
                peak_y = top + db_to_ratio(self._peaks[i]) * height
                # Peak hold : trait coloré selon la zone du pic
                if self._peaks[i] >= -3:
                    peak_color = styles.VU_RED
                elif self._peaks[i] >= -9:
                    peak_color = styles.VU_YELLOW
                else:
                    peak_color = styles.TEXT_PRIMARY
                p.fillRect(QRectF(x, peak_y - 1, self._bar_w, 2), QColor(peak_color))
            # Segmentation, selon le style choisi (styles.VU_STYLE)
            p.setPen(Qt.PenStyle.NoPen)
            if styles.VU_STYLE != "continu":
                step, gap = (6, 2) if styles.VU_STYLE == "led" else (SEGMENT_PX, 1)
                shade = styles.color(styles.BG_SECTION, 0.85 if gap > 1 else 0.55)
                y = top + step
                while y < bottom:
                    p.fillRect(QRectF(x, y, self._bar_w, gap), shade)
                    y += step
            # LED de saturation au-dessus de chaque barre
            if self._clip_leds:
                lit = self._clip_until[i] > now
                p.setBrush(QColor(styles.VU_RED if lit else styles.RED_DIM))
                cx = x + self._bar_w / 2
                p.drawEllipse(QRectF(cx - 3.5, top - 13, 7, 7))


class LedVuMeter(VuMeter):
    """VU-mètre à LED de rack : alvéole sombre et segments discrets (vert →
    jaune → rouge) alignés sur l'échelle du fader. Même API que VuMeter
    (set_levels, is_clipping) ; seule la peinture change."""

    WELL_PAD = 3
    SEG_PITCH = 5

    def __init__(self, channels: int = 2, bar_width: int = 6, gap: int = 2,
                 margin_top: int = TRACK_MARGIN, margin_bottom: int = TRACK_MARGIN,
                 clip_leds: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(channels, bar_width, gap, margin_top, margin_bottom, clip_leds, parent)
        self._base_width = (channels * bar_width + (channels - 1) * gap
                            + (6 if clip_leds else 0) + 2 * self.WELL_PAD)
        self._precision = False
        self.setFixedWidth(self._base_width)

    # ------------------------------------------------------- haute précision
    @property
    def precision(self) -> bool:
        return self._precision

    def set_precision(self, on: bool) -> None:
        """Haute précision : segments plus fins et repères en dB (0, -6, -12, -24, -48) sur le côté,
        alignés sur l'échelle du fader. L'appareil prend un peu plus de largeur."""
        on = bool(on)
        if on != self._precision:
            self._precision = on
            self.setFixedWidth(self._base_width + (SCALE_WIDTH if on else 0))
            self.update()

    def _pitch(self) -> int:
        return PRECISION_PITCH if self._precision else self.SEG_PITCH

    def _paint_scale(self, p: QPainter, top: float, bottom: float) -> None:
        """Repères en dB à droite de l'alvéole."""
        height = max(1.0, bottom - top)
        x0 = self._base_width + 1.0
        p.setFont(styles.font(7, QFont.Weight.Medium))
        for db in SCALE_DB:
            y = top + db_to_ratio(db) * height
            p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.9), 1))
            p.drawLine(QPointF(x0, y), QPointF(x0 + 3, y))
            p.setPen(QColor(styles.TEXT_MUTED))
            p.drawText(QRectF(x0 + 3, y - 5, SCALE_WIDTH - 3, 10),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(abs(db)))

    @staticmethod
    def _segment_color(db: float) -> QColor:
        if db >= -3:
            return QColor(styles.VU_RED)
        if db >= -9:
            return QColor(styles.VU_YELLOW)
        return QColor(styles.VU_GREEN)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Alvéole
        p.setPen(QPen(QColor("#050505"), 1))
        p.setBrush(QColor("#0e1011"))
        p.drawRoundedRect(QRectF(0.5, 0.5, self._base_width - 1, self.height() - 1), 3, 3)

        top, bottom = float(self._margin_top), float(self.height() - self._margin_bottom)
        pitch = self._pitch()
        count = max(4, int((bottom - top) // pitch))
        seg_h = pitch - (1.0 if self._precision else 1.5)
        offset = self.WELL_PAD + (3 if self._clip_leds else 0)
        now = time.monotonic()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self._channels):
            x = offset + i * (self._bar_w + self._gap)
            peak = self._peaks[i]
            for k in range(count):
                # Segment k, compté depuis le bas ; db du seuil d'allumage
                y = bottom - (k + 1) * pitch + 1
                db = ratio_to_db(1.0 - (k + 0.5) / count)
                color = self._segment_color(db)
                lit = self._shown[i] >= db
                peak_here = peak > FADER_MIN_DB + 1 and abs(peak - db) <= 60.0 / count
                if lit or peak_here:
                    p.setBrush(color)
                else:
                    color.setAlphaF(0.14)
                    p.setBrush(color)
                p.drawRoundedRect(QRectF(x, y, self._bar_w, seg_h), 1, 1)
            if self._clip_leds:
                lit = self._clip_until[i] > now
                p.setBrush(QColor(styles.VU_RED if lit else styles.RED_DIM))
                p.drawEllipse(QRectF(x + self._bar_w / 2 - 3.5, top - 13, 7, 7))
        if self._precision:
            self._paint_scale(p, top, bottom)


class ClipLed(QWidget):
    """Petite LED rouge (en-tête du fader) : s'allume quand le VU sature."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lit = False
        self.setFixedSize(10, 10)
        self.setToolTip("Saturation")

    def set_lit(self, lit: bool) -> None:
        if lit != self._lit:
            self._lit = lit
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self._lit:
            p.setBrush(styles.color(styles.RED_MUTE, 0.35))
            p.drawEllipse(QRectF(0, 0, 10, 10))
        p.setBrush(QColor(styles.RED_MUTE if self._lit else styles.RED_DIM))
        p.drawEllipse(QRectF(2, 2, 6, 6))

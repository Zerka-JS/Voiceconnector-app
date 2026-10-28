"""
Panneau droit du rack VoiceConnector.

- OutputsPanel : « HARDWARE OUTPUTS » (A1, A2, A3), tranches crème avec VU à
  LED et fader d'atténuation.
- MasterWidget : titre + engrenage (réglages), badge de connexion, projet,
  MUTE ALL / EJECT ALL, bouton MASTER (volume du casque, bus A1) avec son
  interpolation douce, VU à LED + spectre, deux VU à aiguille, magnéto
  (REC / PLAY / STOP / A-B) et section INTELLIPAN (BASS / MID / TREBLE).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ui import styles
from ui.fader import format_db
from ui.fader import HardwareFader
from ui.hardware_widgets import AnalogVu, SpectrumDisplay, TapeDeck
from ui.knob import DialKnob, HardwareKnob
from ui.strip_widget import make_section
from ui.vu_meter import LedVuMeter

MASTER_WIDTH = 300
HEADPHONE_KNOB = 64
EQ_KNOB = 56
EQ_RANGE_DB = 12.0               # BASS / MID / TREBLE : -12 … +12 dB
OUTPUT_LABELS = ("A1", "A2", "A3")
OUTPUT_WIDTH = 74
PULSE_MS = 1500


class GearButton(QAbstractButton):
    """Bouton carré avec une icône d'engrenage dessinée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Réglages")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)

        c = QPointF(self.width() / 2, self.height() / 2)
        outer, inner, teeth = 7.0, 5.2, 8
        path = QPainterPath()
        for i in range(teeth * 2):
            # Alternance dent / creux, chaque segment couvrant 1/16 de tour
            radius = outer if i % 2 == 0 else inner
            for k in (0.0, 1.0):
                a = 2 * math.pi * (i + k * 0.999) / (teeth * 2)
                point = QPointF(c.x() + radius * math.cos(a), c.y() + radius * math.sin(a))
                if i == 0 and k == 0.0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
        path.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(c, 2.3, 2.3)
        ink = QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY)
        p.setPen(QPen(ink, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.drawPath(hole)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class MinimizeButton(QAbstractButton):
    """Bouton carré « – » : réduit VoiceConnector (dans la barre des tâches ou la zone
    de notification, selon les réglages)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Réduire VoiceConnector")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
        p.setPen(QPen(QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY), 1.8,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        y = self.height() / 2 + 3
        p.drawLine(QPointF(8, y), QPointF(self.width() - 8, y))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class ConnectionBadge(QWidget):
    """Pill « REAPER connecté » avec un point qui pulse (opacité 0,5 → 1 en 1,5 s)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ok = False
        self._text = "Connexion…"
        self._opacity = 1.0
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(PULSE_MS)
        self._pulse.setStartValue(0.5)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.5)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

    def set_status(self, ok: bool, text: str, detail: str = "") -> None:
        self._ok, self._text = ok, text
        self.setToolTip(detail or text)
        if ok:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._opacity = 1.0
        self.update()

    def _on_pulse(self, value: float) -> None:
        self._opacity = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BG_DEEP))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        dot = QColor(styles.GREEN_NEON if self._ok else styles.RED_MUTE)
        center = QPointF(12, rect.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        if self._ok:
            p.setBrush(styles.color(styles.GREEN_NEON, 0.25 * self._opacity))
            p.drawEllipse(center, 6, 6)
        dot.setAlphaF(self._opacity)
        p.setBrush(dot)
        p.drawEllipse(center, 3.5, 3.5)

        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_SECONDARY if self._ok else styles.TEXT_PRIMARY))
        text_rect = rect.adjusted(22, 0, -6, 0)
        text = p.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class ProjectButton(QAbstractButton):
    """Pill « Projet : <nom> » : ouvre le choix du projet REAPER."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = "…"
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_project(self, name: str, path: str, tooltip: str | None = None) -> None:
        self._name = name
        self.setToolTip(tooltip or f"Projet REAPER ouvert :\n{path}\n\nClic : changer de projet")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hover = self.underMouse()
        p.setPen(QPen(QColor(styles.GREEN_DIM if hover else styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BG_DEEP))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        # Petite icône de dossier
        p.setPen(QPen(QColor(styles.TEXT_SECONDARY), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        folder = QRectF(8, rect.center().y() - 3.5, 9, 7)
        p.drawRoundedRect(folder, 1.2, 1.2)
        p.drawLine(QPointF(folder.left() + 1, folder.top()), QPointF(folder.left() + 3.5, folder.top() - 1.5))
        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.TEXT_PRIMARY if hover else styles.TEXT_SECONDARY))
        text_rect = rect.adjusted(22, 0, -8, 0)
        text = p.fontMetrics().elidedText(self._name, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class UpdateButton(QAbstractButton):
    """Pill accent « ⬆ Mise à jour x.y » : visible quand une nouvelle version existe."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def text(self) -> str:  # noqa: D401 - API Qt
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        accent = QColor(styles.GREEN_BRIGHT if self.underMouse() else styles.GREEN_NEON)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        ink = QColor(styles.ON_ACCENT_DARK)
        # Flèche vers le haut
        p.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, cy = 13.0, rect.center().y()
        p.drawLine(QPointF(cx, cy + 4), QPointF(cx, cy - 4))
        p.drawLine(QPointF(cx - 3.5, cy - 0.5), QPointF(cx, cy - 4))
        p.drawLine(QPointF(cx + 3.5, cy - 0.5), QPointF(cx, cy - 4))
        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(ink)
        text_rect = rect.adjusted(22, 0, -8, 0)
        text = p.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight,
                                          int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class OutputStrip(QFrame):
    """Une sortie matérielle (A1, A2, A3) : titre, VU à LED, fader d'atténuation."""

    gainChanged = Signal(int, float)          # (numéro de sortie 0…2, dB)

    def __init__(self, index: int, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.setObjectName("outputStrip")
        self.setFixedWidth(OUTPUT_WIDTH)
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 6, 4, 4)
        box.setSpacing(6)
        self.title = QLabel(label)
        self.title.setObjectName("stripName")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.title)
        self.value_label = QLabel()
        self.value_label.setObjectName("faderValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.value_label)
        body = QHBoxLayout()
        body.setSpacing(3)
        body.addStretch(1)
        self.fader = HardwareFader("#3a3b3f")
        self.fader.valueChanged.connect(self._on_fader)
        body.addWidget(self.fader)
        self.vu = LedVuMeter(channels=2, bar_width=5, gap=2)
        body.addWidget(self.vu)
        body.addStretch(1)
        box.addLayout(body, 1)
        self._show_value(0.0)

    def set_gain(self, db: float) -> None:
        self.fader.setValue(db)
        if not self.fader.isInteracting():
            self._show_value(db)

    def set_levels(self, levels: tuple[float, float]) -> None:
        self.vu.set_levels(levels)

    def _on_fader(self, db: float) -> None:
        self._show_value(db)
        self.gainChanged.emit(self.index, db)

    def _show_value(self, db: float) -> None:
        self.value_label.setText(f"{format_db(db)} dB")


class OutputsPanel(QFrame):
    """« HARDWARE OUTPUTS » : A1, A2, A3.

    Seule A1 (le casque / monitoring) a un niveau fourni par le moteur ; les
    VU de A2 et A3 s'animent si MixerLevels expose un champ `buses`."""

    gainChanged = Signal(int, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("creamGroup")
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 6, 4, 4)
        box.setSpacing(2)
        title = QLabel("HARDWARE OUTPUTS")
        title.setObjectName("creamTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(title)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.outputs: list[OutputStrip] = []
        for index, label in enumerate(OUTPUT_LABELS):
            strip = OutputStrip(index, label)
            strip.setProperty("last", index == len(OUTPUT_LABELS) - 1)
            strip.gainChanged.connect(self.gainChanged)
            row.addWidget(strip)
            self.outputs.append(strip)
        box.addLayout(row, 1)

    def set_gain(self, index: int, db: float) -> None:
        if 0 <= index < len(self.outputs):
            self.outputs[index].set_gain(db)

    def limit_to(self, count: int) -> None:
        """Mode autonome : une seule sortie (le volume général de Windows) ; les autres sont cachées."""
        count = max(1, min(count, len(self.outputs)))
        for index, strip in enumerate(self.outputs):
            strip.setVisible(index < count)
            strip.setProperty("last", index == count - 1)

    def set_precision(self, on: bool) -> None:
        """VU-mètres haute précision (disposition FL Studio / MAO)."""
        for strip in self.outputs:
            strip.vu.set_precision(on)

    def set_levels(self, master: tuple[float, float], buses=None) -> None:
        """master : niveau du bus casque (A1). buses : [(g, d), …] si le moteur en fournit."""
        self.outputs[0].set_levels(master)
        for index, levels in enumerate(buses or (), start=0):
            if 0 < index < len(self.outputs):
                self.outputs[index].set_levels(levels)


class MasterWidget(QFrame):
    settingsRequested = Signal()
    projectRequested = Signal()
    minimizeRequested = Signal()
    updateRequested = Signal()
    headphoneChanged = Signal(float)
    muteAllRequested = Signal()
    ejectAllRequested = Signal()
    eqChanged = Signal(str, float)        # ("bass" | "mid" | "treble", dB)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("column")
        self.setFixedWidth(MASTER_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        header.setSpacing(4)
        title = QLabel("MASTER")
        title.setObjectName("masterTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.minimize = MinimizeButton()
        self.minimize.clicked.connect(self.minimizeRequested)
        header.addWidget(self.minimize)
        self.gear = GearButton()
        self.gear.clicked.connect(self.settingsRequested)
        header.addWidget(self.gear)
        layout.addLayout(header)

        status = QHBoxLayout()
        status.setSpacing(4)
        self.badge = ConnectionBadge()
        status.addWidget(self.badge, 1)
        self.project = ProjectButton()
        self.project.clicked.connect(self.projectRequested)
        status.addWidget(self.project, 1)
        layout.addLayout(status)
        self.update_button = UpdateButton()
        self.update_button.clicked.connect(self.updateRequested)
        layout.addWidget(self.update_button)

        # MUTE ALL / EJECT ALL
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.mute_all = QPushButton("MUTE ALL")
        self.mute_all.setObjectName("toolMute")
        self.mute_all.setToolTip("Coupe (ou rétablit) toutes les entrées")
        self.mute_all.clicked.connect(self.muteAllRequested)
        self.eject_all = QPushButton("EJECT ALL")
        self.eject_all.setObjectName("toolEject")
        self.eject_all.setToolTip("Déconnecte toutes les entrées de tous les bus")
        self.eject_all.clicked.connect(self.ejectAllRequested)
        for button in (self.mute_all, self.eject_all):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            tools.addWidget(button, 1)
        layout.addLayout(tools)

        # Bouton MASTER (volume du casque) | VU à LED + spectre
        top = QHBoxLayout()
        top.setSpacing(6)
        knob_section = make_section()
        knob_box = QVBoxLayout(knob_section)
        knob_box.setContentsMargins(6, 8, 6, 8)
        knob_box.setSpacing(2)
        self.knob = HardwareKnob(diameter=HEADPHONE_KNOB, checkable=False, smooth=True, on_dark=True)
        self.knob.color_element, self.knob.color_scope = "headphone", styles.MASTER_SCOPE
        self.knob.setToolTip("Volume master (casque) — double-clic : 0 dB")
        self.knob.valueChanged.connect(self._on_knob)
        knob_box.addWidget(self.knob, 0, Qt.AlignmentFlag.AlignHCenter)
        self.value_label = QLabel()
        self.value_label.setObjectName("masterValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        knob_box.addWidget(self.value_label)
        caption = QLabel("MASTER")
        caption.setObjectName("masterLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        knob_box.addWidget(caption)
        top.addWidget(knob_section, 1)

        spectrum_section = make_section()
        spectrum_box = QHBoxLayout(spectrum_section)
        spectrum_box.setContentsMargins(6, 6, 6, 6)
        spectrum_box.setSpacing(4)
        self.vu = LedVuMeter(channels=2, bar_width=6, gap=3, margin_top=4, margin_bottom=4,
                             clip_leds=True)
        self.vu.color_scope = styles.MASTER_SCOPE
        spectrum_box.addWidget(self.vu)
        self.spectrum = SpectrumDisplay()
        spectrum_box.addWidget(self.spectrum, 1)
        top.addWidget(spectrum_section, 1)
        layout.addLayout(top)

        # VU à aiguille (gauche / droite)
        needles = QHBoxLayout()
        needles.setSpacing(6)
        self.vu_left = AnalogVu("L")
        self.vu_right = AnalogVu("R")
        needles.addWidget(self.vu_left)
        needles.addWidget(self.vu_right)
        layout.addLayout(needles)

        # Magnéto
        self.tape = TapeDeck()
        layout.addWidget(self.tape)
        layout.addStretch(1)

        # INTELLIPAN : égaliseur 3 bandes
        eq_section = make_section()
        self.eq_section = eq_section
        eq_box = QVBoxLayout(eq_section)
        eq_box.setContentsMargins(6, 6, 6, 6)
        eq_box.setSpacing(2)
        eq_title = QLabel("INTELLIPAN")
        eq_title.setObjectName("sectionTitle")
        eq_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eq_box.addWidget(eq_title)
        knobs = QHBoxLayout()
        knobs.setSpacing(2)
        self.eq: dict[str, DialKnob] = {}
        for band, text in (("bass", "BASS"), ("mid", "MID"), ("treble", "TREBLE")):
            dial = DialKnob(-EQ_RANGE_DB, EQ_RANGE_DB, 0.0, EQ_KNOB, on_dark=True)
            dial.valueChanged.connect(lambda value, b=band: self.eqChanged.emit(b, value))
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(dial, 0, Qt.AlignmentFlag.AlignHCenter)
            label = QLabel(text)
            label.setObjectName("masterLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.addWidget(label)
            knobs.addLayout(cell)
            self.eq[band] = dial
        eq_box.addLayout(knobs)
        layout.addWidget(eq_section)

        self._show_value(0.0)
        self.apply_colors()

    def apply_colors(self) -> None:
        """Fond du Master dans sa couleur choisie (le reste est dessiné à la main)."""
        panel = styles.element_color("panel", styles.MASTER_SCOPE)
        sheet = styles.panel_style(panel) if panel else ""
        if self.styleSheet() != sheet:
            self.setStyleSheet(sheet)

    def set_headphone(self, db: float) -> None:
        self.knob.setValue(db)
        if not self.knob.isInteracting():
            self._show_value(db)

    def set_levels(self, levels: tuple[float, float]) -> None:
        self.vu.set_levels(levels)
        self.vu_left.set_level(levels[0])
        self.vu_right.set_level(levels[1])
        self.spectrum.set_levels(levels)

    def set_eq(self, band: str, db: float) -> None:
        if band in self.eq:
            self.eq[band].setValue(db)

    def set_voicemeeter_tools(self, visible: bool) -> None:
        """Magnéto, égaliseur Intellipan et « Eject all » n'existent que dans Voicemeeter : le mode
        autonome (B) les cache plutôt que d'afficher des commandes qui n'agissent sur rien."""
        for widget in (self.tape, self.eq_section, self.eject_all):
            widget.setVisible(visible)

    def set_precision(self, on: bool) -> None:
        """VU-mètre haute précision (disposition FL Studio / MAO)."""
        self.vu.set_precision(on)

    def set_status(self, ok: bool, text: str, detail: str = "") -> None:
        self.badge.set_status(ok, text, detail)

    def set_project(self, name: str, path: str, tooltip: str | None = None) -> None:
        self.project.set_project(name, path, tooltip)

    def set_update(self, version: str | None) -> None:
        """Affiche (ou cache) la pill de mise à jour."""
        if version:
            self.update_button.set_text(f"Mise à jour {version}")
            self.update_button.setToolTip(f"VoiceConnector {version} est disponible — clic pour l'installer")
        self.update_button.setVisible(bool(version))

    def _on_knob(self, db: float) -> None:
        self._show_value(db)
        self.headphoneChanged.emit(db)

    def _show_value(self, db: float) -> None:
        self.value_label.setText(f"{format_db(db)} dB")

"""
Une tranche de console VoiceConnector (fond crème, façon rack vintage), de
haut en bas : nom éditable (double-clic), potentiomètre de trim — ou
Compr. / Gate sur les entrées virtuelles —, effets (REAPER), Intellipan (le
carré au petit cube rouge de Voicemeeter), icône de source + Solo / Muet (et
« Apps » : volume par application), fader coloré + VU à LED, routage rapide
(boutons ronds rétro-éclairés).

La tranche ne parle à aucun moteur : elle émet des signaux, la fenêtre
principale les relaie à Voicemeeter / REAPER, et lui renvoie l'état réel.

Les dispositions (Réglages → Mode et thème) changent ce que montre la tranche sans la
reconstruire : `apply_style(style, libellé)` affiche ou masque le panoramique (carré Intellipan,
curseur large ou rien), le trim, le compresseur et le gate, l'égaliseur rapide et sa touche
« Clarté », les effets et le routage, et passe le VU en haute précision. Dans la disposition
Podcasting, Compr. + Gate remplacent le trim des entrées matérielles (la tranche est trop étroite
pour les trois). Une commande n'est montrée que si le moteur la porte vraiment : l'égaliseur
rapide n'apparaît que sur les tranches dont le moteur renvoie ses valeurs.

Couleurs (Réglages → Couleurs) : chaque élément dessiné connaît son canal (`color_scope`) et relit
sa couleur en se redessinant ; `apply_colors()` pose celles qui passent par une feuille de style
(Muet, Solo, nom, fond). `_build_ui()` construit l'agencement : la disposition « Console »
(ui/console_strip.py) le remplace par le sien sans toucher à la partie moteur de la tranche.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QStackedLayout, QVBoxLayout,
                               QWidget)

from core.layout_manager import PAN_PAD, PAN_WIDE, VU_PRECISION, StripStyle
from core.mixer_model import CLARITY_EQ, EQ_BANDS, EQ_RANGE_DB, FLAT_EQ
from core.reaper_ctrl import TrackInfo
from core.voicemeeter_ctrl import StripParams
from ui import styles
from ui.fader import HardwareFader, format_db
from ui.fx_pill import PILL_HEIGHT, FxPill
from ui.intellipan import PAD_HEIGHT, IntelliPad
from ui.hardware_widgets import SourceIcon
from ui.knob import DialKnob, RouteButton
from ui.vu_meter import ClipLed, LedVuMeter
from ui.wide_pan import WidePan

STRIP_WIDTH = 112
FX_SECTION_HEIGHT = 96
ROUTING_SECTION_HEIGHT = 92
PAD_SECTION_HEIGHT = PAD_HEIGHT + 8
VU_BAR_WIDTH, VU_GAP = 6, 2      # VU-mètres des strips (largeur d'une barre, écart)
MUTE_HEIGHT = 24
APPS_BUTTON_WIDTH = 44
NAME_HEIGHT = 22
SLOT_PITCH = PILL_HEIGHT + 2
MIN_SLOTS = 4
ROUTING_KNOB = 24
TRIM_KNOB = 46
SMALL_KNOB = 34
TRIM_RANGE_DB = 12.0             # trim : -12 … +12 dB
COMP_GATE_MAX = 10.0             # Compr. / Gate : 0 … 10 (échelle Voicemeeter)
EQ_KNOB = 22                     # égaliseur rapide : trois petits potentiomètres
CLARITY_HEIGHT = 20
CLARITY_TOLERANCE_DB = 0.15      # écart toléré pour reconnaître le préréglage « Clarté »
EQ_LABELS = ("Graves", "Méd.", "Aigus")


def make_section() -> QFrame:
    frame = QFrame()
    frame.setObjectName("section")
    return frame


def make_bay() -> QFrame:
    """Encart creusé sur fond crème (grille de routage…)."""
    frame = QFrame()
    frame.setObjectName("bay")
    return frame


# --------------------------------------------------------------------------- #
# Badge FX (bypass de toute la chaîne)
# --------------------------------------------------------------------------- #
class FxBadge(QAbstractButton):
    """Carré « FX ⏻ » : vert = chaîne active, sombre = chaîne en bypass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tips = ("Effets actifs — clic pour tout bypasser",
                      "Effets en bypass — clic pour les réactiver")
        self.color_scope: int | None = None     # canal (couleur personnalisée)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._refresh_tooltip)
        self._refresh_tooltip(True)

    def set_tooltips(self, on: str, off: str) -> None:
        self._tips = (on, off)
        self._refresh_tooltip(self.isChecked())

    def _refresh_tooltip(self, checked: bool) -> None:
        self.setToolTip(self._tips[0] if checked else self._tips[1])

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        on = self.isChecked() and self.isEnabled()   # sans piste REAPER : éteint
        custom = styles.element_color("fx_badge", self.color_scope)
        if on:
            accent = custom or styles.GREEN_NEON
            p.setPen(QPen(styles.color(accent, 0.25), 3))
            p.setBrush(QColor(accent))
            ink = QColor(styles.ink_on(custom) if custom else styles.ON_ACCENT)
        else:
            p.setPen(QPen(QColor(styles.accent_dim("fx_badge", self.color_scope)), 1.5))
            p.setBrush(QColor(styles.BADGE_OFF))
            ink = QColor(styles.TEXT_MUTED)
        if self.underMouse():
            p.setBrush(p.brush().color().lighter(112))
        p.drawRoundedRect(rect, 7, 7)

        p.setPen(ink)
        p.setFont(styles.font(7, QFont.Weight.DemiBold))
        p.drawText(QRectF(rect.x(), rect.y() + 2, rect.width(), 9), Qt.AlignmentFlag.AlignCenter, "FX")
        # Icône ⏻ : arc ouvert en haut + trait vertical
        c = QPointF(rect.center().x(), rect.y() + rect.height() * 0.66)
        r = rect.width() * 0.2
        p.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 125 * 16, 290 * 16)
        p.drawLine(QPointF(c.x(), c.y() - r - 1.5), QPointF(c.x(), c.y() - 0.5))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# Bouton « Apps » (volume par application)
# --------------------------------------------------------------------------- #
class AppsButton(QAbstractButton):
    """Icône de mélangeur + nombre d'applications qui jouent sur la strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self.color_scope: int | None = None     # canal (couleur personnalisée)
        self.setFixedSize(APPS_BUTTON_WIDTH, MUTE_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Volume par application")

    def count(self) -> int:
        return self._count

    def set_count(self, count: int) -> None:
        if count != self._count:
            self._count = count
            self.setToolTip(f"Volume par application — {count} application(s)" if count
                            else "Volume par application — aucune application ne joue ici")
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        hover = self.underMouse() or self.isDown()
        p.setPen(QPen(QColor(styles.accent_dim("apps", self.color_scope) if hover
                             else styles.BORDER_COLUMN), 1))
        p.setBrush(QColor(styles.BUTTON_HOVER if hover else styles.BUTTON_BG))
        p.drawRoundedRect(rect, 7, 7)
        # Icône : trois glissières horizontales
        ink = QColor(styles.TEXT_PRIMARY)
        p.setPen(QPen(ink, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        left, top = 9.0, self.height() / 2 - 5
        for n, knob in enumerate((0.7, 0.3, 0.55)):
            y = top + n * 5
            p.drawLine(QPointF(left, y), QPointF(left + 12, y))
            p.setBrush(ink)
            p.drawEllipse(QPointF(left + 12 * knob, y), 1.8, 1.8)
        p.setFont(styles.font(10, QFont.Weight.DemiBold))
        p.setPen(QColor(styles.accent("apps", self.color_scope) if self._count else styles.TEXT_MUTED))
        p.drawText(QRectF(left + 15, 0, self.width() - left - 19, self.height()),
                   Qt.AlignmentFlag.AlignCenter, str(self._count))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# Liste des effets
# --------------------------------------------------------------------------- #
class FxList(QWidget):
    """Pills des plugins puis emplacements vides (« + ») : un clic sur un
    emplacement vide ouvre le dialogue d'ajout."""

    addRequested = Signal()
    openRequested = Signal(int)
    bypassRequested = Signal(int, bool)
    removeRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pills: list[FxPill] = []
        self._hover_slot = -1
        self._enabled = True
        self._add_label = "+ Ajouter un effet"
        self._hint = ""
        self.color_scope: int | None = None     # canal (couleur des pastilles)
        self.setMouseTracking(True)
        self._resize_for(0)

    def set_texts(self, add_label: str, hint: str) -> None:
        """Texte de l'emplacement libre au survol, et de la zone vide."""
        if (add_label, hint) != (self._add_label, self._hint):
            self._add_label, self._hint = add_label, hint
            self.update()

    def set_hint(self, hint: str) -> None:
        self.set_texts(self._add_label, hint)

    def set_latencies(self, latencies: list[float | None]) -> None:
        for pill, ms in zip(self._pills, latencies):
            pill.set_latency(ms)

    def set_effects(self, effects: list[tuple[str, bool, bool]]) -> None:
        """effects = [(nom affiché, actif, interface ouverte), …]"""
        while len(self._pills) > len(effects):
            self._pills.pop().deleteLater()
        for index, (name, enabled, is_open) in enumerate(effects):
            if index < len(self._pills):
                self._pills[index].set_info(name, enabled, is_open)
                continue
            pill = FxPill(name, enabled, is_open, self)
            pill.color_scope = self.color_scope
            pill.clicked.connect(lambda i=index: self.openRequested.emit(i))
            pill.bypassRequested.connect(lambda on, i=index: self.bypassRequested.emit(i, on))
            pill.removeRequested.connect(lambda i=index: self.removeRequested.emit(i))
            pill.show()
            self._pills.append(pill)
        self._resize_for(len(effects))
        self._layout_pills()
        self.update()

    def set_available(self, available: bool) -> None:
        """REAPER déconnecté : on ne propose plus d'ajouter d'effet."""
        self._enabled = available
        self.update()

    def _resize_for(self, count: int) -> None:
        # Les pills + un emplacement libre ; les emplacements vides suivants
        # sont seulement dessinés (la zone ne défile que s'il y a beaucoup d'effets)
        self.setMinimumHeight((count + 1) * SLOT_PITCH)

    def _layout_pills(self) -> None:
        for index, pill in enumerate(self._pills):
            pill.setGeometry(0, index * SLOT_PITCH, self.width(), PILL_HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._layout_pills()
        super().resizeEvent(event)

    def _slot_at(self, y: float) -> int:
        return int(y // SLOT_PITCH)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        slot = self._slot_at(event.position().y())
        slot = slot if slot >= len(self._pills) else -1
        if slot != self._hover_slot:
            self._hover_slot = slot
            self.setCursor(Qt.CursorShape.PointingHandCursor if slot >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover_slot = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton and self._enabled
                and self._slot_at(event.position().y()) >= len(self._pills)):
            self.addRequested.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        slots = max(MIN_SLOTS, (self.height() + 2) // SLOT_PITCH)
        dash = QPen(styles.color(styles.TEXT_MUTED, 0.18), 1, Qt.PenStyle.DotLine)
        for slot in range(len(self._pills), slots):
            y = slot * SLOT_PITCH
            rect = QRectF(0, y, self.width(), PILL_HEIGHT)
            if slot > len(self._pills) or self._pills:
                p.setPen(dash)
                p.drawLine(QPointF(4, y - 1), QPointF(self.width() - 4, y - 1))
            if slot == self._hover_slot and self._enabled:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(styles.color(styles.GREEN_DIM, 0.35))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setPen(QColor(styles.GREEN_NEON))
                p.setFont(styles.font(10, QFont.Weight.Medium))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._add_label)
            elif slot == 0 and not self._pills and self._hint:
                p.setPen(QColor(styles.TEXT_MUTED))
                p.setFont(styles.font(10, QFont.Weight.Medium))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._hint)
            else:
                p.setPen(QPen(styles.color(styles.TEXT_MUTED, 0.55), 1.2))
                cx, cy = rect.center().x(), rect.center().y()
                p.drawLine(QPointF(cx - 2, cy), QPointF(cx + 2, cy))
                p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 2))


# --------------------------------------------------------------------------- #
# Nom éditable
# --------------------------------------------------------------------------- #
class EditableLabel(QWidget):
    """Label centré ; double-clic pour le renommer (Entrée valide, Échap annule)."""

    edited = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(text)
        self._label.setObjectName("stripName")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setToolTip("Double-clic ou clic droit pour renommer")
        self._edit = QLineEdit(text)
        self._edit.setObjectName("stripNameEdit")
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setMaxLength(30)
        self._edit.editingFinished.connect(self._commit)
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._label)
        self._stack.addWidget(self._edit)
        self._extra_actions: list[tuple[str, Callable[[], None]]] = []

    def set_menu_actions(self, actions: list[tuple[str, Callable[[], None]]]) -> None:
        """Entrées ajoutées au menu du clic droit (« Assigner une application… » en mode autonome)."""
        self._extra_actions = list(actions)

    def text(self) -> str:
        return self._label.text()

    def setText(self, text: str) -> None:  # noqa: N802
        self._label.setText(text)

    def set_color(self, value: str | None) -> None:
        """Couleur du nom (None : celle du thème)."""
        sheet = f"QLabel#stripName {{ color: {value}; }}" if value else ""
        if self._label.styleSheet() != sheet:
            self._label.setStyleSheet(sheet)

    def mouseDoubleClickEvent(self, _event) -> None:  # noqa: N802
        self.start_editing()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("Renommer le canal…", self.start_editing)
        if self._extra_actions:
            menu.addSeparator()
            for text, callback in self._extra_actions:
                menu.addAction(text, callback)
        menu.exec(event.globalPos())

    def start_editing(self) -> None:
        self._edit.setText(self._label.text())
        self._stack.setCurrentWidget(self._edit)
        self._edit.setFocus()
        self._edit.selectAll()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Échap remonte du QLineEdit jusqu'ici : on annule sans valider
        if event.key() == Qt.Key.Key_Escape:
            self._stack.setCurrentWidget(self._label)
        super().keyPressEvent(event)

    def _commit(self) -> None:
        if self._stack.currentWidget() is not self._edit:
            return
        self._stack.setCurrentWidget(self._label)
        text = self._edit.text().strip()
        if text and text != self._label.text():
            self._label.setText(text)
            self.edited.emit(text)


# --------------------------------------------------------------------------- #
# Strip
# --------------------------------------------------------------------------- #
class StripWidget(QFrame):
    gainChanged = Signal(int, float)
    muteToggled = Signal(int, bool)
    routeToggled = Signal(int, int, bool)
    sendChanged = Signal(int, int, float)
    renamed = Signal(int, str)
    chainToggled = Signal(int, bool)
    fxAddRequested = Signal(int)
    fxOpenRequested = Signal(int, int)
    fxBypassRequested = Signal(int, int, bool)
    fxRemoveRequested = Signal(int, int)
    padMoved = Signal(int, str, float, float)    # (strip, mode, x, y)
    padModeChanged = Signal(int, str)
    appsRequested = Signal(int)
    # Nouveautés du design matériel
    soloToggled = Signal(int, bool)
    trimChanged = Signal(int, float)             # entrées matérielles, dB
    compChanged = Signal(int, float)             # entrées virtuelles, 0…10
    gateChanged = Signal(int, float)             # entrées virtuelles, 0…10
    # Dispositions (Mode et thème) et mode autonome
    eqChanged = Signal(int, int, float)          # égaliseur rapide : (strip, bande 0…2, dB)
    assignRequested = Signal(int)                # mode autonome : assigner une application à la tranche
    unassignRequested = Signal(int)

    # Nom d'objet Qt (feuille de style) et sélecteur du fond de la tranche : la disposition
    # « Console » (ui/console_strip.py) les remplace par les siens.
    OBJECT_NAME = "stripPanel"
    PANEL_SELECTOR = "QFrame#stripPanel"

    def __init__(self, index: int, name: str, route_labels: list[str],
                 sends_adjustable: bool = True, pad_modes: tuple[str, ...] = ("color", "pan", "fx"),
                 physical: bool = True, pad_mode: str | None = None, apps_button: bool = False,
                 accent: str | None = None, route_colors: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self._sends_adjustable = sends_adjustable   # False : Banana, knobs = interrupteurs
        self._physical = physical
        self._accent = accent or styles.CAP_COLORS[index % len(styles.CAP_COLORS)]
        self.setObjectName(self.OBJECT_NAME)
        self._track: TrackInfo | None = None
        self._style = StripStyle()          # la console d'origine ; changé par apply_style()
        self._eq_known = False              # le moteur renvoie l'égaliseur rapide de cette tranche
        self._present = True
        self._released = False
        self._build_ui(name, route_labels, pad_modes, pad_mode, apps_button, route_colors)
        self._refresh_sections()
        self._assign_color_scopes()
        self.apply_colors()

    def _build_ui(self, name: str, route_labels: list[str], pad_modes: tuple[str, ...],
                  pad_mode: str | None, apps_button: bool, route_colors: list[str] | None) -> None:
        """Agencement de la tranche matérielle (fond crème) : crée tous les widgets que le reste
        de la classe pilote (name, fader, vu, mute, solo, knobs, pad, fx_list, fx_badge…)."""
        self.setFixedWidth(STRIP_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 6, 5, 4)
        layout.setSpacing(6)

        self._role = QLabel()               # libellé de l'emplacement dans la disposition (« Bus Batterie »)
        self._role.setObjectName("roleLabel")
        self._role.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._role.hide()
        layout.addWidget(self._role)

        self.name = EditableLabel(name)
        self.name.setFixedHeight(NAME_HEIGHT)
        self.name.edited.connect(lambda text: self.renamed.emit(self.index, text))
        layout.addWidget(self.name)

        self._input_section = self._build_input_section()
        self._eq_section = self._build_eq_section()
        self._fx_section = self._build_fx_section()
        self._pad_section = self._build_pad_section(pad_modes, self._physical, pad_mode)
        self._build_wide_pan()
        self._routing_section = self._build_routing_section(route_labels, route_colors)
        layout.addWidget(self._input_section)
        layout.addWidget(self._eq_section)
        layout.addWidget(self._fx_section)
        layout.addWidget(self._pad_section)
        layout.addWidget(self.wide_pan)
        layout.addLayout(self._build_switch_rows(apps_button))
        layout.addWidget(self._build_fader_section(), 1)
        layout.addWidget(self._routing_section)

    # -------------------------------------------------------------- couleurs
    def _assign_color_scopes(self) -> None:
        """Chaque élément dessiné connaît son canal : c'est lui qui relit sa couleur à l'écran."""
        for widget in (self.fader, self.vu, self.fx_badge, self.fx_list, self.pad, self.apps):
            if widget is not None:
                widget.color_scope = self.index
        for route, knob in enumerate(self.knobs):
            knob.color_element, knob.color_scope = f"send{route}", self.index

    @staticmethod
    def _set_sheet(widget: QWidget, sheet: str) -> None:
        if widget.styleSheet() != sheet:      # re-styler un widget coûte : seulement si besoin
            widget.setStyleSheet(sheet)

    def apply_colors(self) -> None:
        """Couleurs choisies pour les éléments qui passent par une feuille de style (Muet, Solo,
        nom, fond) ; les éléments dessinés à la main relisent les leurs en se redessinant."""
        scope = self.index
        for button, element, make in ((self.mute, "mute", styles.mute_style),
                                      (self.solo, "solo", styles.solo_style)):
            if button is not None:
                chosen = styles.element_color(element, scope)
                self._set_sheet(button, make(chosen) if chosen else "")
        self.name.set_color(styles.element_color("name", scope))
        panel = styles.element_color("panel", scope)
        self._set_sheet(self, styles.panel_style(panel, self.PANEL_SELECTOR) if panel else "")

    # ------------------------------------------------------------ sections
    def _labelled(self, widget: QWidget, text: str) -> QVBoxLayout:
        cell = QVBoxLayout()
        cell.setSpacing(0)
        cell.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        label = QLabel(text)
        label.setObjectName("knobLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cell.addWidget(label)
        return cell

    def _cell(self, widget: QWidget, text: str) -> QWidget:
        """Un potentiomètre et son libellé, dans un widget qu'on peut masquer d'un coup."""
        cell = QWidget()
        box = self._labelled(widget, text)
        box.setContentsMargins(0, 0, 0, 0)
        cell.setLayout(box)
        return cell

    def _build_input_section(self) -> QWidget:
        """Trim (entrée matérielle), Compr. + Gate (entrée virtuelle, ou toute entrée dans la
        disposition Podcasting). Tout est construit ; apply_style() montre ce qui convient."""
        section = QWidget()
        row = QHBoxLayout(section)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.trim: DialKnob | None = None
        self._trim_cell: QWidget | None = None
        if self._physical:
            self.trim = DialKnob(-TRIM_RANGE_DB, TRIM_RANGE_DB, 0.0, TRIM_KNOB, accent=self._accent)
            self.trim.valueChanged.connect(lambda v: self.trimChanged.emit(self.index, v))
            self._trim_cell = self._cell(self.trim, "TRIM")
            row.addWidget(self._trim_cell)
        self.comp = DialKnob(0.0, COMP_GATE_MAX, 0.0, SMALL_KNOB, accent=self._accent, unit="")
        self.gate = DialKnob(0.0, COMP_GATE_MAX, 0.0, SMALL_KNOB, accent=self._accent, unit="")
        self.comp.valueChanged.connect(lambda v: self.compChanged.emit(self.index, v))
        self.gate.valueChanged.connect(lambda v: self.gateChanged.emit(self.index, v))
        self._comp_cell = self._cell(self.comp, "Compr.")
        self._gate_cell = self._cell(self.gate, "Gate")
        row.addWidget(self._comp_cell)
        row.addWidget(self._gate_cell)
        return section

    def _build_eq_section(self) -> QWidget:
        """Égaliseur rapide à trois bandes et touche « Clarté » (disposition Podcasting)."""
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.eq_knobs: list[DialKnob] = []
        for band, label in enumerate(EQ_LABELS):
            knob = DialKnob(EQ_RANGE_DB[0], EQ_RANGE_DB[1], 0.0, EQ_KNOB, accent=self._accent)
            knob.setToolTip(f"{EQ_BANDS[band]} : 0.0 dB")
            knob.valueChanged.connect(lambda db, b=band: self._on_eq_knob(b, db))
            row.addLayout(self._labelled(knob, label))
            self.eq_knobs.append(knob)
        box.addLayout(row)
        self.clarity = QPushButton("Clarté")
        self.clarity.setObjectName("clarityButton")
        self.clarity.setCheckable(True)
        self.clarity.setFixedHeight(CLARITY_HEIGHT)
        self.clarity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clarity.setToolTip("Clarté de la voix : coupe un peu de graves, ouvre les aigus.\n"
                                "Un second clic remet l'égaliseur à plat.")
        self.clarity.clicked.connect(self._on_clarity)
        box.addWidget(self.clarity)
        return section

    def _build_wide_pan(self) -> None:
        self.wide_pan = WidePan()
        self.wide_pan.moved.connect(self._on_wide_pan)

    def _build_fx_section(self) -> QFrame:
        # Encart sombre (écran) dans la tranche crème : liste d'effets du moteur
        section = make_section()
        section.setFixedHeight(FX_SECTION_HEIGHT)
        box = QVBoxLayout(section)
        box.setContentsMargins(5, 5, 5, 5)
        box.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        title = QLabel("Effets")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.latency_label = QLabel()
        self.latency_label.setObjectName("latencyLabel")
        self.latency_label.hide()
        header.addWidget(self.latency_label)
        self.fx_badge = FxBadge()
        self.fx_badge.clicked.connect(lambda: self.chainToggled.emit(self.index, self.fx_badge.isChecked()))
        header.addWidget(self.fx_badge)
        box.addLayout(header)

        self.fx_list = FxList()
        self.fx_list.addRequested.connect(lambda: self.fxAddRequested.emit(self.index))
        self.fx_list.openRequested.connect(lambda i: self.fxOpenRequested.emit(self.index, i))
        self.fx_list.bypassRequested.connect(lambda i, on: self.fxBypassRequested.emit(self.index, i, on))
        self.fx_list.removeRequested.connect(lambda i: self.fxRemoveRequested.emit(self.index, i))
        scroll = QScrollArea()
        scroll.setObjectName("fxScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.fx_list)
        scroll.viewport().setAutoFillBackground(False)
        self.fx_list.setAutoFillBackground(False)
        box.addWidget(scroll, 1)
        return section

    def _build_pad_section(self, modes: tuple[str, ...], physical: bool,
                           mode: str | None) -> QFrame:
        section = make_section()
        section.setFixedHeight(PAD_SECTION_HEIGHT)
        box = QVBoxLayout(section)
        box.setContentsMargins(4, 4, 4, 4)
        self.pad = IntelliPad(modes, physical, mode)
        self.pad.moved.connect(lambda m, x, y: self.padMoved.emit(self.index, m, x, y))
        self.pad.modeChanged.connect(lambda m: self.padModeChanged.emit(self.index, m))
        box.addWidget(self.pad)
        return section

    def _build_switch_rows(self, apps_button: bool) -> QVBoxLayout:
        """Solo / Muet, puis icône de source (+ « Apps » sur les entrées virtuelles)."""
        rows = QVBoxLayout()
        rows.setSpacing(4)

        keys = QHBoxLayout()
        keys.setSpacing(4)
        self.solo = QPushButton("Solo")
        self.solo.setObjectName("soloButton")
        self.solo.setCheckable(True)
        self.solo.setFixedHeight(MUTE_HEIGHT)
        self.solo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.solo.setToolTip("Solo : coupe toutes les autres entrées")
        self.solo.toggled.connect(lambda on: self.soloToggled.emit(self.index, on))
        self.mute = QPushButton("Muet")
        self.mute.setObjectName("muteButton")
        self.mute.setCheckable(True)
        self.mute.setFixedHeight(MUTE_HEIGHT)
        self.mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute.toggled.connect(self._on_mute)
        keys.addWidget(self.solo, 1)
        keys.addWidget(self.mute, 1)
        rows.addLayout(keys)

        extra = QHBoxLayout()
        extra.setSpacing(4)
        self.source_icon = SourceIcon(self._physical)
        extra.addWidget(self.source_icon)
        extra.addStretch(1)
        self.apps: AppsButton | None = None
        if apps_button:
            self.apps = AppsButton()
            self.apps.clicked.connect(lambda: self.appsRequested.emit(self.index))
            extra.addWidget(self.apps)
        rows.addLayout(extra)
        return rows

    def _build_fader_section(self) -> QWidget:
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        header = QHBoxLayout()
        self.clip = ClipLed()
        header.addWidget(self.clip, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.value_label = QLabel()
        self.value_label.setObjectName("faderValue")
        self.value_label.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.value_label)
        box.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(4)
        body.addStretch(1)
        self.fader = HardwareFader(self._accent)
        self.fader.valueChanged.connect(self._on_fader)
        body.addWidget(self.fader)
        self.vu = LedVuMeter(channels=2, bar_width=VU_BAR_WIDTH, gap=VU_GAP)
        body.addWidget(self.vu)
        body.addStretch(1)
        box.addLayout(body, 1)
        self._show_value(0.0)
        return section

    def _build_routing_section(self, labels: list[str], colors: list[str] | None) -> QFrame:
        """Routage rapide : un bouton rond rétro-éclairé par bus (clic = on/off,
        glisser = niveau d'envoi)."""
        section = make_bay()
        section.setFixedHeight(ROUTING_SECTION_HEIGHT)
        grid = QGridLayout(section)
        grid.setContentsMargins(3, 4, 3, 3)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(0)
        self.knobs: list[RouteButton] = []
        for route, label_text in enumerate(labels):
            color = colors[route] if colors and route < len(colors) else styles.HW_ORANGE
            knob = RouteButton(diameter=ROUTING_KNOB, color=color, adjustable=self._sends_adjustable)
            knob.toggled.connect(lambda on, r=route: self.routeToggled.emit(self.index, r, on))
            knob.valueChanged.connect(lambda db, r=route: self.sendChanged.emit(self.index, r, db))
            grid.addLayout(self._labelled(knob, label_text), route // 2, route % 2)
            self.knobs.append(knob)
        return section

    # ---------------------------------------------------- état venant des moteurs
    def set_params(self, params: StripParams) -> None:
        self.fader.setValue(params.gain)
        if not self.fader.isInteracting():
            self._show_value(params.gain)
        if self.mute.isChecked() != params.mute:
            self.mute.blockSignals(True)
            self.mute.setChecked(params.mute)
            self.mute.blockSignals(False)
            self._apply_mute_look(params.mute)
        for knob, enabled, send in zip(self.knobs, params.routes, params.sends):
            if not knob.isInteracting():
                knob.setActive(enabled)
                knob.setValue(send)
        self.pad.set_values(params.pads)
        pan = params.pad("pan")
        if pan is not None:
            self.wide_pan.set_value(pan[0])
        if params.dynamics is not None:            # les valeurs du moteur priment sur la mémoire de l'interface
            self.set_comp_gate(*params.dynamics)
        known = params.eq is not None
        if known != self._eq_known:
            self._eq_known = known
            self._refresh_sections()
        if params.eq is not None:
            for knob, db in zip(self.eq_knobs, params.eq):
                knob.setValue(db)
            self._sync_clarity()

    def set_levels(self, levels: tuple[float, float]) -> None:
        self.vu.set_levels(levels)
        self.clip.set_lit(self.vu.is_clipping())

    # -------------------------------------------------- disposition et état de la tranche
    def apply_style(self, style: StripStyle, label: str | None = None) -> None:
        """Ce que la tranche montre dans la disposition en cours, et le libellé de son emplacement."""
        self._style = style
        self._role.setText((label or "").upper())
        self._role.setVisible(bool(label))
        self._refresh_sections()
        self.vu.set_precision(style.vu == VU_PRECISION)

    @property
    def style_in_use(self) -> StripStyle:
        return self._style

    def _refresh_sections(self) -> None:
        style = self._style
        dynamics = style.dynamics or (not self._physical and style.trim)
        trim = self._physical and style.trim and not style.dynamics   # Compr. + Gate prennent sa place
        if self._trim_cell is not None:
            self._trim_cell.setVisible(trim)
        self._comp_cell.setVisible(dynamics)
        self._gate_cell.setVisible(dynamics)
        self._input_section.setVisible(trim or dynamics)
        self._eq_section.setVisible(style.eq and self._eq_known)
        self._fx_section.setVisible(style.fx)
        self._pad_section.setVisible(style.pan == PAN_PAD)
        self.wide_pan.setVisible(style.pan == PAN_WIDE)
        self._routing_section.setVisible(style.routing)

    def shows(self, section: str) -> bool:
        """La section est-elle voulue par la disposition ? (« trim », « dynamics », « eq », « fx »,
        « routing », « pad », « wide_pan »). Sert aux tests : isVisible() dépend de la fenêtre."""
        widgets = {"trim": self._trim_cell, "dynamics": self._comp_cell, "eq": self._eq_section,
                   "fx": self._fx_section, "routing": self._routing_section,
                   "pad": self._pad_section, "wide_pan": self.wide_pan}
        widget = widgets[section]
        return widget is not None and not widget.isHidden()

    def set_present(self, present: bool, tip: str = "") -> None:
        """Mode autonome : l'application de la tranche n'est pas lancée → tranche grisée."""
        self._present = bool(present)
        for widget in (self, self.name._label, self._role):
            widget.setProperty("absent", not self._present)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.setToolTip(tip)

    def set_released(self, released: bool, tip: str = "") -> None:
        """Mode musical : les traitements de voix de la tranche sont libérés (effets en pause)."""
        self._released = bool(released)
        self._fx_section.setEnabled(not self._released)
        self.name.setToolTip(tip if self._released and tip
                             else "Double-clic ou clic droit pour renommer")

    def set_assignable(self, assignable: bool, assigned: bool = False) -> None:
        """Mode autonome : clic droit sur le nom → assigner une application (ou retirer l'assignation)."""
        actions: list[tuple[str, Callable[[], None]]] = []
        if assignable:
            actions.append(("Assigner une application…", lambda: self.assignRequested.emit(self.index)))
            if assigned:
                actions.append(("Retirer l'assignation", lambda: self.unassignRequested.emit(self.index)))
        self.name.set_menu_actions(actions)

    # -- réglages propres au design matériel (mémorisés par la fenêtre principale)
    def set_trim(self, db: float) -> None:
        if self.trim is not None:
            self.trim.setValue(db)

    def set_comp_gate(self, comp: float | None = None, gate: float | None = None) -> None:
        if comp is not None:
            self.comp.setValue(comp)
        if gate is not None:
            self.gate.setValue(gate)

    def set_solo(self, on: bool) -> None:
        """Etat du bouton Solo sans émettre de signal."""
        if self.solo.isChecked() != on:
            self.solo.blockSignals(True)
            self.solo.setChecked(on)
            self.solo.blockSignals(False)

    def set_track(self, track: TrackInfo | None) -> None:
        """Piste REAPER de la strip (None : aucune piste du projet n'écoute ses canaux)."""
        self._track = track
        if track is None:
            self.fx_list.set_effects([])
            self.fx_badge.setEnabled(False)
            self.fx_badge.setToolTip("Aucune piste REAPER pour cette strip : "
                                     "ajoute un effet pour en créer une.")
            return
        self.fx_list.set_effects([(fx.display_name, fx.enabled, fx.open) for fx in track.fx])
        if self.fx_badge.isChecked() != track.fx_enabled:
            self.fx_badge.blockSignals(True)
            self.fx_badge.setChecked(track.fx_enabled)
            self.fx_badge.blockSignals(False)
            self.fx_badge.update()
        if track.processes_live:
            self.fx_badge._refresh_tooltip(self.fx_badge.isChecked())
        else:
            self.fx_badge.setToolTip(f"La piste REAPER « {track.name} » n'est pas armée en "
                                     "écoute : ses effets ne s'appliquent pas au son.")

    def set_reaper_available(self, available: bool, can_add: bool = True) -> None:
        """REAPER joignable ? `can_add` : un effet peut être ajouté (la strip a
        une piste, ou l'app a le droit d'en créer une)."""
        self.fx_list.set_available(available and can_add)
        self.fx_badge.setEnabled(available and self._track is not None)

    def configure_effects(self, add_label: str, hint: str, manages_fx: bool,
                          chain_on_tip: str, chain_off_tip: str) -> None:
        """Textes de la zone d'effets selon le moteur (REAPER, FL Studio, APO…)."""
        self.fx_list.set_texts(add_label, hint)
        self.fx_badge.set_tooltips(chain_on_tip, chain_off_tip)

    def set_fx_hint(self, hint: str) -> None:
        self.fx_list.set_hint(hint)

    def set_latency(self, ms: float | None, per_fx: list[float | None] | None = None) -> None:
        """Latence des plugins actifs de la strip (None : inconnue)."""
        if per_fx is not None:
            self.fx_list.set_latencies(per_fx)
        if ms is None or ms < 0.05:
            self.latency_label.hide()
            return
        self.latency_label.setText(f"+{ms:.1f} ms")
        self.latency_label.setProperty("slow", ms >= 3.0)
        self.latency_label.style().unpolish(self.latency_label)
        self.latency_label.style().polish(self.latency_label)
        self.latency_label.setToolTip("Délai ajouté à la voix par les plugins actifs de cette strip "
                                      "(en plus des tampons audio)")
        self.latency_label.show()

    def fx_name(self, fx_index: int) -> str:
        if self._track is not None and 0 <= fx_index < len(self._track.fx):
            return self._track.fx[fx_index].display_name
        return ""

    def strip_name(self) -> str:
        return self.name.text()

    # ------------------------------------------------------------ interne
    def _on_wide_pan(self, x: float) -> None:
        """Le curseur large règle le même panoramique que le carré de l'Intellipan (mode Position)."""
        _, y = self.pad.value("pan")
        self.pad.set_values((("pan", x, y),))
        self.padMoved.emit(self.index, "pan", x, y)

    def _on_eq_knob(self, band: int, db: float) -> None:
        self.eq_knobs[band].setToolTip(f"{EQ_BANDS[band]} : {db:+.1f} dB")
        self._sync_clarity()
        self.eqChanged.emit(self.index, band, db)

    def _on_clarity(self, checked: bool) -> None:
        target = CLARITY_EQ if checked else FLAT_EQ
        for band, (knob, db) in enumerate(zip(self.eq_knobs, target)):
            knob.setValue(db)
            self.eq_knobs[band].setToolTip(f"{EQ_BANDS[band]} : {db:+.1f} dB")
            self.eqChanged.emit(self.index, band, db)
        self._sync_clarity()

    def _sync_clarity(self) -> None:
        """« Clarté » est allumée tant que l'égaliseur est sur son préréglage."""
        on = all(abs(knob.value() - target) <= CLARITY_TOLERANCE_DB
                 for knob, target in zip(self.eq_knobs, CLARITY_EQ))
        if self.clarity.isChecked() != on:
            blocked = self.clarity.blockSignals(True)
            self.clarity.setChecked(on)
            self.clarity.blockSignals(blocked)

    def _on_fader(self, db: float) -> None:
        self._show_value(db)
        self.gainChanged.emit(self.index, db)

    def _on_mute(self, muted: bool) -> None:
        self._apply_mute_look(muted)
        self.muteToggled.emit(self.index, muted)

    def _apply_mute_look(self, muted: bool) -> None:
        self.fader.setMuted(muted)
        self.value_label.setProperty("muted", muted)
        self.value_label.style().unpolish(self.value_label)
        self.value_label.style().polish(self.value_label)

    def _show_value(self, db: float) -> None:
        self.value_label.setText(
            f"{format_db(db)}<span style='color:{styles.INK_SOFT}; font-size:9px;'> dB</span>")

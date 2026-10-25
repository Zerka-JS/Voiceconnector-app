"""
Onglet « Layout et Apparence » des réglages.

Cinq blocs :
  - Mode de fonctionnement : A (Voicemeeter + application vocale) ou B (autonome, sans Voicemeeter) ;
    un changement de mode demande un redémarrage ;
  - Thème de l'interface : Rétro (VFD / Neon Wave Synth), Futuriste (Cyberpunk), Épuré (Flat Modern),
    Studio Pro (Dark Slate), appliqué tout de suite ;
  - Disposition : « Auto » (selon le logiciel détecté), Standard, FL Studio / MAO, Streaming / Broadcast,
    Podcasting / Voix, et l'affectation des emplacements aux canaux ;
  - Logiciel suivi : détection automatique ou choix manuel, et (mode B) le profil des canaux ;
  - Application vocale (mode A) : quelle application alimenter, par un bus ou un flux VBAN
    (expérimental), et le routage conseillé.

Cet onglet ne touche à aucun widget de la console : il écrit dans le coordinateur de session et dans
la config, et émet `sessionChanged(SessionChange)` que la fenêtre principale applique.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from core.app_detector import KNOWN_APPS
from core.fl_sync import fl_setup_checks, install_script
from core.layout_manager import AUTO, LAYOUT_PRESETS, MODE_STANDALONE, MODE_VOICEMEETER, layout_choices
from core.mixer_model import MixerParams
from core.mixer_router import (MAX_VBAN_STREAMS, TRANSPORT_BUS, TRANSPORT_VBAN, VbanStream, VoiceRoute,
                               describe_status)
from core.profile_import import Profile, save_profile
from core.session_coordinator import SessionChange, SessionCoordinator
from core.session_mode import MODE_DESCRIPTIONS, MODE_LABELS, MODES
from core.themes import GLOBAL_THEME_ORDER, GLOBAL_THEMES, global_theme

log = logging.getLogger(__name__)

AUTOMATIC = "Automatique"
BUS_CHOICES = (("", AUTOMATIC), ("B1", "B1"), ("B2", "B2"), ("B3", "B3"))
PROFILE_FILTER = "Profil VoiceConnector (*.json)"
REFRESH_DELAY_MS = 900


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


@dataclass
class LiveState:
    """Ce que la console sait en ce moment et que l'onglet affiche (fourni par la fenêtre principale)."""

    names: list[str] = field(default_factory=list)          # nom de chaque canal
    route: VoiceRoute | None = None                         # routage vers l'application vocale
    params: MixerParams | None = None                       # dernier état de la console
    profile_name: str = ""                                  # mode B : nom du profil en cours
    profile: Profile | None = None                          # mode B : le profil en cours (export)


class ThemeCard(QAbstractButton):
    """Carte d'un thème global : aperçu de ses couleurs, son nom et une phrase."""

    HEIGHT = 74

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.spec = GLOBAL_THEMES[key]
        self.setCheckable(True)
        self.setMinimumHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.spec.tagline)

    def paintEvent(self, _event) -> None:  # noqa: N802
        spec, palette = self.spec, self.spec.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        selected = self.isChecked()
        p.setPen(QPen(QColor(spec.accent if selected else palette["BORDER_COLUMN"]), 2 if selected else 1))
        p.setBrush(QColor(palette["BG_WINDOW"]))
        p.drawRoundedRect(card, 9, 9)
        # Aperçu : colonne, bloc interne, accent, texte
        x = card.left() + 10
        for color in (palette["BG_COLUMN"], palette["BG_SECTION"], spec.accent, palette["TEXT_PRIMARY"]):
            p.setPen(QPen(QColor(palette["BORDER_COLUMN"]), 1))
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, card.top() + 10, 18, 18), 4, 4)
            x += 22
        p.setPen(QColor(palette["TEXT_PRIMARY"]))
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QRectF(card.left() + 10, card.top() + 32, card.width() - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, spec.name)
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(palette["TEXT_SECONDARY"]))
        wrapped = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        wrapped.setWrapMode(QTextOption.WrapMode.WordWrap)
        p.drawText(QRectF(card.left() + 10, card.top() + 49, card.width() - 20, 22), spec.tagline, wrapped)
        if selected:
            p.setPen(QColor(spec.accent))
            font.setWeight(QFont.Weight.Bold)
            font.setPixelSize(11)
            p.setFont(font)
            p.drawText(QRectF(card.right() - 28, card.top() + 8, 20, 18), Qt.AlignmentFlag.AlignCenter, "✔")


class LayoutSettings(QWidget):
    sessionChanged = Signal(object)      # SessionChange : la fenêtre principale le applique
    appearanceChanged = Signal()         # thème global choisi
    restartRequested = Signal()          # changement de mode : redémarrer
    routeRequested = Signal()            # « Appliquer le routage » (mode A)

    def __init__(self, cfg: dict, session: SessionCoordinator, detector=None,
                 live: Callable[[], LiveState] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._session = session
        self._detector = detector
        self._live = live or LiveState
        self._running_mode = session.mode
        self._building = True

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 8, 6, 0)
        box.setSpacing(12)
        box.addWidget(self._mode_group())
        box.addWidget(self._theme_group())
        box.addWidget(self._layout_group())
        box.addWidget(self._software_group())
        self.voice_group = self._voice_group()
        box.addWidget(self.voice_group)
        box.addWidget(self._fl_group())
        box.addStretch(1)

        if detector is not None:
            detector.scanned.connect(self._on_scanned)
        self._building = False
        self.refresh()

    # ------------------------------------------------------------------ mode
    def _mode_group(self) -> QGroupBox:
        group = QGroupBox("Mode de fonctionnement")
        box = QVBoxLayout(group)
        self.mode_buttons = QButtonGroup(self)
        for mode in MODES:
            radio = QRadioButton(MODE_LABELS[mode])
            radio.setProperty("mode", mode)
            radio.setToolTip(MODE_DESCRIPTIONS[mode])
            radio.setChecked(mode == self._session.session.mode)
            self.mode_buttons.addButton(radio)
            box.addWidget(radio)
        self.mode_buttons.buttonToggled.connect(self._on_mode)
        self.mode_hint = _hint("")
        box.addWidget(self.mode_hint)
        row = QHBoxLayout()
        row.addStretch(1)
        self.restart_button = QPushButton("Redémarrer VoiceConnector")
        self.restart_button.clicked.connect(self.restartRequested)
        row.addWidget(self.restart_button)
        box.addLayout(row)
        return group

    def _on_mode(self, button, checked: bool) -> None:
        if checked and not self._building:
            self._session.session.set_mode(str(button.property("mode")))
            self._show_mode()

    def _show_mode(self) -> None:
        chosen = self._session.session.mode
        self.mode_hint.setText(MODE_DESCRIPTIONS[chosen])
        pending = chosen != self._running_mode
        self.restart_button.setVisible(pending)
        if pending:
            self.mode_hint.setText(MODE_DESCRIPTIONS[chosen] + "\n\nRedémarre VoiceConnector pour passer "
                                   "à ce mode (le mode A demande que Voicemeeter soit installé).")
        self.voice_group.setVisible(self._running_mode == MODE_VOICEMEETER)

    # ----------------------------------------------------------------- thème
    def _theme_group(self) -> QGroupBox:
        group = QGroupBox("Thème de l'interface")
        box = QVBoxLayout(group)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.theme_buttons = QButtonGroup(self)
        self.theme_cards: dict[str, ThemeCard] = {}
        for position, key in enumerate(GLOBAL_THEME_ORDER):
            card = ThemeCard(key)
            self.theme_buttons.addButton(card)
            self.theme_cards[key] = card
            grid.addWidget(card, position // 2, position % 2)
        self.theme_buttons.buttonClicked.connect(self._on_theme)
        box.addLayout(grid)
        self.theme_hint = _hint("Les couleurs d'accent classiques sont dans l'onglet « Apparence ».")
        box.addWidget(self.theme_hint)
        return group

    def _on_theme(self, button) -> None:
        self._cfg.setdefault("ui", {})["theme"] = button.key
        suggested = button.spec.suggested_layout
        label = LAYOUT_PRESETS[suggested].name if suggested in LAYOUT_PRESETS else ""
        self.theme_hint.setText(f"Thème « {button.spec.name} » appliqué."
                                + (f" Il va bien avec la disposition « {label} »." if label else ""))
        self.appearanceChanged.emit()

    def show_theme(self) -> None:
        """Coche la carte du thème en cours (aucune si c'est un thème d'accent de l'onglet Apparence)."""
        current = global_theme(str(self._cfg.get("ui", {}).get("theme") or ""))
        self.theme_buttons.setExclusive(False)
        for key, card in self.theme_cards.items():
            card.setChecked(current is not None and key == current.key)
            card.update()
        self.theme_buttons.setExclusive(True)

    # ------------------------------------------------------------ disposition
    def _layout_group(self) -> QGroupBox:
        group = QGroupBox("Disposition de la console")
        box = QVBoxLayout(group)
        form = QFormLayout()
        self.layout_choice = QComboBox()
        for key, label in layout_choices():
            self.layout_choice.addItem(label, key)
        self.layout_choice.currentIndexChanged.connect(self._on_layout)
        form.addRow("Disposition", self.layout_choice)
        box.addLayout(form)
        self.layout_hint = _hint("")
        box.addWidget(self.layout_hint)
        self.roles_form = QFormLayout()
        self.role_choices: dict[str, QComboBox] = {}
        box.addLayout(self.roles_form)
        self.roles_hint = _hint("Emplacements de la disposition : laisse « Automatique » pour que VoiceConnector "
                                "range les canaux d'après leur type, ou choisis le canal de chaque emplacement.")
        box.addWidget(self.roles_hint)
        return group

    def _on_layout(self, _index: int) -> None:
        if self._building:
            return
        choice = str(self.layout_choice.currentData())
        self.sessionChanged.emit(self._session.set_layout(choice))
        self.show_layout()

    def show_layout(self) -> None:
        """Description de la disposition choisie et sélecteurs d'emplacements."""
        layouts = self._session.layouts
        selector = layouts.selector_value
        was, self._building = self._building, True
        self.layout_choice.setCurrentIndex(max(0, self.layout_choice.findData(selector)))
        self._building = was
        effective = layouts.effective_preset()
        preset = LAYOUT_PRESETS[effective]
        if selector == AUTO:
            self.layout_hint.setText(f"Auto : la console suit le logiciel détecté — en ce moment « {preset.name} ». "
                                     f"{preset.description}")
        else:
            self.layout_hint.setText(preset.description)
        if self._session.music_on:
            self.layout_hint.setText(self.layout_hint.text() + "\nLe mode musical impose la disposition "
                                     "FL Studio / MAO tant qu'il est actif.")
        self._fill_roles(effective)

    def _fill_roles(self, preset_key: str) -> None:
        for _ in range(self.roles_form.rowCount()):
            self.roles_form.removeRow(0)             # supprime aussi les widgets de la ligne
        self.role_choices = {}
        preset = LAYOUT_PRESETS[preset_key]
        names = self._live().names
        explicit = self._session.layouts.explicit_roles(preset_key)
        for slot in preset.slots:
            combo = QComboBox()
            combo.addItem(AUTOMATIC, None)
            for index, name in enumerate(names):
                combo.addItem(f"{index + 1} — {name}", index)
            chosen = explicit.get(slot.role)
            combo.setCurrentIndex(max(0, combo.findData(chosen)) if chosen is not None else 0)
            combo.currentIndexChanged.connect(
                lambda _i, key=preset_key, role=slot.role, box=combo: self._on_role(key, role, box.currentData()))
            self.roles_form.addRow(slot.label, combo)
            self.role_choices[slot.role] = combo
        self.roles_hint.setVisible(bool(preset.slots))

    def _on_role(self, preset: str, role: str, index: int | None) -> None:
        if self._building:
            return
        self._session.layouts.set_role(preset, role, index)
        self.sessionChanged.emit(SessionChange(False, True))

    # ------------------------------------------------------------- logiciel
    def _software_group(self) -> QGroupBox:
        group = QGroupBox("Logiciel suivi")
        box = QVBoxLayout(group)
        form = QFormLayout()
        self.app_choice = QComboBox()
        self.app_choice.addItem("Automatique (détection)", AUTO)
        for app in sorted(KNOWN_APPS, key=lambda a: (a.priority, a.name.lower())):
            self.app_choice.addItem(f"{app.name} — {app.kind_label}", app.key)
        self.app_choice.currentIndexChanged.connect(self._on_app)
        form.addRow("Logiciel", self.app_choice)
        box.addLayout(form)
        self.app_hint = _hint("")
        box.addWidget(self.app_hint)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Actualiser la détection")
        self.refresh_button.clicked.connect(self._refresh_detection)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(_hint("Le bouton « Mode musical » n'apparaît que si le logiciel suivi a une partie "
                            "musique (FL Studio, Ableton, REAPER, Cubase…) : jamais pour Discord, Zoom ou Spotify."))
        self.profile_box = QWidget()
        profile = QVBoxLayout(self.profile_box)
        profile.setContentsMargins(0, 4, 0, 0)
        self.profile_label = _hint("")
        profile.addWidget(self.profile_label)
        buttons = QHBoxLayout()
        self.import_button = QPushButton("Importer un profil…")
        self.import_button.clicked.connect(self._import_profile)
        buttons.addWidget(self.import_button)
        self.export_button = QPushButton("Exporter le profil…")
        self.export_button.clicked.connect(self._export_profile)
        buttons.addWidget(self.export_button)
        self.clear_profile_button = QPushButton("Profil automatique")
        self.clear_profile_button.clicked.connect(self._clear_profile)
        buttons.addWidget(self.clear_profile_button)
        buttons.addStretch(1)
        profile.addLayout(buttons)
        box.addWidget(self.profile_box)
        return group

    def _on_app(self, _index: int) -> None:
        if self._building:
            return
        self.sessionChanged.emit(self._session.select_app(str(self.app_choice.currentData())))
        self.show_software()
        self.show_layout()

    def _refresh_detection(self) -> None:
        if self._detector is not None:
            self._detector.refresh()
            QTimer.singleShot(REFRESH_DELAY_MS, self.show_software)

    def _on_scanned(self, _detected) -> None:
        self.show_software()

    def show_software(self) -> None:
        session = self._session
        was, self._building = self._building, True
        self.app_choice.setCurrentIndex(max(0, self.app_choice.findData(session.session.app)))
        self._building = was
        running = [d.app.name for d in session.detected]
        active = session.active
        lines = [("Détecté : " + ", ".join(running) + ".") if running
                 else "Aucun logiciel audio connu n'est détecté pour le moment."]
        if active.app is not None:
            lines.append(f"Suivi : {active.name} ({'en cours' if active.running else 'pas encore lancé'}).")
        self.app_hint.setText("\n".join(lines))
        standalone = self._running_mode == MODE_STANDALONE
        self.profile_box.setVisible(standalone)
        if standalone:
            current = session.session.profile
            live = self._live()
            name = live.profile_name or "automatique"
            self.profile_label.setText(
                f"Profil des canaux : {name} — "
                + (f"fichier {Path(current).name}" if current else "choisi d'après le logiciel suivi")
                + (f"\n{session.profile_note}" if session.profile_note else ""))
            self.clear_profile_button.setEnabled(bool(current))

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importer un profil de canaux", str(Path.home()),
                                              PROFILE_FILTER)
        if path:
            self.set_profile(path)

    def set_profile(self, path: str) -> None:
        """Profil du mode B choisi dans un fichier (illisible : le profil automatique reste, avec une note)."""
        self._session.session.set_profile(path)
        self.sessionChanged.emit(self._session.reload_profile())
        self.show_software()

    def _export_profile(self) -> None:
        profile = self._live().profile or self._session.profile
        if profile is None:
            return
        name = re.sub(r'[\\/:*?"<>|]+', "_", profile.name).strip() or "profil"
        path, _ = QFileDialog.getSaveFileName(self, "Exporter le profil de canaux",
                                              str(Path.home() / f"{name}.json"), PROFILE_FILTER)
        if path:
            self.export_profile(path)

    def export_profile(self, path: str) -> bool:
        """Écrit le profil en cours dans un fichier (réimportable plus tard, ou sur un autre PC)."""
        profile = self._live().profile or self._session.profile
        if profile is None:
            return False
        try:
            save_profile(profile, path)
        except OSError as exc:
            self.profile_label.setText(f"Profil non exporté : {exc}")
            return False
        self.profile_label.setText(f"Profil « {profile.name} » exporté : {Path(path).name}")
        return True

    def _clear_profile(self) -> None:
        self.set_profile("")

    # --------------------------------------------------- application vocale
    def _voice_group(self) -> QGroupBox:
        group = QGroupBox("Application vocale (mode A)")
        box = QVBoxLayout(group)
        form = QFormLayout()
        self.voice_app_choice = QComboBox()
        self.voice_app_choice.addItem("Automatique (logiciel suivi ou application vocale en cours)", AUTO)
        for app in sorted((a for a in KNOWN_APPS if a.is_voice_app), key=lambda a: (a.priority, a.name.lower())):
            self.voice_app_choice.addItem(f"{app.name} — {app.kind_label}", app.key)
        self.voice_app_choice.currentIndexChanged.connect(self._on_voice_app)
        form.addRow("Application", self.voice_app_choice)
        transport = QHBoxLayout()
        self.transport_buttons = QButtonGroup(self)
        for key, label in ((TRANSPORT_BUS, "Bus Voicemeeter"), (TRANSPORT_VBAN, "Flux VBAN (expérimental)")):
            radio = QRadioButton(label)
            radio.setProperty("transport", key)
            self.transport_buttons.addButton(radio)
            transport.addWidget(radio)
        transport.addStretch(1)
        self.transport_buttons.buttonToggled.connect(self._on_transport)
        form.addRow("Transport", transport)
        self.bus_choice = QComboBox()
        for key, label in BUS_CHOICES:
            self.bus_choice.addItem(label, key)
        self.bus_choice.currentIndexChanged.connect(self._on_bus)
        form.addRow("Bus", self.bus_choice)
        self.vban_name = QLineEdit()
        self.vban_ip = QLineEdit()
        self.vban_port = QLineEdit()
        for edit in (self.vban_name, self.vban_ip, self.vban_port):
            edit.editingFinished.connect(self._on_vban)
        self.vban_row = QWidget()
        vban = QHBoxLayout(self.vban_row)
        vban.setContentsMargins(0, 0, 0, 0)
        for caption, edit in (("Nom", self.vban_name), ("IP", self.vban_ip), ("Port", self.vban_port)):
            vban.addWidget(QLabel(caption))
            vban.addWidget(edit, 1)
        form.addRow("Flux VBAN", self.vban_row)
        box.addLayout(form)
        self.voice_status = QLabel()
        self.voice_status.setWordWrap(True)
        self.voice_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.voice_status)
        self.voice_steps = _hint("")
        self.voice_steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.voice_steps)
        row = QHBoxLayout()
        self.apply_route = QPushButton("Appliquer le routage")
        self.apply_route.setToolTip("Envoie la voix vers le bus de l'application (sans écho) ; rien n'est "
                                    "modifié sans ce clic.")
        self.apply_route.clicked.connect(self._on_apply_route)
        row.addWidget(self.apply_route)
        row.addStretch(1)
        box.addLayout(row)
        return group

    def _voice_key(self) -> str:
        """Clé de l'application vocale dont on règle le transport (celle du plan, sinon le choix)."""
        route = self._live().route
        return route.app.key if route is not None else str(self.voice_app_choice.currentData())

    def _on_voice_app(self, _index: int) -> None:
        if self._building:
            return
        self._session.session.set_voice_app(str(self.voice_app_choice.currentData()))
        self.show_voice()

    def _on_transport(self, button, checked: bool) -> None:
        if checked and not self._building:
            key = self._voice_key()
            if key != AUTO:
                self._session.session.set_transport(key, str(button.property("transport")))
            self.show_voice()

    def _on_bus(self, _index: int) -> None:
        if self._building:
            return
        key = self._voice_key()
        if key != AUTO:
            self._session.session.set_voice_bus(key, str(self.bus_choice.currentData()) or None)
        self.show_voice()

    def _on_vban(self) -> None:
        if self._building:
            return
        stream = VbanStream.from_mapping({"index": 0, "name": self.vban_name.text(),
                                          "ip": self.vban_ip.text(), "port": self.vban_port.text()},
                                         MAX_VBAN_STREAMS)
        self._session.session.set_vban({"index": stream.index, "name": stream.name, "ip": stream.ip,
                                        "port": stream.port})
        self.show_voice()

    def _on_apply_route(self) -> None:
        self.routeRequested.emit()
        QTimer.singleShot(REFRESH_DELAY_MS, self.show_voice)

    def show_voice(self) -> None:
        session = self._session
        live = self._live()
        was, self._building = self._building, True
        self.voice_app_choice.setCurrentIndex(max(0, self.voice_app_choice.findData(session.session.voice_app)))
        key = live.route.app.key if live.route is not None else session.session.voice_app
        transport = session.session.transport(key) if key != AUTO else TRANSPORT_BUS
        for button in self.transport_buttons.buttons():
            button.setChecked(button.property("transport") == transport)
        self.bus_choice.setCurrentIndex(max(0, self.bus_choice.findData(
            (session.session.voice_bus(key) or "") if key != AUTO else "")))
        stream = VbanStream.from_mapping(session.session.vban)
        self.vban_name.setText(stream.name)
        self.vban_ip.setText(stream.ip)
        self.vban_port.setText(str(stream.port))
        self._building = was
        self.vban_row.setVisible(transport == TRANSPORT_VBAN)
        self.bus_choice.setEnabled(transport == TRANSPORT_BUS and key != AUTO)
        route = live.route
        self.voice_status.setText(describe_status(route, live.params))
        self.voice_steps.setText("\n".join([*(f"{n}. {step}" for n, step in enumerate(route.steps, 1)),
                                            *(f"⚠ {warning}" for warning in route.warnings)])
                                 if route is not None else
                                 "Lance Discord, OBS, TeamSpeak ou Audacity, ou choisis une application ci-dessus.")
        self.apply_route.setEnabled(route is not None and transport == TRANSPORT_BUS
                                    and route.route is not None)

    # ------------------------------------------------- synchronisation FL Studio
    def _fl_group(self) -> QGroupBox:
        group = QGroupBox("Synchronisation avec FL Studio (mode musical)")
        box = QVBoxLayout(group)
        self.fl_status = QLabel()
        self.fl_status.setWordWrap(True)
        self.fl_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.fl_status)
        row = QHBoxLayout()
        self.install_script_button = QPushButton("Installer le script FL Studio")
        self.install_script_button.setToolTip("Copie le script MIDI dans Documents\\Image-Line\\FL Studio\\"
                                              "Settings\\Hardware\\VoiceConnector.")
        self.install_script_button.clicked.connect(self._install_script)
        row.addWidget(self.install_script_button)
        self.fl_check_button = QPushButton("Vérifier")
        self.fl_check_button.clicked.connect(self.show_fl)
        row.addWidget(self.fl_check_button)
        row.addStretch(1)
        box.addLayout(row)
        box.addWidget(_hint("1. Crée deux ports avec loopMIDI : « VoiceConnector FL » et « VoiceConnector VC ».  "
                            "2. Installe le script ci-dessus.  3. Dans FL Studio, Options → MIDI settings : "
                            "entrée « VoiceConnector FL » (activée, Controller type « VoiceConnector », Port 1) "
                            "et sortie « VoiceConnector VC » (activée, même Port 1).  Le bouton « Mode musical » "
                            "synchronise alors faders, muets, solos et panoramiques avec le mixer de FL Studio."))
        self._fl_note = ""
        return group

    def _install_script(self) -> None:
        try:
            target = install_script()
        except OSError as exc:
            self._fl_note = f"Installation impossible : {exc}"
        else:
            self._fl_note = f"Script installé : {target}. Relance FL Studio (ou rafraîchis ses réglages MIDI)."
        self.show_fl()

    def show_fl(self) -> None:
        """Ce qui est prêt et ce qui manque (bibliothèque MIDI, ports loopMIDI, script de FL Studio)."""
        checks = fl_setup_checks(self._cfg)
        lines = [("✔ " if check.ok else "✘ ") + check.text for check in checks]
        if self._fl_note:
            lines.append(self._fl_note)
        self.fl_status.setText("\n".join(lines))
        self.install_script_button.setText("Réinstaller le script FL Studio" if checks and checks[-1].ok
                                           else "Installer le script FL Studio")

    # ------------------------------------------------------------- général
    def refresh(self) -> None:
        """Relit tout l'état (à l'ouverture de l'onglet et après un changement fait ailleurs)."""
        self._show_mode()
        self.show_theme()
        self.show_layout()
        self.show_software()
        self.show_voice()
        self.show_fl()

    def shutdown(self) -> None:
        """Débranche la détection quand les réglages se ferment."""
        if self._detector is not None:
            try:
                self._detector.scanned.disconnect(self._on_scanned)
            except (RuntimeError, TypeError):
                pass

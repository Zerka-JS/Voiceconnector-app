"""
Onglet « Couleurs » des réglages : la couleur de chaque élément de la console (knobs d'envoi,
fader, VU-mètres, bouton Muet, pastilles d'effets, Intellipan, nom, fond du panneau, lecteur de
musique…), pour toute la console, pour un seul canal ou pour le Master.

Les choix s'appliquent tout de suite (signal `appearanceChanged`, la fenêtre principale se
repeint derrière les réglages) et sont écrits dans la config, section `ui`, clé `colors`
(voir core/color_scheme.py pour le format). Un fichier JSON permet de les sauvegarder, de les
partager ou de les retrouver après une réinstallation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QColorDialog, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from core import color_scheme
from core.voicemeeter_ctrl import DEFAULT_ROUTING
from ui import styles

log = logging.getLogger(__name__)

COLORS_FILTER = "Couleurs VoiceConnector (*.json)"
DEFAULT_EXPORT_NAME = "voiceconnector-couleurs.json"


class ColorButton(QAbstractButton):
    """Rectangle de couleur cliquable (choix d'une couleur d'élément) ; liseré clair quand la
    couleur a été choisie pour cette portée."""

    def __init__(self, color: str, custom: bool, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = color
        self.custom = custom
        self.setFixedSize(52, 22)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(styles.TEXT_PRIMARY if self.custom or self.underMouse()
                             else styles.BORDER_COLUMN), 1.5 if self.custom else 1))
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(rect, 6, 6)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


class ColorSettings(QWidget):
    """Page « Couleurs » : choix de la portée (console, canal, Master) puis une ligne par élément."""

    appearanceChanged = Signal()

    def __init__(self, cfg: dict, channels: Callable[[], list[str]], standalone: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._channels = channels            # noms des canaux (relus à chaque ouverture de la page)
        self._standalone = standalone        # mode autonome : pas de routage, donc pas de knobs d'envoi
        self.color_buttons: dict[str, ColorButton] = {}
        self.color_resets: dict[str, QPushButton] = {}
        self.color_labels: dict[str, str] = {}

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 8, 6, 0)
        box.setSpacing(12)
        group = QGroupBox("Couleur de chaque élément")
        inner = QVBoxLayout(group)

        row = QHBoxLayout()
        row.addWidget(QLabel("Appliquer à"))
        self.color_scope_choice = QComboBox()
        self.color_scope_choice.addItem("Toute la console", None)
        for index, name in enumerate(self._channels()):
            self.color_scope_choice.addItem(f"Canal {index + 1} — {name}", index)
        self.color_scope_choice.addItem("Master", color_scheme.MASTER_SCOPE)
        self.color_scope_choice.currentIndexChanged.connect(lambda _i: self._show_colors())
        row.addWidget(self.color_scope_choice, 1)
        inner.addLayout(row)

        self.color_form = QFormLayout()
        inner.addLayout(self.color_form)
        inner.addWidget(_hint("Clic sur une couleur pour la changer, « Défaut » pour revenir à celle "
                              "du thème. La couleur d'un canal passe avant celle de toute la console."))

        buttons = QHBoxLayout()
        self.reset_scope_button = QPushButton("Réinitialiser cette portée")
        self.reset_scope_button.clicked.connect(self.reset_scope)
        self.reset_all_button = QPushButton("Tout réinitialiser")
        self.reset_all_button.clicked.connect(self.reset_element_colors)
        buttons.addWidget(self.reset_scope_button)
        buttons.addWidget(self.reset_all_button)
        buttons.addStretch(1)
        inner.addLayout(buttons)
        box.addWidget(group)

        files = QGroupBox("Sauvegarder et partager")
        files_box = QVBoxLayout(files)
        file_row = QHBoxLayout()
        self.export_button = QPushButton("Exporter les couleurs…")
        self.export_button.clicked.connect(self._choose_export)
        self.import_button = QPushButton("Importer des couleurs…")
        self.import_button.clicked.connect(self._choose_import)
        file_row.addWidget(self.export_button)
        file_row.addWidget(self.import_button)
        file_row.addStretch(1)
        files_box.addLayout(file_row)
        self.status = _hint("")
        files_box.addWidget(self.status)
        files_box.addWidget(_hint("Un fichier .json contient toutes les couleurs choisies : à garder "
                                  "d'une installation à l'autre ou à envoyer à un ami."))
        box.addWidget(files)
        box.addStretch(1)
        self._show_colors()

    # ---------------------------------------------------------------- lecture
    def colors(self) -> dict[str, str]:
        """Les couleurs choisies, telles que la config les porte (toujours le même dictionnaire :
        la fenêtre principale et les réglages le partagent)."""
        return self._cfg.setdefault("ui", {}).setdefault("colors", {})

    def scope(self):
        """Portée choisie : None (toute la console), un numéro de canal ou « master »."""
        return self.color_scope_choice.currentData()

    def elements(self, scope) -> list[tuple[str, str]]:
        """(élément, nom affiché) proposés pour une portée (console, canal, Master)."""
        routing = self._cfg.get("voicemeeter", {}).get("routing", DEFAULT_ROUTING)
        labels = dict(styles.ELEMENT_LABELS)
        for route, element in enumerate(color_scheme.SEND_ELEMENTS):
            label = routing[route].get("label", f"Envoi {route + 1}") if route < len(routing) else ""
            labels[element] = f"Knob {label}" if label else ""
        wanted = [key for key in color_scheme.elements_for(scope) if labels.get(key)]
        if self._standalone:          # sans Voicemeeter, il n'y a ni routage ni envois
            wanted = [key for key in wanted if key not in color_scheme.SEND_ELEMENTS]
        return [(key, labels[key]) for key in wanted]

    def _show_colors(self) -> None:
        """Lignes des éléments de la portée choisie."""
        for _ in range(self.color_form.rowCount()):
            self.color_form.removeRow(0)
        self.color_buttons, self.color_resets, self.color_labels = {}, {}, {}
        scope = self.scope()
        for element, label in self.elements(scope):
            button = ColorButton(styles.element_default(element), False, label)
            button.clicked.connect(lambda _c=False, e=element, n=label: self._choose_element_color(e, n))
            reset = QPushButton("Défaut")
            reset.setToolTip("Couleur du thème" if scope is None else "Comme toute la console")
            reset.clicked.connect(lambda _c=False, e=element: self.set_element_color(e, self.scope(), None))
            line = QHBoxLayout()
            line.addWidget(button)
            line.addWidget(reset)
            line.addStretch(1)
            self.color_form.addRow(label, line)
            self.color_buttons[element], self.color_resets[element] = button, reset
            self.color_labels[element] = label
        self._refresh_colors()

    def _refresh_colors(self) -> None:
        """Couleurs affichées (en place : un bouton cliqué n'est pas détruit)."""
        scope = self.scope()
        colors = self.colors()
        for element, button in self.color_buttons.items():
            custom = color_scheme.has_own(colors, element, scope)
            button.color = styles.element_color(element, scope, colors) or styles.element_default(element)
            button.custom = custom
            button.update()
            self.color_resets[element].setEnabled(custom)
        self.reset_scope_button.setEnabled(bool(color_scheme.scope_keys(colors, scope)))
        self.reset_all_button.setEnabled(bool(colors))

    # -------------------------------------------------------------- actions
    def _choose_element_color(self, element: str, label: str) -> None:
        scope = self.scope()
        current = QColor(styles.element_color(element, scope, self.colors()) or styles.element_default(element))
        color = QColorDialog.getColor(current, self, f"Couleur — {label}")
        if color.isValid():
            self.set_element_color(element, scope, color.name())

    def set_element_color(self, element: str, scope, value: str | None) -> None:
        """Couleur d'un élément (None : retour à la couleur du thème / de la console), appliquée tout de suite."""
        if color_scheme.set_color(self.colors(), element, scope, value):
            self.appearanceChanged.emit()
        self._refresh_colors()

    def reset_scope(self) -> None:
        """Retire les couleurs choisies pour la portée affichée (console, un canal ou le Master)."""
        if color_scheme.reset_scope(self.colors(), self.scope()):
            self.appearanceChanged.emit()
        self._refresh_colors()

    def reset_element_colors(self) -> None:
        """Retire toutes les couleurs choisies : la console reprend celles du thème."""
        if self.colors():
            self._cfg.setdefault("ui", {})["colors"] = {}
            self.appearanceChanged.emit()
        self._refresh_colors()

    # ------------------------------------------------------- export / import
    def _say(self, text: str, ok: bool = True) -> None:
        self.status.setObjectName("ok" if ok else "warning")
        self.status.setText(text)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def export_to(self, path: Path) -> bool:
        """Écrit les couleurs choisies dans un fichier JSON ; True si c'est fait."""
        try:
            Path(path).write_text(color_scheme.export_text(self.colors()), encoding="utf-8")
        except OSError as exc:
            log.error("Couleurs non exportées : %s", exc)
            self._say(f"Export impossible : {exc.strerror or exc}", ok=False)
            return False
        self._say(f"Couleurs exportées dans {Path(path).name}.")
        return True

    def import_from(self, path: Path) -> bool:
        """Remplace les couleurs choisies par celles d'un fichier exporté ; True si c'est fait."""
        try:
            imported = color_scheme.import_text(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Couleurs non importées : %s", exc)
            self._say(f"Import impossible : {exc}", ok=False)
            return False
        self._cfg.setdefault("ui", {})["colors"] = imported
        self.appearanceChanged.emit()
        self._refresh_colors()
        self._say(f"{len(imported)} couleur(s) importée(s) depuis {Path(path).name}.")
        return True

    def _choose_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exporter les couleurs",
                                              str(Path.home() / DEFAULT_EXPORT_NAME), COLORS_FILTER)
        if path:
            self.export_to(Path(path))

    def _choose_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importer des couleurs", str(Path.home()), COLORS_FILTER)
        if path:
            self.import_from(Path(path))

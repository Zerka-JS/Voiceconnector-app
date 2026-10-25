"""
Barre d'actions de la console (sous le haut de la fenêtre) : mode de fonctionnement, logiciel
suivi, choix de la disposition et bouton « Mode musical ».

Le bouton « Mode musical » n'existe QUE pour un logiciel qui a une partie musique / DAW
(FL Studio, Ableton, REAPER, Cubase…) : pour une application seulement vocale (Discord, Zoom,
Spotify…) ou sans logiciel reconnu, il est caché, pas seulement grisé. La règle est dans
core/app_detector.py (`music_button_state`) ; ce widget n'en applique que le résultat.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from core.app_detector import SOURCE_DETECTED, SOURCE_SELECTED, ActiveApp, MusicButtonState
from core.layout_manager import MODE_STANDALONE, layout_choices

MODE_TEXT = {
    MODE_STANDALONE: ("MODE B · AUTONOME", "Sans Voicemeeter : volumes de Windows et pistes de FL Studio. "
                      "Réglages → Layout et Apparence pour changer de mode."),
}
MODE_A_TEXT = ("MODE A · VOICEMEETER", "Voicemeeter fait le mixage. "
               "Réglages → Layout et Apparence pour changer de mode.")
MUSIC_LABEL = "♪  Mode musical"


def app_text(active: ActiveApp) -> tuple[str, str]:
    """(texte, info-bulle) de la pastille du logiciel suivi."""
    if active.app is None:
        return ("Aucun logiciel reconnu",
                "Aucun logiciel audio connu n'est détecté. Choisis-en un dans Réglages → Layout et Apparence.")
    how = {SOURCE_SELECTED: "choisi dans les réglages", SOURCE_DETECTED: "détecté automatiquement"}.get(
        active.source, "")
    state = "en cours" if active.running else "pas encore lancé"
    return (f"{'●' if active.running else '○'}  {active.name}",
            f"{active.name} — {state} ({how}). Clic : réglages du suivi de logiciel.")


class ActionBar(QFrame):
    musicToggled = Signal(bool)          # clic sur « Mode musical » (état demandé)
    layoutChosen = Signal(str)           # clé de disposition choisie, ou « auto »
    settingsRequested = Signal()         # clic sur la pastille du logiciel

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("actionBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(10)

        self.mode_chip = QLabel(MODE_A_TEXT[0])
        self.mode_chip.setObjectName("modeChip")
        self.mode_chip.setToolTip(MODE_A_TEXT[1])
        row.addWidget(self.mode_chip)

        self.app_chip = QPushButton("Aucun logiciel reconnu")
        self.app_chip.setObjectName("appChip")
        self.app_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.app_chip.clicked.connect(self.settingsRequested)
        row.addWidget(self.app_chip)

        row.addStretch(1)

        caption = QLabel("Disposition")
        caption.setObjectName("barLabel")
        row.addWidget(caption)
        self.layout_choice = QComboBox()
        self.layout_choice.setObjectName("layoutChoice")
        self.layout_choice.setToolTip("Disposition de la console : « Auto » suit le logiciel détecté.")
        for key, label in layout_choices():
            self.layout_choice.addItem(label, key)
        self.layout_choice.currentIndexChanged.connect(self._on_layout_index)
        row.addWidget(self.layout_choice)

        self.sync_led = QLabel("●")
        self.sync_led.setObjectName("syncLed")
        self.sync_led.setProperty("on", False)
        self.sync_led.hide()
        row.addWidget(self.sync_led)

        self.music_button = QPushButton(MUSIC_LABEL)
        self.music_button.setObjectName("musicButton")
        self.music_button.setCheckable(True)
        self.music_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.music_button.toggled.connect(self.musicToggled)
        self.music_button.hide()                    # caché tant qu'aucun logiciel musical n'est suivi
        row.addWidget(self.music_button)

    # ------------------------------------------------------------- affichage
    def set_mode(self, standalone: bool) -> None:
        text, tip = MODE_TEXT[MODE_STANDALONE] if standalone else MODE_A_TEXT
        self.mode_chip.setText(text)
        self.mode_chip.setToolTip(tip)

    def set_active_app(self, active: ActiveApp) -> None:
        text, tip = app_text(active)
        self.app_chip.setText(text)
        self.app_chip.setToolTip(tip)

    def set_layout_choice(self, key: str) -> None:
        """Disposition affichée par le sélecteur, sans déclencher de choix."""
        index = self.layout_choice.findData(key)
        if index >= 0 and index != self.layout_choice.currentIndex():
            blocked = self.layout_choice.blockSignals(True)
            self.layout_choice.setCurrentIndex(index)
            self.layout_choice.blockSignals(blocked)

    def set_music_state(self, state: MusicButtonState) -> None:
        """Visible seulement avec un logiciel musical ; coché tant que le mode musical est actif."""
        self.music_button.setVisible(state.visible)
        self.music_button.setEnabled(state.enabled)
        blocked = self.music_button.blockSignals(True)
        self.music_button.setChecked(state.checked and state.visible)
        self.music_button.blockSignals(blocked)
        self.music_button.setToolTip(state.tooltip)

    def set_sync(self, connected: bool | None) -> None:
        """Voyant de la synchronisation avec le mixer de FL Studio (None : pas de synchronisation)."""
        self.sync_led.setVisible(connected is not None and self.music_button.isVisibleTo(self))
        on = bool(connected)
        self.sync_led.setProperty("on", on)
        self.sync_led.setToolTip("Mixer de FL Studio synchronisé" if on
                                 else "Synchronisation du mixer : en attente de FL Studio")
        self.sync_led.style().unpolish(self.sync_led)       # la QSS relit la propriété « on »
        self.sync_led.style().polish(self.sync_led)

    # --------------------------------------------------------------- signaux
    def _on_layout_index(self, _index: int) -> None:
        self.layoutChosen.emit(str(self.layout_choice.currentData()))

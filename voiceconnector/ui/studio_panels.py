"""
Les panneaux de la fenêtre Studio : barre de transport, channel rack (canaux et pas du séquenceur),
playlist (pistes) et mixer de pistes (Mackie Control).

Chaque panneau lit un `DawState` (`update_from`) et signale ce que l'utilisateur veut faire (signaux
`…Requested` / `…Changed`) ; la fenêtre les relie à la liaison avec le logiciel de musique. Mettre un
panneau à jour ne renvoie jamais d'ordre au logiciel.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core.daw_model import (CAP_BANK, CAP_METRONOME, CAP_PATTERNS, CAP_RECORD, CAP_SONG_MODE, CAP_STEPS,
                            CAP_TEMPO, CAP_TEMPO_WRITE, MAX_TEMPO, MIN_TEMPO, STEP_CHOICES, DawChannel,
                            DawState, DawTrack, format_seconds)
from core.studio_geometry import percent_text
from ui import studio_style as st
from ui import styles
from ui.studio_widgets import (ActivityLed, LedButton, MiniKnob, NameButton, StepRow, StudioFader,
                               TransportButton)

AUTO_STEPS_LABEL = "Auto"


def _label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()


# --------------------------------------------------------------------------- #
# Barre de transport
# --------------------------------------------------------------------------- #
class TransportBar(QFrame):
    rewindRequested = Signal()
    playRequested = Signal()
    stopRequested = Signal()
    recordRequested = Signal()
    modeRequested = Signal()
    metronomeRequested = Signal()
    tempoRequested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transportBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        self.rewind = TransportButton("rewind", tip="Retour au début")
        self.play = TransportButton("play", tip="Lecture / pause (Espace)")
        self.stop = TransportButton("stop", tip="Arrêt")
        self.record = TransportButton("record", tip="Enregistrement", lit_color=st.RED)
        for button, signal in ((self.rewind, self.rewindRequested), (self.play, self.playRequested),
                               (self.stop, self.stopRequested), (self.record, self.recordRequested)):
            button.clicked.connect(signal)
            row.addWidget(button)

        row.addSpacing(8)
        self.mode = QPushButton("Morceau")
        self.mode.setObjectName("modeButton")
        self.mode.setCheckable(True)
        self.mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode.clicked.connect(lambda _checked=False: self.modeRequested.emit())
        row.addWidget(self.mode)
        self.metronome = QPushButton("Métronome")
        self.metronome.setObjectName("modeButton")
        self.metronome.setCheckable(True)
        self.metronome.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.metronome.clicked.connect(lambda _checked=False: self.metronomeRequested.emit())
        row.addWidget(self.metronome)

        row.addSpacing(14)
        self.tempo_title = _label("TEMPO", "panelTitle")
        row.addWidget(self.tempo_title)
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(MIN_TEMPO, MAX_TEMPO)
        self.tempo.setDecimals(3)
        self.tempo.setSingleStep(1.0)
        self.tempo.setSuffix(" BPM")
        self.tempo.setKeyboardTracking(False)
        self.tempo.setFixedWidth(130)
        self.tempo.valueChanged.connect(self._on_tempo)
        row.addWidget(self.tempo)
        self.tempo_text = _label("—", "tempoValue")
        row.addWidget(self.tempo_text)

        row.addStretch(1)
        self.position = _label("—", "positionValue")
        self.position.setMinimumWidth(150)
        self.position.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.position)
        self.time = _label("", "timeValue")
        self.time.setMinimumWidth(70)
        row.addWidget(self.time)
        self._applying = False
        self.setFixedHeight(54)
        self.position.setFont(styles.font(20, QFont.Weight.Bold))
        self.tempo_text.setFont(styles.font(15, QFont.Weight.Bold))

    def _on_tempo(self, value: float) -> None:
        if not self._applying:
            self.tempoRequested.emit(float(value))

    def update_from(self, state: DawState) -> None:
        transport = state.transport
        self.play.set_lit(transport.playing)
        self.record.set_lit(transport.recording)
        self.record.setEnabled(state.can(CAP_RECORD))
        self.mode.setText(state.song_mode_label or "Morceau")
        self.mode.setVisible(state.can(CAP_SONG_MODE))
        self.mode.setChecked(transport.song_mode)
        self.metronome.setVisible(state.can(CAP_METRONOME))
        self.metronome.setChecked(transport.metronome)
        known = state.can(CAP_TEMPO) and transport.tempo > 0
        writable = known and state.can(CAP_TEMPO_WRITE)
        self.tempo_title.setVisible(state.can(CAP_TEMPO))
        self.tempo.setVisible(writable)
        self.tempo_text.setVisible(state.can(CAP_TEMPO) and not writable)
        if known:
            self._applying = True
            try:
                if not self.tempo.hasFocus() and abs(self.tempo.value() - transport.tempo) >= 0.0005:
                    self.tempo.setValue(transport.tempo)
            finally:
                self._applying = False
            self.tempo_text.setText(f"{transport.tempo:.3f} BPM")
        else:
            self.tempo_text.setText("—")
        self.position.setText(transport.position_text())
        seconds = transport.seconds()
        self.time.setText(format_seconds(seconds) if seconds is not None else "")


# --------------------------------------------------------------------------- #
# Channel rack
# --------------------------------------------------------------------------- #
class ChannelRow(QWidget):
    """Une ligne du channel rack : voyant (muet), solo, panoramique, volume, nom, pas du séquenceur."""

    muteRequested = Signal(bool)
    soloRequested = Signal(bool)
    panChanged = Signal(float)
    volumeChanged = Signal(float)
    selectRequested = Signal()
    stepChanged = Signal(int, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        self.led = LedButton(tip="Actif / muet : clic")
        self.solo = LedButton("S", color=st.YELLOW, tip="Solo")
        self.pan = MiniKnob(low=-1.0, high=1.0, default=0.0, bipolar=True, color=st.BLUE, tip="Panoramique")
        self.volume = MiniKnob(default=0.78125, color=st.ACCENT, tip="Volume")
        self.name = NameButton()
        self.name.setFixedWidth(150)
        self.steps = StepRow()
        for widget in (self.led, self.solo, self.pan, self.volume, self.name):
            row.addWidget(widget)
        row.addWidget(self.steps, 1)
        self.led.toggled.connect(lambda lit: self.muteRequested.emit(not lit))
        self.solo.toggled.connect(self.soloRequested)
        self.pan.valueChanged.connect(self.panChanged)
        self.volume.valueChanged.connect(self.volumeChanged)
        self.name.clicked.connect(self.selectRequested)
        self.steps.stepChanged.connect(self.stepChanged)

    def update_from(self, channel: DawChannel, count: int, playhead: int) -> None:
        self.led.set_on(not channel.muted)
        self.solo.set_on(channel.solo)
        self.pan.set_value(channel.pan)
        self.volume.set_value(channel.volume)
        self.name.set_data(channel.name or f"Canal {channel.index + 1}", channel.color, channel.selected,
                           dim=channel.muted)
        self.steps.set_data(channel.steps, count, channel.color, playhead)


class RackPanel(QFrame):
    muteRequested = Signal(int, bool)
    soloRequested = Signal(int, bool)
    panChanged = Signal(int, float)
    volumeChanged = Signal(int, float)
    selectRequested = Signal(int)
    stepChanged = Signal(int, int, bool)           # (canal, pas, allumé)
    patternRequested = Signal(int)
    stepCountRequested = Signal(int)               # 0 : automatique

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("studioPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("CHANNEL RACK", "panelTitle"))
        head.addStretch(1)
        self.pattern_label = _label("Pattern", "panelTitle")
        head.addWidget(self.pattern_label)
        self.pattern = QComboBox()
        self.pattern.setMinimumWidth(170)
        self.pattern.activated.connect(self._on_pattern)
        head.addWidget(self.pattern)
        self.steps_label = _label("Pas", "panelTitle")
        head.addWidget(self.steps_label)
        self.step_count = QComboBox()
        self.step_count.addItem(AUTO_STEPS_LABEL, 0)
        for choice in STEP_CHOICES:
            self.step_count.addItem(str(choice), choice)
        self.step_count.activated.connect(self._on_step_count)
        head.addWidget(self.step_count)
        outer.addLayout(head)

        self.hint = _label("", "emptyHint")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.hint)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.rows_layout = QVBoxLayout(self.body)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        self.rows_layout.addStretch(1)
        self.area.setWidget(self.body)
        outer.addWidget(self.area, 1)
        self.rows: dict[int, ChannelRow] = {}
        self._order: list[int] = []
        self._patterns: list[tuple[int, str]] | None = None

    # ------------------------------------------------------------- contenu
    def clear(self) -> None:
        for row in self.rows.values():
            row.deleteLater()
        self.rows.clear()
        self._order = []
        self._patterns = None
        self.pattern.clear()

    def _make_row(self, index: int) -> ChannelRow:
        row = ChannelRow()
        row.muteRequested.connect(lambda muted, i=index: self.muteRequested.emit(i, muted))
        row.soloRequested.connect(lambda solo, i=index: self.soloRequested.emit(i, solo))
        row.panChanged.connect(lambda value, i=index: self.panChanged.emit(i, value))
        row.volumeChanged.connect(lambda value, i=index: self.volumeChanged.emit(i, value))
        row.selectRequested.connect(lambda i=index: self.selectRequested.emit(i))
        row.stepChanged.connect(lambda step, on, i=index: self.stepChanged.emit(i, step, on))
        return row

    def _arrange(self, wanted: list[int]) -> None:
        for index in [i for i in self.rows if i not in wanted]:
            self.rows.pop(index).deleteLater()
        for index in wanted:
            if index not in self.rows:
                self.rows[index] = self._make_row(index)
        for row in self.rows.values():
            self.rows_layout.removeWidget(row)
        for position, index in enumerate(wanted):
            self.rows_layout.insertWidget(position, self.rows[index])
        self._order = list(wanted)

    def update_from(self, state: DawState, *, steps_choice: int = 0, playhead: int = -1) -> None:
        wanted = [channel.index for channel in state.channels]
        if wanted != self._order:
            self._arrange(wanted)
        for channel in state.channels:
            self.rows[channel.index].update_from(channel, state.step_count, playhead)
        self.hint.setVisible(not state.channels)
        self.area.setVisible(bool(state.channels))
        patterns_ok = state.can(CAP_PATTERNS)
        for widget in (self.pattern, self.pattern_label):
            widget.setVisible(patterns_ok)
        for widget in (self.step_count, self.steps_label):
            widget.setVisible(state.can(CAP_STEPS))
        self._sync_patterns(state)
        self._sync_step_count(max(0, steps_choice))

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)

    def _sync_patterns(self, state: DawState) -> None:
        items = [(p.index, p.name or f"Pattern {p.index}") for p in state.patterns]
        if items != self._patterns:
            self._patterns = items
            self.pattern.blockSignals(True)
            try:
                self.pattern.clear()
                for index, name in items:
                    self.pattern.addItem(name, index)
            finally:
                self.pattern.blockSignals(False)
        row = self.pattern.findData(state.pattern)
        if row >= 0 and row != self.pattern.currentIndex():
            self.pattern.blockSignals(True)
            try:
                self.pattern.setCurrentIndex(row)
            finally:
                self.pattern.blockSignals(False)

    def _sync_step_count(self, choice: int) -> None:
        row = self.step_count.findData(choice)
        if row >= 0 and row != self.step_count.currentIndex():
            self.step_count.blockSignals(True)
            try:
                self.step_count.setCurrentIndex(row)
            finally:
                self.step_count.blockSignals(False)

    def _on_pattern(self, row: int) -> None:
        index = self.pattern.itemData(row)
        if index is not None:
            self.patternRequested.emit(int(index))

    def _on_step_count(self, row: int) -> None:
        value = self.step_count.itemData(row)
        if value is not None:
            self.stepCountRequested.emit(int(value))


# --------------------------------------------------------------------------- #
# Playlist : les pistes
# --------------------------------------------------------------------------- #
class TrackRow(QWidget):
    muteRequested = Signal(bool)
    soloRequested = Signal(bool)
    selectRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        self.activity = ActivityLed(color=st.GREEN)
        self.name = NameButton()
        self.mute = LedButton("M", color=st.RED, tip="Muet")
        self.solo = LedButton("S", color=st.YELLOW, tip="Solo")
        row.addWidget(self.activity)
        row.addWidget(self.name, 1)
        row.addWidget(self.mute)
        row.addWidget(self.solo)
        self.mute.toggled.connect(self.muteRequested)
        self.solo.toggled.connect(self.soloRequested)
        self.name.clicked.connect(self.selectRequested)

    def update_from(self, track: DawTrack) -> None:
        self.name.set_data(track.name or f"Piste {track.index}", track.color, track.selected, dim=track.muted)
        self.mute.set_on(track.muted)
        self.solo.set_on(track.solo)
        self.activity.set_level(1.0 if track.active else track.level)


class PlaylistPanel(QFrame):
    muteRequested = Signal(int, bool)
    soloRequested = Signal(int, bool)
    selectRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("studioPanel")
        self.setMinimumWidth(250)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        outer.addWidget(_label("PLAYLIST", "panelTitle"))
        self.hint = _label("Aucune piste.", "emptyHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.hint)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.rows_layout = QVBoxLayout(self.body)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        self.rows_layout.addStretch(1)
        self.area.setWidget(self.body)
        outer.addWidget(self.area, 1)
        self.rows: dict[int, TrackRow] = {}
        self._order: list[int] = []

    def clear(self) -> None:
        for row in self.rows.values():
            row.deleteLater()
        self.rows.clear()
        self._order = []

    def _make_row(self, index: int) -> TrackRow:
        row = TrackRow()
        row.muteRequested.connect(lambda muted, i=index: self.muteRequested.emit(i, muted))
        row.soloRequested.connect(lambda solo, i=index: self.soloRequested.emit(i, solo))
        row.selectRequested.connect(lambda i=index: self.selectRequested.emit(i))
        return row

    def update_from(self, state: DawState) -> None:
        wanted = [track.index for track in state.tracks]
        if wanted != self._order:
            for index in [i for i in self.rows if i not in wanted]:
                self.rows.pop(index).deleteLater()
            for index in wanted:
                if index not in self.rows:
                    self.rows[index] = self._make_row(index)
            for row in self.rows.values():
                self.rows_layout.removeWidget(row)
            for position, index in enumerate(wanted):
                self.rows_layout.insertWidget(position, self.rows[index])
            self._order = list(wanted)
        for track in state.tracks:
            self.rows[track.index].update_from(track)
        self.hint.setVisible(not state.tracks)
        self.area.setVisible(bool(state.tracks))


# --------------------------------------------------------------------------- #
# Mixer de pistes (Mackie Control)
# --------------------------------------------------------------------------- #
class MixerStrip(QFrame):
    armRequested = Signal(bool)
    muteRequested = Signal(bool)
    soloRequested = Signal(bool)
    selectRequested = Signal()
    panChanged = Signal(float)
    volumeChanged = Signal(float)

    def __init__(self, *, master: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("studioPanel")
        self.master = master
        self.setFixedWidth(104)
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 8, 6, 8)
        column.setSpacing(6)
        self.name = NameButton()
        self.name.setMinimumWidth(80)
        column.addWidget(self.name)
        keys = QHBoxLayout()
        keys.setSpacing(4)
        self.arm = LedButton("R", color=st.RED, tip="Armer pour l'enregistrement")
        self.mute = LedButton("M", color=st.RED, tip="Muet")
        self.solo = LedButton("S", color=st.YELLOW, tip="Solo")
        if not master:
            keys.addWidget(self.arm)
        keys.addWidget(self.mute)
        keys.addWidget(self.solo)
        keys.addStretch(1)
        column.addLayout(keys)
        self.pan = MiniKnob(low=-1.0, high=1.0, default=0.0, bipolar=True, color=st.BLUE, size=30, tip="Panoramique")
        column.addWidget(self.pan, 0, Qt.AlignmentFlag.AlignHCenter)
        self.fader = StudioFader(default=0.78 if not master else 0.8)
        column.addWidget(self.fader, 1, Qt.AlignmentFlag.AlignHCenter)
        self.value = _label("", "timeValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.value)
        if master:
            self.arm.setVisible(False)
            self.mute.setVisible(False)
            self.solo.setVisible(False)
            self.pan.setVisible(False)
        self.arm.toggled.connect(self.armRequested)
        self.mute.toggled.connect(self.muteRequested)
        self.solo.toggled.connect(self.soloRequested)
        self.name.clicked.connect(self.selectRequested)
        self.pan.valueChanged.connect(self.panChanged)
        self.fader.valueChanged.connect(self._on_fader)

    def _on_fader(self, value: float) -> None:
        self.value.setText(percent_text(value))
        self.volumeChanged.emit(value)

    def update_from(self, track: DawTrack) -> None:
        empty = not track.name and track.volume <= 0.001
        self.setEnabled(self.master or not empty)
        self.name.set_data(track.name or ("—" if not self.master else "Master"), track.color, track.selected,
                           dim=track.muted or empty)
        self.arm.set_on(track.armed)
        self.mute.set_on(track.muted)
        self.solo.set_on(track.solo)
        self.pan.set_value(track.pan)
        self.fader.set_value(max(0.0, track.volume))
        self.fader.set_level(track.level)
        if not self.fader.is_dragging():
            self.value.setText("" if track.volume < 0 else percent_text(track.volume))


class MixerPanel(QFrame):
    armRequested = Signal(int, bool)
    muteRequested = Signal(int, bool)
    soloRequested = Signal(int, bool)
    selectRequested = Signal(int)
    panChanged = Signal(int, float)
    volumeChanged = Signal(int, float)
    masterChanged = Signal(float)
    bankRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("studioPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_label("MIXER DES PISTES", "panelTitle"))
        head.addStretch(1)
        self.previous = QPushButton("◀")
        self.previous.setObjectName("bankButton")
        self.previous.setToolTip("Les 8 pistes précédentes")
        self.range = _label("", "timeValue")
        self.range.setMinimumWidth(80)
        self.range.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next = QPushButton("▶")
        self.next.setObjectName("bankButton")
        self.next.setToolTip("Les 8 pistes suivantes")
        for button, step in ((self.previous, -1), (self.next, 1)):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, s=step: self.bankRequested.emit(s))
        head.addWidget(self.previous)
        head.addWidget(self.range)
        head.addWidget(self.next)
        outer.addLayout(head)
        self.strips_layout = QHBoxLayout()
        self.strips_layout.setSpacing(6)
        outer.addLayout(self.strips_layout, 1)
        self.strips: list[MixerStrip] = []
        self.master_strip = MixerStrip(master=True, parent=self)    # jamais sans parent : Qt le détruit avec le panneau
        self.master_strip.hide()
        self.master_strip.volumeChanged.connect(self.masterChanged)
        self._indexes: list[int] = []

    def clear(self) -> None:
        for strip in self.strips:
            self.strips_layout.removeWidget(strip)
            strip.hide()
            strip.deleteLater()
        self.strips = []
        self._indexes = []
        self.strips_layout.removeWidget(self.master_strip)
        self.master_strip.hide()

    def _make_strip(self) -> MixerStrip:
        strip = MixerStrip()
        position = len(self.strips)
        strip.armRequested.connect(lambda on, p=position: self.armRequested.emit(self._indexes[p], on))
        strip.muteRequested.connect(lambda on, p=position: self.muteRequested.emit(self._indexes[p], on))
        strip.soloRequested.connect(lambda on, p=position: self.soloRequested.emit(self._indexes[p], on))
        strip.selectRequested.connect(lambda p=position: self.selectRequested.emit(self._indexes[p]))
        strip.panChanged.connect(lambda value, p=position: self.panChanged.emit(self._indexes[p], value))
        strip.volumeChanged.connect(lambda value, p=position: self.volumeChanged.emit(self._indexes[p], value))
        return strip

    def update_from(self, state: DawState) -> None:
        while len(self.strips) < len(state.tracks):
            strip = self._make_strip()
            self.strips.append(strip)
            self.strips_layout.insertWidget(len(self.strips) - 1, strip)
        for extra in self.strips[len(state.tracks):]:
            extra.setVisible(False)
        self._indexes = [track.index for track in state.tracks]
        for strip, track in zip(self.strips, state.tracks, strict=False):
            strip.setVisible(True)
            strip.update_from(track)
        if self.strips_layout.indexOf(self.master_strip) < 0:
            self.strips_layout.addWidget(self.master_strip)
        self.master_strip.setVisible(state.master is not None)
        if state.master is not None:
            self.master_strip.update_from(state.master)
        banks = state.can(CAP_BANK)
        for widget in (self.previous, self.next, self.range):
            widget.setVisible(banks)
        if state.tracks:
            self.range.setText(f"Pistes {state.tracks[0].index + 1}–{state.tracks[-1].index + 1}")

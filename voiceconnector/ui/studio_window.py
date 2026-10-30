"""
La fenêtre Studio : un endroit à part de la console de VoiceConnector pour faire de la musique, relié
au vrai logiciel (FL Studio, Cubase, Studio One, Ableton…).

    - barre de transport : lecture, arrêt, enregistrement, tempo, position ;
    - FL Studio : le channel rack (canaux, pas du séquenceur, patterns) et la playlist ;
    - Cubase, Studio One, Ableton, REAPER… (Mackie Control) : le mixer de pistes par groupes de 8 ;
    - sans logiciel lancé : une démonstration, avec un bandeau qui dit pourquoi et comment relier.

La fenêtre ne parle jamais au logiciel directement : elle lit `session.link.state` et appelle les
méthodes de la liaison (core/studio_session.py, core/fl_studio_link.py, core/mcu_link.py).
"""

from __future__ import annotations

import html
import logging
from typing import Callable

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from core import fl_sync
from core import studio_geometry as sg
from core import studio_session as ss
from core.daw_model import CAP_PLAYLIST, CAP_RACK, CAP_TRACK_MIX, DawState
from core.studio_session import StudioSession, StudioStatus
from ui import studio_style as st
from ui import styles
from ui.studio_panels import MixerPanel, PlaylistPanel, RackPanel, TransportBar

log = logging.getLogger(__name__)

TICK_MS = 33                                   # le Studio se met à jour une trentaine de fois par seconde
DEFAULT_SIZE = (1180, 720)
MIN_SIZE = (780, 500)
LOOPMIDI_URL = "https://www.tobias-erichsen.de/software/loopmidi.html"
AUTO_LABEL = "Automatique"
DEMO_LABEL = "Démonstration"


# --------------------------------------------------------------------------- #
# Textes de l'aide (purs : testés sans fenêtre)
# --------------------------------------------------------------------------- #
def checks_html(checks) -> str:
    """Le diagnostic : une ligne par vérification, « OK » ou « À faire »."""
    lines = []
    for check in checks:
        mark = (f"<b style='color:{st.GREEN}'>OK</b>" if check.ok else f"<b style='color:{st.RED}'>À faire</b>")
        lines.append(f"{mark}&nbsp; {html.escape(check.text)}")
    return "<br>".join(lines)


def steps_html(steps) -> str:
    return "<br>".join(f"<b>{number}.</b>&nbsp; {html.escape(step)}" for number, step in enumerate(steps, start=1))


def software_choices() -> list[tuple[str, str]]:
    """(libellé, clé) de la liste des logiciels : automatique, ceux que le Studio pilote, démonstration."""
    return [(AUTO_LABEL, "auto"), *((t.name, t.key) for t in ss.TARGETS), (DEMO_LABEL, ss.DEMO_KEY)]


# --------------------------------------------------------------------------- #
# Aide à la configuration
# --------------------------------------------------------------------------- #
class StudioSetupDialog(QDialog):
    """Ce qui est prêt, ce qui manque et les étapes pour relier un logiciel au Studio."""

    def __init__(self, session: StudioSession, parent: QWidget | None = None, *, documents=None, source=None,
                 mido_module=None) -> None:
        super().__init__(parent)
        self.setObjectName("studioWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(st.stylesheet())
        self.setWindowTitle("Relier un logiciel au Studio")
        self.setMinimumWidth(620)
        self._session = session
        self._documents, self._source, self._mido = documents, source, mido_module
        column = QVBoxLayout(self)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(10)
        intro = QLabel("Le Studio pilote ton vrai logiciel de musique par MIDI. Choisis-le, puis suis les étapes : "
                       "tout ce qui est « À faire » se règle une seule fois.")
        intro.setWordWrap(True)
        column.addWidget(intro)

        pick = QHBoxLayout()
        pick.addWidget(QLabel("Logiciel :"))
        self.software = QComboBox()
        for target in ss.TARGETS:
            self.software.addItem(target.name, target.key)
        row = self.software.findData(session.guide_target.key)
        self.software.setCurrentIndex(max(0, row))
        self.software.activated.connect(lambda _row: self.refresh())
        pick.addWidget(self.software, 1)
        column.addLayout(pick)

        column.addWidget(self._title("État"))
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.checks)
        column.addWidget(self._title("À faire"))
        self.steps = QLabel()
        self.steps.setWordWrap(True)
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.steps)
        self.ports = QLabel()
        self.ports.setWordWrap(True)
        self.ports.setTextFormat(Qt.TextFormat.RichText)
        self.ports.setOpenExternalLinks(True)
        column.addWidget(self.ports)
        self.result = QLabel()
        self.result.setWordWrap(True)
        column.addWidget(self.result)

        buttons = QHBoxLayout()
        self.install = QPushButton("Installer le script FL Studio")
        self.install.clicked.connect(lambda _checked=False: self.install_script())
        self.recheck = QPushButton("Vérifier de nouveau")
        self.recheck.clicked.connect(lambda _checked=False: self.refresh())
        self.close_button = QPushButton("Fermer")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.install)
        buttons.addStretch(1)
        buttons.addWidget(self.recheck)
        buttons.addWidget(self.close_button)
        column.addLayout(buttons)
        self.refresh()

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("panelTitle")
        return label

    @property
    def target(self) -> ss.StudioTarget:
        return ss.target_for(self.software.currentData()) or ss.TARGETS[0]

    def refresh(self) -> None:
        target = self.target
        checks = ss.setup_checks(target, self._session._cfg, documents=self._documents, source=self._source,
                                 mido_module=self._mido)
        self.checks.setText(checks_html(checks))
        self.steps.setText(steps_html(target.steps))
        self.ports.setText(f"Ports MIDI virtuels à créer avec <a href='{LOOPMIDI_URL}'>loopMIDI</a> : "
                           f"{html.escape(ss.ports_text(target, self._session._cfg))}.")
        self.install.setVisible(target.backend == ss.BACKEND_FL)

    def install_script(self) -> bool:
        """Copie le script dans le dossier de FL Studio ; le résultat est écrit dans la fenêtre."""
        try:
            path = fl_sync.install_script(self._documents, self._source)
        except OSError as exc:
            self.result.setText(f"Installation impossible : {exc}")
            return False
        self.result.setText(f"Script installé : {path}. Redémarre FL Studio si besoin.")
        self.refresh()
        return True


# --------------------------------------------------------------------------- #
# La fenêtre
# --------------------------------------------------------------------------- #
class StudioWindow(QWidget):
    closed = Signal()

    def __init__(self, session: StudioSession, *, cfg: dict | None = None,
                 persist: Callable[[], None] | None = None, interval_ms: int = TICK_MS,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.session = session
        self._cfg = cfg if cfg is not None else {}
        self._persist = persist
        self._banner_state: tuple[str, str] | None = None
        self._panels: tuple[bool, bool, bool] | None = None      # panneaux montrés : rien d'appliqué encore
        self.setObjectName("studioWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(st.stylesheet())
        self.setWindowTitle("VoiceConnector — Studio")
        self.setMinimumSize(*MIN_SIZE)
        self._build()
        self._restore_geometry()
        if not session.opened:
            session.open()
        self._rebind()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    # ------------------------------------------------------------ construction
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QFrame()
        header.setObjectName("studioHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(14, 8, 14, 8)
        bar.setSpacing(10)
        title = QLabel("STUDIO")
        title.setObjectName("studioTitle")
        title.setFont(styles.font(15, QFont.Weight.Bold))
        bar.addWidget(title)
        bar.addWidget(QLabel("Logiciel :"))
        self.software = QComboBox()
        for label, key in software_choices():
            self.software.addItem(label, key)
        self.software.setMinimumWidth(170)
        self.software.setToolTip("Le logiciel de musique que le Studio pilote. « Automatique » suit celui qui est lancé.")
        self.software.activated.connect(self._on_software)
        bar.addWidget(self.software)
        self.project = QLabel("")
        self.project.setObjectName("studioProject")
        bar.addWidget(self.project, 1)
        self.configure = QPushButton("Configurer…")
        self.configure.setToolTip("Ce qui est prêt, ce qui manque, et les étapes pour relier le logiciel")
        self.configure.clicked.connect(lambda _checked=False: self.show_setup())
        self.reconnect = QPushButton("Reconnecter")
        self.reconnect.setToolTip("Retente la liaison avec le logiciel")
        self.reconnect.clicked.connect(lambda _checked=False: self.reconnect_now())
        for button in (self.configure, self.reconnect):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            bar.addWidget(button)
        root.addWidget(header)

        self.banner = QLabel("")
        self.banner.setObjectName("studioBanner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        self.transport = TransportBar()
        root.addWidget(self.transport)

        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setChildrenCollapsible(False)
        self.rack = RackPanel()
        self.playlist = PlaylistPanel()
        self.body.addWidget(self.rack)
        self.body.addWidget(self.playlist)
        self.body.setStretchFactor(0, 3)
        self.body.setStretchFactor(1, 1)
        root.addWidget(self.body, 1)
        self.mixer = MixerPanel()
        root.addWidget(self.mixer, 1)
        self.empty = QLabel("Ce logiciel ne donne ni channel rack ni pistes au Studio.")
        self.empty.setObjectName("emptyHint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty, 1)

        self._connect()
        for key, action in (("Space", self.toggle_play), ("Home", self.rewind)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)

    def _connect(self) -> None:
        bar = self.transport
        bar.rewindRequested.connect(self.rewind)
        bar.playRequested.connect(self.toggle_play)
        bar.stopRequested.connect(lambda: self._act("stop_playback"))
        bar.recordRequested.connect(lambda: self._act("record"))
        bar.modeRequested.connect(lambda: self._act("toggle_song_mode"))
        bar.metronomeRequested.connect(lambda: self._act("toggle_metronome"))
        bar.tempoRequested.connect(lambda bpm: self._set("set_tempo", bpm))
        rack = self.rack
        rack.muteRequested.connect(lambda index, muted: self._act("set_channel_mute", index, muted))
        rack.soloRequested.connect(lambda index, solo: self._act("set_channel_solo", index, solo))
        rack.selectRequested.connect(lambda index: self._act("select_channel", index))
        rack.volumeChanged.connect(lambda index, value: self._set("set_channel_volume", index, value))
        rack.panChanged.connect(lambda index, value: self._set("set_channel_pan", index, value))
        rack.stepChanged.connect(lambda index, step, on: self._set("toggle_step", index, step, on))
        rack.patternRequested.connect(lambda index: self._act("select_pattern", index))
        rack.stepCountRequested.connect(lambda count: self._act("set_step_count", count))
        playlist = self.playlist
        playlist.muteRequested.connect(lambda index, muted: self._act("set_track_mute", index, muted))
        playlist.soloRequested.connect(lambda index, solo: self._act("set_track_solo", index, solo))
        playlist.selectRequested.connect(lambda index: self._act("select_track", index))
        mixer = self.mixer
        mixer.armRequested.connect(lambda index, on: self._act("set_track_arm", index, on))
        mixer.muteRequested.connect(lambda index, muted: self._act("set_track_mute", index, muted))
        mixer.soloRequested.connect(lambda index, solo: self._act("set_track_solo", index, solo))
        mixer.selectRequested.connect(lambda index: self._act("select_track", index))
        mixer.volumeChanged.connect(lambda index, value: self._set("set_track_volume", index, value))
        mixer.panChanged.connect(lambda index, value: self._set("set_track_pan", index, value))
        mixer.masterChanged.connect(lambda value: self._set("set_master_volume", value))
        mixer.bankRequested.connect(lambda delta: self._act("bank", delta))

    # ----------------------------------------------------------------- ordres
    def _call(self, method: str, *args) -> None:
        action = getattr(self.session.link, method, None)
        if callable(action):
            try:
                action(*args)
            except Exception as exc:                  # un ordre refusé ne doit jamais faire tomber la fenêtre
                log.warning("Studio : ordre « %s » impossible : %s", method, exc)

    def _act(self, method: str, *args) -> None:
        """Un ordre ponctuel : la fenêtre se remet tout de suite d'accord avec ce que dit la liaison."""
        self._call(method, *args)
        self.refresh()

    def _set(self, method: str, *args) -> None:
        """Un réglage continu (potard, fader, pas) : l'écran de l'utilisateur est déjà à jour."""
        self._call(method, *args)

    def toggle_play(self) -> None:
        self._act("play_pause")

    def rewind(self) -> None:
        self._act("rewind")

    # ------------------------------------------------------------------ temps
    def tick(self) -> None:
        flags = self.session.poll()
        if flags & ss.SWITCHED:
            self._rebind()
        elif flags & ss.CHANGED:
            self.refresh()

    def _rebind(self) -> None:
        """La liaison a changé (autre logiciel, démonstration, reprise) : les vues repartent de zéro."""
        self.rack.clear()
        self.playlist.clear()
        self.mixer.clear()
        self._panels = None                                # la visibilité des panneaux est refaite
        row = self.software.findData(self.session.selected)
        if row >= 0 and row != self.software.currentIndex():
            self.software.blockSignals(True)
            try:
                self.software.setCurrentIndex(row)
            finally:
                self.software.blockSignals(False)
        self.refresh()

    # ----------------------------------------------------------------- affichage
    def refresh(self) -> None:
        session = self.session
        link = session.link
        state: DawState = link.state
        self._apply_status(session.status())
        self.project.setText(state.project)
        name = state.daw or session.target.name
        self.setWindowTitle(f"VoiceConnector — Studio · {name}" + (" (démonstration)" if session.demo else ""))
        self.transport.update_from(state)
        rack, mixer = state.can(CAP_RACK), state.can(CAP_TRACK_MIX)
        playlist = state.can(CAP_PLAYLIST) and not mixer
        if (rack, playlist, mixer) != self._panels:
            self._panels = (rack, playlist, mixer)
            self.body.setVisible(rack or playlist)
            self.rack.setVisible(rack)
            self.playlist.setVisible(playlist)
            self.mixer.setVisible(mixer)
            self.empty.setVisible(not (rack or playlist or mixer))
        if rack:
            self.rack.set_hint(self._rack_hint(state))
            self.rack.update_from(state, steps_choice=int(getattr(link, "steps_choice", 0) or 0),
                                  playhead=self._playhead(state))
        if playlist:
            self.playlist.update_from(state)
        if mixer:
            self.mixer.update_from(state)

    def _rack_hint(self, state: DawState) -> str:
        if not state.connected:
            return state.message or f"En attente de {state.daw or self.session.target.name}…"
        return "Le channel rack est vide : ajoute des canaux dans le logiciel."

    @staticmethod
    def _playhead(state: DawState) -> int:
        if not state.transport.playing:
            return -1
        pattern = state.current_pattern()
        length = pattern.length_steps if pattern is not None else state.step_count
        return sg.playhead_step(state.transport.step_position(), length or state.step_count)

    def _apply_status(self, status: StudioStatus) -> None:
        key = (status.kind, status.text)
        if key == self._banner_state:
            return
        self._banner_state = key
        self.banner.setText(status.text)
        self.banner.setStyleSheet(st.banner_style(status.kind))
        self.banner.setVisible(bool(status.text))
        self.configure.setVisible(status.configurable or status.kind != "live")

    # ------------------------------------------------------------- en-tête
    def _on_software(self, row: int) -> None:
        key = self.software.itemData(row)
        if key is None:
            return
        self.session.select(str(key))
        self._save()
        self._rebind()

    def reconnect_now(self) -> None:
        self.session.reconnect()
        self._rebind()

    def show_setup(self) -> None:
        dialog = StudioSetupDialog(self.session, self)
        dialog.exec()
        self.reconnect_now()

    # ------------------------------------------------------------ géométrie
    def _restore_geometry(self) -> None:
        saved = (self._cfg.get("studio") or {}).get("geometry") if isinstance(self._cfg.get("studio"), dict) else ""
        if saved and self.restoreGeometry(QByteArray.fromBase64(str(saved).encode("ascii"))):
            return
        self.resize(*DEFAULT_SIZE)

    def _save(self) -> None:
        if self._persist is not None:
            try:
                self._persist()
            except OSError as exc:
                log.error("Config du Studio non sauvegardée : %s", exc)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        section = self._cfg.setdefault("studio", {})
        section["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.session.close()
        self._save()
        self.closed.emit()
        super().closeEvent(event)

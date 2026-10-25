"""
Fenêtre principale « rack VoiceConnector » : logo, entrées matérielles et
virtuelles (tranches crème), matrice de routage, sorties matérielles et
panneau Master, plus le câblage entre l'interface et les moteurs
(Voicemeeter, REAPER, processus).

Fenêtre « comme Voicemeeter » : taille compacte par défaut (strips + master,
sans grand vide), position et taille mémorisées, réduction possible dans la
zone de notification (icône à côté de l'horloge).

Aussi : volume par application (bouton « Apps » des strips virtuelles) et
mises à jour (vérification au démarrage puis régulière, pill dans le Master).

Dispositions, logiciel suivi et mode musical : une barre d'actions (ui/action_bar.py) sous le
haut de la fenêtre. Toutes les décisions viennent du coordinateur de session
(core/session_coordinator.py, sans Qt) ; la fenêtre ne fait que les appliquer :

- `_relayout()` demande le plan de la disposition et réarrange les tranches déjà construites
  (groupes, Master à gauche ou à droite, matrice et sorties montrées ou non) ;
- le mode musical libère les traitements voix inutiles (insert Voicemeeter, compresseur / gate)
  et lance la synchronisation du mixer de FL Studio ;
- en mode autonome (B), la console refait ses tranches quand le profil change.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPointF, QProcess, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout,
                               QWidget)
from shiboken6 import delete as delete_now

from core import config
from core.app_detector import KNOWN_APPS, known_app
from core.app_volume import AppVolumeBackend, NullAppVolumes
from core.detect_service import DetectService
from core.effects import HOST_APO, HOST_NONE, HOST_REAPER
from core.fl_service import FlSyncService
from core.fl_sync import connect_fl, mido_available, role_tracks
from core.layout_manager import VU_PRECISION, LayoutPlan
from core.mixer_router import TRANSPORT_VBAN, VbanStream, VoiceRoute, choose_voice_app, plan_voice_route
from core.process_manager import is_blank_project, reaper_project
from core.profile_import import SOURCE_APP
from core.session_coordinator import SessionChange, SessionCoordinator
from core.session_mode import NO_RELEASE, DynamicsStash
from core.updater import UpdateChecker, UpdateInfo
from core.voicemeeter_ctrl import DEFAULT_ROUTING, MixerLevels, MixerParams
from ui import styles
from ui.action_bar import ActionBar
from ui.app_mixer import AppMixerPopup
from ui.backdrops import paint_backdrop
from ui.hardware_widgets import CHEEK_W, LedMatrix, paint_chassis
from ui.layout_settings import LiveState
from ui.master_widget import MasterWidget, OutputsPanel
from ui.project_dialog import ProjectDialog
from ui.settings_dialog import SETTINGS_TAB_LAYOUT, SettingsDialog
from ui.strip_widget import StripWidget
from ui.update_dialog import UpdateDialog
from ui.vst_dialog import VstDialog

log = logging.getLogger(__name__)

STRIP_GAP = 0                 # les tranches se touchent (filet crème entre elles)
PANEL_GAP = 10
FOCUS_PLUGIN_DELAY_MS = 400
TOAST_MS = 4500
TOAST_MS_PER_CHAR = 55        # un message long reste affiché plus longtemps
TOAST_MAX_MS = 16000
COMPACT_HEIGHT = 900          # hauteur de la fenêtre compacte (comme Voicemeeter)
MIN_HEIGHT = 680
TITLE_BAR_ALLOWANCE = 40      # barre de titre Windows (le cadre n'est connu qu'une fois affiché)
# Augmenté quand la disposition des strips change (0.4 : Intellipan) : la
# taille mémorisée, prévue pour l'ancienne disposition, est alors ignorée.
LAYOUT_VERSION = 4            # 4 : barre d'actions, dispositions (Layout et Apparence)
DEFAULT_DARKNESS = 0.45       # assombrissement de l'image d'arrière-plan
DEFAULT_PANEL_OPACITY = 1.0
APP_COUNT_MS = 2000           # nombre d'applications affiché sur les boutons « Apps »
UPDATE_FIRST_CHECK_MS = 8000  # première vérification des mises à jour après l'ouverture
DEFAULT_UPDATE_HOURS = 6


def background_image_path(cfg: dict) -> Path | None:
    """Image d'arrière-plan choisie (copiée dans %APPDATA%/VoiceConnector), ou None."""
    path = config.resolve_path(cfg.get("ui", {}).get("background_image"), base=config.app_data_dir())
    return path if path is not None and path.is_file() else None


def restart_command() -> tuple[str, list[str]]:
    """Commande qui relance VoiceConnector (exe ou script), avec --restart pour attendre
    que l'instance actuelle ait fini de s'arrêter."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--restart"]
    return sys.executable, [str(Path(sys.argv[0]).resolve()), "--restart"]


def launch_restart() -> bool:
    """Lance la nouvelle instance de VoiceConnector (détachée) ; l'appelant quitte ensuite."""
    program, args = restart_command()
    # Exe PyInstaller « onefile » : sans cette variable, la nouvelle instance
    # se croirait un sous-processus de celle-ci et réutiliserait son dossier
    # temporaire, supprimé dès que cette instance se ferme.
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    result = QProcess.startDetached(program, args)
    # PySide6 renvoie (succès, pid) ; d'autres versions un simple booléen
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        log.error("Impossible de relancer VoiceConnector : %s %s", program, " ".join(args))
        return False
    log.info("Redémarrage de VoiceConnector : %s %s", program, " ".join(args))
    return True


def running_config(cfg: dict, vm) -> dict:
    """La config vue par la session en cours : l'édition de Voicemeeter qui
    tourne, même si une autre a été choisie pour le prochain démarrage (le
    projet vierge doit correspondre aux canaux réellement câblés). Les autres
    sections sont partagées : ce qui y est écrit va dans la vraie config."""
    kind = getattr(getattr(vm, "edition", None), "kind", None)
    if not kind or cfg.get("voicemeeter", {}).get("kind") == kind:
        return cfg
    return {**cfg, "voicemeeter": {**cfg.get("voicemeeter", {}), "kind": kind}}


class Background(QWidget):
    """Arrière-plan : image personnalisée (cadrée pour remplir, assombrie), ou
    noir profond avec un halo radial vert très sombre au centre-droit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._chassis: QPixmap | None = None     # châssis rétro (cache, refait au redimensionnement)
        self._backdrop: QPixmap | None = None    # arrière-plan d'un thème global (cache)
        self._backdrop_key: tuple | None = None
        self._darkness = DEFAULT_DARKNESS

    def set_image(self, path: Path | None, darkness: float) -> None:
        pixmap = QPixmap(str(path)) if path is not None else None
        self._image = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._darkness = max(0.0, min(0.95, darkness))
        self._scaled = None
        self.update()

    @property
    def has_image(self) -> bool:
        return self._image is not None

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._scaled = None
        self._chassis = None
        self._backdrop = None
        super().resizeEvent(event)

    def invalidate(self) -> None:
        """Le thème a changé : les arrière-plans gardés en cache sont refaits au prochain dessin."""
        self._chassis = None
        self._backdrop = None
        self._backdrop_key = None
        self.update()

    def _backdrop_pixmap(self, kind: str) -> QPixmap:
        key = (kind, self.width(), self.height(), styles.current_theme())
        if self._backdrop is None or self._backdrop_key != key:
            pixmap = QPixmap(self.size())
            painter = QPainter(pixmap)
            paint_backdrop(painter, kind, self.width(), self.height())
            painter.end()
            self._backdrop, self._backdrop_key = pixmap, key
        return self._backdrop

    def _chassis_pixmap(self) -> QPixmap:
        if self._chassis is None or self._chassis.size() != self.size():
            pixmap = QPixmap(self.size())
            painter = QPainter(pixmap)
            paint_chassis(painter, self.width(), self.height())
            painter.end()
            self._chassis = pixmap
        return self._chassis

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(styles.BG_WINDOW))
        w, h = self.width(), self.height()
        if self._image is not None:
            if self._scaled is None:
                self._scaled = self._image.scaled(
                    self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((w - self._scaled.width()) // 2, (h - self._scaled.height()) // 2,
                         self._scaled)
            p.fillRect(self.rect(), styles.color(styles.BG_BLACK, self._darkness))
            return
        spec = styles.global_spec()
        if spec is not None:                      # Rétro, Futuriste, Épuré, Studio Pro
            p.drawPixmap(0, 0, self._backdrop_pixmap(spec.backdrop))
            return
        if styles.current_theme() == "vintage":
            p.drawPixmap(0, 0, self._chassis_pixmap())
            return
        gradient = QRadialGradient(QPointF(w * 0.68, h * 0.42), max(w, h) * 0.62)
        gradient.setColorAt(0.0, styles.color(styles.BG_GLOW, 0.95))
        gradient.setColorAt(0.55, styles.color(styles.BG_GLOW, 0.35))
        gradient.setColorAt(1.0, styles.color(styles.BG_GLOW, 0.0))
        p.fillRect(self.rect(), gradient)


class Toast(QLabel):
    """Message discret en bas de la fenêtre, qui s'efface tout seul."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_style()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(400)
        self._fade.finished.connect(self._after_fade)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._animate(0.0))
        self.hide()

    @staticmethod
    def duration_ms(text: str) -> int:
        """Durée d'affichage : un message long (consignes du mode musical) reste plus longtemps."""
        return max(TOAST_MS, min(TOAST_MAX_MS, TOAST_MS_PER_CHAR * len(text)))

    def refresh_style(self) -> None:
        self.setStyleSheet(
            f"background: {styles.BG_COLUMN}; border: 1px solid {styles.GREEN_DIM};"
            f"border-radius: 10px; padding: 8px 14px; color: {styles.TEXT_PRIMARY};")

    def show_message(self, text: str) -> None:
        self.setText(text)
        width = min(560, self.parentWidget().width() - 40)
        self.setFixedWidth(width)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._animate(1.0)
        self._timer.start(self.duration_ms(text))

    def _animate(self, target: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def _after_fade(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.hide()


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, vm, reaper, processes, persist: bool = True,
                 apps: AppVolumeBackend | None = None, updater: UpdateChecker | None = None,
                 parent: QWidget | None = None, *, session: SessionCoordinator | None = None,
                 detector: DetectService | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._persist = persist          # False en mode démo : la config n'est pas touchée
        self._vm = vm
        self._reaper = reaper
        self._processes = processes
        self._apps = apps or NullAppVolumes()
        self._updater = updater
        self._update: UpdateInfo | None = None
        self._app_mixer: AppMixerPopup | None = None
        self._params: MixerParams | None = None
        self._vm_ok = True
        self._reaper_ok = reaper.is_connected
        self._device_warned = False
        self._tray_hint_shown = False
        self._tracks: list = reaper.strip_tracks   # piste REAPER de chaque strip (ou None)
        self._missing_features: set[str] = set()
        self._solo: set[int] = set()
        self._pre_solo: dict[int, bool] = {}

        # Session : logiciel suivi, mode musical, disposition (décisions dans core/session_coordinator.py)
        self._session = session or SessionCoordinator(cfg, host_kind=reaper.kind,
                                                      fl_sync_available=mido_available())
        self._standalone = self._session.standalone       # mode B : sans Voicemeeter
        self._detector = detector
        self._plan: LayoutPlan | None = None
        self._release = NO_RELEASE                       # canaux dont le traitement voix est libéré
        self._stash = DynamicsStash()                    # compresseur / gate de ces canaux, mis de côté
        self._fl_sync: FlSyncService | None = None
        self._fl_linked: dict[int, int] = {}              # tranche → piste de FL Studio, pendant la synchro
        self._fl_notice = ""                             # dernière raison d'échec déjà annoncée
        self._presence: tuple[bool, ...] = ()
        self._engine_status = ""
        self._groups: list[QFrame] = []
        self.strips: list[StripWidget] = []
        self.matrix: LedMatrix | None = None

        cfg.setdefault("ui", {})
        self.setWindowTitle(config.APP_NAME)

        self.background = Background()
        self.setCentralWidget(self.background)
        outer = QVBoxLayout(self.background)
        outer.setContentsMargins(CHEEK_W + 14, 8, CHEEK_W + 14, 14)
        outer.setSpacing(6)

        self.action_bar = ActionBar()
        self.action_bar.set_mode(self._standalone)
        self.action_bar.musicToggled.connect(self._on_music_toggled)
        self.action_bar.layoutChosen.connect(self._on_layout_chosen)
        self.action_bar.settingsRequested.connect(lambda: self.open_settings(SETTINGS_TAB_LAYOUT))
        outer.addWidget(self.action_bar)

        self.body = QHBoxLayout()
        self.body.setSpacing(PANEL_GAP)
        outer.addLayout(self.body, 1)

        self._build_panels()
        self.toast = Toast(self.background)
        self._tray = self._make_tray()

        # Logiciel suivi (première détection), puis les tranches et leur disposition
        if not self._session.started:
            first = self._detector.scan_now() if self._detector is not None else []
            self._session.start(first)
        self._build_rack()
        self._relayout(resize=False)
        self.apply_appearance()
        self._restore_geometry()

        # Moteurs → interface
        vm.params_changed.connect(self._on_params)
        vm.levels_changed.connect(self._on_levels)
        vm.connection_changed.connect(self._on_vm_connection)
        reaper.tracks_changed.connect(self._on_tracks)
        reaper.connection_changed.connect(self._on_reaper_connection)
        reaper.device_changed.connect(self._on_device)
        reaper.project_changed.connect(self._on_project)
        reaper.command_failed.connect(self.toast.show_message)
        if self._standalone:
            self._connect_standalone()

        self._refresh_status()
        self._on_tracks(self._tracks)
        self._on_project(reaper.project_path)
        if reaper.device is not None:
            self._on_device(reaper.device)

        # Démarrage : état sauvegardé, noms des strips dans Voicemeeter, scrutation
        state = cfg.get("state") or {}
        if state.get("strips"):
            vm.apply_state(state)
        for strip in self.strips:
            vm.set_label(strip.index, strip.strip_name())
        vm.start_polling()

        # Détection des logiciels audio : une fois toutes les quelques secondes, en tâche de fond
        if self._detector is not None:
            self._detector.scanned.connect(self._on_detected)
            self._detector.set_interval(self._session.session.detect_interval_s)
            self._detector.start()
        if self._session.music_on:
            QTimer.singleShot(0, self._announce_music_mode)

        # Volume par application : nombre d'applications sur chaque bouton « Apps »
        self._app_timer = QTimer(self)
        self._app_timer.setInterval(APP_COUNT_MS)
        self._app_timer.timeout.connect(self.refresh_app_counts)
        if any(strip.apps is not None for strip in self.strips):
            self._app_timer.start()
            QTimer.singleShot(0, self.refresh_app_counts)

        # Mises à jour : au démarrage (option), puis régulièrement
        self._update_timer = QTimer(self)
        if updater is not None:
            updater.available.connect(self._on_update_available)
            hours = float(cfg.get("update", {}).get("interval_hours", DEFAULT_UPDATE_HOURS) or 0)
            if cfg.get("update", {}).get("check_on_start", True) and updater.enabled:
                QTimer.singleShot(UPDATE_FIRST_CHECK_MS, updater.check)
                if hours > 0:
                    self._update_timer.setInterval(int(hours * 3600 * 1000))
                    self._update_timer.timeout.connect(updater.check)
                    self._update_timer.start()
            if updater.latest is not None:
                self._on_update_available(updater.latest)

    # ------------------------------------------------- rack : panneaux et tranches
    def _build_panels(self) -> None:
        """Panneaux qui ne changent pas avec la disposition : matrice, sorties et Master."""
        self._matrix_panel = QFrame()
        self._matrix_panel.setObjectName("darkGroup")
        self._matrix_box = QVBoxLayout(self._matrix_panel)
        self._matrix_box.setContentsMargins(6, 6, 6, 6)
        self._matrix_box.setSpacing(4)
        matrix_title = QLabel("VOICE CONNECTOR MATRIX")
        matrix_title.setObjectName("matrixTitle")
        matrix_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._matrix_box.addWidget(matrix_title)
        self._matrix_box.addStretch(1)

        self.outputs = OutputsPanel()
        self.outputs.gainChanged.connect(self._on_output_gain)

        self.master = MasterWidget()
        self.master.headphoneChanged.connect(self._on_master_gain)
        self.master.settingsRequested.connect(self.open_settings)
        self.master.projectRequested.connect(self._on_project_button)
        self.master.minimizeRequested.connect(self.minimize)
        self.master.updateRequested.connect(self.open_update)
        self.master.muteAllRequested.connect(self._mute_all)
        self.master.ejectAllRequested.connect(self._eject_all)
        self.master.eqChanged.connect(self._on_eq)
        self.master.tape.recordToggled.connect(self._on_record)
        self.master.tape.playRequested.connect(self._on_play)
        self.master.tape.stopRequested.connect(self._on_stop_tape)
        self.master.tape.abToggled.connect(self._on_ab)
        if self._standalone:
            self.master.set_voicemeeter_tools(False)     # magnéto, égaliseur Intellipan, « eject » : propres à Voicemeeter
            self.outputs.limit_to(1)                     # une seule sortie : le volume général de Windows

    def _routing(self) -> tuple[list[str], list[str], list[str]]:
        """(libellés, bus, couleurs) des routages rapides, d'après la config."""
        routing = self._cfg.get("voicemeeter", {}).get("routing", DEFAULT_ROUTING)
        labels = [route.get("label", route.get("bus", "?")) for route in routing]
        buses = [route.get("bus", "?") for route in routing]
        return labels, buses, [styles.bus_color(bus) for bus in buses]

    def _build_rack(self) -> None:
        """(Re)crée les tranches et la matrice d'après la console : au démarrage, et en mode autonome
        quand le profil change (autres canaux). La disposition les range ensuite (`_apply_plan`)."""
        vm = self._vm
        ui_cfg = self._cfg.setdefault("ui", {})
        for strip in self.strips:
            strip.hide()
            strip.setParent(self.background)
            strip.deleteLater()
        self.strips = []
        if self.matrix is not None:
            self._matrix_box.removeWidget(self.matrix)
            self.matrix.hide()
            self.matrix.deleteLater()
            self.matrix = None

        labels, buses, colors = self._routing()
        engine_names = [info.name for info in vm.strip_infos()]
        saved_names = list(ui_cfg.get("strip_names", []))
        sends = bool(getattr(vm, "supports_sends", True))
        pad_modes = list(ui_cfg.get("pad_modes", []))
        strip_names: list[str] = []
        for index in range(vm.strip_count):
            if self._standalone:                       # les noms viennent du profil
                name = engine_names[index] if index < len(engine_names) and engine_names[index] \
                    else f"Canal {index + 1}"
            else:
                name = saved_names[index] if index < len(saved_names) else f"Strip {index + 1}"
            virtual = vm.virtual_input(index)
            strip = StripWidget(index, name, labels, sends_adjustable=sends,
                                pad_modes=vm.pad_modes(index), physical=vm.is_physical(index),
                                pad_mode=pad_modes[index] if index < len(pad_modes) else None,
                                apps_button=(not self._standalone) and self._apps.available
                                and virtual is not None,
                                route_colors=colors)
            self._wire_strip(strip)
            strip.configure_effects(self._reaper.add_label, self._reaper.strip_hint(index),
                                    self._reaper.manages_fx, *self._chain_tooltips())
            strip.setParent(self.background)
            strip.hide()
            self.strips.append(strip)
            strip_names.append(name)

        self.matrix = LedMatrix(strip_names, buses, labels)
        self.matrix.routeToggled.connect(vm.set_route)
        self._matrix_box.insertWidget(1, self.matrix, 0, Qt.AlignmentFlag.AlignHCenter)
        self._restore_hardware_settings()
        if self._standalone:
            self._refresh_assignable()
            self._on_presence(self._presence)

    # ------------------------------------------------- disposition (plan)
    def _relayout(self, resize: bool = True) -> None:
        """Demande la disposition au coordinateur et réarrange la console d'après elle."""
        names = [strip.strip_name() for strip in self.strips]
        plan = self._session.plan(self._vm, names)
        self._apply_plan(plan, resize)
        self._apply_release(plan)
        precision = any(planned.style.vu == VU_PRECISION for planned in plan.strips)
        self.master.set_precision(precision)
        self.outputs.set_precision(precision)
        self._update_fl_sync()
        self._refresh_bar()

    def _apply_plan(self, plan: LayoutPlan, resize: bool = True) -> None:
        """Range les tranches déjà construites dans les groupes du plan (aucune n'est recréée), montre
        ou cache la matrice et les sorties, et met le Master du bon côté."""
        self._plan = plan
        for strip in self.strips:
            strip.hide()
            strip.setParent(self.background)          # hors de son groupe, invisible, mais bien vivante
            self._mark_last(strip, False)
        for frame in self._groups:
            self.body.removeWidget(frame)
            frame.hide()
            frame.deleteLater()
        self._groups = []
        for group in plan.groups:
            frame, row = self._make_group(group.title)
            members = [self.strips[i] for i in group.strips if i < len(self.strips)]
            for strip in members:
                row.addWidget(strip)
                strip.show()
            if members:
                self._mark_last(members[-1], True)
            self._groups.append(frame)
        for planned in plan.strips:
            if planned.index < len(self.strips):
                self.strips[planned.index].apply_style(planned.style, planned.label)
        self._arrange_body(plan)
        self._fit_to_layout(resize)

    def _arrange_body(self, plan: LayoutPlan) -> None:
        """Ordre des panneaux : [Master] groupes de tranches, matrice, sorties [Master]."""
        for widget in (self._matrix_panel, self.outputs, self.master):
            self.body.removeWidget(widget)
        order: list[QWidget] = list(self._groups)
        if plan.show_matrix:
            order.append(self._matrix_panel)
        if plan.show_outputs:
            order.append(self.outputs)
        if plan.master_side == "left":
            order.insert(0, self.master)
        else:
            order.append(self.master)
        for widget in order:
            self.body.addWidget(widget)
            widget.show()
        self._matrix_panel.setVisible(plan.show_matrix)
        self.outputs.setVisible(plan.show_outputs)

    def _fit_to_layout(self, resize: bool) -> None:
        """La fenêtre suit la largeur de la disposition : jamais plus étroite que ses tranches,
        et recalée dessus quand on change de disposition en cours de route."""
        layout = self.background.layout()
        layout.activate()
        width = layout.sizeHint().width()
        self.setMinimumWidth(width)
        if resize and not (self.isMaximized() or self.isFullScreen()) and self.width() != width:
            self.resize(width, self.height())

    @staticmethod
    def _mark_last(strip: QWidget, last: bool) -> None:
        """Propriété « last » (pas de filet à droite de la dernière tranche d'un groupe)."""
        if bool(strip.property("last")) != last:
            strip.setProperty("last", last)
            strip.style().unpolish(strip)
            strip.style().polish(strip)

    # ------------------------------------------- session : événements et barre
    def _refresh_bar(self) -> None:
        bar, session = self.action_bar, self._session
        bar.set_mode(self._standalone)
        bar.set_active_app(session.active)
        bar.set_music_state(session.button())
        bar.set_layout_choice(session.layouts.selector_value)
        bar.set_sync(session.sync_connected if self._fl_sync is not None else None)

    def _apply_change(self, change: SessionChange) -> None:
        """Applique ce que le coordinateur a décidé après un événement (détection, clic, réglage)."""
        if change.music is not None and change.music.message and not (
                change.music.on and change.music.reason == "toggle"):
            self.toast.show_message(change.music.message)
        if change.note:
            self.toast.show_message(change.note)
        if change.profile is not None and self._standalone:
            self._vm.set_profile(change.profile)         # → structure_changed → tranches + disposition
        elif change.any:
            self._relayout()
        else:
            self._refresh_bar()
        if change.music is not None and change.music.on and change.music.reason in ("toggle", "restore"):
            self._announce_music_mode()

    def _on_detected(self, detected: list) -> None:
        self._apply_change(self._session.on_detected(detected))

    def _on_layout_chosen(self, choice: str) -> None:
        self._apply_change(self._session.set_layout(choice))

    def _on_music_toggled(self, on: bool) -> None:
        self._apply_change(self._session.toggle_music(on))
        self._refresh_bar()                              # un refus (logiciel vocal) décoche le bouton

    def _announce_music_mode(self) -> None:
        """Consignes du mode musical : retour ASIO direct, canaux libérés."""
        if self._plan is None or not self._session.music_on:
            return
        lines = self._session.help_lines(self._plan)
        self.toast.show_message("Mode musical : disposition studio.\n" + "\n".join(lines))

    # -------------------------------------------- mode musical : libérer la voix
    def _apply_release(self, plan: LayoutPlan) -> None:
        """Libère (ou rétablit) les traitements voix selon le mode musical : insert Voicemeeter et
        compresseur / gate des canaux vocaux ou cachés. Le mode B n'a rien à libérer."""
        release = self._session.release(plan)
        self._release = release
        why = ("Traitement voix libéré (mode musical) : sans insert ni compresseur / gate, "
               "moins de latence.")
        for strip in self.strips:
            strip.set_released(strip.index in release, why)
        current = {strip.index: (strip.comp.value(), strip.gate.value()) for strip in self.strips}
        for index, (comp, gate) in self._stash.sync(sorted(release.strips), current).items():
            self._send_dynamics("set_comp", index, comp, "Compresseur", announce=False)
            self._send_dynamics("set_gate", index, gate, "Gate", announce=False)
        self.update_inserts()

    # ----------------------------------------- mode musical : synchro FL Studio
    def _update_fl_sync(self) -> None:
        wanted = self._session.wants_fl_sync
        if wanted and self._fl_sync is None:
            self._start_fl_sync()
        elif not wanted and self._fl_sync is not None:
            self._stop_fl_sync()
        elif wanted and self._fl_sync is not None and self._plan is not None:
            self._restart_fl_links()

    def _fl_links(self) -> dict[int, int]:
        """Tranche → piste de FL Studio, d'après les rôles de la disposition en cours."""
        roles = self._plan.roles if self._plan is not None else {}
        return role_tracks(self._cfg, roles)

    def _start_fl_sync(self) -> None:
        links = self._fl_links()
        if not links:
            self._notify_fl("Aucune tranche de la disposition n'est liée à une piste de FL Studio.")
            return
        core, reason = connect_fl(self._cfg, watch=links.values())
        if core is None:
            self._notify_fl(f"Synchronisation du mixer de FL Studio indisponible : {reason}")
            return
        self._fl_linked = dict(links)
        service = FlSyncService(core, links, self._vm, parent=self)
        service.connection_changed.connect(self._on_fl_connection)
        service.solo_changed.connect(self._on_fl_solo)
        self._vm.params_changed.connect(service.on_params)
        self._fl_sync = service
        self._fl_notice = ""
        service.start()
        if self._params is not None:
            service.on_params(self._params)

    def _restart_fl_links(self) -> None:
        """La disposition a changé en mode musical : les liens tranche → piste sont recalculés."""
        if self._fl_sync is not None and self._fl_linked != self._fl_links():
            self._stop_fl_sync()
            self._start_fl_sync()

    def _stop_fl_sync(self) -> None:
        service, self._fl_sync = self._fl_sync, None
        self._session.sync_connected = False
        if service is None:
            return
        try:
            self._vm.params_changed.disconnect(service.on_params)
        except (RuntimeError, TypeError):
            pass
        service.stop()
        service.deleteLater()

    def _notify_fl(self, reason: str) -> None:
        """Raison pour laquelle la synchronisation n'a pas démarré : dite une fois, pas à chaque détection."""
        self._session.sync_connected = False
        if reason != self._fl_notice:
            self._fl_notice = reason
            self.toast.show_message(reason)

    def _on_fl_connection(self, connected: bool) -> None:
        self._session.sync_connected = connected
        if connected:
            self.toast.show_message("Mixer de FL Studio synchronisé : faders, muets, solos et panoramiques.")
        self._refresh_bar()

    def _on_fl_solo(self, strip: int, solo: bool) -> None:
        """Solo décidé dans FL Studio : la tranche l'affiche, sans couper les autres entrées."""
        if 0 <= strip < len(self.strips):
            self.strips[strip].set_solo(solo)

    # ------------------------------------------------------------- mode autonome
    def _connect_standalone(self) -> None:
        vm = self._vm
        vm.presence_changed.connect(self._on_presence)
        vm.status_changed.connect(self._on_engine_status)
        vm.solo_changed.connect(self._on_engine_solos)
        vm.structure_changed.connect(self._on_structure_changed)

    def _on_structure_changed(self) -> None:
        """Autre profil (logiciel détecté ou importé) : nouvelles tranches, nouvelle disposition."""
        self._params = None
        self._solo.clear()
        self._pre_solo.clear()
        reason = self._vm.ensure_fl(self._cfg)
        if reason:
            self._notify_fl(f"Pistes de FL Studio indisponibles : {reason}")
        self._build_rack()
        for strip in self.strips:
            self._vm.set_label(strip.index, strip.strip_name())
        self._relayout()
        self._on_tracks(self._tracks)
        self._refresh_status()
        if self._plan is not None and self._plan.groups:
            self.toast.show_message(f"Profil « {self._vm.profile.name} » : {len(self.strips)} canaux.")

    def _on_presence(self, presence) -> None:
        """Mode autonome : une tranche dont l'application n'est pas lancée est grisée."""
        self._presence = tuple(presence)
        for strip in self.strips:
            present = self._presence[strip.index] if strip.index < len(self._presence) else True
            label = self._vm.source_label(strip.index) if self._standalone else ""
            strip.set_present(present, "" if present else f"{label} : pas lancé")

    def _on_engine_status(self, text: str) -> None:
        self._engine_status = text
        self._refresh_status()

    def _on_engine_solos(self, solos: dict) -> None:
        """Solo des pistes de FL Studio (mode autonome) : les tranches l'affichent."""
        for index, solo in (solos or {}).items():
            if 0 <= index < len(self.strips):
                self.strips[index].set_solo(bool(solo))

    def _refresh_assignable(self) -> None:
        """Clic droit sur le nom : « Assigner une application… » pour les canaux d'application."""
        profile = self._vm.profile
        overrides = self._session.session.overrides(profile.name)
        for strip in self.strips:
            channel = profile.channels[strip.index] if strip.index < len(profile.channels) else None
            is_app = channel is not None and channel.source.type == SOURCE_APP
            strip.set_assignable(is_app, bool(overrides.get(strip.index, {}).get("app")))

    def _on_assign_requested(self, index: int) -> None:
        from PySide6.QtWidgets import QInputDialog

        if not 0 <= index < len(self.strips):
            return
        names = [app.name for app in KNOWN_APPS if app.exe_names or app.exe_patterns]
        text, ok = QInputDialog.getItem(
            self, "Assigner une application",
            f"Application de « {self.strips[index].strip_name()} »\n"
            "(un logiciel de la liste, ou le nom de son processus, par exemple « chrome.exe ») :",
            names, 0, True)
        text = text.strip()
        if not ok or not text:
            return
        by_name = {app.name.lower(): app.key for app in KNOWN_APPS}
        key = by_name.get(text.lower()) or (known_app(text).key if known_app(text) is not None else text)
        self._vm.assign_app(index, key)
        self._session.session.set_override(self._vm.profile.name, index, app=key)
        self._refresh_assignable()
        self._save_config()

    def _on_unassign_requested(self, index: int) -> None:
        self._session.session.set_override(self._vm.profile.name, index, app="")
        self._save_config()
        self._apply_change(self._session.reload_profile())

    # ------------------------------------------ mode A : application vocale
    def _voice_route(self) -> VoiceRoute | None:
        """Routage conseillé vers l'application vocale (Discord, OBS, TeamSpeak, Audacity…).
        None en mode autonome (pas de bus) ou quand aucune application vocale n'est à alimenter."""
        if self._standalone:
            return None
        session = self._session
        app = choose_voice_app(session.session.voice_app, session.active, session.detected)
        if app is None:
            return None
        edition = getattr(self._vm, "edition", None)
        buses = tuple(getattr(edition, "buses", ()) or ("B1", "B2"))
        routing = self._cfg.get("voicemeeter", {}).get("routing", DEFAULT_ROUTING)
        return plan_voice_route(
            app, session.session.transport(app.key), routing=routing, buses=buses,
            preferred_bus=session.session.voice_bus(app.key), plan=self._plan,
            physical=[self._vm.is_physical(i) for i in range(self._vm.strip_count)],
            vban=VbanStream.from_mapping(session.session.vban),
            strip_names=[strip.strip_name() for strip in self.strips])

    def _live_state(self) -> LiveState:
        """Ce que l'onglet « Layout et Apparence » montre de l'état en cours."""
        return LiveState(names=[strip.strip_name() for strip in self.strips], route=self._voice_route(),
                         params=self._params,
                         profile_name=self._vm.profile.name if self._standalone else "",
                         profile=self._vm.profile if self._standalone else None)

    def _apply_voice_route(self) -> None:
        """« Appliquer le routage » : la voix part vers le bus de l'application vocale, et ce qui ferait
        écho est coupé. Rien n'est modifié sans ce clic."""
        route = self._voice_route()
        if route is None:
            self.toast.show_message("Aucune application vocale à alimenter : lance Discord, OBS, TeamSpeak "
                                    "ou Audacity, ou choisis-en une dans Réglages → Layout et Apparence.")
            return
        pending = route.pending(self._params)
        for command in pending:
            self._vm.set_route(command.strip, command.route, command.enabled)
        vban = route.vban_params()
        if vban:
            self._vm.set_optional(vban)
        if route.transport == TRANSPORT_VBAN:
            message = f"{route.summary()} : réglages du flux VBAN envoyés (expérimental)."
        elif route.route is None:
            message = f"{route.summary()} : ce bus n'a pas de bouton dans la matrice, règle-le dans Voicemeeter."
        elif pending:
            message = f"{route.summary()} : {len(pending)} route{'s' if len(pending) > 1 else ''} réglée" \
                      f"{'s' if len(pending) > 1 else ''}."
        else:
            message = f"{route.summary()} : routage déjà conforme."
        self.toast.show_message(message)

    # ---------------------------------------------------------- câblage
    def _wire_strip(self, strip: StripWidget) -> None:
        vm, reaper = self._vm, self._reaper
        strip.gainChanged.connect(vm.set_gain)
        strip.muteToggled.connect(vm.set_mute)
        strip.routeToggled.connect(vm.set_route)
        strip.sendChanged.connect(vm.set_send)
        strip.padMoved.connect(vm.set_pad)
        strip.padModeChanged.connect(self._on_pad_mode)
        strip.appsRequested.connect(self.open_app_mixer)
        strip.renamed.connect(self._on_renamed)
        strip.chainToggled.connect(reaper.set_chain_enabled)
        strip.fxAddRequested.connect(self.add_effect)
        strip.fxOpenRequested.connect(self._open_effect)
        strip.fxBypassRequested.connect(reaper.set_fx_enabled)
        strip.fxRemoveRequested.connect(self._remove_effect)
        strip.soloToggled.connect(self._on_solo)
        strip.trimChanged.connect(self._on_trim)
        strip.compChanged.connect(self._on_comp)
        strip.gateChanged.connect(self._on_gate)
        strip.eqChanged.connect(self._on_strip_eq)
        strip.assignRequested.connect(self._on_assign_requested)
        strip.unassignRequested.connect(self._on_unassign_requested)

    # ------------------------------------------------------- moteurs → UI
    def _on_params(self, params: MixerParams) -> None:
        self._params = params
        for strip, strip_params in zip(self.strips, params.strips):
            strip.set_params(strip_params)
        self.matrix.set_params(params.strips)
        self.master.set_headphone(params.headphone_gain)
        self.outputs.set_gain(0, params.headphone_gain)

    def _on_levels(self, levels: MixerLevels) -> None:
        for strip, strip_levels in zip(self.strips, levels.strips):
            strip.set_levels(strip_levels)
        self.master.set_levels(levels.master)
        self.outputs.set_levels(levels.master, getattr(levels, "buses", None))

    def _chain_tooltips(self) -> tuple[str, str]:
        """Info-bulles du badge FX (chaîne active / en bypass) selon le moteur."""
        if self._reaper.kind == "flstudio":
            return ("Son envoyé à FL Studio (insert Voicemeeter) — clic pour le faire passer "
                    "sans effets",
                    "Son sans effets — clic pour l'envoyer à FL Studio (piste du mixer câblée "
                    "sur l'insert de la strip)")
        return ("Effets actifs — clic pour tout bypasser", "Effets en bypass — clic pour les réactiver")

    def _on_tracks(self, tracks: list) -> None:
        """tracks : la piste (REAPER) ou la chaîne d'effets (autres moteurs) de
        chaque strip ; None si aucune piste du projet n'écoute ses canaux."""
        self._tracks = list(tracks)
        host = self._reaper
        create = (host.kind != HOST_REAPER
                  or bool(self._cfg.get("reaper", {}).get("manage_routing", True)))
        for strip in self.strips:
            track = tracks[strip.index] if strip.index < len(tracks) else None
            strip.set_track(track)
            strip.set_fx_hint(host.strip_hint(strip.index))
            strip.set_reaper_available(self._reaper_ok and host.kind != HOST_NONE,
                                       track is not None or create)
            strip.set_latency(host.strip_latency_ms(strip.index),
                              [host.fx_latency_ms(strip.index, i)
                               for i in range(len(track.fx) if track is not None else 0)])
        self.update_inserts()

    def _on_vm_connection(self, ok: bool) -> None:
        self._vm_ok = ok
        self._refresh_status()

    def _on_reaper_connection(self, ok: bool) -> None:
        self._reaper_ok = ok
        self._on_tracks(self._tracks)
        self._refresh_status()

    def _on_project(self, path: str) -> None:
        host = self._reaper
        if self._standalone:
            self.master.project.setVisible(False)          # pas de moteur d'effets en mode autonome
            return
        if not host.has_projects:
            # Pas de projet VoiceConnector : le bouton ouvre l'application du moteur
            names = {"flstudio": ("FL Studio", "Clic : afficher FL Studio pour régler ses effets"),
                     HOST_APO: ("Equalizer APO", "Clic : ouvrir l'éditeur d'Equalizer APO")}
            name, tip = names.get(host.kind, ("Voicemeeter seul", "Aucun moteur d'effets"))
            self.master.set_project(name, "", tip)
            self.master.project.setVisible(host.kind != HOST_NONE)
            return
        name = Path(path).stem if path else "…"
        if path and is_blank_project(path):
            name = "Projet vierge"
        self.master.set_project(name, path or "(en attente de REAPER)")

    def _on_project_button(self) -> None:
        if self._reaper.has_projects:
            self.choose_project()
        else:
            self._reaper.show_app()

    def update_inserts(self) -> None:
        """Insert Voicemeeter activé seulement pour les strips dont le moteur
        d'effets traite vraiment le son (REAPER : piste armée, écoutée, avec une
        sortie, et carte son sur le pilote de l'insert ; FL Studio : badge FX
        allumé) : sinon la strip serait muette. Tout le reste passe sans effets."""
        manage = bool(self._cfg.get("voicemeeter", {}).get("manage_insert", True))
        strips = self._reaper.insert_strips(self._tracks, self._reaper_ok) if manage else set()
        self._vm.set_inserts(set(strips) - set(self._release.strips))   # mode musical : voix libérées

    def _refresh_status(self) -> None:
        if not self._vm_ok:
            if self._standalone:
                self.master.set_status(False, "Audio Windows indisponible",
                                       self._engine_status or "Les volumes de Windows ne sont pas joignables.")
            else:
                self.master.set_status(False, "Voicemeeter déconnecté",
                                       "Voicemeeter ne répond plus : faders et routing sont figés.")
        elif self._standalone:
            ok, text, detail = self._reaper.status(self._reaper_ok)
            self.master.set_status(ok, text, self._engine_status or detail)
        else:
            self.master.set_status(*self._reaper.status(self._reaper_ok))

    def _on_device(self, device) -> None:
        manage = bool(self._cfg.get("voicemeeter", {}).get("manage_insert", True))
        is_insert = device.is_voicemeeter_insert
        self.update_inserts()
        if manage and not is_insert and not self._device_warned:
            self._device_warned = True
            self.toast.show_message(
                "Les effets REAPER ne s'appliquent pas encore : choisis « Voicemeeter Insert "
                "Virtual ASIO » comme carte son de REAPER (⚙ Réglages → préférences audio).")

    # ------------------------------------------------ rack : groupes et réglages
    @staticmethod
    def _make_group(title: str) -> tuple[QFrame, QHBoxLayout]:
        """Groupe crème (titre + rangée de tranches) ; renvoie (cadre, rangée)."""
        group = QFrame()
        group.setObjectName("creamGroup")
        box = QVBoxLayout(group)
        box.setContentsMargins(4, 6, 4, 4)
        box.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("creamTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(STRIP_GAP)
        box.addLayout(row, 1)
        return group, row

    def _hardware_cfg(self) -> dict:
        return self._cfg.setdefault("ui", {}).setdefault("hardware", {})

    def _remember(self, key: str, value, index: int | None = None) -> None:
        """Retient un réglage que le moteur ne porte pas (trim, compr., EQ…)."""
        store = self._hardware_cfg()
        if index is None:
            store[key] = round(float(value), 2)
            return
        values = store.setdefault(key, {})
        values[str(index)] = round(float(value), 2)

    def _restore_hardware_settings(self) -> None:
        store = self._hardware_cfg()
        for strip in self.strips:
            key = str(strip.index)
            strip.set_trim(float(store.get("trim", {}).get(key, 0.0)))
            strip.set_comp_gate(float(store.get("comp", {}).get(key, 0.0)),
                                float(store.get("gate", {}).get(key, 0.0)))
        for band in ("bass", "mid", "treble"):
            self.master.set_eq(band, float(store.get("eq", {}).get(band, 0.0)))

    def _engine_call(self, method: str, *args, feature: str) -> bool:
        """Appelle `method` sur le moteur Voicemeeter s'il la propose. Sinon le
        réglage reste dans l'interface (mémorisé) et on le dit une fois."""
        function = getattr(self._vm, method, None)
        if callable(function):
            function(*args)
            return True
        if feature not in self._missing_features:
            self._missing_features.add(feature)
            self.toast.show_message(f"{feature} : pas encore relié au moteur audio — "
                                    "le réglage est mémorisé dans l'interface.")
        return False

    def _on_trim(self, index: int, db: float) -> None:
        self._remember("trim", db, index)
        self._engine_call("set_trim", index, db, feature="Trim")

    def _on_comp(self, index: int, value: float) -> None:
        self._remember("comp", value, index)
        if self._stash.remember(index, comp=value):      # canal libéré (mode musical) : rétabli à la sortie
            return
        self._send_dynamics("set_comp", index, value, "Compresseur")

    def _on_gate(self, index: int, value: float) -> None:
        self._remember("gate", value, index)
        if self._stash.remember(index, gate=value):
            return
        self._send_dynamics("set_gate", index, value, "Gate")

    def _has_dynamics(self, index: int) -> bool:
        """Le moteur a-t-il un compresseur / gate pour cette tranche (entrées matérielles de Voicemeeter) ?"""
        params = self._params
        return (params is not None and index < len(params.strips)
                and params.strips[index].dynamics is not None)

    def _send_dynamics(self, method: str, index: int, value: float, feature: str,
                       announce: bool = True) -> None:
        if self._has_dynamics(index):
            self._engine_call(method, index, value, feature=feature)
        elif announce and feature not in self._missing_features:
            self._missing_features.add(feature)
            self.toast.show_message(f"{feature} : le moteur audio n'en a pas pour cette entrée — "
                                    "le réglage est mémorisé dans l'interface.")

    def _on_strip_eq(self, index: int, band: int, db: float) -> None:
        """Égaliseur rapide d'une tranche (disposition Podcasting)."""
        self._engine_call("set_strip_eq", index, band, db, feature="Égaliseur rapide")

    def _on_eq(self, band: str, db: float) -> None:
        store = self._hardware_cfg().setdefault("eq", {})
        store[band] = round(float(db), 2)
        self._engine_call("set_eq", band, db, feature="Égaliseur Intellipan")

    # ---- master et sorties : A1 = bus du casque, MASTER = le même volume
    def _on_master_gain(self, db: float) -> None:
        self._vm.set_headphone_gain(db)
        self.outputs.set_gain(0, db)

    def _on_output_gain(self, output: int, db: float) -> None:
        if output == 0:
            self._vm.set_headphone_gain(db)
            self.master.set_headphone(db)
        else:
            self._engine_call("set_bus_gain", output, db, feature=f"Sortie A{output + 1}")

    # ---- solo / mute all / eject all
    def _on_solo(self, index: int, on: bool) -> None:
        """Solo = toutes les entrées non solo sont coupées ; à la fin du solo,
        les états de mute d'avant sont rétablis. Le solo d'une tranche liée à une piste de FL
        Studio est celui de FL Studio : il ne coupe rien d'autre dans la console."""
        if self._fl_sync is not None and self._fl_sync.push_solo(index, on):
            return
        if self._standalone and self._vm.native_solo(index):
            self._vm.set_solo(index, on)
            return
        if on:
            if not self._solo:
                self._pre_solo = {s.index: s.mute.isChecked() for s in self.strips}
            self._solo.add(index)
        else:
            self._solo.discard(index)
        for strip in self.strips:
            if self._solo:
                muted = strip.index not in self._solo
            else:
                muted = self._pre_solo.get(strip.index, strip.mute.isChecked())
            self._vm.set_mute(strip.index, muted)
        if not self._solo:
            self._pre_solo = {}

    def _mute_all(self) -> None:
        """Coupe toutes les entrées ; si tout est déjà coupé, les rétablit."""
        mute = not all(strip.mute.isChecked() for strip in self.strips)
        for strip in self.strips:
            if strip.solo.isChecked():
                strip.set_solo(False)
        self._solo.clear()
        self._pre_solo = {}
        for strip in self.strips:
            self._vm.set_mute(strip.index, mute)

    def _eject_all(self) -> None:
        answer = QMessageBox.question(
            self, "Eject all", "Déconnecter toutes les entrées de tous les bus ?\n"
            "(Les niveaux d'envoi sont conservés ; tu peux tout rebrancher dans la matrice.)")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for strip in self.strips:
            for route in range(len(strip.knobs)):
                self._vm.set_route(strip.index, route, False)

    # ---- magnéto (nécessite un enregistreur côté moteur)
    def _on_record(self, on: bool) -> None:
        if not self._engine_call("set_recording", on, feature="Enregistreur"):
            self.master.tape.reset()

    def _on_play(self) -> None:
        if not self._engine_call("play_recording", feature="Enregistreur"):
            self.master.tape.reset()

    def _on_stop_tape(self) -> None:
        self._engine_call("stop_recording", feature="Enregistreur")

    def _on_ab(self, on: bool) -> None:
        self._engine_call("set_ab_loop", on, feature="Boucle A-B")

    # ------------------------------------------------------- actions UI
    def _on_pad_mode(self, index: int, mode: str) -> None:
        """Mode d'Intellipan choisi pour une strip : retenu pour la prochaine fois."""
        modes = self._cfg.setdefault("ui", {}).setdefault("pad_modes", [])
        modes.extend([None] * (index + 1 - len(modes)))
        modes[index] = mode

    # ------------------------------------------------- volume par application
    def refresh_app_counts(self) -> None:
        if not self.isVisible() or self.isMinimized():
            return
        for strip in self.strips:
            if strip.apps is not None:
                index = self._vm.virtual_input(strip.index)
                strip.apps.set_count(len(self._apps.apps(index)) if index is not None else 0)

    def open_app_mixer(self, index: int) -> None:
        strip = self.strips[index]
        virtual = self._vm.virtual_input(index)
        if virtual is None or strip.apps is None:
            return
        if self._app_mixer is not None:
            self._app_mixer.close()
        self._app_mixer = AppMixerPopup(self._apps, virtual, strip.strip_name(), self)
        self._app_mixer.destroyed.connect(self._on_app_mixer_closed)
        self._app_mixer.show_under(strip.apps)

    def _on_app_mixer_closed(self) -> None:
        self._app_mixer = None
        QTimer.singleShot(0, self.refresh_app_counts)

    # ------------------------------------------------------- mises à jour
    def _on_update_available(self, update: UpdateInfo) -> None:
        skipped = int(self._cfg.get("update", {}).get("skipped_build", 0) or 0)
        if update.build == skipped and self._update is None:
            return
        first = self._update is None or self._update.build != update.build
        self._update = update
        self.master.set_update(update.version)
        if first:
            self.toast.show_message(f"VoiceConnector {update.version} est disponible : "
                                    "clique sur « Mise à jour » dans le panneau Master.")
            if self._tray is not None and not self.isVisible():
                self._tray.showMessage(config.APP_NAME, f"VoiceConnector {update.version} est disponible.",
                                       self.windowIcon(), 5000)

    def open_update(self) -> None:
        # Mise à jour signalée, ou trouvée par une recherche manuelle (même ignorée)
        update = self._update or (self._updater.latest if self._updater is not None else None)
        if update is None:
            return
        dialog = UpdateDialog(update, self._updater.build if self._updater else None, self)
        dialog.exec()
        skipped, installed = dialog.skipped, dialog.installed
        delete_now(dialog)        # tout de suite : pas de suppression différée en suspens
        if skipped:
            self._cfg.setdefault("update", {})["skipped_build"] = update.build
            self._save_config()
            self._update = None
            self.master.set_update(None)
        elif installed:
            self.restart_app()

    def rename_strip(self, index: int, name: str) -> None:
        """Renomme un canal (depuis les réglages) : strip, REAPER, Voicemeeter, config."""
        name = name.strip()[:30]
        if not name or not 0 <= index < len(self.strips) or self.strips[index].name.text() == name:
            return
        self.strips[index].name.setText(name)
        self._on_renamed(index, name)

    def _on_renamed(self, index: int, name: str) -> None:
        if self._standalone:           # le nom appartient au profil : il est retenu pour lui
            self._session.session.set_override(self._vm.profile.name, index, name=name)
        else:
            names = self._cfg.setdefault("ui", {}).setdefault("strip_names", [])
            while len(names) <= index:
                names.append(f"Strip {len(names) + 1}")
            names[index] = name
        self.matrix.set_strip_name(index, name)
        self._reaper.rename_track(index, name)
        self._vm.set_label(index, name)
        self._save_config()

    def add_effect(self, index: int) -> None:
        strip, host = self.strips[index], self._reaper
        if not host.manages_fx:
            host.add_fx(index, "")              # FL Studio : sa fenêtre s'ouvre
            return
        extra_dirs = self._cfg.get("vst", {}).get("extra_dirs", [])
        if host.kind == HOST_APO:
            if not host.strip_device(index) and not self.choose_apo_device(index):
                return
            from core.apo_ctrl import default_vst2_dirs, scan_vst2
            dialog = VstDialog(strip.strip_name(), None, None, extra_dirs, self,
                               plugins=scan_vst2(default_vst2_dirs(extra_dirs)),
                               browse_filter="Plugin VST2 64 bits (*.dll)",
                               note="Equalizer APO charge les plugins VST2 64 bits (.dll).")
        else:
            exe = self._processes.executable("reaper") if self._processes is not None else None
            dialog = VstDialog(strip.strip_name(), host, host.resource_path(exe), extra_dirs, self)
        selected = dialog.selected if dialog.exec() else None
        delete_now(dialog)        # sinon chaque ouverture garderait un dialogue (et sa liste) en mémoire
        if selected is not None:
            log.info("Ajout de %s sur %s", selected.add_name, strip.strip_name())
            host.add_fx(index, selected.add_name)

    def choose_apo_device(self, index: int) -> bool:
        """Périphérique Windows d'une strip pour Equalizer APO (micro…)."""
        from PySide6.QtWidgets import QInputDialog

        host = self._reaper
        names = [e.name for e in host.endpoints() if e.capture] + \
                [e.name for e in host.endpoints() if not e.capture]
        current = host.strip_device(index)
        if current and current not in names:
            names.insert(0, current)
        name, ok = QInputDialog.getItem(
            self, "Périphérique Windows",
            f"Périphérique Windows de « {self.strips[index].strip_name()} »\n"
            "(celui où Equalizer APO est installé, par exemple ton micro) :",
            names, names.index(current) if current in names else 0, True)
        if not ok or not name.strip():
            return False
        host.set_device(index, name.strip())
        return True

    def _open_effect(self, index: int, fx_index: int) -> None:
        self._reaper.toggle_fx_ui(index, fx_index)
        name = self.strips[index].fx_name(fx_index)
        if self._reaper.kind == HOST_REAPER and name and self._processes is not None:
            # L'interface du plugin s'ouvre dans REAPER (caché) : on la ramène devant
            QTimer.singleShot(FOCUS_PLUGIN_DELAY_MS, self,
                              lambda: self._processes.focus_window("reaper", name))

    def _remove_effect(self, index: int, fx_index: int) -> None:
        name = self.strips[index].fx_name(fx_index) or "cet effet"
        answer = QMessageBox.question(
            self, "Supprimer l'effet",
            f"Supprimer « {name} » de « {self.strips[index].strip_name()} » ?\n"
            "Ses réglages seront perdus.")
        if answer == QMessageBox.StandardButton.Yes:
            self._reaper.remove_fx(index, fx_index)

    def open_settings(self, tab: str | None = None) -> None:
        if self._detector is not None:
            self._detector.refresh()               # la liste des logiciels est à jour à l'ouverture
        dialog = SettingsDialog(self._cfg, self._processes, self._reaper, self._vm, self,
                                persist=self._persist, updater=self._updater,
                                session=self._session, detector=self._detector,
                                start_tab=tab if isinstance(tab, str) else None,
                                live=self._live_state)
        dialog.sessionChanged.connect(self._apply_change)
        dialog.routeRequested.connect(self._apply_voice_route)
        dialog.optionsChanged.connect(lambda: self._on_tracks(self._tracks))
        dialog.projectRequested.connect(self.choose_project)
        dialog.appearanceChanged.connect(self.apply_appearance)
        dialog.windowResetRequested.connect(self.reset_window_size)
        dialog.stripRenamed.connect(self.rename_strip)
        dialog.latencyChanged.connect(lambda: self._on_tracks(self._tracks))
        # Redémarrage après la fermeture des réglages (pas depuis leur boucle modale)
        restart = []
        dialog.restartRequested.connect(lambda: restart.append(True))
        dialog.restartRequested.connect(dialog.accept)
        # « Installer la mise à jour » (onglet À propos) : après la fermeture des réglages
        update = []
        dialog.updateRequested.connect(lambda: update.append(True))
        dialog.updateRequested.connect(dialog.accept)
        dialog.exec()
        delete_now(dialog)        # tout de suite : pas de suppression différée en suspens
        if restart:
            self.restart_app()
        elif update:
            self.open_update()

    # ------------------------------------------------------------ apparence
    def apply_appearance(self) -> None:
        """Thème de couleur, arrière-plan et transparence des panneaux, d'après la config."""
        ui_cfg = self._cfg.get("ui", {})
        theme = ui_cfg.get("theme", styles.DEFAULT_THEME)
        theme_changed = theme != styles.current_theme()
        if theme_changed:
            styles.set_theme(theme)
        if styles.configure(ui_cfg):          # police, style des faders / VU
            theme_changed = True
        self.background.set_image(background_image_path(self._cfg),
                                  float(ui_cfg.get("background_darkness", DEFAULT_DARKNESS)))
        app = QApplication.instance()
        if app is not None:
            styles.refresh(app, float(ui_cfg.get("panel_opacity", DEFAULT_PANEL_OPACITY)))
        if theme_changed:
            # Widgets dessinés à la main : ils relisent la palette en se redessinant
            self.background.invalidate()
            self.toast.refresh_style()
            for widget in self.findChildren(QWidget):
                widget.update()
            self.update()

    # -------------------------------------------------------------- fenêtre
    def compact_size(self) -> tuple[int, int]:
        """Taille « comme Voicemeeter » : juste les strips et le master."""
        width = self.background.layout().sizeHint().width()
        return width, COMPACT_HEIGHT

    def _restore_geometry(self) -> None:
        width, _ = self.compact_size()
        # Hauteur minimale : celle prévue, sauf sur un petit écran (1280×720 logiques
        # à 150 %) où elle doit tenir au-dessus de la barre des tâches
        layout_min = self.background.layout().minimumSize().height()
        area = self._screen_area()
        fit = area.height() - TITLE_BAR_ALLOWANCE if area is not None else MIN_HEIGHT
        self.setMinimumSize(width, max(layout_min, min(MIN_HEIGHT, fit)))
        ui_cfg = self._cfg.setdefault("ui", {})
        saved = ui_cfg.get("geometry")
        if ui_cfg.get("layout_version") != LAYOUT_VERSION:
            saved = None          # taille prévue pour l'ancienne disposition des strips
        if saved and self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
            return
        self.reset_window_size()

    def _screen_area(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def reset_window_size(self) -> None:
        """Fenêtre compacte, centrée dans l'espace libre de l'écran (barre des
        tâches exclue), barre de titre comprise."""
        if self.isVisible():
            self.showNormal()
        width, height = self.compact_size()
        area = self._screen_area()
        if area is None:
            self.resize(width, height)
            return
        height = max(self.minimumHeight(), min(height, area.height() - TITLE_BAR_ALLOWANCE))
        self.resize(width, height)
        left = area.x() + max(0, (area.width() - self.frameGeometry().width()) // 2)
        top = area.y() + max(0, (area.height() - height - TITLE_BAR_ALLOWANCE) // 2)
        self.move(left, top)

    def minimize(self) -> None:
        """Réduit VoiceConnector : dans la zone de notification si l'option est active
        (le son continue de tourner), sinon dans la barre des tâches."""
        if self._to_tray():
            # Fenêtre cachée : fermer une popup ne doit pas quitter VoiceConnector
            QApplication.setQuitOnLastWindowClosed(False)
            self.hide()           # l'état « agrandie » est conservé par Qt
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray.showMessage(config.APP_NAME, "VoiceConnector continue de tourner ici. "
                                       "Clique sur l'icône pour la rouvrir.",
                                       self.windowIcon(), 3000)
        else:
            self.showMinimized()

    def show_from_tray(self) -> None:
        """Ré-affiche la fenêtre (zone de notification, barre des tâches, second
        lancement) dans son état d'avant : agrandie si elle l'était."""
        if not self.isVisible() or self.isMinimized():
            # Qt garde WindowMaximized pendant la réduction : on n'enlève que « réduite »
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
        self.raise_()
        self.activateWindow()
        QApplication.setQuitOnLastWindowClosed(True)

    def quit_app(self) -> None:
        """« Quitter VoiceConnector » du menu de la zone de notification : la fenêtre peut
        être cachée, on quitte donc explicitement après l'avoir fermée."""
        if self.close():
            QApplication.quit()

    def _to_tray(self) -> bool:
        return (self._tray is not None
                and bool(self._cfg.get("ui", {}).get("minimize_to_tray", False)))

    def _make_tray(self) -> QSystemTrayIcon | None:
        """Icône dans la zone de notification (à côté de l'horloge)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        app = QApplication.instance()
        tray = QSystemTrayIcon(app.windowIcon() if app else self.windowIcon(), self)
        tray.setToolTip(config.APP_NAME)
        menu = QMenu(self)
        show_action = QAction("Afficher VoiceConnector", menu)
        show_action.triggered.connect(self.show_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        quit_action = QAction("Quitter VoiceConnector", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason) -> None:
        # Sous Windows, un double-clic envoie Trigger puis DoubleClick : le
        # double-clic ne fait donc qu'afficher (sinon il annulerait le clic).
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.minimize()
            else:
                self.show_from_tray()

    def changeEvent(self, event) -> None:  # noqa: N802
        # Réduction par la barre de titre : direction la zone de notification si demandé
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and self._to_tray()):
            QTimer.singleShot(0, self.minimize)
        super().changeEvent(event)

    def restart_app(self) -> None:
        """Relance VoiceConnector (changement d'édition de Voicemeeter) : la nouvelle
        instance attend que celle-ci ait arrêté proprement les moteurs."""
        if not self._persist:
            self.toast.show_message("Mode démo : pas de redémarrage.")
            return
        self._save_config()
        if not launch_restart():
            QMessageBox.warning(self, config.APP_NAME,
                                "Impossible de relancer VoiceConnector automatiquement : relance-la à la main.")
            return
        self.close()

    def choose_project(self) -> None:
        """Choix du projet REAPER ; il est ouvert tout de suite, sans redémarrer."""
        session_cfg = running_config(self._cfg, self._vm)
        dialog = ProjectDialog(session_cfg, current=self._reaper.project_path, parent=self)
        accepted, selected = bool(dialog.exec()), dialog.selected
        delete_now(dialog)
        if not accepted:
            return
        self._save_config()
        path = reaper_project(session_cfg, selected)
        if path is None:
            self.toast.show_message("Projet introuvable.")
            return
        current = self._reaper.project_path
        if current and Path(current).resolve() == path.resolve():
            return
        self._reaper.open_project(path)
        self.toast.show_message(f"Ouverture de « {path.stem} » dans REAPER…")

    # ------------------------------------------------------------ fermeture
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._detector is not None:
            self._detector.stop()
        self._stop_fl_sync()
        if self._params is not None:
            state = self._params.to_state()
            for index, (comp, gate) in self._stash.saved.items():   # mode musical : on garde les vrais réglages
                if index < len(state["strips"]) and "dynamics" in state["strips"][index]:
                    state["strips"][index]["dynamics"] = [comp, gate]
            if not getattr(self._vm, "supports_sends", True):
                # Banana n'a pas de niveaux d'envoi : on garde ceux réglés sur Potato
                previous = (self._cfg.get("state") or {}).get("strips", [])
                for strip, old in zip(state["strips"], previous):
                    if isinstance(old, dict) and "sends" in old:
                        strip["sends"] = old["sends"]
            self._cfg["state"] = state
        ui_cfg = self._cfg.setdefault("ui", {})
        ui_cfg["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        ui_cfg["layout_version"] = LAYOUT_VERSION    # la taille enregistrée va avec cette disposition
        for legacy in ("width", "height"):   # remplacés par « geometry »
            ui_cfg.pop(legacy, None)
        self._save_config()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    def _save_config(self) -> None:
        if not self._persist:
            return
        try:
            config.save_config(self._cfg)
        except OSError as exc:
            log.error("Config non sauvegardée : %s", exc)

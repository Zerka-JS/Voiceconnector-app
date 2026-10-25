"""
DetectService : la détection des logiciels audio en tâche de fond, dans son enveloppe Qt.

Lister les processus de Windows prend quelques dizaines de millisecondes : on ne le fait jamais
dans le thread de l'interface. Un thread léger relance la détection à intervalle régulier et
émet `scanned(list[DetectedApp])` ; Qt livre le signal dans le thread de l'interface (comme pour
les contrôleurs Voicemeeter). Toute la logique (catalogue, correspondance des processus) est dans
app_detector.py, testable sans Qt.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 1.0
STOP_JOIN_S = 1.0


class DetectService(QObject):
    scanned = Signal(object)                    # list[DetectedApp]

    def __init__(self, detector, interval_s: float = 3.0, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._detector = detector
        self._interval = max(MIN_INTERVAL_S, float(interval_s))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    # ------------------------------------------------------------- réglages
    @property
    def interval_s(self) -> float:
        return self._interval

    def set_interval(self, seconds: float) -> None:
        self._interval = max(MIN_INTERVAL_S, float(seconds))
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ----------------------------------------------------------- cycle de vie
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="detect", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(STOP_JOIN_S)

    def refresh(self) -> None:
        """Relance une détection tout de suite (ouverture des réglages, clic sur « Actualiser »)."""
        self._wake.set()

    def scan_now(self) -> list:
        """Détection immédiate dans le thread appelant (démarrage de la fenêtre, tests)."""
        try:
            return list(self._detector.scan())
        except Exception as exc:                # jamais fatale : la console marche sans détection
            log.warning("Détection des logiciels interrompue : %s", exc)
            return []

    # ---------------------------------------------------------------- thread
    def _run(self) -> None:
        while not self._stop.is_set():
            detected = self.scan_now()
            if self._stop.is_set():
                break
            self.scanned.emit(detected)
            self._wake.wait(self._interval)
            self._wake.clear()

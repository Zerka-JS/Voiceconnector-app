"""
FlSyncService : la synchronisation avec les pistes du mixer de FL Studio, côté Voicemeeter
(mode A), dans son enveloppe Qt. Toute la logique est dans fl_driver.py (testable sans Qt) ;
ici, la minuterie (un tour toutes les 20 ms : lecture MIDI, ordres vers la console,
écritures en attente) et les signaux.

Le mode autonome (B) n'en a pas besoin : les pistes y sont les canaux du moteur.
"""

from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import QObject, QTimer, Signal

from core.fl_driver import FlSyncDriver, StripTarget
from core.fl_sync import FlStripSync
from core.mixer_model import MixerParams

TICK_MS = 20


class FlSyncService(QObject):
    connection_changed = Signal(bool)
    solo_changed = Signal(int, bool)          # solo d'une tranche liée, décidé dans FL Studio

    def __init__(self, core, links: Mapping[int, int], target: StripTarget,
                 parent: QObject | None = None, interval_ms: int = TICK_MS) -> None:
        super().__init__(parent)
        self.core = core
        self._driver = FlSyncDriver(FlStripSync(core, links), target,
                                    on_solo=self.solo_changed.emit,
                                    on_connection=self.connection_changed.emit)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._driver.tick)

    @property
    def connected(self) -> bool:
        return self._driver.connected

    @property
    def linked_strips(self) -> frozenset[int]:
        return self._driver.linked_strips

    def start(self) -> None:
        self._driver.start()
        self._timer.start()

    def stop(self) -> None:
        """Arrête le dialogue (FL Studio est prévenu) ; les ports MIDI sont fermés."""
        self._timer.stop()
        self._driver.stop()
        self.core.close()

    def on_params(self, params: MixerParams) -> None:
        """À brancher sur `params_changed` de la console."""
        self._driver.on_params(params)

    def push_solo(self, strip: int, solo: bool) -> bool:
        """Vrai si le solo de cette tranche est celui de FL Studio (tranche liée)."""
        return self._driver.push_solo(strip, solo)

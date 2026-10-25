"""
StandaloneMixer : la console du mode autonome (B) dans son enveloppe Qt.

Même interface que VoicemeeterController et DemoVoicemeeter (signaux `params_changed`,
`levels_changed`, `connection_changed` ; méthodes `set_gain`, `set_mute`, `set_pad`,
`login`, `start_polling`, `release`, `close`…) : la fenêtre principale ne fait pas la
différence. Toute la logique est dans standalone_engine.py (testable sans Qt) ; ici,
seulement la minuterie et les signaux, tous dans le thread de l'interface (c'est là que
COM est initialisé pour les sessions audio de Windows).

Signaux en plus, propres à ce mode :
    presence_changed  tuple[bool, ...]   la source de chaque canal est là (ou non)
    status_changed    str                ce qui marche, ce qui manque (barre d'état)
    solo_changed      dict[int, bool]    solo des pistes de FL Studio
    structure_changed                    le profil a changé : la console refait ses tranches
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from core.effects import NoEffects
from core.fl_sync import connect_fl
from core.layout_manager import EngineCaps, StripInfo
from core.profile_import import Profile
from core.standalone_engine import StandaloneEngine
from core.system_audio import SystemAudio, create_system_audio

log = logging.getLogger(__name__)

POLL_MS = 50                      # 20 Hz : assez fluide pour les vumètres, léger pour COM


class StandaloneMixer(QObject):
    params_changed = Signal(object)        # MixerParams
    levels_changed = Signal(object)        # MixerLevels
    connection_changed = Signal(bool)
    presence_changed = Signal(object)
    status_changed = Signal(str)
    solo_changed = Signal(object)
    structure_changed = Signal()

    def __init__(self, engine: StandaloneEngine, parent: QObject | None = None,
                 interval_ms: int = POLL_MS) -> None:
        super().__init__(parent)
        self.engine = engine
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._last_params = None
        self._last_presence: tuple[bool, ...] | None = None
        self._last_status = ""
        self._last_solos: dict[int, bool] | None = None
        self._connected: bool | None = None

    # ----------------------------------------------------- cycle de vie
    def login(self) -> None:
        pass                              # rien à joindre : Windows est toujours là

    def start_polling(self) -> None:
        self._tick()                      # l'état initial arrive tout de suite
        self._timer.start()

    def release(self) -> None:
        self._timer.stop()

    def close(self, shutdown_app: bool = False) -> None:
        self._timer.stop()
        self.engine.shutdown()

    def _tick(self) -> None:
        try:
            params, levels = self.engine.poll()
        except Exception as exc:          # une session audio qui disparaît ne doit jamais tuer l'interface
            log.warning("Lecture de l'audio Windows interrompue : %s", exc)
            return
        if params != self._last_params:
            self._last_params = params
            self.params_changed.emit(params)
        self.levels_changed.emit(levels)
        presence = self.engine.presence()
        if presence != self._last_presence:
            self._last_presence = presence
            self.presence_changed.emit(presence)
        status = self.engine.status()
        if status != self._last_status:
            self._last_status = status
            self.status_changed.emit(status)
        solos = self.engine.solo_states()
        if solos != self._last_solos:
            self._last_solos = solos
            self.solo_changed.emit(solos)
        available = bool(self.engine.audio.available)
        if available != self._connected:
            self._connected = available
            self.connection_changed.emit(available)

    # ------------------------------------------------------- profil
    @property
    def profile(self) -> Profile:
        return self.engine.profile

    def set_profile(self, profile: Profile) -> None:
        """Autre liste de canaux : la console refait ses tranches puis reprend la lecture."""
        self.engine.set_profile(profile)
        self._last_params = self._last_presence = self._last_solos = None
        self.structure_changed.emit()
        if self._timer.isActive():
            self._tick()

    def assign_app(self, strip: int, key: str) -> None:
        self.engine.assign_app(strip, key)
        self._last_presence = None

    # ----------------------------------------- l'interface de la console
    @property
    def strip_count(self) -> int:
        return self.engine.strip_count

    @property
    def edition(self):
        return self.engine.edition

    @property
    def supports_sends(self) -> bool:
        return self.engine.supports_sends

    @property
    def persistent_state(self) -> bool:
        return self.engine.persistent_state

    @property
    def caps(self) -> EngineCaps:
        return self.engine.caps

    def strip_infos(self) -> list[StripInfo]:
        return self.engine.strip_infos()

    def strip_caps(self, strip: int) -> EngineCaps:
        return self.engine.strip_caps(strip)

    def source_label(self, strip: int) -> str:
        return self.engine.source_label(strip)

    def max_gain_db(self, strip: int) -> float:
        return self.engine.max_gain_db(strip)

    @property
    def fl_connected(self) -> bool:
        return self.engine.fl_connected

    @property
    def uses_fl(self) -> bool:
        return self.engine.uses_fl

    def ensure_fl(self, cfg) -> str:
        """Ouvre la liaison avec FL Studio si le profil a des pistes de FL Studio (et la ferme sinon).
        Renvoie la raison de l'échec, ou « » (ne lève jamais : sans liaison, ces canaux sont absents)."""
        if not self.engine.uses_fl:
            if self.engine.fl is not None:
                self.engine.attach_fl(None)
            return ""
        if self.engine.fl is not None:
            return ""
        core, reason = connect_fl(cfg)
        self.engine.attach_fl(core, reason)
        self._last_status = ""                  # le texte de la barre d'état doit être relu
        return reason

    def is_physical(self, strip: int) -> bool:
        return self.engine.is_physical(strip)

    def virtual_input(self, strip: int) -> int | None:
        return self.engine.virtual_input(strip)

    def pad_modes(self, strip: int) -> tuple[str, ...]:
        return self.engine.pad_modes(strip)

    def set_gain(self, strip: int, db: float) -> None:
        self.engine.set_gain(strip, db)

    def set_mute(self, strip: int, muted: bool) -> None:
        self.engine.set_mute(strip, muted)

    def set_solo(self, strip: int, solo: bool) -> None:
        self.engine.set_solo(strip, solo)

    def native_solo(self, strip: int) -> bool:
        return self.engine.native_solo(strip)

    def set_pad(self, strip: int, mode: str, x: float, y: float) -> None:
        self.engine.set_pad(strip, mode, x, y)

    def set_headphone_gain(self, db: float) -> None:
        self.engine.set_headphone_gain(db)

    def set_label(self, strip: int, text: str) -> None:
        self.engine.set_label(strip, text)

    def set_route(self, strip: int, route: int, enabled: bool) -> None:
        pass

    def set_send(self, strip: int, route: int, db: float) -> None:
        pass

    def set_buffering(self, samples: int) -> None:
        pass

    def set_wdm_exclusive(self, exclusive: bool) -> None:
        pass

    def set_inserts(self, strips) -> None:
        pass

    def set_optional(self, params) -> None:
        pass

    def set_comp(self, strip: int, value: float) -> None:
        pass

    def set_gate(self, strip: int, value: float) -> None:
        pass

    def set_strip_eq(self, strip: int, band: int, db: float) -> None:
        pass

    def apply_state(self, state: dict) -> None:
        pass


class StandaloneHost(NoEffects):
    """« Moteur d'effets » du mode autonome : il n'y en a pas (ni Voicemeeter, ni REAPER)."""

    empty_hint = ""

    def status(self, connected: bool) -> tuple[bool, str, str]:
        return (True, "Mode autonome",
                "Sans Voicemeeter : les volumes sont ceux de Windows et des pistes de FL Studio.")


def create_standalone(cfg: dict, profile: Profile, audio: SystemAudio | None = None
                      ) -> tuple[StandaloneMixer, StandaloneHost]:
    """La console du mode autonome et son « moteur d'effets » (aucun), prêts pour la fenêtre principale.

    `audio` : l'audio système (par défaut celui de Windows ; une maquette en démo). Si le profil a des
    pistes de FL Studio, la liaison MIDI est ouverte ici ; sans elle (mido ou loopMIDI absents), ces
    canaux sont simplement signalés absents."""
    mixer = StandaloneMixer(StandaloneEngine(profile, audio if audio is not None else create_system_audio(cfg)))
    mixer.ensure_fl(cfg)
    return mixer, StandaloneHost(cfg)

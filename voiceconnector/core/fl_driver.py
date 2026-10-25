"""
Liaison de la console Voicemeeter (mode A) avec les pistes du mixer de FL Studio
(logique pure, sans Qt) : faders, muet, solo et panoramique vont dans les deux sens.

`FlStripSync` (fl_sync.py) décide quoi écrire en comparant une tranche à l'état de sa
piste. Il lui manque une chose que seul le pilote voit : le délai de la console. Quand
FL Studio bouge un fader, l'ordre part vers Voicemeeter, qui met un instant à
l'appliquer ; si la console renvoie son ancienne valeur dans l'intervalle, la liaison
la prendrait pour une demande de l'utilisateur et la recopierait dans FL Studio. Le
pilote retient donc chaque ordre donné à la console et, jusqu'à ce qu'elle l'ait
appliqué (ou que `HOLD_S` soit écoulé), fait « comme si » elle l'avait déjà fait.

Le mode autonome (B) n'a pas besoin de ce pilote : les pistes y sont les canaux du moteur.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Callable, Protocol

from core.fl_sync import (KIND_GAIN, KIND_MUTE, KIND_PAN, KIND_SOLO, FlStripSync, StripCommand)
from core.mixer_model import PAD_X_RANGE, MixerParams, StripParams

log = logging.getLogger(__name__)

HOLD_S = 0.8                     # temps laissé à la console pour appliquer un ordre
GAIN_MATCH_DB = 0.15
PAN_MATCH = 0.01


class StripTarget(Protocol):
    """Ce que le pilote commande (VoicemeeterController, DemoVoicemeeter)."""

    def set_gain(self, strip: int, db: float) -> None: ...

    def set_mute(self, strip: int, muted: bool) -> None: ...

    def set_pad(self, strip: int, mode: str, x: float, y: float) -> None: ...


def _pan_of(params: StripParams) -> tuple[float, float] | None:
    return params.pad("pan")


def _matches(params: StripParams, command: StripCommand) -> bool:
    """La tranche a-t-elle déjà la valeur de l'ordre ?"""
    if command.kind == KIND_GAIN:
        return abs(params.gain - float(command.value)) <= GAIN_MATCH_DB
    if command.kind == KIND_MUTE:
        return params.mute == bool(command.value)
    if command.kind == KIND_PAN:
        pad = _pan_of(params)
        return pad is None or abs(pad[0] - float(command.value)) <= PAN_MATCH
    return True


def _with(params: StripParams, command: StripCommand) -> StripParams:
    """La tranche, telle qu'elle sera quand la console aura appliqué l'ordre."""
    if command.kind == KIND_GAIN:
        return replace(params, gain=float(command.value))
    if command.kind == KIND_MUTE:
        return replace(params, mute=bool(command.value))
    if command.kind == KIND_PAN:
        pads = tuple((mode, float(command.value), y) if mode == "pan" else (mode, x, y)
                     for mode, x, y in params.pads)
        return replace(params, pads=pads)
    return params


class FlSyncDriver:
    """Un tour de `tick()` toutes les ~20 ms ; `on_params()` à chaque changement de la console."""

    def __init__(self, sync: FlStripSync, target: StripTarget, *,
                 on_solo: Callable[[int, bool], None] | None = None,
                 on_connection: Callable[[bool], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, hold_s: float = HOLD_S) -> None:
        self.sync = sync
        self.core = sync.core
        self._target = target
        self._on_solo = on_solo
        self._on_connection = on_connection
        self._clock = clock
        self._hold_s = hold_s
        self._params: MixerParams | None = None
        self._held: dict[tuple[int, str], tuple[StripCommand, float]] = {}
        self._was_connected = False

    # ------------------------------------------------------------ cycle de vie
    @property
    def connected(self) -> bool:
        return bool(self.core.connected)

    @property
    def linked_strips(self) -> frozenset[int]:
        """Tranches de la console liées à une piste de FL Studio."""
        return frozenset(self.sync.links)

    def start(self) -> None:
        self.core.start()

    def stop(self) -> None:
        self.core.stop()
        self._held.clear()
        self._set_connected(False)

    # ------------------------------------------------------ console → FL Studio
    def on_params(self, params: MixerParams) -> int:
        """La console a changé : ce qui diffère de FL Studio y est écrit. Renvoie le nombre d'écritures."""
        self._params = params
        return self.sync.push(self._as_if_applied(params.strips))

    def push_solo(self, strip: int, solo: bool) -> bool:
        """Solo demandé sur une tranche : vrai si c'est celui de FL Studio (la tranche est liée)."""
        if strip not in self.sync.links:
            return False
        self.sync.push_solo(strip, solo)
        return True

    def _as_if_applied(self, strips):
        now = self._clock()
        shown = list(strips)
        for key, (command, deadline) in list(self._held.items()):
            strip = command.strip
            if strip >= len(shown) or now > deadline or _matches(shown[strip], command):
                del self._held[key]
                continue
            shown[strip] = _with(shown[strip], command)
        return shown

    # ------------------------------------------------------ FL Studio → console
    def tick(self) -> list[StripCommand]:
        """Lit FL Studio, applique à la console ce qu'il a changé, envoie les écritures en attente."""
        result = self.core.poll()
        commands = self.sync.pull(result.changed)
        applied = [command for command in commands if self._apply(command)]
        self.core.flush()
        self._set_connected(self.connected)
        return applied

    def _apply(self, command: StripCommand) -> bool:
        strip = command.strip
        if command.kind == KIND_SOLO:
            if self._on_solo is not None:
                self._on_solo(strip, bool(command.value))
            return True
        if self._params is not None and strip >= len(self._params.strips):
            return False
        deadline = self._clock() + self._hold_s
        self._held[(strip, command.kind)] = (command, deadline)
        if command.kind == KIND_GAIN:
            self._target.set_gain(strip, float(command.value))
        elif command.kind == KIND_MUTE:
            self._target.set_mute(strip, bool(command.value))
        elif command.kind == KIND_PAN:
            pad = _pan_of(self._params.strips[strip]) if self._params is not None else None
            x = max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], float(command.value)))
            self._target.set_pad(strip, "pan", x, pad[1] if pad else 0.0)
        else:
            del self._held[(strip, command.kind)]
            return False
        return True

    def _set_connected(self, connected: bool) -> None:
        if connected != self._was_connected:
            self._was_connected = connected
            log.info("Synchronisation FL Studio %s.", "active" if connected else "interrompue")
            if self._on_connection is not None:
                self._on_connection(connected)

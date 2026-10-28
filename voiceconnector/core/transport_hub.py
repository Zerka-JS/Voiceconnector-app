"""
Un même port MIDI pour plusieurs liaisons (sans Qt).

FL Studio n'a qu'une paire de ports virtuels pour VoiceConnector (« VoiceConnector FL » et
« VoiceConnector VC »), et Windows ne laisse un port MIDI d'entrée s'ouvrir qu'une fois. Or deux
liaisons s'en servent : la synchronisation du mixer (core/fl_sync.py) et le Studio
(core/fl_studio_link.py). Le hub ouvre le transport une seule fois, donne à chaque liaison sa
« vue » (même interface qu'un transport) et distribue à toutes les vues chaque message reçu : chaque
liaison ne garde que les commandes qu'elle connaît.

Le transport réel est refermé quand la dernière vue l'est.
"""

from __future__ import annotations

import logging
from typing import Callable

from core.fl_sync import Transport

log = logging.getLogger(__name__)

MAX_PENDING = 4000                   # messages gardés par vue si personne ne les lit


class HubView:
    """Une liaison vue du transport partagé : même interface que `Transport`."""

    def __init__(self, hub: "TransportHub") -> None:
        self._hub = hub
        self._inbox: list[bytes] = []
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open and self._hub.transport_open

    def send(self, data: bytes) -> None:
        if self._open:
            self._hub.send(data)

    def receive(self) -> list[bytes]:
        """Messages reçus pour cette liaison depuis le dernier appel (sans attendre)."""
        if not self._open:
            return []
        self._hub.pump()
        messages, self._inbox = self._inbox, []
        return messages

    def close(self) -> None:
        if self._open:
            self._open = False
            self._hub.release(self)

    def _deliver(self, raw: bytes) -> None:
        self._inbox.append(raw)
        if len(self._inbox) > MAX_PENDING:
            del self._inbox[:len(self._inbox) - MAX_PENDING]


class TransportHub:
    """Ouvre un transport au premier besoin, le partage, le referme au dernier départ."""

    def __init__(self, opener: Callable[[], Transport]) -> None:
        self._opener = opener
        self._transport: Transport | None = None
        self._views: list[HubView] = []

    @property
    def transport_open(self) -> bool:
        return self._transport is not None and bool(self._transport.is_open)

    @property
    def views(self) -> int:
        return len(self._views)

    def open(self) -> HubView:
        """Une vue du transport ; ouvre les ports MIDI s'ils ne le sont pas déjà.

        Lève ce que l'ouverture lève (TransportError : mido ou port virtuel absent…)."""
        if not self.transport_open:
            if self._transport is not None:                      # port fermé de force : on repart de zéro
                self._drop_transport()
            self._transport = self._opener()
        view = HubView(self)
        self._views.append(view)
        return view

    # ------------------------------------------------------- appelé par les vues
    def send(self, data: bytes) -> None:
        if self._transport is not None:
            self._transport.send(data)

    def pump(self) -> None:
        """Lit le transport une fois et distribue les messages à toutes les vues."""
        if self._transport is None:
            return
        try:
            messages = self._transport.receive()
        except Exception as exc:
            log.debug("Lecture MIDI impossible : %s", exc)
            return
        for raw in messages:
            for view in self._views:
                view._deliver(raw)

    def release(self, view: HubView) -> None:
        if view in self._views:
            self._views.remove(view)
        if not self._views:
            self._drop_transport()

    def _drop_transport(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                log.debug("Fermeture du transport MIDI : %s", exc)

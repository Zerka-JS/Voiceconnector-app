"""
Ports MIDI « bruts » pour les logiciels qui parlent Mackie Control (Cubase, Studio One, Ableton…),
et diagnostic de leur installation.

Contrairement à `fl_sync.MidoTransport` (qui n'échange que du SysEx avec le script de FL Studio),
ce transport laisse passer tous les messages MIDI : notes (boutons, voyants), pitch-bend (faders),
contrôleurs (potards) et SysEx (écrans).

Deux ports virtuels (loopMIDI sous Windows), à créer une fois :
    « VoiceConnector DAW »    : VoiceConnector écrit, le logiciel lit  → l'ENTRÉE MIDI de la surface ;
    « VoiceConnector Retour » : le logiciel écrit, VoiceConnector lit  → la SORTIE MIDI de la surface.
"""

from __future__ import annotations

import importlib
import logging
import queue
from typing import Any, Mapping

from core.fl_sync import SetupCheck, TransportError, find_port, mido_available

log = logging.getLogger(__name__)

DEFAULT_OUT_PORT = "VoiceConnector DAW"        # VoiceConnector écrit ici, le logiciel lit
DEFAULT_IN_PORT = "VoiceConnector Retour"      # le logiciel écrit ici, VoiceConnector lit


class RawMidiTransport:
    """Transport réel : mido + python-rtmidi (dépendances facultatives), tous les messages MIDI."""

    def __init__(self, out_port: str = DEFAULT_OUT_PORT, in_port: str = DEFAULT_IN_PORT) -> None:
        try:
            import mido
        except Exception as exc:
            raise TransportError("Le module « mido » n'est pas installé (pip install mido "
                                 f"python-rtmidi) : {exc}") from exc
        self._mido = mido
        self.is_open = False
        self._queue: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        try:
            out_name = find_port(mido.get_output_names(), out_port)
            in_name = find_port(mido.get_input_names(), in_port)
        except Exception as exc:
            raise TransportError(f"Ports MIDI illisibles : {exc}") from exc
        if out_name is None or in_name is None:
            missing = " et ".join(n for n, found in ((f"« {out_port} »", out_name),
                                                     (f"« {in_port} »", in_name)) if found is None)
            raise TransportError(f"Port MIDI virtuel introuvable : {missing}. Crée-le avec loopMIDI.")
        try:
            self._out = mido.open_output(out_name)
            self._in = mido.open_input(in_name, callback=self._on_message)
        except Exception as exc:
            raise TransportError(f"Ouverture des ports MIDI impossible : {exc}") from exc
        self.is_open = True
        self.out_name, self.in_name = out_name, in_name
        log.info("Studio (Mackie Control) : écriture sur « %s », lecture sur « %s ».", out_name, in_name)

    def _on_message(self, message) -> None:          # thread de python-rtmidi
        try:
            self._queue.put(bytes(message.bytes()))
        except Exception as exc:                     # message illisible : ignoré
            log.debug("Message MIDI ignoré : %s", exc)

    def send(self, data: bytes) -> None:
        if not self.is_open:
            return
        self._out.send(self._mido.Message.from_bytes(list(data)))

    def receive(self) -> list[bytes]:
        messages = []
        while True:
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                return messages

    def close(self) -> None:
        self.is_open = False
        for port in (getattr(self, "_in", None), getattr(self, "_out", None)):
            try:
                if port is not None:
                    port.close()
            except Exception as exc:
                log.debug("Fermeture d'un port MIDI : %s", exc)


def mcu_ports(cfg: Mapping | None) -> tuple[str, str]:
    """(port d'écriture, port de lecture) d'après la config (studio.mcu_out_port / studio.mcu_in_port)."""
    section = (cfg or {}).get("studio", {})
    section = section if isinstance(section, Mapping) else {}
    return (str(section.get("mcu_out_port") or DEFAULT_OUT_PORT),
            str(section.get("mcu_in_port") or DEFAULT_IN_PORT))


def open_mcu_transport(cfg: Mapping | None) -> RawMidiTransport:
    out_port, in_port = mcu_ports(cfg)
    return RawMidiTransport(out_port, in_port)


def mcu_setup_checks(cfg: Mapping | None = None, *, mido_module: Any = None) -> list[SetupCheck]:
    """Ce qui est prêt, et ce qui manque, pour piloter un logiciel par Mackie Control.

    Ne modifie rien et n'ouvre aucun port. `mido_module` : un module de remplacement (tests)."""
    out_wanted, in_wanted = mcu_ports(cfg)
    checks: list[SetupCheck] = []
    library = mido_module
    if library is None and mido_available():
        try:
            library = importlib.import_module("mido")
        except Exception as exc:                                 # module cassé : même diagnostic que l'absence
            log.debug("mido inutilisable : %s", exc)
    if library is None:
        checks.append(SetupCheck(False, "Bibliothèque MIDI absente (mido et python-rtmidi) : « pip install mido "
                                        "python-rtmidi » ; elle est incluse dans VoiceConnector.exe."))
        return checks
    checks.append(SetupCheck(True, "Bibliothèque MIDI (mido, python-rtmidi) : installée."))
    try:
        outputs, inputs = list(library.get_output_names()), list(library.get_input_names())
    except Exception as exc:
        checks.append(SetupCheck(False, f"Ports MIDI illisibles : {exc}"))
        return checks
    for wanted, names, role in ((out_wanted, outputs, "écriture : entrée MIDI du logiciel"),
                                (in_wanted, inputs, "lecture : sortie MIDI du logiciel")):
        found = find_port(names, wanted)
        checks.append(SetupCheck(found is not None, f"Port MIDI virtuel « {wanted} » ({role}) : "
                                 + (f"trouvé ({found})." if found else "introuvable, crée-le avec loopMIDI.")))
    return checks

"""
Paramètres de Voicemeeter qui n'existent pas partout : compresseur, gate, égaliseur rapide
des entrées matérielles, flux VBAN (logique pure, sans Qt).

Le contrôleur traite toute exception de l'API Remote comme une perte de connexion : il
repasse en « déconnecté » et rejoue les écritures. Pour un paramètre qu'une édition ou une
version de Voicemeeter ne connaît pas, ce serait faux : la console se croirait coupée alors
que Voicemeeter répond très bien. Les paramètres ci-dessous passent donc par ce chemin
à part :

- une écriture facultative qui échoue n'est jamais fatale : on l'oublie, sans la rejouer ;
- une lecture facultative qui échoue rend None (la commande reste masquée dans la console) ;
- un paramètre refusé (code -3 « paramètre inconnu », ou trois échecs d'affilée) n'est
  plus demandé du tout pendant cette session.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Callable, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

UNKNOWN_PARAMETER = -3          # code de l'API Remote : paramètre inconnu
MAX_FAILURES = 3


class OptionalParams:
    """File d'écritures facultatives et mémoire des paramètres que Voicemeeter refuse."""

    def __init__(self, max_failures: int = MAX_FAILURES) -> None:
        self._max_failures = max_failures
        self._lock = threading.Lock()
        self._writes: dict[str, float | str] = {}
        self._unsupported: set[str] = set()
        self._failures: Counter[str] = Counter()

    # ------------------------------------------------------------- état
    def supported(self, param: str) -> bool:
        return param not in self._unsupported

    @property
    def unsupported(self) -> frozenset[str]:
        return frozenset(self._unsupported)

    def forget(self) -> None:
        """Nouvelle session Voicemeeter (relancé, autre version) : tout est de nouveau permis."""
        with self._lock:
            self._unsupported.clear()
            self._failures.clear()

    def failed(self, param: str, exc: BaseException) -> None:
        """Note l'échec d'un paramètre ; le refuse définitivement s'il est inconnu ou répété."""
        code = getattr(exc, "code", None)
        with self._lock:
            self._failures[param] += 1
            refuse = code == UNKNOWN_PARAMETER or self._failures[param] >= self._max_failures
            if refuse and param not in self._unsupported:
                self._unsupported.add(param)
                log.info("Voicemeeter ne gère pas « %s » (%s) : commande masquée.", param, exc)
                return
        log.debug("Paramètre facultatif « %s » en échec : %s", param, exc)

    # ---------------------------------------------------------- écritures
    def queue(self, params: Iterable[tuple[str, float | str]]) -> int:
        """Met en file des écritures ; renvoie combien ont été retenues (les refusés sont ignorés)."""
        kept = 0
        with self._lock:
            for param, value in params:
                if param not in self._unsupported:
                    self._writes[param] = value
                    kept += 1
        return kept

    def take(self) -> dict[str, float | str]:
        with self._lock:
            writes, self._writes = self._writes, {}
        return writes

    def write(self, setter: Callable[[str, float | str], None],
              writes: Mapping[str, float | str]) -> list[str]:
        """Envoie les écritures ; renvoie les paramètres écrits. Ne lève jamais."""
        done = []
        for param, value in writes.items():
            if not self.supported(param):
                continue
            try:
                setter(param, value)
            except Exception as exc:
                self.failed(param, exc)
            else:
                done.append(param)
                self._failures.pop(param, None)
        return done

    # ------------------------------------------------------------ lectures
    def read(self, getter: Callable[[str], float], names: Sequence[str]) -> tuple[float, ...] | None:
        """Valeurs de ces paramètres, ou None si l'un d'eux n'est pas disponible. Ne lève jamais."""
        if not all(self.supported(name) for name in names):
            return None
        values = []
        for name in names:
            try:
                values.append(float(getter(name)))
            except Exception as exc:
                self.failed(name, exc)
                return None
            self._failures.pop(name, None)
        return tuple(values)

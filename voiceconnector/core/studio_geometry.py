"""
Géométrie et valeurs des commandes du Studio (sans Qt) : où tombe chaque pas du séquenceur, quelle
valeur donne un glissé de potard ou de fader, comment on peint plusieurs pas d'un seul geste.

Les widgets (ui/studio_widgets.py) ne font que dessiner et relayer la souris : tout ce qui se calcule
est ici, et se teste sans écran.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.daw_model import clamp

# --------------------------------------------------------------------------- #
# Pas du séquenceur
# --------------------------------------------------------------------------- #
STEP_GAP = 2.0                 # espace entre deux pas
GROUP_GAP = 5.0                # espace de plus entre deux groupes de quatre pas (un temps)
GROUP = 4
MIN_CELL = 3.0                 # largeur minimale d'un pas : en dessous, il reste cliquable mais très fin


@dataclass(frozen=True)
class StepCell:
    index: int
    left: float
    right: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def beat(self) -> int:
        """Numéro du temps (groupe de quatre pas) : sert à alterner les teintes."""
        return self.index // GROUP


class StepLayout:
    """Les `count` pas répartis sur `width` pixels, par groupes de quatre."""

    def __init__(self, width: float, count: int, *, margin: float = 0.0, min_cell: float = MIN_CELL) -> None:
        self.count = max(0, int(count))
        self.width = max(0.0, float(width))
        self.margin = max(0.0, float(margin))
        self.min_cell = max(1.0, float(min_cell))
        self.cells = self._build()

    def _build(self) -> list[StepCell]:
        if self.count == 0:
            return []
        groups = (self.count - 1) // GROUP
        room = self.width - 2 * self.margin - (self.count - 1) * STEP_GAP - groups * GROUP_GAP
        size = max(self.min_cell, room / self.count)
        cells, x = [], self.margin
        for index in range(self.count):
            cells.append(StepCell(index, x, x + size))
            x += size + STEP_GAP + (GROUP_GAP if (index + 1) % GROUP == 0 else 0.0)
        return cells

    @property
    def content_width(self) -> float:
        """Largeur réellement occupée (plus grande que `width` quand les pas sont au minimum)."""
        return self.cells[-1].right + self.margin if self.cells else 0.0

    def index_at(self, x: float) -> int | None:
        """Le pas sous l'abscisse `x` ; l'espace entre deux pas revient au plus proche, au-delà des bords : None."""
        if not self.cells or x < self.cells[0].left - STEP_GAP or x > self.cells[-1].right + STEP_GAP:
            return None
        low, high = 0, len(self.cells) - 1
        while low < high:                                           # le premier pas dont le bord droit dépasse x
            middle = (low + high) // 2
            if self.cells[middle].right >= x:
                high = middle
            else:
                low = middle + 1
        cell = self.cells[low]
        if x < cell.left and low > 0:
            before = self.cells[low - 1]
            return before.index if x - before.right < cell.left - x else cell.index
        return cell.index

    def span(self, first: float, last: float) -> list[int]:
        """Les pas touchés par un glissé de `first` à `last` (dans l'ordre du geste). Hors de la ligne,
        le glissé s'arrête au premier ou au dernier pas ; entièrement d'un même côté, il ne touche rien."""
        if not self.cells:
            return []
        start, end = self.index_at(first), self.index_at(last)
        left = self.cells[0].left
        if start is None and end is None and (first < left) == (last < left):
            return []
        if start is None:
            start = 0 if first < left else self.count - 1
        if end is None:
            end = 0 if last < left else self.count - 1
        step = 1 if end >= start else -1
        return list(range(start, end + step, step))


class StepPainter:
    """Peindre plusieurs pas d'un geste : le premier pas touché décide s'ils s'allument ou s'éteignent."""

    def __init__(self, layout: StepLayout, steps: list[bool]) -> None:
        self.layout = layout
        self.steps = list(steps)
        self.value: bool | None = None
        self._last_x = 0.0

    @property
    def active(self) -> bool:
        return self.value is not None

    def press(self, x: float) -> list[tuple[int, bool]]:
        index = self.layout.index_at(x)
        if index is None:
            return []
        self.value = not (self.steps[index] if index < len(self.steps) else False)
        self._last_x = x
        return self._apply([index])

    def move(self, x: float) -> list[tuple[int, bool]]:
        if self.value is None:
            return []
        indexes = self.layout.span(self._last_x, x)
        self._last_x = x
        return self._apply(indexes)

    def release(self) -> None:
        self.value = None

    def _apply(self, indexes: list[int]) -> list[tuple[int, bool]]:
        changes = []
        for index in indexes:
            if index >= len(self.steps):
                self.steps.extend([False] * (index + 1 - len(self.steps)))
            if self.steps[index] != self.value:
                self.steps[index] = bool(self.value)
                changes.append((index, bool(self.value)))
        return changes


def playhead_step(step_position: float, length: int) -> int:
    """Pas éclairé sous le curseur de lecture : la position en pas, ramenée à la longueur de la boucle."""
    if length <= 0 or step_position < 0:
        return -1
    return int(step_position) % int(length)


# --------------------------------------------------------------------------- #
# Potards et faders
# --------------------------------------------------------------------------- #
KNOB_SPAN = 150.0              # pixels de glissé pour parcourir toute la plage
FINE = 0.2                     # Maj enfoncée : réglage cinq fois plus fin
WHEEL_STEP = 0.02
CENTER_DETENT = 0.04           # le panoramique « colle » au centre sous cette distance


def drag_value(start: float, delta_y: float, *, low: float = 0.0, high: float = 1.0, fine: bool = False,
               span: float = KNOB_SPAN) -> float:
    """Valeur après un glissé vertical de `delta_y` pixels (vers le haut : positif, la valeur monte)."""
    scale = (high - low) / max(1.0, span) * (FINE if fine else 1.0)
    return clamp(start + delta_y * scale, low, high)


def wheel_value(value: float, notches: float, *, low: float = 0.0, high: float = 1.0, fine: bool = False) -> float:
    return clamp(value + notches * WHEEL_STEP * (high - low) * (FINE if fine else 1.0), low, high)


def snap_center(value: float, low: float = -1.0, high: float = 1.0, tolerance: float = CENTER_DETENT) -> float:
    middle = (low + high) / 2.0
    return middle if abs(value - middle) <= tolerance * (high - low) / 2.0 else value


def fader_value(y: float, top: float, bottom: float) -> float:
    """Valeur d'un fader vertical à la hauteur `y` (1 en haut de la course, 0 en bas)."""
    return clamp(1.0 - (y - top) / max(1.0, bottom - top), 0.0, 1.0)


def fader_y(value: float, top: float, bottom: float) -> float:
    return top + (1.0 - clamp(value, 0.0, 1.0)) * (bottom - top)


def percent_text(value: float) -> str:
    """Valeur d'un fader sans unité connue : « 78 »."""
    return str(int(round(clamp(value, 0.0, 1.0) * 100)))


def pan_text(pan: float) -> str:
    """« C » au centre, « G 40 » (gauche) ou « D 40 » (droite) : le panoramique en pour cent."""
    amount = int(round(abs(clamp(pan, -1.0, 1.0)) * 100))
    if amount == 0:
        return "C"
    return f"{'G' if pan < 0 else 'D'} {amount}"

"""
Modèle de la console : paramètres et niveaux d'une tranche, de la console entière, et
géométrie de l'Intellipan (le carré au petit cube). Aucun Qt ici : les moteurs
(Voicemeeter, démo, mode autonome, synchronisation FL Studio) produisent ces objets,
l'interface les affiche.

voicemeeter_ctrl.py réexporte tous ces noms : `from core.voicemeeter_ctrl import
MixerParams` continue de fonctionner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SILENCE_DB = -100.0
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0
DEFAULT_PHYSICAL_STRIPS = 5      # entrées matérielles de Voicemeeter Potato (édition par défaut)


def amp_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 1e-5 else SILENCE_DB


def clamp_gain(db: float) -> float:
    """Gain envoyé à Voicemeeter : sa plage, au dixième de dB (sa précision d'affichage)."""
    return round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(db))), 1)


# --- Intellipan -------------------------------------------------------------
@dataclass(frozen=True)
class PadMode:
    key: str          # nom court, stocké dans la config
    label: str        # affiché sur le pad
    title: str        # menu et info-bulle
    param: str        # préfixe des paramètres : Color → Color_x / Color_y


PAD_MODES = {
    "color": PadMode("color", "VOIX", "Voix (couleur du son)", "Color"),
    "pan": PadMode("pan", "POSITION", "Position (panoramique)", "Pan"),
    "fx": PadMode("fx", "EFFETS", "Effets (écho, modulation)", "fx"),
}
PHYSICAL_PAD_MODES = ("color", "pan", "fx")
VIRTUAL_PAD_MODES = ("pan",)
PAD_X_RANGE = (-0.5, 0.5)


def pad_modes(vm_strip: int, physical_strips: int = DEFAULT_PHYSICAL_STRIPS) -> tuple[str, ...]:
    """Modes d'Intellipan d'une strip Voicemeeter (Color et fx : entrées matérielles)."""
    return PHYSICAL_PAD_MODES if vm_strip < physical_strips else VIRTUAL_PAD_MODES


def pad_y_range(mode: str, physical: bool) -> tuple[float, float]:
    """Plage de y : 0 → 1, sauf le Pan 5.1 des entrées virtuelles (-0,5 → +0,5)."""
    return (-0.5, 0.5) if mode == "pan" and not physical else (0.0, 1.0)


def clamp_pad(mode: str, physical: bool, x: float, y: float) -> tuple[float, float]:
    low, high = pad_y_range(mode, physical)
    return (round(max(PAD_X_RANGE[0], min(PAD_X_RANGE[1], float(x))), 3),
            round(max(low, min(high, float(y))), 3))


# --- Compresseur, gate et égaliseur rapide (entrées matérielles) -----------------
KNOB_RANGE = (0.0, 10.0)              # compresseur et gate : le potentiomètre de Voicemeeter
EQ_RANGE_DB = (-12.0, 12.0)           # gain de chaque bande de l'égaliseur rapide
EQ_BANDS = ("Graves", "Médiums", "Aigus")
CLARITY_EQ = (-2.0, 1.0, 3.0)         # préréglage « Clarté » (dégage les graves, ouvre la voix)
FLAT_EQ = (0.0, 0.0, 0.0)


def clamp_knob(value: float) -> float:
    """Compresseur / gate : 0 (inactif) → 10, au dixième."""
    return round(max(KNOB_RANGE[0], min(KNOB_RANGE[1], float(value))), 1)


def clamp_eq(db: float) -> float:
    return round(max(EQ_RANGE_DB[0], min(EQ_RANGE_DB[1], float(db))), 1)


@dataclass(frozen=True)
class StripParams:
    gain: float
    mute: bool
    routes: tuple[bool, ...]   # un booléen par knob de routing
    sends: tuple[float, ...]   # niveau (dB) de chaque knob de routing
    # Intellipan : (mode, x, y) pour chaque mode de la strip
    pads: tuple[tuple[str, float, float], ...] = ()
    # Traitements de la voix : None quand le moteur ou la strip ne les a pas
    dynamics: tuple[float, float] | None = None            # (compresseur, gate), 0 → 10
    eq: tuple[float, float, float] | None = None           # graves, médiums, aigus (dB)

    def pad(self, mode: str) -> tuple[float, float] | None:
        for key, x, y in self.pads:
            if key == mode:
                return x, y
        return None


@dataclass(frozen=True)
class MixerParams:
    strips: tuple[StripParams, ...]
    headphone_gain: float

    def to_state(self) -> dict:
        """Forme sauvegardée dans config.json (section « state »)."""
        strips = []
        for s in self.strips:
            item = {"gain": s.gain, "mute": s.mute, "routes": list(s.routes), "sends": list(s.sends),
                    "pads": {mode: [x, y] for mode, x, y in s.pads}}
            if s.dynamics is not None:
                item["dynamics"] = list(s.dynamics)
            if s.eq is not None:
                item["eq"] = list(s.eq)
            strips.append(item)
        return {"strips": strips, "headphone_db": self.headphone_gain}


@dataclass(frozen=True)
class MixerLevels:
    strips: tuple[tuple[float, float], ...]  # (gauche, droite) en dB, post-fader
    master: tuple[float, float]              # sortie du bus casque

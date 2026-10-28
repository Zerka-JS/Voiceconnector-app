"""
Couleurs personnalisées des éléments de la console (données pures, sans Qt).

Réglages → Couleurs : chaque élément (knob d'envoi, fader, VU-mètre, bouton Muet…) peut
recevoir sa propre couleur, pour toute la console ou pour un seul canal. Les choix sont
mémorisés dans la config, section `ui`, clé `colors`, sous la forme `{clé: "#rrggbb"}` :

    "mute"        le bouton Muet allumé, sur tous les canaux
    "mute@2"      … sur le canal 3 seulement (les canaux sont comptés depuis 0)
    "vu@master"   le VU-mètre du Master

La couleur d'un canal passe avant celle de toute la console ; sans choix, l'élément suit le
thème. Ce module ne fait que vérifier, résoudre et mélanger des couleurs : c'est
`ui/styles.py` qui les applique aux widgets.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, MutableMapping

from core.themes import contrast_ratio

MASTER_SCOPE = "master"
SEPARATOR = "@"
SEND_COUNT = 4                         # knobs d'envoi par canal (A1, A2, B1, B2)
SEND_ELEMENTS = tuple(f"send{route}" for route in range(SEND_COUNT))

# Éléments propres à un canal, au Master, et libellés affichés dans l'éditeur
STRIP_ELEMENTS = (*SEND_ELEMENTS, "fader", "vu", "mute", "solo", "fx_badge", "fx_pill", "pad",
                  "apps", "name", "panel")
MASTER_ELEMENTS = ("headphone", "vu", "engines", "media", "panel")
ELEMENT_LABELS = {
    "fader": "Fader",
    "vu": "VU-mètres",
    "mute": "Bouton Muet (allumé)",
    "solo": "Bouton Solo (allumé)",
    "fx_badge": "Badge FX",
    "fx_pill": "Effets (pastilles)",
    "pad": "Intellipan (petit cube)",
    "apps": "Bouton Apps",
    "name": "Nom du canal",
    "panel": "Fond du panneau",
    "headphone": "Knob Casque",
    "engines": "Boutons Voicemeeter / REAPER",
    "media": "Lecteur de musique",
}
FORMAT_NAME = "voiceconnector-colors"
FORMAT_VERSION = 1

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CHANNEL = re.compile(r"^\d{1,3}$")

Scope = int | str | None               # canal, MASTER_SCOPE, ou None (toute la console)


def normalize(value: Any) -> str | None:
    """« #rgb » ou « #rrggbb » (casse libre) → « #rrggbb » en minuscules ; None si invalide."""
    if not isinstance(value, str) or not _HEX.match(value.strip()):
        return None
    text = value.strip().lower()
    if len(text) == 4:
        text = "#" + "".join(char * 2 for char in text[1:])
    return text


def color_key(element: str, scope: Scope = None) -> str:
    """Clé de `ui.colors` : « mute » pour toute la console, « mute@2 » pour un canal."""
    return element if scope is None else f"{element}{SEPARATOR}{scope}"


def is_known(element: str, scope: Scope = None) -> bool:
    """L'élément existe-t-il dans cette portée ? (toute la console : tous ; un canal : ceux
    d'un canal ; le Master : ceux du Master)."""
    return element in elements_for(scope)


def parse_key(key: Any) -> tuple[str, int | str | None] | None:
    """(élément, portée) d'une clé de config, ou None si elle n'a pas de sens."""
    if not isinstance(key, str):
        return None
    element, separator, raw_scope = key.partition(SEPARATOR)
    scope: int | str | None
    if not separator:
        scope = None
    elif raw_scope == MASTER_SCOPE:
        scope = MASTER_SCOPE
    elif _CHANNEL.match(raw_scope):
        scope = int(raw_scope)
    else:
        return None
    return (element, scope) if is_known(element, scope) else None


def elements_for(scope: Scope = None) -> tuple[str, ...]:
    """Éléments proposés pour une portée : ceux d'un canal, ceux du Master, ou (toute la
    console) les deux, sans doublon."""
    if scope == MASTER_SCOPE:
        return MASTER_ELEMENTS
    if scope is None:
        return STRIP_ELEMENTS + tuple(e for e in MASTER_ELEMENTS if e not in STRIP_ELEMENTS)
    return STRIP_ELEMENTS


def clean(colors: Any) -> dict[str, str]:
    """Les choix valides d'une config `ui.colors` (le reste est ignoré sans bruit : une
    config éditée à la main ou venant d'une autre version ne doit jamais empêcher de démarrer)."""
    if not isinstance(colors, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, raw in colors.items():
        value = normalize(raw)
        if value is not None and parse_key(key) is not None:
            result[str(key)] = value
    return result


def resolve(colors: Mapping[str, str], element: str, scope: Scope = None) -> str | None:
    """Couleur choisie pour un élément : celle de son canal (ou du Master), sinon celle de
    toute la console ; None si l'élément suit le thème. `colors` est supposé déjà vérifié
    (voir `clean`)."""
    if scope is not None and color_key(element, scope) in colors:
        return colors[color_key(element, scope)]
    return colors.get(element)


def has_own(colors: Mapping[str, str], element: str, scope: Scope = None) -> bool:
    """Un choix existe-t-il pour exactement cette portée (et pas seulement hérité) ?"""
    return color_key(element, scope) in colors


def set_color(colors: MutableMapping[str, str], element: str, scope: Scope,
              value: str | None) -> bool:
    """Choisit (ou, avec None, retire) la couleur d'un élément ; renvoie True si la config a
    changé. Une couleur invalide ou un élément qui n'existe pas dans cette portée sont refusés."""
    if not is_known(element, scope):
        return False
    key = color_key(element, scope)
    if value is None:
        return colors.pop(key, None) is not None
    color = normalize(value)
    if color is None or colors.get(key) == color:
        return False
    colors[key] = color
    return True


def scope_keys(colors: Mapping[str, str], scope: Scope) -> list[str]:
    """Les clés des choix faits pour exactement cette portée (toute la console, un canal ou le Master)."""
    return [key for key in colors if (parsed := parse_key(key)) is not None and parsed[1] == scope]


def reset_scope(colors: MutableMapping[str, str], scope: Scope) -> int:
    """Retire tous les choix d'une portée ; renvoie leur nombre."""
    keys = scope_keys(colors, scope)
    for key in keys:
        del colors[key]
    return len(keys)


def mix(first: str, second: str, amount: float) -> str:
    """Couleur entre `first` (amount = 0) et `second` (amount = 1), toutes deux « #rrggbb »."""
    amount = max(0.0, min(1.0, amount))
    a, b = normalize(first), normalize(second)
    if a is None or b is None:
        raise ValueError(f"couleur invalide : {first!r} / {second!r}")
    channels = [round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * amount)
                for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def ink_on(background: str, dark: str, light: str = "#ffffff") -> str:
    """Encre lisible sur un fond : la plus contrastée de `dark` et `light` (texte d'un bouton
    allumé dans une couleur choisie)."""
    return dark if contrast_ratio(dark, background) >= contrast_ratio(light, background) else light


# --------------------------------------------------------------------------- #
# Fichier d'échange (export / import)
# --------------------------------------------------------------------------- #
def export_text(colors: Mapping[str, str]) -> str:
    """Les couleurs choisies, en JSON lisible (pour les sauvegarder ou les partager)."""
    payload = {"format": FORMAT_NAME, "version": FORMAT_VERSION,
               "colors": dict(sorted(clean(colors).items()))}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def import_text(text: str) -> dict[str, str]:
    """Les couleurs d'un fichier exporté (ou d'un simple dictionnaire JSON {clé: couleur}).
    Lève ValueError si le texte n'est pas du JSON ou ne contient aucune couleur valide."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("fichier illisible : ce n'est pas du JSON") from exc
    if isinstance(data, Mapping) and data.get("format") == FORMAT_NAME:
        data = data.get("colors")
    colors = clean(data)
    if not colors:
        raise ValueError("aucune couleur valide dans ce fichier")
    return colors

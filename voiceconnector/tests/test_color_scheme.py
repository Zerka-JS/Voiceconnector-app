"""Couleurs personnalisées par élément (core/color_scheme.py) : vérification, portées, mélange, échange."""

import json

import pytest

from core import color_scheme as cs


# ---------------------------------------------------------------- couleurs
@pytest.mark.parametrize("raw, expected", [
    ("#FF8800", "#ff8800"), ("#abc", "#aabbcc"), ("  #0A0B0C ", "#0a0b0c"),
    ("#12345", None), ("12345f", None), ("red", None), ("", None), (None, None), (255, None),
    ("#gggggg", None), ("#ff880080", None),
])
def test_normalize(raw, expected):
    assert cs.normalize(raw) == expected


def test_mix_endpoints_and_middle():
    assert cs.mix("#000000", "#ffffff", 0) == "#000000"
    assert cs.mix("#000000", "#ffffff", 1) == "#ffffff"
    assert cs.mix("#ff0000", "#0000ff", 0.5) == "#800080"
    assert cs.mix("#102030", "#102030", 0.7) == "#102030"
    # La proportion est bornée : jamais de canal hors 0…255
    assert cs.mix("#102030", "#ffffff", 5) == "#ffffff"
    assert cs.mix("#102030", "#ffffff", -1) == "#102030"
    with pytest.raises(ValueError):
        cs.mix("rouge", "#ffffff", 0.5)


def test_ink_is_readable_on_any_background():
    from core.themes import contrast_ratio

    dark, light = "#00140a", "#ffffff"
    assert cs.ink_on("#ffffff", dark) == dark          # fond clair : encre sombre
    assert cs.ink_on("#101820", dark) == light         # fond sombre : encre claire
    for background in ("#ff0000", "#00ff88", "#3b82f6", "#ffcc00", "#7f7f7f", "#9b59b6"):
        ink = cs.ink_on(background, dark)
        other = light if ink == dark else dark
        assert contrast_ratio(ink, background) >= contrast_ratio(other, background)


# -------------------------------------------------------------------- clés
def test_keys_round_trip():
    assert cs.color_key("mute") == "mute"
    assert cs.color_key("mute", 2) == "mute@2"
    assert cs.color_key("vu", cs.MASTER_SCOPE) == "vu@master"
    for element, scope in (("mute", None), ("mute", 0), ("vu", 7), ("vu", cs.MASTER_SCOPE),
                           ("headphone", None), ("send3", 4)):
        assert cs.parse_key(cs.color_key(element, scope)) == (element, scope)


@pytest.mark.parametrize("key", [
    "", "inconnu", "mute@", "mute@x", "mute@-1", "mute@1.5", "mute@master@1", "@2",
    "headphone@2",          # le knob Casque n'existe pas sur un canal
    "send0@master",         # ni les envois sur le Master
    "fader@master",         # ni le fader (le Master n'en a pas)
    "mute@1234", None, 3, ("mute", 1),
])
def test_parse_key_rejects_nonsense(key):
    assert cs.parse_key(key) is None


def test_elements_by_scope():
    strip = cs.elements_for(2)
    master = cs.elements_for(cs.MASTER_SCOPE)
    everything = cs.elements_for(None)
    assert strip == cs.STRIP_ELEMENTS and master == cs.MASTER_ELEMENTS
    assert set(everything) == set(strip) | set(master)
    assert len(everything) == len(set(everything))          # pas de doublon (« vu », « panel »)
    assert "headphone" not in strip and "fader" not in master
    assert all(element in cs.ELEMENT_LABELS or element in cs.SEND_ELEMENTS for element in everything)
    assert cs.is_known("vu", cs.MASTER_SCOPE) and cs.is_known("vu", 3) and cs.is_known("vu")
    assert not cs.is_known("headphone", 3)


# ------------------------------------------------------------ vérification
def test_clean_keeps_only_valid_choices():
    raw = {"mute": "#B91C1C", "mute@2": "#abc", "vu@master": "#00ff88", "send2@0": "#123456",
           "mute@x": "#ffffff", "fader@master": "#ffffff", "inconnu": "#ffffff",
           "name": "blanc", "pad": 12, "apps": None, 7: "#ffffff"}
    assert cs.clean(raw) == {"mute": "#b91c1c", "mute@2": "#aabbcc", "vu@master": "#00ff88",
                             "send2@0": "#123456"}
    for junk in (None, [], "mute", 3, [("mute", "#ffffff")]):
        assert cs.clean(junk) == {}


def test_clean_is_idempotent():
    once = cs.clean({"fader@1": "#FFF", "panel": "#101010"})
    assert cs.clean(once) == once


# ----------------------------------------------------------------- portées
def test_channel_choice_beats_console_choice():
    colors = {"mute": "#111111", "mute@1": "#222222"}
    assert cs.resolve(colors, "mute", 1) == "#222222"
    assert cs.resolve(colors, "mute", 0) == "#111111"          # pas de choix propre : celui de la console
    assert cs.resolve(colors, "mute") == "#111111"
    assert cs.resolve(colors, "mute", cs.MASTER_SCOPE) == "#111111"
    assert cs.resolve(colors, "fader", 1) is None              # rien : l'élément suit le thème
    assert cs.resolve({}, "mute", 1) is None


def test_master_choice_beats_console_choice():
    colors = {"vu": "#111111", "vu@master": "#333333"}
    assert cs.resolve(colors, "vu", cs.MASTER_SCOPE) == "#333333"
    assert cs.resolve(colors, "vu", 0) == "#111111"            # le Master ne déteint pas sur les canaux
    assert cs.has_own(colors, "vu", cs.MASTER_SCOPE) and not cs.has_own(colors, "vu", 0)
    assert cs.has_own(colors, "vu") and not cs.has_own(colors, "mute")


def test_set_color_adds_changes_and_removes():
    colors: dict[str, str] = {}
    assert cs.set_color(colors, "mute", None, "#FFAA00") is True
    assert colors == {"mute": "#ffaa00"}
    assert cs.set_color(colors, "mute", None, "#ffaa00") is False        # rien n'a changé
    assert cs.set_color(colors, "mute", 2, "#abc") is True
    assert colors == {"mute": "#ffaa00", "mute@2": "#aabbcc"}
    assert cs.set_color(colors, "mute", 2, None) is True                 # retour à celle de la console
    assert cs.set_color(colors, "mute", 2, None) is False
    assert colors == {"mute": "#ffaa00"}


def test_set_color_refuses_invalid_input():
    colors = {"mute": "#ffaa00"}
    assert cs.set_color(colors, "mute", None, "orange") is False
    assert cs.set_color(colors, "headphone", 1, "#ffffff") is False       # pas sur un canal
    assert cs.set_color(colors, "inconnu", None, "#ffffff") is False
    assert cs.set_color(colors, "fader", cs.MASTER_SCOPE, "#ffffff") is False
    assert colors == {"mute": "#ffaa00"}


def test_scope_keys_lists_only_that_scope():
    colors = {"mute": "#111111", "mute@1": "#222222", "fader@1": "#333333", "vu@master": "#444444"}
    assert sorted(cs.scope_keys(colors, 1)) == ["fader@1", "mute@1"]
    assert cs.scope_keys(colors, None) == ["mute"]
    assert cs.scope_keys(colors, cs.MASTER_SCOPE) == ["vu@master"]
    assert cs.scope_keys(colors, 5) == []


def test_reset_scope_only_touches_that_scope():
    colors = {"mute": "#111111", "mute@1": "#222222", "fader@1": "#333333", "vu@master": "#444444",
              "pad@10": "#555555"}
    assert cs.reset_scope(colors, 1) == 2
    assert colors == {"mute": "#111111", "vu@master": "#444444", "pad@10": "#555555"}
    assert cs.reset_scope(colors, cs.MASTER_SCOPE) == 1
    assert cs.reset_scope(colors, None) == 1
    assert colors == {"pad@10": "#555555"}
    assert cs.reset_scope(colors, 1) == 0


# ------------------------------------------------------------------ échange
def test_export_import_round_trip():
    colors = {"mute": "#b91c1c", "fader@1": "#00ff88", "vu@master": "#ffcc00"}
    text = cs.export_text(colors)
    payload = json.loads(text)
    assert payload["format"] == cs.FORMAT_NAME and payload["version"] == cs.FORMAT_VERSION
    assert cs.import_text(text) == colors


def test_export_drops_invalid_entries():
    payload = json.loads(cs.export_text({"mute": "#b91c1c", "inconnu": "#ffffff", "pad": "x"}))
    assert payload["colors"] == {"mute": "#b91c1c"}


def test_import_accepts_a_bare_dictionary():
    assert cs.import_text('{"mute": "#ABCDEF", "bidon": "#ffffff"}') == {"mute": "#abcdef"}


@pytest.mark.parametrize("text", ["", "pas du json", "[]", "{}", '{"format": "voiceconnector-colors"}',
                                  '{"colors": {"mute": "rouge"}}', "null", None])
def test_import_rejects_unusable_files(text):
    with pytest.raises(ValueError):
        cs.import_text(text)

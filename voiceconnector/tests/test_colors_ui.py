"""Couleurs par élément dans l'interface : onglet « Couleurs » des réglages et widgets de la console.

Le cœur (clés, portées, mélange, fichiers d'échange) est testé sans Qt dans test_color_scheme.py ;
ici, on vérifie que les choix atteignent les widgets, tout de suite, et que « Défaut » / « Tout
réinitialiser » ramènent aux couleurs du thème."""

import json

import pytest

pytest.importorskip("PySide6")

from conftest import pump  # noqa: E402
from test_modes_ui import build_window  # noqa: E402


@pytest.fixture
def settings(qapp, mixer):
    """Réglages ouverts sur la console en démo ; les changements de couleur se répercutent sur la fenêtre."""
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, window._processes, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    yield window, dialog, dialog.colors_tab
    window._cfg["ui"]["colors"] = {}
    window.apply_appearance()
    dialog.done(0)


# --------------------------------------------------------------------------- #
# Configuration → styles
# --------------------------------------------------------------------------- #
def test_configure_reads_only_valid_colors(qapp):
    from ui import styles

    try:
        assert styles.configure({"colors": {"mute": "#00FF00", "vu@master": "#abc", "bidon": "#ffffff",
                                            "fader": "pas une couleur", "pad@x": "#ffffff"}})
        assert styles.COLORS == {"mute": "#00ff00", "vu@master": "#aabbcc"}
        assert not styles.configure({"colors": {"mute": "#00ff00", "vu@master": "#aabbcc"}})   # inchangé
        assert styles.configure({"colors": "n'importe quoi"}) and styles.COLORS == {}
    finally:
        styles.configure({})


def test_element_color_priority_and_defaults(qapp):
    from ui import styles

    try:
        styles.configure({"colors": {"mute": "#111111", "mute@1": "#222222", "vu@master": "#333333"}})
        assert styles.element_color("mute", 0) == "#111111" and styles.element_color("mute", 1) == "#222222"
        assert styles.element_color("vu", styles.MASTER_SCOPE) == "#333333"
        assert styles.element_color("vu", 0) is None
        assert styles.accent("vu", 0) == styles.GREEN_NEON                 # suit le thème
        assert styles.accent("mute", 3) == "#111111"
        assert styles.accent("solo") == styles.SOLO_ON and styles.accent("pad") == styles.RED_MUTE
        # La config à consulter peut être passée à la main (l'éditeur montre un choix avant qu'il soit appliqué)
        assert styles.element_color("mute", 1, {"mute@1": "#444444"}) == "#444444"
        assert styles.element_color("mute", 1, {"mute@x": "#444444"}) is None
    finally:
        styles.configure({})


def _lightness(value: str) -> float:
    """Clarté HLS d'une couleur « #rrggbb » (sans dépendre de Qt)."""
    import colorsys

    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[1]


def test_derived_shades_follow_the_chosen_color(qapp):
    from ui import styles

    try:
        styles.configure({"colors": {"fader": "#ff8800"}})
        assert styles.accent_dim("fader", 0) != styles.GREEN_DIM
        assert styles.accent_bright("fader", 0) != styles.GREEN_BRIGHT
        assert _lightness(styles.accent_bright("fader", 0)) > _lightness("#ff8800")
        assert styles.accent_dim("send0", 0) == styles.GREEN_DIM and styles.accent_bright("send0", 0) == styles.GREEN_BRIGHT
    finally:
        styles.configure({})


def test_lit_button_styles_pick_a_readable_ink(qapp):
    from ui import styles

    light = styles.mute_style("#ffee00")
    dark = styles.mute_style("#101820")
    assert "QPushButton#muteButton:checked" in light and "background: #ffee00" in light
    assert f"color: {styles.ON_ACCENT_DARK}" in light and f"color: {styles.WHITE}" in dark
    assert "QPushButton#soloButton:checked" in styles.solo_style("#00aaff")
    panel = styles.panel_style("#402000", "QFrame#column")
    assert panel.startswith("QFrame#column {") and "rgba(64, 32, 0," in panel


# --------------------------------------------------------------------------- #
# Onglet « Couleurs »
# --------------------------------------------------------------------------- #
def test_colors_tab_lists_the_elements_of_each_scope(qapp, settings):
    from ui import styles

    _, dialog, tab = settings
    tabs = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert "Couleurs" in tabs and tabs.index("Couleurs") == tabs.index("Apparence") + 1
    assert tab.scope() is None
    assert {"send0", "send3", "fader", "vu", "mute", "solo", "fx_badge", "fx_pill", "pad", "apps", "name",
            "panel", "headphone", "engines", "media"} <= set(tab.color_buttons)
    assert tab.color_labels["send2"] == "Knob Discord" and tab.color_labels["send0"] == "Knob Monitor"

    tab.color_scope_choice.setCurrentIndex(tab.color_scope_choice.findData(1))
    assert "headphone" not in tab.color_buttons and "fader" in tab.color_buttons
    tab.color_scope_choice.setCurrentIndex(tab.color_scope_choice.findData(styles.MASTER_SCOPE))
    assert set(tab.color_buttons) == {"headphone", "vu", "engines", "media", "panel"}
    channels = [tab.color_scope_choice.itemData(i) for i in range(tab.color_scope_choice.count())]
    assert channels[0] is None and channels[-1] == styles.MASTER_SCOPE
    assert channels[1:-1] == list(range(len(channels) - 2)) and len(channels) - 2 == len(window_strips(dialog))


def window_strips(dialog):
    return dialog.parent().strips


def test_element_colors_per_console_and_channel(qapp, settings):
    """Pour toute la console, puis un canal en particulier (prioritaire), appliquée tout de suite."""
    from ui import styles

    window, _, tab = settings
    tab.set_element_color("mute", None, "#00ff00")
    tab.set_element_color("send2", None, "#ff00ff")
    tab.set_element_color("solo", None, "#00aaff")
    assert window._cfg["ui"]["colors"] == {"mute": "#00ff00", "send2": "#ff00ff", "solo": "#00aaff"}
    assert all("#00ff00" in strip.mute.styleSheet() for strip in window.strips)
    assert all("#00aaff" in strip.solo.styleSheet() for strip in window.strips)
    assert styles.accent("send2", 0) == "#ff00ff" and styles.accent("send1", 0) == styles.GREEN_NEON
    assert tab.color_buttons["mute"].custom and tab.color_resets["mute"].isEnabled()
    assert not tab.color_buttons["fader"].custom and not tab.color_resets["fader"].isEnabled()

    # Canal 2 seulement : prioritaire sur toute la console
    tab.color_scope_choice.setCurrentIndex(tab.color_scope_choice.findData(1))
    assert tab.color_buttons["mute"].color == "#00ff00" and not tab.color_buttons["mute"].custom
    tab.set_element_color("mute", 1, "#123456")
    tab.set_element_color("panel", 1, "#402000")
    tab.set_element_color("name", 1, "#ffcc00")
    assert "#123456" in window.strips[1].mute.styleSheet() and "#00ff00" in window.strips[0].mute.styleSheet()
    assert "64, 32, 0" in window.strips[1].styleSheet() and not window.strips[0].styleSheet()
    assert "#ffcc00" in window.strips[1].name._label.styleSheet() and not window.strips[0].name._label.styleSheet()

    # Le Master a ses propres choix et ne déteint pas sur les canaux
    tab.set_element_color("vu", styles.MASTER_SCOPE, "#ff8800")
    assert styles.accent("vu", styles.MASTER_SCOPE) == "#ff8800" and styles.accent("vu", 1) == styles.GREEN_NEON
    tab.set_element_color("panel", styles.MASTER_SCOPE, "#003040")
    assert "0, 48, 64" in window.master.styleSheet()
    window.grab()                                          # rendu avec les couleurs choisies

    tab.color_resets["mute"].click()                       # « Défaut » : comme toute la console
    assert "#00ff00" in window.strips[1].mute.styleSheet()
    assert window._cfg["ui"]["colors"]["panel@1"] == "#402000"


def test_reset_scope_and_reset_all(qapp, settings):
    from ui import styles

    window, _, tab = settings
    tab.set_element_color("mute", None, "#00ff00")
    tab.set_element_color("fader", 1, "#ff8800")
    tab.set_element_color("panel", 1, "#402000")
    tab.set_element_color("vu", styles.MASTER_SCOPE, "#ff8800")
    tab.color_scope_choice.setCurrentIndex(tab.color_scope_choice.findData(1))
    assert tab.reset_scope_button.isEnabled()
    tab.reset_scope()
    assert window._cfg["ui"]["colors"] == {"mute": "#00ff00", "vu@master": "#ff8800"}
    assert not window.strips[1].styleSheet() and not tab.reset_scope_button.isEnabled()
    tab.reset_element_colors()
    assert window._cfg["ui"]["colors"] == {} and not tab.reset_all_button.isEnabled()
    assert not any(strip.mute.styleSheet() for strip in window.strips) and not window.master.styleSheet()
    assert styles.accent("vu", styles.MASTER_SCOPE) == styles.GREEN_NEON


def test_colors_survive_a_theme_change(qapp, settings):
    from ui import styles

    window, dialog, tab = settings
    tab.set_element_color("fader", None, "#ff8800")
    original = window._cfg["ui"].get("theme")
    try:
        dialog.set_theme("orange")
        assert styles.accent("fader", 0) == "#ff8800"          # un choix précis passe avant le thème
        assert styles.GREEN_NEON == styles.THEMES["orange"][1]
    finally:
        dialog.set_theme(original or styles.DEFAULT_THEME)
    assert window._cfg["ui"]["colors"] == {"fader": "#ff8800"}


def test_drawn_widgets_read_their_channel_color(qapp, mixer):
    from ui import styles

    window, _, _ = mixer
    strip = window.strips[2]
    assert strip.fader.color_scope == 2 and strip.vu.color_scope == 2 and strip.fx_badge.color_scope == 2
    assert strip.fx_list.color_scope == 2 and strip.pad.color_scope == 2
    assert [(knob.color_element, knob.color_scope) for knob in strip.knobs] == [
        ("send0", 2), ("send1", 2), ("send2", 2), ("send3", 2)]
    assert window.master.vu.color_scope == styles.MASTER_SCOPE
    assert (window.master.knob.color_element, window.master.knob.color_scope) == ("headphone", styles.MASTER_SCOPE)
    try:
        window._cfg["ui"]["colors"] = {"send1@2": "#ff0000", "fader": "#00ff00", "vu@master": "#0000ff"}
        window.apply_appearance()
        window.grab()
        assert styles.accent("send1", 2) == "#ff0000" and styles.accent("send1", 1) == styles.GREEN_NEON
        assert styles.accent("fader", 4) == "#00ff00"
    finally:
        window._cfg["ui"]["colors"] = {}
        window.apply_appearance()


def test_panel_color_follows_the_panel_opacity(qapp, settings):
    window, dialog, tab = settings
    tab.set_element_color("panel", 0, "#402000")
    assert "rgba(64, 32, 0, 1.000)" in window.strips[0].styleSheet()
    dialog.opacity.setValue(60)
    assert "rgba(64, 32, 0, 0.600)" in window.strips[0].styleSheet()
    dialog.opacity.setValue(100)


# --------------------------------------------------------------------------- #
# Export / import
# --------------------------------------------------------------------------- #
def test_export_then_import_restores_the_colors(qapp, settings, tmp_path):
    window, _, tab = settings
    tab.set_element_color("mute", None, "#00ff00")
    tab.set_element_color("fader", 1, "#ff8800")
    target = tmp_path / "couleurs.json"
    assert tab.export_to(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["colors"] == {"fader@1": "#ff8800", "mute": "#00ff00"}

    tab.reset_element_colors()
    assert window._cfg["ui"]["colors"] == {}
    assert tab.import_from(target)
    assert window._cfg["ui"]["colors"] == {"fader@1": "#ff8800", "mute": "#00ff00"}
    assert "#00ff00" in window.strips[0].mute.styleSheet() and "importée" in tab.status.text()


def test_a_bad_file_never_changes_the_colors(qapp, settings, tmp_path):
    window, _, tab = settings
    tab.set_element_color("mute", None, "#00ff00")
    for name, content in (("vide.json", ""), ("texte.json", "pas du json"), ("rien.json", '{"colors": {}}')):
        bad = tmp_path / name
        bad.write_text(content, encoding="utf-8")
        assert not tab.import_from(bad), name
        assert "impossible" in tab.status.text()
    assert not tab.import_from(tmp_path / "absent.json")
    assert window._cfg["ui"]["colors"] == {"mute": "#00ff00"}
    assert not tab.export_to(tmp_path / "dossier" / "inexistant" / "couleurs.json")      # dossier absent


# --------------------------------------------------------------------------- #
# Mode autonome
# --------------------------------------------------------------------------- #
def test_standalone_mode_has_no_send_knobs_to_color(qapp, cfg):
    from ui.settings_dialog import SettingsDialog

    window, vm, session = build_window(qapp, cfg, standalone=True)
    try:
        dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False, session=session)
        try:
            tab = dialog.colors_tab
            assert "fader" in tab.color_buttons and "mute" in tab.color_buttons
            assert not any(key.startswith("send") for key in tab.color_buttons)
            names = [tab.color_scope_choice.itemText(i) for i in range(1, tab.color_scope_choice.count() - 1)]
            assert len(names) == len(window.strips) and all(name.startswith("Canal ") for name in names)
        finally:
            dialog.done(0)
    finally:
        window.close()
    pump(qapp, 0.05)

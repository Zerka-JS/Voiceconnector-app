"""Thèmes globaux : complets, lisibles, cohérents (données pures, sans Qt)."""

from dataclasses import replace

import pytest

from core import themes
from core.themes import (CONTRAST_RULES, EXTRA_KEYS, GLOBAL_THEME_ORDER, GLOBAL_THEMES,
                         PALETTE_KEYS, SHAPE_KEYS, SKIN_KEYS, check_theme, contrast_ratio,
                         global_theme, luminance)


def test_the_four_requested_themes_exist():
    assert GLOBAL_THEME_ORDER == ("retro", "cyberpunk", "flat", "slate")
    names = {key: spec.name for key, spec in GLOBAL_THEMES.items()}
    assert "VFD" in names["retro"] and "Neon Wave Synth" in names["retro"]
    assert "Cyberpunk" in names["cyberpunk"]
    assert "Flat Modern" in names["flat"] and "Minimaliste" in names["flat"]
    assert "Dark Slate" in names["slate"] and "Studio Pro" in names["slate"]
    assert global_theme("retro") is GLOBAL_THEMES["retro"]
    assert global_theme("vintage") is None and global_theme("#00aaff") is None
    assert global_theme(None) is None


@pytest.mark.parametrize("key", GLOBAL_THEME_ORDER)
def test_theme_is_complete_and_readable(key):
    spec = GLOBAL_THEMES[key]
    assert check_theme(spec) == []
    assert set(spec.palette) == set(PALETTE_KEYS)
    assert set(spec.skin) == set(SKIN_KEYS)
    assert set(spec.extras) <= set(EXTRA_KEYS)
    assert set(spec.shape) <= set(SHAPE_KEYS)
    values = spec.values()
    assert all(name in values for name in SHAPE_KEYS)       # rayons par défaut complétés
    assert spec.tagline and spec.suggested_layout in {"standard", "fl_studio", "streaming", "podcast"}


def test_only_the_flat_theme_is_light():
    assert [k for k, spec in GLOBAL_THEMES.items() if not spec.dark] == ["flat"]
    assert luminance(GLOBAL_THEMES["flat"].palette["BG_WINDOW"]) > 0.6
    for key in ("retro", "cyberpunk", "slate"):
        assert luminance(GLOBAL_THEMES[key].palette["BG_WINDOW"]) < 0.05


def test_flat_skin_has_no_gradient():
    skin = GLOBAL_THEMES["flat"].skin
    assert skin["CREAM"] == skin["CREAM_DARK"]               # aplat, comme l'indique « Flat Modern »
    assert GLOBAL_THEMES["flat"].shape["R_COLUMN"] > GLOBAL_THEMES["cyberpunk"].shape["R_COLUMN"]


def test_contrast_helpers():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert luminance("#ffffff") == pytest.approx(1.0) and luminance("#000000") == 0.0


def test_check_theme_detects_problems():
    base = GLOBAL_THEMES["slate"]
    missing = replace(base, palette={k: v for k, v in base.palette.items() if k != "BG_DEEP"})
    assert any("sans BG_DEEP" in p for p in check_theme(missing))

    invalid = replace(base, palette={**base.palette, "BG_DEEP": "rouge"})
    assert any("couleur invalide BG_DEEP" in p for p in check_theme(invalid))

    unreadable = replace(base, palette={**base.palette, "TEXT_PRIMARY": "#222a38"})
    assert any("TEXT_PRIMARY sur BG_COLUMN" in p for p in check_theme(unreadable))

    wrong_flag = replace(base, dark=False)
    assert any("incohérent" in p for p in check_theme(wrong_flag))

    assert any("arrière-plan inconnu" in p for p in check_theme(replace(base, backdrop="x")))
    assert any("clé inconnue" in p for p in check_theme(replace(base, shape={"R_BIDON": 3})))
    assert any("rayon invalide" in p for p in check_theme(replace(base, shape={"R_COLUMN": -1})))


def test_every_contrast_rule_references_known_keys():
    known = set(PALETTE_KEYS) | set(SKIN_KEYS) | set(EXTRA_KEYS)
    for foreground, background, minimum in CONTRAST_RULES:
        assert minimum >= 3.0
        for name in (foreground, background):
            assert name.startswith("#") or name in known, name


def test_module_has_no_qt_dependency():
    import sys

    assert "PySide6" not in sys.modules or themes.__name__ == "core.themes"
    source = open(themes.__file__, encoding="utf-8").read()
    assert "PySide6" not in source and "PyQt" not in source

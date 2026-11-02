"""Arrangement des tranches : la console (toutes les tranches) et l'arrangement studio du mode musical
(logique pure, sans Qt)."""

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core.layout_manager import (APP_SESSION_CAPS, FL_STUDIO, FL_TRACK_CAPS, FULL_CAPS, LAYOUT_PRESETS,
                                 MODE_STANDALONE, MODE_VOICEMEETER, STANDARD, EngineCaps, LayoutManager,
                                 StripInfo, StripStyle, assign_roles, build_plan, style_for)


def console(hardware: int, virtual: int) -> list[StripInfo]:
    """Canaux d'une console : d'abord les entrées matérielles, puis les virtuelles."""
    return [StripInfo(i, physical=i < hardware) for i in range(hardware + virtual)]


BANANA = console(3, 2)
POTATO = console(5, 3)
STUDIO_SLOTS = LAYOUT_PRESETS[FL_STUDIO].slots


def active(key: str, running: bool = True) -> ad.ActiveApp:
    return ad.ActiveApp(ad.known_app(key), ad.SOURCE_DETECTED, running)


# --------------------------------------------------------------------------- #
# Catalogue : la console, et l'arrangement studio du mode musical
# --------------------------------------------------------------------------- #
def test_there_is_no_layout_to_choose_only_the_console_and_the_studio_arrangement():
    assert set(LAYOUT_PRESETS) == {STANDARD, FL_STUDIO}
    for removed in ("AUTO", "STREAMING", "PODCAST", "PRESET_ORDER", "layout_choices", "GROUP_HARDWARE"):
        assert not hasattr(lm, removed), removed


def test_the_studio_arrangement_has_the_requested_slots():
    labels = [slot.label for slot in STUDIO_SLOTS]
    assert labels == ["Master", "Bus Batterie", "Synths", "FX", "Micro"]
    assert "Studio" in LAYOUT_PRESETS[FL_STUDIO].name
    roles = [slot.role for slot in STUDIO_SLOTS]
    assert len(roles) == len(set(roles)), "un rôle ne peut apparaître qu'une fois"
    assert set(roles) <= lm.ALL_ROLES
    for slot in STUDIO_SLOTS:
        assert slot.kind in (lm.KIND_ANY, lm.KIND_PHYSICAL, lm.KIND_VIRTUAL)


def test_studio_arrangement_has_wide_pan_and_precise_vu_the_console_is_unchanged():
    style = LAYOUT_PRESETS[FL_STUDIO].style
    assert style.pan == "wide" and style.vu == "precision"
    assert LAYOUT_PRESETS[STANDARD].style == StripStyle()
    assert LAYOUT_PRESETS[STANDARD].slots == ()
    assert StripStyle().dynamics and StripStyle().fx and StripStyle().routing


def test_style_rejects_unknown_values():
    with pytest.raises(ValueError):
        StripStyle(pan="gros")
    with pytest.raises(ValueError):
        StripStyle(vu="enorme")


# --------------------------------------------------------------------------- #
# Affectation des rôles
# --------------------------------------------------------------------------- #
def test_roles_follow_the_preferred_channel_type():
    roles = assign_roles(STUDIO_SLOTS, POTATO)
    assert roles[lm.ROLE_MIC] == 0                                    # 1re entrée matérielle
    assert (roles[lm.ROLE_MASTER], roles[lm.ROLE_DRUMS], roles[lm.ROLE_SYNTHS], roles[lm.ROLE_FX]) == (5, 6, 7, 1)
    assert len(set(roles.values())) == len(roles)


def test_roles_fall_back_to_any_free_channel():
    # Banana : 2 virtuels seulement pour 4 emplacements virtuels (Master, Bus Batterie, Synths, FX)
    roles = assign_roles(STUDIO_SLOTS, BANANA)
    assert roles[lm.ROLE_MIC] == 0
    assert roles[lm.ROLE_MASTER] == 3 and roles[lm.ROLE_DRUMS] == 4
    assert roles[lm.ROLE_SYNTHS] == 1 and roles[lm.ROLE_FX] == 2      # repli : les premiers canaux libres
    assert len(set(roles.values())) == len(roles)


def test_more_slots_than_channels_leaves_some_roles_unassigned():
    roles = assign_roles(STUDIO_SLOTS, console(1, 1))
    assert len(roles) == 2 and len(set(roles.values())) == 2


def test_explicit_roles_win_and_are_validated():
    roles = assign_roles(STUDIO_SLOTS, POTATO, {lm.ROLE_MIC: 1, lm.ROLE_FX: 0})
    assert roles[lm.ROLE_MIC] == 1 and roles[lm.ROLE_FX] == 0
    assert len(set(roles.values())) == len(roles)
    # canal inexistant, ou déjà pris par un autre emplacement : l'automatique reprend
    roles = assign_roles(STUDIO_SLOTS, POTATO, {lm.ROLE_MIC: 99})
    assert roles[lm.ROLE_MIC] == 0
    roles = assign_roles(STUDIO_SLOTS, POTATO, {lm.ROLE_MIC: 4, lm.ROLE_FX: 4})
    assert roles[lm.ROLE_FX] == 4                                     # l'emplacement qui vient en premier l'emporte
    assert roles[lm.ROLE_MIC] == 0                                    # l'autre retombe sur l'automatique
    assert len(set(roles.values())) == len(roles)


def test_declared_roles_from_a_profile_beat_the_automatic_choice():
    strips = [StripInfo(0, True, "Pod mic", role=lm.ROLE_FX), *BANANA[1:]]
    roles = assign_roles(STUDIO_SLOTS, strips)
    assert roles[lm.ROLE_FX] == 0
    # le premier qui déclare un rôle l'obtient
    twice = [StripInfo(0, True, role=lm.ROLE_MIC), StripInfo(1, True, role=lm.ROLE_MIC), *BANANA[2:]]
    assert assign_roles(STUDIO_SLOTS, twice)[lm.ROLE_MIC] == 0


def test_fixed_strips_are_never_assigned_automatically():
    """Profil du mode autonome : un canal sans rôle ne prend pas l'emplacement d'un autre."""
    strips = [StripInfo(0, False, "Master", role=lm.ROLE_MASTER, fixed=True),
              StripInfo(1, False, "Ableton", fixed=True),
              StripInfo(2, True, "Micro", role=lm.ROLE_MIC, fixed=True)]
    roles = assign_roles(STUDIO_SLOTS, strips)
    assert roles == {lm.ROLE_MASTER: 0, lm.ROLE_MIC: 2}              # rien pour Ableton
    plan = build_plan(LAYOUT_PRESETS[FL_STUDIO], strips)
    assert plan.order == (0, 2) and plan.hidden == (1,)
    assert plan.strip(1).role is None and not plan.strip(1).visible
    # un choix manuel de l'utilisateur reste possible, même sur un canal fixe
    roles = assign_roles(STUDIO_SLOTS, strips, {lm.ROLE_SYNTHS: 1})
    assert roles[lm.ROLE_SYNTHS] == 1
    # sans « fixed », le canal libre aurait pris le premier emplacement vide
    loose = [StripInfo(i.index, i.physical, i.name, i.role) for i in strips]
    assert 1 in assign_roles(STUDIO_SLOTS, loose).values()


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
def test_console_plan_shows_every_channel_hardware_first():
    plan = build_plan(LAYOUT_PRESETS[STANDARD], POTATO)
    assert plan.preset == STANDARD
    assert plan.order == tuple(range(8)) and plan.hidden == ()
    assert all(s.role is None and s.label is None and s.visible for s in plan.strips)
    mixed = [StripInfo(0, False), StripInfo(1, True), StripInfo(2, False), StripInfo(3, True)]
    assert build_plan(LAYOUT_PRESETS[STANDARD], mixed).order == (1, 3, 0, 2)


def test_roles_declared_by_a_profile_stay_known_on_the_console():
    """Le routage sans écho (Discord, Invité) lit les rôles du plan, même sur la console."""
    strips = [StripInfo(0, True, "Micro", lm.ROLE_MIC, fixed=True),
              StripInfo(1, False, "Discord", lm.ROLE_DISCORD, fixed=True),
              StripInfo(2, False, "Autre", "inconnu", fixed=True),
              StripInfo(3, False, "Libre")]
    plan = build_plan(LAYOUT_PRESETS[STANDARD], strips)
    assert plan.roles == {lm.ROLE_MIC: 0, lm.ROLE_DISCORD: 1}
    assert plan.strip(2).role is None and plan.strip(3).role is None   # rôle inconnu : ignoré
    assert all(s.label is None for s in plan.strips)                    # aucun libellé d'emplacement
    assert plan.order == (0, 1, 2, 3) and plan.hidden == ()


def test_studio_plan_orders_the_slots_and_hides_the_other_channels():
    plan = build_plan(LAYOUT_PRESETS[FL_STUDIO], POTATO)
    assert plan.order == (5, 6, 7, 1, 0)                              # Master, Bus Batterie, Synths, FX, Micro
    assert plan.hidden == (2, 3, 4)
    assert [plan.strip(i).label for i in plan.order] == ["Master", "Bus Batterie", "Synths", "FX", "Micro"]
    assert plan.roles == {lm.ROLE_MASTER: 5, lm.ROLE_DRUMS: 6, lm.ROLE_SYNTHS: 7, lm.ROLE_FX: 1, lm.ROLE_MIC: 0}
    assert not plan.strip(2).visible and plan.strip(2).role is None
    assert {plan.strip(i).style.pan for i in plan.order} == {"wide"}
    assert {plan.strip(i).style.vu for i in plan.order} == {"precision"}


def test_plan_without_routing_hides_the_quick_routing():
    """Mode B : pas de Voicemeeter, donc rien à router."""
    no_routing = EngineCaps(routing=False)
    plan = build_plan(LAYOUT_PRESETS[STANDARD], BANANA, caps=no_routing)
    assert all(not s.style.routing and s.style.dynamics for s in plan.strips)
    plan = build_plan(LAYOUT_PRESETS[FL_STUDIO], BANANA, caps=no_routing)
    assert all(not plan.strip(i).style.routing for i in plan.order)


def test_engine_caps_restrict_what_a_strip_shows():
    studio = LAYOUT_PRESETS[FL_STUDIO].style
    assert style_for(studio, FULL_CAPS) == studio
    no_pan = style_for(studio, EngineCaps(pan=False))
    assert no_pan.pan == "none" and no_pan.vu == "precision"          # le reste est conservé
    session = style_for(StripStyle(), APP_SESSION_CAPS)
    assert not (session.dynamics or session.fx or session.routing) and session.pan == "none"
    # pistes de FL Studio : panoramique oui, le reste non
    assert style_for(StripStyle(), FL_TRACK_CAPS).pan == "pad"
    assert not style_for(StripStyle(), FL_TRACK_CAPS).routing
    assert not style_for(StripStyle(), FL_TRACK_CAPS).dynamics


def test_caps_combine_with_and():
    assert APP_SESSION_CAPS & FL_TRACK_CAPS == EngineCaps(routing=False, pan=False, dynamics=False, fx=False)
    assert FULL_CAPS & FL_TRACK_CAPS == FL_TRACK_CAPS
    assert FULL_CAPS & FULL_CAPS == FULL_CAPS


def test_a_strip_can_have_its_own_caps():
    strips = [StripInfo(0, True), StripInfo(1, True, caps=FL_TRACK_CAPS)]
    plan = build_plan(LAYOUT_PRESETS[STANDARD], strips, caps=APP_SESSION_CAPS)
    assert plan.strip(0).style.pan == "none" and plan.strip(1).style.pan == "pad"


def test_plan_lookup_of_a_missing_channel_raises():
    plan = build_plan(LAYOUT_PRESETS[STANDARD], BANANA)
    with pytest.raises(KeyError):
        plan.strip(42)


@pytest.mark.parametrize("key", sorted(LAYOUT_PRESETS))
@pytest.mark.parametrize("strips", [console(0, 0), console(1, 0), BANANA, POTATO])
def test_every_channel_is_shown_at_most_once(key, strips):
    plan = build_plan(LAYOUT_PRESETS[key], strips)
    assert len(plan.order) == len(set(plan.order))
    assert set(plan.order) | set(plan.hidden) == {s.index for s in strips}
    assert not set(plan.order) & set(plan.hidden)
    assert [s.index for s in plan.strips] == [s.index for s in strips]


# --------------------------------------------------------------------------- #
# Gestionnaire
# --------------------------------------------------------------------------- #
def test_the_console_is_the_default_arrangement():
    manager = LayoutManager({})
    assert manager.effective_preset() == STANDARD
    assert manager.plan(POTATO).order == tuple(range(8))


def test_a_stored_layout_choice_from_an_older_version_is_ignored():
    manager = LayoutManager({"layout": {"preset": "streaming", "follow_software": True, "show_unassigned": True}},
                            mode=MODE_STANDALONE)
    assert manager.effective_preset() == STANDARD
    assert manager.plan(BANANA).hidden == ()


def test_music_mode_gives_the_studio_arrangement_only_with_a_music_software():
    manager = LayoutManager({})
    manager.active = active("fl_studio")
    assert manager.effective_preset() == STANDARD                     # mode musical éteint
    manager.music_mode = True
    assert manager.effective_preset() == FL_STUDIO
    manager.music_mode = False                                        # retour à la console, rien n'a été écrasé
    assert manager.effective_preset() == STANDARD

    for key in ("discord", "zoom", "spotify", "obs", "audacity"):     # pas de partie musicale
        manager.active = active(key)
        manager.music_mode = True
        assert manager.effective_preset() == STANDARD, key
    manager.active = ad.NO_APP
    assert manager.effective_preset() == STANDARD


def test_plan_uses_the_effective_preset_and_the_user_roles():
    cfg: dict = {}
    manager = LayoutManager(cfg, mode=MODE_VOICEMEETER, music_mode=True, active=active("fl_studio"))
    plan = manager.plan(POTATO)
    assert plan.preset == FL_STUDIO and plan.order == (5, 6, 7, 1, 0)

    manager.set_role(FL_STUDIO, lm.ROLE_MIC, 2)
    manager.set_role(FL_STUDIO, lm.ROLE_FX, 3)
    plan = manager.plan(POTATO)
    assert plan.roles[lm.ROLE_MIC] == 2 and plan.roles[lm.ROLE_FX] == 3
    assert cfg["layout"]["roles"] == {FL_STUDIO: {lm.ROLE_MIC: 2, lm.ROLE_FX: 3}}
    manager.set_role(FL_STUDIO, lm.ROLE_MIC, None)                    # retour à l'automatique
    assert manager.plan(POTATO).roles[lm.ROLE_MIC] == 0


def test_roles_are_stored_per_preset_and_garbage_is_ignored():
    manager = LayoutManager({"layout": {"roles": {
        FL_STUDIO: {"mic": "2", "fx": "x", "synths": None}, STANDARD: {"fx": 6}, "streaming": "?"}}},
        music_mode=True, active=active("fl_studio"))
    assert manager.explicit_roles() == {"mic": 2}
    assert manager.explicit_roles(STANDARD) == {"fx": 6}
    assert manager.explicit_roles("streaming") == {}
    assert manager.explicit_roles("inconnu") == {}


def test_plan_in_standalone_mode_has_no_routing():
    manager = LayoutManager({}, mode=MODE_STANDALONE)
    plan = manager.plan(BANANA, EngineCaps(routing=False))
    assert all(not s.style.routing for s in plan.strips)


def test_theme_accessors():
    cfg: dict = {}
    manager = LayoutManager(cfg)
    assert manager.theme == "" and manager.global_theme is None
    manager.set_theme("flat")
    assert cfg["ui"]["theme"] == "flat" and manager.global_theme.key == "flat"
    manager.set_theme("#00aaff")                                      # thème d'accent
    assert manager.global_theme is None and manager.theme == "#00aaff"


def test_module_has_no_qt_dependency():
    source = open(lm.__file__, encoding="utf-8").read()
    assert "PySide6" not in source and "PyQt" not in source

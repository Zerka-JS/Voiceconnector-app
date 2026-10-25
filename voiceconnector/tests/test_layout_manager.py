"""Dispositions et plan d'arrangement des canaux (logique pure, sans Qt)."""

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core.layout_manager import (APP_SESSION_CAPS, AUTO, FL_STUDIO, FL_TRACK_CAPS, FULL_CAPS,
                                 GROUP_APPS, GROUP_HARDWARE, GROUP_INPUTS, GROUP_OTHERS,
                                 GROUP_VIRTUAL, LAYOUT_PRESETS, MODE_STANDALONE, MODE_VOICEMEETER,
                                 PODCAST, PRESET_ORDER, STANDARD, STREAMING, EngineCaps,
                                 LayoutManager, StripInfo, StripStyle, assign_roles, build_plan,
                                 style_for)


def console(hardware: int, virtual: int) -> list[StripInfo]:
    """Canaux d'une console : d'abord les entrées matérielles, puis les virtuelles."""
    return [StripInfo(i, physical=i < hardware) for i in range(hardware + virtual)]


BANANA = console(3, 2)
POTATO = console(5, 3)


def active(key: str, running: bool = True) -> ad.ActiveApp:
    return ad.ActiveApp(ad.known_app(key), ad.SOURCE_DETECTED, running)


# --------------------------------------------------------------------------- #
# Catalogue des dispositions
# --------------------------------------------------------------------------- #
def test_presets_are_the_requested_ones():
    assert PRESET_ORDER == (STANDARD, FL_STUDIO, STREAMING, PODCAST)
    assert set(LAYOUT_PRESETS) == set(PRESET_ORDER)
    labels = lambda key: [slot.label for slot in LAYOUT_PRESETS[key].slots]       # noqa: E731
    assert labels(FL_STUDIO)[:4] == ["Master", "Bus Batterie", "Synths", "FX"]
    assert labels(STREAMING) == ["Micro", "Game", "Discord / Vocal", "Music"]
    assert "FL Studio" in LAYOUT_PRESETS[FL_STUDIO].name
    assert "Streaming" in LAYOUT_PRESETS[STREAMING].name
    assert "Podcasting" in LAYOUT_PRESETS[PODCAST].name


@pytest.mark.parametrize("key", PRESET_ORDER)
def test_preset_is_consistent(key):
    preset = LAYOUT_PRESETS[key]
    assert preset.key == key and preset.name and preset.description
    roles = [slot.role for slot in preset.slots]
    assert len(roles) == len(set(roles)), "un rôle ne peut apparaître qu'une fois"
    grouped = [role for group in preset.groups for role in group.roles]
    assert len(grouped) == len(set(grouped)), "un rôle ne peut être que dans un groupe"
    assert set(grouped) <= set(roles), "un groupe ne peut citer qu'un rôle existant"
    assert set(roles) <= set(grouped), "chaque emplacement doit être dans un groupe"
    assert preset.master_side in ("left", "right")
    for slot in preset.slots:
        assert slot.kind in (lm.KIND_ANY, lm.KIND_PHYSICAL, lm.KIND_VIRTUAL)


def test_studio_layout_has_wide_pan_precise_vu_and_master_on_the_left():
    preset = LAYOUT_PRESETS[FL_STUDIO]
    assert preset.style.pan == "wide" and preset.style.vu == "precision"
    assert preset.master_side == "left"
    assert LAYOUT_PRESETS[STANDARD].style == StripStyle()             # le reste inchangé
    assert LAYOUT_PRESETS[STANDARD].master_side == "right"


def test_podcast_layout_integrates_dynamics_and_quick_eq():
    style = LAYOUT_PRESETS[PODCAST].style
    assert style.dynamics and style.eq and style.pan == "none"      # l'égaliseur remplace le pad
    assert not LAYOUT_PRESETS[STREAMING].style.dynamics
    assert not LAYOUT_PRESETS[STANDARD].style.eq


def test_style_rejects_unknown_values():
    with pytest.raises(ValueError):
        StripStyle(pan="gros")
    with pytest.raises(ValueError):
        StripStyle(vu="enorme")


def test_layout_choices_start_with_auto():
    choices = lm.layout_choices()
    assert choices[0][0] == AUTO
    assert [key for key, _ in choices[1:]] == list(PRESET_ORDER)
    assert all(label for _, label in choices)


# --------------------------------------------------------------------------- #
# Affectation des rôles
# --------------------------------------------------------------------------- #
def test_roles_follow_the_preferred_channel_type():
    slots = LAYOUT_PRESETS[STREAMING].slots
    roles = assign_roles(slots, POTATO)
    assert roles[lm.ROLE_MIC] == 0                                    # 1re entrée matérielle
    assert (roles[lm.ROLE_GAME], roles[lm.ROLE_DISCORD], roles[lm.ROLE_MUSIC]) == (5, 6, 7)


def test_roles_fall_back_to_any_free_channel():
    # Banana : 2 virtuels seulement pour 3 emplacements virtuels (Game, Discord, Music)
    roles = assign_roles(LAYOUT_PRESETS[STREAMING].slots, BANANA)
    assert roles[lm.ROLE_MIC] == 0
    assert roles[lm.ROLE_GAME] == 3 and roles[lm.ROLE_DISCORD] == 4
    assert roles[lm.ROLE_MUSIC] == 1                                  # repli : 1er canal libre
    assert len(set(roles.values())) == len(roles)


def test_more_slots_than_channels_leaves_some_roles_unassigned():
    roles = assign_roles(LAYOUT_PRESETS[FL_STUDIO].slots, console(1, 1))
    assert len(roles) == 2 and len(set(roles.values())) == 2


def test_explicit_roles_win_and_are_validated():
    slots = LAYOUT_PRESETS[STREAMING].slots
    roles = assign_roles(slots, POTATO, {lm.ROLE_MIC: 1, lm.ROLE_MUSIC: 0})
    assert roles[lm.ROLE_MIC] == 1 and roles[lm.ROLE_MUSIC] == 0
    assert len(set(roles.values())) == len(roles)
    # canal inexistant, ou déjà pris par un autre emplacement : l'automatique reprend
    roles = assign_roles(slots, POTATO, {lm.ROLE_MIC: 99})
    assert roles[lm.ROLE_MIC] == 0
    roles = assign_roles(slots, POTATO, {lm.ROLE_MIC: 4, lm.ROLE_GAME: 4})
    assert roles[lm.ROLE_MIC] == 4 and roles[lm.ROLE_GAME] != 4


def test_declared_roles_from_a_profile_beat_the_automatic_choice():
    strips = [StripInfo(0, True, "Pod mic", role=lm.ROLE_MUSIC), *BANANA[1:]]
    roles = assign_roles(LAYOUT_PRESETS[STREAMING].slots, strips)
    assert roles[lm.ROLE_MUSIC] == 0
    # le premier qui déclare un rôle l'obtient
    twice = [StripInfo(0, True, role=lm.ROLE_MIC), StripInfo(1, True, role=lm.ROLE_MIC), *BANANA[2:]]
    assert assign_roles(LAYOUT_PRESETS[STREAMING].slots, twice)[lm.ROLE_MIC] == 0


def test_fixed_strips_are_never_assigned_automatically():
    """Profil du mode autonome : un canal sans rôle ne prend pas l'emplacement d'un autre."""
    strips = [StripInfo(0, False, "Master", role=lm.ROLE_MASTER, fixed=True),
              StripInfo(1, False, "Ableton", fixed=True),
              StripInfo(2, True, "Micro", role=lm.ROLE_MIC, fixed=True)]
    roles = assign_roles(LAYOUT_PRESETS[FL_STUDIO].slots, strips)
    assert roles == {lm.ROLE_MASTER: 0, lm.ROLE_MIC: 2}              # rien pour Ableton
    plan = build_plan(LAYOUT_PRESETS[FL_STUDIO], strips, show_unassigned=True)
    assert [g.title for g in plan.groups] == ["MASTER", "ENTRÉE", GROUP_OTHERS]
    assert plan.groups[-1].strips == (1,) and plan.strip(1).role is None and plan.strip(1).visible
    # un choix manuel de l'utilisateur reste possible, même sur un canal fixe
    roles = assign_roles(LAYOUT_PRESETS[FL_STUDIO].slots, strips, {lm.ROLE_SYNTHS: 1})
    assert roles[lm.ROLE_SYNTHS] == 1
    # sans « fixed », le canal libre aurait pris le premier emplacement vide
    loose = [StripInfo(i.index, i.physical, i.name, i.role) for i in strips]
    assert 1 in assign_roles(LAYOUT_PRESETS[FL_STUDIO].slots, loose).values()


def test_unassigned_channels_are_shown_by_default_in_standalone_mode_only():
    assert not LayoutManager({}, mode=MODE_VOICEMEETER).show_unassigned
    assert LayoutManager({}, mode=MODE_STANDALONE).show_unassigned
    assert not LayoutManager({"layout": {"show_unassigned": False}}, mode=MODE_STANDALONE).show_unassigned
    assert LayoutManager({"layout": {"show_unassigned": True}}, mode=MODE_VOICEMEETER).show_unassigned
    manager = LayoutManager({}, mode=MODE_STANDALONE)
    manager.select(STREAMING)
    plan = manager.plan([StripInfo(0, True, role=lm.ROLE_MIC, fixed=True),
                         StripInfo(1, False, "Chrome", fixed=True)])
    assert plan.hidden == () and plan.groups[-1].title == GROUP_OTHERS


def test_all_roles_cover_the_slots_of_every_preset():
    for preset in LAYOUT_PRESETS.values():
        assert {slot.role for slot in preset.slots} <= lm.ALL_ROLES


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
def test_standard_plan_is_the_original_hardware_then_virtual_layout():
    plan = build_plan(LAYOUT_PRESETS[STANDARD], POTATO)
    assert [(g.title, g.strips) for g in plan.groups] == [
        (GROUP_HARDWARE, (0, 1, 2, 3, 4)), (GROUP_VIRTUAL, (5, 6, 7))]
    assert plan.order == tuple(range(8)) and plan.hidden == ()
    assert plan.master_side == "right" and plan.show_matrix and plan.show_outputs
    assert all(s.role is None and s.label is None and s.visible for s in plan.strips)


def test_standard_plan_without_virtual_channels_has_no_empty_group():
    plan = build_plan(LAYOUT_PRESETS[STANDARD], console(2, 0))
    assert [g.title for g in plan.groups] == [GROUP_HARDWARE]


def test_streaming_plan_orders_mic_game_discord_music_and_hides_the_rest():
    plan = build_plan(LAYOUT_PRESETS[STREAMING], POTATO)
    assert plan.order == (0, 5, 6, 7)                                 # Micro, Game, Discord, Music
    assert plan.hidden == (1, 2, 3, 4)
    assert [plan.strip(i).label for i in plan.order] == ["Micro", "Game", "Discord / Vocal", "Music"]
    assert plan.roles == {lm.ROLE_MIC: 0, lm.ROLE_GAME: 5, lm.ROLE_DISCORD: 6, lm.ROLE_MUSIC: 7}
    assert not plan.strip(1).visible and plan.strip(1).role is None


def test_unassigned_channels_can_be_kept_in_an_others_group():
    plan = build_plan(LAYOUT_PRESETS[STREAMING], POTATO, show_unassigned=True)
    assert plan.groups[-1].title == GROUP_OTHERS and plan.groups[-1].strips == (1, 2, 3, 4)
    assert plan.hidden == () and plan.order == (0, 5, 6, 7, 1, 2, 3, 4)


def test_studio_plan_puts_master_first_with_wide_pan_and_precise_vu():
    plan = build_plan(LAYOUT_PRESETS[FL_STUDIO], POTATO)
    assert [g.title for g in plan.groups] == ["MASTER", "BUS", "ENTRÉE"]
    assert plan.strip(plan.order[0]).label == "Master"
    assert plan.master_side == "left" and not plan.show_matrix
    assert {plan.strip(i).style.pan for i in plan.order} == {"wide"}
    assert {plan.strip(i).style.vu for i in plan.order} == {"precision"}
    labels = [plan.strip(i).label for i in plan.order]
    assert labels[:4] == ["Master", "Bus Batterie", "Synths", "FX"]


def test_podcast_plan_gives_every_visible_strip_dynamics_and_eq():
    plan = build_plan(LAYOUT_PRESETS[PODCAST], BANANA)
    assert len(plan.order) == 3
    for index in plan.order:
        style = plan.strip(index).style
        assert style.dynamics and style.eq
    assert plan.strip(plan.order[0]).label == "Voix"


def test_plan_without_routing_hides_matrix_and_quick_routing():
    """Mode B : pas de Voicemeeter, donc rien à router."""
    no_routing = EngineCaps(routing=False)
    plan = build_plan(LAYOUT_PRESETS[STANDARD], BANANA, caps=no_routing)
    assert not plan.show_matrix and all(not s.style.routing for s in plan.strips)
    plan = build_plan(LAYOUT_PRESETS[PODCAST], BANANA, caps=no_routing)
    assert not plan.show_matrix
    assert all(plan.strip(i).style.dynamics and not plan.strip(i).style.routing for i in plan.order)


def test_engine_caps_restrict_what_a_strip_shows():
    studio = LAYOUT_PRESETS[FL_STUDIO].style
    assert style_for(studio, FULL_CAPS) == studio
    no_pan = style_for(studio, EngineCaps(pan=False))
    assert no_pan.pan == "none" and no_pan.vu == "precision"          # le reste est conservé
    podcast = style_for(LAYOUT_PRESETS[PODCAST].style, APP_SESSION_CAPS)
    assert not (podcast.dynamics or podcast.eq or podcast.trim or podcast.fx or podcast.routing)
    assert podcast.pan == "none"
    # sessions Windows : ni routage, ni trim, ni panoramique ; pistes de FL Studio : panoramique oui
    assert style_for(StripStyle(), APP_SESSION_CAPS).pan == "none"
    assert style_for(StripStyle(), FL_TRACK_CAPS).pan == "pad"
    assert not style_for(StripStyle(), FL_TRACK_CAPS).routing


def test_caps_combine_with_and():
    both = APP_SESSION_CAPS & FL_TRACK_CAPS
    assert both == EngineCaps(routing=False, pan=False, trim=False, eq=False, fx=False)
    assert FULL_CAPS & FL_TRACK_CAPS == FL_TRACK_CAPS
    assert FULL_CAPS & FULL_CAPS == FULL_CAPS


def test_standalone_standard_plan_uses_input_and_application_titles():
    manager = LayoutManager({}, mode=MODE_STANDALONE)
    manager.select(STANDARD)
    plan = manager.plan(BANANA, APP_SESSION_CAPS)
    assert [g.title for g in plan.groups] == [GROUP_INPUTS, GROUP_APPS]
    plan = LayoutManager({}, mode=MODE_VOICEMEETER).plan(BANANA)
    assert [g.title for g in plan.groups] == [GROUP_HARDWARE, GROUP_VIRTUAL]


def test_plan_lookup_of_a_missing_channel_raises():
    plan = build_plan(LAYOUT_PRESETS[STANDARD], BANANA)
    with pytest.raises(KeyError):
        plan.strip(42)


@pytest.mark.parametrize("key", PRESET_ORDER)
@pytest.mark.parametrize("strips", [console(0, 0), console(1, 0), BANANA, POTATO])
def test_every_channel_is_shown_at_most_once(key, strips):
    plan = build_plan(LAYOUT_PRESETS[key], strips, show_unassigned=True)
    assert len(plan.order) == len(set(plan.order))
    assert set(plan.order) | set(plan.hidden) == {s.index for s in strips}
    assert not set(plan.order) & set(plan.hidden)
    assert [s.index for s in plan.strips] == [s.index for s in strips]


# --------------------------------------------------------------------------- #
# Gestionnaire
# --------------------------------------------------------------------------- #
def test_default_choice_is_the_standard_layout():
    manager = LayoutManager({})
    assert manager.stored_preset == STANDARD and manager.effective_preset() == STANDARD
    assert manager.selector_value == STANDARD and not manager.follow_software


def test_unknown_stored_preset_falls_back_to_standard():
    assert LayoutManager({"layout": {"preset": "inconnu"}}).stored_preset == STANDARD
    assert LayoutManager({"layout": {"preset": None}}).stored_preset == STANDARD


def test_selecting_a_preset_stops_following_the_software():
    cfg: dict = {}
    manager = LayoutManager(cfg, mode=MODE_STANDALONE)
    assert manager.follow_software                                    # mode B : suivi par défaut
    assert manager.select(STREAMING) == STREAMING
    assert not manager.follow_software and manager.selector_value == STREAMING
    assert cfg["layout"] == {"preset": STREAMING, "follow_software": False}
    assert manager.select(AUTO) == AUTO
    assert manager.follow_software and manager.selector_value == AUTO
    assert manager.stored_preset == STREAMING                         # le choix est conservé
    assert manager.select("n'importe quoi") == AUTO                   # inchangé, ignoré
    assert manager.stored_preset == STREAMING


def test_follow_software_default_depends_on_the_mode():
    assert not LayoutManager({}, mode=MODE_VOICEMEETER).follow_software
    assert LayoutManager({}, mode=MODE_STANDALONE).follow_software
    # le réglage explicite l'emporte dans les deux sens
    assert LayoutManager({"layout": {"follow_software": True}}, mode=MODE_VOICEMEETER).follow_software
    assert not LayoutManager({"layout": {"follow_software": False}}, mode=MODE_STANDALONE).follow_software


def test_layout_follows_the_active_software():
    manager = LayoutManager({}, mode=MODE_STANDALONE)
    assert manager.effective_preset() == STANDARD                     # rien de détecté
    manager.active = active("fl_studio")
    assert manager.effective_preset() == FL_STUDIO
    manager.active = active("obs")
    assert manager.effective_preset() == STREAMING
    manager.active = active("audacity")
    assert manager.effective_preset() == PODCAST
    manager.select(STANDARD)                                          # choix explicite : on ne suit plus
    manager.active = active("fl_studio")
    assert manager.effective_preset() == STANDARD


def test_music_mode_forces_the_studio_layout_only_with_a_music_software():
    manager = LayoutManager({"layout": {"preset": STREAMING}})
    manager.active = active("fl_studio")
    assert manager.effective_preset() == STREAMING
    manager.music_mode = True
    assert manager.effective_preset() == FL_STUDIO
    manager.music_mode = False                                        # retour à l'ancienne disposition
    assert manager.effective_preset() == STREAMING
    assert manager.stored_preset == STREAMING                         # rien n'a été écrasé

    for key in ("discord", "zoom", "spotify", "obs", "audacity"):     # pas de partie musicale
        manager.active = active(key)
        manager.music_mode = True
        assert manager.effective_preset() != FL_STUDIO, key
    manager.active = ad.NO_APP
    assert manager.effective_preset() == STREAMING


def test_plan_uses_the_effective_preset_and_the_user_roles():
    cfg: dict = {}
    manager = LayoutManager(cfg)
    manager.select(STREAMING)
    plan = manager.plan(POTATO)
    assert plan.preset == STREAMING and plan.order == (0, 5, 6, 7)

    manager.set_role(STREAMING, lm.ROLE_MIC, 1)
    manager.set_role(STREAMING, lm.ROLE_MUSIC, 3)
    plan = manager.plan(POTATO)
    assert plan.roles[lm.ROLE_MIC] == 1 and plan.roles[lm.ROLE_MUSIC] == 3
    assert cfg["layout"]["roles"] == {STREAMING: {lm.ROLE_MIC: 1, lm.ROLE_MUSIC: 3}}
    manager.set_role(STREAMING, lm.ROLE_MIC, None)                    # retour à l'automatique
    assert manager.plan(POTATO).roles[lm.ROLE_MIC] == 0

    manager.set_show_unassigned(True)
    assert manager.show_unassigned and manager.plan(POTATO).hidden == ()


def test_roles_are_stored_per_preset_and_garbage_is_ignored():
    manager = LayoutManager({"layout": {"roles": {
        STREAMING: {"mic": "2", "game": "x", "music": None}, FL_STUDIO: {"fx": 6}, PODCAST: "?"}}})
    manager.select(STREAMING)
    assert manager.explicit_roles() == {"mic": 2}
    assert manager.explicit_roles(FL_STUDIO) == {"fx": 6}
    assert manager.explicit_roles(PODCAST) == {}
    assert manager.explicit_roles(STANDARD) == {}


def test_plan_in_standalone_mode_has_no_routing():
    manager = LayoutManager({}, mode=MODE_STANDALONE)
    plan = manager.plan(BANANA, EngineCaps(routing=False))
    assert not plan.show_matrix and all(not s.style.routing for s in plan.strips)


def test_theme_accessors():
    cfg: dict = {}
    manager = LayoutManager(cfg)
    assert manager.theme == "" and manager.global_theme is None
    manager.set_theme("flat")
    assert cfg["ui"]["theme"] == "flat" and manager.global_theme.key == "flat"
    manager.set_theme("#00aaff")                                      # thème d'accent hérité
    assert manager.global_theme is None and manager.theme == "#00aaff"


def test_module_has_no_qt_dependency():
    source = open(lm.__file__, encoding="utf-8").read()
    assert "PySide6" not in source and "PyQt" not in source

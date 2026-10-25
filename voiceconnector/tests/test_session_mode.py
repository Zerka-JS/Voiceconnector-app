"""Modes A / B, mode musical et libération des traitements voix (logique pure, sans Qt)."""

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core import session_mode as sm
from core.layout_manager import FL_STUDIO, STREAMING, LayoutManager, StripInfo
from core.session_mode import (MusicModeController, SessionSettings, direct_return_help,
                               music_release)


def console(hardware: int, virtual: int) -> list[StripInfo]:
    return [StripInfo(i, physical=i < hardware) for i in range(hardware + virtual)]


BANANA, POTATO = console(3, 2), console(5, 3)


def active(key: str, running: bool = True) -> ad.ActiveApp:
    return ad.ActiveApp(ad.known_app(key), ad.SOURCE_DETECTED, running)


def make(cfg=None):
    cfg = {} if cfg is None else cfg
    session = SessionSettings(cfg)
    layouts = LayoutManager(cfg, mode=session.mode)
    return cfg, session, layouts, MusicModeController(session, layouts)


# --------------------------------------------------------------------------- #
# Réglages de session
# --------------------------------------------------------------------------- #
def test_defaults():
    session = SessionSettings({})
    assert session.mode == lm.MODE_VOICEMEETER and not session.standalone
    assert session.app == ad.AUTO and session.voice_app == ad.AUTO
    assert session.profile == "" and not session.music_mode
    assert session.detect_interval_s == sm.DEFAULT_DETECT_S
    assert session.transport("discord") == sm.TRANSPORT_BUS


def test_modes_are_labelled_and_described():
    assert set(sm.MODES) == {lm.MODE_VOICEMEETER, lm.MODE_STANDALONE}
    for mode in sm.MODES:
        assert sm.MODE_LABELS[mode] and sm.MODE_DESCRIPTIONS[mode]
    assert "Voicemeeter" in sm.MODE_LABELS[lm.MODE_VOICEMEETER]
    assert "sans Voicemeeter" in sm.MODE_LABELS[lm.MODE_STANDALONE]


def test_mode_is_validated():
    cfg = {"session": {"mode": "n'importe quoi"}}
    session = SessionSettings(cfg)
    assert session.mode == lm.MODE_VOICEMEETER
    assert session.set_mode(lm.MODE_STANDALONE) == lm.MODE_STANDALONE and session.standalone
    assert cfg["session"]["mode"] == "standalone"
    assert session.set_mode("autre") == lm.MODE_STANDALONE          # refusé, l'ancien est gardé
    assert SessionSettings({"session": {"mode": "STANDALONE"}}).mode == lm.MODE_STANDALONE


def test_followed_application_must_be_in_the_catalog():
    cfg = {}
    session = SessionSettings(cfg)
    assert session.set_app("FL Studio") == ad.AUTO                  # clé, pas le nom affiché
    assert session.set_app("fl_studio") == "fl_studio" and cfg["session"]["app"] == "fl_studio"
    assert session.set_app(None) == ad.AUTO
    assert session.set_app("logiciel-inconnu") == ad.AUTO
    assert SessionSettings({"session": {"app": "OBS"}}).app == "obs"   # la casse est ignorée
    assert session.set_voice_app("discord") == "discord" and session.voice_app == "discord"
    assert session.set_voice_app("zzz") == ad.AUTO


def test_voice_transport_is_per_application_and_validated():
    session = SessionSettings({})
    assert session.set_transport("obs", sm.TRANSPORT_VBAN) == sm.TRANSPORT_VBAN
    assert session.transport("obs") == "vban" and session.transport("discord") == "bus"
    assert session.set_transport("obs", "wifi") == "vban"           # refusé
    assert SessionSettings({"session": {"voice_transport": "x"}}).transport("obs") == "bus"


def test_profile_and_music_memory_and_interval():
    cfg = {}
    session = SessionSettings(cfg)
    session.set_profile("C:/profils/studio.json")
    assert session.profile == "C:/profils/studio.json"
    session.set_profile(None)
    assert session.profile == ""
    session.remember_music_mode(True)
    assert session.music_mode and cfg["session"]["music_mode"] is True
    for raw, expected in ((0.1, sm.MIN_DETECT_S), (999, sm.MAX_DETECT_S), ("5", 5.0),
                          ("abc", sm.DEFAULT_DETECT_S), (None, sm.DEFAULT_DETECT_S)):
        assert SessionSettings({"session": {"detect_interval_s": raw}}).detect_interval_s == expected


# --------------------------------------------------------------------------- #
# Mode musical
# --------------------------------------------------------------------------- #
def test_music_mode_turns_on_with_a_music_software():
    cfg, session, layouts, music = make()
    transition = music.request(True, active("fl_studio"))
    assert transition.on and transition.changed and transition.reason == "toggle"
    assert "FL Studio" in transition.message
    assert music.on and layouts.music_mode and session.music_mode
    assert layouts.effective_preset() == FL_STUDIO


def test_music_mode_is_refused_without_a_music_software():
    cfg, session, layouts, music = make({"layout": {"preset": STREAMING}})
    for key in ("discord", "zoom", "spotify", "obs", "audacity", "teamspeak3"):
        transition = music.request(True, active(key))
        assert not transition.on and transition.reason == "unavailable", key
        assert "FL Studio" in transition.message or "production" in transition.message
        assert not music.on and not layouts.music_mode and not session.music_mode
    assert not music.request(True, ad.NO_APP).on
    assert layouts.effective_preset() == STREAMING


@pytest.mark.parametrize("key", [app.key for app in ad.KNOWN_APPS])
def test_music_mode_rule_is_strict_for_every_known_application(key):
    _, _, _, music = make()
    app = ad.known_app(key)
    assert music.request(True, active(key)).on == app.has_music_production
    assert music.available(active(key)) == app.has_music_production


def test_turning_the_mode_off_restores_the_previous_layout():
    cfg, session, layouts, music = make({"layout": {"preset": STREAMING}})
    music.request(True, active("fl_studio"))
    transition = music.request(False, active("fl_studio"))
    assert not transition.on and transition.changed and "rétablis" in transition.message
    assert layouts.effective_preset() == STREAMING and not session.music_mode
    assert not music.request(False, active("fl_studio")).changed     # déjà éteint


def test_requesting_the_same_state_does_not_report_a_change():
    _, _, _, music = make()
    music.request(True, active("ableton"))
    again = music.request(True, active("ableton"))
    assert again.on and not again.changed and again.message == ""


def test_music_mode_exits_when_the_music_software_is_replaced_or_lost():
    cfg, session, layouts, music = make()
    music.request(True, active("fl_studio"))
    assert music.app_changed(active("reaper")) == sm.MusicTransition(True, False)   # autre logiciel musical
    assert music.on
    transition = music.app_changed(active("discord"))
    assert not transition.on and transition.changed and transition.reason == "app_lost"
    assert "Discord" in transition.message
    assert not layouts.music_mode and not session.music_mode
    assert not music.app_changed(active("discord")).changed

    music.request(True, active("fl_studio"))
    transition = music.app_changed(ad.NO_APP)
    assert not transition.on and "n'est plus détecté" in transition.message


def test_application_changes_are_passed_on_to_the_layout_manager():
    _, _, layouts, music = make()
    music.app_changed(active("obs"))
    assert layouts.active.key == "obs"


def test_restore_at_startup_needs_the_music_software():
    cfg = {"session": {"music_mode": True}}
    _, session, layouts, music = make(cfg)
    transition = music.restore(active("fl_studio"))
    assert transition.on and transition.reason == "restore" and music.on and layouts.music_mode

    cfg = {"session": {"music_mode": True}}
    _, session, layouts, music = make(cfg)
    assert not music.restore(active("discord")).on
    assert not music.on and not session.music_mode                   # le souhait est oublié

    _, _, _, music = make({})
    assert not music.restore(active("fl_studio")).on


def test_music_button_follows_the_strict_visibility_rule():
    _, _, _, music = make()
    assert not music.button(active("discord")).visible
    assert not music.button(ad.NO_APP).visible
    button = music.button(active("fl_studio"))
    assert button.visible and button.enabled and not button.checked
    assert "FL Studio" in button.tooltip
    music.request(True, active("fl_studio"))
    assert music.button(active("fl_studio"), sync_connected=True).checked
    assert "connectée" in music.button(active("fl_studio"), sync_connected=True).tooltip
    # le logiciel disparaît : le bouton aussi, et il n'est jamais coché
    music.app_changed(active("zoom"))
    hidden = music.button(active("zoom"))
    assert not hidden.visible and not hidden.checked


# --------------------------------------------------------------------------- #
# Libération des traitements voix
# --------------------------------------------------------------------------- #
def studio_plan(strips):
    layouts = LayoutManager({})
    layouts.music_mode = True
    layouts.active = active("fl_studio")
    return layouts.plan(strips)


def test_vocal_and_hidden_strips_are_released_in_music_mode():
    plan = studio_plan(POTATO)
    assert plan.roles[lm.ROLE_MIC] == 0
    release = music_release(plan, host_kind="reaper", active=active("fl_studio"))
    assert not release.host_conflict
    assert 0 in release                                              # le micro
    assert set(plan.hidden) <= set(release.strips)                   # canaux que le studio ne montre pas
    for role in (lm.ROLE_MASTER, lm.ROLE_DRUMS, lm.ROLE_SYNTHS):
        assert plan.roles[role] not in release                       # les bus de la production restent


def test_banana_studio_layout_still_releases_the_microphone():
    plan = studio_plan(BANANA)
    assert plan.hidden == ()                                         # 5 emplacements pour 5 canaux
    release = music_release(plan, host_kind="none", active=active("fl_studio"))
    assert release.strips == {plan.roles[lm.ROLE_MIC]}


def test_fl_studio_as_effects_host_conflicts_with_production():
    plan = studio_plan(BANANA)
    release = music_release(plan, host_kind=sm.FLSTUDIO_HOST, active=active("fl_studio"))
    assert release.host_conflict and release.strips == {s.index for s in plan.strips}
    # un autre logiciel musical, ou un autre moteur d'effets : pas de conflit
    assert not music_release(plan, host_kind=sm.FLSTUDIO_HOST, active=active("reaper")).host_conflict
    assert not music_release(plan, host_kind="reaper", active=active("fl_studio")).host_conflict


def test_release_membership_and_empty_release():
    assert 3 not in sm.NO_RELEASE and not sm.NO_RELEASE.strips
    assert 2 in sm.MusicRelease(frozenset({2}))


def test_direct_return_help_explains_the_asio_setup():
    plan = studio_plan(POTATO)
    lines = direct_return_help(music_release(plan, host_kind="reaper", active=active("fl_studio")),
                               active("fl_studio"))
    assert "ASIO" in lines[0] and "FL Studio" in lines[0]
    assert "latence" in lines[1]
    conflict = music_release(plan, host_kind=sm.FLSTUDIO_HOST, active=active("fl_studio"))
    assert any("moteur d'effets" in line for line in direct_return_help(conflict, active("fl_studio")))
    one = sm.MusicRelease(frozenset({0}))
    assert "1 canal de voix passe" in direct_return_help(one, active("ableton"))[1]
    assert len(direct_return_help(sm.NO_RELEASE, ad.NO_APP)) == 1


def test_dynamics_stash_saves_zeroes_and_restores():
    stash = sm.DynamicsStash()
    sent = stash.release([0, 2], {0: (4.0, 2.5), 2: (1.0, 0.0), 3: (9.0, 9.0)})
    assert sent == {0: (0.0, 0.0), 2: (0.0, 0.0)}
    # un second appel ne perd pas les valeurs d'origine
    assert stash.release([0], {0: (0.0, 0.0)}) == {0: (0.0, 0.0)}
    assert stash.restore() == {0: (4.0, 2.5), 2: (1.0, 0.0)}
    assert stash.restore() == {}
    assert stash.release([5], {}) == {5: (0.0, 0.0)} and stash.restore() == {5: (0.0, 0.0)}


def test_dynamics_stash_follows_a_changing_release_set():
    """En mode musical, la disposition peut changer : les canaux qui ne sont plus libérés retrouvent
    leurs réglages, ceux qui le deviennent sont mis à zéro."""
    stash = sm.DynamicsStash()
    current = {0: (4.0, 2.0), 1: (1.0, 1.0), 2: (7.0, 3.0)}
    assert stash.sync({0, 1}, current) == {0: (0.0, 0.0), 1: (0.0, 0.0)}
    assert stash.sync({0, 1}, current) == {}                                # rien de nouveau : rien à envoyer
    assert stash.sync({1, 2}, current) == {0: (4.0, 2.0), 2: (0.0, 0.0)}     # 0 rétabli, 2 libéré
    assert stash.holds(1) and stash.holds(2) and not stash.holds(0)
    assert stash.sync(set(), current) == {1: (1.0, 1.0), 2: (7.0, 3.0)}
    assert stash.saved == {}


def test_a_knob_moved_on_a_released_strip_is_kept_for_later():
    stash = sm.DynamicsStash()
    stash.sync({3}, {3: (2.0, 5.0)})
    assert stash.remember(3, comp=6.5) is True                              # rien ne part au moteur
    assert stash.remember(3, gate=1.0) is True
    assert stash.remember(4, comp=9.0) is False                             # canal non libéré : à envoyer
    assert stash.restore() == {3: (6.5, 1.0)}


# --------------------------------------------------------------------------- #
# Cohérence avec le reste de l'application
# --------------------------------------------------------------------------- #
def test_host_constant_matches_the_effects_module():
    pytest.importorskip("PySide6")
    from core.effects import HOST_FLSTUDIO

    assert sm.FLSTUDIO_HOST == HOST_FLSTUDIO


def test_module_has_no_qt_dependency():
    source = open(sm.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

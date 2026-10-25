"""Coordinateur de session : logiciel suivi, mode musical, disposition et profil (logique pure)."""

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core import session_coordinator as sc
from core.layout_manager import FULL_CAPS, StripInfo
from core.profile_import import SOURCE_FL_TRACK
from core.session_coordinator import SessionCoordinator


def seen(*keys: str) -> list[ad.DetectedApp]:
    return [ad.DetectedApp(ad.known_app(key), (1000 + i,)) for i, key in enumerate(keys)]


class FakeConsole:
    """Ce que le coordinateur lit d'une console Voicemeeter (Potato : 5 matérielles, 3 virtuelles)."""

    caps = FULL_CAPS
    strip_count = 8

    def strip_infos(self):
        return [StripInfo(i, physical=i < 5) for i in range(self.strip_count)]


def coordinator(cfg=None, **kwargs):
    return SessionCoordinator({} if cfg is None else cfg, **kwargs)


# --------------------------------------------------------------------------- #
# Le bouton « Mode musical » : jamais pour une application seulement vocale
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["discord", "zoom", "spotify", "obs", "teamspeak3"])
def test_the_music_button_is_hidden_for_voice_and_broadcast_apps(key):
    session = coordinator()
    session.start(seen(key))
    assert session.active.key == key
    state = session.button()
    assert not state.visible and not state.enabled and not state.checked


@pytest.mark.parametrize("key", ["fl_studio", "ableton", "reaper", "cubase"])
def test_the_music_button_shows_for_music_production_software(key):
    session = coordinator()
    session.start(seen(key))
    state = session.button()
    assert state.visible and state.enabled and not state.checked


def test_no_software_no_button():
    session = coordinator()
    session.start([])
    assert not session.button().visible


def test_the_button_appears_and_disappears_with_the_detected_software():
    session = coordinator()
    session.start(seen("discord"))
    assert not session.button().visible
    change = session.on_detected(seen("discord", "fl_studio"))                 # FL Studio est lancé
    assert change.active_changed and session.button().visible
    change = session.on_detected(seen("discord"))                              # puis fermé
    assert change.active_changed and not session.button().visible


def test_a_voice_app_cannot_turn_the_music_mode_on():
    session = coordinator()
    session.start(seen("discord"))
    change = session.toggle_music(True)
    assert not session.music_on and change.music is not None and "n'est proposé" in change.music.message
    assert not change.preset_changed


def test_the_checked_state_follows_the_mode():
    session = coordinator()
    session.start(seen("fl_studio"))
    session.toggle_music()
    assert session.music_on and session.button().checked
    session.toggle_music()
    assert not session.music_on and not session.button().checked


# --------------------------------------------------------------------------- #
# Mode musical : disposition studio, sortie d'office, mémoire
# --------------------------------------------------------------------------- #
def test_the_music_mode_switches_to_the_studio_layout_and_back():
    session = coordinator({"layout": {"preset": lm.STREAMING, "follow_software": False}})
    session.start(seen("fl_studio"))
    assert session.preset == lm.STREAMING
    on = session.toggle_music(True)
    assert on.preset_changed and on.music is not None and on.music.changed and session.preset == lm.FL_STUDIO
    off = session.toggle_music(False)
    assert off.preset_changed and session.preset == lm.STREAMING                # retour à l'ancienne disposition


def test_the_music_mode_is_left_when_the_music_software_goes_away():
    session = coordinator()
    session.start(seen("fl_studio"))
    session.toggle_music(True)
    change = session.on_detected(seen("discord"))                              # FL Studio fermé, Discord reste
    assert not session.music_on and change.music is not None and change.music.reason == "app_lost"
    assert change.preset_changed and session.preset == lm.STANDARD
    assert not session.button().visible


def test_the_music_mode_stays_when_the_followed_software_is_chosen_by_hand():
    session = coordinator({"session": {"app": "fl_studio"}})
    session.start(seen("discord"))                                             # FL Studio n'est pas lancé
    assert session.active.key == "fl_studio" and not session.active.running
    assert session.button().visible and "n'est pas détecté" in session.button().tooltip
    session.toggle_music(True)
    change = session.on_detected(seen("discord", "fl_studio"))                 # puis il démarre
    assert session.music_on and change.music is None and session.active.running


def test_the_music_mode_is_restored_at_startup_only_if_the_software_is_there():
    cfg = {"session": {"music_mode": True}}
    session = coordinator(cfg)
    change = session.start(seen("fl_studio"))
    assert session.music_on and change.music is not None and change.music.reason == "restore"
    cfg = {"session": {"music_mode": True}}
    session = coordinator(cfg)
    session.start(seen("discord"))
    assert not session.music_on and cfg["session"]["music_mode"] is False


def test_choosing_the_followed_software_in_the_settings():
    session = coordinator()
    session.start(seen("discord"))
    change = session.select_app("ableton")
    assert change.active_changed and session.active.key == "ableton" and session.button().visible
    assert session.session.app == "ableton"
    change = session.select_app("auto")
    assert session.active.key == "discord" and not session.button().visible and change.active_changed


def test_nothing_changes_when_the_detection_is_the_same():
    session = coordinator()
    session.start(seen("fl_studio", "discord"))
    assert not session.on_detected(seen("fl_studio", "discord")).any
    assert not session.on_detected(seen("discord", "fl_studio")).any


# --------------------------------------------------------------------------- #
# Disposition
# --------------------------------------------------------------------------- #
def test_following_the_software_changes_the_layout_in_mode_a_when_asked():
    session = coordinator({"layout": {"follow_software": True}})
    session.start(seen("obs"))
    assert session.preset == lm.STREAMING
    change = session.on_detected(seen("fl_studio"))
    assert change.preset_changed and session.preset == lm.FL_STUDIO


def test_the_layout_is_not_touched_by_detection_when_not_following():
    session = coordinator()                                                      # mode A : pas de suivi par défaut
    session.start(seen("obs"))
    assert session.preset == lm.STANDARD
    assert not session.on_detected(seen("fl_studio")).preset_changed


def test_the_layout_choice_reports_a_change_only_when_the_layout_changes():
    session = coordinator()
    session.start([])
    assert session.set_layout(lm.PODCAST).preset_changed and session.preset == lm.PODCAST
    assert not session.set_layout(lm.PODCAST).preset_changed
    assert session.set_layout(lm.AUTO).preset_changed is False                   # aucun logiciel : reste podcast
    session.on_detected(seen("obs"))
    assert session.preset == lm.STREAMING                                        # « auto » suit maintenant OBS
    assert session.layouts.selector_value == lm.AUTO


def test_the_plan_names_the_strips_and_follows_the_music_mode():
    session = coordinator()
    session.start(seen("fl_studio"))
    names = [f"Canal {i}" for i in range(8)]
    plan = session.plan(FakeConsole(), names)
    assert plan.preset == lm.STANDARD and len(plan.groups) == 2 and plan.hidden == ()
    session.toggle_music(True)
    plan = session.plan(FakeConsole(), names)
    assert plan.preset == lm.FL_STUDIO and plan.master_side == "left" and not plan.show_matrix
    assert plan.hidden                                                           # les canaux sans emplacement sont libérés


def test_strip_infos_keep_the_names_of_the_profile_and_fill_the_others():
    class Mixed(FakeConsole):
        def strip_infos(self):
            return [StripInfo(0, True, "Profil"), StripInfo(1, True)]

    infos = sc.strip_infos(Mixed(), ["Interface", "Deuxième"])
    assert [i.name for i in infos] == ["Profil", "Deuxième"]


# --------------------------------------------------------------------------- #
# Libération des traitements voix
# --------------------------------------------------------------------------- #
def test_nothing_is_released_outside_the_music_mode():
    session = coordinator()
    session.start(seen("fl_studio"))
    plan = session.plan(FakeConsole())
    assert session.release(plan) is sc.NO_RELEASE or not session.release(plan).strips


def test_vocal_and_hidden_strips_are_released_in_the_music_mode():
    session = coordinator(host_kind="reaper")
    session.start(seen("fl_studio"))
    session.toggle_music(True)
    plan = session.plan(FakeConsole())
    release = session.release(plan)
    assert release.strips and not release.host_conflict
    assert set(plan.hidden) <= release.strips
    lines = session.help_lines(plan)
    assert "ASIO" in lines[0] and any("latence" in line for line in lines)


def test_fl_studio_cannot_be_both_the_effects_host_and_the_production_software():
    session = coordinator(host_kind="flstudio")
    session.start(seen("fl_studio"))
    session.toggle_music(True)
    plan = session.plan(FakeConsole())
    release = session.release(plan)
    assert release.host_conflict and release.strips == frozenset(range(8))


def test_mode_b_has_nothing_to_release():
    session = coordinator({"session": {"mode": "standalone"}})
    session.start(seen("fl_studio"))
    session.toggle_music(True)
    assert session.standalone and not session.release(session.plan(FakeConsole())).strips
    assert session.help_lines(session.plan(FakeConsole()))                      # mais la consigne ASIO reste utile


def test_fl_sync_is_wanted_only_for_music_mode_with_a_syncable_software():
    session = coordinator()
    session.start(seen("fl_studio"))
    assert not session.wants_fl_sync
    session.toggle_music(True)
    assert session.wants_fl_sync
    assert not coordinator(fl_sync_available=False).wants_fl_sync
    other = coordinator()
    other.start(seen("ableton"))                                                # pas de synchronisation prévue pour lui
    other.toggle_music(True)
    assert not other.wants_fl_sync


def test_the_button_tooltip_follows_the_synchronisation():
    session = coordinator()
    session.start(seen("fl_studio"))
    assert "en attente de FL Studio" in session.button().tooltip
    session.sync_connected = True
    assert "connectée" in session.button().tooltip


# --------------------------------------------------------------------------- #
# Mode B : le profil suit le logiciel
# --------------------------------------------------------------------------- #
def test_mode_b_gives_a_profile_at_startup_and_when_the_software_changes():
    session = coordinator({"session": {"mode": "standalone"}})
    start = session.start(seen("discord"))
    assert start.profile is not None and start.profile.software == "discord"
    assert session.profile == start.profile
    change = session.on_detected(seen("fl_studio"))
    assert change.profile is not None and any(
        c.source.type == SOURCE_FL_TRACK for c in change.profile.channels)
    assert session.on_detected(seen("fl_studio")).profile is None               # rien de neuf : pas de rechargement


def test_mode_a_has_no_profile():
    session = coordinator()
    assert session.start(seen("fl_studio")).profile is None and session.profile is None


def test_without_mido_fl_studio_is_an_ordinary_application_in_mode_b():
    session = coordinator({"session": {"mode": "standalone"}}, fl_sync_available=False)
    start = session.start(seen("fl_studio"))
    assert not any(c.source.type == SOURCE_FL_TRACK for c in start.profile.channels)


def test_overrides_made_in_the_console_are_kept_in_the_profile():
    cfg = {"session": {"mode": "standalone"}}
    session = coordinator(cfg)
    first = session.start(seen("discord")).profile
    session.session.set_override(first.name, 1, name="Mon micro")
    again = session.reload_profile().profile
    assert again.channels[1].name == "Mon micro" and first.channels[1].name != "Mon micro"


def test_an_unreadable_profile_file_falls_back_to_the_automatic_one_with_a_note():
    session = coordinator({"session": {"mode": "standalone", "profile": "/introuvable/profil.json"}})
    start = session.start(seen("discord"))
    assert start.profile is not None and "ignoré" in start.note


def test_voice_candidates_are_the_running_voice_apps():
    session = coordinator()
    session.start(seen("fl_studio", "discord", "obs", "spotify"))
    assert {d.key for d in session.voice_candidates()} == {"discord", "obs"}


def test_the_session_knows_whether_it_was_started():
    session = coordinator()
    assert not session.started
    session.start(seen("discord"))
    assert session.started


def test_module_has_no_qt_dependency():
    source = open(sc.__file__, encoding="utf-8").read()
    assert "PySide6" not in source

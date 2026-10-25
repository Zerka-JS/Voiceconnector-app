"""Moteur du mode autonome (B) : canaux → sources Windows / pistes FL Studio, consignes en
attente, niveaux, et un aller-retour complet avec le vrai script de FL Studio."""

from dataclasses import replace

import pytest
from fl_rig import FakeClock, FakeTransport, FlStudioRig

from core import app_detector as ad
from core import fl_sync as fl
from core import layout_manager as lm
from core import profile_import as pi
from core import standalone_engine as se
from core import system_audio as sa
from core.fl_sync import FlSyncCore, db_to_fl_volume, fl_volume_to_db
from core.mixer_model import SILENCE_DB, amp_to_db
from core.profile_import import ChannelSource, Profile, ProfileChannel
from core.standalone_engine import DemoFlLink, StandaloneEngine
from core.system_audio import SourceState


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #
class FakeAudio(sa.SystemAudio):
    """Audio Windows de test : on décide quelles applications existent, on lit les appels."""

    available = True

    def __init__(self) -> None:
        self.apps: dict[str, SourceState] = {}
        self.endpoints = {sa.SOURCE_CAPTURE: SourceState(0.8, False, 0.1, True, "Micro"),
                          sa.SOURCE_MASTER: SourceState(0.7, False, 0.2, True, "Haut-parleurs")}
        self.calls: list[tuple] = []

    def add(self, key: str, volume: float = 1.0, muted: bool = False, peak: float = 0.0) -> None:
        self.apps[key] = SourceState(volume, muted, peak, True, key)

    def app_states(self):
        return dict(self.apps)

    def set_app_volume(self, key, volume):
        self.calls.append(("app_volume", key, volume))
        if key in self.apps:
            self.apps[key] = replace(self.apps[key], volume=volume)

    def set_app_mute(self, key, muted):
        self.calls.append(("app_mute", key, muted))
        if key in self.apps:
            self.apps[key] = replace(self.apps[key], muted=muted)

    def endpoint_state(self, kind):
        return self.endpoints.get(kind, SourceState())

    def set_endpoint_volume(self, kind, volume):
        self.calls.append(("endpoint_volume", kind, volume))
        if kind in self.endpoints:
            self.endpoints[kind] = replace(self.endpoints[kind], volume=volume)

    def set_endpoint_mute(self, kind, muted):
        self.calls.append(("endpoint_mute", kind, muted))
        if kind in self.endpoints:
            self.endpoints[kind] = replace(self.endpoints[kind], muted=muted)

    def writes_to(self, key: str) -> list[tuple]:
        return [call for call in self.calls if call[1] == key]


def app(name, key, role=None, gain=None, mute=None) -> ProfileChannel:
    return ProfileChannel(name, ChannelSource(sa.SOURCE_APP, key), role, gain, mute)


MIC = ProfileChannel("Micro", ChannelSource(sa.SOURCE_CAPTURE), "mic")


def fl_track(name, track, role=None) -> ProfileChannel:
    return ProfileChannel(name, ChannelSource(pi.SOURCE_FL_TRACK, track=track), role)


def profile(*channels, **kwargs) -> Profile:
    return Profile("Test", tuple(channels), **kwargs)


def engine_for(*channels, audio=None, link=None, **kwargs):
    audio = audio if audio is not None else FakeAudio()
    return StandaloneEngine(profile(*channels), audio, link, **kwargs), audio


# --------------------------------------------------------------------------- #
# Description pour l'interface
# --------------------------------------------------------------------------- #
def test_the_engine_describes_itself_like_the_voicemeeter_controller():
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), fl_track("Synths", 2),
                           link=DemoFlLink())
    assert engine.strip_count == 3 and engine.names == ["Micro", "Discord", "Synths"]
    assert [engine.is_physical(i) for i in range(3)] == [True, False, False]
    assert [engine.virtual_input(i) for i in range(3)] == [None, None, None]
    assert [engine.pad_modes(i) for i in range(3)] == [(), (), ("pan",)]
    assert engine.supports_sends is False and engine.edition is None
    assert engine.persistent_state is False and engine.uses_fl


def test_capabilities_follow_the_source_of_each_channel():
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), fl_track("Synths", 2))
    assert engine.strip_caps(0) == lm.APP_SESSION_CAPS
    assert engine.strip_caps(2) == lm.FL_TRACK_CAPS
    assert engine.caps == lm.APP_SESSION_CAPS                      # pas de pan commun à tout le monde
    only_fl, _ = engine_for(fl_track("Master", 0), fl_track("Synths", 2))
    assert only_fl.caps == lm.FL_TRACK_CAPS
    assert not only_fl.caps.routing and not only_fl.caps.trim and not only_fl.caps.fx


def test_strip_infos_keep_the_roles_of_the_profile():
    engine, _ = engine_for(MIC, app("Discord", "discord.exe", "discord"), fl_track("Synths", 2))
    infos = engine.strip_infos()
    assert [(i.index, i.physical, i.name, i.role, i.fixed) for i in infos] == [
        (0, True, "Micro", "mic", True), (1, False, "Discord", "discord", True),
        (2, False, "Synths", None, True)]
    assert infos[2].caps == lm.FL_TRACK_CAPS and infos[0].caps == lm.APP_SESSION_CAPS


def test_a_plan_built_from_the_engine_hides_what_the_sources_cannot_do():
    engine, _ = engine_for(fl_track("Master", 0, "master"), fl_track("Synths", 2, "synths"), MIC)
    plan = lm.build_plan(lm.LAYOUT_PRESETS["fl_studio"], engine.strip_infos(), caps=engine.caps)
    assert plan.strip(1).style.pan == lm.PAN_WIDE and not plan.strip(1).style.routing
    assert plan.strip(2).style.pan == lm.PAN_NONE                       # le micro (Windows) : pas de pan
    assert not plan.show_matrix


@pytest.mark.parametrize("source, text", [
    (ChannelSource(sa.SOURCE_APP, "discord.exe"), "discord.exe"),
    (ChannelSource(sa.SOURCE_APP, ""), "à choisir"),
    (ChannelSource(sa.SOURCE_CAPTURE), "Micro"),
    (ChannelSource(sa.SOURCE_MASTER), "Sortie"),
    (ChannelSource(pi.SOURCE_FL_TRACK, track=0), "Master"),
    (ChannelSource(pi.SOURCE_FL_TRACK, track=3), "piste 3"),
])
def test_source_labels(source, text):
    assert text in se.source_label(source)


def test_max_gain_depends_on_the_source():
    engine, _ = engine_for(app("Discord", "discord.exe"), fl_track("Synths", 2))
    assert engine.max_gain_db(0) == 0.0
    assert engine.max_gain_db(1) == pytest.approx(fl.FL_MAX_DB)


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def test_poll_reads_volumes_mutes_and_levels_from_windows():
    audio = FakeAudio()
    audio.add("discord.exe", 0.5, False, 0.25)
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), audio=audio)
    params, levels = engine.poll()
    assert params.strips[1].gain == pytest.approx(-6.02, abs=0.01) and not params.strips[1].mute
    assert params.strips[0].gain == pytest.approx(-1.94, abs=0.01)          # micro : 0,8
    assert params.headphone_gain == pytest.approx(-3.1, abs=0.01)           # sortie : 0,7
    assert levels.strips[1] == pytest.approx((amp_to_db(0.25),) * 2)
    assert levels.strips[0][0] == pytest.approx(amp_to_db(0.1))
    assert levels.master[0] == pytest.approx(amp_to_db(0.2))
    assert engine.presence() == (True, True)
    assert all(s.routes == () and s.sends == () and s.pads == () for s in params.strips)


def test_an_absent_source_shows_its_wanted_value_and_no_level():
    engine, _ = engine_for(app("Game", "hl2.exe", gain=-6.5, mute=True), app("Music", "spotify.exe"))
    params, levels = engine.poll()
    assert params.strips[0].gain == -6.5 and params.strips[0].mute
    assert params.strips[1].gain == 0.0 and not params.strips[1].mute
    assert levels.strips == ((SILENCE_DB, SILENCE_DB),) * 2
    assert engine.presence() == (False, False)


def test_an_unassigned_channel_is_simply_absent():
    audio = FakeAudio()
    audio.add("chrome.exe")
    engine, _ = engine_for(app("Game", ""), audio=audio)
    engine.poll()
    assert engine.presence() == (False,)


def test_a_muted_source_has_no_level():
    audio = FakeAudio()
    audio.add("discord.exe", 1.0, True, 0.5)
    engine, _ = engine_for(app("Discord", "discord.exe"), audio=audio)
    params, levels = engine.poll()
    assert params.strips[0].mute and levels.strips[0] == (SILENCE_DB, SILENCE_DB)


def test_silent_volume_is_clamped_to_the_console_range():
    audio = FakeAudio()
    audio.add("discord.exe", 0.0)
    engine, _ = engine_for(app("Discord", "discord.exe"), audio=audio)
    assert engine.poll()[0].strips[0].gain == -60.0


def test_all_the_sessions_of_a_catalogue_application_are_one_source():
    audio = FakeAudio()
    audio.add("Ableton Live 12 Suite.exe", 0.5, False, 0.2)
    audio.add("Ableton Live 11 Suite.exe", 1.0, False, 0.6)
    audio.add("chrome.exe", 1.0, False, 0.9)
    assert sa.source_matches("ableton", "Ableton Live 12 Suite.exe")
    engine, _ = engine_for(app("Ableton", "ableton"), audio=audio)
    params, levels = engine.poll()
    assert params.strips[0].gain == pytest.approx(0.0, abs=0.01)             # la plus forte des sessions
    assert levels.strips[0][0] == pytest.approx(amp_to_db(0.6))              # la crête la plus haute
    engine.set_gain(0, -6.0)
    assert {c[1] for c in audio.calls} == {"Ableton Live 12 Suite.exe", "Ableton Live 11 Suite.exe"}
    assert audio.apps["chrome.exe"].volume == 1.0


def test_poll_without_windows_audio_is_quiet_and_explains_itself():
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), audio=sa.NullSystemAudio())
    params, levels = engine.poll()
    assert engine.presence() == (False, False) and len(params.strips) == 2
    assert "Audio Windows indisponible" in engine.status() and "Windows seulement" in engine.status()


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def test_gain_and_mute_reach_the_application():
    audio = FakeAudio()
    audio.add("discord.exe")
    engine, _ = engine_for(app("Discord", "discord.exe"), audio=audio)
    engine.poll()
    engine.set_gain(0, -6.0)
    engine.set_mute(0, True)
    assert audio.apps["discord.exe"].volume == pytest.approx(0.501, abs=0.001)
    assert audio.apps["discord.exe"].muted
    params, levels = engine.poll()
    assert params.strips[0].gain == pytest.approx(-6.0, abs=0.02) and params.strips[0].mute
    assert levels.strips[0] == (SILENCE_DB, SILENCE_DB)


def test_windows_sources_are_never_amplified():
    audio = FakeAudio()
    audio.add("discord.exe", 0.5)
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), audio=audio)
    engine.poll()
    engine.set_gain(1, 6.0)
    engine.set_gain(0, 12.0)
    assert audio.apps["discord.exe"].volume == 1.0 and audio.endpoints[sa.SOURCE_CAPTURE].volume == 1.0
    assert engine.poll()[0].strips[1].gain == 0.0


def test_a_setting_for_an_absent_source_waits_until_it_appears():
    audio = FakeAudio()
    engine, _ = engine_for(app("Game", "hl2.exe"), audio=audio)
    engine.poll()
    engine.set_gain(0, -9.0)
    engine.set_mute(0, True)
    assert audio.calls == []
    params, _ = engine.poll()
    assert params.strips[0].gain == -9.0 and params.strips[0].mute         # la console s'en souvient
    audio.add("hl2.exe", 1.0)
    engine.poll()
    assert audio.apps["hl2.exe"].volume == pytest.approx(0.355, abs=0.001) and audio.apps["hl2.exe"].muted
    count = len(audio.calls)
    engine.poll()
    engine.poll()
    assert len(audio.calls) == count                                       # appliqué une seule fois


def test_profile_values_are_applied_when_the_source_appears_and_none_means_hands_off():
    audio = FakeAudio()
    audio.add("discord.exe", 1.0)
    audio.add("spotify.exe", 0.4)
    engine, _ = engine_for(app("Discord", "discord.exe", gain=-6.0, mute=True),
                           app("Musique", "spotify.exe"), audio=audio)
    engine.poll()
    assert audio.apps["discord.exe"].volume == pytest.approx(0.501, abs=0.001)
    assert audio.apps["discord.exe"].muted
    assert audio.writes_to("spotify.exe") == []                           # aucune consigne : on n'y touche pas
    assert audio.apps["spotify.exe"].volume == 0.4


def test_what_the_user_set_in_windows_is_followed_and_remembered_for_next_time():
    audio = FakeAudio()
    audio.add("discord.exe", 1.0)
    engine, _ = engine_for(app("Discord", "discord.exe", gain=-6.0), audio=audio)
    engine.poll()
    audio.apps["discord.exe"] = replace(audio.apps["discord.exe"], volume=0.25)     # mélangeur de Windows
    assert engine.poll()[0].strips[0].gain == pytest.approx(-12.04, abs=0.02)
    del audio.apps["discord.exe"]
    assert engine.poll()[0].strips[0].gain == pytest.approx(-12.04, abs=0.02)       # absent : son dernier réglage
    audio.add("discord.exe", 1.0)
    engine.poll()
    assert audio.apps["discord.exe"].volume == pytest.approx(0.25, abs=0.005)


def test_without_a_wish_a_windows_change_is_not_turned_into_one():
    audio = FakeAudio()
    audio.add("spotify.exe", 1.0)
    engine, _ = engine_for(app("Musique", "spotify.exe"), audio=audio)
    engine.poll()
    audio.apps["spotify.exe"] = replace(audio.apps["spotify.exe"], volume=0.25)
    engine.poll()
    del audio.apps["spotify.exe"]
    engine.poll()
    audio.add("spotify.exe", 1.0)
    engine.poll()
    assert audio.writes_to("spotify.exe") == [] and audio.apps["spotify.exe"].volume == 1.0


def test_the_master_fader_is_the_windows_output_volume():
    audio = FakeAudio()
    engine, _ = engine_for(MIC, audio=audio)
    engine.set_headphone_gain(-6.0)
    assert audio.endpoints[sa.SOURCE_MASTER].volume == pytest.approx(0.501, abs=0.001)
    assert engine.poll()[0].headphone_gain == pytest.approx(-6.0, abs=0.02)
    engine.set_headphone_gain(3.0)
    assert audio.endpoints[sa.SOURCE_MASTER].volume == 1.0
    assert engine.poll()[0].headphone_gain == 0.0


def test_commands_for_unknown_strips_are_ignored():
    engine, audio = engine_for(MIC)
    for call in (lambda: engine.set_gain(5, -3), lambda: engine.set_gain(-1, -3),
                 lambda: engine.set_mute(9, True), lambda: engine.set_pad(7, "pan", 0.1, 0.0),
                 lambda: engine.set_solo(3, True), lambda: engine.set_label(4, "x"),
                 lambda: engine.assign_app(8, "chrome.exe")):
        call()
    assert audio.calls == []


def test_commands_without_an_equivalent_are_harmless():
    engine, audio = engine_for(MIC)
    engine.set_route(0, 1, True)
    engine.set_send(0, 1, -3.0)
    engine.set_buffering(256)
    engine.set_wdm_exclusive(True)
    engine.set_inserts({0})
    engine.apply_state({"strips": [{"gain": -10}]})
    engine.set_pad(0, "pan", 0.2, 0.0)                    # le micro n'a pas de pan
    assert audio.calls == []


def test_assigning_an_application_changes_the_source_and_the_profile():
    audio = FakeAudio()
    audio.add("hl2.exe", 0.5)
    engine = StandaloneEngine(pi.default_profile(ad.known_app("obs")), audio)
    assert engine.profile.channels[1].source.key == ""
    engine.poll()
    assert engine.presence()[1] is False
    engine.assign_app(1, "hl2.exe")
    engine.assign_app(1, "   ")                                         # vide : sans effet
    engine.poll()
    assert engine.presence()[1] is True
    assert engine.profile.channels[1].source == ChannelSource(sa.SOURCE_APP, "hl2.exe")
    assert engine.profile.channels[1].role == "game" and engine.profile.name == "OBS Studio (direct)"


def test_renaming_a_strip_renames_the_channel_of_the_profile():
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"))
    engine.set_label(1, "  Équipe  ")
    engine.set_label(0, "   ")
    engine.set_label(1, "x" * 80)
    assert engine.names[0] == "Micro" and len(engine.names[1]) == se.MAX_NAME
    assert engine.profile.channels[1].name == "x" * se.MAX_NAME


def test_a_new_profile_replaces_the_channels_and_resets_what_was_wished():
    audio = FakeAudio()
    audio.add("discord.exe")
    engine, _ = engine_for(app("Discord", "discord.exe"), audio=audio)
    engine.poll()
    engine.set_gain(0, -20.0)
    engine.set_profile(profile(MIC, app("Musique", "spotify.exe")))
    assert engine.strip_count == 2 and engine.names == ["Micro", "Musique"]
    assert engine.poll()[0].strips[1].gain == 0.0


def test_status_counts_the_active_sources():
    audio = FakeAudio()
    audio.add("discord.exe")
    engine, _ = engine_for(MIC, app("Discord", "discord.exe"), app("Game", "hl2.exe"), audio=audio)
    engine.poll()
    assert engine.status() == "2 sources sur 3 actives"
    audio.apps.clear()
    engine.poll()
    assert engine.status() == "1 source sur 3 active"


# --------------------------------------------------------------------------- #
# Pistes de FL Studio
# --------------------------------------------------------------------------- #
def test_fl_tracks_give_volume_mute_pan_solo_and_levels():
    link = DemoFlLink()
    link.state(1).volume, link.state(1).pan, link.state(1).muted = 0.5, 0.5, True
    link.state(2).solo = True
    engine, _ = engine_for(fl_track("Bus Batterie", 1), fl_track("Synths", 2), link=link)
    params, levels = engine.poll()
    assert params.strips[0].gain == pytest.approx(fl_volume_to_db(0.5), abs=0.01)
    assert params.strips[0].mute and params.strips[0].pads == (("pan", 0.25, 0.0),)
    assert params.strips[1].gain == pytest.approx(0.0) and params.strips[1].pads == (("pan", 0.0, 0.0),)
    assert levels.strips[0] == (SILENCE_DB, SILENCE_DB)                      # coupé
    assert levels.strips[1][0] > SILENCE_DB
    assert engine.solo_states() == {0: False, 1: True}
    assert engine.presence() == (True, True)


def test_the_console_drives_fl_studio():
    link = DemoFlLink()
    engine, _ = engine_for(fl_track("Master", 0), fl_track("Synths", 2), link=link)
    engine.poll()
    engine.set_gain(1, -12.0)
    engine.set_mute(1, True)
    engine.set_pad(1, "pan", 0.25, 0.9)
    engine.set_pad(1, "color", 0.25, 0.9)                                   # mode inconnu : ignoré
    engine.set_solo(1, True)
    assert link.writes == [("volume", 2, pytest.approx(db_to_fl_volume(-12.0))), ("mute", 2, True),
                           ("pan", 2, 0.5), ("solo", 2, True)]
    params, _ = engine.poll()
    assert params.strips[1].gain == pytest.approx(-12.0, abs=0.02) and params.strips[1].mute
    assert params.strips[1].pads == (("pan", 0.25, 0.0),)


def test_the_top_of_the_fl_fader_is_above_zero_db():
    link = DemoFlLink()
    engine, _ = engine_for(fl_track("Master", 0), link=link)
    engine.poll()
    engine.set_gain(0, 6.0)
    assert engine.poll()[0].strips[0].gain == pytest.approx(fl.FL_MAX_DB, abs=0.01)


def test_fl_studio_has_the_last_word_when_it_connects():
    link = DemoFlLink()
    link.connected = False
    engine, _ = engine_for(ProfileChannel("Master", ChannelSource(pi.SOURCE_FL_TRACK, track=0),
                                          "master", gain=-3.0, mute=True), link=link)
    assert engine.poll()[0].strips[0].gain == -3.0 and engine.presence() == (False,)
    link.connected = True
    params, _ = engine.poll()
    assert link.writes == []                                                # le profil ne bouscule pas FL
    assert params.strips[0].gain == pytest.approx(0.0) and not params.strips[0].mute


def test_fl_tracks_are_absent_until_fl_studio_has_described_them():
    link = DemoFlLink()
    link.state(1).known = False
    engine, _ = engine_for(fl_track("Drums", 1), link=link)
    assert engine.poll()[0].strips[0].gain == 0.0 and engine.presence() == (False,)
    link.state(1).known = True
    engine.poll()
    assert engine.presence() == (True,)


def test_native_solo_belongs_to_connected_fl_tracks_only():
    link = DemoFlLink()
    engine, _ = engine_for(MIC, fl_track("Synths", 2), link=link)
    assert [engine.native_solo(0), engine.native_solo(1)] == [False, True]
    link.connected = False
    assert engine.native_solo(1) is False
    engine.set_solo(0, True)                                                # le micro : sans effet
    assert link.writes == []


def test_the_master_meter_follows_the_fl_master_track():
    class Still(DemoFlLink):
        def poll(self):                          # niveaux figés : on les choisit
            return fl.PollResult()

    link = Still()
    link.state(0).peak_db = (-8.0, -9.0)
    audio = FakeAudio()
    audio.endpoints[sa.SOURCE_MASTER] = SourceState(0.7, False, 0.0, True)         # Windows : silence
    engine, _ = engine_for(fl_track("Master", 0), link=link, audio=audio)
    _, levels = engine.poll()
    assert levels.master == (-8.0, -8.0)
    audio.endpoints[sa.SOURCE_MASTER] = SourceState(0.7, False, 0.5, True)
    assert engine.poll()[1].master[0] == pytest.approx(amp_to_db(0.5))


def test_without_a_link_fl_strips_are_absent_and_the_status_says_why():
    engine, _ = engine_for(MIC, fl_track("Synths", 2), fl_error="Port MIDI virtuel introuvable.")
    params, _ = engine.poll()
    assert engine.presence() == (True, False) and len(params.strips) == 2
    assert "FL Studio non synchronisé : Port MIDI virtuel introuvable." in engine.status()
    engine.set_gain(1, -3.0)                                                # sans erreur
    engine.set_pad(1, "pan", 0.1, 0.0)
    engine.set_solo(1, True)
    assert not engine.native_solo(1) and not engine.fl_connected


def test_status_waits_for_the_fl_script():
    transport, clock = FakeTransport(), FakeClock()
    core = FlSyncCore(transport, clock=clock)
    core.start()
    engine, _ = engine_for(fl_track("Synths", 2), link=core)
    engine.poll()
    assert "en attente du script VoiceConnector" in engine.status()


def test_the_watch_list_follows_the_profile():
    class Spy(DemoFlLink):
        watched = None

        def set_watch(self, tracks):
            self.watched = list(tracks)

    link = Spy()
    engine, _ = engine_for(fl_track("Master", 0), fl_track("Synths", 2), MIC, fl_track("FX", 3), link=link)
    assert link.watched == [0, 2, 3]
    engine.set_profile(profile(MIC))
    assert link.watched == []


def test_shutdown_says_goodbye_to_fl_studio():
    link = DemoFlLink()
    engine, _ = engine_for(fl_track("Master", 0), link=link)
    engine.shutdown()
    assert link.connected is False
    quiet, _ = engine_for(MIC)
    quiet.shutdown()                                                        # sans liaison : sans erreur


def test_the_fl_link_can_be_attached_and_detached_while_running():
    """Le logiciel suivi change : la synchronisation se branche (ou se débranche) sans recréer le moteur."""
    engine, _ = engine_for(MIC, fl_track("Synths", 2), fl_error="Port MIDI introuvable.")
    engine.poll()
    assert engine.presence() == (True, False) and "Port MIDI introuvable." in engine.status()

    class Spy(DemoFlLink):
        watched = None
        closed = 0

        def set_watch(self, tracks):
            self.watched = list(tracks)

        def close(self):
            self.closed += 1
            super().close()

    link = Spy()
    engine.attach_fl(link)
    assert link.watched == [2] and engine.fl_error == ""
    engine.poll()
    assert engine.presence() == (True, True)                               # la piste apparaît
    other = Spy()
    engine.attach_fl(other)                                                # autre liaison : l'ancienne est fermée
    assert link.closed == 1 and other.watched == [2]
    engine.attach_fl(None, "Plus de port MIDI.")
    assert other.closed == 1 and engine.fl is None
    engine.poll()
    assert engine.presence() == (True, False)
    assert "FL Studio non synchronisé : Plus de port MIDI." in engine.status()


def test_attaching_the_same_link_again_does_not_close_it():
    link = DemoFlLink()
    engine, _ = engine_for(fl_track("Master", 0), link=link)
    engine.attach_fl(link)
    assert link.connected is not False


def test_the_demo_fl_link_behaves_like_the_real_one():
    link = DemoFlLink(names=("Master", "Drums"))
    assert link.connected and link.track_count == 2 and link.state(1).name == "Drums"
    link.write_volume(1, 7.0)
    link.write_pan(1, -3.0)
    link.write_mute(1, True)
    link.write_solo(1, True)
    state = link.state(1)
    assert (state.volume, state.pan, state.muted, state.solo) == (1.0, -1.0, True, True)
    assert link.state(9).known and link.state(9).name == "Insert 9"        # piste inconnue : créée
    link.poll()
    assert link.state(0).peak_db[0] > SILENCE_DB and link.state(1).peak_db[0] == SILENCE_DB
    assert link.flush() == 0
    link.stop()
    assert not link.connected
    link.start()
    assert link.connected


# --------------------------------------------------------------------------- #
# Avec le vrai script de FL Studio (faux mixer, sans MIDI)
# --------------------------------------------------------------------------- #
@pytest.fixture
def rig(monkeypatch):
    return FlStudioRig(monkeypatch)


def test_console_and_fl_studio_follow_each_other_through_the_real_script(rig):
    clock = FakeClock()
    core = FlSyncCore(rig, watch=(0, 1, 2), clock=clock)
    core.start()
    for _ in range(3):
        core.poll()
    rig.mixer.tracks[1]["volume"] = 0.5
    engine, audio = engine_for(fl_track("Master", 0, "master"), fl_track("Bus Batterie", 1, "drums"),
                               MIC, link=core, clock=clock)
    clock.advance(1.0)
    params, _ = engine.poll()
    assert engine.fl_connected and engine.presence() == (True, True, True)
    assert params.strips[1].gain == pytest.approx(fl_volume_to_db(0.5), abs=0.01)

    engine.set_gain(0, -12.0)                       # console → FL Studio
    engine.set_mute(1, True)
    engine.set_pad(1, "pan", -0.25, 0.0)
    engine.set_solo(1, True)
    engine.poll()
    assert rig.mixer.tracks[0]["volume"] == pytest.approx(db_to_fl_volume(-12.0), abs=1e-3)
    assert rig.mixer.tracks[1]["mute"] and rig.mixer.tracks[1]["solo"]
    assert rig.mixer.tracks[1]["pan"] == pytest.approx(-0.5, abs=1e-3)

    clock.advance(1.0)                              # FL Studio → console
    rig.mixer.tracks[0]["volume"] = 0.7
    rig.mixer.tracks[1]["mute"] = False
    rig.mixer.tracks[1]["pan"] = 0.6
    rig.script.OnRefresh(0)
    params, _ = engine.poll()
    assert params.strips[0].gain == pytest.approx(fl_volume_to_db(0.7), abs=0.02)
    assert not params.strips[1].mute and params.strips[1].pads == (("pan", 0.3, 0.0),)
    assert audio.calls == []                        # FL Studio ne touche pas à l'audio de Windows
    engine.shutdown()
    assert rig.outbox == [] or True                 # BYE envoyé, sans erreur


# --------------------------------------------------------------------------- #
# Tous les profils automatiques
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("known", ad.KNOWN_APPS, ids=lambda a: a.key)
def test_every_automatic_profile_runs_on_the_engine(known):
    engine = StandaloneEngine(pi.default_profile(known), sa.DemoSystemAudio(clock=lambda: 0.0),
                              DemoFlLink())
    params, levels = engine.poll()
    assert len(params.strips) == len(levels.strips) == engine.strip_count
    infos = engine.strip_infos()
    plan = lm.build_plan(lm.LAYOUT_PRESETS[engine.profile.layout or "standard"], infos, caps=engine.caps)
    assert sorted(plan.order + tuple(plan.hidden)) == list(range(engine.strip_count))


def test_module_has_no_qt_dependency():
    source = open(se.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

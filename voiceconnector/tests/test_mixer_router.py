"""Routage vers l'application vocale (mode A) : choix de l'application et du bus, routage
sans écho, flux VBAN expérimental, et les réglages de session qui l'accompagnent."""

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core import mixer_router as mr
from core import profile_import as pi
from core import session_mode as sm
from core.mixer_model import MixerParams, StripParams
from core.mixer_router import (RouteCommand, VbanStream, choose_voice_app, describe_status, pick_bus,
                               plan_voice_route, recording_device)

DEFAULT_ROUTING = ({"label": "Monitor", "bus": "A1"}, {"label": "Mic Out", "bus": "B1"},
                   {"label": "Discord", "bus": "B2"}, {"label": "Obs", "bus": "A2"})
POTATO = ("A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3")
BANANA = ("A1", "A2", "A3", "B1", "B2")


def detected(*keys) -> list[ad.DetectedApp]:
    return [ad.DetectedApp(ad.known_app(k)) for k in keys]


def active(key: str | None, running: bool = True) -> ad.ActiveApp:
    return ad.ActiveApp(ad.known_app(key), ad.SOURCE_DETECTED, running) if key else ad.NO_APP


def streaming_plan() -> lm.LayoutPlan:
    """Micro, Game, Discord, Music : des rôles déclarés (comme un profil)."""
    strips = [lm.StripInfo(0, True, "Micro", "mic", fixed=True), lm.StripInfo(1, False, "Game", "game", fixed=True),
              lm.StripInfo(2, False, "Discord", "discord", fixed=True),
              lm.StripInfo(3, False, "Music", "music", fixed=True)]
    return lm.build_plan(lm.LAYOUT_PRESETS["streaming"], strips)


def podcast_plan() -> lm.LayoutPlan:
    strips = [lm.StripInfo(0, True, "Voix", "voice", fixed=True), lm.StripInfo(1, False, "Invité", "guest", fixed=True),
              lm.StripInfo(2, False, "Jingles", "music", fixed=True)]
    return lm.build_plan(lm.LAYOUT_PRESETS["podcast"], strips)


# --------------------------------------------------------------------------- #
# Application vocale
# --------------------------------------------------------------------------- #
def test_an_explicit_voice_app_wins():
    app = choose_voice_app("teamspeak3", active("discord"), detected("discord"))
    assert app.key == "teamspeak3"


def test_a_setting_that_is_not_a_voice_app_falls_back_to_auto():
    app = choose_voice_app("fl_studio", active("fl_studio"), detected("fl_studio", "discord"))
    assert app.key == "discord"


def test_auto_prefers_the_followed_software_when_it_is_vocal():
    assert choose_voice_app("auto", active("obs"), detected("obs", "discord")).key == "obs"
    assert choose_voice_app(None, active("obs"), []).key == "obs"


def test_auto_takes_the_best_ranked_running_voice_app():
    apps = detected("zoom", "discord", "fl_studio")
    best = min(apps[:2], key=lambda d: (d.app.priority, d.app.name.lower())).key
    assert choose_voice_app("auto", active("fl_studio"), apps).key == best


def test_no_voice_app_means_nothing_to_feed():
    assert choose_voice_app("auto", active("fl_studio"), detected("fl_studio", "spotify")) is None
    assert choose_voice_app("auto", ad.NO_APP, []) is None
    assert choose_voice_app("inconnu", ad.NO_APP, []) is None


# --------------------------------------------------------------------------- #
# Bus
# --------------------------------------------------------------------------- #
def test_the_button_named_after_the_app_decides_the_bus():
    assert pick_bus(ad.known_app("discord"), DEFAULT_ROUTING, POTATO) == ("B2", 2)
    assert pick_bus(ad.known_app("obs"), DEFAULT_ROUTING, POTATO) == ("A2", 3)


def test_a_chosen_bus_wins_over_the_buttons():
    assert pick_bus(ad.known_app("discord"), DEFAULT_ROUTING, POTATO, "b1") == ("B1", 1)
    assert pick_bus(ad.known_app("discord"), DEFAULT_ROUTING, POTATO, "B3") == ("B3", None)   # sans bouton


def test_an_unknown_bus_is_ignored():
    assert pick_bus(ad.known_app("discord"), DEFAULT_ROUTING, BANANA, "B3") == ("B2", 2)
    assert pick_bus(ad.known_app("discord"), DEFAULT_ROUTING, BANANA, "Z9") == ("B2", 2)


@pytest.mark.parametrize("key, expected", [("discord", "B1"), ("zoom", "B1"), ("audacity", "B2"),
                                           ("streamlabs", "B2")])
def test_without_a_button_the_default_bus_depends_on_the_kind(key, expected):
    # Streamlabs : le bouton « Obs » de la matrice ne porte pas son nom
    assert pick_bus(ad.known_app(key), (), POTATO)[0] == expected


def test_a_missing_default_bus_becomes_the_first_b_bus():
    assert pick_bus(ad.known_app("audacity"), (), ("A1", "A2", "B1"))[0] == "B1"
    assert pick_bus(ad.known_app("audacity"), (), ("A1", "B1"))[0] == "B1"
    assert pick_bus(ad.known_app("audacity"), (), ("A1", "A2"))[0] == "A1"
    assert pick_bus(ad.known_app("audacity"), (), ())[0] == "B1"


def test_recording_device_names():
    assert recording_device("B1") == "Voicemeeter Out B1" and recording_device("b3") == "Voicemeeter Out B3"


# --------------------------------------------------------------------------- #
# Plan : bus
# --------------------------------------------------------------------------- #
def test_discord_gets_the_voice_and_never_its_own_sound_back():
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO,
                             plan=streaming_plan(), strip_names=["Micro", "Game", "Discord", "Music"])
    assert (route.bus, route.route, route.device) == ("B2", 2, "Voicemeeter Out B2")
    assert route.feed == (0,) and route.block == (2,)
    assert route.commands() == [RouteCommand(0, 2, True), RouteCommand(2, 2, False)]
    assert route.summary() == "Discord ← bus B2 (« Voicemeeter Out B2 »)"
    text = " ".join(route.steps)
    assert "Paramètres utilisateur → Voix et vidéo" in text and "« Voicemeeter Out B2 »" in text
    assert "Voicemeeter Aux Output" in text and "Voicemeeter Input (VAIO)" in text
    assert "Micro" in text and "Discord" in text and "pas d'écho" in text
    assert route.warnings == ()


def test_a_podcast_guest_does_not_hear_themselves():
    route = plan_voice_route(ad.known_app("zoom"), routing=DEFAULT_ROUTING, buses=POTATO, plan=podcast_plan())
    assert route.feed == (0,) and route.block == (1,)                    # la voix : oui ; l'invité : non
    assert 2 not in route.feed and 2 not in route.block                  # les jingles : au choix de l'utilisateur


def test_obs_captures_everything_but_the_master():
    plan = lm.build_plan(lm.LAYOUT_PRESETS["fl_studio"], [
        lm.StripInfo(0, False, "Master", "master", fixed=True), lm.StripInfo(1, False, "Synths", "synths", fixed=True),
        lm.StripInfo(2, True, "Micro", "mic", fixed=True)])
    route = plan_voice_route(ad.known_app("obs"), routing=DEFAULT_ROUTING, buses=POTATO, plan=plan)
    assert route.feed == (1, 2) and route.block == ()


def test_audacity_records_the_voice_only():
    route = plan_voice_route(ad.known_app("audacity"), routing=(), buses=POTATO, plan=streaming_plan())
    assert route.feed == (0,) and route.block == ()


def test_without_roles_the_hardware_inputs_are_the_voice():
    plan = lm.build_plan(lm.LAYOUT_PRESETS["standard"], [lm.StripInfo(i, i < 2, f"S{i}") for i in range(4)])
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO, plan=plan,
                             physical=[True, True, False, False])
    assert route.feed == (0, 1) and route.block == ()
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO,
                             physical=[True, False, True])
    assert route.feed == (0, 2)


def test_only_voice_apps_have_a_route():
    assert plan_voice_route(None) is None
    assert plan_voice_route(ad.known_app("fl_studio")) is None
    assert plan_voice_route(ad.known_app("spotify")) is None


def test_pending_commands_are_only_the_ones_that_change_something():
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO, plan=streaming_plan())
    off = StripParams(0.0, False, (False,) * 4, (0.0,) * 4)
    on_b2 = StripParams(0.0, False, (False, False, True, False), (0.0,) * 4)
    params = MixerParams((on_b2, off, on_b2, off), 0.0)                  # le micro l'a déjà ; Discord se l'envoie à lui-même
    assert route.pending(params) == [RouteCommand(2, 2, False)]
    assert route.pending(MixerParams((on_b2, off, off, off), 0.0)) == []
    assert route.pending(None) == route.commands()
    assert route.pending(MixerParams((on_b2,), 0.0)) == []               # tranches absentes : ignorées


def test_missing_matrix_button_is_explained():
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO,
                             preferred_bus="B3", plan=streaming_plan())
    assert route.route is None and route.commands() == []
    assert any("n'a pas de bouton" in w for w in route.warnings)
    assert "bouton" not in " ".join(route.steps)


def test_a_hardware_bus_gets_a_warning():
    route = plan_voice_route(ad.known_app("obs"), routing=DEFAULT_ROUTING, buses=POTATO, plan=streaming_plan())
    assert route.bus == "A2" and any("sortie matérielle" in w for w in route.warnings)


def test_no_voice_strip_gets_a_warning():
    plan = lm.build_plan(lm.LAYOUT_PRESETS["standard"], [lm.StripInfo(0, False, "VAIO")])
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO, plan=plan)
    assert route.feed == () and any("rôles" in w for w in route.warnings)


def test_an_unknown_transport_means_bus():
    assert plan_voice_route(ad.known_app("discord"), "pigeon", routing=DEFAULT_ROUTING, buses=POTATO).transport == "bus"


# --------------------------------------------------------------------------- #
# VBAN
# --------------------------------------------------------------------------- #
def test_vban_stream_parameters_for_the_remote_api():
    stream = VbanStream(2, "Voix", "192.168.1.20", 6981)
    assert stream.params() == (("vban.Enable", 1.0), ("vban.outstream[2].name", "Voix"),
                               ("vban.outstream[2].ip", "192.168.1.20"), ("vban.outstream[2].port", 6981.0),
                               ("vban.outstream[2].on", 1.0))
    assert stream.off_params() == (("vban.outstream[2].on", 0.0),)
    assert stream.describe() == "flux VBAN n°3 « Voix » vers 192.168.1.20:6981"


@pytest.mark.parametrize("raw, expected", [
    (None, VbanStream()), ({}, VbanStream()), ("pas un dict", VbanStream()),
    ({"index": 3, "name": "Studio", "ip": "10.0.0.5", "port": 7000}, VbanStream(3, "Studio", "10.0.0.5", 7000)),
    ({"index": 99}, VbanStream()), ({"index": "x"}, VbanStream()), ({"index": -1}, VbanStream()),
    ({"ip": "pas-une-ip"}, VbanStream()), ({"ip": "999.1.1.1"}, VbanStream()),
    ({"port": 80}, VbanStream()), ({"port": 70000}, VbanStream()), ({"port": "x"}, VbanStream()),
    ({"name": "é" * 30}, VbanStream()),
    ({"name": "UnNomBeaucoupTropLongPourVBAN"}, VbanStream(name="UnNomBeaucoupTro")),
    ({"name": "a b"}, VbanStream(name="ab")),
])
def test_vban_settings_are_sanitized(raw, expected):
    assert VbanStream.from_mapping(raw) == expected


def test_banana_has_fewer_vban_streams():
    assert VbanStream.from_mapping({"index": 6}, max_streams=4).index == 0
    assert VbanStream.from_mapping({"index": 3}, max_streams=4).index == 3


def test_a_vban_route_configures_the_stream_instead_of_the_matrix():
    route = plan_voice_route(ad.known_app("obs"), "vban", routing=DEFAULT_ROUTING, buses=POTATO,
                             plan=streaming_plan(), vban=VbanStream(0, "Direct", "127.0.0.1", 6980))
    assert route.transport == "vban" and route.vban.name == "Direct"
    assert route.commands() == [] and route.pending(None) == []
    assert route.vban_params()[1] == ("vban.outstream[0].name", "Direct")
    assert route.summary() == "OBS Studio ← flux VBAN n°1 « Direct » vers 127.0.0.1:6980"
    text = " ".join(route.steps)
    assert "Route" in text and "VBAN Receptor" in text and "6980" in text
    assert any("expérimental" in w for w in route.warnings)


def test_a_bus_route_has_no_vban_parameters():
    route = plan_voice_route(ad.known_app("discord"), "bus", routing=DEFAULT_ROUTING, buses=POTATO,
                             vban=VbanStream())
    assert route.vban is None and route.vban_params() == ()


# --------------------------------------------------------------------------- #
# Texte pour les réglages
# --------------------------------------------------------------------------- #
def test_status_line():
    assert describe_status(None, None) == "Aucune application vocale à alimenter."
    route = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO, plan=streaming_plan())
    off = StripParams(0.0, False, (False,) * 4, (0.0,) * 4)
    on_b2 = StripParams(0.0, False, (False, False, True, False), (0.0,) * 4)
    assert describe_status(route, MixerParams((off, off, on_b2, off), 0.0)).endswith("2 routes à régler")
    assert describe_status(route, MixerParams((on_b2, off, off, off), 0.0)).endswith("routage conforme")
    assert describe_status(route, MixerParams((off, off, off, off), 0.0)).endswith("1 route à régler")
    no_button = plan_voice_route(ad.known_app("discord"), routing=DEFAULT_ROUTING, buses=POTATO, preferred_bus="B3")
    assert describe_status(no_button, None).endswith("bus à régler dans Voicemeeter")
    vban = plan_voice_route(ad.known_app("discord"), "vban", buses=POTATO)
    assert describe_status(vban, None).endswith("(expérimental)")


# --------------------------------------------------------------------------- #
# Réglages de session
# --------------------------------------------------------------------------- #
def test_the_transport_constants_are_shared():
    assert sm.TRANSPORT_BUS is mr.TRANSPORT_BUS and sm.TRANSPORT_VBAN is mr.TRANSPORT_VBAN
    assert sm.TRANSPORTS == mr.TRANSPORTS == ("bus", "vban")


def test_voice_bus_setting():
    session = sm.SessionSettings({})
    assert session.voice_bus("discord") is None
    session.set_voice_bus("discord", "b2")
    assert session.voice_bus("discord") == "B2" and session.cfg["session"]["voice_bus"] == {"discord": "B2"}
    session.cfg["session"]["voice_bus"]["obs"] = "n'importe quoi"
    assert session.voice_bus("obs") is None
    session.set_voice_bus("discord", None)
    assert session.voice_bus("discord") is None


def test_vban_setting_round_trip():
    session = sm.SessionSettings({})
    assert session.vban == {}
    session.set_vban({"index": 1, "name": "Voix", "ip": "10.0.0.2", "port": 7001})
    assert VbanStream.from_mapping(session.vban) == VbanStream(1, "Voix", "10.0.0.2", 7001)
    session.cfg["session"]["vban"] = "cassé"
    assert session.vban == {}


def test_console_overrides_are_stored_per_profile():
    session = sm.SessionSettings({})
    assert session.overrides("OBS Studio (direct)") == {}
    session.set_override("OBS Studio (direct)", 1, app="hl2.exe")
    session.set_override("OBS Studio (direct)", 1, name="Jeu")
    session.set_override("OBS Studio (direct)", 3, name="Zik")
    assert session.overrides("OBS Studio (direct)") == {1: {"app": "hl2.exe", "name": "Jeu"}, 3: {"name": "Zik"}}
    assert session.overrides("Autre profil") == {}
    session.set_override("OBS Studio (direct)", 1, app="")                # on retire le choix
    session.set_override("OBS Studio (direct)", 3, name="  ")
    assert session.overrides("OBS Studio (direct)") == {1: {"name": "Jeu"}}
    session.cfg["session"]["overrides"]["OBS Studio (direct)"]["x"] = {"name": "ignoré"}
    session.cfg["session"]["overrides"]["OBS Studio (direct)"]["2"] = "pas un dict"
    assert session.overrides("OBS Studio (direct)") == {1: {"name": "Jeu"}}


def test_overrides_are_applied_to_a_profile():
    profile = pi.default_profile(ad.known_app("obs"))
    result = pi.apply_overrides(profile, {1: {"app": "hl2.exe", "name": "Jeu"}, 0: {"app": "x.exe"},
                                         9: {"name": "hors profil"}, "a": {"name": "x"}})
    assert result.channels[1].name == "Jeu" and result.channels[1].source.key == "hl2.exe"
    assert result.channels[1].role == "game"
    assert result.channels[0].source.type == "capture" and result.channels[0].name == "Micro"   # micro : pas d'appli
    assert result.name == profile.name and len(result.channels) == len(profile.channels)
    assert pi.apply_overrides(profile, {}) == profile


def test_module_has_no_qt_dependency():
    source = open(mr.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

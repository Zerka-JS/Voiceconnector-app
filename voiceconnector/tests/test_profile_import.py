"""Profils du mode autonome : lecture, validation, export et profils automatiques."""

import json

import pytest

from core import app_detector as ad
from core import layout_manager as lm
from core import profile_import as pi
from core.profile_import import (ChannelSource, ProfileError, default_profile, load_profile,
                                 parse_profile, profile_to_dict, resolve_profile, save_profile)


def sample() -> dict:
    return {
        "format": "voiceconnector-profile", "version": 1, "name": "Mon studio",
        "software": "fl_studio", "layout": "fl_studio", "theme": "slate",
        "channels": [
            {"name": "Master", "role": "master", "source": {"type": "fl_track", "track": 0}},
            {"name": "Discord", "role": "discord", "source": {"type": "app", "key": "discord.exe"},
             "gain": -6.5, "mute": True},
            {"name": "Micro", "role": "mic", "source": {"type": "capture"}},
        ],
    }


def active(key: str) -> ad.ActiveApp:
    return ad.ActiveApp(ad.known_app(key), ad.SOURCE_DETECTED, True)


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def test_parse_a_complete_profile():
    profile = parse_profile(sample())
    assert profile.name == "Mon studio" and profile.software == "fl_studio"
    assert profile.layout == "fl_studio" and profile.theme == "slate"
    master, discord, mic = profile.channels
    assert master.source == ChannelSource("fl_track", track=0) and master.role == "master"
    assert discord.source == ChannelSource("app", "discord.exe") and discord.gain == -6.5 and discord.mute
    assert mic.source.type == "capture" and mic.physical and not discord.physical
    assert profile.uses_fl_sync


def test_parse_is_lenient_about_optional_fields():
    profile = parse_profile({"channels": [{"source": {"type": "master"}}]})
    assert profile.name == "Profil importé" and profile.software == "" and profile.layout == ""
    channel = profile.channels[0]
    assert channel.name == "Canal 1" and channel.role is None
    assert channel.gain is None and channel.mute is None            # on ne touche pas au son de la source
    assert not profile.uses_fl_sync


def test_unknown_references_are_dropped_not_fatal():
    data = sample() | {"software": "logiciel-inconnu", "layout": "bizarre", "theme": "rose"}
    data["channels"][0]["role"] = "chef"
    profile = parse_profile(data)
    assert (profile.software, profile.layout, profile.theme) == ("", "", "")
    assert profile.channels[0].role is None


def test_values_are_clamped_and_cleaned():
    data = sample()
    data["channels"][1].update(gain=99, name="  " + "x" * 80 + "  ")
    data["channels"][2]["gain"] = "pas un nombre"
    profile = parse_profile(data)
    assert profile.channels[1].gain == 12.0 and len(profile.channels[1].name) == pi.MAX_NAME
    assert profile.channels[2].gain is None
    data["channels"][1]["gain"] = -500
    assert parse_profile(data).channels[1].gain == -60.0


@pytest.mark.parametrize("data, message", [
    ([], "objet JSON"),
    ({"format": "autre", "channels": [{"source": {"type": "master"}}]}, "pas un profil"),
    ({"version": "1", "channels": [{"source": {"type": "master"}}]}, "Version"),
    ({"channels": []}, "aucun canal"),
    ({}, "aucun canal"),
    ({"channels": "x"}, "aucun canal"),
    ({"channels": [{"source": {"type": "master"}}] * 25}, "Trop de canaux"),
    ({"channels": ["x"]}, "Canal 1 : un objet"),
    ({"channels": [{"name": "A"}]}, "source"),
    ({"channels": [{"source": {"type": "carte-son"}}]}, "inconnue"),
    ({"channels": [{"source": {}}]}, "inconnue"),
    ({"channels": [{"source": {"type": "fl_track", "track": 500}}]}, "piste FL Studio"),
    ({"channels": [{"source": {"type": "fl_track", "track": "x"}}]}, "piste FL Studio"),
    ({"channels": [{"source": {"type": "fl_track"}}]}, "piste FL Studio"),
])
def test_invalid_profiles_give_a_clear_message(data, message):
    with pytest.raises(ProfileError, match=message):
        parse_profile(data)


def test_a_newer_profile_version_is_read_best_effort():
    assert parse_profile(sample() | {"version": 7}).name == "Mon studio"


def test_source_type_is_validated_on_construction():
    with pytest.raises(ProfileError):
        ChannelSource("telepathie")


# --------------------------------------------------------------------------- #
# Fichiers
# --------------------------------------------------------------------------- #
def test_save_then_load_round_trip(tmp_path):
    profile = parse_profile(sample())
    path = tmp_path / "studio.json"
    save_profile(profile, path)
    assert load_profile(path) == profile
    assert not list(tmp_path.glob("*.tmp"))
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["format"] == pi.PROFILE_FORMAT and written["channels"][1]["gain"] == -6.5
    assert "key" not in written["channels"][2]["source"]                 # micro : pas de clé inutile
    assert "gain" not in written["channels"][0] and "mute" not in written["channels"][0]
    assert written["channels"][1]["mute"] is True
    assert profile_to_dict(parse_profile(profile_to_dict(profile))) == profile_to_dict(profile)


def test_load_handles_bad_files(tmp_path):
    with pytest.raises(ProfileError, match="illisible"):
        load_profile(tmp_path / "absent.json")
    broken = tmp_path / "casse.json"
    broken.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(ProfileError, match="JSON invalide"):
        load_profile(broken)
    binary = tmp_path / "binaire.json"
    binary.write_bytes(b"\xff\xfe\x00\x01\x80")
    with pytest.raises(ProfileError, match="UTF-8"):
        load_profile(binary)
    huge = tmp_path / "enorme.json"
    huge.write_text(" " * (pi.MAX_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ProfileError, match="volumineux"):
        load_profile(huge)


def test_load_accepts_a_utf8_bom_and_accents(tmp_path):
    path = tmp_path / "bom.json"
    data = sample()
    data["name"] = "Émission été"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
    assert load_profile(path).name == "Émission été"


# --------------------------------------------------------------------------- #
# Profils automatiques
# --------------------------------------------------------------------------- #
def test_fl_studio_profile_has_the_studio_buses_synced_with_fl_tracks():
    profile = default_profile(ad.known_app("fl_studio"))
    assert profile.layout == "fl_studio" and profile.software == "fl_studio"
    assert [c.name for c in profile.channels][:4] == ["Master", "Bus Batterie", "Synths", "FX"]
    assert [c.source.track for c in profile.channels[:4]] == [0, 1, 2, 3]
    assert profile.channels[-1].source.type == "capture" and profile.uses_fl_sync


def test_fl_studio_without_sync_is_an_ordinary_application():
    profile = default_profile(ad.known_app("fl_studio"), fl_sync=False)
    assert not profile.uses_fl_sync
    assert any(c.source == ChannelSource("app", "fl_studio") for c in profile.channels)


def test_other_daws_get_master_and_their_own_session():
    profile = default_profile(ad.known_app("ableton"))
    assert profile.layout == "fl_studio" and not profile.uses_fl_sync
    assert profile.channels[0].source.type == "master"
    assert any(c.source == ChannelSource("app", "ableton") for c in profile.channels)


@pytest.mark.parametrize("key, layout, names", [
    ("obs", "streaming", ["Micro", "Game", "Discord", "Music"]),
    ("discord", "streaming", ["Micro", "Game", "Discord", "Music"]),
    ("audacity", "podcast", ["Voix", "Invité / Appel", "Musique / Jingles"]),
    ("zoom", "podcast", ["Voix", "Invité / Appel", "Musique / Jingles"]),
])
def test_voice_and_broadcast_profiles(key, layout, names):
    profile = default_profile(ad.known_app(key))
    assert profile.layout == layout and [c.name for c in profile.channels] == names
    assert profile.software == key


def test_podcast_guest_is_the_voice_chat_application_itself_or_to_choose():
    assert default_profile(ad.known_app("zoom")).channels[1].source.key == "zoom"
    assert default_profile(ad.known_app("audacity")).channels[1].source.key == ""


def test_generic_profile_without_software():
    profile = default_profile(None)
    assert profile.layout == "standard" and profile.software == ""
    assert profile.channels[0].source.type == "master"


@pytest.mark.parametrize("app", ad.KNOWN_APPS, ids=lambda a: a.key)
def test_every_default_profile_is_valid_and_round_trips(app):
    profile = default_profile(app)
    assert profile.layout in lm.LAYOUT_PRESETS and 1 <= len(profile.channels) <= pi.MAX_CHANNELS
    assert all(c.role in lm.ALL_ROLES or c.role is None for c in profile.channels)
    assert parse_profile(profile_to_dict(profile)) == profile


def test_assignment_of_an_application_to_a_channel():
    profile = default_profile(ad.known_app("obs"))
    assert profile.channels[1].source.key == ""
    updated = profile.with_assignment(1, "hl2.exe")
    assert updated.channels[1].source == ChannelSource("app", "hl2.exe")
    assert updated.channels[1].role == "game" and profile.channels[1].source.key == ""


# --------------------------------------------------------------------------- #
# Choix du profil
# --------------------------------------------------------------------------- #
def test_resolve_prefers_the_users_file(tmp_path):
    path = tmp_path / "studio.json"
    save_profile(parse_profile(sample()), path)
    profile, note = resolve_profile(str(path), active("discord"))
    assert profile.name == "Mon studio" and note == ""


def test_resolve_falls_back_to_the_automatic_profile(tmp_path):
    profile, note = resolve_profile("", active("obs"))
    assert profile.software == "obs" and note == ""
    profile, note = resolve_profile(str(tmp_path / "absent.json"), active("obs"))
    assert profile.software == "obs" and "absent.json" in note and "ignoré" in note
    profile, _ = resolve_profile("", ad.NO_APP)
    assert profile.name == "Général"


def test_module_has_no_qt_dependency():
    source = open(pi.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

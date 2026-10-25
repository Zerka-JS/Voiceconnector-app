"""Audio système du mode autonome : conversions, correspondance des sources, moteur simulé."""

import sys

import pytest

from core import system_audio as sa
from core.system_audio import (DemoSystemAudio, NullSystemAudio, SourceState, create_system_audio,
                               db_to_scalar, scalar_to_db, source_matches)


def test_scalar_to_db_and_back():
    assert scalar_to_db(1.0) == pytest.approx(0.0)
    assert scalar_to_db(0.5) == pytest.approx(-6.0206, abs=1e-3)
    assert scalar_to_db(0.0) == sa.SILENCE_DB and scalar_to_db(1e-9) == sa.SILENCE_DB
    for db in (-60.0, -20.0, -6.0, 0.0):
        assert scalar_to_db(db_to_scalar(db)) == pytest.approx(db, abs=1e-6)


def test_windows_cannot_amplify_an_application():
    assert db_to_scalar(0.0) == 1.0 and db_to_scalar(12.0) == 1.0
    assert db_to_scalar(-200.0) == pytest.approx(0.0, abs=1e-9) and db_to_scalar(-200.0) >= 0.0


@pytest.mark.parametrize("source, process, expected", [
    ("discord.exe", "Discord.exe", True),
    ("Discord", "discord.exe", True),            # clé du catalogue
    ("discord", "DiscordPTB.exe", True),
    ("chrome.exe", "chrome.exe", True),
    ("chrome", "chrome.exe", True),               # nom sans extension
    ("chrome.exe", "msedge.exe", False),
    ("ableton", "Ableton Live 12 Suite.exe", True),   # catalogue : exécutable à numéro de version
    ("fl_studio", "FL64.exe", True),
    ("fl_studio", "reaper.exe", False),
    ("system", "system", True),
    ("", "chrome.exe", False),
    ("chrome.exe", "", False),
])
def test_source_matching(source, process, expected):
    assert source_matches(source, process) == expected


def test_null_backend_is_unavailable_but_safe():
    audio = NullSystemAudio()
    assert not audio.available and audio.reason
    assert audio.app_states() == {}
    assert audio.endpoint_state(sa.SOURCE_CAPTURE) == SourceState()
    audio.set_app_volume("x", 0.5)
    audio.set_app_mute("x", True)
    audio.set_endpoint_volume(sa.SOURCE_MASTER, 0.5)
    audio.set_endpoint_mute(sa.SOURCE_MASTER, True)


def test_demo_backend_lists_apps_with_animated_levels():
    now = [0.0]
    audio = DemoSystemAudio(clock=lambda: now[0])
    first = audio.app_states()
    assert {"discord.exe", "spotify.exe", "chrome.exe", sa.SYSTEM_KEY} <= set(first)
    assert all(state.present for state in first.values())
    assert first["discord.exe"].name == "Discord"
    now[0] = 1.3
    assert audio.app_states()["discord.exe"].peak != first["discord.exe"].peak
    assert all(0.0 <= s.peak <= 1.0 for s in audio.app_states().values())


def test_demo_backend_applies_volume_and_mute():
    audio = DemoSystemAudio(clock=lambda: 0.0)
    audio.set_app_volume("discord.exe", 0.25)
    audio.set_app_volume("discord.exe", 7.0)
    assert audio.app_states()["discord.exe"].volume == 1.0
    audio.set_app_volume("discord.exe", -3.0)
    assert audio.app_states()["discord.exe"].volume == 0.0
    audio.set_app_mute("spotify.exe", True)
    assert audio.app_states()["spotify.exe"].muted and audio.app_states()["spotify.exe"].peak == 0.0
    audio.set_app_volume("inconnu.exe", 0.5)                  # ignoré, sans erreur
    audio.set_app_mute("inconnu.exe", True)
    assert "inconnu.exe" not in audio.app_states()


def test_demo_endpoints_are_the_microphone_and_the_output():
    audio = DemoSystemAudio(clock=lambda: 0.0)
    assert audio.endpoint_state(sa.SOURCE_CAPTURE).present and audio.endpoint_state(sa.SOURCE_MASTER).present
    audio.set_endpoint_volume(sa.SOURCE_CAPTURE, 0.3)
    audio.set_endpoint_mute(sa.SOURCE_MASTER, True)
    assert audio.endpoint_state(sa.SOURCE_CAPTURE).volume == 0.3
    assert audio.endpoint_state(sa.SOURCE_MASTER).muted
    assert audio.endpoint_state("autre") == SourceState()
    audio.set_endpoint_volume("autre", 0.5)                   # ignoré, sans erreur


def test_factory_never_raises_off_windows(monkeypatch):
    monkeypatch.setattr(sa.sys, "platform", "linux")
    audio = create_system_audio({})
    assert isinstance(audio, NullSystemAudio) and not audio.available
    # Windows sans pycaw : on bloque l'import de comtypes, qu'il soit installé ou non
    monkeypatch.setattr(sa.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "comtypes", None)
    fallback = create_system_audio({})
    assert isinstance(fallback, NullSystemAudio) and not fallback.available


@pytest.mark.skipif(sys.platform != "win32", reason="Core Audio : Windows seulement")
def test_real_windows_engine_reads_without_raising():
    """Lecture seule (aucun réglage modifié) : sur un poste sans carte son, comme le runner de CI,
    ou avec une vraie, le moteur Windows ne lève jamais d'exception."""
    audio = create_system_audio({})
    if not audio.available:
        pytest.skip("pycaw indisponible sur ce poste")
    assert isinstance(audio, sa.WindowsSystemAudio)
    assert isinstance(audio.app_states(), dict)
    for kind in (sa.SOURCE_MASTER, sa.SOURCE_CAPTURE):
        assert isinstance(audio.endpoint_state(kind), SourceState)


def test_module_has_no_qt_dependency():
    source = open(sa.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

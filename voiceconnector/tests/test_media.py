"""Musique en cours : moteurs (démo, indisponible, Windows avec de faux objets WinRT) et lecteur du Master.

Aucun vrai appel à Windows : les objets WinRT sont remplacés par de petites doublures."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from core import media  # noqa: E402
from core.media import (DemoMedia, MediaInfo, NullMedia, WindowsMedia, app_label,  # noqa: E402
                        create_media_backend, read_cover, winrt_wait)


class Operation:
    """Opération asynchrone WinRT : `wait()` renvoie un état (1 terminé, 3 en erreur, 0 pas fini)."""

    def __init__(self, result=None, status=1, error=None):
        self.result, self.status_value, self.error = result, status, error
        self.cancelled = False
        self.waited = []

    def wait(self, timeout):
        self.waited.append(timeout)
        return self.status_value

    def get_results(self):
        if self.error is not None:
            raise self.error
        return self.result

    def cancel(self):
        self.cancelled = True


def collect(backend):
    seen = []
    backend.changed.connect(seen.append)
    return seen


# --------------------------------------------------------------------------- #
# Noms des applications
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("app_id, label", [
    ("Spotify.exe", "Spotify"), ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Spotify"),
    ("MSEdge", "Edge"), ("chrome.exe", "Chrome"), ("308046B0AF4A39CB", "Firefox"),
    ("C:/Programmes/VLC/vlc.exe", "VLC"), ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
                                           "Lecteur multimédia"),
    ("Truc.exe", "Truc"), ("", ""), (None, ""),
])
def test_application_labels(app_id, label):
    assert app_label(app_id) == label


# --------------------------------------------------------------------------- #
# Moteurs
# --------------------------------------------------------------------------- #
def test_the_null_backend_is_unavailable_and_inert(qapp):
    backend = NullMedia()
    seen = collect(backend)
    assert not backend.available and backend.current is None and backend.reason
    backend.start()
    backend.play_pause()
    backend.next()
    backend.previous()
    backend.stop()
    assert seen == []


def test_the_demo_backend_plays_tracks_with_a_cover(qapp):
    backend = DemoMedia()
    seen = collect(backend)
    assert backend.available and backend.current is None
    backend.start()
    first = backend.current
    assert first.title == "Néons de minuit" and first.app == "Spotify" and first.playing
    assert first.cover.startswith(b"\x89PNG")                          # une vraie image PNG
    backend.next()
    assert backend.current.title == "Ligne de basse" and backend.current.app == "Deezer"
    backend.previous()
    backend.previous()                                                   # on revient en arrière, en boucle
    assert backend.current.title == "Retour au calme"
    backend.play_pause()
    assert backend.current.playing is False
    backend.play_pause()
    assert backend.current.playing is True
    assert len(seen) == 6 and seen[0] == first


def test_a_backend_publishes_only_real_changes(qapp):
    backend = DemoMedia()
    backend.start()
    seen = collect(backend)
    backend.start()                                                      # même morceau : rien de neuf
    backend.start()
    assert seen == []
    backend.next()
    assert len(seen) == 1


def test_the_factory_gives_an_unavailable_backend_outside_windows(qapp):
    backend = create_media_backend({}, platform="linux")
    assert isinstance(backend, NullMedia) and not backend.available


def test_the_factory_explains_missing_winrt_modules(qapp):
    backend = create_media_backend({}, platform="win32", find_spec=lambda name: None)
    assert isinstance(backend, NullMedia) and "winrt" in backend.reason

    def broken(_name):
        raise ImportError("cassé")

    assert isinstance(create_media_backend({}, platform="win32", find_spec=broken), NullMedia)


def test_the_factory_gives_the_windows_backend_when_the_modules_are_there(qapp):
    backend = create_media_backend({}, platform="win32", find_spec=lambda name: object())
    assert isinstance(backend, WindowsMedia) and backend.available
    assert set(media.WINRT_MODULES) == {"winrt.windows.media.control", "winrt.windows.storage.streams",
                                        "winrt.windows.foundation"}


# --------------------------------------------------------------------------- #
# WinRT (doublures)
# --------------------------------------------------------------------------- #
def test_winrt_wait_returns_the_result_of_a_finished_operation():
    operation = Operation(result="résultat")
    assert winrt_wait(operation, timeout=1.5) == "résultat" and operation.waited == [1.5]


def test_winrt_wait_raises_the_error_windows_reports():
    with pytest.raises(RuntimeError, match="refusé"):
        winrt_wait(Operation(status=3, error=RuntimeError("refusé")))


def test_winrt_wait_gives_up_on_an_operation_that_never_answers():
    operation = Operation(status=0)
    with pytest.raises(TimeoutError):
        winrt_wait(operation, timeout=0.05)
    assert operation.cancelled


def test_winrt_wait_also_handles_operations_without_wait():
    done = SimpleNamespace(status=1, get_results=lambda: "ok", cancel=lambda: None)
    assert winrt_wait(done, timeout=0.05) == "ok"
    cancelled = []
    stuck = SimpleNamespace(status=0, get_results=lambda: None, cancel=lambda: cancelled.append(True))
    with pytest.raises(TimeoutError):
        winrt_wait(stuck, timeout=0.05)
    assert cancelled == [True]


def test_no_cover_reference_means_no_cover():
    assert read_cover(None) == b""


def fake_session(*, title="  Néons  ", artist="Les Ondes", album="Nuit", app="Spotify.exe", status=4,
                 controls=None):
    props = SimpleNamespace(title=title, artist=artist, album_title=album, thumbnail="vignette")
    playback = SimpleNamespace(
        playback_status=status,
        controls=controls if controls is not None else SimpleNamespace(
            is_previous_enabled=False, is_next_enabled=True, is_play_pause_toggle_enabled=True))
    calls = []

    def action(name):
        def run():
            calls.append(name)
            return Operation()
        return run

    return SimpleNamespace(
        try_get_media_properties_async=lambda: Operation(props), get_playback_info=lambda: playback,
        source_app_user_model_id=app, calls=calls, try_toggle_play_pause_async=action("play_pause"),
        try_skip_next_async=action("next"), try_skip_previous_async=action("previous"))


def test_a_windows_session_becomes_a_media_info():
    info, thumbnail = WindowsMedia._read(fake_session())
    assert info == MediaInfo("Néons", "Les Ondes", "Nuit", "Spotify", True, can_previous=False,
                             can_next=True, can_play_pause=True)
    assert thumbnail == "vignette"
    paused, _ = WindowsMedia._read(fake_session(status=5))
    assert paused.playing is False


def test_nothing_is_reported_without_a_session_or_a_title():
    assert WindowsMedia._read(None) == (None, None)
    assert WindowsMedia._read(fake_session(title="   ")) == (None, None)


def test_missing_playback_controls_default_to_enabled():
    session = fake_session()
    session.get_playback_info = lambda: SimpleNamespace(playback_status=4, controls=None)
    info, _ = WindowsMedia._read(session)
    assert info.can_previous and info.can_next and info.can_play_pause


def test_commands_reach_the_current_session_in_order(qapp):
    backend = WindowsMedia()
    session = fake_session()
    backend.play_pause()
    backend.next()
    backend.previous()
    assert backend._run_commands(session) is True
    assert session.calls == ["play_pause", "next", "previous"]
    assert backend._run_commands(session) is False                       # rien de nouveau


def test_commands_are_dropped_when_nothing_is_playing(qapp):
    backend = WindowsMedia()
    backend.next()
    assert backend._run_commands(None) is False
    assert backend._commands == []


def test_a_refused_command_does_not_stop_the_others(qapp):
    backend = WindowsMedia()
    session = fake_session()

    def refuse():
        raise RuntimeError("refusé")

    session.try_skip_next_async = refuse
    backend.next()
    backend.previous()
    assert backend._run_commands(session) is True
    assert session.calls == ["previous"]


def test_stopping_a_backend_that_never_started_is_harmless(qapp):
    backend = WindowsMedia()
    backend.stop()
    backend.stop()

"""Détection des logiciels audio / DAW et règle d'affichage du bouton « Mode musical »
(logique pure : aucune interface Qt, aucun processus réel)."""

import pytest

from core.app_detector import (APPS_BY_KEY, AUTO, KNOWN_APPS, NO_APP, SOURCE_DETECTED,
                               SOURCE_NONE, SOURCE_SELECTED, ActiveApp, ActiveAppTracker,
                               AppDetector, AppKind, KnownApp, ProcessInfo, expand_env, known_app,
                               music_apps, music_button_state, music_mode_available,
                               process_stem, psutil_processes, resolve_active_app, voice_apps)

VALID_LAYOUTS = {"standard", "fl_studio", "streaming", "podcast"}


def procs(*names, exe=""):
    return lambda: [ProcessInfo(name, exe, pid) for pid, name in enumerate(names, start=100)]


# ------------------------------------------------------------------ catalogue
def test_catalog_is_consistent():
    keys = [app.key for app in KNOWN_APPS]
    assert len(keys) == len(set(keys)), "clés en double"
    for app in KNOWN_APPS:
        assert app.exe_names or app.exe_patterns, f"{app.key} : aucun nom de processus"
        assert app.layout in VALID_LAYOUTS, f"{app.key} : disposition inconnue {app.layout}"
        assert app.name and app.kind_label
        # Une application vocale, de diffusion ou un simple lecteur n'a jamais de partie musicale
        if not app.is_daw:
            assert not app.has_music_production, app.key
        else:
            assert app.has_music_production, app.key
    assert {a.key for a in music_apps()} == {a.key for a in KNOWN_APPS if a.is_daw}
    assert known_app("FL_Studio") is APPS_BY_KEY["fl_studio"]
    assert known_app("auto") is None and known_app("") is None and known_app(None) is None
    assert known_app("inconnu") is None


def test_every_listed_software_is_known():
    # Logiciels cités dans le cahier des charges
    for key in ("fl_studio", "reaper", "ableton", "cubase", "logic", "studio_one", "audacity",
                "premiere", "obs", "discord", "teamspeak3", "zoom", "spotify"):
        assert key in APPS_BY_KEY, key


@pytest.mark.parametrize("key, process, expected", [
    ("fl_studio", "FL64.exe", True), ("fl_studio", "fl64.EXE", True),
    ("fl_studio", "FL64 (scaled).exe", True), ("fl_studio", "FL.exe", True),
    ("fl_studio", "flash.exe", False), ("fl_studio", "", False),
    ("reaper", "reaper.exe", True), ("reaper", "Reaper", True),
    ("reaper", "reaper_helper.exe", False), ("reaper", "reaperd.exe", False),
    ("ableton", "Ableton Live 12 Suite.exe", True), ("ableton", "Ableton Live 11 Intro.exe", True),
    ("ableton", "Ableton Live 12 Lite.exe", True), ("ableton", "Ableton Index.exe", False),
    ("cubase", "Cubase13.exe", True), ("cubase", "Cubase 12.exe", True),
    ("cubase", "Cubase10.5.exe", True), ("cubase", "CubaseHelper.exe", False),
    ("nuendo", "Nuendo12.exe", True), ("studio_one", "Studio One.exe", True),
    ("logic", "Logic Pro X", True), ("ardour", "Ardour8.exe", True),
    ("obs", "obs64.exe", True), ("obs", "obs-browser-page.exe", False),
    ("discord", "Discord.exe", True), ("discord", "DiscordPTB.exe", True),
    ("discord", "Discord Update.exe", False),
    ("teamspeak3", "ts3client_win64.exe", True), ("audacity", "audacity.exe", True),
    ("premiere", "Adobe Premiere Pro.exe", True), ("zoom", "Zoom.exe", True),
    ("spotify", "Spotify.exe", True),
])
def test_process_name_matching(key, process, expected):
    assert APPS_BY_KEY[key].matches(process) is expected


def test_process_stem_normalisation():
    assert process_stem("  FL64.EXE ") == "fl64"
    assert process_stem("Logic Pro X") == "logic pro x"
    assert process_stem(None) == ""


# ----------------------------------------------------------- règle du bouton
VOICE_ONLY = ["discord", "zoom", "spotify", "obs", "audacity", "premiere", "teamspeak3",
              "teams", "skype", "vlc", "streamlabs", "resolve", "audition"]
MUSICAL = ["fl_studio", "ableton", "reaper", "cubase", "nuendo", "studio_one", "logic",
           "bitwig", "pro_tools", "reason", "cakewalk", "ardour", "lmms"]


@pytest.mark.parametrize("key", VOICE_ONLY)
def test_music_mode_hidden_for_voice_and_broadcast_apps(key):
    app = APPS_BY_KEY[key]
    assert music_mode_available(app) is False
    for active in (ActiveApp(app, SOURCE_SELECTED, True), ActiveApp(app, SOURCE_DETECTED, True)):
        assert music_mode_available(active) is False
        state = music_button_state(active, music_on=True, sync_connected=True)
        assert state.visible is False and state.checked is False and state.enabled is False


@pytest.mark.parametrize("key", MUSICAL)
def test_music_mode_shown_for_music_apps(key):
    app = APPS_BY_KEY[key]
    assert music_mode_available(app) is True
    active = ActiveApp(app, SOURCE_DETECTED, True)
    assert music_mode_available(active) is True
    state = music_button_state(active, music_on=False)
    assert state.visible and state.enabled and not state.checked
    assert app.name in state.tooltip
    assert music_button_state(active, music_on=True).checked is True


def test_music_mode_hidden_without_application():
    assert music_mode_available(None) is False
    assert music_mode_available(NO_APP) is False
    assert music_button_state(NO_APP, music_on=True).visible is False


def test_music_button_tooltip_tells_the_sync_state():
    fl = ActiveApp(APPS_BY_KEY["fl_studio"], SOURCE_DETECTED, True)
    assert "connectée" in music_button_state(fl, False, sync_connected=True).tooltip
    assert "en attente" in music_button_state(fl, False, sync_connected=False).tooltip
    reaper = ActiveApp(APPS_BY_KEY["reaper"], SOURCE_DETECTED, True)
    assert "pas de synchronisation" in music_button_state(reaper, False).tooltip
    idle = ActiveApp(APPS_BY_KEY["fl_studio"], SOURCE_SELECTED, False)
    assert "n'est pas détecté" in music_button_state(idle, False).tooltip


# ------------------------------------------------------------------ détection
def test_scan_finds_known_apps_by_priority():
    detector = AppDetector(processes=procs("explorer.exe", "Discord.exe", "Discord.exe", "FL64.exe",
                                           "chrome.exe", "obs64.exe", "Spotify.exe"))
    found = detector.scan()
    assert [d.key for d in found] == ["fl_studio", "obs", "discord", "spotify"]
    discord = next(d for d in found if d.key == "discord")
    assert len(discord.pids) == 2


def test_scan_keeps_the_executable_path():
    detector = AppDetector(processes=procs("FL64.exe", exe=r"C:\FL\FL64.exe"))
    (found,) = detector.scan()
    assert found.exe == r"C:\FL\FL64.exe" and found.pids == (100,)


def test_scan_survives_a_failing_process_list():
    def broken():
        raise PermissionError("accès refusé")

    assert AppDetector(processes=broken).scan() == []


def test_one_process_is_one_application():
    custom = (KnownApp("a", "A", AppKind.DAW, exe_names=("same.exe",), has_music_production=True),
              KnownApp("b", "B", AppKind.VOICE_CHAT, exe_names=("same.exe",)))
    found = AppDetector(catalog=custom, processes=procs("same.exe")).scan()
    assert [d.key for d in found] == ["a"]


def test_refresh_reports_changes():
    state = {"names": ["explorer.exe"]}
    detector = AppDetector(processes=lambda: [ProcessInfo(n) for n in state["names"]])
    assert detector.refresh() == ([], True)          # premier passage : état initial
    assert detector.refresh() == ([], False)
    state["names"] = ["reaper.exe"]
    found, changed = detector.refresh()
    assert changed and [d.key for d in found] == ["reaper"]
    assert detector.refresh()[1] is False
    state["names"] = []
    assert detector.refresh() == ([], True)


def test_voice_apps_filter():
    detector = AppDetector(processes=procs("FL64.exe", "Discord.exe", "obs64.exe", "Spotify.exe",
                                           "audacity.exe"))
    assert [d.key for d in voice_apps(detector.scan())] == ["obs", "audacity", "discord"]


def test_psutil_processes_lists_this_process():
    pytest.importorskip("psutil")
    names = {p.name.lower() for p in psutil_processes()}
    assert any("python" in name or "pytest" in name for name in names)


# --------------------------------------------------------------- installation
def test_installed_apps_from_standard_folders(tmp_path):
    program_files = tmp_path / "Program Files"
    (program_files / "Image-Line" / "FL Studio 20").mkdir(parents=True)
    (program_files / "Image-Line" / "FL Studio 21").mkdir(parents=True)
    (program_files / "REAPER (x64)").mkdir(parents=True)
    (program_files / "Audacity").mkdir(parents=True)
    env = {"ProgramFiles": str(program_files)}      # pas de ProgramFiles(x86), LOCALAPPDATA…
    detector = AppDetector(processes=procs(), environ=env)
    found = {item.app.key: item.folder for item in detector.installed()}
    assert set(found) == {"fl_studio", "reaper", "audacity"}
    assert found["fl_studio"].endswith("FL Studio 21"), "la version la plus récente d'abord"
    assert detector.find_install_dir(APPS_BY_KEY["ableton"]) is None


def test_expand_env():
    assert expand_env(r"%A%\x", {"A": "C:/a"}) == r"C:/a\x"
    assert expand_env(r"%A%\%B%", {"A": "1"}) is None       # variable absente : emplacement ignoré
    assert expand_env("sans variable", {}) == "sans variable"


# ---------------------------------------------------------------- logiciel actif
def detected(*names):
    return AppDetector(processes=procs(*names)).scan()


def test_auto_prefers_the_music_application():
    active = resolve_active_app(detected("Discord.exe", "FL64.exe", "obs64.exe"), AUTO)
    assert active.key == "fl_studio" and active.source == SOURCE_DETECTED and active.running
    assert music_mode_available(active)


def test_auto_without_known_application():
    assert resolve_active_app(detected("explorer.exe"), AUTO) == NO_APP
    assert NO_APP.source == SOURCE_NONE and not NO_APP.has_music_production
    assert NO_APP.name == "Aucun logiciel reconnu" and NO_APP.suggested_layout is None


def test_auto_with_only_a_voice_application_hides_the_music_button():
    active = resolve_active_app(detected("Discord.exe", "Spotify.exe"))
    assert active.key == "discord"
    assert music_button_state(active, music_on=False).visible is False


def test_selection_wins_even_when_the_application_is_not_running():
    active = resolve_active_app(detected("Discord.exe"), "fl_studio")
    assert active.key == "fl_studio" and active.source == SOURCE_SELECTED
    assert active.running is False
    assert music_button_state(active, False).visible is True       # sélectionné : préparé d'avance
    chosen_voice = resolve_active_app(detected("FL64.exe", "Discord.exe"), "discord")
    assert chosen_voice.key == "discord" and chosen_voice.running
    assert music_button_state(chosen_voice, False).visible is False


@pytest.mark.parametrize("selected", ["", "auto", "AUTO", None, "n-existe-pas"])
def test_invalid_or_auto_selection_falls_back_to_detection(selected):
    active = resolve_active_app(detected("reaper.exe"), selected)
    assert active.key == "reaper" and active.source == SOURCE_DETECTED


def test_suggested_layout_follows_the_application():
    assert resolve_active_app(detected("FL64.exe")).suggested_layout == "fl_studio"
    assert resolve_active_app(detected("obs64.exe")).suggested_layout == "streaming"
    assert resolve_active_app(detected("audacity.exe")).suggested_layout == "podcast"


def test_tracker_reports_changes_only():
    tracker = ActiveAppTracker()
    assert tracker.update(detected()) is False                       # rien → rien
    assert tracker.update(detected("FL64.exe")) is True
    assert tracker.active.key == "fl_studio"
    assert tracker.update(detected("FL64.exe", "Discord.exe")) is False
    assert tracker.update(detected("Discord.exe")) is True           # FL Studio fermé
    assert tracker.active.key == "discord"
    assert tracker.update(detected("Discord.exe"), selected="fl_studio") is True
    assert tracker.active.running is False
    assert tracker.update(detected("Discord.exe", "FL64.exe")) is True   # il démarre : running
    assert tracker.active.running is True
    assert tracker.update(detected("Discord.exe"), selected="auto") is True
    assert tracker.active.key == "discord"

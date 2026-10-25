"""Garde-fou de nommage : plus aucune trace de l'ancien nom (ni de son préfixe de deux lettres) dans le
projet — noms de fichiers et de dossiers, classes, variables, fonctions, métadonnées, textes de l'interface.

Tout s'appelle « VoiceConnector » (ou « voiceconnector » dans le code)."""

import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
REPO = APP_DIR.parent if (APP_DIR.parent / ".github").is_dir() else APP_DIR

# Les motifs sont assemblés ici pour que ce fichier ne se signale pas lui-même.
OLD_NAME = re.compile("re" + "ka", re.IGNORECASE)
OLD_PREFIX = re.compile(r"\b[Rr][Kk](?:_|\b)|\b[Rr]k[A-Z]|(?<=[a-z])_[Rr][Kk]\b|\b[Rr][Kk]\.")
SKIPPED_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv",
                "node_modules", "build", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".spec", ".toml", ".cfg", ".ini", ".lua",
                 ".html", ".css", ".bat", ".ps1", ".sh", ".iss", ".rc", ".gitignore", ".cmd"}


def project_files():
    for path in sorted(REPO.rglob("*")):
        relative = path.relative_to(REPO)
        if SKIPPED_DIRS & set(relative.parts):
            continue
        yield path, relative


def test_no_file_or_folder_carries_the_old_name():
    offenders = [str(relative) for _, relative in project_files()
                 if OLD_NAME.search(str(relative)) or OLD_PREFIX.search(str(relative))]
    assert offenders == []


def test_no_text_file_mentions_the_old_name():
    offenders = []
    for path, relative in project_files():
        if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
                ".gitignore", "LICENSE", "Makefile"}):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if OLD_NAME.search(line) or OLD_PREFIX.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()[:100]}")
    assert offenders == []


def test_the_application_folder_is_called_voiceconnector():
    assert APP_DIR.name == "voiceconnector"
    assert (APP_DIR / "main.py").is_file()


@pytest.mark.parametrize("module", ["core.session_coordinator", "core.app_detector", "core.layout_manager",
                                    "core.themes", "core.session_mode", "core.mixer_router"])
def test_public_names_are_voiceconnector_names(module):
    import importlib

    names = dir(importlib.import_module(module))
    assert [n for n in names if OLD_NAME.search(n) or OLD_PREFIX.search(n)] == []

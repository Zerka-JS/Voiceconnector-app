# -*- mode: python ; coding: utf-8 -*-
#
# Build de l'exécutable Windows (un seul fichier, sans console) :
#     python -m PyInstaller --noconfirm --clean build.spec
# Résultat : dist/VoiceConnector.exe
#
# Vérification du build (rend la console en mode démo dans une image) :
#     "dist/VoiceConnector.exe" --selftest selftest/console.png

import os
import re

try:
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct,
                                                     VSVersionInfo)
except ImportError:             # build hors Windows : pas de ressource de version
    VSVersionInfo = None

# Si SPECPATH n'est pas défini (par exemple lors d'un pytest), on utilise le dossier courant
if 'SPECPATH' not in globals():
    SPECPATH = os.path.dirname(os.path.abspath(__file__))

# Version et développeurs lus dans core/config.py (sans importer l'application)
with open(os.path.join(SPECPATH, "core", "config.py"), encoding="utf-8") as source:
    _config_source = source.read()


def _constant(name):
    return re.search(rf'^{name} = "([^"]*)"', _config_source, re.MULTILINE).group(1)


APP_VERSION = _constant("APP_VERSION")
APP_AUTHORS = "Zerka"  # Forcé à Zerka
_numbers = tuple(int(n) for n in re.findall(r"\d+", APP_VERSION))[:4]
_numbers += (0,) * (4 - len(_numbers))

# Propriétés de VoiceConnector.exe (clic droit > Propriétés > Détails)
version_info = None if VSVersionInfo is None else VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers, prodvers=_numbers),
    kids=[
        StringFileInfo([StringTable("040C04B0", [          # français, Unicode
            StringStruct("CompanyName", "Zerka"),
            StringStruct("FileDescription", "VoiceConnector"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", "VoiceConnector"),
            StringStruct("LegalCopyright", "© Zerka"),
            StringStruct("OriginalFilename", "VoiceConnector.exe"),
            StringStruct("ProductName", "VoiceConnector"),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("Comments", "Console de mixage (Voicemeeter ou autonome), par Zerka"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("config.json", "."),
        ("assets", "assets"),
        *([("build_info.json", ".")] if os.path.isfile(os.path.join(SPECPATH, "build_info.json")) else []),
    ],
    hiddenimports=["voicemeeterlib", "core.demo", "pycaw.utils", "pycaw.api.endpointvolume",
                   "mido", "mido.backends.rtmidi", "rtmidi"],       # synchronisation FL Studio (facultative)
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VoiceConnector",
    icon="assets/icon.ico",
    version=version_info,
    console=False,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
)
"""Configuration commune des tests : chemins, Qt hors écran, %APPDATA% temporaire."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def appdata(tmp_path, monkeypatch):
    """Chaque test a son propre %APPDATA% : la vraie config n'est jamais touchée."""
    path = tmp_path / "appdata"
    path.mkdir()
    monkeypatch.setenv("APPDATA", str(path))
    return path


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from ui import styles

    app = QApplication.instance() or QApplication([])
    styles.apply_theme(app)
    return app


def dispose_windows(app, keep=()) -> None:
    """Détruit les fenêtres (sans parent) que Qt n'a pas encore détruites, par la voie normale de Qt.

    Une fenêtre sans parent appartient à Python, et ses connexions à des lambdas qui la citent
    (`signal.connect(lambda: self.refresh())`) la gardent en vie après la fin du test : PySide la
    détruit alors à l'arrêt de l'interpréteur, d'un coup, et la lambda libérée en pleine destruction
    supprime une seconde fois la même fenêtre (« shared QObject was deleted directly », abort
    de pytest après un résumé pourtant vert). `deleteLater` + envoi des suppressions en attente les
    détruit tant que QApplication existe, comme le fait Qt pour toute fenêtre fermée."""
    import gc

    from PySide6.QtCore import QEvent

    for widget in app.topLevelWidgets():
        if widget in keep:
            continue
        try:
            widget.close()
        except Exception:             # une fermeture qui échoue ne doit pas masquer le résultat du test
            pass
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


@pytest.fixture(autouse=True)
def qt_windows():
    """Chaque test laisse Qt comme il l'a trouvé : ses fenêtres sont détruites à sa fin."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield
        return
    app = QApplication.instance()
    before = list(app.topLevelWidgets()) if app is not None else []
    yield
    app = QApplication.instance()
    if app is not None:
        dispose_windows(app, keep=before)


@pytest.fixture(scope="session", autouse=True)
def qt_teardown():
    """Fin de session : tout ce qui reste est détruit avant que Python ne ferme (voir dispose_windows)."""
    yield
    if os.environ.get("VC_NO_QT_TEARDOWN"):             # pour comparer avec et sans (diagnostic CI)
        return
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is not None:
        dispose_windows(app)


@pytest.fixture
def cfg(appdata):
    from core import config

    return config.load_config()


def pump(app, seconds: float) -> None:
    """Fait tourner la boucle Qt (les signaux venant d'autres threads arrivent en file)."""
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def mixer(qapp, cfg):
    """Console en démo (Voicemeeter et REAPER simulés), affichée."""
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui.main_window import MainWindow

    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False,
                        apps=DemoAppVolumes())
    window.resize(1920, 1040)
    window.show()
    pump(qapp, 0.2)
    yield window, vm, reaper
    window.close()

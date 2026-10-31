"""Diagnostic CI temporaire : quelle partie de StudioSetupDialog fait planter Qt à la sortie ?"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.getcwd())

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QVBoxLayout  # noqa: E402

stage = sys.argv[1]
app = QApplication([])
from ui import studio_style as st  # noqa: E402
from ui import styles  # noqa: E402

styles.apply_theme(app)
keep = []


def base(with_sheet=True, with_combo=False, with_labels=False, with_buttons=False):
    dialog = QDialog()
    if with_sheet:
        dialog.setStyleSheet(st.stylesheet())
    column = QVBoxLayout(dialog)
    if with_combo:
        combo = QComboBox()
        for name in ("A", "B", "C"):
            combo.addItem(name, name)
        combo.activated.connect(lambda _row: dialog.update())
        column.addWidget(combo)
    if with_labels:
        for _ in range(4):
            label = QLabel("<b>x</b> <a href='https://example.org'>lien</a>")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            column.addWidget(label)
    if with_buttons:
        button = QPushButton("ok")
        button.clicked.connect(lambda _checked=False: dialog.update())
        column.addWidget(button)
    keep.append(dialog)
    return dialog


class NoPorts:
    @staticmethod
    def get_output_names():
        return []

    @staticmethod
    def get_input_names():
        return []


def real():
    from core import studio_session as ss
    from core.demo_daw import DemoDaw
    from ui import studio_window as sw

    session = ss.StudioSession({}, scan=lambda: [], demo_factory=DemoDaw)
    tmp = Path(tempfile.mkdtemp())
    dialog = sw.StudioSetupDialog(session, documents=tmp, source=tmp / "absent.py", mido_module=NoPorts())
    keep.append(dialog)
    return dialog, session


if stage == "rien":
    pass
elif stage == "dialog":
    base(True)
elif stage == "dialog_sans_style":
    base(False)
elif stage == "combo":
    base(True, with_combo=True)
elif stage == "labels":
    base(True, with_labels=True)
elif stage == "boutons":
    base(True, with_buttons=True)
elif stage == "complet":
    real()
elif stage == "complet_ferme":
    dialog, _session = real()
    dialog.close()
elif stage == "complet_detruit":
    from shiboken6 import delete
    dialog, session = real()
    dialog.close()
    delete(dialog)
    keep.clear()
elif stage == "complet_detruit_session":
    from shiboken6 import delete
    dialog, session = real()
    dialog.close()
    delete(dialog)
    session.close()
    keep.clear()
print("fin", stage, flush=True)
sys.exit(0)

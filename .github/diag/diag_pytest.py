"""Diagnostic CI temporaire : pytest plante-t-il encore à la sortie (après un résumé vert) ?"""
import os
import subprocess
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent.parent / "voiceconnector")
PYTEST = [sys.executable, "-X", "faulthandler", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
RUNS = {
    "dialogue_d_aide": ["tests/test_studio_ui.py", "-k", "setup_dialog"],
    "tout_le_studio": ["tests/test_studio_ui.py", "tests/test_studio_widgets.py", "tests/test_studio_session.py"],
}


def emit(level: str, title: str, text: str) -> None:
    text = text.strip()[-3600:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{text}", flush=True)


def run(cmd):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace", timeout=900)
    return done.returncode, (done.stdout or "") + "\n" + (done.stderr or "")


failed = []
for title, args in RUNS.items():
    code, out = run(PYTEST + args)
    emit("notice" if code == 0 else "error", f"pytest_{title}", f"exit={code}\n" + out[-900:])
    if code != 0:
        failed.append(args)

if failed:
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "gdb"], capture_output=True)
    code, out = run(["gdb", "-batch", "-ex", "set pagination off", "-ex", "run", "-ex", "bt 40", "--args",
                     *PYTEST, *failed[0]])
    frames = [line[:150] for line in out.splitlines() if line.startswith("#")]
    emit("error", "gdb_bt", "\n".join(frames[:24]) or out[-1500:])

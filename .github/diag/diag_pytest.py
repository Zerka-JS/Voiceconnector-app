"""Diagnostic CI temporaire : où plante pytest à la sortie (après un résumé vert) ?"""
import os
import subprocess
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent.parent / "voiceconnector")
BASE = [sys.executable, "-X", "faulthandler", "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "tests/test_studio_ui.py", "-k", "setup_dialog"]


def emit(level: str, title: str, text: str) -> None:
    text = text.strip()[-3600:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title}::{text}", flush=True)


def run(cmd, extra=None):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", **(extra or {}))
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace", timeout=600)
    return done.returncode, (done.stdout or "") + "\n" + (done.stderr or "")


code, out = run(BASE)
emit("notice", "pytest_3_tests", f"exit={code}\n" + out[-1400:])

code, out = run(BASE, {"VC_NO_QT_TEARDOWN": "1"})
emit("notice", "pytest_sans_teardown", f"exit={code}")

code, out = run(BASE[:-1] + ["explains_the_chosen"])
emit("notice", "pytest_1_test", f"exit={code}")

subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "gdb"], capture_output=True)
code, out = run(["gdb", "-batch", "-ex", "set pagination off", "-ex", "run", "-ex", "bt 45", "--args", *BASE])
frames = [line[:170] for line in out.splitlines() if line.startswith("#") or "signal" in line.lower()]
emit("error", "gdb_bt", f"exit={code}\n" + "\n".join(frames[:60]) if frames else out[-1500:])

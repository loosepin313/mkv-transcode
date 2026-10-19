"""Tests for the helper shell scripts."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVATE = ROOT / "venv-activate.sh"


def test_activate_is_valid_bash():
    proc = subprocess.run(["bash", "-n", str(ACTIVATE)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_activate_refuses_to_run_as_a_program():
    proc = subprocess.run(["bash", str(ACTIVATE)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "source" in proc.stderr


def test_activate_marks_the_prompt_and_restores_it(tmp_path):
    venv = tmp_path / "venv"
    subprocess.run(["python3", "-m", "venv", str(venv)], check=True)
    script = (
        "PS1='orig> '; "
        "source '%s'; "
        'echo "marked [$PS1]"; '
        "deactivate; "
        'echo "after [$PS1]"' % ACTIVATE
    )
    env = dict(os.environ, MKVTC_VENV=str(venv))
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "marked [(mkv-transcode) orig> ]" in proc.stdout
    assert "after [orig> ]" in proc.stdout

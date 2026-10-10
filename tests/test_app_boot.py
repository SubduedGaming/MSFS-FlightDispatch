"""Boots the real server application entry point headlessly, twice (first start, then a normal start)."""
import json
import os
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent("""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from skydispatch.server_gui import app
    from skydispatch.ui import context

    original = context.AppContext.start_sim

    def start_sim_then_quit_soon(self):
        QTimer.singleShot(2500, lambda: QApplication.instance().quit())
        original(self)
    context.AppContext.start_sim = start_sim_then_quit_soon
    print("EXIT", app.run(["skydispatch"{extra}]))
""")


def _run(tmp_path, extra=""):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SKYDISPATCH_NO_UPDATE_CHECK="1", SKYDISPATCH_HOME=str(tmp_path / "h"),
               PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
    return subprocess.run([sys.executable, "-c", SCRIPT.format(extra=extra)],
                          capture_output=True, text=True, timeout=60, env=env)


def test_first_start_then_normal_start(tmp_path):
    r = _run(tmp_path)
    assert "EXIT 0" in r.stdout, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert (tmp_path / "h" / "data" / "career.db").exists()
    saved = json.loads((tmp_path / "h" / "config" / "settings.json").read_text())
    assert saved["remote"]["enabled"] is True and saved["ui"]["server_mode"] is True      # the phone API is switched on

    r2 = _run(tmp_path, ', "--minimized"')              # settings load from disk; started hidden, as from Windows startup
    assert "EXIT 0" in r2.stdout, r2.stdout + r2.stderr
    assert "Traceback" not in r2.stderr, r2.stderr

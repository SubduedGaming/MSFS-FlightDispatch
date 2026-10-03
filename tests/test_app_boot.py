"""Boots the real application entry point headlessly, twice (first run, then a normal start)."""
import os
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent("""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from skydispatch.ui import app, context, wizard

    def quit_soon():
        QTimer.singleShot(2500, lambda: QApplication.instance().quit())

    FIRST_RUN = {first_run}
    if FIRST_RUN:
        def fake_exec(self):                       # click through the wizard
            self.pilot.name.setText("Boot Test")
            self.sim.r_demo.setChecked(True)
            self.accept()
            quit_soon()
            return 1
        wizard.SetupWizard.exec = fake_exec
    else:
        def no_wizard(self):
            raise AssertionError("wizard shown again")
        wizard.SetupWizard.exec = no_wizard
        original = context.AppContext.start_sim
        context.AppContext.start_sim = lambda self: (quit_soon(), original(self))
    print("EXIT", app.run(["skydispatch"]))
""")


def _run(tmp_path, first_run):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SKYDISPATCH_HOME=str(tmp_path / "h"),
               PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
    return subprocess.run([sys.executable, "-c", SCRIPT.format(first_run=first_run)],
                          capture_output=True, text=True, timeout=60, env=env)


def test_first_run_then_normal_start(tmp_path):
    r = _run(tmp_path, True)
    assert "EXIT 0" in r.stdout, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert (tmp_path / "h" / "data" / "career.db").exists()
    assert (tmp_path / "h" / "config" / "settings.json").exists()

    r2 = _run(tmp_path, False)          # settings + career load from disk; no wizard
    assert "EXIT 0" in r2.stdout, r2.stdout + r2.stderr
    assert "Traceback" not in r2.stderr, r2.stderr

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import APP_NAME, __version__
from ..core import paths
from . import fmt
from .widgets import heading, muted


class AboutDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(f"{APP_NAME} {__version__}"))
        lay.addWidget(muted("A career-mode add-on for Microsoft Flight Simulator 2020 and 2024 with an AI "
                            "flight dispatcher, job market, hangar and logbook."))
        info = QTextBrowser()
        info.setMaximumHeight(170)
        tts_ok, tts_msg = ctx.voice.tts_status()
        stt_ok, stt_msg = ctx.voice.stt_status()
        info.setHtml(f"<p>Python {platform.python_version()} &middot; Qt/PySide {pyside_version}<br>"
                     f"{platform.system()} {platform.release()}<br>"
                     f"Data: {paths.data_dir()}<br>Logs: {paths.log_dir()}<br>"
                     f"Voice out: {tts_msg}<br>Voice in: {stt_msg}</p>"
                     "<p>Licensed under the Apache License 2.0. Not affiliated with Microsoft or Asobo Studio. "
                     "Airport data seeded from public sources; optional worldwide data from OurAirports (public domain).</p>")
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class FlightResultDialog(QDialog):
    def __init__(self, ctx, settlement, parent=None):
        super().__init__(parent)
        s = ctx.settings
        m, sc = settlement.metrics, settlement.score
        self.setWindowTitle("Flight complete")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        title = {"completed": "Flight complete", "diverted": "Wrong destination", "crashed": "Aircraft lost",
                 "aborted": "Flight abandoned"}.get(m.outcome, "Flight ended")
        lay.addWidget(heading(f"{title}  -  grade {sc.grade}"))
        lay.addWidget(muted(settlement.job.title if settlement.job else "Free flight (no contract)"))
        form = QFormLayout()
        form.addRow("Score:", QLabel(f"{sc.score:.0f} / 100"))
        form.addRow("Block time:", QLabel(fmt.duration(m.block_min)))
        form.addRow("Distance:", QLabel(fmt.dist(s, m.distance_nm)))
        if m.landing_fpm is not None:
            form.addRow("Landing:", QLabel(f"{abs(m.landing_fpm):.0f} fpm, {m.landing_g:.2f} G"))
        form.addRow("Payout:", QLabel(fmt.money(s, settlement.payout)))
        form.addRow("Operating cost:", QLabel(fmt.money(s, -settlement.costs)))
        form.addRow("Net:", QLabel(fmt.money(s, settlement.payout - settlement.costs, signed=True)))
        lay.addLayout(form)
        if settlement.notes:
            box = QTextBrowser()
            box.setMaximumHeight(120)
            box.setHtml("<ul>" + "".join(f"<li>{n}</li>" for n in settlement.notes) + "</ul>")
            lay.addWidget(box)
        lay.addWidget(muted("Your dispatcher will debrief you in the Dispatcher tab."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class UninstallDialog(QDialog):
    """Explains/launches uninstall and can remove user data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Uninstall {APP_NAME}")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(heading(f"Uninstall {APP_NAME}"))
        lay.addWidget(muted(self._instructions()))
        self.launch = QPushButton("Launch the uninstaller")
        self.launch.setObjectName("primary")
        self.launch.clicked.connect(self._launch)
        self.launch.setVisible(self._uninstaller() is not None)
        lay.addWidget(self.launch)
        lay.addWidget(muted(f"Your career data is stored separately in:\n{paths.data_dir()}\n"
                            "Uninstalling the program does not delete it, so reinstalling keeps your career."))
        wipe = QPushButton("Delete my career data and settings now...")
        wipe.setObjectName("danger")
        wipe.clicked.connect(self._wipe)
        lay.addWidget(wipe)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _uninstaller() -> Path | None:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            exe = Path(sys.executable).parent / "unins000.exe"
            return exe if exe.exists() else None
        return None

    @staticmethod
    def _instructions() -> str:
        if sys.platform == "win32":
            return ("Use 'Uninstall SkyDispatch' in the Start menu, or Settings > Apps > Installed apps.")
        if sys.platform == "darwin":
            return ("Quit SkyDispatch, then drag SkyDispatch.app from Applications to the Trash. "
                    "A tool 'Uninstall SkyDispatch.command' is also included in the disk image.")
        return ("Use your software centre, or run: sudo apt remove skydispatch (Debian/Ubuntu). "
                "AppImage users can simply delete the file.")

    def _launch(self) -> None:
        exe = self._uninstaller()
        if exe:
            subprocess.Popen([str(exe)])  # noqa: S603
            QMessageBox.information(self, "Uninstall", "The uninstaller was started. SkyDispatch will now close.")
            os._exit(0)

    def _wipe(self) -> None:
        if QMessageBox.warning(self, "Delete data", "This permanently deletes your career, logbook, settings, "
                               "downloaded voices and logs, then closes SkyDispatch. Continue?",
                               QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes:
            return
        import shutil
        for d in (paths.data_dir(), paths.config_dir()):
            shutil.rmtree(d, ignore_errors=True)
        QMessageBox.information(self, "Done", "Your data was deleted. SkyDispatch will close now.")
        os._exit(0)

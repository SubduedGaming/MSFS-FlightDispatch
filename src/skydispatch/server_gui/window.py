from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon, QTabWidget)

from .. import APP_NAME, __version__
from ..core import paths
from ..ui import theme
from ..ui.context import AppContext
from ..ui.dialogs import AboutDialog, UninstallDialog, UpdateDialog
from ..ui.widgets import StatusDot, Toast
from .logs_tab import LogsTab
from .phones_tab import PhonesTab
from .settings_page import SettingsPage
from .status_tab import StatusTab

log = logging.getLogger(__name__)


class AdminWindow(QMainWindow):
    """The server's small control window. Closing it hides it to the tray (when there is one); Quit really exits."""

    def __init__(self, ctx: AppContext, icon: QIcon | None = None, use_tray: bool | None = None):
        super().__init__()
        self.ctx = ctx
        self.quitting = False
        self.setWindowTitle(f"{APP_NAME} server {__version__}")
        self.resize(900, 700)
        self.setMinimumSize(760, 560)
        if icon is not None:
            self.setWindowIcon(icon)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.status_tab = StatusTab(ctx)
        self.phones_tab = PhonesTab(ctx)
        self.settings_tab = SettingsPage(ctx)
        self.logs_tab = LogsTab(ctx)
        for tab, label in ((self.status_tab, "Status"), (self.phones_tab, "Phones"), (self.settings_tab, "Settings"),
                           (self.logs_tab, "Logs")):
            self.tabs.addTab(tab, label)
        self.status_tab.open_phones.connect(lambda: self.tabs.setCurrentWidget(self.phones_tab))
        self.settings_tab.theme_changed.connect(self.apply_theme)

        self.toast = Toast(self)
        ctx.toast.connect(self.toast.show_message)
        sb = self.statusBar()
        self.sim_dot, self.ai_dot = StatusDot(), StatusDot()
        sb.addWidget(self.sim_dot)
        sb.addWidget(self.ai_dot)
        ctx.sim_status.connect(self._sim_status)
        ctx.ai_status.connect(self._ai_status)
        ctx.update_available.connect(self._update_found)
        ctx.loadout_report.connect(self._show_loadout_report)
        self._update_dlg = None
        self._sim_status(ctx.provider.status if ctx.provider else "disconnected", "")
        self.ai_dot.set_state("off", "AI: not checked")
        self._build_menu()

        self.tray: QSystemTrayIcon | None = None
        if use_tray if use_tray is not None else QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray(icon)

    # ------------------------------------------------------------------ tray
    def _build_tray(self, icon: QIcon | None) -> None:
        self.tray = QSystemTrayIcon(icon or self.windowIcon(), self)
        self.tray.setToolTip(f"{APP_NAME} server")
        menu = QMenu(self)
        menu.addAction(self._act("Open SkyDispatch", self.bring_to_front))
        menu.addAction(self._act("Pair a phone...", self.show_pairing))
        menu.addSeparator()
        menu.addAction(self._act("Quit", self.quit_app))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.bring_to_front()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_pairing(self) -> None:
        self.tabs.setCurrentWidget(self.phones_tab)
        self.bring_to_front()

    # ---------------------------------------------------------------- status
    def _sim_status(self, status: str, message: str) -> None:
        mode = {"simulated": "Simulated", "simconnect": "MSFS", "bridge": "Bridge"}.get(self.ctx.settings.sim.mode, "Sim")
        state = {"connected": "ok", "connecting": "busy", "error": "bad"}.get(status, "off")
        label = {"connected": "connected", "connecting": "waiting", "error": "error"}.get(status, "disconnected")
        self.sim_dot.set_state(state, f"{mode}: {label}")
        self.sim_dot.setToolTip(message)

    def _ai_status(self, ok: bool, message: str) -> None:
        self.ai_dot.set_state("ok" if ok else "bad", "AI dispatcher: " + ("online" if ok else "offline"))
        self.ai_dot.setToolTip(message)

    # ------------------------------------------------------------------ menu
    def _act(self, text, fn, shortcut=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(lambda *_: fn())
        if shortcut:
            a.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
        return a

    def _build_menu(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction(self._act("Back up career now", self.backup))
        f.addSeparator()
        f.addAction(self._act("Quit SkyDispatch", self.quit_app, QKeySequence.Quit))
        t = mb.addMenu("&Tools")
        t.addAction(self._act("Pair a phone...", self.show_pairing))
        t.addAction(self._act("Check AI connection", self.ctx.check_ai))
        t.addAction(self._act("Reconnect simulator", self.ctx.start_sim))
        t.addAction(self._act("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir())))))
        t.addAction(self._act("Toggle light/dark theme", self._toggle_theme))
        h = mb.addMenu("&Help")
        h.addAction(self._act("Open log folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir())))))
        h.addAction(self._act("Check sim loadout (diagnostics)...", self.ctx.diagnose_loadout))
        h.addAction(self._act("Check for updates...", self.check_updates_now))
        h.addAction(self._act("Uninstall SkyDispatch...", lambda: UninstallDialog(self).exec()))
        h.addAction(self._act(f"About {APP_NAME}", lambda: AboutDialog(self.ctx, self).exec()))

    def backup(self) -> None:
        dest = paths.backups_dir() / f"career-{datetime.now():%Y%m%d-%H%M%S}.db"
        self.ctx.db.backup(dest)
        self.ctx.toast.emit("good", f"Backup saved: {dest.name}")

    def _toggle_theme(self) -> None:
        new = "light" if self.ctx.settings.ui.theme == "dark" else "dark"
        self.ctx.settings.ui.theme = new
        self.ctx.settings.save()
        self.apply_theme(new)

    def apply_theme(self, name: str) -> None:
        theme.set_theme(name)
        QApplication.instance().setStyleSheet(theme.stylesheet())
        self._sim_status(self.ctx.provider.status if self.ctx.provider else "disconnected", "")

    # ---------------------------------------------------------------- voices
    def offer_voices(self) -> None:
        """Once: offer to download the natural voices (otherwise everyone shares one voice or the OS voice)."""
        from ..voice import tts
        s = self.ctx.settings
        if s.voice.voices_prompted or not s.voice.tts_enabled or not tts.piper_importable():
            return
        s.voice.voices_prompted = True
        s.save()
        needed, missing = self.ctx.character_voices()
        if not missing:
            return
        mb = sum(tts.voice_size_mb(v) for v in missing)
        who = ", ".join(w.split(" (")[0] for v, w in needed if v in missing)
        if QMessageBox.question(
                self, "Natural voices",
                f"SkyDispatch can download natural-sounding voices so each person sounds different ({who}).\n\n"
                f"{len(missing)} voice(s), about {mb} MB, from the public Piper voice library on Hugging Face. "
                "The phone plays them. You can also do this later in Settings > Voice.\n\nDownload them now?") == QMessageBox.Yes:
            self.ctx.toast.emit("info", f"Downloading {len(missing)} voice(s) in the background...")
            self.ctx.download_character_voices()

    def _show_loadout_report(self, text: str) -> None:
        box = QMessageBox(QMessageBox.Information, "Sim loadout diagnostics",
                          "What SkyDispatch can read from the aircraft in MSFS right now (nothing was changed). "
                          "The same lines are in the log.", QMessageBox.Ok, self)
        box.setDetailedText(text)
        box.setModal(False)
        box.show()
        self._loadout_box = box

    # --------------------------------------------------------------- updates
    def check_updates_now(self) -> None:
        if self.ctx.update_info:
            self._show_update(self.ctx.update_info)
        else:
            self.ctx.check_for_updates(manual=True)

    def _update_found(self, info) -> None:
        if self.ctx.career.has_flown():
            self.ctx.toast.emit("info", f"SkyDispatch {info.version} is available (Help > Check for updates).")
        else:
            self._show_update(info)

    def _show_update(self, info) -> None:
        dlg = UpdateDialog(self.ctx, info, self)
        dlg.quit_for_update.connect(self.quit_app)
        dlg.setModal(False)
        dlg.show()
        self._update_dlg = dlg

    # ------------------------------------------------------------- closing
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.toast.reposition()

    def quit_app(self) -> None:
        """Really exit (the window's close button only hides it to the tray)."""
        job = self.ctx.career.recorder
        if job and job.started and not job.finished and job.air_s > 0:
            if QMessageBox.question(self, "Flight in progress", "A flight is being recorded. Quit anyway? "
                                    "The flight will be lost.") != QMessageBox.Yes:
                return
        self.quitting = True
        self.close()

    def closeEvent(self, e) -> None:
        if not self.quitting and self.tray is not None and self.tray.isVisible():
            e.ignore()
            self.hide()
            if not getattr(self, "_told_about_tray", False):
                self._told_about_tray = True
                self.tray.showMessage(APP_NAME, "SkyDispatch keeps running in the tray so your phone can connect. "
                                      "Right-click the icon to quit.", QSystemTrayIcon.Information, 5000)
            return
        if self.tray is not None:
            self.tray.hide()
        self.ctx.settings.ui.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.ctx.shutdown()
        e.accept()
        QApplication.instance().quit()

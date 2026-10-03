from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import paths
from ..voice.hotkey import GlobalPushToTalk, hotkey_available
from . import fmt, theme
from .context import AppContext
from .dialogs import AboutDialog, FlightResultDialog, UninstallDialog, UpdateDialog
from .pages.dashboard import DashboardPage
from .pages.finance import FinancePage
from .pages.flight import FlightPage
from .pages.hangar import HangarPage
from .pages.jobboard import JobBoardPage
from .pages.messenger import MessengerPage
from .pages.logbook import LogbookPage
from .pages.market import MarketPage
from .pages.settings import SettingsPage
from .widgets import StatusDot, Toast

log = logging.getLogger(__name__)

QT_KEYS = {"F7": Qt.Key_F7, "F8": Qt.Key_F8, "F9": Qt.Key_F9, "F10": Qt.Key_F10, "caps_lock": Qt.Key_CapsLock,
           "scroll_lock": Qt.Key_ScrollLock, "pause": Qt.Key_Pause}


class MainWindow(QMainWindow):
    _ptt_press = Signal()
    _ptt_release = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 820)
        self.setMinimumSize(1000, 680)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)
        brand = QLabel(f"✈  {APP_NAME}")
        brand.setStyleSheet("font-size: 18px; font-weight: 800; padding: 6px 8px 14px 8px;")
        sl.addWidget(brand)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.nav: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        defs = [("dashboard", "Dashboard", DashboardPage), ("jobboard", "Job Board", JobBoardPage),
                ("messenger", "Messenger", MessengerPage), ("flight", "Flight", FlightPage),
                ("market", "Freelance", MarketPage), ("hangar", "Hangar", HangarPage),
                ("logbook", "Logbook", LogbookPage), ("finance", "Finances", FinancePage),
                ("settings", "Settings", SettingsPage)]
        for key, label, cls in defs:
            page = cls(ctx)
            self.pages[key] = page
            self.stack.addWidget(page)
            btn = QPushButton(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.goto(k))
            group.addButton(btn)
            self.nav[key] = btn
            if key == "settings":
                sl.addStretch(1)
            sl.addWidget(btn)
            if hasattr(page, "goto"):
                page.goto.connect(self.goto)
            if hasattr(page, "open_thread"):
                page.open_thread.connect(self.open_messenger)
        outer.addWidget(side)
        outer.addWidget(self.stack, 1)

        self.toast = Toast(self)
        ctx.toast.connect(self.toast.show_message)
        sb = self.statusBar()
        self.sim_dot, self.ai_dot = StatusDot(), StatusDot()
        self.bal = QLabel("")
        sb.addWidget(self.sim_dot)
        sb.addWidget(self.ai_dot)
        sb.addPermanentWidget(self.bal)
        ctx.sim_status.connect(self._sim_status)
        ctx.ai_status.connect(self._ai_status)
        ctx.career_event.connect(self._career_event)
        ctx.update_available.connect(self._update_found)
        self._update_dlg = None
        self._sim_status(ctx.provider.status if ctx.provider else "disconnected", "")
        self._ai_status(False, "") if ctx.dispatcher.online is False else self.ai_dot.set_state("off", "AI: not checked")

        self._pending_refresh = QTimer(self, singleShot=True, interval=200)
        self._pending_refresh.timeout.connect(self._refresh_visible)
        self._build_menu()

        settings_page: SettingsPage = self.pages["settings"]  # type: ignore[assignment]
        settings_page.run_wizard.connect(self.run_wizard)
        settings_page.theme_changed.connect(self.apply_theme)

        # push-to-talk (in-app key + optional global hotkey): talks to the copilot during a flight, else the messenger
        self._ptt_target = None
        self._ptt_press.connect(self._route_press)
        self._ptt_release.connect(self._route_release)
        QApplication.instance().installEventFilter(self)
        self._global_ptt: GlobalPushToTalk | None = None
        ctx.settings_changed.connect(self._setup_global_ptt)
        self._setup_global_ptt()

        self.nav["dashboard"].setChecked(True)
        self._update_balance()

    # ------------------------------------------------------------ navigation
    def goto(self, key: str) -> None:
        page = self.pages.get(key)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        self.nav[key].setChecked(True)
        if hasattr(page, "refresh"):
            page.refresh()

    def open_messenger(self, thread: str) -> None:
        self.goto("messenger")
        self.pages["messenger"].select_thread(thread)

    def _route_press(self) -> None:
        self._ptt_target = self.pages["flight"].copilot if self.ctx.copilot.in_flight() \
            and self.ctx.settings.ai.copilot_enabled else self.pages["messenger"]
        self._ptt_target.start_talking()

    def _route_release(self) -> None:
        if self._ptt_target is not None:
            self._ptt_target.stop_talking()
            self._ptt_target = None

    def _refresh_visible(self) -> None:
        if self.ctx.closed:
            return
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()
        self._update_balance()

    def _career_event(self, name: str, payload: dict) -> None:
        if name == "flight_event" or self.ctx.closed:
            return
        if name == "flight_finished":
            dlg = FlightResultDialog(self.ctx, payload["settlement"], self)
            dlg.setModal(False)
            dlg.show()
        for key in ("logbook", "hangar", "market", "dashboard", "finance", "jobboard"):
            if self.stack.currentWidget() is not self.pages[key]:
                if hasattr(self.pages[key], "refresh"):
                    QTimer.singleShot(0, self.pages[key].refresh)       # refresh() ignores calls after shutdown
        self._pending_refresh.start()

    def _update_balance(self) -> None:
        p = self.ctx.db.pilot()
        self.bal.setText(f"{p.rank} {p.name}   |   {fmt.money(self.ctx.settings, p.balance)}   " if p else "")

    def _sim_status(self, status: str, message: str) -> None:
        mode = {"simulated": "Simulated", "simconnect": "MSFS", "bridge": "Bridge"}[self.ctx.settings.sim.mode]
        state = {"connected": "ok", "connecting": "busy", "error": "bad"}.get(status, "off")
        label = {"connected": "connected", "connecting": "waiting", "error": "error"}.get(status, "disconnected")
        self.sim_dot.set_state(state, f"{mode}: {label}")
        self.sim_dot.setToolTip(message)

    def _ai_status(self, ok: bool, message: str) -> None:
        self.ai_dot.set_state("ok" if ok else "bad", "AI dispatcher: " + ("online" if ok else "offline"))
        self.ai_dot.setToolTip(message)

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction(self._act("Back up career now", self._backup))
        f.addSeparator()
        f.addAction(self._act("Quit", self.close, QKeySequence.Quit))
        c = mb.addMenu("&Career")
        c.addAction(self._act("Find new freelance contracts", self.ctx.refresh_market, "Ctrl+R"))
        c.addAction(self._act("Run setup wizard...", self.run_wizard))
        v = mb.addMenu("&View")
        for i, (key, label) in enumerate([("dashboard", "Dashboard"), ("jobboard", "Job Board"),
                                          ("messenger", "Messenger"), ("flight", "Flight"), ("market", "Freelance"),
                                          ("hangar", "Hangar"), ("logbook", "Logbook"), ("finance", "Finances"),
                                          ("settings", "Settings")], start=1):
            v.addAction(self._act(label, lambda k=key: self.goto(k), f"Ctrl+{i}"))
        v.addSeparator()
        v.addAction(self._act("Toggle light/dark theme", self._toggle_theme))
        t = mb.addMenu("&Tools")
        t.addAction(self._act("Check AI connection", self.ctx.check_ai))
        t.addAction(self._act("Reconnect simulator", self.ctx.start_sim))
        t.addAction(self._act("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir())))))
        h = mb.addMenu("&Help")
        h.addAction(self._act("Open log folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir())))))
        h.addAction(self._act("Uninstall SkyDispatch...", lambda: UninstallDialog(self).exec()))
        h.addAction(self._act("Check for updates...", self.check_updates_now))
        h.addAction(self._act(f"About {APP_NAME}", lambda: AboutDialog(self.ctx, self).exec()))

    # --------------------------------------------------------------- updates
    def check_updates_now(self) -> None:
        if self.ctx.update_info:
            self._show_update(self.ctx.update_info)
        else:
            self.ctx.check_for_updates(manual=True)

    def _update_found(self, info) -> None:
        rec = self.ctx.career.recorder
        if rec and rec.started and not rec.finished:
            self.ctx.toast.emit("info", f"SkyDispatch {info.version} is available (Help > Check for updates).")
        else:
            self._show_update(info)

    def _show_update(self, info) -> None:
        dlg = UpdateDialog(self.ctx, info, self)
        dlg.quit_for_update.connect(self.close)
        dlg.setModal(False)
        dlg.show()
        self._update_dlg = dlg

    def _act(self, text, fn, shortcut=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(lambda *_: fn())
        if shortcut:
            a.setShortcut(shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut))
        return a

    def _backup(self) -> None:
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
        for p in self.pages.values():
            p.update()
        self._sim_status(self.ctx.provider.status if self.ctx.provider else "disconnected", "")

    def run_wizard(self) -> None:
        from .wizard import SetupWizard
        wiz = SetupWizard(self.ctx, self)
        if wiz.exec():
            for p in self.pages.values():
                if hasattr(p, "refresh"):
                    p.refresh()
            self.goto("dashboard")
            self._update_balance()
            self.ctx.career_event.emit("market_changed", {})

    # ------------------------------------------------------- push-to-talk
    def _setup_global_ptt(self) -> None:
        if self._global_ptt:
            self._global_ptt.stop()
            self._global_ptt = None
        if hotkey_available() and self.ctx.settings.voice.stt_enabled:
            g = GlobalPushToTalk(self.ctx.settings.voice.push_to_talk_key, self._ptt_press.emit, self._ptt_release.emit)
            if g.start():
                self._global_ptt = g

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() in (QEvent.KeyPress, QEvent.KeyRelease) and not ev.isAutoRepeat():
            key = QT_KEYS.get(self.ctx.settings.voice.push_to_talk_key)
            if key is not None and ev.key() == key and self.isActiveWindow() and self._global_ptt is None:
                (self._ptt_press if ev.type() == QEvent.KeyPress else self._ptt_release).emit()
                return True
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.toast.reposition()

    def closeEvent(self, e) -> None:
        job = self.ctx.career.recorder
        if job and job.started and not job.finished:
            if QMessageBox.question(self, "Flight in progress", "A flight is being recorded. Quit anyway? "
                                    "The flight will be lost.") != QMessageBox.Yes:
                e.ignore()
                return
        self._pending_refresh.stop()
        if self._global_ptt:
            self._global_ptt.stop()
            self._global_ptt = None
        self.ctx.settings.ui.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.ctx.shutdown()
        e.accept()

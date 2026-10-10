"""Server application bootstrap: single instance, tray icon, admin window, and the phone API."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, APP_ORG, __version__
from ..core import paths
from ..core.config import Settings
from ..core.logging_setup import setup_logging
from ..ui import theme

log = logging.getLogger(__name__)


def resource_path(name: str) -> Path:
    """Locate a bundled resource both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for candidate in (base / "resources" / name, base / "skydispatch" / "resources" / name):
        if candidate.exists():
            return candidate
    return base / "resources" / name


_running_mutex = None


def hold_running_mutex() -> None:
    """Windows: a named mutex that exists exactly as long as this process. The updater's installer waits on it so it
    never overwrites files of an app that is still shutting down."""
    global _running_mutex
    if sys.platform == "win32":
        import ctypes
        _running_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "SkyDispatch.Running")


def apply_pending_restore() -> None:
    """A 'Restore from backup' request is applied here, before the database is opened."""
    pending = paths.data_dir() / "pending_restore.db"
    if not pending.exists():
        return
    from ..db.database import Database
    try:
        Database.restore(pending, paths.database_path())
        log.info("Career restored from backup")
    except Exception:
        log.exception("Restore failed; keeping current career")
    finally:
        pending.unlink(missing_ok=True)


def run(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    minimized = "--minimized" in argv
    argv = [a for a in argv if a != "--minimized"]
    setup_logging()
    log.info("%s server %s starting", APP_NAME, __version__)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)                  # the window hides to the tray; Quit really exits
    icon = QIcon()
    icon_file = resource_path("icon.png")
    if icon_file.exists():
        icon = QIcon(str(icon_file))
        app.setWindowIcon(icon)

    lock = QLockFile(str(paths.data_dir() / "skydispatch.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running (look for it in the tray).")
        return 0

    hold_running_mutex()
    settings = Settings.load()
    theme.set_theme(settings.ui.theme)
    app.setStyleSheet(theme.stylesheet())

    apply_pending_restore()
    from ..ui.context import AppContext
    from .window import AdminWindow
    try:
        ctx = AppContext.create()
    except Exception as exc:
        log.exception("Could not open the career database")
        QMessageBox.critical(None, APP_NAME, f"Could not open your career data:\n{exc}\n\nSee the log in {paths.log_dir()}")
        return 1
    if not ctx.settings.ui.server_mode:                    # first start of the server edition: the phone API is the point
        ctx.settings.remote.enabled = True
        ctx.settings.ui.server_mode = True
        ctx.settings.save()
    previous_hook = sys.excepthook

    def notify_hook(exc_type, exc, tb):
        previous_hook(exc_type, exc, tb)          # logs to file
        if not ctx.closed:
            ctx.toast.emit("bad", f"Unexpected error: {exc}. Details are in the log (Help > Open log folder).")

    sys.excepthook = notify_hook
    win = AdminWindow(ctx, icon)
    geo = ctx.settings.ui.window_geometry
    if geo:
        win.restoreGeometry(QByteArray.fromBase64(geo.encode()))
    if not ctx.pairing.devices():
        win.tabs.setCurrentWidget(win.phones_tab)       # nothing paired yet: start where you pair
    if not (minimized and win.tray is not None):
        win.show()

    ctx.start_sim()
    ctx.check_ai()
    ctx.start_web()
    ctx.check_for_updates()
    QTimer.singleShot(2500, win.offer_voices)
    code = app.exec()
    lock.unlock()
    return code

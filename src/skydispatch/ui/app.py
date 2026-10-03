"""Application bootstrap: single instance, theming, wizard on first run."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QByteArray
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, APP_ORG, __version__
from ..core import paths
from ..core.config import Settings
from ..core.logging_setup import setup_logging
from . import theme

log = logging.getLogger(__name__)


def resource_path(name: str) -> Path:
    """Locate a bundled resource both from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for candidate in (base / "resources" / name, base / "skydispatch" / "resources" / name):
        if candidate.exists():
            return candidate
    return base / "resources" / name


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
    setup_logging()
    log.info("%s %s starting", APP_NAME, __version__)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(__version__)
    icon = resource_path("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    lock = QLockFile(str(paths.data_dir() / "skydispatch.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running.")
        return 0

    settings = Settings.load()
    theme.set_theme(settings.ui.theme)
    app.setStyleSheet(theme.stylesheet())

    apply_pending_restore()
    from .context import AppContext
    from .main_window import MainWindow
    try:
        ctx = AppContext.create()
    except Exception as exc:
        log.exception("Could not open the career database")
        QMessageBox.critical(None, APP_NAME, f"Could not open your career data:\n{exc}\n\nSee the log in {paths.log_dir()}")
        return 1
    win = MainWindow(ctx)
    geo = ctx.settings.ui.window_geometry
    if geo:
        win.restoreGeometry(QByteArray.fromBase64(geo.encode()))
    win.show()

    if not ctx.career.has_career or not ctx.settings.ui.first_run_complete:
        win.run_wizard()
        if not ctx.career.has_career:         # wizard cancelled
            ctx.shutdown()
            return 0
    else:
        ctx.start_sim()
        ctx.check_ai()
    win.goto("dashboard")
    code = app.exec()
    lock.unlock()
    return code

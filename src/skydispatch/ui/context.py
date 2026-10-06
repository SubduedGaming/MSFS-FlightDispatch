"""AppContext: the desktop's Engine, with real Qt signals and the Qt GUI thread as the engine thread."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QThreadPool, QTimer, Signal

from ..core.config import Settings
from ..db.database import Database
from ..server.engine import TICK_SECONDS, Engine
from ..web.mainthread import MainThread

log = logging.getLogger(__name__)


class AppContext(Engine, QObject):
    sim_state = Signal(object)
    sim_status = Signal(str, str)
    career_event = Signal(str, dict)
    thread_changed = Signal(str)          # a messenger thread ('general', 'employer:<id>', 'copilot') has new messages
    chat_busy = Signal(str, bool)         # thread, busy
    ai_status = Signal(bool, str)
    toast = Signal(str, str)              # level (info/good/warn/bad), message
    listening = Signal(bool)
    settings_changed = Signal()
    update_available = Signal(object)     # updater.UpdateInfo
    plan_changed = Signal()               # SimBrief plan imported or the sim aircraft was loaded
    loadout_report = Signal(str)          # the result of "Check sim loadout"
    speech = Signal(object)               # an utterance for the phone to speak (voice.output = "phone")
    speech_stop = Signal(object)          # stop speech: {"keep": [threads still being looked at]}

    def __init__(self, settings: Settings, db: Database):
        QObject.__init__(self)
        Engine.__init__(self, settings, db, main=MainThread())        # created on the GUI thread

    # ------------------------------------------------------- threading hooks
    def _make_events(self) -> None:
        pass                              # the class-level Qt signals above are the events

    def _on_main(self) -> bool:
        return QThread.currentThread() is self.thread()

    def call_later(self, ms: int, fn) -> None:
        QTimer.singleShot(ms, fn)

    def _start_ticker(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_SECONDS * 1000)

    def _stop_ticker(self) -> None:
        self._timer.stop()

    def shutdown(self) -> None:
        if not self.closed:
            QThreadPool.globalInstance().waitForDone(1500)     # let GUI-side background tasks finish before the DB closes
        super().shutdown()

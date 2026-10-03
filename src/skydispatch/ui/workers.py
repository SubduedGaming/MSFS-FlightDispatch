"""Run blocking work (LLM, network, disk) off the UI thread."""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:
            log.exception("background task failed")
            self._emit(self.signals.failed, str(exc) or exc.__class__.__name__)
        else:
            self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:      # the app is shutting down and Qt already freed the signal source
            pass


_alive: set[_Task] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None, owner: QObject | None = None) -> None:
    """Execute `fn` in the thread pool; callbacks run on the UI thread.

    Pass `owner` (a QObject living on the UI thread) so callbacks are queued to it.
    """
    task = _Task(fn)
    _alive.add(task)

    def finish(cb):
        def inner(value):
            _alive.discard(task)
            if cb:
                cb(value)
        return inner

    task.signals.done.connect(finish(on_done))
    task.signals.failed.connect(finish(on_error))
    QThreadPool.globalInstance().start(task)

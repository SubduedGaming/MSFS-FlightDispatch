"""Run callables on the Qt GUI thread from the web server's worker threads.

The career, database and AI services are only ever touched from the GUI thread (like the desktop UI does), so web
requests are marshalled here instead of being made thread-safe one by one.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal


class MainThread(QObject):
    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()                 # must be created on the GUI thread
        self._call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(job: Callable[[], None]) -> None:
        job()

    def post(self, fn: Callable[[], Any]) -> None:
        """Fire and forget."""
        self._call.emit(fn)

    def run(self, fn: Callable[[], Any], timeout: float = 20.0) -> Any:
        """Call `fn` on the GUI thread and return its result (or raise its exception)."""
        if QThread.currentThread() is self.thread():
            return fn()
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:          # re-raised in the calling thread
                box["error"] = exc
            finally:
                done.set()

        self._call.emit(job)
        if not done.wait(timeout):
            raise TimeoutError("SkyDispatch did not answer in time (is a dialog open on the PC?)")
        if "error" in box:
            raise box["error"]
        return box.get("value")

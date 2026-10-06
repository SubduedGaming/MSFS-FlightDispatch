"""Run work on one dedicated thread, so the career, database and AI services keep their "one thread touches state"
rule without Qt's GUI thread."""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable


class SerialExecutor:
    def __init__(self, name: str = "skydispatch-engine") -> None:
        self._q: queue.Queue = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            job()

    @property
    def on_worker(self) -> bool:
        return threading.current_thread() is self._thread

    def post(self, fn: Callable[[], Any]) -> None:
        """Fire and forget. Errors are swallowed (logged by the caller's own handlers)."""
        if self._closed:
            return

        def job() -> None:
            try:
                fn()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("posted job failed")
        self._q.put(job)

    def run(self, fn: Callable[[], Any], timeout: float = 20.0) -> Any:
        """Call `fn` on the worker thread and return its result (or raise its exception)."""
        if self.on_worker:
            return fn()
        if self._closed:
            raise RuntimeError("executor is closed")
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:          # re-raised in the calling thread
                box["error"] = exc
            finally:
                done.set()

        self._q.put(job)
        if not done.wait(timeout):
            raise TimeoutError("SkyDispatch did not answer in time")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._q.put(None)
        if not self.on_worker:
            self._thread.join(timeout)

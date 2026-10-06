"""A tiny thread-safe event bus: the Qt-free replacement for the ``Signal``s the desktop context used."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[Callable[..., Any]]] = {}

    def on(self, name: str, fn: Callable[..., Any]) -> Callable[[], None]:
        """Subscribe `fn` to `name`. Returns a function that unsubscribes it again."""
        with self._lock:
            self._subs.setdefault(name, []).append(fn)

        def off() -> None:
            with self._lock:
                if fn in self._subs.get(name, []):
                    self._subs[name].remove(fn)
        return off

    def emit(self, name: str, *args: Any) -> None:
        """Call every subscriber on the caller's thread. A failing subscriber never stops the others."""
        with self._lock:
            subs = list(self._subs.get(name, ()))
        for fn in subs:
            try:
                fn(*args)
            except Exception:
                log.exception("event handler for %r failed", name)

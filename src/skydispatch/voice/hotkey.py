"""Optional global push-to-talk key (works while MSFS has focus). Needs `pynput`."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def hotkey_available() -> bool:
    try:
        import pynput  # noqa: F401
        return True
    except Exception:
        return False


class GlobalPushToTalk:
    def __init__(self, key_name: str, on_press: Callable[[], None], on_release: Callable[[], None]):
        self.key_name = key_name
        self.on_press, self.on_release = on_press, on_release
        self._listener = None
        self._down = False

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        target = self._resolve(keyboard, self.key_name)
        if target is None:
            return False

        def press(k):
            if k == target and not self._down:
                self._down = True
                self.on_press()

        def release(k):
            if k == target and self._down:
                self._down = False
                self.on_release()

        try:
            self._listener = keyboard.Listener(on_press=press, on_release=release)
            self._listener.start()
            return True
        except Exception as exc:       # e.g. macOS accessibility permission not granted
            log.warning("Global hotkey unavailable: %s", exc)
            return False

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    @staticmethod
    def _resolve(keyboard, name: str):
        name = name.strip().lower()
        if hasattr(keyboard.Key, name):
            return getattr(keyboard.Key, name)
        if len(name) == 1:
            return keyboard.KeyCode.from_char(name)
        return None

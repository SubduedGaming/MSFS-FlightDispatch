from __future__ import annotations

import functools

from PySide6.QtWidgets import QWidget

from ..context import AppContext


class Page(QWidget):
    """Base class for main-window pages. `refresh()` is called whenever data may have changed."""

    title = ""

    def __init_subclass__(cls, **kwargs):
        """Every page's refresh() becomes a no-op once the app is shutting down. Queued events can arrive after the
        database has been closed, and a refresh then would raise."""
        super().__init_subclass__(**kwargs)
        original = cls.__dict__.get("refresh")
        if original is not None:
            @functools.wraps(original)
            def guarded(self, *args, **kwargs):
                if self.ctx.closed:
                    return None
                return original(self, *args, **kwargs)
            cls.refresh = guarded

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.db = ctx.db
        self.career = ctx.career
        self.settings = ctx.settings

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass

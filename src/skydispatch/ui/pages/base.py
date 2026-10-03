from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..context import AppContext


class Page(QWidget):
    """Base class for main-window pages. `refresh()` is called whenever data may have changed."""

    title = ""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self.db = ctx.db
        self.career = ctx.career
        self.settings = ctx.settings

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass

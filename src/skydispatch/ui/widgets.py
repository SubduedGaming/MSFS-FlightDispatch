"""Small reusable widgets: headings, muted text, toasts and the status dot."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QWidget

from . import theme


def heading(text: str, level: int = 1) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("h1" if level == 1 else "h2")
    return lbl


def muted(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(wrap)
    return lbl


class Toast(QLabel):
    """Small non-blocking notification in the bottom-right of its parent window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMaximumWidth(380)
        self.hide()
        self._timer = QTimer(self, singleShot=True, interval=5000)
        self._timer.timeout.connect(self.hide)

    def show_message(self, level: str, text: str) -> None:
        p = theme.palette()
        color = {"good": p.good, "warn": p.warn, "bad": p.bad}.get(level, p.accent)
        self.setStyleSheet(f"background:{p.surface2};color:{p.text};border:1px solid {color};"
                           f"border-left:4px solid {color};border-radius:8px;padding:10px 14px;")
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.raise_()
        self.show()
        self._timer.start()

    def reposition(self) -> None:
        par = self.parentWidget()
        if par:
            self.move(par.width() - self.width() - 20, par.height() - self.height() - 44)


class StatusDot(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.set_state("off", "")

    def set_state(self, state: str, text: str) -> None:
        p = theme.palette()
        color = {"ok": p.good, "busy": p.warn, "bad": p.bad}.get(state, p.muted)
        self.setText(f'<span style="color:{color}">&#9679;</span> {text}')

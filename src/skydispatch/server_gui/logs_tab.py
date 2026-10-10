from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from ..core import paths
from ..ui.widgets import heading, muted
from .logtail import tail
from .page import Page


class LogsTab(Page):
    title = "Logs"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("Log"))
        root.addWidget(muted("The most recent lines of the log file. Send this if something goes wrong."))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        root.addWidget(self.view, 1)
        bar = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        folder = QPushButton("Open log folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir()))))
        bar.addWidget(refresh)
        bar.addWidget(folder)
        bar.addStretch(1)
        root.addLayout(bar)
        self._clock = QTimer(self, interval=3000)
        self._clock.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self._clock.start()
        self.refresh()

    def refresh(self) -> None:
        text = tail(paths.log_dir() / "skydispatch.log")
        if text != self.view.toPlainText():
            bar = self.view.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.view.setPlainText(text)
            if at_end:
                bar.setValue(bar.maximum())

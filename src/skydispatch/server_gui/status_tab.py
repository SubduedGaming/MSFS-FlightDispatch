from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..ui.widgets import StatusDot, heading, muted
from .page import Page
from .status import status_rows


class StatusTab(Page):
    title = "Status"
    open_phones = Signal()

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("SkyDispatch server"))
        root.addWidget(muted("This PC runs the simulator link, the AI dispatcher and your career. You fly and manage "
                             "everything from the SkyDispatch app on your phone."))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        root.addLayout(self.grid)
        self._cells: dict[str, tuple[StatusDot, QLabel]] = {}

        bar = QHBoxLayout()
        self.toggle = QPushButton("")
        self.toggle.clicked.connect(self._toggle_server)
        pair = QPushButton("Pair a phone...")
        pair.setObjectName("primary")
        pair.clicked.connect(self.open_phones.emit)
        reconnect = QPushButton("Reconnect simulator")
        reconnect.clicked.connect(self.ctx.start_sim)
        check = QPushButton("Check AI")
        check.clicked.connect(self.ctx.check_ai)
        for b in (pair, self.toggle, reconnect, check):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)
        root.addStretch(1)

        for sig in (ctx.sim_status, ctx.ai_status, ctx.settings_changed, ctx.career_event):
            sig.connect(lambda *_: self._soon())
        self._debounce = QTimer(self, singleShot=True, interval=150)
        self._debounce.timeout.connect(self.refresh)
        self._clock = QTimer(self, interval=2000)               # phones connecting and leaving have no signal
        self._clock.timeout.connect(self.refresh)
        self._clock.start()
        self.refresh()

    def _soon(self) -> None:
        if not self.ctx.closed:
            self._debounce.start()

    def _toggle_server(self) -> None:
        s = self.settings
        s.remote.enabled = not (self.ctx.web is not None and self.ctx.web.running)
        s.save()
        self.ctx.start_web() if s.remote.enabled else self.ctx.stop_web()
        self.ctx.settings_changed.emit()
        self.refresh()

    def refresh(self) -> None:
        rows = status_rows(self.ctx)
        for i, row in enumerate(rows):
            if row.key not in self._cells:
                dot, text = StatusDot(), QLabel("")
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.grid.addWidget(dot, i, 0)
                self.grid.addWidget(text, i, 1)
                self.grid.setColumnStretch(1, 1)
                self._cells[row.key] = (dot, text)
            dot, text = self._cells[row.key]
            dot.set_state(row.state, f"<b>{row.title}</b>")
            text.setText(row.text)
        for key in set(self._cells) - {r.key for r in rows}:         # the update row disappears once installed
            dot, text = self._cells.pop(key)
            dot.deleteLater()
            text.deleteLater()
        running = self.ctx.web is not None and self.ctx.web.running
        self.toggle.setText("Stop phone access" if running else "Start phone access")

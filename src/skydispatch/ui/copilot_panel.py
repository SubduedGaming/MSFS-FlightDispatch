"""The copilot panel on the Flight page: ask for help, run checklists, and read proactive callouts."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..copilot.copilot import QUICK_ACTIONS, THREAD
from ..copilot.personas import get_copilot
from .chat import ChatView
from .context import AppContext
from .widgets import muted


class CopilotPanel(QWidget):
    heard = Signal(str)
    heard_error = Signal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel("")
        self.title.setObjectName("h2")
        self.callouts = QCheckBox("Callouts")
        self.callouts.setToolTip("Proactive callouts: positive rate, top of descent, approach, sink rate, fuel")
        self.callouts.setChecked(ctx.settings.ai.copilot_callouts)
        self.callouts.toggled.connect(self._callouts_toggled)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.callouts)
        lay.addLayout(head)
        self.note = muted("")
        lay.addWidget(self.note)
        self.view = ChatView(ctx)
        self.view.setMinimumHeight(170)
        lay.addWidget(self.view, 1)

        quick = QHBoxLayout()
        quick.setSpacing(4)
        for key, label in QUICK_ACTIONS:
            b = QPushButton(label)
            b.setStyleSheet("padding:4px 8px;font-size:12px;")
            b.clicked.connect(lambda _=False, k=key: ctx.ask_copilot(quick=k))
            quick.addWidget(b)
        lay.addLayout(quick)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask your copilot...")
        self.input.returnPressed.connect(self._send)
        send = QPushButton("Ask")
        send.setObjectName("primary")
        send.clicked.connect(self._send)
        self.talk = QPushButton("Hold to talk")
        self.talk.setObjectName("talk")
        self.talk.pressed.connect(self.start_talking)
        self.talk.released.connect(self.stop_talking)
        row.addWidget(self.input, 1)
        row.addWidget(send)
        row.addWidget(self.talk)
        lay.addLayout(row)

        ctx.thread_changed.connect(self._changed)
        ctx.chat_busy.connect(self._on_busy)
        ctx.settings_changed.connect(self.refresh)
        self.heard.connect(self._ask)
        self.heard_error.connect(lambda m: ctx.toast.emit("warn", m))
        self.refresh()

    def refresh(self) -> None:
        s = self.ctx.settings
        p = get_copilot(s.ai.copilot)
        self.title.setText(f"Copilot: {p.name}")
        self.callouts.blockSignals(True)
        self.callouts.setChecked(s.ai.copilot_callouts)
        self.callouts.blockSignals(False)
        self.setEnabled(s.ai.copilot_enabled)
        stt_ok, stt_msg = self.ctx.voice.stt_status()
        self.talk.setEnabled(stt_ok and s.voice.stt_enabled)
        self.talk.setToolTip("" if stt_ok else stt_msg)
        self.note.setText("Ask for advice any time. Answers use your live flight data, even if the AI server is offline."
                          if s.ai.copilot_enabled else "The copilot is switched off in Settings > AI Dispatcher.")
        if self.ctx.db.last_message(THREAD) is None:
            self.ctx.db.add_message("assistant", p.greeting, THREAD)
        self._render()

    def _render(self) -> None:
        p = get_copilot(self.ctx.settings.ai.copilot)
        self.view.set_messages(self.ctx.db.messages(60, THREAD), p.name, self._busy)

    def _changed(self, thread: str) -> None:
        if thread == THREAD:
            self._render()

    def _on_busy(self, thread: str, busy: bool) -> None:
        if thread == THREAD:
            self._busy = busy
            self._render()

    def _callouts_toggled(self, on: bool) -> None:
        self.ctx.settings.ai.copilot_callouts = on
        self.ctx.settings.save()

    def _send(self) -> None:
        self._ask(self.input.text())

    def _ask(self, text: str) -> None:
        text = text.strip()
        if text:
            self.input.clear()
            self.ctx.voice.shut_up()
            self.ctx.ask_copilot(text=text)

    def start_talking(self) -> None:
        if self.ctx.voice.start_listening():
            self.talk.setProperty("live", True)
            self.talk.setText("Listening...")
            self.talk.style().unpolish(self.talk)
            self.talk.style().polish(self.talk)
        else:
            self.ctx.toast.emit("warn", "Could not open the microphone. Check Settings > Voice.")

    def stop_talking(self) -> None:
        self.talk.setProperty("live", False)
        self.talk.setText("Hold to talk")
        self.talk.style().unpolish(self.talk)
        self.talk.style().polish(self.talk)
        self.ctx.voice.stop_listening(self.heard.emit, self.heard_error.emit)

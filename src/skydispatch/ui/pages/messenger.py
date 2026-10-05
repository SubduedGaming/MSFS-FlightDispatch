"""Messenger: one conversation thread per employer (plus your own operations desk)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ...ai.dispatcher import employer_thread
from ...data.employers import get_employer
from ..chat import ChatView
from ..widgets import StatusDot, heading, muted
from .base import Page

GENERAL = "general"


class MessengerPage(Page):
    title = "Messenger"
    goto = Signal(str)
    heard = Signal(str)
    heard_error = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.thread = GENERAL
        self._busy: dict[str, bool] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(heading("Messenger"))
        top.addStretch(1)
        self.status = StatusDot()
        top.addWidget(self.status)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.threads = QListWidget()
        self.threads.setMinimumWidth(230)
        self.threads.setMaximumWidth(300)
        self.threads.currentRowChanged.connect(self._thread_selected)
        split.addWidget(self.threads)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(8)
        self.header = heading("", 2)
        self.sub = muted("")
        rl.addWidget(self.header)
        rl.addWidget(self.sub)
        self.view = ChatView(ctx)
        self.view.offer_accepted.connect(self._accept)
        self.view.offer_declined.connect(self._decline)
        self.view.availability_chosen.connect(self._chip)
        rl.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message your dispatcher...  (or hold the talk button)")
        self.input.returnPressed.connect(lambda: self._send(self.input.text()))
        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(lambda: self._send(self.input.text()))
        self.talk = QPushButton("Hold to talk")
        self.talk.setObjectName("talk")
        self.talk.pressed.connect(self.start_talking)
        self.talk.released.connect(self.stop_talking)
        self.stop_btn = QPushButton("Stop voice")
        self.stop_btn.clicked.connect(self.ctx.voice.shut_up)
        for w in (self.input, self.send_btn, self.talk, self.stop_btn):
            row.addWidget(w, 1 if w is self.input else 0)
        rl.addLayout(row)
        opts = QHBoxLayout()
        self.speak = QCheckBox("Speak replies")
        self.speak.setChecked(self.settings.voice.auto_speak_replies)
        self.speak.toggled.connect(self._speak_toggled)
        self.voice_note = muted("", wrap=False)
        self.more_time = QPushButton("Different amount of time")
        self.more_time.clicked.connect(self._ask_time_again)
        clear = QPushButton("Clear conversation")
        clear.clicked.connect(self._clear)
        opts.addWidget(self.speak)
        opts.addWidget(self.voice_note)
        opts.addStretch(1)
        opts.addWidget(self.more_time)
        opts.addWidget(clear)
        rl.addLayout(opts)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 800])
        root.addWidget(split, 1)

        ctx.thread_changed.connect(self._thread_changed)
        ctx.chat_busy.connect(self._on_busy)
        ctx.ai_status.connect(lambda ok, _m: self.status.set_state("ok" if ok else "bad", "online" if ok else "offline"))
        ctx.settings_changed.connect(self.refresh)
        ctx.career_event.connect(self._career_event)
        self.heard.connect(lambda t: self._send(t))
        self.heard_error.connect(lambda m: ctx.toast.emit("warn", m))
        self.refresh()

    # ------------------------------------------------------------------ threads
    def _thread_ids(self) -> list[str]:
        ids = [GENERAL]
        for e in self.db.employments():
            ids.append(employer_thread(e["employer_id"]))
        return ids

    def _label(self, thread: str) -> tuple[str, str]:
        employer = get_employer(thread.split(":", 1)[1]) if thread != GENERAL else None
        name = employer.name if employer else "Operations (freelance)"
        last = self.db.last_message(thread)
        preview = ""
        if last:
            preview = "Flight offer" if last["kind"] == "offer" else last["content"].replace("\n", " ")[:46]
        return name, preview

    def _rebuild_threads(self) -> None:
        if self.ctx.closed:
            return
        ids = self._thread_ids()
        if self.thread not in ids:
            self.thread = GENERAL
        self.threads.blockSignals(True)
        self.threads.clear()
        for t in ids:
            name, preview = self._label(t)
            item = QListWidgetItem(f"{name}\n{preview}" if preview else name)
            item.setData(Qt.UserRole, t)
            self.threads.addItem(item)
            if t == self.thread:
                self.threads.setCurrentItem(item)
        self.threads.blockSignals(False)

    def _announce_view(self) -> None:
        """Tell the speech system which conversation is on screen, so only that person is heard."""
        self.ctx.set_viewing("desktop:messenger", self.thread if self.isVisible() else None)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._announce_view()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._announce_view()

    def _thread_selected(self, row: int) -> None:
        item = self.threads.item(row)
        if item:
            self.thread = item.data(Qt.UserRole)
            self.ctx.open_thread(self.thread)
            self._announce_view()
            self._render()

    def select_thread(self, thread: str) -> None:
        self.thread = thread
        self._announce_view()
        self.refresh()
        self.ctx.open_thread(thread)

    # ------------------------------------------------------------------ rendering
    def refresh(self) -> None:
        self._rebuild_threads()
        stt_ok, stt_msg = self.ctx.voice.stt_status()
        self.talk.setEnabled(stt_ok and self.settings.voice.stt_enabled)
        self.talk.setToolTip("" if stt_ok else stt_msg)
        tts_ok, _ = self.ctx.voice.tts_status()
        self.voice_note.setText(("Voice out: on" if tts_ok else "Voice out: unavailable") + "  |  " +
                                ("Voice in: on" if stt_ok else "Voice in: unavailable"))
        online = self.ctx.dispatcher.online
        self.status.set_state("ok" if online else "bad" if online is False else "off",
                              "online" if online else "offline" if online is False else "not checked")
        if self.db.last_message(GENERAL) is None:
            self.db.add_message("assistant", self.ctx.dispatcher.greeting(GENERAL), GENERAL)
        self._render()

    def _render(self) -> None:
        if self.ctx.closed:
            return
        d = self.ctx.dispatcher
        thread = self.thread
        name = d.display_name(thread)
        employer = get_employer(thread.split(":", 1)[1]) if thread != GENERAL else None
        self.header.setText(name)
        if employer:
            avail = d.availability(thread)
            self.sub.setText(f"{employer.tagline}  |  Based at {employer.base}  |  Pay x{employer.pay_factor:.2f}"
                             + (f"  |  You said you have {avail} min" if avail else ""))
        else:
            self.sub.setText(f"Your own operations desk  |  AI server: {self.settings.ai.base_url}")
        rows = self.db.messages(120, thread)
        awaiting = bool(employer) and d.is_awaiting_availability(thread)
        last = rows[-1] if rows else None
        show_chips = awaiting and bool(last) and last["kind"] == "ask_time"
        self.more_time.setVisible(bool(employer))
        self.more_time.setEnabled(bool(employer) and not awaiting and self.db.active_job() is None)
        self.view.set_messages(rows, name, self._busy.get(thread, False),
                               can_act=self.db.active_job() is None, show_chips=show_chips)

    def _thread_changed(self, thread: str) -> None:
        if thread == "copilot":
            return
        self._rebuild_threads()
        if thread == self.thread:
            self._render()

    def _on_busy(self, thread: str, busy: bool) -> None:
        self._busy[thread] = busy
        if thread == self.thread:
            self._render()

    def _career_event(self, name: str, _payload: dict) -> None:
        if name in ("job_accepted", "flight_finished", "job_abandoned", "employment_changed", "hired", "career_reset"):
            self._rebuild_threads()
            self._render()

    # ------------------------------------------------------------------ actions
    def _send(self, text: str) -> None:
        text = text.strip()
        if text:
            self.input.clear()
            self.ctx.voice.shut_up()
            self.ctx.ask(text, self.thread)

    def _chip(self, minutes: int) -> None:
        self.ctx.voice.shut_up()
        self.ctx.answer_availability(self.thread, minutes)

    def _accept(self, job_id: int) -> None:
        self.ctx.accept_offer(job_id, self.thread)
        self._render()
        self.goto.emit("flight")

    def _decline(self, job_id: int) -> None:
        self.ctx.decline_offer(job_id, self.thread)

    def _ask_time_again(self) -> None:
        self.ctx.dispatcher.ask_availability(self.thread, "Sure.")

    def _clear(self) -> None:
        self.db.clear_messages(self.thread)
        if self.thread == GENERAL:
            self.db.add_message("assistant", self.ctx.dispatcher.greeting(GENERAL), GENERAL)
        else:
            self.db.set_meta(f"await:{self.thread}", "0")
            self.ctx.open_thread(self.thread)
        self.refresh()

    def _speak_toggled(self, on: bool) -> None:
        self.settings.voice.auto_speak_replies = on
        self.settings.save()
        if not on:
            self.ctx.voice.shut_up()

    # ---------------------------------------------------------------- voice
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

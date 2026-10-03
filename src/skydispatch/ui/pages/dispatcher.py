from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout)

from ...ai.personas import get_persona
from .. import theme
from ..widgets import StatusDot, heading, muted
from .base import Page

QUICK = [("Jobs for me", "What jobs do you have for me?"), ("Status report", "Give me a status report"),
         ("My hangar", "How's my hangar?"), ("Weather", "What's the weather at my departure airport?"),
         ("Best job", "What's the best paying job I can fly right now?")]


class DispatcherPage(Page):
    title = "Dispatcher"
    heard = Signal(str)
    heard_error = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        self._log: list[tuple[str, str]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(10)
        top = QHBoxLayout()
        self.title_lbl = heading("Dispatcher")
        top.addWidget(self.title_lbl)
        top.addStretch(1)
        self.status = StatusDot()
        top.addWidget(self.status)
        root.addLayout(top)
        self.sub = muted("")
        root.addWidget(self.sub)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        root.addWidget(self.view, 1)

        chips = QHBoxLayout()
        for label, q in QUICK:
            b = QPushButton(label)
            b.setStyleSheet("padding: 4px 10px; border-radius: 12px; font-size: 12px;")
            b.clicked.connect(lambda _=False, text=q: self._send(text))
            chips.addWidget(b)
        chips.addStretch(1)
        root.addLayout(chips)

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
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.talk)
        row.addWidget(self.stop_btn)
        root.addLayout(row)

        opts = QHBoxLayout()
        self.speak = QCheckBox("Speak replies")
        self.speak.setChecked(self.settings.voice.auto_speak_replies)
        self.speak.toggled.connect(self._speak_toggled)
        self.voice_note = muted("", wrap=False)
        clear = QPushButton("Clear conversation")
        clear.clicked.connect(self._clear)
        opts.addWidget(self.speak)
        opts.addWidget(self.voice_note)
        opts.addStretch(1)
        opts.addWidget(clear)
        root.addLayout(opts)

        ctx.chat.connect(self._on_chat)
        ctx.chat_busy.connect(self._on_busy)
        ctx.ai_status.connect(self._on_status)
        ctx.settings_changed.connect(self.refresh)
        self.heard.connect(self._on_heard)
        self.heard_error.connect(lambda m: ctx.toast.emit("warn", m))
        self._busy = False
        self._load_history()
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        persona = get_persona(self.settings.ai.persona)
        self.title_lbl.setText(f"Dispatcher: {persona.name}")
        self.sub.setText(f"{persona.title}  |  AI server: {self.settings.ai.base_url}")
        stt_ok, stt_msg = self.ctx.voice.stt_status()
        self.talk.setEnabled(stt_ok and self.settings.voice.stt_enabled)
        self.talk.setToolTip("" if stt_ok else stt_msg)
        tts_ok, _ = self.ctx.voice.tts_status()
        self.voice_note.setText(("Voice out: on" if tts_ok else "Voice out: unavailable") + "  |  " +
                                ("Voice in: on" if stt_ok else "Voice in: unavailable (see Settings > Voice)"))
        online = self.ctx.dispatcher.online
        self.status.set_state("ok" if online else "bad" if online is False else "off",
                              "online" if online else "offline" if online is False else "not checked")

    def _load_history(self) -> None:
        rows = self.db.messages(40)
        if not rows:
            greeting = self.ctx.dispatcher.greeting()
            self.db.add_message("assistant", greeting)
            rows = self.db.messages(1)
        self._log = [(r["role"], r["content"]) for r in rows]
        self._render()

    def _clear(self) -> None:
        self.db.clear_messages()
        self._log = []
        self._load_history()

    def _speak_toggled(self, on: bool) -> None:
        self.settings.voice.auto_speak_replies = on
        self.settings.save()
        if not on:
            self.ctx.voice.shut_up()

    def _on_status(self, ok: bool, msg: str) -> None:
        self.status.set_state("ok" if ok else "bad", "online" if ok else "offline")

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        self._render()

    def _on_chat(self, role: str, text: str) -> None:
        self._log.append((role, text))
        self._log = self._log[-200:]
        self._render()

    def _send(self, text: str) -> None:
        text = text.strip()
        if text:
            self.input.clear()
            self.ctx.voice.shut_up()
            self.ctx.ask(text)

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

    def _on_heard(self, text: str) -> None:
        self._send(text)

    # --------------------------------------------------------------- render
    def _render(self) -> None:
        p = theme.palette()
        parts = []
        for role, text in self._log:
            safe = html.escape(text).replace("\n", "<br>")
            if role == "user":
                parts.append(f'<table width="100%"><tr><td width="25%"></td><td style="background:{p.accent};'
                             f'color:{p.accent_text};padding:8px 12px;border-radius:10px;">{safe}</td></tr></table>'
                             f'<div style="height:6px"></div>')
            elif role == "system":
                parts.append(f'<p style="color:{p.warn};text-align:center">{safe}</p>')
            else:
                parts.append(f'<table width="100%"><tr><td style="background:{p.surface2};color:{p.text};'
                             f'padding:8px 12px;border-radius:10px;">{safe}</td><td width="25%"></td></tr></table>'
                             f'<div style="height:6px"></div>')
        if self._busy:
            parts.append(f'<p style="color:{p.muted}"><i>{get_persona(self.settings.ai.persona).name} is typing...</i></p>')
        self.view.setHtml(f'<body style="background:{p.surface}">' + "".join(parts) + "</body>")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

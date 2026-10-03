"""Chat widgets shared by the messenger and the copilot: bubbles, flight-offer cards and availability chips."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..ai.dispatcher import AVAILABILITY_CHIPS
from ..data.aircraft import get_type
from ..jobs.pricing import KIND_LABEL
from . import fmt, theme


class Bubble(QFrame):
    def __init__(self, role: str, text: str, kind: str = "text", name: str = ""):
        super().__init__()
        p = theme.palette()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        caption = {"hr": "HR", "assistant": name}.get(role, "")
        if kind == "callout":
            caption = f"{name} · callout"
        if caption:
            cap = QLabel(caption)
            cap.setStyleSheet(f"color:{p.muted};font-size:11px;font-weight:600;background:transparent;")
            lay.addWidget(cap)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(lbl)
        if role == "user":
            self.setStyleSheet(f"Bubble{{background:{p.accent};border-radius:12px;}} "
                               f"QLabel{{color:{p.accent_text};background:transparent;}}")
        elif role == "system":
            self.setStyleSheet(f"Bubble{{background:transparent;}} QLabel{{color:{p.warn};background:transparent;}}")
        elif role == "hr":
            self.setStyleSheet(f"Bubble{{background:{p.surface2};border:1px solid {p.good};border-radius:12px;}}"
                               f"QLabel{{color:{p.text};background:transparent;}}")
        elif kind == "callout":
            self.setStyleSheet(f"Bubble{{background:transparent;border-left:3px solid {p.warn};}} "
                               f"QLabel{{color:{p.text};font-style:italic;background:transparent;}}")
        else:
            self.setStyleSheet(f"Bubble{{background:{p.surface2};border-radius:12px;}} "
                               f"QLabel{{color:{p.text};background:transparent;}}")


class OfferCard(QFrame):
    accepted = Signal(int)
    declined = Signal(int)

    def __init__(self, ctx, job, summary: str, can_act: bool):
        super().__init__()
        p = theme.palette()
        self.setObjectName("offerCard")          # styled by the global theme so child buttons keep their styling
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        if job is None:
            lay.addWidget(QLabel("This flight is no longer available."))
            return
        t = get_type(job.provided_type) if job.provided_type else None
        title = QLabel(f"✈  {KIND_LABEL.get(job.kind, job.kind)}: {job.origin} → {job.dest}")
        title.setStyleSheet("font-weight:700;font-size:14px;background:transparent;")
        lay.addWidget(title)
        body = QLabel(summary)
        body.setWordWrap(True)
        body.setStyleSheet("background:transparent;")
        lay.addWidget(body)
        if t:
            plane = QLabel(f"Aircraft: {t.name} (company aircraft)  ·  Deadline {fmt.duration(job.deadline_minutes)}")
            plane.setObjectName("muted")
            lay.addWidget(plane)
        row = QHBoxLayout()
        if job.status == "offered":
            self.accept = QPushButton("Accept flight")
            self.accept.setObjectName("primary")
            self.accept.setEnabled(can_act)
            self.accept.setToolTip("" if can_act else "Finish or abandon your current flight first")
            self.decline = QPushButton("Decline")
            self.accept.clicked.connect(lambda: self.accepted.emit(job.id))
            self.decline.clicked.connect(lambda: self.declined.emit(job.id))
            row.addWidget(self.accept)
            row.addWidget(self.decline)
        else:
            state = {"accepted": "Accepted", "active": "In progress", "completed": "Completed", "failed": "Failed",
                     "declined": "Declined", "expired": "Expired"}.get(job.status, job.status)
            tone = {"completed": p.good, "accepted": p.accent, "active": p.accent, "failed": p.bad}.get(job.status, p.muted)
            lbl = QLabel(state)
            lbl.setStyleSheet(f"color:{tone};font-weight:700;background:transparent;")
            row.addWidget(lbl)
        row.addStretch(1)
        lay.addLayout(row)


class Chips(QWidget):
    chosen = Signal(int)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        for minutes, label in AVAILABILITY_CHIPS:
            b = QPushButton(label)
            b.setStyleSheet("padding:5px 14px;border-radius:14px;")
            b.clicked.connect(lambda _=False, m=minutes: self.chosen.emit(m))
            lay.addWidget(b)
        lay.addStretch(1)


class ChatView(QScrollArea):
    """Scrollable conversation rendered from database rows."""
    offer_accepted = Signal(int)
    offer_declined = Signal(int)
    availability_chosen = Signal(int)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._inner.setObjectName("chatInner")
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(8, 8, 8, 8)
        self._lay.setSpacing(8)
        self._lay.addStretch(1)
        self.setWidget(self._inner)

    def _clear(self) -> None:
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.hide()               # deleteLater() alone leaves the old widget painted until the event loop runs
                w.setParent(None)
                w.deleteLater()

    def _add(self, widget: QWidget, role: str = "assistant") -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        widget.setMaximumWidth(620)
        if role == "user":
            row.addStretch(1)
            row.addWidget(widget)
        elif role == "system":
            row.addStretch(1)
            row.addWidget(widget)
            row.addStretch(1)
        else:
            row.addWidget(widget)
            row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self._lay.insertWidget(self._lay.count() - 1, holder)

    def set_messages(self, rows, name: str, busy: bool = False, can_act: bool = True, show_chips: bool = False) -> None:
        p = theme.palette()
        self.viewport().setObjectName("chatViewport")          # selector required: a bare rule would cascade to buttons
        self.viewport().setStyleSheet(f"#chatViewport{{background:{p.surface};}}")
        self._inner.setStyleSheet(f"#chatInner{{background:{p.surface};}}")
        self._clear()
        for r in rows:
            role, kind = r["role"], r["kind"]
            if kind == "offer":
                try:
                    job = self.ctx.db.job(json.loads(r["payload"])["job_id"])
                except (ValueError, KeyError):
                    job = None
                card = OfferCard(self.ctx, job, r["content"], can_act)
                card.accepted.connect(self.offer_accepted)
                card.declined.connect(self.offer_declined)
                self._add(card)
            else:
                self._add(Bubble(role, r["content"], kind, name), role)
        if show_chips:
            chips = Chips()
            chips.chosen.connect(self.availability_chosen)
            self._add(chips)
        if busy:
            typing = QLabel(f"{name.split(' - ')[0]} is typing…")
            typing.setObjectName("muted")
            self._add(typing)
        self._fit()
        QTimer.singleShot(0, self._after_layout)

    def _fit(self) -> None:
        """Wrapped labels need height-for-width, which QScrollArea does not apply once content outgrows the
        viewport. Size the inner widget from the layout ourselves so rows are never squashed."""
        width = max(100, self.viewport().width())
        self._inner.setMinimumHeight(max(0, self._lay.totalHeightForWidth(width)))

    def _after_layout(self) -> None:
        self._fit()
        self._scroll_bottom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _scroll_bottom(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

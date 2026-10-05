"""Training and licences: keep your licence and medical current, earn ratings, and see what your life costs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import theme
from ..widgets import Card, heading, muted
from .base import Page


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            w = item.widget()
            w.hide()                      # deleteLater() alone leaves the old widget findable and painted until later
            w.setParent(None)
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())


class TrainingPage(Page):
    title = "Training"

    def __init__(self, ctx):
        super().__init__(ctx)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(10)
        outer.addWidget(heading("Training and licences"))
        outer.addWidget(muted("Your licence and medical must be current to fly contracts. Ratings unlock companies and "
                              "aircraft. Companies pay for the aircraft and fuel, but your life, your licences and your "
                              "training are on you."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 8, 0, 8)
        self.body_lay.setSpacing(14)
        scroll.setWidget(self.body)
        outer.addWidget(scroll, 1)
        ctx.career_event.connect(self._event)
        ctx.settings_changed.connect(self.refresh)
        self.refresh()

    def _event(self, name: str, _payload: dict) -> None:
        if name in ("pilot_changed", "credentials_changed", "training_done", "bills_charged", "flight_finished",
                    "career_started", "career_reset", "hangar_changed", "credential_expiring"):
            self.refresh()

    # ------------------------------------------------------------------
    def _row(self, left: str, right: str = "", button: QPushButton | None = None, tone: str | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        a = QLabel(left)
        a.setWordWrap(True)
        if tone:
            a.setStyleSheet(f"color:{getattr(theme.palette(), tone)};")
        row.addWidget(a, 1)
        if right:
            row.addWidget(muted(right, wrap=False))
        if button is not None:
            row.addWidget(button)
        return row

    def _button(self, text: str, fn, enabled: bool = True, primary: bool = False, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setEnabled(enabled)
        if primary:
            b.setObjectName("primary")
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def refresh(self) -> None:
        if not self.db.pilot():
            return
        d = self.ctx.training_summary()
        _clear(self.body_lay)

        card = Card()
        card.lay.addWidget(heading("Licence and medical", 2))
        for c in d["certs"]:
            state = (f"valid until {c['expires']} ({c['days_left']} days)" if c["valid"]
                     else f"EXPIRED ({c['expires']})")
            card.lay.addLayout(self._row(
                f"{c['label']}: {state}", "",
                self._button(f"Renew ({c['fee']})", lambda _=False, k=c["kind"]: self.ctx.renew_credential(k),
                             enabled=c["can_renew"] and c["affordable"],
                             tip="" if c["can_renew"] else "You can renew in the last 30 days before it expires"),
                None if c["valid"] else "bad"))
        self.body_lay.addWidget(card)

        card = Card()
        card.lay.addWidget(heading("Ratings and courses", 2))
        for c in d["courses"]:
            if c["held"]:
                text, right, btn, tone = f"{c['name']}: held", "", None, "good"
            elif c["in_training"]:
                text, right, btn, tone = f"{c['name']}: in training, finishes {c['finishes']}", "", None, "accent"
            else:
                why = c["problem"] or ("" if c["affordable"] else "You can't afford this yet.")
                text = f"{c['name']}  -  {c['blurb']}" + (f"\n{why}" if why else "")
                right = f"{c['fee']} | {c['duration']}"
                btn = self._button("Start course", lambda _=False, i=c["id"]: self.ctx.start_course(i),
                                   enabled=c["can_start"], primary=c["can_start"])
                tone = None if not why else "muted"
            card.lay.addLayout(self._row(text, right, btn, tone))
        if d["employers"]:
            card.lay.addWidget(muted("Your companies expect: " + "; ".join(
                f"{e['name']} ({', '.join(e['needs']) or 'no ratings'})" for e in d["employers"])))
        self.body_lay.addWidget(card)

        card = Card()
        card.lay.addWidget(heading("Monthly costs", 2))
        for b in d["bills"]:
            card.lay.addLayout(self._row(b["label"], b["amount"]))
        card.lay.addLayout(self._row("Total every 30 days", d["total"]))
        card.lay.addWidget(muted(f"Next bills: {d['next_due']}. {d['runway']}"))
        self.body_lay.addWidget(card)
        self.body_lay.addStretch(1)

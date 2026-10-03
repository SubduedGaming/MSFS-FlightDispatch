"""Job Board: your qualifications and the companies you can apply to."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...ai.dispatcher import employer_thread
from ...career import CareerError
from ...data.employers import EMPLOYERS, get_employer
from ...jobs.pricing import KIND_LABEL
from ...pilot import quals
from ...sim.installed import installed_types
from .. import fmt, theme
from ..widgets import Card, StatTile, heading, muted
from .base import Page


class JobBoardPage(Page):
    title = "Job Board"
    goto = Signal(str)
    open_thread = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("Job Board"))
        root.addWidget(muted("Apply to companies. They check your total time, recent experience, skill level and time "
                             "on their aircraft. Once hired, their dispatcher messages you flights."))

        tiles = QHBoxLayout()
        self.t_total, self.t_recent, self.t_skill, self.t_last = (
            StatTile("Total flight time"), StatTile("Recent experience"), StatTile("Skill level"),
            StatTile("Since last flight"))
        for t in (self.t_total, self.t_recent, self.t_skill, self.t_last):
            tiles.addWidget(t)
        root.addLayout(tiles)
        self.classes = muted("", wrap=True)
        root.addWidget(self.classes)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        board = QWidget()
        bl = QHBoxLayout(board)
        bl.setContentsMargins(8, 8, 8, 8)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(280)
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)
        self.detail = Card()
        self.d_title = QLabel("")
        self.d_title.setObjectName("h2")
        self.d_blurb = QLabel("")
        self.d_blurb.setWordWrap(True)
        self.d_facts = QLabel("")
        self.d_facts.setWordWrap(True)
        self.d_facts.setTextFormat(Qt.RichText)
        self.checks = QTableWidget(0, 4)
        self.checks.setHorizontalHeaderLabels(["Requirement", "Needed", "You have", ""])
        self.checks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.checks.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.checks.verticalHeader().hide()
        self.checks.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.checks.setMaximumHeight(170)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        row = QHBoxLayout()
        self.apply = QPushButton("Apply")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self._apply)
        self.message = QPushButton("Open messenger")
        self.message.clicked.connect(self._open_thread)
        self.resign = QPushButton("Resign")
        self.resign.setObjectName("danger")
        self.resign.clicked.connect(self._resign)
        for b in (self.apply, self.message, self.resign):
            row.addWidget(b)
        row.addStretch(1)
        for w in (self.d_title, self.d_blurb, self.d_facts, self.checks, self.status):
            self.detail.lay.addWidget(w)
        self.detail.lay.addLayout(row)
        self.detail.lay.addStretch(1)
        split.addWidget(self.detail)
        split.setSizes([300, 700])
        bl.addWidget(split)
        tabs.addTab(board, "Companies")

        hist = QWidget()
        hl = QVBoxLayout(hist)
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(["Company", "Result", "Date", "Details"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setWordWrap(True)
        hl.addWidget(self.history)
        tabs.addTab(hist, "Applications")

        ctx.career_event.connect(self._career_event)
        ctx.settings_changed.connect(self.refresh)
        self._ids: list[str] = []
        self.refresh()

    def _career_event(self, name: str, _p: dict) -> None:
        if name in ("flight_finished", "hired", "employment_changed", "application_result", "career_started",
                    "career_reset", "pilot_changed"):
            self.refresh()

    # ---------------------------------------------------------------------
    def _state(self, employer, q) -> tuple[str, str]:
        """(label, tone) for the list."""
        p = theme.palette()
        if self.db.is_employed_by(employer.id):
            return "Employed", p.good
        if self.career.employer_fleet_note(employer):
            return "Aircraft not installed", p.muted
        if quals.meets(employer, q):
            return "You qualify", p.accent
        return "Not yet qualified", p.warn

    def refresh(self) -> None:
        pilot = self.db.pilot()
        if not pilot:
            return
        q = self.career.qualifications()
        self.t_total.set_value(fmt.duration(q.total_h * 60))
        self.t_recent.set_value(f"{q.recent_h:.1f} h", "warn" if q.recent_h < 2 and q.total_h > 5 else None)
        self.t_recent.setToolTip(f"Hours flown recently, weighted so each flight's hours halve every "
                                 f"{self.settings.game.recency_half_life_days} days. It shrinks every day you don't fly. "
                                 f"Plain hours in the last 90 days: {q.last_90d_h:.1f}.")
        self.t_skill.set_value(f"{q.skill_level} ({q.skill:.0f})")
        days = q.days_since_last
        self.t_last.set_value("no flights yet" if days is None else "today" if days < 1 else f"{days:.0f} days",
                              "bad" if days is not None and days > 45 else "warn" if days is not None and days > 14 else None)
        cats = ", ".join(f"{k} {v:.0f} h" for k, v in q.by_category_h.items() if v >= 0.5)
        self.classes.setText(f"Hours by aircraft class: {cats}" if cats else "No hours logged on any aircraft class yet.")

        keep = self._ids[self.list.currentRow()] if 0 <= self.list.currentRow() < len(self._ids) else None
        self._ids = [e.id for e in EMPLOYERS]
        self.list.blockSignals(True)
        self.list.clear()
        for e in EMPLOYERS:
            label, _tone = self._state(e, q)
            item = QListWidgetItem(f"{e.name}\n{label}  ·  {e.tagline}")
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.list.setCurrentRow(self._ids.index(keep) if keep in self._ids else 0)
        self._show()
        self._fill_history()

    def _current(self):
        r = self.list.currentRow()
        return get_employer(self._ids[r]) if 0 <= r < len(self._ids) else None

    def _show(self) -> None:
        e = self._current()
        if e is None:
            return
        p = theme.palette()
        q = self.career.qualifications()
        employed = self.db.is_employed_by(e.id)
        installed = installed_types(self.settings, self.db)
        fleet = ", ".join(f"{t.name}" + ("" if installed is None or t.id in installed else " (not installed)")
                          for t in e.fleet_types())
        kinds = ", ".join(KIND_LABEL[k] for k in e.kinds)
        self.d_title.setText(f"{e.name}")
        self.d_blurb.setText(f"{e.blurb}")
        emp = self.db.employment(e.id)
        stats = (f"<br><b>Your record:</b> {emp['flights']} flights, {fmt.duration(emp['minutes'])}, "
                 f"{fmt.money(self.settings, emp['earned'])} earned" if emp and emp["status"] == "active" else "")
        self.d_facts.setText(f"<b>Base:</b> {e.base} &nbsp; <b>Work:</b> {kinds}<br><b>Aircraft:</b> {fleet}<br>"
                             f"<b>Pay:</b> {e.pay_factor:.0%} of standard, company covers fuel and running costs{stats}")
        checks = quals.check_requirements(e.reqs, q)
        self.checks.setRowCount(len(checks) or 1)
        if not checks:
            self.checks.setItem(0, 0, QTableWidgetItem("No requirements: they will train you"))
            for c in (1, 2, 3):
                self.checks.setItem(0, c, QTableWidgetItem(""))
        for r, c in enumerate(checks):
            for col, text in enumerate((c.label, c.required, c.actual, "✓" if c.met else "✗")):
                it = QTableWidgetItem(text)
                if col == 3:
                    it.setForeground(Qt.green if c.met else Qt.red)
                    it.setTextAlignment(Qt.AlignCenter)
                self.checks.setItem(r, col, it)
        note = self.career.employer_fleet_note(e)
        if employed:
            self.status.setText("You work here. Open the messenger to get flights.")
            self.status.setStyleSheet(f"color:{p.good};")
        elif note:
            self.status.setText(note + ".")
            self.status.setStyleSheet(f"color:{p.warn};")
        elif quals.meets(e, q):
            self.status.setText("You meet every requirement.")
            self.status.setStyleSheet(f"color:{p.good};")
        else:
            self.status.setText("You don't meet every requirement yet. You can still apply, but you'll be turned down.")
            self.status.setStyleSheet(f"color:{p.warn};")
        self.apply.setVisible(not employed)
        self.apply.setEnabled(not note)
        self.message.setVisible(employed)
        self.resign.setVisible(employed)

    def _fill_history(self) -> None:
        rows = self.db.applications()
        self.history.setRowCount(len(rows))
        for r, a in enumerate(rows):
            e = get_employer(a["employer_id"])
            vals = [e.name if e else a["employer_id"], a["status"].capitalize(), fmt.when(a["applied_at"]), a["message"]]
            for c, v in enumerate(vals):
                self.history.setItem(r, c, QTableWidgetItem(v))
        self.history.resizeRowsToContents()

    # ---------------------------------------------------------------------
    def _apply(self) -> None:
        e = self._current()
        if e is None:
            return
        try:
            result = self.ctx.apply_to_employer(e.id)
        except CareerError as exc:
            QMessageBox.warning(self, "Cannot apply", str(exc))
            return
        if result.accepted:
            QMessageBox.information(self, f"Welcome to {e.name}", result.message)
            self.refresh()
            self.open_thread.emit(employer_thread(e.id))
        else:
            box = QMessageBox(QMessageBox.Warning, f"{e.name}: application declined", result.message, parent=self)
            box.exec()
            self.refresh()

    def _open_thread(self) -> None:
        e = self._current()
        if e:
            self.open_thread.emit(employer_thread(e.id))

    def _resign(self) -> None:
        e = self._current()
        if e is None:
            return
        if QMessageBox.question(self, "Resign", f"Resign from {e.name}? Open flight offers will be withdrawn.") \
                != QMessageBox.Yes:
            return
        try:
            self.ctx.resign(e.id)
        except CareerError as exc:
            QMessageBox.warning(self, "Cannot resign", str(exc))
            return
        self.refresh()

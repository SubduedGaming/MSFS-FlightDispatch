from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from ...career import CareerError
from ...data.aircraft import get_type
from ...jobs.pricing import KIND_LABEL
from .. import fmt
from ..widgets import Card, RouteMap, heading, muted
from .base import Page


class MarketPage(Page):
    title = "Job Market"
    goto = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading("Job Market"))
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by airport or city...")
        self.search.setFixedWidth(220)
        self.kind = QComboBox()
        self.kind.addItem("All types", "")
        for k, v in KIND_LABEL.items():
            self.kind.addItem(v, k)
        self.flyable = QCheckBox("Only jobs I can fly now")
        self.btn_refresh = QPushButton("Find new contracts")
        for w in (self.search, self.kind, self.flyable, self.btn_refresh):
            top.addWidget(w)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Type", "Route", "Distance", "Load", "Payout", "Deadline", "Rep."])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(70)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        split.addWidget(self.table)

        self.detail = Card()
        self.d_title = QLabel("Select a job")
        self.d_title.setObjectName("h2")
        self.d_title.setWordWrap(True)
        self.d_text = QTextBrowser()
        self.d_text.setMinimumHeight(170)
        self.d_text.setFrameShape(QTextBrowser.NoFrame)
        self.map = RouteMap()
        self.map.setMinimumHeight(120)
        self.plane = QComboBox()
        self.plane_note = muted("")
        row = QHBoxLayout()
        self.btn_accept = QPushButton("Accept job")
        self.btn_accept.setObjectName("primary")
        self.btn_decline = QPushButton("Decline")
        self.btn_ask = QPushButton("Ask dispatcher")
        for b in (self.btn_accept, self.btn_decline, self.btn_ask):
            row.addWidget(b)
        for w in (self.d_title, self.d_text, self.map, QLabel("Assign aircraft:"), self.plane, self.plane_note):
            self.detail.lay.addWidget(w)
        self.detail.lay.addLayout(row)
        self.detail.lay.addStretch(1)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([640, 460])
        root.addWidget(split, 1)
        self.empty = muted("")
        root.addWidget(self.empty)

        self.table.itemSelectionChanged.connect(self._show)
        self.plane.currentIndexChanged.connect(self._plane_changed)
        self.search.textChanged.connect(self.refresh)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.flyable.toggled.connect(self.refresh)
        self.btn_refresh.clicked.connect(self._refresh_market)
        self.btn_accept.clicked.connect(self._accept)
        self.btn_decline.clicked.connect(self._decline)
        self.btn_ask.clicked.connect(self._ask)
        self._jobs: list = []
        self.refresh()

    # ------------------------------------------------------------------
    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self._jobs[rows[0].row()] if rows and rows[0].row() < len(self._jobs) else None

    def refresh(self) -> None:
        s = self.settings
        sel = self._selected()
        sel_id = sel.id if sel else None
        text = self.search.text().strip().lower()
        kind = self.kind.currentData()
        jobs = []
        for j in self.db.jobs("offered"):
            if kind and j.kind != kind:
                continue
            if text:
                o, d = self.db.airport(j.origin), self.db.airport(j.dest)
                hay = f"{j.origin} {j.dest} {o.city if o else ''} {d.city if d else ''} {o.name if o else ''} " \
                      f"{d.name if d else ''}".lower()
                if text not in hay:
                    continue
            if self.flyable.isChecked() and not any(e.ok for _, e in self.career.eligible_aircraft(j)):
                continue
            jobs.append(j)
        jobs.sort(key=lambda j: -j.payout)
        self._jobs = jobs
        self.table.blockSignals(True)
        self.table.setRowCount(len(jobs))
        for r, j in enumerate(jobs):
            vals = [KIND_LABEL[j.kind], f"{j.origin} \u2192 {j.dest}", fmt.dist(s, j.distance_nm),
                    f"{j.pax} pax" if j.pax else fmt.weight(s, j.cargo_lb), fmt.money(s, j.payout),
                    fmt.duration(j.deadline_minutes), f"{j.min_reputation:.0f}" if j.min_reputation else "-"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (2, 4, 5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
            if j.id == sel_id:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.empty.setText("" if jobs else "No contracts match. Try 'Find new contracts' or relax the filters.")
        self._show()

    def _show(self) -> None:
        j = self._selected()
        active = self.db.active_job() is not None
        has = j is not None
        for b in (self.btn_accept, self.btn_decline, self.btn_ask, self.plane):
            b.setEnabled(has)
        if not j:
            self.d_title.setText("Select a job")
            self.d_text.setHtml("")
            self.map.set_route(None, None)
            self.plane.clear()
            self.plane_note.setText("")
            return
        s = self.settings
        o, d = self.db.airport(j.origin), self.db.airport(j.dest)
        self.d_title.setText(j.title)
        self.d_text.setHtml(
            f"<p>{j.briefing}</p><p><b>Client:</b> {j.client}<br>"
            f"<b>From:</b> {o.name if o else j.origin} ({j.origin})<br>"
            f"<b>To:</b> {d.name if d else j.dest} ({j.dest})"
            f"{'' if not d else f' - runway {d.runway_ft:,} ft, elev {d.elevation_ft:,.0f} ft'}<br>"
            f"<b>Pays:</b> {fmt.money(s, j.payout)}  &nbsp; <b>Deadline:</b> {fmt.duration(j.deadline_minutes)} "
            f"&nbsp; <b>Expires:</b> {fmt.when(j.expires_at)}</p>")
        self.map.set_route((j.origin, o.lat, o.lon) if o else None, (j.dest, d.lat, d.lon) if d else None)
        self.plane.blockSignals(True)
        self.plane.clear()
        best = -1
        for a, e in self.career.eligible_aircraft(j):
            t = get_type(a.type_id)
            self.plane.addItem(f"{a.registration}  {t.name if t else a.type_id}  ({a.location_icao})"
                               + ("" if e.ok else "   - not eligible"), (a.id, e.ok, "; ".join(e.reasons)))
            if e.ok and best < 0:
                best = self.plane.count() - 1
        if not self.plane.count():
            self.plane.addItem("No aircraft in hangar", None)
        self.plane.setCurrentIndex(max(0, best))
        self.plane.blockSignals(False)
        self._plane_changed()
        self.btn_accept.setEnabled(not active)
        self.btn_accept.setToolTip("Finish or abandon your current job first" if active else "")

    def _plane_changed(self) -> None:
        data = self.plane.currentData()
        if not data:
            self.plane_note.setText("Buy an aircraft in the Hangar to take jobs.")
            self.btn_accept.setEnabled(False)
            return
        _, ok, why = data
        self.plane_note.setText("This aircraft can fly the job." if ok else why)
        self.plane_note.setObjectName("good" if ok else "warn")
        self.plane_note.style().unpolish(self.plane_note)
        self.plane_note.style().polish(self.plane_note)
        self.btn_accept.setEnabled(ok and self.db.active_job() is None)

    # ------------------------------------------------------------------
    def _accept(self) -> None:
        j, data = self._selected(), self.plane.currentData()
        if not j or not data:
            return
        try:
            self.career.accept_job(j.id, data[0])
        except CareerError as exc:
            QMessageBox.warning(self, "Cannot accept job", str(exc))
            return
        self.ctx.toast.emit("good", "Job accepted. Start your engines when ready.")
        self.goto.emit("flight")

    def _decline(self) -> None:
        j = self._selected()
        if j:
            try:
                self.career.decline_job(j.id)
            except CareerError as exc:
                QMessageBox.warning(self, "Cannot decline", str(exc))

    def _ask(self) -> None:
        j = self._selected()
        if j:
            self.goto.emit("dispatcher")
            self.ctx.ask(f"Tell me about job {j.id}, {j.origin} to {j.dest}. Is it worth taking?")

    def _refresh_market(self) -> None:
        self.btn_refresh.setEnabled(False)
        self.ctx.refresh_market()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self.btn_refresh.setEnabled(True))

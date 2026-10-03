from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout)

from ...flight.scoring import landing_label
from .. import fmt
from ..widgets import Card, ProfileChart, RouteMap, heading, muted
from .base import Page


class LogbookPage(Page):
    title = "Logbook"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading("Logbook"))
        top.addStretch(1)
        self.totals = muted("", wrap=False)
        top.addWidget(self.totals)
        export = QPushButton("Export CSV")
        export.clicked.connect(self._export)
        top.addWidget(export)
        root.addLayout(top)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Date", "Route", "Aircraft", "Air time", "Distance", "Landing", "Score",
                                              "Result", "Net"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for col in (0, 5):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        split.addWidget(self.table)

        self.detail = Card()
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.d_title = QLabel("Select a flight")
        self.d_title.setObjectName("h2")
        self.d_text = QTextBrowser()
        self.d_text.setFrameShape(QTextBrowser.NoFrame)
        left.addWidget(self.d_title)
        left.addWidget(self.d_text, 1)
        right = QVBoxLayout()
        self.map = RouteMap()
        self.map.setMinimumHeight(150)
        self.chart = ProfileChart()
        right.addWidget(self.map, 1)
        right.addWidget(self.chart)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        self.detail.lay.addLayout(row)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        self.table.itemSelectionChanged.connect(self._show)
        self._flights: list = []
        self.refresh()

    def refresh(self) -> None:
        s = self.settings
        sel = self._current()
        self._flights = self.db.flights()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._flights))
        for r, f in enumerate(self._flights):
            ac = self.db.aircraft(f.aircraft_id) if f.aircraft_id else None
            vals = [fmt.when(f.started_at), f"{f.dep or '?'} - {f.arr or '?'}",
                    ac.registration if ac else (f.sim_title[:26] or "-"), fmt.duration(f.air_min),
                    fmt.dist(s, f.distance_nm), "-" if f.landing_fpm is None else
                    f"{abs(f.landing_fpm):.0f} fpm ({landing_label(f.landing_fpm)})",
                    f"{f.score:.0f}" if f.outcome != "in_progress" else "-", f.outcome.replace("_", " "),
                    fmt.money(s, f.payout - f.costs, signed=True)]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
            if sel and sel.id == f.id:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        total = sum(f.air_min for f in self._flights)
        self.totals.setText(f"{len(self._flights)} flights  |  {fmt.duration(total)}  |  "
                            f"{fmt.dist(s, sum(f.distance_nm for f in self._flights))}")
        self._show()

    def _current(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self._flights[rows[0].row()] if rows and rows[0].row() < len(self._flights) else None

    def _show(self) -> None:
        f = self._current()
        if not f:
            self.d_title.setText("Select a flight")
            self.d_text.setHtml("")
            self.map.set_route(None, None)
            self.chart.set_data([])
            return
        s = self.settings
        self.d_title.setText(f"{f.dep or '?'} to {f.arr or '?'}  -  {f.outcome.replace('_', ' ')}")
        events = "".join(f"&bull; {e['kind'].replace('_', ' ')}: {e['detail']}<br>" for e in self.db.events(f.id))
        job = self.db.job(f.job_id) if f.job_id else None
        self.d_text.setHtml(
            f"<p><b>{job.title if job else 'Free flight (no contract)'}</b><br>Sim aircraft: {f.sim_title or '-'}</p>"
            f"<p>Block {fmt.duration(f.block_min)} &middot; Air {fmt.duration(f.air_min)} &middot; "
            f"{fmt.dist(s, f.distance_nm)} &middot; fuel {f.fuel_used_gal:.1f} gal<br>"
            f"Max altitude {f.max_alt_ft:,.0f} ft &middot; Max IAS {f.max_ias:.0f} kt &middot; "
            f"Max G {f.max_g:.2f} &middot; Overspeed {f.overspeed_s:.0f}s<br>"
            f"Score <b>{f.score:.0f}</b> &middot; Payout {fmt.money(s, f.payout)} &middot; "
            f"Costs {fmt.money(s, f.costs)}</p>"
            + (f"<p><i>Dispatcher: {f.debrief}</i></p>" if f.debrief else "")
            + f"<p>{events}</p>")
        tel = self.db.telemetry(f.id)
        self.chart.set_data(tel)
        o, d = self.db.airport(f.dep) if f.dep else None, self.db.airport(f.arr) if f.arr else None
        self.map.set_route((o.icao, o.lat, o.lon) if o else None, (d.icao, d.lat, d.lon) if d else None)
        self.map.set_track([(r["lat"], r["lon"]) for r in tel if r["lat"] is not None])

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export logbook", "logbook.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["id", "started", "dep", "arr", "aircraft", "air_min", "distance_nm", "fuel_gal",
                            "landing_fpm", "max_g", "score", "outcome", "payout", "costs"])
                for f in self._flights:
                    w.writerow([f.id, f.started_at, f.dep, f.arr, f.sim_title, round(f.air_min, 1),
                                round(f.distance_nm, 1), round(f.fuel_used_gal, 1), f.landing_fpm,
                                round(f.max_g, 2), f.score, f.outcome, f.payout, f.costs])
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.ctx.toast.emit("good", f"Exported {len(self._flights)} flights")

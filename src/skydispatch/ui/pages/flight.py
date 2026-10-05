from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QMessageBox, QProgressBar, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ...data.aircraft import get_type
from ...data.employers import flight_label
from ...sim.base import SimState
from .. import fmt
from ..copilot_panel import CopilotPanel
from ..plan_card import PlanCard
from ..widgets import Card, RouteMap, heading, muted
from .base import Page

PHASE_LABEL = {"parked": "Waiting for engine start", "taxi_out": "Taxi out", "takeoff": "Takeoff roll",
               "climb": "Climb", "cruise": "Cruise", "descent": "Descent", "landed": "Landed - rollout",
               "taxi_in": "Taxi in", "arrived": "Arrived"}


class FlightPage(Page):
    title = "Flight"
    goto = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading("Flight Tracker"))
        top.addStretch(1)
        self.link = QLabel("")
        top.addWidget(self.link)
        root.addLayout(top)

        self.summary = Card()
        self.job_lbl = QLabel("")
        self.job_lbl.setObjectName("h2")
        self.job_lbl.setWordWrap(True)
        self.phase_lbl = QLabel("")
        self.phase_lbl.setStyleSheet("font-size: 26px; font-weight: 700;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.eta_lbl = muted("")
        for w in (self.job_lbl, self.phase_lbl, self.progress, self.eta_lbl):
            self.summary.lay.addWidget(w)
        root.addWidget(self.summary)

        self.plan_card = PlanCard(ctx)
        root.addWidget(self.plan_card)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        self.map = RouteMap()
        mid.addWidget(self.map, 2)
        right = QVBoxLayout()
        grid_card = Card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self.cells: dict[str, QLabel] = {}
        names = [("alt", "Altitude"), ("ias", "IAS"), ("gs", "Ground speed"), ("vs", "Vertical speed"),
                 ("hdg", "Heading"), ("fuel", "Fuel"), ("g", "G-force"), ("dist", "Distance flown")]
        for i, (key, label) in enumerate(names):
            cap = QLabel(label.upper())
            cap.setObjectName("statLabel")
            val = QLabel("-")
            val.setObjectName("statValue")
            self.cells[key] = val
            grid.addWidget(cap, (i // 4) * 2, i % 4)
            grid.addWidget(val, (i // 4) * 2 + 1, i % 4)
        grid_card.lay.addLayout(grid)
        right.addWidget(grid_card)
        tabs = QTabWidget()
        self.copilot = CopilotPanel(ctx)
        tabs.addTab(self.copilot, "Copilot")
        log_tab = QWidget()
        ll = QVBoxLayout(log_tab)
        ll.setContentsMargins(6, 8, 6, 6)
        self.events = QListWidget()
        self.events.setMinimumHeight(120)
        ll.addWidget(self.events)
        tabs.addTab(log_tab, "Flight log")
        right.addWidget(tabs, 1)
        mid.addLayout(right, 3)
        root.addLayout(mid, 1)

        btns = QHBoxLayout()
        self.btn_demo = QPushButton("Start demo flight (simulated mode)")
        self.btn_demo.setObjectName("primary")
        self.btn_demo.clicked.connect(self._demo)
        self.btn_abandon = QPushButton("Abandon flight")
        self.btn_abandon.setObjectName("danger")
        self.btn_abandon.clicked.connect(self._abandon)
        btns.addWidget(self.btn_demo)
        btns.addStretch(1)
        btns.addWidget(self.btn_abandon)
        root.addLayout(btns)

        ctx.sim_state.connect(self._on_state)
        ctx.sim_status.connect(self._on_status)
        ctx.career_event.connect(self._on_career)
        self._last_set = None
        self.refresh()

    # ------------------------------------------------------------------
    def _on_status(self, status: str, message: str) -> None:
        txt = {"connected": "Connected", "connecting": "Connecting...", "error": "Error",
               "disconnected": "Disconnected"}.get(status, status)
        self.link.setText(f"<b>Sim:</b> {txt}{' - ' + message if message else ''}")

    def _on_career(self, name: str, payload: dict) -> None:
        if self.ctx.closed:
            return
        if name == "flight_event":
            self.events.addItem(f"{payload['kind'].replace('_', ' ').title()}: {payload['detail']}")
            self.events.scrollToBottom()
        if name in ("job_accepted", "flight_finished", "job_abandoned", "career_started"):
            if name == "flight_finished":
                s = payload["settlement"]
                self.events.addItem(f"Result: {s.metrics.outcome}, score {s.score.score:.0f} ({s.score.grade})")
            self.refresh()

    def refresh(self) -> None:
        job = self.db.active_job()
        o = self.db.airport(job.origin) if job else None
        d = self.db.airport(job.dest) if job else None
        key = job.id if job else None
        if key != self._last_set:
            self._last_set = key
            self.events.clear()
            self.map.set_route((o.icao, o.lat, o.lon) if o else None, (d.icao, d.lat, d.lon) if d else None)
        if job:
            aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
            t = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
            plane = f"{t.name} (company aircraft)" if job.employer_id and t else \
                (f"{t.name} {aircraft.registration}" if t and aircraft else "")
            fl = flight_label(job)
            self.job_lbl.setText(f"{job.title}\n{fl + '  |  ' if fl else ''}{job.origin} to {job.dest}  |  pays "
                                 f"{fmt.money(self.settings, job.payout)}  |  {plane}")
        else:
            self.job_lbl.setText("Free flight: no contract. Everything you fly is still logged to your logbook.")
        self.btn_abandon.setVisible(job is not None)
        self.btn_demo.setVisible(self.ctx.simulated is not None)
        self.btn_demo.setEnabled(job is not None)
        live = self.career.live()
        self.phase_lbl.setText(PHASE_LABEL.get(live.phase if live else "parked", ""))

    def _on_state(self, s: SimState) -> None:
        if self.ctx.closed:
            return
        live = self.career.live()
        phase = live.phase if live else "parked"
        self.phase_lbl.setText(PHASE_LABEL.get(phase, phase))
        c = self.cells
        c["alt"].setText(f"{s.alt_msl:,.0f} ft")
        c["ias"].setText(f"{s.ias:.0f} kt")
        c["gs"].setText(f"{s.gs:.0f} kt")
        c["vs"].setText(f"{s.vs:+,.0f} fpm")
        c["hdg"].setText(f"{s.heading:03.0f}°")
        c["fuel"].setText(f"{s.fuel_gal:.1f} gal")
        c["g"].setText(f"{s.g_force:.2f} G")
        if live:
            c["dist"].setText(fmt.dist(self.settings, live.distance_nm))
            self.progress.setValue(int(live.progress * 1000))
            eta = f"ETA {fmt.duration(live.eta_min)}  |  " if live.eta_min else ""
            rem = f"{fmt.dist(self.settings, live.remaining_nm)} to go  |  " if live.remaining_nm is not None else ""
            self.eta_lbl.setText(f"{eta}{rem}block time {fmt.duration(live.elapsed_min)}  |  max {live.max_g:.2f} G")
        self.map.set_aircraft(s.lat, s.lon, s.heading)

    def _demo(self) -> None:
        err = self.ctx.demo_fly_active_job()
        if err:
            QMessageBox.information(self, "Demo flight", err)
        else:
            self.events.addItem("Demo flight started (time is accelerated).")

    def _abandon(self) -> None:
        if QMessageBox.question(self, "Abandon flight", "Abandon the current job? Reputation will suffer.") \
                == QMessageBox.Yes:
            self.career.abandon_job()
            self.refresh()

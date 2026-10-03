from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import fmt
from ..widgets import Card, StatTile, heading, muted
from .base import Page


class DashboardPage(Page):
    title = "Dashboard"
    goto = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        self.hello = heading("Welcome back")
        self.sub = muted("")
        root.addWidget(self.hello)
        root.addWidget(self.sub)

        tiles = QHBoxLayout()
        self.t_balance, self.t_rep, self.t_hours, self.t_recent, self.t_skill, self.t_fleet = (
            StatTile("Balance"), StatTile("Reputation"), StatTile("Flight time"), StatTile("Recent experience"),
            StatTile("Skill"), StatTile("Fleet"))
        for t in (self.t_balance, self.t_rep, self.t_hours, self.t_recent, self.t_skill, self.t_fleet):
            tiles.addWidget(t)
        root.addLayout(tiles)

        mid = QHBoxLayout()
        mid.setSpacing(16)
        self.job_card = Card()
        self.job_card.lay.addWidget(heading("Current contract", 2))
        self.job_title = QLabel("")
        self.job_title.setWordWrap(True)
        self.job_info = muted("")
        self.job_card.lay.addWidget(self.job_title)
        self.job_card.lay.addWidget(self.job_info)
        row = QHBoxLayout()
        self.btn_flight = QPushButton("Open flight tracker")
        self.btn_flight.setObjectName("primary")
        self.btn_flight.clicked.connect(lambda: self.goto.emit("flight"))
        self.btn_market = QPushButton("Find work")
        self.btn_market.clicked.connect(lambda: self.goto.emit("jobboard"))
        self.btn_abandon = QPushButton("Abandon")
        self.btn_abandon.setObjectName("danger")
        self.btn_abandon.clicked.connect(self._abandon)
        for b in (self.btn_flight, self.btn_market, self.btn_abandon):
            row.addWidget(b)
        row.addStretch(1)
        self.job_card.lay.addLayout(row)
        mid.addWidget(self.job_card, 3)

        self.sys_card = Card()
        self.sys_card.lay.addWidget(heading("Systems", 2))
        self.sim_lbl, self.ai_lbl, self.voice_lbl = QLabel(""), QLabel(""), QLabel("")
        for w in (self.sim_lbl, self.ai_lbl, self.voice_lbl):
            w.setWordWrap(True)
            self.sys_card.lay.addWidget(w)
        b = QPushButton("Open settings")
        b.clicked.connect(lambda: self.goto.emit("settings"))
        self.sys_card.lay.addWidget(b, 0, Qt.AlignLeft)
        mid.addWidget(self.sys_card, 2)
        root.addLayout(mid)

        root.addWidget(heading("Recent flights", 2))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Route", "Aircraft", "Landing", "Score", "Net"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, 1)

        ctx.sim_status.connect(lambda *_: self._systems())
        ctx.ai_status.connect(lambda *_: self._systems())
        self.refresh()

    def _abandon(self) -> None:
        if QMessageBox.question(self, "Abandon job", "Abandon this job? It will cost reputation.") == QMessageBox.Yes:
            self.career.abandon_job()
            self.refresh()

    def _systems(self) -> None:
        sim = self.ctx.provider
        mode = self.settings.sim.mode
        state = sim.status if sim else "disconnected"
        self.sim_lbl.setText(f"<b>Simulator</b> ({mode}): {state}")
        online = self.ctx.dispatcher.online
        self.ai_lbl.setText("<b>AI dispatcher</b> (LM Studio): " +
                            ("online" if online else "offline" if online is False else "not checked yet"))
        ok, msg = self.ctx.voice.tts_status()
        stt_ok, _ = self.ctx.voice.stt_status()
        self.voice_lbl.setText(f"<b>Voice</b>: speech out {'ready' if ok else 'unavailable'}, "
                               f"speech in {'ready' if stt_ok else 'unavailable'}")

    def refresh(self) -> None:
        s = self.settings
        pilot = self.db.pilot()
        if not pilot:
            return
        self.hello.setText(f"Welcome back, {pilot.rank} {pilot.name}")
        self.sub.setText(f"Callsign {pilot.callsign}  |  Home base {pilot.home_icao}")
        self.t_balance.set_value(fmt.money(s, pilot.balance), "bad" if pilot.balance < 0 else None)
        self.t_rep.set_value(f"{pilot.reputation:.0f} / 100", "good" if pilot.reputation >= 70 else
                             "warn" if pilot.reputation < 40 else None)
        self.t_hours.set_value(fmt.duration(pilot.total_minutes))
        nxt = pilot.next_rank_xp
        q = self.career.qualifications()
        self.t_recent.set_value(f"{q.recent_h:.1f} h", "warn" if q.total_h > 5 and q.recent_h < 2 else None)
        days = q.days_since_last
        self.t_recent.setToolTip("Recent experience halves every "
                                 f"{self.settings.game.recency_half_life_days} days without flying. "
                                 + ("No flights yet." if days is None else f"Last flight: {days:.0f} days ago."))
        self.t_skill.set_value(pilot.skill_level)
        self.t_skill.setToolTip(f"Skill rating {pilot.skill:.0f}/100, rank {pilot.rank} ({pilot.xp} XP"
                                + (f", {nxt} for next rank)" if nxt else ", max rank)"))
        self.t_fleet.set_value(str(len(self.db.hangar())))
        job = self.db.active_job()
        if job:
            self.job_title.setText(f"<b>{job.title}</b>")
            aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
            who = job.client if job.employer_id else (aircraft.registration if aircraft else "no aircraft")
            self.job_info.setText(f"{job.origin} to {job.dest}  |  {fmt.dist(s, job.distance_nm)}  |  "
                                  f"pays {fmt.money(s, job.payout)}  |  {who}  |  {job.status}")
        else:
            self.job_title.setText("No active contract")
            self.job_info.setText("Apply for a job, ask your dispatcher for a flight, or take a freelance contract. "
                                  "Flights without a contract are still logged.")
        for b in (self.btn_flight, self.btn_abandon):
            b.setVisible(job is not None)
        flights = self.db.flights(8)
        self.table.setRowCount(len(flights))
        for r, f in enumerate(flights):
            ac = self.db.aircraft(f.aircraft_id) if f.aircraft_id else None
            vals = [fmt.when(f.started_at), f"{f.dep or '?'} - {f.arr or '?'}",
                    ac.registration if ac else (f.sim_title[:28] or "-"),
                    "-" if f.landing_fpm is None else f"{abs(f.landing_fpm):.0f} fpm",
                    f"{f.score:.0f}" if f.outcome != "in_progress" else "-",
                    fmt.money(s, f.payout - f.costs, signed=True)]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self._systems()

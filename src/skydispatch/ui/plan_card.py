"""Flight page card: SimBrief plan and loading the sim aircraft to match SkyDispatch."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..planning.loadout import LoadoutError
from . import theme
from .widgets import Card, heading, muted


class PlanCard(Card):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.lay.addWidget(heading("Flight plan and loadout", 2))
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.text.setOpenExternalLinks(False)
        self.text.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        self.lay.addWidget(self.text)
        self.hint = muted("")
        self.lay.addWidget(self.hint)
        row = QHBoxLayout()
        self.b_plan = QPushButton("Plan on SimBrief")
        self.b_plan.setToolTip("Opens SimBrief with this flight filled in. Press Generate there, then Import.")
        self.b_import = QPushButton("Import plan")
        self.b_refuel = QPushButton("Refuel to plan")
        self.b_load = QPushButton("Load aircraft in sim")
        self.b_load.setObjectName("primary")
        self.b_load.setToolTip("Sets the sim aircraft's fuel and payload to match SkyDispatch. "
                               "The aircraft must be parked with engines off.")
        for b in (self.b_plan, self.b_import, self.b_refuel, self.b_load):
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.b_plan.clicked.connect(self._open_simbrief)
        self.b_import.clicked.connect(ctx.import_simbrief)
        self.b_refuel.clicked.connect(self._guard(ctx.refuel_to_plan))
        self.b_load.clicked.connect(lambda: ctx.sync_loadout())
        ctx.plan_changed.connect(self.refresh)
        ctx.settings_changed.connect(self.refresh)
        ctx.career_event.connect(lambda name, _p: self.refresh() if name in (
            "job_accepted", "job_abandoned", "flight_finished", "hangar_changed", "career_started") else None)
        self.refresh()

    def _guard(self, fn):
        def run():
            try:
                fn()
            except Exception as exc:                   # HangarError, LoadoutError: show, don't crash
                self.ctx.toast.emit("warn", str(exc))
        return run

    def _open_simbrief(self) -> None:
        try:
            QDesktopServices.openUrl(QUrl(self.ctx.simbrief_link()))
        except LoadoutError as exc:
            self.ctx.toast.emit("warn", str(exc))

    def refresh(self) -> None:
        if self.ctx.closed:
            return
        d = self.ctx.plan_summary()
        self.setVisible(d["has_job"])
        if not d["has_job"]:
            return
        p = theme.palette()
        lines = []
        o = d["ofp"]
        if o:
            lines.append(f"<b>SimBrief plan:</b> {escape(o['route'])}<br>{o['altitude']} &nbsp; {o['distance']} &nbsp; "
                         f"{o['ete']} &nbsp; alternate {escape(o['alternate'])}<br>"
                         f"Block fuel {o['block_fuel']} &nbsp; reserve {o['reserve_fuel']}"
                         + (f' &nbsp; <a href="{escape(o["pdf"])}">Open the PDF</a>' if o["pdf"] else "")
                         + (f"<br><span style='color:{p.warn}'>This plan is more than a day old.</span>" if o["stale"] else ""))
        else:
            lines.append("<b>SimBrief plan:</b> none for this flight yet.")
        ld = d["loadout"]
        lines.append(f"<b>Aircraft should carry:</b> {ld['fuel']} fuel ({ld['fuel_source']}) and {escape(ld['payload'])}.")
        for n in ld["notes"]:
            lines.append(f"<span style='color:{p.warn}'>{escape(n)}</span>")
        sim = d["sim"]
        if sim:
            colour, mark = (p.good, "matches") if sim["matches"] else (p.warn, "differs from SkyDispatch")
            lines.append(f"<b>In the sim now:</b> {sim['fuel']} fuel, {sim['payload']} payload "
                         f"<span style='color:{colour}'>({mark})</span>")
        self.text.setText("<br>".join(lines))
        self.hint.setText("" if d["user_set"] else "Set your SimBrief username in Settings > General to import plans.")
        self.b_import.setEnabled(d["user_set"])
        self.b_refuel.setVisible(d["can_refuel"])

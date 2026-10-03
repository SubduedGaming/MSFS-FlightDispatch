from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ...data.aircraft import CATALOG, get_type
from ...hangar.service import (HangarError, airworthiness, inspection_cost, repair_cost, resale_value)
from .. import fmt
from ..widgets import Card, heading, muted
from .base import Page


class DealerDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Aircraft dealer")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Aircraft dealer"))
        self.table = QTableWidget(len(CATALOG), 7)
        self.table.setHorizontalHeaderLabels(["Aircraft", "Class", "Seats", "Cargo", "Range", "Cruise", "Price"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        s = ctx.settings
        for r, t in enumerate(CATALOG):
            vals = [t.name, t.category, str(t.pax), fmt.weight(s, t.cargo_lb), fmt.dist(s, t.range_nm),
                    f"{t.cruise_kts} kt", fmt.money(s, t.price)]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        lay.addWidget(self.table, 1)
        form = QFormLayout()
        self.cond = QComboBox()
        self.cond.addItem("New (full price)", False)
        self.cond.addItem("Used (-30%, worn, more hours)", True)
        self.loc = QLineEdit(ctx.db.pilot().home_icao if ctx.db.pilot() else "")
        self.loc.setMaxLength(4)
        self.loc.setPlaceholderText("ICAO, e.g. EGLL")
        self.nick = QLineEdit()
        self.nick.setPlaceholderText("Optional nickname")
        form.addRow("Condition:", self.cond)
        form.addRow("Deliver to airport:", self.loc)
        form.addRow("Nickname:", self.nick)
        lay.addLayout(form)
        self.msg = muted("")
        lay.addWidget(self.msg)
        bb = QDialogButtonBox()
        self.buy_btn = bb.addButton("Buy", QDialogButtonBox.AcceptRole)
        self.buy_btn.setObjectName("primary")
        bb.addButton(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        self.buy_btn.clicked.connect(self._buy)
        lay.addWidget(bb)

    def _buy(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.msg.setText("Select an aircraft first.")
            return
        t = CATALOG[row]
        used = bool(self.cond.currentData())
        price = round(t.price * (0.7 if used else 1.0), -2)
        if QMessageBox.question(self, "Confirm purchase",
                                f"Buy {t.name} for {fmt.money(self.ctx.settings, price)}?") != QMessageBox.Yes:
            return
        try:
            a = self.ctx.career.hangar.buy(t.id, self.loc.text().strip().upper(), self.nick.text(), used)
        except HangarError as exc:
            self.msg.setText(str(exc))
            return
        self.ctx.career._fire("hangar_changed")
        self.ctx.career._fire("pilot_changed")
        self.ctx.toast.emit("good", f"Purchased {t.name} ({a.registration})")
        self.accept()


class HangarPage(Page):
    title = "Hangar"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(heading("Hangar"))
        top.addStretch(1)
        buy = QPushButton("Buy aircraft")
        buy.setObjectName("primary")
        buy.clicked.connect(self._dealer)
        top.addWidget(buy)
        root.addLayout(top)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        fleet = QWidget()
        fl = QHBoxLayout(fleet)
        fl.setContentsMargins(10, 10, 10, 10)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(self.list)
        self.detail = Card()
        self.d_title = QLabel("")
        self.d_title.setObjectName("h2")
        self.d_info = QLabel("")
        self.d_info.setWordWrap(True)
        self.d_info.setTextFormat(Qt.RichText)
        self.bars: dict[str, QProgressBar] = {}
        self.detail.lay.addWidget(self.d_title)
        self.detail.lay.addWidget(self.d_info)
        for key, label in (("condition", "Condition"), ("inspection", "Hours to next inspection"), ("fuel", "Fuel")):
            self.detail.lay.addWidget(QLabel(label))
            b = QProgressBar()
            b.setRange(0, 100)
            self.bars[key] = b
            self.detail.lay.addWidget(b)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.detail.lay.addWidget(self.status)
        row = QHBoxLayout()
        self.b_fuel, self.b_inspect, self.b_repair, self.b_rename, self.b_sell = (
            QPushButton("Refuel"), QPushButton("Inspection"), QPushButton("Repair"), QPushButton("Rename"),
            QPushButton("Sell"))
        self.b_sell.setObjectName("danger")
        for b in (self.b_fuel, self.b_inspect, self.b_repair, self.b_rename, self.b_sell):
            row.addWidget(b)
        self.detail.lay.addLayout(row)
        self.detail.lay.addStretch(1)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)
        fl.addWidget(split)
        tabs.addTab(fleet, "My aircraft")

        flown = QWidget()
        fv = QVBoxLayout(flown)
        fv.addWidget(muted("Every aircraft you fly in the simulator is recorded here, whether or not you own it."))
        self.flown = QTableWidget(0, 5)
        self.flown.setHorizontalHeaderLabels(["Simulator aircraft", "Catalog match", "Flights", "Time", "Last flown"])
        self.flown.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.flown.verticalHeader().hide()
        self.flown.setEditTriggers(QAbstractItemView.NoEditTriggers)
        fv.addWidget(self.flown)
        tabs.addTab(flown, "Aircraft flown")

        self.list.currentRowChanged.connect(self._show)
        self.b_fuel.clicked.connect(lambda: self._do("refuel"))
        self.b_inspect.clicked.connect(lambda: self._do("inspect"))
        self.b_repair.clicked.connect(lambda: self._do("repair"))
        self.b_rename.clicked.connect(self._rename)
        self.b_sell.clicked.connect(self._sell)
        self._ids: list[int] = []
        self.refresh()

    def _current(self):
        r = self.list.currentRow()
        return self.db.aircraft(self._ids[r]) if 0 <= r < len(self._ids) else None

    def refresh(self) -> None:
        keep = self._current().id if self._current() else None
        fleet = self.db.hangar()
        self._ids = [a.id for a in fleet]
        self.list.blockSignals(True)
        self.list.clear()
        for a in fleet:
            t = get_type(a.type_id)
            ok, _ = airworthiness(a, t) if t else (False, "")
            item = QListWidgetItem(f"{a.registration}{' - ' + a.nickname if a.nickname else ''}\n"
                                   f"{t.name if t else a.type_id}  |  at {a.location_icao}"
                                   f"{'' if ok else '  |  GROUNDED'}")
            self.list.addItem(item)
        self.list.blockSignals(False)
        if fleet:
            self.list.setCurrentRow(self._ids.index(keep) if keep in self._ids else 0)
        self._show()
        rows = self.db.aircraft_flown()
        self.flown.setRowCount(len(rows))
        for r, row in enumerate(rows):
            t = get_type(row["type_id"]) if row["type_id"] else None
            vals = [row["sim_title"], t.name if t else "Not in catalog", str(row["flights"]),
                    fmt.duration(row["minutes"]), fmt.when(row["last_flown"])]
            for c, v in enumerate(vals):
                self.flown.setItem(r, c, QTableWidgetItem(v))

    def _show(self) -> None:
        a = self._current()
        for b in (self.b_fuel, self.b_inspect, self.b_repair, self.b_rename, self.b_sell):
            b.setEnabled(a is not None)
        if not a:
            self.d_title.setText("Your hangar is empty")
            self.d_info.setText("Visit the dealer to buy your first aircraft.")
            return
        t = get_type(a.type_id)
        s = self.settings
        self.d_title.setText(f"{a.registration}  {t.name if t else a.type_id}")
        self.d_info.setText(
            f"Location: <b>{a.location_icao}</b> &nbsp; Total time: <b>{a.hours_total:.1f} h</b><br>"
            f"Cruise {t.cruise_kts} kt &nbsp; Range {fmt.dist(s, t.range_nm)} &nbsp; Seats {t.pax} &nbsp; "
            f"Cargo {fmt.weight(s, t.cargo_lb)}<br>Value: {fmt.money(s, resale_value(a, t))}" if t else "")
        self.bars["condition"].setValue(int(a.condition))
        if t:
            left = max(0.0, t.maint_hours - a.hours_since_inspection)
            self.bars["inspection"].setValue(int(left / t.maint_hours * 100))
            self.bars["inspection"].setFormat(f"{left:.0f} h")
            self.bars["fuel"].setValue(int(a.fuel_gal / t.fuel_cap_gal * 100))
            self.bars["fuel"].setFormat(f"{a.fuel_gal:.0f} / {t.fuel_cap_gal:.0f} gal")
            ok, why = airworthiness(a, t)
            self.status.setText(("Airworthy" if ok else why))
            self.status.setObjectName("good" if ok else "bad")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            self.b_inspect.setText(f"Inspection ({fmt.money(s, inspection_cost(t))})")
            self.b_repair.setText(f"Repair ({fmt.money(s, repair_cost(a, t))})")
            self.b_repair.setEnabled(repair_cost(a, t) > 0)
            self.b_fuel.setEnabled(a.fuel_gal < t.fuel_cap_gal - 0.5)
            self.b_sell.setText(f"Sell ({fmt.money(s, resale_value(a, t))})")

    def _do(self, what: str) -> None:
        a = self._current()
        if not a:
            return
        try:
            cost = getattr(self.career.hangar, what)(a.id)
        except HangarError as exc:
            QMessageBox.warning(self, "Cannot do that", str(exc))
            return
        self.ctx.toast.emit("info", f"{what.title()} done for {fmt.money(self.settings, cost)}")
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")

    def _rename(self) -> None:
        a = self._current()
        if not a:
            return
        text, ok = QInputDialog.getText(self, "Rename aircraft", "Nickname:", text=a.nickname)
        if ok:
            self.career.hangar.rename(a.id, text)
            self.career._fire("hangar_changed")

    def _sell(self) -> None:
        a = self._current()
        if not a:
            return
        t = get_type(a.type_id)
        if QMessageBox.question(self, "Sell aircraft", f"Sell {a.registration} for "
                                f"{fmt.money(self.settings, resale_value(a, t))}?") != QMessageBox.Yes:
            return
        try:
            self.career.hangar.sell(a.id)
        except HangarError as exc:
            QMessageBox.warning(self, "Cannot sell", str(exc))
            return
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")

    def _dealer(self) -> None:
        DealerDialog(self.ctx, self).exec()

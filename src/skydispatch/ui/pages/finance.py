from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import fmt
from ..widgets import StatTile, heading
from .base import Page


class FinancePage(Page):
    title = "Finances"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("Finances"))
        tiles = QHBoxLayout()
        self.t_bal, self.t_in, self.t_out, self.t_net = (StatTile("Balance"), StatTile("Job income"),
                                                          StatTile("Expenses"), StatTile("Net profit"))
        for t in (self.t_bal, self.t_in, self.t_out, self.t_net):
            tiles.addWidget(t)
        root.addLayout(tiles)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Category", "Description", "Amount", "Balance"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        s = self.settings
        rows = self.db.transactions(500)
        income = sum(r["amount"] for r in rows if r["category"] == "job")
        spend = sum(-r["amount"] for r in rows if r["amount"] < 0)
        pilot = self.db.pilot()
        self.t_bal.set_value(fmt.money(s, pilot.balance) if pilot else "-")
        self.t_in.set_value(fmt.money(s, income), "good")
        self.t_out.set_value(fmt.money(s, spend), "warn")
        net = sum(r["amount"] for r in rows if r["category"] != "career")
        self.t_net.set_value(fmt.money(s, net, signed=True), "good" if net >= 0 else "bad")
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [fmt.when(row["ts"]), row["category"], row["description"],
                    fmt.money(s, row["amount"], signed=True), fmt.money(s, row["balance_after"])]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))

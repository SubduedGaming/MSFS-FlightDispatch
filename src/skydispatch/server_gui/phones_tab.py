from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..ui.widgets import heading, muted
from .page import Page
from .qr import qr_png


def when(ts: float) -> str:
    if not ts:
        return "never"
    secs = time.time() - ts
    if secs < 90:
        return "just now"
    return datetime.fromtimestamp(ts).strftime("%d %b %H:%M")


class PhonesTab(Page):
    title = "Phones"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("Pair a phone"))
        root.addWidget(muted("Open the SkyDispatch app, choose 'Add server' and scan this code, or type the address and "
                             "pairing code. The code works once and expires in five minutes."))

        row = QHBoxLayout()
        self.qr = QLabel("")
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setFixedSize(260, 260)
        self.qr.setStyleSheet("background:white;border-radius:8px;")
        row.addWidget(self.qr)
        side = QVBoxLayout()
        self.code = QLabel("")
        self.code.setStyleSheet("font-size:34px;font-weight:800;letter-spacing:4px;")
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.expires = muted("", wrap=False)
        self.addresses = QLabel("")
        self.addresses.setWordWrap(True)
        self.addresses.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        self.problem.setObjectName("muted")
        new = QPushButton("New code")
        new.clicked.connect(lambda: self.refresh(new_code=True))
        copy = QPushButton("Copy pairing link")
        copy.clicked.connect(self._copy_link)
        side.addWidget(QLabel("Pairing code"))
        side.addWidget(self.code)
        side.addWidget(self.expires)
        side.addWidget(QLabel("Server address"))
        side.addWidget(self.addresses)
        side.addWidget(self.problem)
        brow = QHBoxLayout()
        brow.addWidget(new)
        brow.addWidget(copy)
        brow.addStretch(1)
        side.addLayout(brow)
        side.addStretch(1)
        row.addLayout(side, 1)
        root.addLayout(row)

        g = QGroupBox("Paired phones")
        gl = QVBoxLayout(g)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Paired", "Last seen"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        gl.addWidget(self.table)
        b2 = QHBoxLayout()
        self.remove = QPushButton("Remove selected")
        self.remove.clicked.connect(self._remove_selected)
        self.remove_all = QPushButton("Remove all")
        self.remove_all.clicked.connect(self._remove_all)
        b2.addWidget(self.remove)
        b2.addWidget(self.remove_all)
        b2.addStretch(1)
        gl.addLayout(b2)
        gl.addWidget(muted("A removed phone is signed out at once and must be paired again."))
        root.addWidget(g, 1)

        self._info: dict = {}
        self._devices: list = []
        ctx.settings_changed.connect(lambda: self.refresh())
        self._clock = QTimer(self, interval=1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self.refresh()

    def showEvent(self, e) -> None:                               # opening the tab: show the phones paired meanwhile
        super().showEvent(e)
        if not self.ctx.closed:
            self.refresh()

    # ------------------------------------------------------------------ data
    def refresh(self, new_code: bool = False) -> None:
        info = self.ctx.pairing_info(new_code=new_code)
        self._info = info
        self.code.setText(info["code"])
        self._update_expiry()
        self.addresses.setText("\n".join(info["urls"]) or "No network address found")
        if not info["running"]:
            self.problem.setText("Phone access is off. Turn it on in Settings > Simulator > Phone access, or on the Status tab.")
        elif not info["urls"]:
            self.problem.setText("This PC has no network address. Connect it to your home network.")
        else:
            self.problem.setText("")
        png = qr_png(info["uri"]) if info["running"] else None
        if png:
            pix = QPixmap()
            pix.loadFromData(png, "PNG")
            self.qr.setPixmap(pix.scaled(244, 244, Qt.KeepAspectRatio, Qt.FastTransformation))
        else:
            self.qr.setPixmap(QPixmap())
            self.qr.setText("No QR code" if info["running"] else "Phone access is off")
            self.qr.setStyleSheet("background:white;color:#444;border-radius:8px;")
        self._fill_devices()

    def _fill_devices(self) -> None:
        self._devices = self.ctx.pairing.devices()
        selected = self.table.currentRow()
        self.table.setRowCount(len(self._devices))
        for r, d in enumerate(self._devices):
            for c, text in enumerate((d.name, when(d.created), when(d.last_seen))):
                self.table.setItem(r, c, QTableWidgetItem(text))
        if 0 <= selected < len(self._devices):
            self.table.selectRow(selected)
        self.remove.setEnabled(bool(self._devices))
        self.remove_all.setEnabled(bool(self._devices))

    def _update_expiry(self) -> None:
        left = self._info.get("expires_in", 0) if self._info else 0
        self.expires.setText(f"Expires in {left // 60}:{left % 60:02d}" if left > 0 else "")

    def _tick(self) -> None:
        if self.ctx.closed or not self.isVisible():
            return
        code, left = self.ctx.pairing.code_state()
        if not code:
            self.refresh(new_code=True)                          # expired or used: show a fresh one
            return
        self._info["expires_in"] = int(left)
        self._update_expiry()
        if len(self._devices) != len(self.ctx.pairing.devices()):    # a phone paired
            self.refresh(new_code=True)
        elif int(time.time()) % 10 == 0:
            self._fill_devices()                                 # last-seen times

    # --------------------------------------------------------------- actions
    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self._info.get("uri", ""))
        self.ctx.toast.emit("info", "Pairing link copied.")

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._devices):
            self.ctx.toast.emit("info", "Select a phone first.")
            return
        d = self._devices[row]
        if QMessageBox.question(self, "Remove phone", f"Sign out and remove '{d.name}'?") == QMessageBox.Yes:
            self.ctx.pairing.revoke(d.id)
            self._fill_devices()

    def _remove_all(self) -> None:
        if QMessageBox.question(self, "Remove all phones", "Sign out and remove every paired phone?") == QMessageBox.Yes:
            self.ctx.pairing.revoke_all()
            self._fill_devices()

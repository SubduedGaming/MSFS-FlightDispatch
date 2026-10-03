"""Reusable widgets: cards, stat tiles, toasts, the route map and the flight profile chart."""
from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QFrame, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..core import geo
from . import theme


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)


class StatTile(QFrame):
    def __init__(self, label: str, value: str = "-", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        self._label = QLabel(label.upper())
        self._label.setObjectName("statLabel")
        self._value = QLabel(value)
        self._value.setObjectName("statValue")
        lay.addWidget(self._label)
        lay.addWidget(self._value)

    def set_value(self, text: str, tone: str | None = None) -> None:
        self._value.setText(text)
        self._value.setObjectName({"good": "good", "warn": "warn", "bad": "bad"}.get(tone or "", "statValue"))
        self._value.setStyleSheet("font-size: 22px; font-weight: 700;")
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)


def heading(text: str, level: int = 1) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("h1" if level == 1 else "h2")
    return lbl


def muted(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(wrap)
    return lbl


class Toast(QLabel):
    """Small non-blocking notification in the bottom-right of its parent window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMaximumWidth(380)
        self.hide()
        self._timer = QTimer(self, singleShot=True, interval=5000)
        self._timer.timeout.connect(self.hide)

    def show_message(self, level: str, text: str) -> None:
        p = theme.palette()
        color = {"good": p.good, "warn": p.warn, "bad": p.bad}.get(level, p.accent)
        self.setStyleSheet(f"background:{p.surface2};color:{p.text};border:1px solid {color};"
                           f"border-left:4px solid {color};border-radius:8px;padding:10px 14px;")
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.raise_()
        self.show()
        self._timer.start()

    def reposition(self) -> None:
        par = self.parentWidget()
        if par:
            self.move(par.width() - self.width() - 20, par.height() - self.height() - 44)


class RouteMap(QWidget):
    """Simple equirectangular route view: great-circle route, airports, flown track, aircraft."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.origin: tuple[str, float, float] | None = None
        self.dest: tuple[str, float, float] | None = None
        self.aircraft: tuple[float, float, float] | None = None   # lat, lon, heading
        self.track: list[tuple[float, float]] = []

    def set_route(self, origin: tuple[str, float, float] | None, dest: tuple[str, float, float] | None) -> None:
        self.origin, self.dest = origin, dest
        self.track = []
        self.update()

    def set_aircraft(self, lat: float, lon: float, heading: float) -> None:
        self.aircraft = (lat, lon, heading)
        if not self.track or geo.distance_nm(*self.track[-1], lat, lon) > 0.5:
            self.track.append((lat, lon))
            self.track = self.track[-2000:]
        self.update()

    def set_track(self, pts: Sequence[tuple[float, float]]) -> None:
        self.track = list(pts)
        self.aircraft = None
        self.update()

    def _points(self) -> list[tuple[float, float]]:
        pts = [(p[1], p[2]) for p in (self.origin, self.dest) if p]
        if self.aircraft:
            pts.append(self.aircraft[:2])
        pts += self.track
        return pts

    def paintEvent(self, _):
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        qp.fillRect(r, QColor(p.surface))
        qp.setPen(QPen(QColor(p.border), 1))
        for i in range(1, 8):
            x = r.width() * i / 8
            qp.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        for i in range(1, 5):
            y = r.height() * i / 5
            qp.drawLine(QPointF(0, y), QPointF(r.width(), y))
        pts = self._points()
        if not pts:
            qp.setPen(QColor(p.muted))
            qp.drawText(r, Qt.AlignCenter, "No active route")
            return
        lats = [a for a, _ in pts]
        lons = [b for _, b in pts]
        lat_c, lon_c = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        kx = math.cos(math.radians(lat_c))
        span_x = max(0.2, (max(lons) - min(lons)) * kx)
        span_y = max(0.2, max(lats) - min(lats))
        pad = 50
        scale = min((r.width() - 2 * pad) / span_x, (r.height() - 2 * pad) / span_y)

        def proj(lat: float, lon: float) -> QPointF:
            return QPointF(r.width() / 2 + (lon - lon_c) * kx * scale, r.height() / 2 - (lat - lat_c) * scale)

        if self.origin and self.dest:
            path = QPainterPath()
            for i in range(41):
                la, lo = geo.interpolate(self.origin[1], self.origin[2], self.dest[1], self.dest[2], i / 40)
                pt = proj(la, lo)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            pen = QPen(QColor(p.muted), 1.5, Qt.DashLine)
            qp.setPen(pen)
            qp.setBrush(Qt.NoBrush)
            qp.drawPath(path)
        if len(self.track) > 1:
            qp.setPen(QPen(QColor(p.accent), 2.5))
            qp.drawPolyline(QPolygonF([proj(a, b) for a, b in self.track]))
        for pt in (self.origin, self.dest):
            if pt:
                c = proj(pt[1], pt[2])
                qp.setPen(Qt.NoPen)
                qp.setBrush(QColor(p.good if pt is self.dest else p.warn))
                qp.drawEllipse(c, 6, 6)
                qp.setPen(QColor(p.text))
                f = QFont(qp.font())
                f.setBold(True)
                qp.setFont(f)
                qp.drawText(QPointF(c.x() + 10, c.y() - 8), pt[0])
        if self.aircraft:
            lat, lon, hdg = self.aircraft
            c = proj(lat, lon)
            qp.save()
            qp.translate(c)
            qp.rotate(hdg)
            tri = QPolygonF([QPointF(0, -11), QPointF(7, 9), QPointF(0, 5), QPointF(-7, 9)])
            qp.setPen(QPen(QColor(p.bg), 1))
            qp.setBrush(QColor(p.accent))
            qp.drawPolygon(tri)
            qp.restore()


class ProfileChart(QWidget):
    """Altitude (area) and groundspeed (line) against time for the logbook."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.data: list[tuple[float, float, float]] = []   # t, alt, gs

    def set_data(self, rows) -> None:
        self.data = [(r["t"], r["alt_ft"] or 0.0, r["gs"] or 0.0) for r in rows]
        self.update()

    def paintEvent(self, _):
        p = theme.palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(46, 12, -46, -26)
        qp.fillRect(self.rect(), QColor(p.surface))
        if len(self.data) < 2:
            qp.setPen(QColor(p.muted))
            qp.drawText(self.rect(), Qt.AlignCenter, "No telemetry recorded")
            return
        t_max = max(d[0] for d in self.data) or 1
        a_max = max(1000.0, max(d[1] for d in self.data) * 1.1)
        g_max = max(50.0, max(d[2] for d in self.data) * 1.1)

        def pt(t, v, vmax):
            return QPointF(r.left() + t / t_max * r.width(), r.bottom() - v / vmax * r.height())

        qp.setPen(QPen(QColor(p.border), 1))
        for i in range(5):
            y = r.top() + r.height() * i / 4
            qp.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            qp.setPen(QColor(p.muted))
            qp.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignRight | Qt.AlignVCenter, f"{a_max * (1 - i / 4):,.0f}")
            qp.setPen(QColor(p.warn))
            qp.drawText(QRectF(r.right() + 4, y - 8, 42, 16), Qt.AlignLeft | Qt.AlignVCenter, f"{g_max * (1 - i / 4):.0f}")
            qp.setPen(QPen(QColor(p.border), 1))
        area = QPainterPath(QPointF(r.left(), r.bottom()))
        for t, a, _g in self.data:
            area.lineTo(pt(t, a, a_max))
        area.lineTo(QPointF(pt(self.data[-1][0], 0, a_max)))
        fill = QColor(p.accent)
        fill.setAlpha(60)
        qp.setPen(Qt.NoPen)
        qp.setBrush(fill)
        qp.drawPath(area)
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(p.accent), 2))
        qp.drawPolyline(QPolygonF([pt(t, a, a_max) for t, a, _ in self.data]))
        qp.setPen(QPen(QColor(p.warn), 1.5))
        qp.drawPolyline(QPolygonF([pt(t, g, g_max) for t, _, g in self.data]))
        qp.setPen(QColor(p.muted))
        qp.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 18), Qt.AlignCenter,
                    f"Altitude (ft, blue) and groundspeed (kt, amber) over {geo.fmt_duration(t_max / 60)}")


class StatusDot(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.set_state("off", "")

    def set_state(self, state: str, text: str) -> None:
        p = theme.palette()
        color = {"ok": p.good, "busy": p.warn, "bad": p.bad}.get(state, p.muted)
        self.setText(f'<span style="color:{color}">&#9679;</span> {text}')

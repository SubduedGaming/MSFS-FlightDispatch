"""Render the SkyDispatch icon (PNG sizes + Windows .ico + macOS .iconset). Run: python packaging/make_icons.py"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPolygonF

ROOT = Path(__file__).resolve().parent.parent
SIZES = [16, 32, 48, 64, 128, 256, 512, 1024]


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.22, s * 0.22)
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0, QColor("#4db3ff"))
    grad.setColorAt(1, QColor("#0b4fa8"))
    p.fillPath(bg, grad)
    # stylised aircraft, heading up-right
    p.translate(s / 2, s / 2)
    p.rotate(45)
    u = s / 100.0
    plane = QPolygonF([QPointF(0, -38 * u), QPointF(5 * u, -22 * u), QPointF(5 * u, -8 * u), QPointF(34 * u, 8 * u),
                       QPointF(34 * u, 15 * u), QPointF(5 * u, 7 * u), QPointF(4 * u, 26 * u), QPointF(14 * u, 33 * u),
                       QPointF(14 * u, 38 * u), QPointF(0, 34 * u), QPointF(-14 * u, 38 * u), QPointF(-14 * u, 33 * u),
                       QPointF(-4 * u, 26 * u), QPointF(-5 * u, 7 * u), QPointF(-34 * u, 15 * u),
                       QPointF(-34 * u, 8 * u), QPointF(-5 * u, -8 * u), QPointF(-5 * u, -22 * u)])
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    p.drawPolygon(plane)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path, sizes=(16, 32, 48, 64, 128, 256)) -> None:
    images = [png_bytes(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    path.write_bytes(header + entries + blobs)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    res = ROOT / "src" / "skydispatch" / "resources"
    res.mkdir(parents=True, exist_ok=True)
    render(512).save(str(res / "icon.png"))
    (ROOT / "packaging" / "windows").mkdir(parents=True, exist_ok=True)
    write_ico(ROOT / "packaging" / "windows" / "skydispatch.ico")
    iconset = ROOT / "packaging" / "macos" / "SkyDispatch.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        render(base).save(str(iconset / f"icon_{base}x{base}.png"))
        render(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"))
    linux = ROOT / "packaging" / "linux"
    linux.mkdir(parents=True, exist_ok=True)
    render(256).save(str(linux / "skydispatch.png"))
    print("icons written")


if __name__ == "__main__":
    main()

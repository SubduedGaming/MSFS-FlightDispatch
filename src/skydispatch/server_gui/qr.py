"""Draw the pairing link as a QR code (needs the small pure-Python ``segno`` package)."""
from __future__ import annotations

import io


def qr_png(text: str, scale: int = 6) -> bytes | None:
    """PNG bytes of a QR code for `text`, or None when there is nothing to draw or segno is not installed."""
    if not text:
        return None
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=3, dark="#000000", light="#ffffff")
    return buf.getvalue()

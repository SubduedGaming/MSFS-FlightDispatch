from __future__ import annotations

from pathlib import Path


def tail(path: Path, lines: int = 300, max_bytes: int = 200_000) -> str:
    """The last `lines` lines of a text file (empty when it does not exist). Reads only the end of big files."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])

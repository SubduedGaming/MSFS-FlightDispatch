"""Rotating file logging + crash capture."""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from .paths import log_dir

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir() / "skydispatch.log", maxBytes=2_000_000,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    log = logging.getLogger("skydispatch.crash")

    def _hook(exc_type, exc, tb):
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        log.critical("Unhandled thread exception in %s", args.thread,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _hook
    threading.excepthook = _thread_hook

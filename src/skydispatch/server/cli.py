"""``skydispatch-server``: run the server with no window (Linux, testing, or a PC that is only a server).

It starts the simulator link and the phone API, prints the address and a pairing code, and keeps running until Ctrl+C.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from typing import Callable

from .. import __version__
from ..core.logging_setup import setup_logging
from .engine import Engine

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skydispatch-server", description="SkyDispatch server (no window).")
    p.add_argument("--port", type=int, help="port for the phone API (default: the saved setting, 8766)")
    p.add_argument("--sim", choices=("simulated", "simconnect", "bridge"), help="simulator source for this run")
    p.add_argument("--no-sim", action="store_true", help="do not start the simulator link")
    p.add_argument("--revoke-all", action="store_true", help="forget every paired phone, then continue")
    p.add_argument("--version", action="version", version=f"skydispatch-server {__version__}")
    return p


def describe_pairing(info: dict) -> list[str]:
    if not info["running"]:
        return ["The phone API is not running (see the log for why)."]
    lines = [f"Phone API on port {info['port']}"]
    lines += [f"  address: {u}" for u in info["urls"]] or ["  address: (no network address found)"]
    lines += [f"Pairing code: {info['code']}   (valid {max(1, round(info['expires_in'] / 60))} min, one use)"]
    if info["uri"]:
        lines.append(f"Pairing link: {info['uri']}")
    return lines


def run(argv: list[str] | None = None, stop: threading.Event | None = None,
        out: Callable[[str], None] = print, make_engine: Callable[[], Engine] = Engine.create) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    engine = make_engine()
    try:
        s = engine.settings
        s.remote.enabled = True
        if args.port:
            s.remote.port = args.port
        if args.sim:
            s.sim.mode = args.sim
        if args.revoke_all:
            engine.pairing.revoke_all()
        engine.main.run(engine.start_web)
        if not args.no_sim:
            engine.main.run(engine.start_sim)
        out(f"SkyDispatch server {__version__}")
        for line in describe_pairing(engine.pairing_info(new_code=True)):
            out(line)
        out("Press Ctrl+C to stop.")
        (stop or threading.Event()).wait()
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())

"""SkyDispatch Bridge: run on the PC that runs MSFS.

    skydispatch-bridge --port 8765 --token SECRET

Streams live SimConnect telemetry to any SkyDispatch app that connects.
Use ``--simulate`` to test the link without MSFS.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import socket
import socketserver
import threading
import time

from .base import SimProvider, SimState
from .bridge import PROTOCOL, encode_line

log = logging.getLogger("skydispatch.bridge")


class _Hub:
    """Fans out the latest state to every connected client handler."""

    def __init__(self, provider: SimProvider, token: str):
        self.provider, self.token = provider, token
        provider.on_state = self._on_state
        provider.on_status = self._on_status
        self._lock = threading.Lock()
        self._clients: set["_Handler"] = set()

    def add(self, h: "_Handler") -> None:
        with self._lock:
            self._clients.add(h)

    def remove(self, h: "_Handler") -> None:
        with self._lock:
            self._clients.discard(h)

    def _broadcast(self, obj: dict) -> None:
        data = encode_line(obj)
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.send(data)

    def _on_state(self, state: SimState) -> None:
        self._broadcast({"state": state.to_dict()})

    def _on_status(self, status: str, message: str) -> None:
        self._broadcast({"status": status, "message": message})


class _Handler(socketserver.StreamRequestHandler):
    hub: _Hub

    def send(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            pass

    def handle(self) -> None:
        self.request.settimeout(10)
        try:
            hello = json.loads(self.rfile.readline() or b"{}")
        except ValueError:
            return
        token_ok = (not self.hub.token) or hmac.compare_digest(str(hello.get("token", "")), self.hub.token)
        if hello.get("hello") != PROTOCOL or not token_ok:
            self.send(encode_line({"ok": False, "error": "Invalid token"}))
            log.warning("Rejected client %s", self.client_address)
            return
        self.send(encode_line({"ok": True}))
        log.info("Client connected: %s", self.client_address)
        self.request.settimeout(None)
        self.hub.add(self)
        try:
            self.hub._on_status(self.hub.provider.status, "")
            while self.rfile.read(1):  # block until the client disconnects
                pass
        except OSError:
            pass
        finally:
            self.hub.remove(self)
            log.info("Client disconnected: %s", self.client_address)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(provider: SimProvider, host: str, port: int, token: str) -> _Server:
    hub = _Hub(provider, token)
    handler = type("Handler", (_Handler,), {"hub": hub})
    server = _Server((host, port), handler)
    provider.start()
    threading.Thread(target=server.serve_forever, daemon=True, name="bridge-server").start()
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skydispatch-bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="", help="shared secret clients must send")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--simulate", action="store_true", help="stream a demo flight instead of MSFS")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.simulate:
        from .simulated import SimulatedProvider
        provider: SimProvider = SimulatedProvider(args.hz)
    else:
        from .simconnect_provider import SimConnectProvider
        provider = SimConnectProvider(args.hz)
    if not args.token:
        log.warning("No --token set: anyone on your network can read your telemetry.")
    serve(provider, args.host, args.port, args.token)
    host_name = socket.gethostname()
    print(f"SkyDispatch Bridge listening on {args.host}:{args.port} (host: {host_name}). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        provider.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

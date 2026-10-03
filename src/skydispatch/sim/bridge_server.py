"""Share live flight data with other computers running SkyDispatch.

``BridgeHost`` is embedded in the Windows app (Settings > Simulator > Share flight data): MSFS runs on Windows, so
the Windows app is the host and a Mac/Linux SkyDispatch connects to it as a client. It speaks a tiny JSON-lines
protocol over TCP (see ``bridge.py``) with a shared-token handshake.

A headless command-line host also exists for development and testing:

    skydispatch-bridge --token SECRET [--simulate]
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import socket
import socketserver
import sys
import threading
import time
from typing import Callable

from .base import SimProvider, SimState
from .bridge import PROTOCOL, encode_line

log = logging.getLogger("skydispatch.bridge")

InstalledSource = Callable[[], "list[str] | None"] | "list[str] | None"


class _Hub:
    """Fans out telemetry and status to every connected client handler."""

    def __init__(self, token: str, installed: InstalledSource = None):
        self.token, self._installed = token, installed
        self.status, self.message = "disconnected", ""
        self._lock = threading.Lock()
        self._clients: set["_Handler"] = set()

    def installed(self) -> list[str] | None:
        return self._installed() if callable(self._installed) else self._installed

    def add(self, h: "_Handler") -> None:
        with self._lock:
            self._clients.add(h)

    def remove(self, h: "_Handler") -> None:
        with self._lock:
            self._clients.discard(h)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _broadcast(self, obj: dict) -> None:
        data = encode_line(obj)
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.send(data)

    def publish_state(self, state: SimState) -> None:
        self._broadcast({"state": state.to_dict()})

    def publish_status(self, status: str, message: str = "") -> None:
        self.status, self.message = status, message
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
        self.send(encode_line({"ok": True, "installed": self.hub.installed()}))
        log.info("Client connected: %s", self.client_address)
        self.request.settimeout(None)
        self.hub.add(self)
        try:
            self.send(encode_line({"status": self.hub.status, "message": self.hub.message}))
            while self.rfile.read(1):  # block until the client disconnects
                pass
        except OSError:
            pass
        finally:
            self.hub.remove(self)
            log.info("Client disconnected: %s", self.client_address)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # On POSIX, SO_REUSEADDR only skips TIME_WAIT. On Windows it lets a second server steal a live port,
    # so there we ask for an exclusive bind instead and a busy port raises OSError as it should.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class BridgeHost:
    """A TCP server that republishes whatever telemetry it is given. Start/stop it any time."""

    def __init__(self, host: str, port: int, token: str, installed: InstalledSource = None):
        self.host, self.port = host, port
        self.hub = _Hub(token, installed)
        self._server: _Server | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def client_count(self) -> int:
        return self.hub.client_count

    def start(self) -> None:
        """Raises OSError (for example when the port is already in use)."""
        if self._server:
            return
        handler = type("Handler", (_Handler,), {"hub": self.hub})
        self._server = _Server((self.host, self.port), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True, name="bridge-host").start()
        log.info("Sharing flight data on %s:%d", self.host, self.port)

    def stop(self) -> None:
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
            log.info("Stopped sharing flight data")

    def publish_state(self, state: SimState) -> None:
        self.hub.publish_state(state)

    def publish_status(self, status: str, message: str = "") -> None:
        self.hub.publish_status(status, message)


def serve(provider: SimProvider, host: str, port: int, token: str, installed: InstalledSource = None) -> BridgeHost:
    """Headless: host a provider's telemetry (CLI and tests)."""
    bridge = BridgeHost(host, port, token, installed)
    provider.on_state = bridge.publish_state
    provider.on_status = bridge.publish_status
    bridge.start()
    provider.start()
    return bridge


def local_addresses() -> list[str]:
    """This computer's LAN IPv4 addresses (what other computers should connect to)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:       # no packets are sent
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")] or ["127.0.0.1"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skydispatch-bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="", help="shared secret clients must send")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--simulate", action="store_true", help="stream a demo flight instead of MSFS")
    ap.add_argument("--packages-path", default="", help="MSFS packages folder (containing Community/ and Official/); "
                                                       "found automatically on most installs")
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
    from .installed import detect_installed
    found = detect_installed(args.packages_path)
    installed = sorted(found) if found is not None else None
    print("Installed aircraft detected:", ", ".join(installed) if installed else "none (job filtering disabled)")
    bridge = serve(provider, args.host, args.port, args.token, installed)
    print(f"Sharing on {', '.join(f'{ip}:{args.port}' for ip in local_addresses())}. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        provider.stop()
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

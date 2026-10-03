"""Wire protocol + client for the SkyDispatch Bridge.

The bridge runs next to MSFS on a Windows PC and streams telemetry over TCP
so the full app can run on macOS/Linux (or another PC). Protocol: UTF-8 JSON
lines. The client first sends ``{"hello": "skydispatch", "token": "..."}``; the
server answers ``{"ok": true}`` or closes the connection, then streams one
``SimState`` dict per line.
"""
from __future__ import annotations

import json
import logging
import socket

from .base import SimProvider, SimState

log = logging.getLogger(__name__)

PROTOCOL = "skydispatch"


def encode_line(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class BridgeClientProvider(SimProvider):
    name = "bridge"

    def __init__(self, host: str, port: int, token: str = "", sample_hz: float = 2.0):
        super().__init__(sample_hz)
        self.host, self.port, self.token = host, port, token

    def _run(self) -> None:
        while not self._stop.is_set():
            self._set_status("connecting", f"Connecting to bridge {self.host}:{self.port}...")
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as sock:
                    sock.settimeout(5)
                    sock.sendall(encode_line({"hello": PROTOCOL, "token": self.token}))
                    f = sock.makefile("rb")
                    reply = json.loads(f.readline() or b"{}")
                    if not reply.get("ok"):
                        self._set_status("error", reply.get("error", "Bridge rejected the connection (check token)"))
                        self._sleep(5)
                        continue
                    self.installed = reply.get("installed")
                    self._set_status("connected", f"Bridge {self.host}:{self.port}")
                    while not self._stop.is_set():
                        line = f.readline()
                        if not line:
                            raise ConnectionError("bridge closed the connection")
                        data = json.loads(line)
                        if "state" in data:
                            self._emit(SimState.from_dict(data["state"]))
                        elif "status" in data and data["status"] != "connected":
                            self._set_status("connecting", data.get("message", "MSFS not running"))
            except (OSError, ValueError, ConnectionError) as exc:
                if self._stop.is_set():
                    break
                self._set_status("connecting", f"Bridge unavailable ({exc}); retrying...")
                self._sleep(3)

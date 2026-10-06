"""Pairing phones with the server.

The admin window shows a QR code holding ``skydispatch://pair?host=..&port=..&code=..``. The phone sends that one-time
code to ``POST /api/v1/pair`` and gets back a long random **device token** that it keeps and sends as
``Authorization: Bearer <token>`` from then on. Only a hash of each token is stored, so ``devices.json`` cannot be used
to sign in. Revoking a device invalidates its token at once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..core.paths import config_dir

log = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O/1/I
CODE_LENGTH = 8
CODE_TTL_S = 300
MAX_DEVICES = 20
TOUCH_EVERY_S = 60


class PairingError(Exception):
    """A pairing request that should be refused; the message is safe to show."""


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    created: float
    last_seen: float = 0.0

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created, "last_seen": self.last_seen}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def normalise_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def format_code(code: str) -> str:
    """ABCD-2345 for people to read; the dash is ignored when it is typed back."""
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


def pair_uri(host: str, port: int, code: str) -> str:
    return "skydispatch://pair?" + urlencode({"host": host, "port": port, "code": code})


def lan_addresses() -> list[str]:
    """This computer's private IPv4 addresses, the most likely one (the default route) first."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))                 # no packet is sent; it just picks the outgoing interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254.", "0."))]


class PairingManager:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "devices.json")
        self._lock = threading.Lock()
        self._devices: dict[str, Device] = {}
        self._code = ""
        self._code_expires = 0.0
        self._dirty_seen = 0.0
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for row in data.get("devices", []):
                d = Device(str(row["id"]), str(row["name"]), str(row["token_hash"]), float(row["created"]),
                           float(row.get("last_seen", 0.0)))
                self._devices[d.id] = d
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("Could not read %s (%s); no phones are paired", self.path, exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"devices": [asdict(d) for d in self._devices.values()]}, fh, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # --------------------------------------------------------------- codes
    def new_code(self, ttl: float = CODE_TTL_S) -> str:
        """A fresh one-time code (replaces any earlier one)."""
        with self._lock:
            self._code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            self._code_expires = time.time() + ttl
            return self._code

    def code_state(self) -> tuple[str, float]:
        """(current code or '', seconds left)."""
        with self._lock:
            left = self._code_expires - time.time()
            return (self._code, left) if self._code and left > 0 else ("", 0.0)

    def cancel_code(self) -> None:
        with self._lock:
            self._code, self._code_expires = "", 0.0

    def redeem(self, code: str, device_name: str) -> tuple[Device, str]:
        """Trade the one-time code for a device token. The code is spent on success; a wrong guess does not spend it
        (the server rate-limits guesses per address)."""
        name = " ".join(str(device_name).split())[:40] or "Phone"
        with self._lock:
            live = self._code and time.time() < self._code_expires
            if not live or not hmac.compare_digest(normalise_code(code).encode(), self._code.encode()):
                raise PairingError("That pairing code is not right or has expired. Show a new one on the PC.")
            if len(self._devices) >= MAX_DEVICES:
                raise PairingError(f"This server already has {MAX_DEVICES} paired devices. Remove one first.")
            device_id = secrets.token_hex(4)
            secret = secrets.token_urlsafe(32)
            now = time.time()
            device = Device(device_id, name, _hash(secret), now, now)
            self._devices[device_id] = device
            self._code, self._code_expires = "", 0.0
            self._save()
        log.info("Paired a new device: %s (%s)", name, device_id)
        return device, f"{device_id}.{secret}"

    # -------------------------------------------------------------- tokens
    def authenticate(self, token: str) -> Device | None:
        device_id, _, secret = str(token).partition(".")
        with self._lock:
            device = self._devices.get(device_id)
            if device is None or not secret or not hmac.compare_digest(_hash(secret), device.token_hash):
                return None
            now = time.time()
            if now - device.last_seen > TOUCH_EVERY_S:
                device.last_seen = now
                try:
                    self._save()
                except OSError:
                    log.warning("Could not save device activity")
            return device

    def devices(self) -> list[Device]:
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: d.created)

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            device = self._devices.pop(device_id, None)
            if device is None:
                return False
            self._save()
        log.info("Removed paired device %s (%s)", device.name, device_id)
        return True

    def revoke_all(self) -> None:
        with self._lock:
            self._devices.clear()
            self._save()

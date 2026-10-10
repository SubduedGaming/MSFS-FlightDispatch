"""Optional LAN discovery: announce the server as ``_skydispatch._tcp`` so the phone can list it.

Needs the optional ``zeroconf`` package (``pip install skydispatch[discovery]``). Without it nothing is announced and
pairing by QR code or typed address works as before.
"""
from __future__ import annotations

import logging
import socket

from .. import __version__
from .pairing import lan_addresses

log = logging.getLogger(__name__)

SERVICE_TYPE = "_skydispatch._tcp.local."


class Advertiser:
    def __init__(self) -> None:
        self._zc = None
        self._info = None

    @property
    def running(self) -> bool:
        return self._zc is not None

    def start(self, port: int, name: str = "") -> bool:
        """Announce the server on `port`. Returns False when discovery is unavailable (never raises)."""
        self.stop()
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            log.info("zeroconf is not installed; the server is not announced on the network")
            return False
        try:
            host = (name or socket.gethostname() or "SkyDispatch").split(".")[0][:40]
            addresses = [socket.inet_aton(ip) for ip in lan_addresses()]
            if not addresses:
                return False
            self._info = ServiceInfo(SERVICE_TYPE, f"{host} SkyDispatch.{SERVICE_TYPE}", addresses=addresses, port=port,
                                     properties={"version": __version__, "api": "1"}, server=f"{host}.local.")
            self._zc = Zeroconf()
            self._zc.register_service(self._info)
            log.info("Announcing SkyDispatch on the network as %r", host)
            return True
        except Exception:
            log.exception("could not announce the server on the network")
            self.stop()
            return False

    def stop(self) -> None:
        zc, info, self._zc, self._info = self._zc, self._info, None, None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception:
            log.debug("zeroconf shutdown failed", exc_info=True)

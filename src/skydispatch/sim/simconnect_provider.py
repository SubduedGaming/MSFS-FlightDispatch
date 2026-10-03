"""Live Microsoft Flight Simulator 2020/2024 link via SimConnect (Windows only).

Uses the `SimConnect` PyPI package (Python-SimConnect). If the package or MSFS
is missing, the provider reports a clear status instead of crashing, and keeps
retrying so you can start MSFS after SkyDispatch.
"""
from __future__ import annotations

import logging
import math
import sys
import time

from .base import SimProvider, SimState

log = logging.getLogger(__name__)

# Each tuple: (SimVar name, attribute) read every cycle.
_VARS = {
    "lat": "PLANE_LATITUDE",
    "lon": "PLANE_LONGITUDE",
    "alt_msl": "PLANE_ALTITUDE",
    "alt_agl": "PLANE_ALT_ABOVE_GROUND",
    "ias": "AIRSPEED_INDICATED",
    "gs": "GROUND_VELOCITY",
    "vs": "VERTICAL_SPEED",
    "heading": "PLANE_HEADING_DEGREES_MAGNETIC",
    "pitch": "PLANE_PITCH_DEGREES",
    "bank": "PLANE_BANK_DEGREES",
    "g_force": "G_FORCE",
    "on_ground": "SIM_ON_GROUND",
    "fuel_gal": "FUEL_TOTAL_QUANTITY",
    "parking_brake": "BRAKE_PARKING_POSITION",
    "gear": "GEAR_HANDLE_POSITION",
    "flaps": "FLAPS_HANDLE_PERCENT",
    "engine": "GENERAL_ENG_COMBUSTION:1",
    "title": "TITLE",
}


def simconnect_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import SimConnect  # noqa: F401
        return True
    except Exception:
        return False


class SimConnectProvider(SimProvider):
    name = "simconnect"

    def _run(self) -> None:
        if sys.platform != "win32":
            self._set_status("error", "SimConnect only works on Windows. Use the network bridge instead.")
            return
        try:
            from SimConnect import AircraftRequests, SimConnect
        except Exception as exc:
            self._set_status("error", f"SimConnect Python package not installed ({exc}). "
                                      "Install with: pip install SimConnect")
            return

        while not self._stop.is_set():
            self._set_status("connecting", "Waiting for Microsoft Flight Simulator...")
            try:
                sm = SimConnect()
                aq = AircraftRequests(sm, _time=250)
            except Exception:
                self._sleep(4.0)
                continue
            self._set_status("connected", "Connected to MSFS")
            failures = 0
            period = 1.0 / self.sample_hz
            while not self._stop.is_set():
                started = time.time()
                try:
                    state = self._read(aq)
                    failures = 0
                    if state is not None:
                        self._emit(state)
                except Exception as exc:
                    failures += 1
                    log.debug("SimConnect read failed: %s", exc)
                    if failures > 10:
                        break
                self._sleep(max(0.05, period - (time.time() - started)))
            try:
                sm.exit()
            except Exception:
                pass
            if not self._stop.is_set():
                self._set_status("connecting", "Lost connection to MSFS; retrying...")
                self._sleep(3.0)

    @staticmethod
    def _num(aq, name: str, default: float = 0.0) -> float:
        value = aq.get(name)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _read(self, aq) -> SimState | None:
        n = lambda key, d=0.0: self._num(aq, _VARS[key], d)  # noqa: E731
        lat = aq.get(_VARS["lat"])
        if lat is None:
            return None
        title = aq.get(_VARS["title"])
        if isinstance(title, bytes):
            title = title.decode("utf-8", "ignore")
        heading = n("heading")
        if abs(heading) <= 2 * math.pi + 0.01:    # MSFS reports radians for angle SimVars in some builds
            heading = math.degrees(heading)
        touch = None
        try:
            tv = aq.get("PLANE_TOUCHDOWN_NORMAL_VELOCITY")
            if tv:
                touch = -abs(float(tv)) * 60.0   # ft/s -> fpm (down = negative)
        except Exception:
            pass
        return SimState(
            timestamp=time.time(), lat=float(lat), lon=n("lon"), alt_msl=n("alt_msl"), alt_agl=n("alt_agl"),
            ias=n("ias"), gs=n("gs"), vs=n("vs"), heading=heading % 360,
            pitch=n("pitch"), bank=n("bank"), g_force=n("g_force", 1.0),
            on_ground=bool(n("on_ground", 1)), fuel_gal=n("fuel_gal"),
            parking_brake=bool(n("parking_brake")), gear_down=n("gear", 1) > 0.5, flaps=n("flaps"),
            engine_running=bool(n("engine")), title=str(title or ""), touchdown_fpm=touch,
        )

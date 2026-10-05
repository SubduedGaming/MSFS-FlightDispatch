"""Live Microsoft Flight Simulator 2020/2024 link via SimConnect (Windows only).

Uses the `SimConnect` PyPI package (Python-SimConnect). If the package or MSFS is
missing, the provider reports a clear status instead of crashing, and keeps
retrying so you can start MSFS after SkyDispatch.

Units (verified against the library's variable table): angles come back in
radians, altitude in feet, speeds in knots, vertical speed in feet/minute,
fuel in gallons.
"""
from __future__ import annotations

import logging
import math
import queue
import sys
import time
from concurrent.futures import Future

from .base import SimProvider, SimState

log = logging.getLogger(__name__)

# SimState attribute -> SimVar. "slow" values change rarely and are refreshed about once a second.
_FAST = {
    "lat": "PLANE_LATITUDE", "lon": "PLANE_LONGITUDE", "alt_msl": "PLANE_ALTITUDE",
    "alt_agl": "PLANE_ALT_ABOVE_GROUND", "ias": "AIRSPEED_INDICATED", "gs": "GROUND_VELOCITY",
    "vs": "VERTICAL_SPEED", "g_force": "G_FORCE", "on_ground": "SIM_ON_GROUND",
}
_SLOW = {
    "heading": "PLANE_HEADING_DEGREES_MAGNETIC", "pitch": "PLANE_PITCH_DEGREES", "bank": "PLANE_BANK_DEGREES",
    "fuel_gal": "FUEL_TOTAL_QUANTITY", "parking_brake": "BRAKE_PARKING_POSITION", "gear": "GEAR_HANDLE_POSITION",
    "flaps": "FLAPS_HANDLE_PERCENT", "engine": "GENERAL_ENG_COMBUSTION:1", "paused": "SIM_DISABLED",
    "total_w": "TOTAL_WEIGHT", "empty_w": "EMPTY_WEIGHT", "fuel_w": "FUEL_TOTAL_QUANTITY_WEIGHT",
}
FAST_SAMPLE_AGL_FT = 250.0          # below this (and airborne) sample at 10 Hz so touchdown rate is accurate
FAST_PERIOD_S = 0.1
SLOW_REFRESH_S = 1.0


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

    def __init__(self, sample_hz: float = 2.0):
        super().__init__(sample_hz)
        self._slow: dict[str, float] = {}
        self._slow_at = 0.0
        self._title = ""
        self._touch_prev: float | None = None
        self._touch_until = 0.0
        self._touch_fpm: float | None = None
        self._commands: "queue.Queue[tuple]" = queue.Queue()     # writes run on this thread, never concurrently

    def _enqueue(self, fn) -> Future:
        fut: Future = Future()
        self._commands.put((fn, fut))
        return fut

    def apply_loadout(self, plan, atype) -> Future:
        """Queue a fuel/payload write. Raises LoadoutError right away if the aircraft is not safe to load."""
        from ..planning.loadout import LoadoutError, apply_loadout, ready_problem
        if self.status != "connected":
            raise LoadoutError("Microsoft Flight Simulator is not connected.")
        problem = ready_problem(self._latest, atype, plan)
        if problem:
            raise LoadoutError(problem)
        return self._enqueue(lambda aq: apply_loadout(aq, plan, atype))

    def diagnose_loadout(self) -> Future:
        """Read (never write) everything the loadout code relies on, for troubleshooting."""
        from ..planning.loadout import LoadoutError, diagnose
        if self.status != "connected":
            raise LoadoutError("Microsoft Flight Simulator is not connected.")
        title = self._latest.title if self._latest else ""
        return self._enqueue(lambda aq: diagnose(aq, title))

    def _drain_commands(self, aq, error: Exception | None = None) -> None:
        while True:
            try:
                fn, fut = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if error is not None:
                    raise error
                fut.set_result(fn(aq))
            except BaseException as exc:
                log.warning("SimConnect command failed: %s", exc, exc_info=not isinstance(exc, (ConnectionError,)))
                fut.set_exception(exc)

    def _run(self) -> None:
        if sys.platform != "win32":
            self._set_status("error", "SimConnect only works on Windows. Use the network bridge instead.")
            return
        try:
            from SimConnect import AircraftRequests, Request, SimConnect
        except Exception as exc:
            self._set_status("error", f"SimConnect Python package not installed ({exc}). "
                                      "Install with: pip install SimConnect")
            return

        while not self._stop.is_set():
            self._set_status("connecting", "Waiting for Microsoft Flight Simulator...")
            try:
                sm = SimConnect()
                aq = AircraftRequests(sm, _time=60)
                touch = Request((b"PLANE TOUCHDOWN NORMAL VELOCITY", b"Feet per second"), sm, _time=60,
                                _dec="touchdown velocity")
            except Exception:
                self._sleep(4.0)
                continue
            self._set_status("connected", "Connected to MSFS")
            self._slow_at = 0.0
            failures = 0
            while not self._stop.is_set():
                started = time.time()
                self._drain_commands(aq)
                last = self._latest
                fast = bool(last and not last.on_ground and last.alt_agl < FAST_SAMPLE_AGL_FT)
                try:
                    state = self._read(aq, touch, refresh_slow=not fast or started - self._slow_at > SLOW_REFRESH_S)
                    failures = 0
                    if state is not None:
                        self._emit(state)
                except Exception as exc:
                    failures += 1
                    log.debug("SimConnect read failed: %s", exc)
                    if failures > 10:
                        break
                period = FAST_PERIOD_S if fast else 1.0 / self.sample_hz
                self._sleep(max(0.02, period - (time.time() - started)))
            try:
                sm.exit()
            except Exception:
                pass
            self._drain_commands(None, ConnectionError("Lost connection to MSFS before the aircraft could be loaded."))
            if not self._stop.is_set():
                self._set_status("connecting", "Lost connection to MSFS; retrying...")
                self._sleep(3.0)

    @staticmethod
    def _num(value, default: float = 0.0) -> float:
        try:
            return default if value is None else float(value)
        except (TypeError, ValueError):
            return default

    def _read(self, aq, touch_req, refresh_slow: bool) -> SimState | None:
        n = self._num
        lat = aq.get(_FAST["lat"])
        if lat is None:
            return None
        now = time.time()
        f = {k: n(aq.get(v), 1.0 if k == "g_force" else 0.0) for k, v in _FAST.items() if k != "lat"}
        if refresh_slow:
            self._slow = {k: n(aq.get(v), 0.0) for k, v in _SLOW.items()}
            title = aq.get("TITLE")
            self._title = title.decode("utf-8", "ignore") if isinstance(title, bytes) else str(title or self._title)
            self._slow_at = now
        s = self._slow
        # Landing rate straight from the sim when it updates (value only changes at each touchdown).
        try:
            tv = touch_req.value
            if tv is not None:
                tv = float(tv)
                if self._touch_prev is not None and tv != self._touch_prev and tv != 0.0:
                    self._touch_fpm = -abs(tv) * 60.0
                    self._touch_until = now + 2.0
                self._touch_prev = tv
        except Exception:
            pass
        touch = self._touch_fpm if now < self._touch_until else None
        return SimState(
            timestamp=now, lat=float(lat), lon=f["lon"], alt_msl=f["alt_msl"], alt_agl=f["alt_agl"], ias=f["ias"],
            gs=f["gs"], vs=f["vs"], heading=math.degrees(s.get("heading", 0.0)) % 360,
            pitch=math.degrees(s.get("pitch", 0.0)), bank=math.degrees(s.get("bank", 0.0)), g_force=f["g_force"],
            on_ground=bool(f["on_ground"]), engine_running=bool(s.get("engine", 0.0)),
            parking_brake=bool(s.get("parking_brake", 0.0)), gear_down=s.get("gear", 1.0) > 0.5,
            flaps=s.get("flaps", 0.0) * 100.0, fuel_gal=s.get("fuel_gal", 0.0),
            sim_paused=bool(s.get("paused", 0.0)), title=self._title, touchdown_fpm=touch,
            payload_lb=(max(0.0, s.get("total_w", 0.0) - s.get("empty_w", 0.0) - s.get("fuel_w", 0.0))
                        if s.get("empty_w", 0.0) > 0 and s.get("total_w", 0.0) > 0 else None))

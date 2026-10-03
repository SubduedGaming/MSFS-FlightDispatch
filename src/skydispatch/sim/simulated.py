"""Demo/test provider that flies a realistic scripted profile.

It lets you try the whole career loop (and run tests) without MSFS. Use
``fly(origin, dest, ...)`` to start a flight; the plane taxis, takes off,
climbs, cruises, descends and lands, then idles on the ground.
"""
from __future__ import annotations

import dataclasses
import random
import threading
import time

from ..core import geo
from .base import SimProvider, SimState


class SimulatedProvider(SimProvider):
    name = "simulated"

    def __init__(self, sample_hz: float = 2.0, speed: float = 8.0):
        super().__init__(sample_hz)
        self.speed = max(1.0, speed)          # sim seconds per wall second
        self._plan_lock = threading.Lock()
        self._plan: dict | None = None
        self._state = SimState(title="Cessna 172 Skyhawk (Simulated)", fuel_gal=40, on_ground=True)
        self.landing_fpm = -150.0             # tweakable for tests ("hard landing" demo)
        self.cruise_alt = 6500.0
        self.cruise_kts = 110.0
        self.fuel_gph = 9.0
        self.rng = random.Random(7)

    # ------------------------------------------------------------------
    def set_position(self, lat: float, lon: float, title: str | None = None, fuel_gal: float | None = None,
                     elev_ft: float = 0.0) -> None:
        with self._plan_lock:
            s = self._state
            s.lat, s.lon = lat, lon
            s.alt_msl, s.alt_agl, s.on_ground = elev_ft, 0.0, True
            if title:
                s.title = title
            if fuel_gal is not None:
                s.fuel_gal = fuel_gal

    def configure_aircraft(self, title: str, cruise_kts: float, cruise_alt: float, fuel_gph: float,
                           fuel_gal: float) -> None:
        with self._plan_lock:
            self._state.title = title
            self.cruise_kts, self.cruise_alt, self.fuel_gph = cruise_kts, cruise_alt, fuel_gph
            self._state.fuel_gal = fuel_gal

    def fly(self, dest_lat: float, dest_lon: float, dest_elev: float = 0.0) -> None:
        with self._plan_lock:
            s = self._state
            self._plan = {"lat": dest_lat, "lon": dest_lon, "elev": dest_elev, "phase": "start", "t": 0.0,
                          "total": geo.distance_nm(s.lat, s.lon, dest_lat, dest_lon),
                          "orig": (s.lat, s.lon)}

    @property
    def flying(self) -> bool:
        return self._plan is not None

    # ------------------------------------------------------------------
    def _run(self) -> None:
        self._set_status("connected", "Simulated flight engine")
        last = time.time()
        while not self._stop.is_set():
            now = time.time()
            dt = (now - last) * self.speed
            last = now
            with self._plan_lock:
                self._step(dt)
                s = self._state
                s.timestamp = now
                s.sim_time_scale = self.speed
                snapshot = dataclasses.replace(s)
            self._emit(snapshot)
            self._sleep(1.0 / self.sample_hz)

    # Phase machine ---------------------------------------------------
    def _step(self, dt: float) -> None:
        s, p = self._state, self._plan
        s.touchdown_fpm = None
        if p is None:
            s.gs = s.ias = s.vs = 0.0
            s.engine_running = False
            s.parking_brake = True
            s.g_force = 1.0
            return
        ph = p["phase"]
        if ph == "start":
            s.engine_running, s.parking_brake = True, False
            p["phase"], p["t"] = "taxi_out", 0.0
        elif ph == "taxi_out":
            s.gs = s.ias = 12.0
            p["t"] += dt
            self._advance(s, p, s.gs, dt)
            if p["t"] > 90:
                p["phase"], p["t"] = "takeoff", 0.0
        elif ph == "takeoff":
            p["t"] += dt
            s.gs = s.ias = min(75.0, s.gs + 2.0 * dt)
            self._advance(s, p, s.gs, dt)
            if s.ias >= 65:
                s.on_ground = False
                s.vs = 700.0
                s.g_force = 1.1
                p["phase"] = "climb"
        elif ph == "climb":
            s.ias = min(self.cruise_kts, s.ias + 1.0 * dt)
            s.gs = s.ias * 1.02
            s.vs = 700.0
            s.alt_msl += s.vs / 60.0 * dt
            s.alt_agl = s.alt_msl - p["elev"]
            s.g_force = 1.0
            self._advance(s, p, s.gs, dt)
            remaining = geo.distance_nm(s.lat, s.lon, p["lat"], p["lon"])
            # Begin descent so we arrive at the field after ~ (alt/500fpm) minutes.
            descent_nm = max(4.0, (s.alt_msl - p["elev"]) / 500.0 * (s.gs / 60.0))
            if s.alt_msl >= self.cruise_alt:
                p["phase"] = "cruise"
            elif remaining <= descent_nm:
                p["phase"] = "descent"
        elif ph == "cruise":
            s.vs = self.rng.uniform(-30, 30)
            s.ias = self.cruise_kts
            s.gs = s.ias * 1.04
            s.g_force = 1.0 + self.rng.uniform(-0.03, 0.03)
            self._advance(s, p, s.gs, dt)
            remaining = geo.distance_nm(s.lat, s.lon, p["lat"], p["lon"])
            descent_nm = max(4.0, (s.alt_msl - p["elev"]) / 500.0 * (s.gs / 60.0))
            if remaining <= descent_nm:
                p["phase"] = "descent"
        elif ph == "descent":
            s.vs = -500.0
            s.ias = max(70.0, s.ias - 0.5 * dt)
            s.gs = s.ias
            s.alt_msl += s.vs / 60.0 * dt
            s.alt_agl = max(0.0, s.alt_msl - p["elev"])
            self._advance(s, p, s.gs, dt)
            if s.alt_agl <= 15 or geo.distance_nm(s.lat, s.lon, p["lat"], p["lon"]) < 0.2:
                s.alt_msl, s.alt_agl = p["elev"], 0.0
                s.on_ground = True
                s.touchdown_fpm = self.landing_fpm
                s.vs = self.landing_fpm
                s.g_force = 1.0 + min(1.5, abs(self.landing_fpm) / 400.0)
                s.lat, s.lon = p["lat"], p["lon"]
                p["phase"], p["t"] = "rollout", 0.0
        elif ph == "rollout":
            p["t"] += dt
            s.vs = 0.0
            s.g_force = 1.0
            s.gs = s.ias = max(10.0, s.gs - 3.0 * dt)
            if s.gs <= 10:
                p["phase"], p["t"] = "taxi_in", 0.0
        elif ph == "taxi_in":
            p["t"] += dt
            s.gs = s.ias = 10.0
            if p["t"] > 60:
                s.gs = s.ias = 0.0
                s.parking_brake = True
                p["phase"], p["t"] = "shutdown", 0.0
        elif ph == "shutdown":
            p["t"] += dt
            if p["t"] > 15:
                s.engine_running = False
                self._plan = None
            return
        # fuel
        if s.engine_running:
            s.fuel_flow_gph = self.fuel_gph
            s.fuel_gal = max(0.0, s.fuel_gal - self.fuel_gph * dt / 3600.0)
        else:
            s.fuel_flow_gph = 0.0

    @staticmethod
    def _advance(s: SimState, p: dict, speed_kts: float, dt: float) -> None:
        d = speed_kts * dt / 3600.0
        if d <= 0:
            return
        brg = geo.bearing_deg(s.lat, s.lon, p["lat"], p["lon"])
        s.heading = brg
        total = max(0.001, geo.distance_nm(s.lat, s.lon, p["lat"], p["lon"]))
        f = min(1.0, d / total)
        s.lat, s.lon = geo.interpolate(s.lat, s.lon, p["lat"], p["lon"], f)

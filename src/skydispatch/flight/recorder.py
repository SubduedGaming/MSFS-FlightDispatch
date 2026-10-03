"""Watches telemetry and turns it into a flight record.

`FlightRecorder` is pure logic: feed it `SimState` samples and it fires
events (takeoff, landing, overspeed...) and, when the aircraft parks,
produces final `FlightMetrics`. It never touches the UI, so it is unit-tested
with the simulated provider.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ..core import geo
from ..data.aircraft import AircraftType
from ..db.models import Airport
from ..sim.base import SimState
from .scoring import FlightMetrics, landing_label

log = logging.getLogger(__name__)

EventCallback = Callable[[str, str, dict], None]

PHASES = ("parked", "taxi_out", "takeoff", "climb", "cruise", "descent", "landed", "taxi_in", "arrived")


@dataclass
class RecorderConfig:
    arrive_radius_nm: float = 5.0
    min_airborne_s: float = 25.0        # shorter hops are treated as bounces/hops
    bounce_window_s: float = 12.0
    telemetry_interval_s: float = 5.0
    max_dt_s: float = 10.0              # clamp for dropped samples


@dataclass
class Live:
    """Snapshot for the UI."""
    phase: str = "parked"
    elapsed_min: float = 0.0
    air_min: float = 0.0
    distance_nm: float = 0.0
    remaining_nm: float | None = None
    eta_min: float | None = None
    fuel_used_gal: float = 0.0
    max_g: float = 1.0
    overspeed_s: float = 0.0
    landing_fpm: float | None = None
    progress: float = 0.0


class FlightRecorder:
    def __init__(self, origin: Airport | None, dest: Airport | None, atype: AircraftType | None,
                 deadline_min: int = 0, on_event: EventCallback | None = None,
                 cfg: RecorderConfig | None = None, nearest: Callable[[float, float], Airport | None] | None = None):
        self.origin, self.dest, self.atype = origin, dest, atype
        self.deadline_min = deadline_min
        self.on_event = on_event or (lambda *_: None)
        self.cfg = cfg or RecorderConfig()
        self._nearest = nearest
        self.phase = "parked"
        self.started = False
        self.finished = False
        self.outcome = "in_progress"
        self.arrival: Airport | None = None
        # clocks (sim seconds)
        self.t = 0.0
        self.air_s = 0.0
        self._last_ts: float | None = None
        self._last: SimState | None = None
        self._airborne_since: float | None = None
        self._landed_at: float | None = None
        # metrics
        self.distance_nm = 0.0
        self.max_g = 1.0
        self.max_ias = 0.0
        self.max_alt = 0.0
        self.overspeed_s = 0.0
        self.landing_fpm: float | None = None
        self.landing_g = 1.0
        self.bounces = 0
        self.slews = 0
        self.landings = 0
        self.fuel_start: float | None = None
        self.fuel_end: float | None = None
        self.low_fuel = False
        self.fuel_exhausted = False
        self.sim_title = ""
        self.start_pos: tuple[float, float] | None = None
        self._flags: set[str] = set()
        self._last_vs_air = 0.0
        self._next_sample_t = 0.0
        self.samples: list[tuple] = []     # telemetry rows ready for the DB
        self.events: list[tuple[float, str, str]] = []

    # ------------------------------------------------------------------ public
    def feed(self, s: SimState) -> None:
        if self.finished:
            return
        dt = self._tick(s)
        if s.crashed:
            self._emit("crash", "The aircraft has crashed.")
            self._finish("crashed", s)
            return
        if not self.started:
            self._maybe_start(s)
            self._last = s
            return
        if s.sim_paused:
            self._last = s
            return
        self._update_metrics(s, dt)
        self._update_phase(s, dt)
        self._refine_touchdown(s)
        self._sample(s)
        self._check_arrival(s)
        self._last = s

    def abort(self) -> None:
        if self.started and not self.finished:
            self._finish("aborted", self._last)

    def live(self) -> Live:
        remaining = eta = None
        progress = 0.0
        s = self._last
        if s and self.dest:
            remaining = geo.distance_nm(s.lat, s.lon, self.dest.lat, self.dest.lon)
            if s.gs > 40:
                eta = remaining / s.gs * 60
            if self.origin:
                total = geo.distance_nm(self.origin.lat, self.origin.lon, self.dest.lat, self.dest.lon)
                progress = max(0.0, min(1.0, 1 - remaining / total)) if total > 1 else 0.0
        used = (self.fuel_start - (self.fuel_end if self.fuel_end is not None else self.fuel_start)
                if self.fuel_start is not None else 0.0)
        return Live(self.phase, self.t / 60, self.air_s / 60, self.distance_nm, remaining, eta,
                    max(0.0, used), self.max_g, self.overspeed_s, self.landing_fpm, progress)

    def metrics(self) -> FlightMetrics:
        used = 0.0
        if self.fuel_start is not None and self.fuel_end is not None:
            used = max(0.0, self.fuel_start - self.fuel_end)
        block = self.t / 60
        late = max(0.0, block - self.deadline_min) if self.deadline_min else 0.0
        return FlightMetrics(
            outcome=self.outcome if self.outcome != "in_progress" else "aborted",
            block_min=block, air_min=self.air_s / 60, distance_nm=self.distance_nm, fuel_used_gal=used,
            landing_fpm=self.landing_fpm, landing_g=self.landing_g, max_g=self.max_g,
            overspeed_s=self.overspeed_s, bounces=self.bounces, slews=self.slews, low_fuel=self.low_fuel,
            fuel_exhausted=self.fuel_exhausted, deadline_min=self.deadline_min,
            on_time=late <= 0, minutes_late=late)

    # ------------------------------------------------------------------ internals
    def _emit(self, kind: str, detail: str = "", **data) -> None:
        self.events.append((self.t, kind, detail))
        try:
            self.on_event(kind, detail, data)
        except Exception:
            log.exception("event callback failed for %s", kind)

    def _tick(self, s: SimState) -> float:
        if self._last_ts is None:
            self._last_ts = s.timestamp
            return 0.0
        wall = max(0.0, min(self.cfg.max_dt_s, s.timestamp - self._last_ts))
        self._last_ts = s.timestamp
        scale = max(0.0, s.sim_time_scale) * max(0.0, s.sim_rate if s.sim_rate else 1.0)
        dt = wall * (scale if scale else 1.0)
        if s.sim_paused:
            return 0.0
        if self.started:
            self.t += dt
        return dt

    def _maybe_start(self, s: SimState) -> None:
        moving = s.engine_running and (s.gs > 2 or not s.parking_brake)
        if not moving:
            return
        self.started = True
        self.phase = "taxi_out"
        self.sim_title = s.title
        self.start_pos = (s.lat, s.lon)
        self.fuel_start = self.fuel_end = s.fuel_gal
        self._emit("start", "Engines running - flight started", lat=s.lat, lon=s.lon)
        if self.origin:
            d = geo.distance_nm(s.lat, s.lon, self.origin.lat, self.origin.lon)
            if d > 10:
                self._emit("wrong_origin", f"You are {d:.0f} nm from the departure airport {self.origin.icao}", dist=d)
        if self.atype and s.title:
            from ..data.aircraft import match_title
            flown = match_title(s.title)
            if flown and flown.id != self.atype.id:
                self._emit("wrong_aircraft", f"Job aircraft is {self.atype.name} but sim shows {s.title}", title=s.title)

    def _update_metrics(self, s: SimState, dt: float) -> None:
        if self._last:
            d = geo.distance_nm(self._last.lat, self._last.lon, s.lat, s.lon)
            plausible = max(1.0, s.gs * dt / 3600.0 * 4 + 0.5) if dt > 0 else 1.0
            if d > plausible and d > 3.0 and not (s.on_ground and self._last.on_ground and d < 10.0):
                self.slews += 1
                self._emit("slew", f"Position jumped {d:.1f} nm - slew/teleport detected", dist=d)
            else:
                self.distance_nm += d
        self.max_g = max(self.max_g, s.g_force)
        self.max_ias = max(self.max_ias, s.ias)
        self.max_alt = max(self.max_alt, s.alt_msl)
        if self.atype and s.ias > self.atype.vne_kts and not s.on_ground:
            self.overspeed_s += dt
            if "overspeed" not in self._flags:
                self._flags.add("overspeed")
                self._emit("overspeed", f"Overspeed! {s.ias:.0f} kt exceeds {self.atype.vne_kts} kt", ias=s.ias)
        elif "overspeed" in self._flags and s.ias < (self.atype.vne_kts - 5 if self.atype else 0):
            self._flags.discard("overspeed")
        self.fuel_end = s.fuel_gal
        if self.atype and not s.on_ground:
            if s.fuel_gal <= 0.5 and not self.fuel_exhausted:
                self.fuel_exhausted = True
                self._emit("fuel_exhausted", "Fuel exhausted")
            elif s.fuel_gal < self.atype.fuel_cap_gal * 0.10 and "lowfuel_warn" not in self._flags:
                self._flags.add("lowfuel_warn")
                self.low_fuel = True
                self._emit("low_fuel", f"Low fuel: {s.fuel_gal:.0f} gal remaining", fuel=s.fuel_gal)
        if not s.on_ground:
            self._last_vs_air = s.vs
            self.air_s += dt

    def _update_phase(self, s: SimState, dt: float) -> None:
        prev = self._last
        was_ground = prev.on_ground if prev else True
        # liftoff
        if was_ground and not s.on_ground:
            if self.landings and self._landed_at is not None and \
                    self.t - self._landed_at <= self.cfg.bounce_window_s:
                self.bounces += 1
                self._emit("bounce", f"Bounced after touchdown ({self.bounces})")
                self.landing_fpm = None   # superseded by the final touchdown
            else:
                self._emit("takeoff", f"Airborne from {self.origin.icao if self.origin else 'runway'}",
                           lat=s.lat, lon=s.lon, fuel=s.fuel_gal)
            self._airborne_since = self.t
            self.phase = "climb"
        # touchdown
        elif not was_ground and s.on_ground and self._airborne_since is not None:
            airborne_for = self.t - self._airborne_since
            if airborne_for >= self.cfg.min_airborne_s:
                fpm = s.touchdown_fpm if s.touchdown_fpm is not None else min(self._last_vs_air, s.vs, 0.0)
                self.landing_fpm = fpm
                self.landing_g = s.g_force
                self.landings += 1
                self._landed_at = self.t
                self.phase = "landed"
                self._emit("landing", f"Touchdown at {abs(fpm):.0f} fpm ({landing_label(fpm)})",
                           fpm=fpm, g=s.g_force, label=landing_label(fpm))
            else:
                self.phase = "taxi_out"   # a hop shorter than min_airborne_s doesn't count as a landing
            self._airborne_since = None
        # in-air phases
        elif not s.on_ground:
            if s.vs > 300:
                self.phase = "climb"
            elif s.vs < -300:
                self.phase = "descent"
            elif s.alt_agl > 1000:
                self.phase = "cruise"
        else:
            if self.phase == "landed" and s.gs < 25:
                self.phase = "taxi_in"
            elif self.phase == "taxi_out" and s.gs > 30 and s.ias > 30:
                self.phase = "takeoff"
            elif self.phase == "takeoff" and s.gs < 25:
                self.phase = "taxi_out"

    def _refine_touchdown(self, s: SimState) -> None:
        """The sim's own touchdown rate may arrive a sample or two after the wheels touch: prefer it."""
        if (self.landings and self._landed_at is not None and s.touchdown_fpm is not None
                and self.t - self._landed_at <= 3.0 and s.on_ground):
            self.landing_fpm = s.touchdown_fpm

    def _sample(self, s: SimState) -> None:
        if self.t >= self._next_sample_t:
            self._next_sample_t = self.t + self.cfg.telemetry_interval_s
            self.samples.append((round(self.t, 1), s.lat, s.lon, s.alt_msl, s.ias, s.gs, s.heading, s.vs,
                                 s.fuel_gal, int(s.on_ground)))

    def _check_arrival(self, s: SimState) -> None:
        if not self.landings or not s.on_ground:
            return
        stopped = s.gs < 3
        parked = stopped and (s.parking_brake or not s.engine_running)
        if parked:
            near = None
            if self.dest and geo.distance_nm(s.lat, s.lon, self.dest.lat, self.dest.lon) <= self.cfg.arrive_radius_nm:
                near = self.dest
            elif self._nearest:
                cand = self._nearest(s.lat, s.lon)
                if cand and geo.distance_nm(s.lat, s.lon, cand.lat, cand.lon) <= self.cfg.arrive_radius_nm:
                    near = cand
            self.arrival = near
            if self.dest is None or near is None:
                self._emit("diverted", "Parked away from any known airport")
                self._finish("diverted" if self.dest else "completed", s)
            elif near.icao != self.dest.icao:
                self._emit("diverted", f"Parked at {near.icao} instead of {self.dest.icao}")
                self._finish("diverted", s)
            else:
                self.phase = "arrived"
                self._emit("arrived", f"Parked at {near.icao}")
                self._finish("completed", s)

    def _finish(self, outcome: str, s: SimState | None) -> None:
        self.outcome = outcome
        self.finished = True
        if s:
            self.fuel_end = s.fuel_gal
        self._emit("finished", outcome)

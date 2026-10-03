"""Proactive copilot callouts, driven by live telemetry. No AI involved, so they are instant and always available."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.aircraft import AircraftType
from ..db.models import Airport
from . import advice

URGENT = {"sink_rate", "fuel_critical"}


@dataclass
class Callout:
    key: str
    text: str


class CopilotMonitor:
    def __init__(self, min_gap_s: float = 7.0):
        self.min_gap_s = min_gap_s
        self.reset()

    def reset(self) -> None:
        self._fired: set[str] = set()
        self._last_any = -1e9
        self._last_by_key: dict[str, float] = {}

    def _once(self, key: str) -> bool:
        if key in self._fired:
            return False
        self._fired.add(key)
        return True

    def feed(self, s, live, atype: AircraftType | None, dest: Airport | None, now: float) -> list[Callout]:
        out: list[Callout] = []
        if s.on_ground:
            # new takeoff / new approach re-arm
            self._fired -= {"positive_rate", "tod_near", "tod_now", "app1000", "app500", "app_speed"}
            return out
        agl = s.alt_agl
        remaining = live.remaining_nm if live else None
        # Wide window on purpose: a stuttering feed (or an accelerated sim) can step past a narrow altitude band.
        if "positive_rate" not in self._fired and s.vs > 300 and 30 < agl < 1500:
            if self._once("positive_rate"):
                out.append(Callout("positive_rate", "Positive rate."))
        if dest and remaining is not None and agl > 2500 and live and live.phase in ("cruise", "climb", "descent"):
            target = dest.elevation_ft + (atype.pattern_agl_ft if atype else 1500)
            tod = advice.tod_distance_nm(s.alt_msl, target)
            if tod > 3:
                if remaining <= tod + 8 and remaining > tod and self._once("tod_near"):
                    out.append(Callout("tod_near", f"Top of descent in about {advice._miles(remaining - tod)}."))
                if remaining <= tod + 0.5 and s.vs > -300 and self._once("tod_now"):
                    rate = advice.descent_rate_fpm(s.alt_msl - target, max(remaining, 1), s.gs)
                    out.append(Callout("tod_now", f"Top of descent. Start down at about {rate:,.0f} feet per minute."))
        near_dest = remaining is not None and remaining < 12
        if near_dest and s.vs < -200:
            if agl <= 1050 and self._once("app1000"):
                out.append(Callout("app1000", "One thousand feet."))
            if agl <= 520 and self._once("app500"):
                out.append(Callout("app500", "Five hundred. Stable approach: check speed and sink rate."))
        if atype and near_dest and agl < 1000 and s.ias > atype.vref_kts + 25 and self._once("app_speed"):
            out.append(Callout("app_speed", f"Speed. We're {s.ias:.0f}, reference is {atype.vref_kts}."))
        sink_limit = -1200 if (atype is None or atype.category in ("piston", "twin")) else -1800
        if agl < 1000 and s.vs < sink_limit and self._due("sink_rate", now, 15.0):
            out.append(Callout("sink_rate", "Sink rate! Pull up if we're not stable."))
        if atype and s.fuel_gal > 0:
            burn = s.fuel_flow_gph or atype.fuel_gph
            rep = advice.fuel_report(s.fuel_gal, burn, remaining, s.gs)
            if remaining is not None and rep.margin_min < advice.RESERVE_MIN * 0.5 and self._once("fuel_critical"):
                out.append(Callout("fuel_critical", "Fuel is critical. " + rep.text))
            elif remaining is not None and rep.margin_min < advice.RESERVE_MIN and self._once("fuel_low"):
                out.append(Callout("fuel_low", "Fuel check. " + rep.text))
        return self._throttle(out, now)

    def _due(self, key: str, now: float, every: float) -> bool:
        if now - self._last_by_key.get(key, -1e9) < every:
            return False
        self._last_by_key[key] = now
        return True

    def _throttle(self, callouts: list[Callout], now: float) -> list[Callout]:
        """Keep callouts from talking over each other; urgent ones always get through."""
        keep: list[Callout] = []
        for c in callouts:
            if c.key in URGENT or now - self._last_any >= self.min_gap_s:
                keep.append(c)
                self._last_any = now
            else:
                self._fired.discard(c.key)        # held back, not lost: it fires on a later sample
        return keep

"""SimBrief integration: open a prefilled dispatch page for the active job, then import the generated plan.

SimBrief only allows fully automatic plan generation for approved partners (they hand out API keys), so SkyDispatch
does what any add-on without a key can: it opens SimBrief's dispatch page with the route, aircraft, passengers and
cargo already filled in, you press *Generate* there, and SkyDispatch fetches the newest plan by your SimBrief
username or Pilot ID (public data, no password involved).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from .. import __version__
from ..data.aircraft import AircraftType

log = logging.getLogger(__name__)

FETCH_URL = "https://www.simbrief.com/api/xml.fetcher.php"
DISPATCH_URL = "https://dispatch.simbrief.com/options/custom"
HOSTS = ("simbrief.com",)                    # the only host plan data and links may come from
LB_PER_KG = 2.20462
MAX_AGE_H = 36                               # an older plan is probably not for this flight

# ICAO type designators SimBrief understands, for the catalog aircraft.
ICAO_TYPE = {
    "c152": "C152", "c172": "C172", "da40": "DA40", "sr22": "SR22", "g36": "BE36", "dr40": "DR40", "baron": "BE58",
    "da62": "DA62", "c208": "C208", "pc12": "PC12", "tbm9": "TBM9", "king": "B350", "cj4": "C25C", "a320": "A320",
    "b738": "B738", "b748": "B748", "b78x": "B78X", "b77f": "B77F", "b77l": "B77L",
}


class SimBriefError(Exception):
    pass


def dispatch_url(job, atype: AircraftType | None, registration: str = "", callsign: str = "",
                 static_id: str = "", units: str = "LBS", airline: str = "", flight_number: str | int = "") -> str:
    """The SimBrief dispatch page, prefilled for this job. `static_id` lets us fetch exactly this plan afterwards.

    A company flight goes out under the company's ICAO airline code and flight number (BBA214 is airline BBA, flight
    214); a freelance flight uses the pilot's own callsign as the flight number."""
    q: dict[str, Any] = {"orig": job.origin, "dest": job.dest, "units": units}
    if airline:
        q["airline"] = airline.upper()[:3]
    if atype is not None:
        q["type"] = ICAO_TYPE.get(atype.id, atype.id.upper())
    if registration:
        q["reg"] = registration
    if flight_number:
        q["fltnum"] = str(flight_number)[:4]
    elif callsign:
        q["fltnum"] = re.sub(r"[^A-Za-z0-9]", "", callsign)[:8]
    if job.pax:
        q["pax"] = job.pax
    if job.cargo_lb:
        q["cargo"] = round(job.cargo_lb / 1000.0, 2)        # SimBrief takes cargo in thousands of lbs/kgs
    if static_id:
        q["static_id"] = static_id
    return f"{DISPATCH_URL}?{urlencode(q)}"


@dataclass
class Ofp:
    """The parts of a SimBrief operational flight plan that SkyDispatch uses. Weights are pounds, fuel is pounds
    and gallons (converted with the aircraft's fuel density)."""
    origin: str = ""
    dest: str = ""
    alternate: str = ""
    aircraft: str = ""
    registration: str = ""
    route: str = ""
    cruise_alt_ft: int = 0
    distance_nm: float = 0.0
    ete_min: float = 0.0
    pax: int = 0
    cargo_lb: float = 0.0
    payload_lb: float = 0.0
    zfw_lb: float = 0.0
    tow_lb: float = 0.0
    block_fuel_lb: float = 0.0
    trip_fuel_lb: float = 0.0
    reserve_fuel_lb: float = 0.0
    taxi_fuel_lb: float = 0.0
    generated: float = 0.0               # unix time SimBrief produced it
    pdf_url: str = ""
    static_id: str = ""
    waypoints: list[str] = field(default_factory=list)

    def block_fuel_gal(self, atype: AircraftType | None) -> float:
        return self.block_fuel_lb / (atype.fuel_lb_per_gal if atype else 6.0)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Ofp":
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in names})

    def matches(self, job) -> bool:
        return bool(job) and self.origin == job.origin.upper() and self.dest == job.dest.upper()

    def age_hours(self, now: float | None = None) -> float:
        return ((now or time.time()) - self.generated) / 3600.0 if self.generated else 0.0


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_ofp(data: dict) -> Ofp:
    """Turn SimBrief's JSON into an Ofp. Raises SimBriefError for an error reply."""
    status = str(data.get("fetch", {}).get("status", "Success"))
    if not status.lower().startswith("success"):
        raise SimBriefError(status.replace("Error: ", "") or "SimBrief returned an error")
    if "general" not in data:
        raise SimBriefError("SimBrief did not return a flight plan.")
    units = str(data.get("params", {}).get("units", "LBS")).upper()
    k = LB_PER_KG if units.startswith("K") else 1.0          # SimBrief reports weights in the plan's own units
    g, fuel, w, t = data.get("general", {}), data.get("fuel", {}), data.get("weights", {}), data.get("times", {})
    files = data.get("files", {})
    pdf = ""
    if files.get("pdf", {}).get("link"):
        pdf = str(files.get("directory", "https://www.simbrief.com/ofp/flightplans/")) + str(files["pdf"]["link"])
    navlog = data.get("navlog", {}).get("fix", [])
    if isinstance(navlog, dict):
        navlog = [navlog]
    return Ofp(
        origin=str(data.get("origin", {}).get("icao_code", "")).upper(),
        dest=str(data.get("destination", {}).get("icao_code", "")).upper(),
        alternate=str(data.get("alternate", {}).get("icao_code", "")).upper(),
        aircraft=str(data.get("aircraft", {}).get("icaocode") or data.get("aircraft", {}).get("icao_code", "")),
        registration=str(data.get("aircraft", {}).get("reg", "")),
        route=str(g.get("route", "")),
        cruise_alt_ft=int(_num(g.get("initial_altitude"))),
        distance_nm=_num(g.get("air_distance") or g.get("route_distance")),
        ete_min=_num(t.get("est_time_enroute")) / 60.0,
        pax=int(_num(w.get("pax_count"))),
        cargo_lb=_num(w.get("cargo")) * k,
        payload_lb=_num(w.get("payload")) * k,
        zfw_lb=_num(w.get("est_zfw")) * k,
        tow_lb=_num(w.get("est_tow")) * k,
        block_fuel_lb=_num(fuel.get("plan_ramp")) * k,
        trip_fuel_lb=_num(fuel.get("enroute_burn")) * k,
        reserve_fuel_lb=_num(fuel.get("reserve")) * k,
        taxi_fuel_lb=_num(fuel.get("taxi")) * k,
        generated=_num(data.get("params", {}).get("time_generated")),
        pdf_url=pdf,
        static_id=str(data.get("params", {}).get("static_id", "")),
        waypoints=[str(f.get("ident", "")) for f in navlog if f.get("ident")][:200],
    )


def fetch_latest(user: str, static_id: str = "", timeout: float = 15.0) -> Ofp:
    """The newest plan for a SimBrief username (letters) or Pilot ID (digits)."""
    user = user.strip()
    if not user:
        raise SimBriefError("Enter your SimBrief username or Pilot ID in Settings > General first.")
    params: dict[str, str] = {"json": "1"}
    params["userid" if user.isdigit() else "username"] = user
    if static_id:
        params["static_id"] = static_id
    try:
        r = httpx.get(FETCH_URL, params=params, timeout=timeout, headers={"User-Agent": f"SkyDispatch/{__version__}"})
    except httpx.HTTPError as exc:
        raise SimBriefError(f"Could not reach SimBrief: {exc}") from exc
    try:
        data = r.json()
    except ValueError:
        raise SimBriefError("SimBrief sent an unreadable reply.") from None
    return parse_ofp(data)         # SimBrief reports problems (unknown user, no plan) inside the JSON, with HTTP 400


def is_trusted_link(url: str) -> bool:
    u = httpx.URL(url)
    return u.scheme == "https" and any(u.host == h or u.host.endswith("." + h) for h in HOSTS)

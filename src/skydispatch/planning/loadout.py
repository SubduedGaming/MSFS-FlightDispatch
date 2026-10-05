"""What the aircraft should carry for the active job, and how to put exactly that into the simulator.

SkyDispatch is the source of truth: a hangar aircraft carries the fuel it has in the hangar, a company aircraft carries
the SimBrief block fuel (or an estimate), and the payload is the contract's passengers and cargo. ``apply_loadout``
writes that into MSFS through a tiny get/set interface, so it can be tested without a simulator.

Safety: nothing is written unless the aircraft is on the ground with engines off, values are clamped to what the
aircraft type can carry, the pilot's own station is never touched, and the result is read back and reported.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..data.aircraft import AircraftType, match_title

log = logging.getLogger(__name__)

PAX_LB = 190.0                  # average passenger including carry-on
ESTIMATE_RESERVE_H = 0.75       # when there is no SimBrief plan: trip fuel + taxi + 45 minutes
ESTIMATE_TAXI_H = 0.1
SEAT_MAX_LB = 400.0             # sanity limit for one seat station
FUEL_TANKS = ("LEFT_MAIN", "RIGHT_MAIN", "CENTER", "CENTER2", "CENTER3", "LEFT_AUX", "RIGHT_AUX", "LEFT_TIP",
              "RIGHT_TIP", "EXTERNAL1", "EXTERNAL2")


class LoadoutError(Exception):
    pass


class SimVars(Protocol):
    def get(self, name: str): ...
    def set(self, name: str, value) -> bool: ...


@dataclass
class Loadout:
    fuel_gal: float
    pax: int
    cargo_lb: float
    fuel_source: str = "hangar"          # hangar | simbrief | estimate
    notes: list[str] = field(default_factory=list)

    @property
    def payload_lb(self) -> float:
        return self.pax * PAX_LB + self.cargo_lb


@dataclass
class LoadoutResult:
    fuel_gal: float = 0.0                # what the sim reports afterwards
    payload_lb: float = 0.0              # passenger and cargo weight now on the payload stations (pilot excluded)
    messages: list[str] = field(default_factory=list)
    fuel_ok: bool = True                 # the sim accepted the fuel we asked for
    payload_ok: bool = True              # ... and the payload


def estimate_fuel(job, atype: AircraftType) -> float:
    hours = job.distance_nm / max(60, atype.cruise_kts) + ESTIMATE_TAXI_H + ESTIMATE_RESERVE_H
    return min(atype.fuel_cap_gal, round(hours * atype.fuel_gph, 1))


def plan_loadout(job, atype: AircraftType, aircraft=None, ofp=None) -> Loadout:
    """The target fuel and payload for `job` flown in `atype`. `aircraft` is the hangar aircraft, if any."""
    notes: list[str] = []
    if aircraft is not None and not job.employer_id:
        fuel, source = min(aircraft.fuel_gal, atype.fuel_cap_gal), "hangar"
    elif ofp is not None and ofp.matches(job) and ofp.block_fuel_lb > 0:
        fuel, source = min(ofp.block_fuel_gal(atype), atype.fuel_cap_gal), "simbrief"
    else:
        fuel, source = estimate_fuel(job, atype), "estimate"
    if ofp is not None and ofp.matches(job) and source == "hangar" and ofp.block_fuel_gal(atype) > fuel + 1:
        notes.append(f"SimBrief wants {ofp.block_fuel_gal(atype):.0f} gal but the aircraft has {fuel:.0f} gal. "
                     "Refuel in the hangar to match the plan.")
    pax, cargo = min(job.pax, atype.pax), min(float(job.cargo_lb), float(atype.cargo_lb))
    return Loadout(round(fuel, 1), pax, cargo, source, notes)


# Add-on aircraft with their own weight and fuel systems (PMDG, Fenix, FlyByWire...) keep them in their own EFB/CDU and
# ignore or overwrite what an outside tool writes into the simulator's variables.
_OWN_SYSTEMS = ("pmdg",)


def ready_problem(state, atype: AircraftType | None, plan: "Loadout | None" = None) -> str | None:
    """Why the sim aircraft must not be loaded right now, or None when it is safe.

    The aircraft has to be on the ground and stationary (engines may be running: MSFS starts flights that way). The
    aircraft in the sim must be the contract's type, and not one that manages its own loading."""
    if state is None:
        return "The simulator is not connected."
    if not state.on_ground or state.gs > 3:
        return "The aircraft must be parked on the ground."
    title = (state.title or "").lower()
    if any(k in title for k in _OWN_SYSTEMS):
        want = (f" Enter fuel {plan.fuel_gal:.0f} gal ({plan.fuel_gal * (atype.fuel_lb_per_gal if atype else 6.0):,.0f} lb) "
                f"and payload {plan.payload_lb:,.0f} lb there." if plan else "")
        return (f"{state.title} manages its own fuel and payload in its EFB or CDU (which can import your SimBrief "
                f"plan), so SkyDispatch leaves it alone.{want}")
    if atype is not None:
        sim_type = match_title(state.title or "")
        if sim_type is None or sim_type.id != atype.id:
            return (f"The aircraft in the sim ({state.title or 'unknown'}) is not the contract's aircraft "
                    f"({atype.name}), so nothing was changed.")
    return None


def station_weights(count: int, pax: int, cargo_lb: float) -> tuple[dict[int, float], list[str]]:
    """Weights for payload stations 2..count (station 1 is the pilot and is never changed).

    Small aircraft: stations are seats with the baggage compartment last. Airliners (many stations): passengers are
    spread over the cabin stations and cargo over the last two (the holds)."""
    notes: list[str] = []
    if count < 2:
        return {}, ["This aircraft has no payload stations to load."]
    out = {i: 0.0 for i in range(2, count + 1)}
    holds = [count - 1, count] if count >= 12 else [count] if count >= 4 else []
    seats = [i for i in range(2, count + 1) if i not in holds]
    if cargo_lb > 0:
        if holds:
            for h in holds:
                out[h] = round(cargo_lb / len(holds), 1)
        else:
            notes.append("This aircraft has no baggage station, so the cargo is not loaded in the sim.")
    if pax > 0 and seats:
        if count >= 12:
            for s in seats:
                out[s] = round(pax * PAX_LB / len(seats), 1)
        else:
            seated = min(pax, len(seats))
            for s in seats[:seated]:
                out[s] = PAX_LB
            if pax > len(seats):
                notes.append(f"Only {len(seats)} seats are available in the sim for {pax} passengers.")
            if seated < pax and seats:
                extra = (pax - seated) * PAX_LB
                out[seats[-1]] = min(SEAT_MAX_LB, out[seats[-1]] + extra)
    elif pax > 0:
        notes.append("This aircraft has no passenger stations in the sim.")
    for i, w in out.items():
        if i not in holds:
            out[i] = min(w, SEAT_MAX_LB if count < 12 else 5000.0)
    return out, notes


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _read_back(sim: SimVars, stations: list[int]) -> tuple[float, float]:
    fuel = _f(sim.get("FUEL_TOTAL_QUANTITY"))
    payload = sum(_f(sim.get(f"PAYLOAD_STATION_WEIGHT:{i}")) for i in stations)
    return fuel, payload


def apply_loadout(sim: SimVars, plan: Loadout, atype: AircraftType,
                  sleep: Callable[[float], None] = time.sleep) -> LoadoutResult:
    """Write `plan` into the simulator and check that it stuck. Raises LoadoutError when nothing was accepted."""
    res = LoadoutResult()
    # ---- what the aircraft has
    caps = {t: _f(sim.get(f"FUEL_TANK_{t}_CAPACITY")) for t in FUEL_TANKS}
    caps = {t: c for t, c in caps.items() if c > 0.01}
    total_cap = sum(caps.values())
    target_fuel = max(0.0, min(plan.fuel_gal, total_cap)) if caps else 0.0
    count = int(_f(sim.get("PAYLOAD_STATION_COUNT")))
    weights, notes = station_weights(count, plan.pax, plan.cargo_lb)
    res.messages += notes
    log.info("Loadout: tanks %s, %d payload stations; target fuel %.1f gal, stations %s", caps, count, target_fuel, weights)
    if caps and plan.fuel_gal > total_cap + 0.5:
        res.messages.append(f"The sim aircraft holds only {total_cap:.0f} gal, so fuel was limited to that.")
    if not caps:
        res.messages.append("Could not read the aircraft's fuel tanks, so fuel was not changed.")

    def write() -> None:
        for t, cap in caps.items():
            ok = sim.set(f"FUEL_TANK_{t}_QUANTITY", round(cap * target_fuel / total_cap, 2))
            log.debug("set %s quantity -> %s", t, ok)
        for i, w in weights.items():
            ok = sim.set(f"PAYLOAD_STATION_WEIGHT:{i}", w)
            log.debug("set payload station %d = %.1f -> %s", i, w, ok)

    def fuel_matches(v: float) -> bool:
        return not caps or abs(v - target_fuel) <= max(0.6, 0.02 * target_fuel)

    def payload_matches(v: float) -> bool:
        return not weights or abs(v - sum(weights.values())) <= 3.0

    if not caps and not weights:
        raise LoadoutError("This aircraft exposes no fuel tanks or payload stations to SkyDispatch, so it cannot be "
                           "loaded from here. Use the aircraft's own loading screen.")
    write()
    sleep(0.5)
    fuel, payload = _read_back(sim, list(weights))
    if not (fuel_matches(fuel) and payload_matches(payload)):
        log.info("Loadout not applied yet (fuel %.1f, payload %.1f); writing again", fuel, payload)
        write()                                                   # some aircraft apply the first write late or drop it
        sleep(1.0)
        fuel, payload = _read_back(sim, list(weights))
    res.fuel_gal, res.payload_lb = fuel, payload
    res.fuel_ok, res.payload_ok = fuel_matches(fuel), payload_matches(payload)
    if caps and not res.fuel_ok:
        res.messages.append(f"MSFS did not accept the fuel change: it reports {fuel:.0f} gal after we asked for "
                            f"{target_fuel:.0f}.")
    if weights and not res.payload_ok:
        res.messages.append(f"MSFS did not accept the payload change: the stations hold {payload:,.0f} lb after we asked "
                            f"for {sum(weights.values()):,.0f}.")
    log.info("Loadout result: fuel %.1f gal (ok=%s), payload %.1f lb (ok=%s)", fuel, res.fuel_ok, payload, res.payload_ok)
    if (caps and not res.fuel_ok) and (not weights or not res.payload_ok):
        raise LoadoutError(" ".join(res.messages) + " The aircraft may control its own loading; check its own "
                           "weight and balance screen.")
    return res


def diagnose(sim: SimVars, title: str = "") -> list[str]:
    """Everything the loadout depends on, read from the live sim (nothing is written). For troubleshooting."""
    lines = [f"Aircraft title: {title or '(unknown)'}"]
    count = int(_f(sim.get("PAYLOAD_STATION_COUNT")))
    lines.append(f"Payload stations: {count}")
    for i in range(1, count + 1):
        lines.append(f"  station {i}: {_f(sim.get(f'PAYLOAD_STATION_WEIGHT:{i}')):.1f} lb")
    found = 0
    for t in FUEL_TANKS:
        cap = _f(sim.get(f"FUEL_TANK_{t}_CAPACITY"))
        if cap > 0.01:
            found += 1
            lines.append(f"  tank {t}: {_f(sim.get(f'FUEL_TANK_{t}_QUANTITY')):.1f} of {cap:.1f} gal")
    lines.append(f"Fuel tanks found: {found}")
    lines.append(f"Fuel total: {_f(sim.get('FUEL_TOTAL_QUANTITY')):.1f} gal "
                 f"({_f(sim.get('FUEL_TOTAL_QUANTITY_WEIGHT')):,.0f} lb)")
    lines.append(f"Weights: total {_f(sim.get('TOTAL_WEIGHT')):,.0f} lb, empty {_f(sim.get('EMPTY_WEIGHT')):,.0f} lb")
    if any(k in title.lower() for k in _OWN_SYSTEMS):
        lines.append("This add-on manages its own loading, so SkyDispatch will not write to it.")
    for line in lines:
        log.info("Loadout diagnostics: %s", line)
    return lines


@dataclass
class LoadoutStatus:
    fuel_ok: bool
    payload_ok: bool
    sim_fuel_gal: float
    plan_fuel_gal: float
    sim_payload_lb: float | None
    plan_payload_lb: float

    @property
    def matches(self) -> bool:
        return self.fuel_ok and self.payload_ok


def compare(plan: Loadout, state) -> LoadoutStatus | None:
    """Does the live sim aircraft carry what the plan says? None when there is no telemetry.

    The sim's payload includes the pilot, so some crew weight on top of the planned load is normal. When the sim does
    not report its weights the payload is 'unknown' and counted as fine rather than shown as a false mismatch."""
    if state is None:
        return None
    fuel_ok = abs(state.fuel_gal - plan.fuel_gal) <= 0.5 + 0.03 * plan.fuel_gal
    known = state.payload_lb is not None
    payload_ok = (not known) or plan.payload_lb - 5 <= state.payload_lb <= plan.payload_lb + 500
    return LoadoutStatus(fuel_ok, payload_ok, state.fuel_gal, plan.fuel_gal, state.payload_lb, plan.payload_lb)

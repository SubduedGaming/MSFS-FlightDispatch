"""Fleet ownership, fuel, maintenance and wear."""
from __future__ import annotations

import random
import string

from ..data.aircraft import AircraftType, get_type
from ..db.database import Database
from ..db.models import HangarAircraft

REGISTRATION_PREFIX = {"GB": "G-", "IE": "EI-", "FR": "F-", "DE": "D-", "NL": "PH-", "ES": "EC-", "IT": "I-",
                       "US": "N", "CA": "C-", "AU": "VH-", "NZ": "ZK-", "JP": "JA", "BR": "PR-", "ZA": "ZS-"}


class HangarError(Exception):
    """User-facing problem (insufficient funds, wrong place, ...)."""


def airworthiness(a: HangarAircraft, t: AircraftType) -> tuple[bool, str]:
    if a.condition < 25:
        return False, "Condition too poor to fly - repair it"
    if a.hours_since_inspection > t.maint_hours * 1.1:
        return False, "Inspection overdue - schedule maintenance"
    return True, "Airworthy"


def inspection_cost(t: AircraftType) -> float:
    return round(t.hourly_cost * 6 + t.price * 0.0004, -1)


def repair_cost(a: HangarAircraft, t: AircraftType) -> float:
    return round(max(0.0, 100 - a.condition) * t.price * 0.0006, -1)


def resale_value(a: HangarAircraft, t: AircraftType) -> float:
    age_factor = max(0.35, 1.0 - a.hours_total / 12000.0)
    return round(t.price * 0.8 * age_factor * (0.5 + a.condition / 200.0), -2)


class HangarService:
    def __init__(self, db: Database, rng: random.Random | None = None):
        self.db = db
        self.rng = rng or random.Random()

    # -- registration -----------------------------------------------------
    def _new_registration(self, location: str) -> str:
        ap = self.db.airport(location)
        prefix = REGISTRATION_PREFIX.get(ap.country if ap else "", "N")
        for _ in range(100):
            if prefix == "N":
                reg = "N" + str(self.rng.randint(100, 999)) + self.rng.choice(string.ascii_uppercase) + \
                      self.rng.choice(string.ascii_uppercase)
            elif prefix.endswith("-"):
                reg = prefix + "".join(self.rng.choice(string.ascii_uppercase) for _ in range(4 if prefix == "G-" else 3))
            else:
                reg = prefix + str(self.rng.randint(1000, 9999))
            if not self.db.registration_exists(reg):
                return reg
        raise HangarError("Could not allocate a registration")

    # -- buying / selling ---------------------------------------------------
    def buy(self, type_id: str, location: str, nickname: str = "", used: bool = False) -> HangarAircraft:
        t = get_type(type_id)
        pilot = self.db.pilot()
        if not t or not pilot:
            raise HangarError("Unknown aircraft or no pilot profile")
        if not self.db.airport(location):
            raise HangarError(f"Unknown airport {location}")
        price = round(t.price * (0.7 if used else 1.0), -2)
        if pilot.balance < price:
            raise HangarError(f"Insufficient funds: need {price:,.0f}, have {pilot.balance:,.0f}")
        reg = self._new_registration(location)
        condition = round(self.rng.uniform(55, 80), 0) if used else 100.0
        hours = round(self.rng.uniform(800, 3500), 1) if used else 0.0
        aid = self.db.add_aircraft(type_id, reg, location, t.fuel_cap_gal * 0.5, price, nickname, condition)
        if used:
            self.db.update_aircraft(aid, hours_total=hours, hours_since_inspection=self.rng.uniform(0, t.maint_hours * 0.7))
        self.db.add_transaction(-price, "purchase", f"Bought {t.name} ({reg})")
        return self.db.aircraft(aid)  # type: ignore[return-value]

    def sell(self, aircraft_id: int) -> float:
        a = self._get(aircraft_id)
        t = get_type(a.type_id)
        if self.db.active_job() and self.db.active_job().aircraft_id == aircraft_id:  # type: ignore[union-attr]
            raise HangarError("This aircraft is assigned to your active job")
        value = resale_value(a, t)  # type: ignore[arg-type]
        self.db.update_aircraft(aircraft_id, sold=1)
        self.db.add_transaction(value, "sale", f"Sold {t.name} ({a.registration})")  # type: ignore[union-attr]
        return value

    # -- services -----------------------------------------------------------
    def refuel(self, aircraft_id: int, gallons: float | None = None) -> float:
        a = self._get(aircraft_id)
        t = get_type(a.type_id)
        assert t
        space = t.fuel_cap_gal - a.fuel_gal
        gal = space if gallons is None else min(gallons, space)
        if gal <= 0.01:
            return 0.0
        cost = round(gal * t.fuel_price_per_gal, 2)
        pilot = self.db.pilot()
        if not pilot or pilot.balance < cost:
            raise HangarError("Insufficient funds for fuel")
        self.db.update_aircraft(aircraft_id, fuel_gal=a.fuel_gal + gal)
        self.db.add_transaction(-cost, "fuel", f"Fuel {gal:.0f} gal for {a.registration}")
        return cost

    def inspect(self, aircraft_id: int) -> float:
        a = self._get(aircraft_id)
        t = get_type(a.type_id)
        assert t
        cost = inspection_cost(t)
        pilot = self.db.pilot()
        if not pilot or pilot.balance < cost:
            raise HangarError(f"Insufficient funds: inspection costs {cost:,.0f}")
        self.db.update_aircraft(aircraft_id, hours_since_inspection=0.0, condition=min(100.0, a.condition + 3))
        self.db.add_transaction(-cost, "maintenance", f"Inspection {a.registration}")
        return cost

    def repair(self, aircraft_id: int) -> float:
        a = self._get(aircraft_id)
        t = get_type(a.type_id)
        assert t
        cost = repair_cost(a, t)
        if cost <= 0:
            return 0.0
        pilot = self.db.pilot()
        if not pilot or pilot.balance < cost:
            raise HangarError(f"Insufficient funds: repairs cost {cost:,.0f}")
        self.db.update_aircraft(aircraft_id, condition=100.0)
        self.db.add_transaction(-cost, "maintenance", f"Repairs {a.registration}")
        return cost

    def rename(self, aircraft_id: int, nickname: str) -> None:
        self.db.update_aircraft(aircraft_id, nickname=nickname.strip()[:40])

    # -- wear -----------------------------------------------------------------
    def apply_flight(self, aircraft_id: int, hours: float, arrival_icao: str, fuel_remaining_gal: float | None,
                     landing_fpm: float | None, overspeed_s: float, max_g: float, wear: bool = True) -> list[str]:
        """Update an aircraft after a flight. Returns human-readable notes."""
        a = self._get(aircraft_id)
        t = get_type(a.type_id)
        assert t
        notes: list[str] = []
        condition = a.condition
        if wear:
            condition -= hours * 0.08          # ~1% per 12 flight hours
            if landing_fpm is not None and landing_fpm < -400:
                hit = min(25.0, (abs(landing_fpm) - 400) / 40.0)
                condition -= hit
                notes.append(f"Hard landing damaged the airframe (-{hit:.0f}% condition)")
            if overspeed_s > 5:
                hit = min(15.0, overspeed_s / 8.0)
                condition -= hit
                notes.append(f"Overspeed stressed the airframe (-{hit:.0f}% condition)")
            if max_g > 2.5:
                hit = min(12.0, (max_g - 2.5) * 6)
                condition -= hit
                notes.append(f"High G loading (-{hit:.0f}% condition)")
        fields = dict(
            hours_total=a.hours_total + hours,
            hours_since_inspection=a.hours_since_inspection + hours,
            condition=max(0.0, condition),
            location_icao=arrival_icao.upper(),
        )
        if fuel_remaining_gal is not None:
            fields["fuel_gal"] = max(0.0, min(t.fuel_cap_gal, fuel_remaining_gal))
        self.db.update_aircraft(aircraft_id, **fields)
        if fields["hours_since_inspection"] > t.maint_hours:
            notes.append("Inspection is due")
        return notes

    def _get(self, aircraft_id: int) -> HangarAircraft:
        a = self.db.aircraft(aircraft_id)
        if not a or a.sold:
            raise HangarError("Aircraft not found")
        return a

    def starter_fleet(self, type_id: str, location: str, nickname: str = "") -> HangarAircraft:
        """Free first aircraft for a new career."""
        t = get_type(type_id)
        if not t:
            raise HangarError("Unknown aircraft")
        reg = self._new_registration(location)
        aid = self.db.add_aircraft(type_id, reg, location, t.fuel_cap_gal * 0.75, 0.0, nickname, 92.0)
        return self.db.aircraft(aid)  # type: ignore[return-value]

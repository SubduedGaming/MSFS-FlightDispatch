"""Tools the dispatcher can call. Each returns a JSON-serialisable dict.

The LLM can only change the world through these functions, and they go through
the same rule checks as the GUI (Career/HangarService), so the AI can't cheat.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from ..career import Career, CareerError
from ..core.geo import fmt_duration
from ..data.aircraft import CATALOG, get_type
from ..hangar.service import HangarError, airworthiness, inspection_cost, repair_cost, resale_value
from ..jobs.pricing import KIND_LABEL

log = logging.getLogger(__name__)


def _schema(name: str, desc: str, props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


_INT = {"type": "integer"}
_BOOL_CONFIRM = {"type": "boolean", "description": "Must be true, and only after the pilot explicitly agreed."}

TOOL_SCHEMAS: list[dict] = [
    _schema("get_status", "Pilot profile, balance, reputation, current job, and live flight status."),
    _schema("list_jobs", "List open jobs on the job board. Optionally filter.",
            {"limit": {"type": "integer", "description": "Max jobs (default 6)"},
             "kind": {"type": "string", "enum": ["passenger", "cargo", "charter", "medevac", "mail"]},
             "from_icao": {"type": "string", "description": "Only jobs departing this airport"},
             "only_flyable": {"type": "boolean", "description": "Only jobs one of the pilot's aircraft can fly now"}}),
    _schema("get_job", "Full details of a job incl. which hangar aircraft are eligible.", {"job_id": _INT}, ["job_id"]),
    _schema("accept_job", "Accept a job and assign a hangar aircraft.",
            {"job_id": _INT, "aircraft_id": _INT}, ["job_id", "aircraft_id"]),
    _schema("decline_job", "Decline a job from the board.", {"job_id": _INT}, ["job_id"]),
    _schema("refresh_jobs", "Ask for fresh contracts to be added to the board."),
    _schema("abandon_job", "Abandon the accepted job (costs reputation).", {"confirmed": _BOOL_CONFIRM}, ["confirmed"]),
    _schema("list_hangar", "List the pilot's aircraft with location, fuel, condition and maintenance state."),
    _schema("refuel_aircraft", "Fill an aircraft's tanks (costs money).", {"aircraft_id": _INT}, ["aircraft_id"]),
    _schema("inspect_aircraft", "Perform a scheduled inspection (resets inspection timer, costs money).",
            {"aircraft_id": _INT}, ["aircraft_id"]),
    _schema("repair_aircraft", "Repair damage to full condition (costs money).", {"aircraft_id": _INT}, ["aircraft_id"]),
    _schema("list_dealer", "List aircraft models available to buy with prices."),
    _schema("buy_aircraft", "Buy a new aircraft delivered to a given airport.",
            {"type_id": {"type": "string"}, "location_icao": {"type": "string"}, "confirmed": _BOOL_CONFIRM},
            ["type_id", "location_icao", "confirmed"]),
    _schema("sell_aircraft", "Sell an aircraft from the hangar.",
            {"aircraft_id": _INT, "confirmed": _BOOL_CONFIRM}, ["aircraft_id", "confirmed"]),
    _schema("get_metar", "Get the live METAR weather report for an airport (needs internet).",
            {"icao": {"type": "string"}}, ["icao"]),
    _schema("recent_flights", "The pilot's most recent logbook entries.", {"limit": _INT}),
]


class ToolBox:
    def __init__(self, career: Career, http_timeout: float = 6.0):
        self.career = career
        self.db = career.db
        self._timeout = http_timeout
        self._handlers: dict[str, Callable[..., dict]] = {
            n: getattr(self, f"t_{n}") for n in (s["function"]["name"] for s in TOOL_SCHEMAS)}

    def call(self, name: str, args: dict[str, Any]) -> dict:
        fn = self._handlers.get(name)
        if not fn:
            return {"error": f"Unknown tool '{name}'"}
        try:
            return fn(**{k: v for k, v in (args or {}).items()})
        except (CareerError, HangarError) as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"Bad arguments for {name}: {exc}"}
        except Exception as exc:  # keep the conversation alive
            log.exception("tool %s failed", name)
            return {"error": f"{name} failed: {exc}"}

    # ------------------------------------------------------------------ tools
    def t_get_status(self) -> dict:
        p = self.db.pilot()
        if not p:
            return {"error": "No pilot profile yet"}
        job = self.db.active_job()
        live = self.career.live()
        out: dict[str, Any] = {
            "pilot": p.name, "callsign": p.callsign, "rank": p.rank, "balance": round(p.balance),
            "reputation": round(p.reputation, 1), "hours": round(p.total_minutes / 60, 1),
            "active_job": None, "flight": None}
        if job:
            out["active_job"] = {"id": job.id, "title": job.title, "from": job.origin, "to": job.dest,
                                 "payout": round(job.payout), "status": job.status}
        if live and live.phase != "parked":
            out["flight"] = {"phase": live.phase, "elapsed": fmt_duration(live.elapsed_min),
                             "remaining_nm": None if live.remaining_nm is None else round(live.remaining_nm),
                             "eta": None if live.eta_min is None else fmt_duration(live.eta_min)}
        return out

    def t_list_jobs(self, limit: int = 6, kind: str | None = None, from_icao: str | None = None,
                    only_flyable: bool = False) -> dict:
        jobs = self.db.jobs("offered")
        rows = []
        for j in jobs:
            if kind and j.kind != kind:
                continue
            if from_icao and j.origin != from_icao.upper():
                continue
            elig = self.career.eligible_aircraft(j)
            flyable = [a.id for a, e in elig if e.ok]
            if only_flyable and not flyable:
                continue
            rows.append({"id": j.id, "type": KIND_LABEL[j.kind], "from": j.origin, "to": j.dest,
                         "distance_nm": round(j.distance_nm), "load": f"{j.pax} pax" if j.pax else f"{j.cargo_lb} lb",
                         "payout": round(j.payout), "deadline": fmt_duration(j.deadline_minutes),
                         "min_reputation": j.min_reputation, "flyable_with_aircraft_ids": flyable})
        rows.sort(key=lambda r: -r["payout"])
        return {"count": len(rows), "jobs": rows[:max(1, min(int(limit), 12))]}

    def t_get_job(self, job_id: int) -> dict:
        j = self.db.job(int(job_id))
        if not j:
            return {"error": "No such job"}
        o, d = self.db.airport(j.origin), self.db.airport(j.dest)
        return {"id": j.id, "title": j.title, "status": j.status, "client": j.client, "briefing": j.briefing,
                "from": f"{o.name} ({o.icao})" if o else j.origin, "to": f"{d.name} ({d.icao})" if d else j.dest,
                "distance_nm": round(j.distance_nm), "payout": round(j.payout), "min_category": j.min_category,
                "min_reputation": j.min_reputation, "deadline": fmt_duration(j.deadline_minutes),
                "aircraft_check": [{"aircraft_id": a.id, "registration": a.registration, "ok": e.ok,
                                    "problems": e.reasons} for a, e in self.career.eligible_aircraft(j)]}

    def t_accept_job(self, job_id: int, aircraft_id: int) -> dict:
        job = self.career.accept_job(int(job_id), int(aircraft_id))
        return {"ok": True, "accepted": job.title, "aircraft_id": job.aircraft_id}

    def t_decline_job(self, job_id: int) -> dict:
        self.career.decline_job(int(job_id))
        return {"ok": True}

    def t_refresh_jobs(self) -> dict:
        return {"ok": True, "new_jobs": self.career.refresh_market()}

    def t_abandon_job(self, confirmed: bool = False) -> dict:
        if not confirmed:
            return {"error": "Ask the pilot to confirm first"}
        self.career.abandon_job()
        return {"ok": True}

    def t_list_hangar(self) -> dict:
        out = []
        for a in self.db.hangar():
            t = get_type(a.type_id)
            ok, why = airworthiness(a, t) if t else (False, "unknown type")
            out.append({"id": a.id, "registration": a.registration, "type": t.name if t else a.type_id,
                        "at": a.location_icao, "fuel_gal": round(a.fuel_gal), "fuel_capacity": t.fuel_cap_gal if t else 0,
                        "condition_pct": round(a.condition), "hours": round(a.hours_total, 1),
                        "hours_to_inspection": round(max(0, (t.maint_hours if t else 100) - a.hours_since_inspection), 1),
                        "airworthy": ok, "note": why,
                        "repair_cost": round(repair_cost(a, t)) if t else 0,
                        "inspection_cost": round(inspection_cost(t)) if t else 0})
        return {"aircraft": out}

    def t_refuel_aircraft(self, aircraft_id: int) -> dict:
        return {"ok": True, "cost": self.career.hangar.refuel(int(aircraft_id))}

    def t_inspect_aircraft(self, aircraft_id: int) -> dict:
        return {"ok": True, "cost": self.career.hangar.inspect(int(aircraft_id))}

    def t_repair_aircraft(self, aircraft_id: int) -> dict:
        return {"ok": True, "cost": self.career.hangar.repair(int(aircraft_id))}

    def t_list_dealer(self) -> dict:
        return {"aircraft": [{"type_id": t.id, "name": t.name, "category": t.category, "price": t.price,
                              "seats": t.pax, "cargo_lb": t.cargo_lb, "range_nm": t.range_nm,
                              "cruise_kts": t.cruise_kts} for t in CATALOG]}

    def t_buy_aircraft(self, type_id: str, location_icao: str, confirmed: bool = False) -> dict:
        if not confirmed:
            return {"error": "Ask the pilot to confirm the purchase first"}
        a = self.career.hangar.buy(type_id, location_icao)
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")
        return {"ok": True, "registration": a.registration, "aircraft_id": a.id}

    def t_sell_aircraft(self, aircraft_id: int, confirmed: bool = False) -> dict:
        if not confirmed:
            a = self.db.aircraft(int(aircraft_id))
            t = get_type(a.type_id) if a else None
            return {"error": "Ask the pilot to confirm the sale first",
                    "offer": round(resale_value(a, t)) if a and t else None}
        value = self.career.hangar.sell(int(aircraft_id))
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")
        return {"ok": True, "sale_price": value}

    def t_get_metar(self, icao: str) -> dict:
        return fetch_metar(icao, self._timeout)

    def t_recent_flights(self, limit: int = 5) -> dict:
        return {"flights": [{"id": f.id, "route": f"{f.dep or '?'}-{f.arr or '?'}", "outcome": f.outcome,
                             "score": round(f.score), "payout": round(f.payout),
                             "landing_fpm": None if f.landing_fpm is None else round(abs(f.landing_fpm)),
                             "air_time": fmt_duration(f.air_min)} for f in self.db.flights(max(1, min(int(limit), 15)))]}


def fetch_metar(icao: str, timeout: float = 6.0) -> dict:
    icao = (icao or "").strip().upper()
    if len(icao) != 4 or not icao.isalnum():
        return {"error": "Give a 4-letter ICAO code"}
    try:
        r = httpx.get("https://aviationweather.gov/api/data/metar", params={"ids": icao, "format": "json"},
                      timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not data:
            return {"icao": icao, "metar": None, "note": "No METAR available for this station"}
        return {"icao": icao, "metar": data[0].get("rawOb"), "wind_dir": data[0].get("wdir"),
                "wind_kt": data[0].get("wspd"), "visibility": data[0].get("visib"),
                "temp_c": data[0].get("temp")}
    except (httpx.HTTPError, ValueError) as exc:
        return {"icao": icao, "error": f"Weather service unavailable ({type(exc).__name__})"}

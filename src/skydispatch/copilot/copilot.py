"""The copilot: answers the pilot with the AI model using live telemetry; the quick buttons are deterministic reports."""
from __future__ import annotations

import json
import logging
import re
import threading

from ..ai.llm import LLMError, LMStudioClient
from ..ai.tools import fetch_metar
from ..career import Career
from ..core import fmt
from ..core.config import Settings
from ..data.aircraft import get_type, match_title
from . import advice
from .monitor import CopilotMonitor, Callout
from .personas import get_copilot

log = logging.getLogger(__name__)

THREAD = "copilot"
WEATHER_TOOL = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "The current METAR weather report for an airport. Use it whenever the captain asks about weather, "
                   "wind, visibility, temperature or conditions. Pass the ICAO code of the departure, the destination "
                   "or whichever airport they asked about.",
    "parameters": {"type": "object", "properties": {"icao": {"type": "string", "description": "4-letter ICAO code"}},
                   "required": ["icao"]}}}]

QUICK_ACTIONS = [("checklist", "Checklist"), ("fuel", "Fuel check"), ("descent", "Descent plan"),
                 ("approach", "Approach brief"), ("weather", "Weather"), ("status", "Status"), ("job", "Job details")]


class CoPilot:
    def __init__(self, career: Career, settings: Settings, client: LMStudioClient | None = None):
        self.career, self.db, self.settings = career, career.db, settings
        self.client = client or LMStudioClient(settings.ai)
        self.monitor = CopilotMonitor()
        self._lock = threading.RLock()
        self._flight_key = None

    @property
    def persona(self):
        return get_copilot(self.settings.ai.copilot)

    # ------------------------------------------------------------------ context
    def _flight(self):
        """(state, live, atype, dest, job) for the current/most recent flight."""
        state, live = self.career.last_state, self.career.live()
        rec = self.career.recorder
        job = self.career.active_job
        atype = rec.atype if rec else None
        if atype is None and state and state.title:
            atype = match_title(state.title)
        dest = rec.dest if rec and rec.dest else (self.db.airport(job.dest) if job else None)
        return state, live, atype, dest, job

    def in_flight(self) -> bool:
        rec = self.career.recorder
        return bool(rec and rec.started and not rec.finished)

    def context(self) -> dict:
        state, live, atype, dest, job = self._flight()
        ctx: dict = {"flight_in_progress": self.in_flight()}
        if atype:
            ctx["aircraft"] = {"name": atype.name, "class": atype.category, "cruise_kts": atype.cruise_kts,
                               "vne_kts": atype.vne_kts, "approach_speed_kt": atype.vref_kts,
                               "fuel_burn_gph": atype.fuel_gph}
        flying = self.in_flight()
        if not flying:
            ctx["note"] = "The flight has not started: the aircraft is parked and no live flight data exists yet."
        if state and flying:
            ctx["live"] = {"altitude_msl_ft": round(state.alt_msl), "altitude_agl_ft": round(state.alt_agl),
                           "ias_kt": round(state.ias), "groundspeed_kt": round(state.gs),
                           "vertical_speed_fpm": round(state.vs), "heading": round(state.heading),
                           "on_ground": state.on_ground, "fuel_gal": round(state.fuel_gal, 1),
                           "g": round(state.g_force, 2), "gear_down": state.gear_down, "flaps_pct": round(state.flaps)}
        if live and flying:
            ctx["phase"] = live.phase
            if live.remaining_nm is not None:
                ctx["distance_to_destination_nm"] = round(live.remaining_nm)
            if live.eta_min:
                ctx["eta_min"] = round(live.eta_min)
        if dest:
            ctx["destination"] = {"icao": dest.icao, "name": dest.name, "elevation_ft": round(dest.elevation_ft),
                                  "longest_runway_ft": dest.runway_ft}
        if job:
            ctx["job"] = self._job_facts(job)
        pilot = self.db.pilot()
        if pilot:
            ctx["pilot"] = {"name": pilot.name, "callsign": pilot.callsign, "home": pilot.home_icao}
        return ctx

    def _job_facts(self, job) -> dict:
        """What the pilot has been assigned: where from and to, what is on board, which aircraft."""
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        facts: dict = {"title": job.title, "kind": job.kind, "status": job.status,
                       "departure": {"icao": job.origin, "name": o.name if o else ""},
                       "destination": {"icao": job.dest, "name": d.name if d else ""},
                       "distance_nm": round(job.distance_nm)}
        if job.pax:
            facts["passengers"] = job.pax
        if job.cargo_lb:
            facts["cargo_lb"] = job.cargo_lb
        plane = self._job_aircraft(job)
        if plane:
            facts["assigned_aircraft"] = plane
        facts["pays"] = fmt.money(self.settings, job.payout)
        facts["client"] = job.client
        facts["deadline_minutes"] = job.deadline_minutes
        return facts

    def _job_aircraft(self, job) -> str:
        if job.employer_id and job.provided_type:
            t = get_type(job.provided_type)
            return f"{t.name} (company aircraft)" if t else ""
        a = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        t = get_type(a.type_id) if a else None
        return f"{t.name} {a.registration}" if a and t else ""

    # ------------------------------------------------------------ deterministic help
    def quick(self, kind: str) -> str:
        state, live, atype, dest, _job = self._flight()
        if kind == "checklist":
            phase = live.phase if live else "parked"
            name, items = advice.checklist_for_phase(phase, atype, state.alt_agl if state else 9999,
                                                     state.on_ground if state else True)
            return f"{name.capitalize()} checklist: " + "; ".join(items) + "."
        if kind == "fuel":
            if not state or not atype:
                return "I can't read the fuel system yet."
            burn = state.fuel_flow_gph or atype.fuel_gph
            return advice.fuel_report(state.fuel_gal, burn, live.remaining_nm if live else None, state.gs).text
        if kind == "descent":
            if not state:
                return "No data yet."
            return advice.descent_plan(state.alt_msl, dest.elevation_ft if dest else 0.0,
                                       live.remaining_nm if live else None, state.gs, atype)
        if kind == "approach":
            return advice.approach_brief(dest, atype)
        if kind == "weather":
            target = dest or (self.db.nearest_airport(state.lat, state.lon)[0] if state else None)
            if not target:
                return "I don't have a station to check."
            m = fetch_metar(target.icao)
            if m.get("metar"):
                return f"{target.icao} weather: {m['metar']}"
            if m.get("error"):
                return f"The weather service didn't answer for {target.icao}: {m['error']}."
            return f"There is no weather report for {target.icao}."
        if kind == "job":
            return self._job_answer()
        return advice.status_text(state, live, atype, dest)

    def _job_answer(self) -> str:
        job = self.career.active_job
        if not job:
            return "There is no job on right now. Pick one from the job board."
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        load = (f"{job.pax} passenger{'s' if job.pax != 1 else ''}" if job.pax else
                f"{job.cargo_lb} pounds of cargo" if job.cargo_lb else "no load")
        plane = self._job_aircraft(job)
        return (f"{load.capitalize()} from {o.name if o else job.origin} ({job.origin}) to "
                f"{d.name if d else job.dest} ({job.dest}), {round(job.distance_nm)} nautical miles"
                + (f", in {plane}." if plane else "."))

    # ------------------------------------------------------------------ chat
    def system_prompt(self) -> str:
        p = self.persona
        return (f"{p.style}\nYou are a real person, the first officer in the right seat of the pilot's aircraft in "
                "Microsoft Flight Simulator, talking out loud to the captain. Answer the question they actually asked, "
                "directly and in natural speech, like a colleague would: no stock phrases, no listing what you can do. "
                "Casual chat and small talk are fine; answer it as a person would, then steer back to the flight if "
                "it fits. Use the DATA below for anything about this flight, the job, the aircraft, the airports or "
                "the numbers; never invent numbers, and if the data does not say, say you don't have it. If the "
                "aircraft is not airborne yet, that is normal: help with the plan and the job. The captain makes "
                "the decisions. One to three short spoken sentences, no markdown, no lists, no emoji.\nDATA: "
                + json.dumps(self.context()))

    def ask(self, text: str, add_user: bool = True) -> str:
        """Answer the pilot with the AI model. Raises LLMError if the model cannot answer: there is no canned reply."""
        text = text.strip()
        if not text:
            return ""
        with self._lock:
            if add_user:
                self.db.add_message("user", text, THREAD)
            history = [{"role": r["role"], "content": r["content"]} for r in self.db.messages(8, THREAD)
                       if r["role"] in ("user", "assistant")]
            messages = [{"role": "system", "content": self.system_prompt()}] + history
            for _round in range(3):
                r = self.client.chat(messages, tools=WEATHER_TOOL, max_tokens=220, temperature=0.6)
                if not r.tool_calls:
                    break
                messages.append(r.raw_message)                       # the model asked for the weather: look it up
                for call in r.tool_calls:
                    metar = fetch_metar(str(call.arguments.get("icao", "")))
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(metar)})
            else:
                raise LLMError("The AI model kept asking for tools instead of answering.")
            reply = re.sub(r"[*_`#]+", "", r.text.strip())
            if not reply:
                raise LLMError("The AI model returned an empty answer. If it is a reasoning model, turn its thinking "
                               "off or raise the reply length in Settings > AI.")
            self.db.add_message("assistant", reply, THREAD)
            self.career._fire("thread_changed", thread=THREAD)
            return reply

    def say_quick(self, kind: str) -> str:
        label = dict(QUICK_ACTIONS).get(kind, kind)
        with self._lock:
            self.db.add_message("user", label, THREAD)
            reply = self.quick(kind)
            self.db.add_message("assistant", reply, THREAD)
            self.career._fire("thread_changed", thread=THREAD)
            return reply

    # --------------------------------------------------------------- callouts
    def callouts(self, state, now: float) -> list[Callout]:
        if not (self.settings.ai.copilot_enabled and self.settings.ai.copilot_callouts) or not self.in_flight():
            return []
        rec = self.career.recorder
        key = id(rec)
        if key != self._flight_key:
            self._flight_key = key
            self.monitor.reset()
        _s, live, atype, dest, _j = self._flight()
        return self.monitor.feed(state, live, atype, dest, now)

    def record_callout(self, text: str) -> None:
        self.db.add_message("assistant", text, THREAD, kind="callout")
        self.career._fire("thread_changed", thread=THREAD)

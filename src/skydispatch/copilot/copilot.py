"""The copilot: answers questions during a flight using live telemetry, with deterministic offline fallbacks."""
from __future__ import annotations

import json
import logging
import re
import threading

from ..ai.llm import LLMError, LMStudioClient
from ..ai.tools import fetch_metar
from ..career import Career
from ..core.config import Settings
from ..data.aircraft import match_title
from . import advice
from .monitor import CopilotMonitor, Callout
from .personas import get_copilot

log = logging.getLogger(__name__)

THREAD = "copilot"
QUICK_ACTIONS = [("checklist", "Checklist"), ("fuel", "Fuel check"), ("descent", "Descent plan"),
                 ("approach", "Approach brief"), ("weather", "Weather"), ("status", "Status")]

_INTENTS = [
    ("checklist", r"check ?list|before (start|takeoff|landing)|procedure"),
    ("fuel", r"fuel|gas|endurance|reserve|range"),
    ("descent", r"descen|top of descent|\btod\b|start down|how (far|early).*(down|descend)"),
    ("approach", r"approach|landing|land\b|runway|vref|final|go.?around|flaps"),
    ("weather", r"weather|metar|wind|visib|ceiling|temperature"),
    ("status", r"status|where are we|eta|how (are|is)|position|altitude|speed|time to"),
]


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
        if state:
            ctx["live"] = {"altitude_msl_ft": round(state.alt_msl), "altitude_agl_ft": round(state.alt_agl),
                           "ias_kt": round(state.ias), "groundspeed_kt": round(state.gs),
                           "vertical_speed_fpm": round(state.vs), "heading": round(state.heading),
                           "on_ground": state.on_ground, "fuel_gal": round(state.fuel_gal, 1),
                           "g": round(state.g_force, 2), "gear_down": state.gear_down, "flaps_pct": round(state.flaps)}
        if live:
            ctx["phase"] = live.phase
            if live.remaining_nm is not None:
                ctx["distance_to_destination_nm"] = round(live.remaining_nm)
            if live.eta_min:
                ctx["eta_min"] = round(live.eta_min)
        if dest:
            ctx["destination"] = {"icao": dest.icao, "name": dest.name, "elevation_ft": round(dest.elevation_ft),
                                  "longest_runway_ft": dest.runway_ft}
        if job:
            ctx["job"] = job.title
        return ctx

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
            return f"I can't get weather for {target.icao} right now."
        return advice.status_text(state, live, atype, dest)

    def offline_answer(self, text: str) -> str:
        low = text.lower()
        for kind, pattern in _INTENTS:
            if re.search(pattern, low):
                return self.quick(kind)
        return ("Standing by, Captain. I can run a checklist, check fuel, plan the descent, brief the approach, "
                "pull the weather or give you a status. Just ask.")

    # ------------------------------------------------------------------ chat
    def system_prompt(self) -> str:
        p = self.persona
        return (f"{p.style}\nYou are the copilot in the right seat of the pilot's aircraft in Microsoft Flight "
                "Simulator. Answer from the LIVE DATA below, never invent numbers, and if you lack data say so. "
                "Give practical advice: checklists, fuel, descent planning, approach speeds, go-around decisions. "
                "The pilot flying is in command and makes the decisions. Reply in one or two short spoken sentences, no "
                "markdown, no lists.\nLIVE DATA: " + json.dumps(self.context()))

    def ask(self, text: str, add_user: bool = True) -> str:
        """Answer the pilot. Never raises: falls back to the deterministic answers if the model is unavailable."""
        text = text.strip()
        if not text:
            return ""
        with self._lock:
            if add_user:
                self.db.add_message("user", text, THREAD)
            history = [{"role": r["role"], "content": r["content"]} for r in self.db.messages(8, THREAD)
                       if r["role"] in ("user", "assistant")]
            try:
                reply = self.client.chat([{"role": "system", "content": self.system_prompt()}] + history,
                                         max_tokens=140, temperature=0.4).text.strip()
                reply = re.sub(r"[*_`#]+", "", reply)
            except LLMError as exc:
                log.info("Copilot LLM unavailable (%s); answering from onboard data", exc)
                reply = ""
            reply = reply or self.offline_answer(text)
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

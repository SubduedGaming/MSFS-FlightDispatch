"""JSON API behind the browser remote. Every handler runs on the GUI thread and mirrors what a desktop page does.

``RemoteApi.handle(method, path, query, body)`` returns ``(status, payload)`` and never raises, so it can be tested
without any HTTP.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from typing import Any, Callable
from urllib.parse import unquote

from .. import APP_NAME, __version__
from ..ai.dispatcher import AVAILABILITY_CHIPS, employer_thread, thread_employer
from ..career import CareerError
from ..copilot.copilot import QUICK_ACTIONS, THREAD as COPILOT_THREAD
from ..copilot.personas import get_copilot
from ..data.aircraft import CATALOG, get_type
from ..data.employers import EMPLOYERS, get_employer
from ..flight.scoring import landing_label
from ..hangar.service import HangarError, airworthiness, inspection_cost, repair_cost, resale_value
from ..jobs.pricing import KIND_LABEL
from ..pilot import quals
from ..sim.base import SimState
from ..planning.loadout import LoadoutError
from ..sim.installed import installed_types
from ..ui import fmt

log = logging.getLogger(__name__)

GENERAL = "general"
VIEWER_RX = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PHASE_LABEL = {"parked": "Waiting for engine start", "taxi_out": "Taxi out", "takeoff": "Takeoff roll",
               "climb": "Climb", "cruise": "Cruise", "descent": "Descent", "landed": "Landed - rollout",
               "taxi_in": "Taxi in", "arrived": "Arrived"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Csv(str):
    """Marker for a text/csv response body."""


Handler = Callable[..., Any]


class RemoteApi:
    def __init__(self, ctx, publish: Callable[[str, dict], None] | None = None,
                 post_to_main: Callable[[Callable[[], Any]], None] | None = None):
        self.ctx = ctx
        self.db = ctx.db
        self.career = ctx.career
        self.settings = ctx.settings
        self._publish = publish or (lambda *_: None)
        self._post = post_to_main or (lambda fn: fn())
        self._routes: list[tuple[str, re.Pattern, Handler]] = []
        self._flight_events: list[dict] = []
        self._flight_key: Any = None
        self._last_state_push = 0.0
        self._listening: str | None = None
        self._register()
        self._wire()

    # ------------------------------------------------------------------ routing
    def route(self, method: str, pattern: str):
        rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn: Handler) -> Handler:
            self._routes.append((method, rx, fn))
            return fn
        return deco

    def handle(self, method: str, path: str, query: dict[str, str] | None = None,
               body: dict | None = None) -> tuple[int, Any]:
        query, body = query or {}, body or {}
        known_path = False
        for m, rx, fn in self._routes:
            match = rx.match(path)
            if not match:
                continue
            known_path = True
            if m != method:
                continue
            try:
                return 200, fn(**{k: unquote(v) for k, v in match.groupdict().items()}, q=query, body=body)
            except ApiError as exc:
                return exc.status, {"error": exc.message}
            except (CareerError, HangarError) as exc:
                return 400, {"error": str(exc)}
            except Exception as exc:
                log.exception("remote API error on %s %s", method, path)
                return 500, {"error": f"Unexpected error: {exc}"}
        return (405, {"error": "Method not allowed"}) if known_path else (404, {"error": "Not found"})

    # ------------------------------------------------------------------ helpers
    def _pilot(self):
        p = self.db.pilot()
        if not p:
            raise ApiError(409, "No career yet. Finish the setup on the Windows PC first.")
        return p

    def _job_json(self, j) -> dict:
        s = self.settings
        o, d = self.db.airport(j.origin), self.db.airport(j.dest)
        return {"id": j.id, "kind": j.kind, "kind_label": KIND_LABEL.get(j.kind, j.kind), "title": j.title,
                "origin": j.origin, "dest": j.dest, "origin_name": o.name if o else j.origin,
                "dest_name": d.name if d else j.dest, "distance": fmt.dist(s, j.distance_nm),
                "load": f"{j.pax} pax" if j.pax else fmt.weight(s, j.cargo_lb), "payout": fmt.money(s, j.payout),
                "payout_raw": j.payout, "deadline": fmt.duration(j.deadline_minutes), "status": j.status,
                "client": j.client, "briefing": j.briefing, "expires": fmt.when(j.expires_at),
                "min_reputation": j.min_reputation, "employer_id": j.employer_id,
                "dest_info": f"runway {d.runway_ft:,} ft, elev {d.elevation_ft:,.0f} ft" if d else "",
                "provided_type": get_type(j.provided_type).name if j.provided_type and get_type(j.provided_type) else ""}

    def _airport_pt(self, icao: str | None):
        a = self.db.airport(icao) if icao else None
        return {"icao": a.icao, "lat": a.lat, "lon": a.lon} if a else None

    def _route_json(self, dep: str | None, arr: str | None) -> dict:
        return {"from": self._airport_pt(dep), "to": self._airport_pt(arr)}

    def _tone_money(self, amount: float) -> str | None:
        return "bad" if amount < 0 else None

    # ------------------------------------------------------------------ live events
    def _wire(self) -> None:
        ctx = self.ctx
        ctx.sim_state.connect(self._on_sim_state)
        ctx.sim_status.connect(lambda st, msg: self._publish("sim_status", {"status": st, "message": msg}))
        ctx.ai_status.connect(lambda ok, msg: self._publish("ai_status", {"ok": bool(ok), "message": msg}))
        ctx.toast.connect(lambda level, msg: self._publish("toast", {"level": level, "message": msg}))
        ctx.thread_changed.connect(lambda t: self._publish("thread", {"thread": t}))
        ctx.chat_busy.connect(lambda t, b: self._publish("busy", {"thread": t, "busy": bool(b)}))
        ctx.settings_changed.connect(lambda: self._publish("settings", {}))
        ctx.career_event.connect(self._on_career_event)
        ctx.plan_changed.connect(lambda: self._publish("plan", {}))

    def _on_career_event(self, name: str, payload: dict) -> None:
        data: dict[str, Any] = {"name": name}
        if name in ("job_accepted", "job_abandoned", "career_started", "career_reset"):
            self._flight_events.clear()
        if name == "flight_event":
            ev = {"kind": payload.get("kind", ""), "detail": payload.get("detail", "")}
            self._flight_events.append(ev)
            data.update(ev)
        elif name == "flight_finished":
            s = payload.get("settlement")
            if s is not None:
                text = f"Result: {s.metrics.outcome}, score {s.score.score:.0f} ({s.score.grade})"
                self._flight_events.append({"kind": "result", "detail": text})
                data.update({"outcome": s.metrics.outcome, "score": round(s.score.score), "grade": s.score.grade,
                             "payout": fmt.money(self.settings, s.payout), "arrival": s.arrival})
        self._publish("career", data)

    def _on_sim_state(self, s: SimState) -> None:
        now = time.monotonic()
        if now - self._last_state_push < 0.45:
            return
        self._last_state_push = now
        self._publish("state", self._state_json(s))

    def _state_json(self, s: SimState | None) -> dict:
        live = self.career.live()
        out: dict[str, Any] = {"phase": PHASE_LABEL.get(live.phase if live else "parked", ""), "live": False}
        if s is not None:
            out.update({"live": True, "lat": s.lat, "lon": s.lon, "heading": s.heading, "cells": {
                "alt": f"{s.alt_msl:,.0f} ft", "ias": f"{s.ias:.0f} kt", "gs": f"{s.gs:.0f} kt",
                "vs": f"{s.vs:+,.0f} fpm", "hdg": f"{s.heading:03.0f}°", "fuel": f"{s.fuel_gal:.1f} gal",
                "g": f"{s.g_force:.2f} G"}})
        if live:
            st = self.settings
            eta = f"ETA {fmt.duration(live.eta_min)}  |  " if live.eta_min else ""
            rem = f"{fmt.dist(st, live.remaining_nm)} to go  |  " if live.remaining_nm is not None else ""
            out["progress"] = live.progress
            out["eta"] = f"{eta}{rem}block time {fmt.duration(live.elapsed_min)}  |  max {live.max_g:.2f} G"
            out.setdefault("cells", {})["dist"] = fmt.dist(st, live.distance_nm)
        return out

    # ------------------------------------------------------------------ routes
    def _register(self) -> None:
        r = self.route

        @r("GET", "/api/state")
        def state(q, body):
            p = self.db.pilot()
            prov = self.ctx.provider
            u = self.ctx.update_info
            return {"app": APP_NAME, "version": __version__, "has_career": p is not None,
                    "pilot": {"name": p.name, "rank": p.rank, "balance": fmt.money(self.settings, p.balance),
                              "callsign": p.callsign} if p else None,
                    "sim": {"status": prov.status if prov else "disconnected", "mode": self.settings.sim.mode},
                    "ai_online": self.ctx.dispatcher.online, "has_job": self.db.active_job() is not None,
                    "update": {"version": u.version, "url": u.page_url} if u else None}

        @r("GET", "/api/dashboard")
        def dashboard(q, body):
            s, pilot = self.settings, self._pilot()
            qual = self.career.qualifications()
            days = qual.days_since_last
            job = self.db.active_job()
            flights = []
            for f in self.db.flights(8):
                ac = self.db.aircraft(f.aircraft_id) if f.aircraft_id else None
                flights.append({"date": fmt.when(f.started_at), "route": f"{f.dep or '?'} - {f.arr or '?'}",
                                "aircraft": ac.registration if ac else (f.sim_title[:28] or "-"),
                                "landing": "-" if f.landing_fpm is None else f"{abs(f.landing_fpm):.0f} fpm",
                                "score": f"{f.score:.0f}" if f.outcome != "in_progress" else "-",
                                "net": fmt.money(s, f.payout - f.costs, signed=True),
                                "net_tone": "bad" if f.payout - f.costs < 0 else "good"})
            prov = self.ctx.provider
            tts_ok, _ = self.ctx.voice.tts_status()
            stt_ok, _ = self.ctx.voice.stt_status()
            online = self.ctx.dispatcher.online
            return {
                "hello": f"Welcome back, {pilot.rank} {pilot.name}",
                "sub": f"Callsign {pilot.callsign}  |  Home base {pilot.home_icao}",
                "tiles": [
                    {"label": "Balance", "value": fmt.money(s, pilot.balance), "tone": self._tone_money(pilot.balance)},
                    {"label": "Reputation", "value": f"{pilot.reputation:.0f} / 100",
                     "tone": "good" if pilot.reputation >= 70 else "warn" if pilot.reputation < 40 else None},
                    {"label": "Flight time", "value": fmt.duration(pilot.total_minutes)},
                    {"label": "Recent experience", "value": f"{qual.recent_h:.1f} h",
                     "tone": "warn" if qual.total_h > 5 and qual.recent_h < 2 else None,
                     "hint": "No flights yet." if days is None else f"Last flight: {days:.0f} days ago."},
                    {"label": "Skill", "value": pilot.skill_level, "hint": f"Skill rating {pilot.skill:.0f}/100"},
                    {"label": "Fleet", "value": str(len(self.db.hangar()))}],
                "job": self._job_card(job),
                "systems": {"sim": f"{s.sim.mode}: {prov.status if prov else 'disconnected'}",
                            "ai": "online" if online else "offline" if online is False else "not checked yet",
                            "voice": f"speech out {'ready' if tts_ok else 'unavailable'}, "
                                     f"speech in {'ready' if stt_ok else 'unavailable'}"},
                "flights": flights}

        @r("POST", "/api/job/abandon")
        def abandon(q, body):
            self.career.abandon_job()
            return {"ok": True}

        # ------------------------------------------------------------ flight
        @r("GET", "/api/flight")
        def flight(q, body):
            job = self.db.active_job()
            key = job.id if job else None
            if key != self._flight_key:
                self._flight_key = key
                self._flight_events = [] if key is not None else self._flight_events
            prov = self.ctx.provider
            latest = prov.latest() if prov else None
            data = self._state_json(latest)
            data.update({"job": self._job_card(job), "has_job": job is not None,
                         "route": self._route_json(job.origin if job else None, job.dest if job else None),
                         "events": self._flight_events[-60:], "can_demo": self.ctx.simulated is not None,
                         "link": {"status": prov.status if prov else "disconnected"},
                         "copilot": self._copilot_json()})
            return data

        @r("POST", "/api/flight/demo")
        def demo(q, body):
            err = self.ctx.demo_fly_active_job()
            if err:
                raise ApiError(400, err)
            return {"ok": True}

        # ------------------------------------------------------------ flight plan and loadout
        @r("GET", "/api/plan")
        def plan(q, body):
            return self.ctx.plan_summary()

        @r("GET", "/api/plan/link")
        def plan_link(q, body):
            try:
                return {"url": self.ctx.simbrief_link()}           # the browser on the remote opens SimBrief itself
            except LoadoutError as exc:
                raise ApiError(400, str(exc))

        @r("POST", "/api/plan/import")
        def plan_import(q, body):
            self.ctx.import_simbrief()
            return {"ok": True}

        @r("POST", "/api/plan/sync")
        def plan_sync(q, body):
            self.ctx.sync_loadout()
            return {"ok": True}

        @r("POST", "/api/plan/refuel")
        def plan_refuel(q, body):
            self.ctx.refuel_to_plan()
            return {"ok": True}

        # ------------------------------------------------------------ copilot
        @r("GET", "/api/copilot")
        def copilot(q, body):
            return self._copilot_json()

        @r("POST", "/api/copilot/ask")
        def copilot_ask(q, body):
            if body.get("quick"):
                self.ctx.ask_copilot(quick=str(body["quick"]))
            else:
                text = str(body.get("text", "")).strip()
                if not text:
                    raise ApiError(400, "Type a question first.")
                self.ctx.voice.shut_up()
                self.ctx.ask_copilot(text=text)
            return {"ok": True}

        @r("POST", "/api/copilot/callouts")
        def callouts(q, body):
            self.settings.ai.copilot_callouts = bool(body.get("on"))
            self.settings.save()
            return {"ok": True}

        # ------------------------------------------------------------ which conversation this browser is showing
        @r("POST", "/api/view")
        def view(q, body):
            viewer, thread = str(body.get("viewer", "")), body.get("thread")
            if not VIEWER_RX.match(viewer):
                raise ApiError(400, "Bad viewer id")
            if thread is not None and not self._valid_thread(str(thread)):
                raise ApiError(400, "Unknown conversation")
            self.ctx.set_viewing("remote:" + viewer, str(thread) if thread else None)
            return {"ok": True}

        # ------------------------------------------------------------ voice (the PC's microphone and speakers)
        @r("POST", "/api/voice/start")
        def voice_start(q, body):
            if not self.ctx.voice.start_listening():
                raise ApiError(400, "Could not open the microphone on the PC. Check Settings > Voice.")
            self._listening = str(body.get("target") or GENERAL)
            return {"ok": True}

        @r("POST", "/api/voice/stop")
        def voice_stop(q, body):
            target, self._listening = self._listening or str(body.get("target") or GENERAL), None

            def heard(text: str) -> None:           # runs on a worker thread
                self._post(lambda: self._heard(text, target))

            def failed(msg: str) -> None:
                self._post(lambda: self.ctx.toast.emit("warn", msg))
            self.ctx.voice.stop_listening(heard, failed)
            return {"ok": True}

        @r("POST", "/api/voice/silence")
        def silence(q, body):
            self.ctx.voice.shut_up()
            return {"ok": True}

        # ------------------------------------------------------------ job board
        @r("GET", "/api/jobboard")
        def jobboard(q, body):
            self._pilot()
            qual = self.career.qualifications()
            cats = ", ".join(f"{k} {v:.0f} h" for k, v in qual.by_category_h.items() if v >= 0.5)
            days = qual.days_since_last
            return {"tiles": [
                {"label": "Total flight time", "value": fmt.duration(qual.total_h * 60)},
                {"label": "Recent experience", "value": f"{qual.recent_h:.1f} h",
                 "tone": "warn" if qual.recent_h < 2 and qual.total_h > 5 else None},
                {"label": "Skill level", "value": f"{qual.skill_level} ({qual.skill:.0f})"},
                {"label": "Since last flight", "value": "no flights yet" if days is None else
                 "today" if days < 1 else f"{days:.0f} days"}],
                "classes": f"Hours by aircraft class: {cats}" if cats else "No hours logged on any aircraft class yet.",
                "employers": [self._employer_json(e, qual, detail=False) for e in EMPLOYERS],
                "applications": [{"company": (get_employer(a["employer_id"]).name if get_employer(a["employer_id"])
                                              else a["employer_id"]), "result": a["status"].capitalize(),
                                  "date": fmt.when(a["applied_at"]), "details": a["message"]}
                                 for a in self.db.applications()]}

        @r("GET", "/api/employer/<eid>")
        def employer(eid, q, body):
            e = get_employer(eid)
            if not e:
                raise ApiError(404, "Unknown company")
            return self._employer_json(e, self.career.qualifications(), detail=True)

        @r("POST", "/api/employer/<eid>/apply")
        def apply(eid, q, body):
            result = self.ctx.apply_to_employer(eid)
            return {"accepted": result.accepted, "message": result.message, "company": result.employer.name,
                    "thread": employer_thread(eid)}

        @r("POST", "/api/employer/<eid>/resign")
        def resign(eid, q, body):
            self.ctx.resign(eid)
            return {"ok": True}

        # ------------------------------------------------------------ messenger
        @r("GET", "/api/threads")
        def threads(q, body):
            self._pilot()
            self._ensure_general()
            ids = [GENERAL] + [employer_thread(e["employer_id"]) for e in self.db.employments()]
            out = []
            for t in ids:
                emp = thread_employer(t) if t != GENERAL else None
                last = self.db.last_message(t)
                preview = "" if not last else "Flight offer" if last["kind"] == "offer" \
                    else last["content"].replace("\n", " ")[:60]
                out.append({"id": t, "name": emp.name if emp else "Operations (freelance)", "preview": preview,
                            "busy": self.ctx._pending.get(t, 0) > 0})
            return {"threads": out}

        @r("GET", "/api/thread/<tid>")
        def thread(tid, q, body):
            self._pilot()
            self._ensure_general()
            return self._thread_json(tid, open_it=q.get("open") == "1")

        @r("POST", "/api/thread/<tid>/send")
        def send(tid, q, body):
            text = str(body.get("text", "")).strip()
            if not text:
                raise ApiError(400, "Type a message first.")
            self.ctx.voice.shut_up()
            self.ctx.ask(text, tid)
            return {"ok": True}

        @r("POST", "/api/thread/<tid>/availability")
        def availability(tid, q, body):
            self.ctx.voice.shut_up()
            self.ctx.answer_availability(tid, int(body.get("minutes", 60)))
            return {"ok": True}

        @r("POST", "/api/thread/<tid>/ask_time")
        def ask_time(tid, q, body):
            self.ctx.dispatcher.ask_availability(tid, "Sure.")
            return {"ok": True}

        @r("POST", "/api/thread/<tid>/clear")
        def clear(tid, q, body):
            self.db.clear_messages(tid)
            if tid == GENERAL:
                self.db.add_message("assistant", self.ctx.dispatcher.greeting(GENERAL), GENERAL)
            else:
                self.db.set_meta(f"await:{tid}", "0")
                self.ctx.open_thread(tid)
            self.ctx.thread_changed.emit(tid)
            return {"ok": True}

        @r("POST", "/api/offer/<jid>/accept")
        def offer_accept(jid, q, body):
            self.ctx.accept_offer(int(jid), str(body.get("thread") or GENERAL))
            if self.db.active_job() is None:
                raise ApiError(400, "That flight could not be accepted.")
            return {"ok": True}

        @r("POST", "/api/offer/<jid>/decline")
        def offer_decline(jid, q, body):
            self.ctx.decline_offer(int(jid), str(body.get("thread") or GENERAL))
            return {"ok": True}

        # ------------------------------------------------------------ freelance market
        @r("GET", "/api/market")
        def market(q, body):
            self._pilot()
            text, kind = (q.get("q") or "").strip().lower(), q.get("kind") or ""
            only_flyable = q.get("flyable") == "1"
            jobs = []
            for j in self.db.jobs("offered"):
                if kind and j.kind != kind:
                    continue
                if text:
                    o, d = self.db.airport(j.origin), self.db.airport(j.dest)
                    hay = f"{j.origin} {j.dest} {o.city if o else ''} {d.city if d else ''} " \
                          f"{o.name if o else ''} {d.name if d else ''}".lower()
                    if text not in hay:
                        continue
                if only_flyable and not any(e.ok for _, e in self.career.eligible_aircraft(j)):
                    continue
                jobs.append(j)
            jobs.sort(key=lambda j: -j.payout)
            return {"jobs": [self._job_json(j) for j in jobs], "kinds": KIND_LABEL,
                    "busy": self.db.active_job() is not None}

        @r("GET", "/api/market/<jid>")
        def market_job(jid, q, body):
            j = self.db.job(int(jid))
            if not j:
                raise ApiError(404, "That contract is gone.")
            planes = []
            for a, e in self.career.eligible_aircraft(j):
                t = get_type(a.type_id)
                planes.append({"id": a.id, "label": f"{a.registration}  {t.name if t else a.type_id}  "
                                                    f"({a.location_icao})" + ("" if e.ok else "   - not eligible"),
                               "ok": e.ok, "why": "; ".join(e.reasons)})
            data = self._job_json(j)
            data.update({"planes": planes, "route": self._route_json(j.origin, j.dest),
                         "active": self.db.active_job() is not None})
            return data

        @r("POST", "/api/market/refresh")
        def market_refresh(q, body):
            self.ctx.refresh_market()
            return {"ok": True}

        @r("POST", "/api/market/<jid>/accept")
        def market_accept(jid, q, body):
            self.career.accept_job(int(jid), body.get("aircraft_id"))
            self.ctx.toast.emit("good", "Job accepted. Start your engines when ready.")
            return {"ok": True}

        @r("POST", "/api/market/<jid>/decline")
        def market_decline(jid, q, body):
            self.career.decline_job(int(jid))
            return {"ok": True}

        @r("POST", "/api/market/<jid>/ask")
        def market_ask(jid, q, body):
            j = self.db.job(int(jid))
            if not j:
                raise ApiError(404, "That contract is gone.")
            self.ctx.ask(f"Tell me about job {j.id}, {j.origin} to {j.dest}. Is it worth taking?", GENERAL)
            return {"ok": True}

        # ------------------------------------------------------------ hangar
        @r("GET", "/api/hangar")
        def hangar(q, body):
            self._pilot()
            s = self.settings
            fleet = []
            for a in self.db.hangar():
                t = get_type(a.type_id)
                ok, why = airworthiness(a, t) if t else (False, "")
                item = {"id": a.id, "registration": a.registration, "nickname": a.nickname,
                        "type": t.name if t else a.type_id, "location": a.location_icao, "airworthy": ok,
                        "status": "Airworthy" if ok else why, "hours": f"{a.hours_total:.1f} h",
                        "condition": int(a.condition)}
                if t:
                    left = max(0.0, t.maint_hours - a.hours_since_inspection)
                    item.update({
                        "inspection_pct": int(left / t.maint_hours * 100), "inspection_label": f"{left:.0f} h",
                        "fuel_pct": int(a.fuel_gal / t.fuel_cap_gal * 100),
                        "fuel_label": f"{a.fuel_gal:.0f} / {t.fuel_cap_gal:.0f} gal",
                        "specs": f"Cruise {t.cruise_kts} kt  |  Range {fmt.dist(s, t.range_nm)}  |  Seats {t.pax}  |  "
                                 f"Cargo {fmt.weight(s, t.cargo_lb)}",
                        "value": fmt.money(s, resale_value(a, t)),
                        "inspect_cost": fmt.money(s, inspection_cost(t)),
                        "repair_cost": fmt.money(s, repair_cost(a, t)), "can_repair": repair_cost(a, t) > 0,
                        "can_refuel": a.fuel_gal < t.fuel_cap_gal - 0.5})
                fleet.append(item)
            flown = []
            for row in self.db.aircraft_flown():
                t = get_type(row["type_id"]) if row["type_id"] else None
                flown.append({"title": row["sim_title"], "match": t.name if t else "Not in catalog",
                              "flights": row["flights"], "time": fmt.duration(row["minutes"]),
                              "last": fmt.when(row["last_flown"])})
            return {"fleet": fleet, "flown": flown}

        @r("POST", "/api/hangar/<aid>/<action>")
        def hangar_action(aid, action, q, body):
            a = self.db.aircraft(int(aid))
            if not a:
                raise ApiError(404, "Aircraft not found")
            h = self.career.hangar
            if action in ("refuel", "inspect", "repair"):
                cost = getattr(h, action)(a.id)
                self.ctx.toast.emit("info", f"{action.title()} done for {fmt.money(self.settings, cost)}")
            elif action == "rename":
                h.rename(a.id, str(body.get("nickname", "")))
            elif action == "sell":
                h.sell(a.id)
            else:
                raise ApiError(404, "Unknown action")
            self.career._fire("hangar_changed")
            if action != "rename":
                self.career._fire("pilot_changed")
            return {"ok": True}

        @r("GET", "/api/dealer")
        def dealer(q, body):
            s = self.settings
            installed = installed_types(s, self.db)
            pilot = self._pilot()
            return {"restricted": installed is not None, "home": pilot.home_icao,
                    "aircraft": [{"id": t.id, "name": t.name, "category": t.category, "seats": t.pax,
                                  "cargo": fmt.weight(s, t.cargo_lb), "range": fmt.dist(s, t.range_nm),
                                  "cruise": f"{t.cruise_kts} kt", "price": fmt.money(s, t.price),
                                  "used_price": fmt.money(s, round(t.price * 0.7, -2))}
                                 for t in CATALOG if installed is None or t.id in installed]}

        @r("POST", "/api/hangar/buy")
        def buy(q, body):
            t = get_type(str(body.get("type_id", "")))
            if not t:
                raise ApiError(400, "Choose an aircraft first.")
            a = self.career.hangar.buy(t.id, str(body.get("location", "")).strip().upper(),
                                       str(body.get("nickname", "")), bool(body.get("used")))
            self.career._fire("hangar_changed")
            self.career._fire("pilot_changed")
            self.ctx.toast.emit("good", f"Purchased {t.name} ({a.registration})")
            return {"ok": True, "registration": a.registration}

        # ------------------------------------------------------------ logbook & finance
        @r("GET", "/api/logbook")
        def logbook(q, body):
            self._pilot()
            s = self.settings
            flights = self.db.flights()
            rows = []
            for f in flights:
                ac = self.db.aircraft(f.aircraft_id) if f.aircraft_id else None
                rows.append({"id": f.id, "date": fmt.when(f.started_at), "route": f"{f.dep or '?'} - {f.arr or '?'}",
                             "aircraft": ac.registration if ac else (f.sim_title[:26] or "-"),
                             "air": fmt.duration(f.air_min), "distance": fmt.dist(s, f.distance_nm),
                             "landing": "-" if f.landing_fpm is None else
                             f"{abs(f.landing_fpm):.0f} fpm ({landing_label(f.landing_fpm)})",
                             "score": f"{f.score:.0f}" if f.outcome != "in_progress" else "-",
                             "result": f.outcome.replace("_", " "),
                             "net": fmt.money(s, f.payout - f.costs, signed=True),
                             "net_tone": "bad" if f.payout - f.costs < 0 else "good"})
            return {"flights": rows, "totals": f"{len(flights)} flights  |  "
                    f"{fmt.duration(sum(f.air_min for f in flights))}  |  "
                    f"{fmt.dist(s, sum(f.distance_nm for f in flights))}"}

        @r("GET", "/api/logbook/<fid>")
        def logbook_flight(fid, q, body):
            f = self.db.flight(int(fid))
            if not f:
                raise ApiError(404, "Flight not found")
            s = self.settings
            job = self.db.job(f.job_id) if f.job_id else None
            tel = self.db.telemetry(f.id)
            step = max(1, len(tel) // 300)
            sampled = tel[::step]
            return {
                "title": f"{f.dep or '?'} to {f.arr or '?'}  -  {f.outcome.replace('_', ' ')}",
                "contract": job.title if job else "Free flight (no contract)", "sim_title": f.sim_title or "-",
                "stats": [f"Block {fmt.duration(f.block_min)}", f"Air {fmt.duration(f.air_min)}",
                          fmt.dist(s, f.distance_nm), f"fuel {f.fuel_used_gal:.1f} gal",
                          f"max altitude {f.max_alt_ft:,.0f} ft", f"max IAS {f.max_ias:.0f} kt",
                          f"max G {f.max_g:.2f}", f"overspeed {f.overspeed_s:.0f}s"],
                "money": f"Score {f.score:.0f}  |  Payout {fmt.money(s, f.payout)}  |  Costs {fmt.money(s, f.costs)}",
                "debrief": f.debrief,
                "events": [f"{e['kind'].replace('_', ' ')}: {e['detail']}" for e in self.db.events(f.id)],
                "route": self._route_json(f.dep, f.arr),
                "track": [[r_["lat"], r_["lon"]] for r_ in sampled if r_["lat"] is not None],
                "profile": [[r_["t"], r_["alt_ft"] or 0.0, r_["gs"] or 0.0] for r_ in sampled]}

        @r("GET", "/api/logbook.csv")
        def logbook_csv(q, body):
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "started", "dep", "arr", "aircraft", "air_min", "distance_nm", "fuel_gal",
                        "landing_fpm", "max_g", "score", "outcome", "payout", "costs"])
            for f in self.db.flights():
                w.writerow([f.id, f.started_at, f.dep, f.arr, f.sim_title, round(f.air_min, 1),
                            round(f.distance_nm, 1), round(f.fuel_used_gal, 1), f.landing_fpm, round(f.max_g, 2),
                            f.score, f.outcome, f.payout, f.costs])
            return Csv(buf.getvalue())

        @r("GET", "/api/finance")
        def finance(q, body):
            s = self.settings
            rows = self.db.transactions(500)
            income = sum(x["amount"] for x in rows if x["category"] == "job")
            spend = sum(-x["amount"] for x in rows if x["amount"] < 0)
            net = sum(x["amount"] for x in rows if x["category"] != "career")
            pilot = self.db.pilot()
            return {"tiles": [
                {"label": "Balance", "value": fmt.money(s, pilot.balance) if pilot else "-"},
                {"label": "Job income", "value": fmt.money(s, income), "tone": "good"},
                {"label": "Expenses", "value": fmt.money(s, spend), "tone": "warn"},
                {"label": "Net profit", "value": fmt.money(s, net, signed=True), "tone": "good" if net >= 0 else "bad"}],
                "rows": [{"date": fmt.when(x["ts"]), "category": x["category"], "description": x["description"],
                          "amount": fmt.money(s, x["amount"], signed=True), "tone": "bad" if x["amount"] < 0 else "good",
                          "balance": fmt.money(s, x["balance_after"])} for x in rows]}

        # ------------------------------------------------------------ settings (the safe subset)
        @r("GET", "/api/settings")
        def get_settings(q, body):
            s, u = self.settings, self.ctx.update_info
            tts_ok, tts_msg = self.ctx.voice.tts_status()
            stt_ok, stt_msg = self.ctx.voice.stt_status()
            return {"auto_speak_replies": s.voice.auto_speak_replies, "copilot_callouts": s.ai.copilot_callouts,
                    "units_distance": s.ui.units_distance, "units_weight": s.ui.units_weight,
                    "voice": {"speech_out": tts_ok, "speech_in": stt_ok and s.voice.stt_enabled,
                              "note": "" if stt_ok else stt_msg},
                    "auto_sync_loadout": s.plan.auto_sync_loadout, "simbrief_user": s.plan.simbrief_user,
                    "version": __version__, "update": {"version": u.version, "url": u.page_url} if u else None,
                    "note": "Everything else (simulator, AI server, voice devices, folders) is set on the Windows PC."}

        @r("POST", "/api/settings")
        def set_settings(q, body):
            s = self.settings
            if "auto_speak_replies" in body:
                s.voice.auto_speak_replies = bool(body["auto_speak_replies"])
                if not s.voice.auto_speak_replies:
                    self.ctx.voice.shut_up()
            if "copilot_callouts" in body:
                s.ai.copilot_callouts = bool(body["copilot_callouts"])
            if "auto_sync_loadout" in body:
                s.plan.auto_sync_loadout = bool(body["auto_sync_loadout"])
            if "simbrief_user" in body:
                s.plan.simbrief_user = str(body["simbrief_user"]).strip()[:64]
            if body.get("units_distance") in ("nm", "km"):
                s.ui.units_distance = body["units_distance"]
            if body.get("units_weight") in ("lb", "kg"):
                s.ui.units_weight = body["units_weight"]
            self.ctx.apply_settings()
            return {"ok": True}

    # ------------------------------------------------------------------ builders
    def _job_card(self, job) -> dict | None:
        if not job:
            return None
        s = self.settings
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        t = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
        who = job.client if job.employer_id else (aircraft.registration if aircraft else "no aircraft")
        plane = (f"{t.name} (company aircraft)" if job.employer_id and t else
                 f"{t.name} {aircraft.registration}" if t and aircraft else "")
        return {"id": job.id, "title": job.title, "origin": job.origin, "dest": job.dest,
                "info": f"{job.origin} to {job.dest}  |  {fmt.dist(s, job.distance_nm)}  |  "
                        f"pays {fmt.money(s, job.payout)}  |  {who}  |  {job.status}",
                "line": f"{job.origin} to {job.dest}  |  pays {fmt.money(s, job.payout)}  |  {plane}"}

    def _employer_json(self, e, qual, detail: bool) -> dict:
        employed = self.db.is_employed_by(e.id)
        note = self.career.employer_fleet_note(e)
        meets = quals.meets(e, qual)
        state, tone = (("Employed", "good") if employed else ("Aircraft not installed", "muted") if note
                       else ("You qualify", "accent") if meets else ("Not yet qualified", "warn"))
        out = {"id": e.id, "name": e.name, "tagline": e.tagline, "state": state, "tone": tone, "employed": employed}
        if not detail:
            return out
        installed = installed_types(self.settings, self.db)
        emp = self.db.employment(e.id)
        out.update({
            "blurb": e.blurb, "base": e.base, "work": ", ".join(KIND_LABEL[k] for k in e.kinds),
            "fleet": ", ".join(t.name + ("" if installed is None or t.id in installed else " (not installed)")
                               for t in e.fleet_types()),
            "pay": f"{e.pay_factor:.0%} of standard, company covers fuel and running costs",
            "record": (f"{emp['flights']} flights, {fmt.duration(emp['minutes'])}, "
                       f"{fmt.money(self.settings, emp['earned'])} earned") if emp and emp["status"] == "active" else "",
            "checks": [{"label": c.label, "required": c.required, "actual": c.actual, "met": c.met}
                       for c in quals.check_requirements(e.reqs, qual)],
            "note": note, "meets": meets, "thread": employer_thread(e.id)})
        return out

    def _valid_thread(self, thread: str) -> bool:
        return thread in (GENERAL, COPILOT_THREAD) or (thread.startswith("employer:") and thread_employer(thread) is not None)

    def clear_viewer(self, viewer: str) -> None:
        if VIEWER_RX.match(viewer):
            self.ctx.set_viewing("remote:" + viewer, None)

    def _ensure_general(self) -> None:
        if self.db.last_message(GENERAL) is None:
            self.db.add_message("assistant", self.ctx.dispatcher.greeting(GENERAL), GENERAL)

    def _messages_json(self, rows) -> list[dict]:
        out = []
        for r_ in rows:
            item = {"id": r_["id"], "role": r_["role"], "kind": r_["kind"], "content": r_["content"]}
            if r_["kind"] == "offer":
                try:
                    job = self.db.job(json.loads(r_["payload"])["job_id"])
                except (ValueError, KeyError, TypeError):
                    job = None
                if job:
                    t = get_type(job.provided_type) if job.provided_type else None
                    item["offer"] = {"id": job.id, "status": job.status,
                                     "title": f"{KIND_LABEL.get(job.kind, job.kind)}: {job.origin} → {job.dest}",
                                     "aircraft": f"{t.name} (company aircraft)" if t else "",
                                     "deadline": fmt.duration(job.deadline_minutes)}
                else:
                    item["offer"] = None
            out.append(item)
        return out

    def _thread_json(self, tid: str, open_it: bool = False) -> dict:
        d = self.ctx.dispatcher
        employer = thread_employer(tid) if tid != GENERAL else None
        if tid != GENERAL and employer is None:
            raise ApiError(404, "Unknown conversation")
        if open_it:
            self.ctx.open_thread(tid)
        name = d.display_name(tid)
        if employer:
            avail = d.availability(tid)
            sub = (f"{employer.tagline}  |  Based at {employer.base}  |  Pay x{employer.pay_factor:.2f}"
                   + (f"  |  You said you have {avail} min" if avail else ""))
        else:
            sub = "Your own operations desk"
        rows = self.db.messages(120, tid)
        awaiting = bool(employer) and d.is_awaiting_availability(tid)
        last = rows[-1] if rows else None
        active = self.db.active_job() is not None
        return {"id": tid, "name": name, "sub": sub, "messages": self._messages_json(rows),
                "busy": self.ctx._pending.get(tid, 0) > 0, "can_act": not active,
                "chips": [{"minutes": m, "label": lbl} for m, lbl in AVAILABILITY_CHIPS]
                if awaiting and last is not None and last["kind"] == "ask_time" else [],
                "can_ask_time": bool(employer) and not awaiting and not active, "is_employer": bool(employer)}

    def _copilot_json(self) -> dict:
        s = self.settings
        p = get_copilot(s.ai.copilot)
        if self.db.last_message(COPILOT_THREAD) is None:
            self.db.add_message("assistant", p.greeting, COPILOT_THREAD)
        return {"name": p.name, "enabled": s.ai.copilot_enabled, "callouts": s.ai.copilot_callouts,
                "messages": self._messages_json(self.db.messages(60, COPILOT_THREAD)),
                "busy": self.ctx._pending.get(COPILOT_THREAD, 0) > 0,
                "quick": [{"key": k, "label": v} for k, v in QUICK_ACTIONS]}

    def _heard(self, text: str, target: str) -> None:
        if target == "copilot":
            self.ctx.ask_copilot(text=text)
        else:
            self.ctx.ask(text, target)

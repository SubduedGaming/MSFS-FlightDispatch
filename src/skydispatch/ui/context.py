"""AppContext: owns the long-lived services and bridges them to Qt signals."""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from ..ai.dispatcher import EVENT_IMPORTANCE, Dispatcher, employer_thread, thread_employer
from ..ai.llm import LLMError
from ..ai.personas import HR_VOICE, get_persona
from ..career import ApplicationResult, Career, CareerError, Settlement
from ..copilot.copilot import CoPilot
from ..copilot.personas import get_copilot
from ..core.config import Settings
from ..core.paths import database_path
from ..db.database import Database
from ..sim.base import SimProvider, SimState
from ..sim.bridge_server import BridgeHost
from ..sim.factory import make_provider
from ..sim.installed import detect_installed, format_ids, parse_ids, searched_locations
from ..sim.simulated import SimulatedProvider
from ..planning import simbrief
from ..planning.loadout import LoadoutError, compare, plan_loadout, ready_problem
from . import fmt
from .. import __version__, updater
from ..voice import tts as tts_mod
from ..voice.characters import voices_in_use
from ..voice.service import VoiceService
from .workers import run_async

log = logging.getLogger(__name__)


class AppContext(QObject):
    sim_state = Signal(object)
    sim_status = Signal(str, str)
    career_event = Signal(str, dict)
    thread_changed = Signal(str)          # a messenger thread ('general', 'employer:<id>', 'copilot') has new messages
    chat_busy = Signal(str, bool)         # thread, busy
    ai_status = Signal(bool, str)
    toast = Signal(str, str)              # level (info/good/warn/bad), message
    listening = Signal(bool)
    settings_changed = Signal()
    update_available = Signal(object)     # updater.UpdateInfo
    plan_changed = Signal()               # SimBrief plan imported or the sim aircraft was loaded

    def __init__(self, settings: Settings, db: Database):
        super().__init__()
        self.settings = settings
        self.db = db
        self.career = Career(db, settings)
        self.dispatcher = Dispatcher(self.career, settings)
        self.copilot = CoPilot(self.career, settings)
        persona = get_persona(settings.ai.persona)
        self.voice = VoiceService(settings, persona.system_voice_hint, persona.piper_voice)
        self.provider: SimProvider | None = None
        self.sim_connected = False
        self.share_host: BridgeHost | None = None
        self.share_error = ""
        self.web = None                       # web.server.WebRemote, created on first start_web()
        self.web_error = ""
        self.update_info: updater.UpdateInfo | None = None
        self.ofp: simbrief.Ofp | None = None
        try:
            raw = db.get_meta("simbrief_ofp", "")
            self.ofp = simbrief.Ofp.from_json(json.loads(raw)) if raw else None
        except (ValueError, TypeError):
            self.ofp = None
        self._synced_job: int | None = None
        self._auto_problem = ""
        self.viewing: dict[str, str] = {}      # viewer (desktop page or browser remote) -> conversation it is showing
        self.closed = False
        self._pending: dict[str, int] = {}
        # Dispatcher comments, debriefs and follow-up questions must appear in the order things happened.
        self._ai_pool = QThreadPool(self)
        self._ai_pool.setMaxThreadCount(1)
        self.career.subscribe(self._on_career_event)
        self.sim_state.connect(self._feed)
        self.career_event.connect(self._react)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30_000)

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, db_path: Path | None = None) -> "AppContext":
        settings = Settings.load()
        db = Database(db_path or database_path())
        ctx = cls(settings, db)
        ctx.career.resume()
        return ctx

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._timer.stop()
        self.stop_sim()
        self.stop_share()
        self.stop_web()
        QThreadPool.globalInstance().waitForDone(1500)     # let in-flight background tasks finish before the DB closes
        self._ai_pool.waitForDone(1500)
        self.career.flush_telemetry()
        self.voice.shutdown()
        self.settings.save()
        self.db.close()

    def apply_settings(self) -> None:
        """Call after the user edits settings: persist and re-wire services."""
        self.settings.save()
        self.dispatcher.client.cfg = self.settings.ai
        self.copilot.client.cfg = self.settings.ai
        self.dispatcher._prompt_tools = self.settings.ai.tool_mode == "prompt"
        persona = get_persona(self.settings.ai.persona)
        self.voice.tts.voice_hint = persona.system_voice_hint
        self.voice.tts.persona_voice = persona.piper_voice
        self.settings_changed.emit()

    # ------------------------------------------------------------------ sim
    def start_sim(self) -> None:
        self.stop_sim()
        self.provider = make_provider(self.settings.sim)
        self.provider.on_state = self._provider_state
        self.provider.on_status = self._on_status
        self.start_share()
        self.provider.start()
        if self.settings.sim.installed_auto:
            self.detect_installed()

    def stop_sim(self) -> None:
        if self.provider:
            self.provider.on_state = None
            self.provider.on_status = None
            self.provider.stop()
            self.provider = None
        self.sim_connected = False

    def _provider_state(self, state: SimState) -> None:
        host = self.share_host
        if host:
            host.publish_state(state)
        self.sim_state.emit(state)

    # -------------------------------------------------------------- sharing
    def start_share(self) -> None:
        """Let SkyDispatch on other computers connect to this one (MSFS runs here, so this is the host)."""
        self.stop_share()
        sim = self.settings.sim
        if not sim.share_enabled or sim.mode == "bridge":
            return
        if not sim.share_token:
            sim.share_token = secrets.token_urlsafe(12)
            self.settings.save()
        host = BridgeHost("0.0.0.0", sim.share_port, sim.share_token,
                          installed=lambda: sorted(parse_ids(self.settings.sim.installed_aircraft)))
        try:
            host.start()
        except OSError as e:
            self.share_error = f"Could not share on port {sim.share_port}: {e}"
            log.warning(self.share_error)
            self.toast.emit("warn", self.share_error)
            return
        self.share_error = ""
        self.share_host = host

    def stop_share(self) -> None:
        if self.share_host:
            self.share_host.stop()
            self.share_host = None

    # ------------------------------------------------------------------ speech
    def character_voices(self) -> tuple[list[tuple[str, str]], list[str]]:
        """(voices the people you talk to use, those of them not downloaded yet)."""
        needed = voices_in_use(self.settings, [e["employer_id"] for e in self.db.employments()])
        return needed, [v for v, _ in needed if not tts_mod.piper_installed(v)]

    def download_character_voices(self, progress=None, done=None) -> None:
        """Fetch the natural (Piper) voices for everyone you talk to. Runs in the background."""
        _needed, missing = self.character_voices()
        if not missing:
            if done:
                done([])
            return

        def finished(fetched):
            if self.closed:
                return
            self.voice.tts._engine = None                  # pick the neural voices up straight away
            self.toast.emit("good", f"Downloaded {len(fetched)} voice(s). Everyone now sounds like themselves.")
            self.settings_changed.emit()
            if done:
                done(fetched)

        def failed(err: str):
            if not self.closed:
                self.toast.emit("warn", f"Voice download failed: {err}")
                if done:
                    done(None)
        run_async(lambda: tts_mod.download_voices(missing, progress), finished, failed, owner=self)

    def audible(self, thread: str) -> bool:
        """Speech for `thread` is wanted only while someone is looking at that conversation. The copilot also speaks
        during a flight, when the pilot is in the sim and not looking at any window."""
        return thread in self.viewing.values() or (thread == "copilot" and self.copilot.in_flight())

    def _audible_tags(self) -> set[str]:
        tags = set(self.viewing.values())
        if self.copilot.in_flight():
            tags.add("copilot")
        return tags

    def set_viewing(self, viewer: str, thread: str | None) -> None:
        """A page (or the browser remote) now shows `thread`, or nothing. Speech for anything else is cancelled."""
        if thread:
            self.viewing[viewer] = thread
        else:
            self.viewing.pop(viewer, None)
        self.voice.drop_unless(self._audible_tags())

    def speak(self, text: str, thread: str, voice: str = "", speed: float | None = None) -> None:
        """Speak `text` as whoever owns `thread`, if speech is on and that conversation is being looked at."""
        if self.closed or not self.settings.voice.auto_speak_replies or not self.audible(thread):
            return
        who = get_copilot(self.settings.ai.copilot) if thread == "copilot" else self.dispatcher.persona_for(thread)
        self.voice.say(text, voice or who.piper_voice, tag=thread, speed=who.rate if speed is None else speed)

    # ------------------------------------------------- flight plan and loadout
    def _job_context(self):
        """(job, aircraft type, hangar aircraft) for the active contract, or None."""
        from ..data.aircraft import get_type
        job = self.career.active_job or self.db.active_job()
        if job is None:
            return None
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        atype = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
        return (job, atype, aircraft) if atype else None

    def current_ofp(self):
        """The imported SimBrief plan if it is for the active job, else None."""
        jc = self._job_context()
        return self.ofp if jc and self.ofp and self.ofp.matches(jc[0]) else None

    def loadout_plan(self):
        jc = self._job_context()
        return plan_loadout(jc[0], jc[1], jc[2], self.current_ofp()) if jc else None

    def simbrief_link(self) -> str:
        """The SimBrief dispatch page prefilled for the active job."""
        jc = self._job_context()
        if jc is None:
            raise LoadoutError("Accept a job first.")
        job, atype, aircraft = jc
        pilot = self.db.pilot()
        units = "KGS" if self.settings.ui.units_weight == "kg" else "LBS"
        return simbrief.dispatch_url(job, atype, aircraft.registration if aircraft else "",
                                     pilot.callsign if pilot else "", f"sd{job.id}", units)

    def import_simbrief(self) -> None:
        """Fetch the newest SimBrief plan and keep it if it is for the active job."""
        jc = self._job_context()
        if jc is None:
            self.toast.emit("warn", "Accept a job first, then plan it on SimBrief.")
            return
        job, user = jc[0], self.settings.plan.simbrief_user

        def work():
            try:
                ofp = simbrief.fetch_latest(user, f"sd{job.id}")
            except simbrief.SimBriefError:
                ofp = simbrief.fetch_latest(user)              # the plan was made without our prefilled link
            if not ofp.matches(job):
                raise simbrief.SimBriefError(f"Your newest SimBrief plan is {ofp.origin} to {ofp.dest}, not this "
                                             f"flight ({job.origin} to {job.dest}). Generate the plan for this flight first.")
            return ofp

        def done(ofp):
            if self.closed:
                return
            self.ofp = ofp
            self.db.set_meta("simbrief_ofp", json.dumps(ofp.to_json()))
            self._synced_job = None                          # load the aircraft again with the new fuel
            msg = f"SimBrief plan imported: {ofp.origin} to {ofp.dest}, block fuel {ofp.block_fuel_lb:,.0f} lb."
            if ofp.age_hours() > simbrief.MAX_AGE_H:
                msg += " It is more than a day old."
            self.toast.emit("good", msg)
            self.plan_changed.emit()
        run_async(work, done, lambda e: None if self.closed else self.toast.emit("warn", e), owner=self)

    def sync_loadout(self, auto: bool = False) -> None:
        """Put the plan's fuel and payload into the simulator aircraft."""
        jc = self._job_context()
        plan = self.loadout_plan()
        if jc is None or plan is None:
            if not auto:
                self.toast.emit("warn", "Accept a job first.")
            return
        prov = self.provider
        try:
            if prov is None:
                raise LoadoutError("The simulator is not connected.")
            fut = prov.apply_loadout(plan, jc[1])
        except LoadoutError as exc:
            if not auto:
                self.toast.emit("warn", str(exc))
            return
        job_id = jc[0].id

        def done(res):
            if self.closed:
                return
            self._synced_job = job_id
            note = " ".join(res.messages)
            self.toast.emit("good", f"Aircraft loaded: {res.fuel_gal:.0f} gal fuel, {res.payload_lb:,.0f} lb payload. "
                                    f"{note}".strip())
            self.plan_changed.emit()

        def failed(err: str):
            if not self.closed:
                self.toast.emit("warn", f"Could not load the aircraft: {err}")
        run_async(lambda: fut.result(timeout=20), done, failed, owner=self)

    def _maybe_auto_sync(self, state: SimState) -> None:
        """Once per job, as soon as the right aircraft is parked with engines off, load fuel and payload."""
        if not self.settings.plan.auto_sync_loadout or not self.sim_connected:
            return
        job = self.career.active_job
        rec = self.career.recorder
        if job is None or self._synced_job == job.id or (rec is not None and rec.started):
            return
        if not state.on_ground or state.engine_running:
            return
        jc = self._job_context()
        if jc is None:
            return
        problem = ready_problem(state, jc[1])
        if problem:
            if "not the contract's aircraft" in problem and problem != self._auto_problem:
                self._auto_problem = problem                  # say it once, not on every sample
                self.toast.emit("info", problem)
            return
        self._auto_problem = ""
        self._synced_job = job.id                            # one automatic attempt per job; the button can repeat it
        self.sync_loadout(auto=True)

    def plan_summary(self) -> dict:
        """Everything the Flight page (desktop and web remote) shows about the plan and the loadout, as text."""
        jc = self._job_context()
        if jc is None:
            return {"has_job": False}
        job, atype, aircraft = jc
        s, ofp, plan = self.settings, self.current_ofp(), self.loadout_plan()
        status = compare(plan, self.provider.latest() if self.provider else None)
        out: dict = {"has_job": True, "user_set": bool(s.plan.simbrief_user.strip()), "auto": s.plan.auto_sync_loadout,
                     "ofp": None, "sim": None, "can_refuel": False}
        if ofp:
            pdf = ofp.pdf_url if ofp.pdf_url and simbrief.is_trusted_link(ofp.pdf_url) else ""
            out["ofp"] = {"route": f"{ofp.origin} {ofp.route} {ofp.dest}".strip(),
                          "altitude": f"{ofp.cruise_alt_ft:,} ft", "distance": fmt.dist(s, ofp.distance_nm),
                          "ete": fmt.duration(ofp.ete_min), "alternate": ofp.alternate or "-",
                          "block_fuel": f"{ofp.block_fuel_lb:,.0f} lb ({ofp.block_fuel_gal(atype):.0f} gal)",
                          "reserve_fuel": f"{ofp.reserve_fuel_lb:,.0f} lb", "pdf": pdf,
                          "stale": ofp.age_hours() > simbrief.MAX_AGE_H}
            out["can_refuel"] = aircraft is not None and ofp.block_fuel_gal(atype) > aircraft.fuel_gal + 0.5
        label = {"hangar": "the aircraft's fuel in your hangar", "simbrief": "the SimBrief block fuel",
                 "estimate": "an estimate (no SimBrief plan yet)"}[plan.fuel_source]
        out["loadout"] = {"fuel": f"{plan.fuel_gal:.0f} gal", "fuel_source": label,
                          "payload": f"{plan.pax} pax, {fmt.weight(s, plan.cargo_lb)} cargo "
                                     f"({fmt.weight(s, plan.payload_lb)})", "notes": plan.notes}
        if status:
            out["sim"] = {"fuel": f"{status.sim_fuel_gal:.0f} gal", "payload": fmt.weight(s, status.sim_payload_lb),
                          "matches": status.matches, "fuel_ok": status.fuel_ok, "payload_ok": status.payload_ok}
        return out

    def refuel_to_plan(self) -> None:
        """Top the hangar aircraft up to the SimBrief block fuel (paying for it), then it can be loaded."""
        jc = self._job_context()
        ofp = self.current_ofp()
        if jc is None or jc[2] is None or ofp is None:
            self.toast.emit("warn", "Import a SimBrief plan for this flight first.")
            return
        job, atype, aircraft = jc
        need = ofp.block_fuel_gal(atype) - aircraft.fuel_gal
        if need <= 0.5:
            self.toast.emit("info", "The aircraft already has the planned fuel.")
            return
        cost = self.career.hangar.refuel(aircraft.id, need)
        self.career._fire("hangar_changed")
        self.career._fire("pilot_changed")
        self._synced_job = None
        self.toast.emit("info", f"Refuelled {need:.0f} gal for {self.settings.ui.currency}{cost:,.0f}.")
        self.plan_changed.emit()

    # ----------------------------------------------------------- web remote
    def start_web(self) -> None:
        """Serve the browser remote (a Mac/tablet/phone UI) when it is switched on in Settings."""
        self.stop_web()
        r = self.settings.remote
        if not r.enabled:
            return
        if not r.token:
            r.token = secrets.token_urlsafe(9)
            self.settings.save()
        from ..web.server import WebRemote
        if self.web is None:
            self.web = WebRemote(self)
        self.web.host, self.web.port, self.web.token = "0.0.0.0", r.port, r.token
        try:
            self.web.start()
        except OSError as e:
            self.web_error = f"Could not start the browser remote on port {r.port}: {e}"
            log.warning(self.web_error)
            self.toast.emit("warn", self.web_error)
            return
        self.web_error = ""

    def stop_web(self) -> None:
        if self.web is not None and self.web.running:
            self.web.stop()

    # -------------------------------------------------------------- updates
    def check_for_updates(self, manual: bool = False) -> None:
        """Look for a newer GitHub release. Automatic checks stay quiet unless something newer exists."""
        if not manual and (not self.settings.ui.check_updates or os.environ.get("SKYDISPATCH_NO_UPDATE_CHECK")):
            return

        def done(info):
            if self.closed:
                return
            self.update_info = info
            if info:
                self.update_available.emit(info)
                self.settings_changed.emit()
            elif manual:
                self.toast.emit("good", f"You're up to date (version {__version__}).")

        def failed(err: str):
            log.info("update check failed: %s", err)
            if manual and not self.closed:
                self.toast.emit("warn", err if "GitHub" in err else "Could not check for updates. Are you online?")
        run_async(updater.check_for_update, done, failed, owner=self)

    def _on_status(self, status: str, message: str) -> None:
        if self.share_host:
            self.share_host.publish_status(status, message)
        self.sim_connected = status == "connected"
        self.sim_status.emit(status, message)
        if status == "connected" and self.provider and self.provider.installed is not None \
                and self.settings.sim.installed_auto:
            self._store_installed(set(self.provider.installed), "your simulator PC")

    def _feed(self, state: SimState) -> None:
        if self.closed:
            return
        self.career.feed(state)
        self._maybe_auto_sync(state)
        try:
            for c in self.copilot.callouts(state, time.time()):
                self.copilot.record_callout(c.text)
                self.speak(c.text, "copilot")
        except Exception:
            log.exception("copilot callouts failed")

    @property
    def simulated(self) -> SimulatedProvider | None:
        return self.provider if isinstance(self.provider, SimulatedProvider) else None

    # --------------------------------------------------- installed aircraft
    def detect_installed(self, force: bool = False) -> None:
        """Scan this computer for MSFS aircraft (a bridge reports its own list when it connects)."""
        path = self.settings.sim.packages_path
        run_async(lambda: detect_installed(path),
                  lambda found: self._detected(found, force),
                  lambda e: log.warning("aircraft detection failed: %s", e), owner=self)

    def _detected(self, found: set[str] | None, force: bool) -> None:
        if self.closed:
            return
        if found is None:
            if force:
                self.toast.emit("warn", "No MSFS packages folder found. Looked in: "
                                        f"{searched_locations(self.settings.sim.packages_path)}. Set your MSFS "
                                        "packages folder in Settings > Simulator, or choose aircraft manually.")
            return
        if not found and force:
            self.toast.emit("warn", "Found your MSFS folder but no supported aircraft in it. Check that the packages "
                                    "folder is the one containing Community and Official, or choose manually.")
        self._store_installed(found, "this computer")

    def _store_installed(self, found: set[str], where: str) -> None:
        text = format_ids(found)
        if text != self.settings.sim.installed_aircraft:
            self.settings.sim.installed_aircraft = text
            self.settings.save()
            self.toast.emit("info", f"Found {len(found)} supported aircraft installed on {where}.")
            self.settings_changed.emit()
            self.career_event.emit("market_changed", {})

    def demo_fly_active_job(self) -> str | None:
        """In simulated mode: place the aircraft at the departure airport and fly the active job."""
        sim = self.simulated
        job = self.career.active_job or self.db.active_job()
        if sim is None or job is None:
            return "Accept a job first (and use Simulated mode)."
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        o, d = self.db.airport(job.origin), self.db.airport(job.dest)
        from ..data.aircraft import get_type
        t = get_type(job.provided_type) if job.employer_id else (get_type(aircraft.type_id) if aircraft else None)
        if not (o and d and t):
            return "Job data is incomplete."
        fuel = t.fuel_cap_gal if job.employer_id else (aircraft.fuel_gal if aircraft else t.fuel_cap_gal)
        sim.configure_aircraft(f"{t.name} (Simulated)", t.cruise_kts * 0.9, 6500 if t.cruise_kts < 250 else 24000,
                               t.fuel_gph, fuel)
        sim.set_position(o.lat, o.lon, fuel_gal=fuel, elev_ft=o.elevation_ft)
        sim.fly(d.lat, d.lon, d.elevation_ft)
        return None

    # ------------------------------------------------------- career -> UI/AI
    def _on_career_event(self, name: str, payload: dict) -> None:
        if self.closed:
            return
        self.career_event.emit(name, payload)    # may arrive from any thread; Qt queues it

    def _thread_for_job(self, job) -> str:
        return employer_thread(job.employer_id) if job is not None and job.employer_id else "general"

    def _react(self, name: str, payload: dict) -> None:
        if self.closed:
            return
        if name == "thread_changed":
            self.thread_changed.emit(payload.get("thread", "general"))
        elif name == "job_accepted":
            job = payload["job"]
            thread = self._thread_for_job(job)
            self._ai_task(lambda: self.dispatcher.brief_job(job), thread, kind="brief")
        elif name == "flight_event":
            kind = payload["kind"]
            job = payload.get("job")
            thread = self._thread_for_job(job)
            if EVENT_IMPORTANCE.get(kind, 0) >= 1 and self.settings.ai.proactive_comms:
                self._ai_task(lambda: self.dispatcher.react(kind, payload["detail"], payload.get("data"), thread),
                              thread, kind="react", important=EVENT_IMPORTANCE.get(kind, 0) >= 2)
        elif name == "flight_finished":
            s: Settlement = payload["settlement"]
            thread = employer_thread(s.employer_id) if s.employer_id else "general"

            def work():
                text = self.dispatcher.debrief(s)
                self.db.finish_flight(s.flight_id, debrief=text)
                return text

            def after(text):
                if s.employer_id and s.metrics.outcome != "crashed" and self.db.is_employed_by(s.employer_id):
                    self.dispatcher.ask_availability(thread, "Ready for another one?")
            self._ai_task(work, thread, kind="debrief", then=after)
            kind = "good" if s.metrics.outcome == "completed" else "bad"
            self.toast.emit(kind, f"Flight {s.metrics.outcome}: score {s.score.score:.0f}, "
                                  f"payout {self.settings.ui.currency}{s.payout:,.0f}")
        elif name == "hired":
            employer = payload["employer"]
            self.toast.emit("good", f"You're hired at {employer.name}! Check your messenger.")

    def _ai_task(self, fn, thread: str, kind: str, important: bool = True, then=None) -> None:
        def done(text):
            if self.closed:
                return
            if text:
                self.dispatcher.say(text, thread)
                if important or kind != "react":
                    self.speak(text, thread)
            if then:
                then(text)
        run_async(fn, done, lambda e: log.warning("AI task %s failed: %s", kind, e), owner=self, pool=self._ai_pool)

    # ------------------------------------------------------------- employers
    def apply_to_employer(self, employer_id: str) -> ApplicationResult:
        """Apply (instant decision). A hire opens the company's messenger thread in the background."""
        result = self.career.apply_to_employer(employer_id)
        employer = result.employer
        if result.accepted:
            def work():
                hr = self.dispatcher.hr_reply(employer, True, result.message)
                self.dispatcher.start_thread(employer, hr)
                return hr

            def opened(hr):
                if self.closed:
                    return
                thread = employer_thread(employer.id)
                self.thread_changed.emit(thread)
                self.speak(hr, thread, voice=HR_VOICE, speed=1.0)     # HR is a different person from the dispatcher
            run_async(work, opened, lambda e: log.warning("could not open thread: %s", e), owner=self)
        return result

    def resign(self, employer_id: str) -> None:
        self.career.resign(employer_id)

    # ------------------------------------------------------------- messenger
    def ask(self, text: str, thread: str = "general") -> None:
        text = text.strip()
        if not text:
            return
        self.db.add_message("user", text, thread)
        self.thread_changed.emit(thread)
        self._busy(thread, +1)

        def done(_reply):
            self._busy(thread, -1)
            if self.closed:
                return
            self.ai_status.emit(True, "AI dispatcher online")
            last = self.db.last_message(thread)
            if last and last["role"] == "assistant":
                self.speak(last["content"], thread)

        def failed(err: str):
            self._busy(thread, -1)
            if self.closed:
                return
            self.db.add_message("system", err, thread)
            self.thread_changed.emit(thread)
            self.ai_status.emit(False, err)

        run_async(lambda: self.dispatcher.chat(text, thread, add_user=False), done, failed, owner=self)

    def answer_availability(self, thread: str, minutes: int) -> None:
        """Quick-answer chips: tell the dispatcher how long you have."""
        label = f"{minutes} minutes" if minutes < 60 else f"{minutes / 60:g} hours"
        self.db.add_message("user", f"I've got about {label}.", thread)
        self.thread_changed.emit(thread)
        self._busy(thread, +1)

        def done(_r):
            self._busy(thread, -1)
            if self.closed:
                return
            last = self.db.messages(40, thread)
            intro = next((m for m in reversed(last) if m["role"] == "assistant" and m["kind"] == "text"), None)
            if intro:
                self.speak(intro["content"], thread)
        run_async(lambda: self.dispatcher.handle_availability(thread, minutes), done,
                  lambda e: (self._busy(thread, -1), log.warning("availability failed: %s", e)), owner=self)

    def accept_offer(self, job_id: int, thread: str) -> None:
        try:
            self.dispatcher.accept_offer(job_id, thread)
        except CareerError as exc:
            self.toast.emit("bad", str(exc))
            return
        self.career_event.emit("market_changed", {})

    def decline_offer(self, job_id: int, thread: str) -> None:
        try:
            self.dispatcher.decline_offer(job_id, thread)
        except CareerError as exc:
            self.toast.emit("bad", str(exc))

    def open_thread(self, thread: str) -> None:
        """Make sure an employer thread has its opening messages (and the availability question)."""
        employer = thread_employer(thread)
        if employer and self.db.is_employed_by(employer.id) and self.db.last_message(thread) is None:
            self.dispatcher.start_thread(employer)
        elif employer and self.db.is_employed_by(employer.id) and not self.db.active_job() \
                and not self.db.jobs("offered", scope=employer.id) and not self.dispatcher.is_awaiting_availability(thread):
            self.dispatcher.ask_availability(thread)

    def _busy(self, thread: str, delta: int) -> None:
        n = self._pending.get(thread, 0) + delta
        self._pending[thread] = max(0, n)
        self.chat_busy.emit(thread, n > 0)

    # --------------------------------------------------------------- copilot
    def ask_copilot(self, text: str | None = None, quick: str | None = None) -> None:
        label = text or quick or ""
        if not label:
            return
        self._busy("copilot", +1)

        def done(reply):
            self._busy("copilot", -1)
            if self.closed or not reply:
                return
            self.speak(reply, "copilot")

        def failed(err):
            self._busy("copilot", -1)
            log.warning("copilot failed: %s", err)

        if quick:
            run_async(lambda: self.copilot.say_quick(quick), done, failed, owner=self)
        else:
            self.db.add_message("user", text, "copilot")
            self.thread_changed.emit("copilot")
            run_async(lambda: self.copilot.ask(text, add_user=False), done, failed, owner=self)

    def refresh_market(self) -> None:
        def work():
            n = self.career.refresh_market()
            try:
                self.dispatcher.enhance_jobs(self.db.jobs("offered")[:8])   # newest first
            except LLMError:
                pass
            return n
        run_async(work, lambda n: None if self.closed else (
                      self.toast.emit("info", f"{n} new contract(s) on the board"),
                      self.career_event.emit("market_changed", {})),
                  lambda e: None if self.closed else self.toast.emit("bad", f"Could not refresh: {e}"), owner=self)

    def check_ai(self) -> None:
        run_async(self.dispatcher.test_connection,
                  lambda r: None if self.closed else self.ai_status.emit(bool(r[0]), r[1]),
                  lambda e: None if self.closed else self.ai_status.emit(False, e), owner=self)

    # ---------------------------------------------------------------- timers
    def _tick(self) -> None:
        if self.closed:
            return
        try:
            self.career.flush_telemetry()
            self.db.expire_jobs()
            if self.db.pilot() and len(self.db.jobs("offered")) < max(3, self.settings.game.job_count // 3):
                self.career.refresh_market()
        except Exception:
            log.exception("periodic tick failed")

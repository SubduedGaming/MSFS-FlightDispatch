"""AppContext: owns the long-lived services and bridges them to Qt signals."""
from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from ..ai.dispatcher import EVENT_IMPORTANCE, Dispatcher, employer_thread, thread_employer
from ..ai.llm import LLMError
from ..ai.personas import get_persona
from ..career import ApplicationResult, Career, CareerError, Settlement
from ..copilot.copilot import CoPilot
from ..copilot.personas import get_copilot
from ..core.config import Settings
from ..core.paths import database_path
from ..db.database import Database
from ..sim.base import SimProvider, SimState
from ..sim.bridge_server import BridgeHost
from ..sim.factory import make_provider
from ..sim.installed import detect_installed, format_ids, parse_ids
from ..sim.simulated import SimulatedProvider
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
        try:
            for c in self.copilot.callouts(state, time.time()):
                self.copilot.record_callout(c.text)
                if self.settings.voice.auto_speak_replies:
                    self.voice.say(c.text, get_copilot(self.settings.ai.copilot).piper_voice)
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
                self.toast.emit("warn", "No MSFS installation found on this computer. Choose your aircraft manually, "
                                        "or connect to the Bridge on your simulator PC.")
            return
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
                if self.settings.voice.auto_speak_replies and (important or kind != "react"):
                    self.voice.say(text, self.dispatcher.persona_for(thread).piper_voice)
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
                return self.dispatcher.start_thread(employer, hr)
            run_async(work, lambda _t: None if self.closed else self.thread_changed.emit(employer_thread(employer.id)),
                      lambda e: log.warning("could not open thread: %s", e), owner=self)
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
            if last and last["role"] == "assistant" and self.settings.voice.auto_speak_replies:
                self.voice.say(last["content"], self.dispatcher.persona_for(thread).piper_voice)

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
            if intro and self.settings.voice.auto_speak_replies:
                self.voice.say(intro["content"], self.dispatcher.persona_for(thread).piper_voice)
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
            if self.settings.voice.auto_speak_replies:
                self.voice.say(reply, get_copilot(self.settings.ai.copilot).piper_voice)

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

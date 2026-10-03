"""The AI dispatcher: conversation threads, availability questions, flight offers, briefings and debriefs.

Threads: ``general`` (your own operations desk) and ``employer:<id>`` (the dispatcher of a company that hired you).
Everything the dispatcher says is stored in the database, and the GUI renders from there.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ..career import Career
from ..core.config import Settings
from ..data.employers import Employer, get_employer
from ..db.models import Job
from ..jobs.employer_jobs import NoFlightsAvailable, parse_duration
from . import briefing as B
from .llm import LLMError, LMStudioClient, ToolCall
from .personas import Persona, get_persona
from .tools import ToolBox, schemas_for

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_MESSAGES = 16
AVAILABILITY_CHIPS = [(30, "30 min"), (60, "1 hour"), (120, "2 hours"), (180, "3 hours"), (300, "4+ hours")]

# Events the dispatcher comments on proactively -> importance (only >= threshold are voiced by default).
EVENT_IMPORTANCE = {"start": 1, "takeoff": 1, "landing": 2, "overspeed": 2, "low_fuel": 3, "fuel_exhausted": 3,
                    "bounce": 1, "slew": 2, "wrong_aircraft": 2, "wrong_origin": 2, "arrived": 1, "crash": 3}

TOOL_PROTOCOL = """\
You can call tools. To call one, reply with ONLY a block like this and nothing else:
<tool_call>{"name": "list_jobs", "arguments": {"limit": 5}}</tool_call>
You will receive the result, then continue. Available tools:
%s
When you have what you need, answer the pilot normally (no tool_call block)."""

_TOOL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def employer_thread(employer_id: str) -> str:
    return f"employer:{employer_id}"


def thread_employer(thread: str) -> Employer | None:
    return get_employer(thread.split(":", 1)[1]) if thread.startswith("employer:") else None


class Dispatcher:
    def __init__(self, career: Career, settings: Settings, client: LMStudioClient | None = None):
        self.career = career
        self.db = career.db
        self.settings = settings
        self.client = client or LMStudioClient(settings.ai)
        self.tools = ToolBox(career)
        self.tools.availability_cb = self._tool_availability
        self._prompt_tools = settings.ai.tool_mode == "prompt"
        self._lock = threading.RLock()   # one LLM conversation at a time (re-entrant: chat() may call one-shots)
        self.online: bool | None = None

    # ------------------------------------------------------------- personas
    @property
    def persona(self) -> Persona:
        return get_persona(self.settings.ai.persona)

    def persona_for(self, thread: str = "general") -> Persona:
        e = thread_employer(thread)
        return get_persona(e.persona) if e else self.persona

    def display_name(self, thread: str = "general") -> str:
        e = thread_employer(thread)
        p = self.persona_for(thread)
        return f"{p.name} - {e.name}" if e else f"{p.name} - Operations"

    def system_prompt(self, thread: str = "general", with_tool_protocol: bool = False) -> str:
        p = self.db.pilot()
        persona, employer = self.persona_for(thread), thread_employer(thread)
        facts = self.tools.t_get_status()
        hangar = self.tools.t_list_hangar()["aircraft"]
        home_fleet = "; ".join(f"id {a['id']} {a['registration']} {a['type']} at {a['at']} "
                               f"({a['condition_pct']}% cond, {a['fuel_gal']} gal)" for a in hangar) or "none"
        prompt = (
            f"{persona.style}\n"
            f"You are a flight dispatcher in a career-mode add-on for Microsoft Flight Simulator. You are "
            f"talking to {p.name if p else 'the pilot'} (callsign {p.callsign if p else '?'}) in a chat messenger "
            "and by voice. Speak in short, natural sentences: 1 to 4 sentences normally, as if talking aloud. No "
            "markdown, no bullet lists, no emojis, no stage directions.\n"
            "RULES: Never invent jobs, aircraft, prices or weather; use your tools to look things up. "
            "Money-spending or irreversible actions (buying/selling aircraft, abandoning a job) require the pilot's "
            "explicit confirmation first, then call the tool with confirmed=true. If a tool returns an error, "
            "explain it simply.\n")
        if employer:
            avail = self.db.get_meta(f"avail:{thread}")
            prompt += (
                f"You dispatch for {employer.name} ({employer.tagline}). {employer.blurb} Company fleet: "
                f"{', '.join(t.name for t in employer.fleet_types())}. The pilot flies company aircraft, which need no "
                "hangar. YOUR JOB: give the pilot flights that fit the time they have free. "
                + (f"They said they have about {avail} minutes free. " if avail else
                   "You do not yet know how long they have, so ask how long they have free before offering flights. ")
                + "When they tell you a duration, call set_availability, then summarise the offers briefly (the "
                  "offer cards are shown automatically). Never name flights that tools did not return.\n")
        else:
            prompt += ("This is the pilot's own operations desk: freelance contracts, their hangar, and the job board "
                       "of companies they can apply to (list_employers / apply_to_employer).\n")
        prompt += f"CURRENT STATE: {json.dumps(facts)}\nHANGAR: {home_fleet}\n"
        if with_tool_protocol:
            names = "\n".join(f"- {s['function']['name']}: {s['function']['description']} "
                              f"args={list(s['function']['parameters']['properties'])}" for s in schemas_for(thread))
            prompt += TOOL_PROTOCOL % names
        return prompt

    # --------------------------------------------------------------- messages
    def greeting(self, thread: str = "general") -> str:
        return self.persona_for(thread).greeting

    def history(self, thread: str = "general") -> list[dict[str, str]]:
        rows = self.db.messages(HISTORY_MESSAGES, thread)
        out = []
        for r in rows:
            if r["role"] in ("user", "assistant"):
                content = r["content"]
                if r["kind"] == "offer":
                    content = "[Flight offer] " + content
                out.append({"role": r["role"], "content": content})
        return out

    def say(self, text: str, thread: str = "general", kind: str = "text", payload: dict | None = None,
            role: str = "assistant") -> int:
        mid = self.db.add_message(role, text, thread, kind, json.dumps(payload) if payload else "")
        self.career._fire("thread_changed", thread=thread)
        return mid

    # ----------------------------------------------------- availability & offers
    def is_awaiting_availability(self, thread: str) -> bool:
        return self.db.get_meta(f"await:{thread}") == "1"

    def availability(self, thread: str) -> int | None:
        v = self.db.get_meta(f"avail:{thread}")
        return int(v) if v.isdigit() else None

    def ask_availability(self, thread: str, lead_in: str = "") -> None:
        """The dispatcher asks how long the pilot has free (the GUI shows quick-answer chips)."""
        if not thread_employer(thread) or self.is_awaiting_availability(thread):
            return
        for j in self.db.jobs("offered", scope=thread.split(":", 1)[1]):
            self.db.set_job(j.id, status="expired")
        persona = self.persona_for(thread)
        question = persona.ask_time or "How long do you have free to fly? Tell me and I'll find something that fits."
        self.db.set_meta(f"await:{thread}", "1")
        self.say((lead_in + " " if lead_in else "") + question, thread, kind="ask_time")

    def handle_availability(self, thread: str, minutes: int, announce: bool = True) -> dict:
        """Record the pilot's free time and create flight offers that fit it. Returns a summary dict."""
        employer = thread_employer(thread)
        if employer is None:
            return {"error": "Availability only applies to a company dispatcher"}
        minutes = int(max(10, min(minutes, 24 * 60)))
        self.db.set_meta(f"avail:{thread}", str(minutes))
        self.db.set_meta(f"await:{thread}", "0")
        for j in self.db.jobs("offered", scope=employer.id):
            self.db.set_job(j.id, status="expired")
        label = _fmt_minutes(minutes)
        try:
            jobs = self.career.employer_dispatch.offer_flights(employer, minutes, 3)
        except NoFlightsAvailable as exc:
            self.db.set_meta(f"await:{thread}", "1")
            if announce:
                self.say(f"{label}, understood. {exc}", thread, kind="ask_time")
            return {"error": str(exc)}
        if announce:
            self.say(self._offer_intro(thread, minutes, jobs), thread)
        for j in jobs:
            self.say(B.offer_summary(self.db, j), thread, kind="offer", payload={"job_id": j.id})
        return {"minutes": minutes, "offers": [{"job_id": j.id, "summary": B.offer_summary(self.db, j)} for j in jobs]}

    def _tool_availability(self, thread: str, minutes: int) -> dict:
        return self.handle_availability(thread, minutes, announce=False)

    def _offer_intro(self, thread: str, minutes: int, jobs: list[Job]) -> str:
        n = len(jobs)
        fallback = (f"{_fmt_minutes(minutes)} - got it. I have {n} flight{'s' if n != 1 else ''} that "
                    f"fit{'s' if n == 1 else ''} your window. Take a look:")
        facts = [B.offer_summary(self.db, j) for j in jobs]
        text = self._llm_oneshot(
            f"The pilot told you they have {_fmt_minutes(minutes)} free. You are offering these flights (shown as cards "
            f"below your message): {json.dumps(facts)}. Say one or two short sentences introducing them. Do not "
            "repeat the details.", max_tokens=80, persona=self.persona_for(thread))
        return text or fallback

    def accept_offer(self, job_id: int, thread: str) -> Job:
        job = self.career.accept_job(job_id)             # raises CareerError with a readable message
        self.db.add_message("user", f"I'll take {job.origin} to {job.dest}.", thread)
        self.career._fire("thread_changed", thread=thread)
        return job

    def decline_offer(self, job_id: int, thread: str) -> None:
        job = self.db.job(job_id)
        self.career.decline_job(job_id)
        if job:
            self.db.add_message("user", f"No thanks, not {job.origin} to {job.dest}.", thread)
        employer = thread_employer(thread)
        if employer and not self.db.jobs("offered", scope=employer.id):
            self.ask_availability(thread, "No problem.")
        else:
            self.career._fire("thread_changed", thread=thread)

    def start_thread(self, employer: Employer, hr_message: str = "") -> str:
        """Open a new employer conversation: HR welcome, dispatcher greeting, availability question."""
        thread = employer_thread(employer.id)
        if self.db.last_message(thread) is None:
            if hr_message:
                self.say(hr_message, thread, role="hr")
            self.say(self.persona_for(thread).greeting.replace("Captain", self._pilot_name()), thread)
        self.ask_availability(thread)
        return thread

    def _pilot_name(self) -> str:
        p = self.db.pilot()
        return p.name if p else "Captain"

    # --------------------------------------------------------------- chat
    def chat(self, user_text: str, thread: str = "general", add_user: bool = True) -> str:
        """Run a full agent turn in `thread`. Raises LLMError if the model is unreachable."""
        user_text = user_text.strip()
        if not user_text:
            return ""
        with self._lock:
            if add_user:
                self.db.add_message("user", user_text, thread)
            employer = thread_employer(thread)
            if employer and self.is_awaiting_availability(thread):
                minutes = parse_duration(user_text)
                if minutes:
                    result = self.handle_availability(thread, minutes)
                    return result.get("error") or "ok"
            self.tools.thread = thread
            try:
                reply = self._agent_loop(user_text, thread)
                self.online = True
            except LLMError:
                self.online = False
                raise
            reply = reply.strip() or "Say again? I didn't catch that."
            self.say(reply, thread)
            return reply

    def _agent_loop(self, user_text: str, thread: str) -> str:
        use_prompt_mode = self._prompt_tools
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(thread, use_prompt_mode)}]
        hist = self.history(thread)
        messages += hist if hist and hist[-1]["content"] == user_text else hist + [{"role": "user", "content": user_text}]
        schemas = schemas_for(thread)

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                r = self.client.chat(messages, tools=None if use_prompt_mode else schemas)
            except LLMError as exc:
                if not use_prompt_mode and self.settings.ai.tool_mode == "auto" and "tool" in str(exc).lower():
                    log.info("Model rejected native tools; switching to prompt tool protocol")
                    self._prompt_tools = use_prompt_mode = True
                    messages[0]["content"] = self.system_prompt(thread, True)
                    continue
                raise
            calls = r.tool_calls or self._parse_text_calls(r.text)
            if not calls:
                return _clean_spoken(r.text)
            if r.tool_calls:
                messages.append(r.raw_message | {"content": r.raw_message.get("content") or ""})
            else:
                messages.append({"role": "assistant", "content": r.text})
            for c in calls:
                result = self.tools.call(c.name, c.arguments)
                log.info("tool %s(%s) -> %s", c.name, c.arguments, str(result)[:200])
                if r.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
                else:
                    messages.append({"role": "user", "content": f"<tool_result name=\"{c.name}\">"
                                                                f"{json.dumps(result)}</tool_result>"})
        return "I'm having trouble pulling that together right now. Can you ask me again?"

    @staticmethod
    def _parse_text_calls(text: str) -> list[ToolCall]:
        calls = []
        for i, m in enumerate(_TOOL_RE.finditer(text or "")):
            try:
                d = json.loads(m.group(1))
                calls.append(ToolCall(f"txt{i}", d.get("name", ""), d.get("arguments") or {}))
            except ValueError:
                continue
        return calls

    # -------------------------------------------------- proactive messaging
    def react(self, kind: str, detail: str, data: dict | None = None, thread: str = "general") -> str | None:
        """A short in-character call-out for a flight event. Falls back to templates offline."""
        if not self.settings.ai.proactive_comms or EVENT_IMPORTANCE.get(kind, 0) < 1:
            return None
        fallback = B.EVENT_TEMPLATES.get(kind, "{detail}").format(detail=detail)
        return self._llm_oneshot(
            f"Flight event just occurred: '{kind}' - {detail}. Give a brief radio call-out reacting to this "
            "(1 or 2 short sentences).", max_tokens=90, persona=self.persona_for(thread)) or fallback

    def brief_job(self, job: Job) -> str:
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        thread = employer_thread(job.employer_id) if job.employer_id else "general"
        persona = self.persona_for(thread)
        facts = B.job_facts(self.db, job, aircraft)
        fallback = B.template_briefing(facts, persona.name)
        text = self._llm_oneshot(
            "The pilot just accepted this job. Give a pre-flight briefing in your own voice covering route, "
            "payload, aircraft, fuel advice and any deadline. Use ONLY these facts, do not invent numbers:\n"
            + json.dumps(facts), max_tokens=260, persona=persona)
        return text or fallback

    def debrief(self, settlement) -> str:
        thread = employer_thread(settlement.employer_id) if settlement.employer_id else "general"
        persona = self.persona_for(thread)
        facts = B.settlement_facts(settlement)
        fallback = B.template_debrief(facts, persona.name)
        text = self._llm_oneshot(
            "The pilot's flight just finished. Give a debrief in your own voice: acknowledge the result, "
            "comment on landing and any penalties, mention pay. Use ONLY these facts:\n" + json.dumps(facts),
            max_tokens=220, persona=persona)
        return text or fallback

    def hr_reply(self, employer: Employer, accepted: bool, base_message: str) -> str:
        """HR's reply to an application: the facts are fixed, the wording may be the model's."""
        text = self._llm_oneshot(
            f"You are the HR manager at {employer.name}. Write a short, professional reply (2-3 sentences) to a pilot's "
            f"application. Decision: {'ACCEPTED' if accepted else 'REJECTED'}. Facts you must keep: {base_message}",
            max_tokens=160, persona=None)
        return text or base_message

    def enhance_jobs(self, jobs: list[Job]) -> int:
        """Ask the model to rewrite job briefings with personality (one request)."""
        jobs = [j for j in jobs if not self.db.get_meta(f"enhanced_{j.id}") and not j.employer_id]
        if not self.settings.ai.ai_job_flavour or not jobs:
            return 0
        items = [{"id": j.id, "type": j.kind, "client": j.client, "from": j.origin, "to": j.dest,
                  "load": f"{j.pax} pax" if j.pax else f"{j.cargo_lb} lb"} for j in jobs[:8]]
        text = self._llm_oneshot(
            "Write a vivid but plausible 1-2 sentence client request for each contract below, in the client's "
            "voice or as the dispatcher summarising it. Keep the load and places as given. Respond with ONLY a "
            'JSON object mapping job id (string) to the text, e.g. {"12": "..."}.\n' + json.dumps(items),
            max_tokens=700, temperature=0.9, persona=None)
        if not text:
            return 0
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return 0
        try:
            mapping = json.loads(m.group(0))
        except ValueError:
            return 0
        n = 0
        for j in jobs:
            new = mapping.get(str(j.id))
            if isinstance(new, str) and 10 < len(new) < 400:
                self.db.set_job(j.id, briefing=new.strip())
                self.db.set_meta(f"enhanced_{j.id}", "1")
                n += 1
        return n

    # ---------------------------------------------------------------- utils
    def _llm_oneshot(self, instruction: str, max_tokens: int = 150, temperature: float | None = None,
                     persona: Persona | None = None) -> str | None:
        # When the server is known to be down, don't queue behind another request: just fall back.
        if not self._lock.acquire(blocking=self.online is not False):
            return None
        try:
            system = (persona.style + " You speak aloud over radio: short, natural, no markdown or emojis."
                      if persona else "You are a helpful writer for a flight-sim career game.")
            r = self.client.chat([{"role": "system", "content": system},
                                  {"role": "user", "content": instruction}],
                                 max_tokens=max_tokens, temperature=temperature)
            self.online = True
            return _clean_spoken(r.text) if persona else r.text
        except LLMError as exc:
            self.online = False
            log.info("LLM unavailable for one-shot: %s", exc)
            return None
        finally:
            self._lock.release()

    def test_connection(self) -> tuple[bool, str]:
        ok, msg = self.client.health()
        self.online = ok
        return ok, msg


def _fmt_minutes(m: int) -> str:
    if m < 60:
        return f"{m} minutes"
    h, rest = divmod(m, 60)
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {rest} minutes" if rest else "")


def _clean_spoken(text: str) -> str:
    text = _TOOL_RE.sub("", text or "")
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\n{2,}", "\n", text).strip()

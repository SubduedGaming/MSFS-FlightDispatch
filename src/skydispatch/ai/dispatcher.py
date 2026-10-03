"""The AI dispatcher: conversation, proactive radio calls, briefings and debriefs."""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ..career import Career
from ..core.config import Settings
from ..db.models import Job
from . import briefing as B
from .llm import LLMError, LLMReply, LMStudioClient, ToolCall
from .personas import Persona, get_persona
from .tools import TOOL_SCHEMAS, ToolBox

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_MESSAGES = 16

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


class Dispatcher:
    def __init__(self, career: Career, settings: Settings, client: LMStudioClient | None = None):
        self.career = career
        self.db = career.db
        self.settings = settings
        self.client = client or LMStudioClient(settings.ai)
        self.tools = ToolBox(career)
        self._prompt_tools = settings.ai.tool_mode == "prompt"
        self._lock = threading.Lock()   # one LLM conversation at a time
        self.online: bool | None = None

    # ------------------------------------------------------------- personas
    @property
    def persona(self) -> Persona:
        return get_persona(self.settings.ai.persona)

    def system_prompt(self, with_tool_protocol: bool = False) -> str:
        p, persona = self.db.pilot(), self.persona
        facts = self.tools.t_get_status()
        hangar = self.tools.t_list_hangar()["aircraft"]
        home_fleet = "; ".join(f"id {a['id']} {a['registration']} {a['type']} at {a['at']} "
                               f"({a['condition_pct']}% cond, {a['fuel_gal']} gal)" for a in hangar) or "none"
        prompt = (
            f"{persona.style}\n"
            f"You are the flight dispatcher for a career-mode add-on for Microsoft Flight Simulator. You are "
            f"talking to {p.name if p else 'the pilot'} (callsign {p.callsign if p else '?'}) by radio/voice chat. "
            "Speak in short, natural sentences: 1 to 4 sentences normally, as if talking aloud. No markdown, no "
            "bullet lists, no emojis, no stage directions. Spell out nothing; say numbers naturally.\n"
            "RULES: Never invent jobs, aircraft, prices or weather; use your tools to look things up. "
            "Money-spending or irreversible actions (buying/selling aircraft, abandoning a job) require the pilot's "
            "explicit confirmation first, then call the tool with confirmed=true. Accepting a job needs a hangar "
            "aircraft that is eligible; check with get_job if unsure. If a tool returns an error, explain it simply.\n"
            f"CURRENT STATE: {json.dumps(facts)}\nHANGAR: {home_fleet}\n")
        if with_tool_protocol:
            names = "\n".join(f"- {s['function']['name']}: {s['function']['description']} "
                              f"args={list(s['function']['parameters']['properties'])}" for s in TOOL_SCHEMAS)
            prompt += TOOL_PROTOCOL % names
        return prompt

    # --------------------------------------------------------------- chat
    def greeting(self) -> str:
        return self.persona.greeting

    def history(self) -> list[dict[str, str]]:
        rows = self.db.messages(HISTORY_MESSAGES)
        return [{"role": r["role"], "content": r["content"]} for r in rows if r["role"] in ("user", "assistant")]

    def chat(self, user_text: str) -> str:
        """Run a full agent turn. Raises LLMError if the model is unreachable."""
        user_text = user_text.strip()
        if not user_text:
            return ""
        with self._lock:
            self.db.add_message("user", user_text)
            try:
                reply = self._agent_loop(user_text)
                self.online = True
            except LLMError:
                self.online = False
                raise
            reply = reply.strip() or "Say again, Captain? I didn't catch that."
            self.db.add_message("assistant", reply)
            return reply

    def _agent_loop(self, user_text: str) -> str:
        use_prompt_mode = self._prompt_tools
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(use_prompt_mode)}]
        hist = self.history()
        # history already includes the just-saved user message
        messages += hist if hist and hist[-1]["content"] == user_text else hist + [{"role": "user", "content": user_text}]

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                r = self.client.chat(messages, tools=None if use_prompt_mode else TOOL_SCHEMAS)
            except LLMError as exc:
                if not use_prompt_mode and self.settings.ai.tool_mode == "auto" and "tool" in str(exc).lower():
                    log.info("Model rejected native tools; switching to prompt tool protocol")
                    self._prompt_tools = use_prompt_mode = True
                    messages[0]["content"] = self.system_prompt(True)
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
    def react(self, kind: str, detail: str, data: dict | None = None) -> str | None:
        """A short in-character call-out for a flight event. Falls back to templates offline."""
        if not self.settings.ai.proactive_comms:
            return None
        if EVENT_IMPORTANCE.get(kind, 0) < 1:
            return None
        fallback = B.EVENT_TEMPLATES.get(kind, "{detail}").format(detail=detail)
        text = self._llm_oneshot(
            f"Flight event just occurred: '{kind}' - {detail}. Give a brief radio call-out reacting to this "
            "(1 or 2 short sentences).", max_tokens=90) or fallback
        return text

    def brief_job(self, job: Job) -> str:
        aircraft = self.db.aircraft(job.aircraft_id) if job.aircraft_id else None
        facts = B.job_facts(self.db, job, aircraft)
        fallback = B.template_briefing(facts, self.persona.name)
        text = self._llm_oneshot(
            "The pilot just accepted this job. Give a pre-flight briefing in your own voice covering route, "
            "payload, aircraft, fuel advice and any deadline. Use ONLY these facts, do not invent numbers:\n"
            + json.dumps(facts), max_tokens=260)
        return text or fallback

    def debrief(self, settlement) -> str:
        facts = B.settlement_facts(settlement)
        fallback = B.template_debrief(facts, self.persona.name)
        text = self._llm_oneshot(
            "The pilot's flight just finished. Give a debrief in your own voice: acknowledge the result, "
            "comment on landing and any penalties, mention pay. Use ONLY these facts:\n" + json.dumps(facts),
            max_tokens=220)
        return text or fallback

    def enhance_jobs(self, jobs: list[Job]) -> int:
        """Ask the model to rewrite job briefings with personality (one request)."""
        jobs = [j for j in jobs if not self.db.get_meta(f"enhanced_{j.id}")]
        if not self.settings.ai.ai_job_flavour or not jobs:
            return 0
        items = [{"id": j.id, "type": j.kind, "client": j.client, "from": j.origin, "to": j.dest,
                  "load": f"{j.pax} pax" if j.pax else f"{j.cargo_lb} lb"} for j in jobs[:8]]
        text = self._llm_oneshot(
            "Write a vivid but plausible 1-2 sentence client request for each contract below, in the client's "
            "voice or as the dispatcher summarising it. Keep the load and places as given. Respond with ONLY a "
            'JSON object mapping job id (string) to the text, e.g. {"12": "..."}.\n' + json.dumps(items),
            max_tokens=700, temperature=0.9, persona=False)
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
                     persona: bool = True) -> str | None:
        # When the server is known to be down, don't queue behind another request: just fall back.
        if not self._lock.acquire(blocking=self.online is not False):
            return None
        try:
            system = (self.persona.style + " You speak aloud over radio: short, natural, no markdown or emojis."
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


def _clean_spoken(text: str) -> str:
    text = _TOOL_RE.sub("", text or "")
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\n{2,}", "\n", text).strip()

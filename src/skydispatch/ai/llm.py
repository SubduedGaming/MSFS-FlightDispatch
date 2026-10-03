"""Client for LM Studio's local OpenAI-compatible server (default http://localhost:1234/v1).

Any OpenAI-compatible endpoint works (Ollama, llama.cpp server, vLLM...).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.config import AISettings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Problem talking to the language model (shown to the user in plain English)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)


class LMStudioClient:
    def __init__(self, cfg: AISettings, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._transport = transport

    # ------------------------------------------------------------------
    def _http(self, timeout: float | None = None) -> httpx.Client:
        base = self.cfg.base_url.rstrip("/")
        return httpx.Client(base_url=base, timeout=timeout or self.cfg.timeout_s, transport=self._transport,
                            headers={"Authorization": f"Bearer {self.cfg.api_key or 'lm-studio'}"})

    def list_models(self, timeout: float = 5.0) -> list[str]:
        try:
            with self._http(timeout) as c:
                r = c.get("/models")
                r.raise_for_status()
                return [m["id"] for m in r.json().get("data", [])]
        except httpx.ConnectError:
            raise LLMError(f"Cannot reach the AI server at {self.cfg.base_url}. "
                           "Is LM Studio running with the local server started?") from None
        except httpx.TimeoutException:
            raise LLMError("The AI server did not answer in time.") from None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMError(f"AI server error: {exc}") from exc

    def health(self) -> tuple[bool, str]:
        try:
            models = self.list_models()
        except LLMError as exc:
            return False, str(exc)
        if not models:
            return False, "Connected, but no model is loaded in LM Studio. Load one and try again."
        return True, f"Connected. {len(models)} model(s) available: {', '.join(models[:3])}"

    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             max_tokens: int | None = None, temperature: float | None = None) -> LLMReply:
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "stream": False,
        }
        if self.cfg.model:
            body["model"] = self.cfg.model
        if tools:
            body["tools"] = tools
        try:
            with self._http() as c:
                r = c.post("/chat/completions", json=body)
                if r.status_code >= 400:
                    detail = _error_text(r)
                    raise LLMError(f"AI server returned {r.status_code}: {detail}")
                data = r.json()
        except httpx.ConnectError:
            raise LLMError(f"Cannot reach the AI server at {self.cfg.base_url}. "
                           "Start LM Studio's local server and load a model.") from None
        except httpx.TimeoutException:
            raise LLMError("The AI server took too long to respond. Try a smaller model or raise the "
                           "timeout in Settings > AI.") from None
        except httpx.HTTPError as exc:
            raise LLMError(f"AI request failed: {exc}") from exc
        except ValueError as exc:
            raise LLMError("AI server returned invalid JSON") from exc
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected response from the AI server") from None
        text = _strip_thinking(msg.get("content") or "")
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {}
            calls.append(ToolCall(tc.get("id") or f"call_{len(calls)}", fn.get("name", ""), args or {}))
        return LLMReply(text=text, tool_calls=calls, raw_message=msg)


def _error_text(r: httpx.Response) -> str:
    try:
        data = r.json()
        err = data.get("error", data)
        return err.get("message", str(err)) if isinstance(err, dict) else str(err)
    except ValueError:
        return r.text[:200]


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models."""
    while "<think>" in text and "</think>" in text:
        a, b = text.index("<think>"), text.index("</think>") + len("</think>")
        text = text[:a] + text[b:]
    if "</think>" in text:           # some templates only emit the closing tag
        text = text.split("</think>", 1)[1]
    return text.strip()

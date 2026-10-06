"""User settings, stored as JSON. Every field has a safe default.

Add a new setting by adding a field to one of the dataclasses below; old
settings files keep working because unknown/missing keys are tolerated.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .paths import config_path

log = logging.getLogger(__name__)


@dataclass
class PilotSettings:
    name: str = "Captain"
    callsign: str = "SKY1"
    home_icao: str = "EGLL"


@dataclass
class SimSettings:
    # "simulated" | "simconnect" | "bridge"
    mode: str = "simulated"
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    bridge_token: str = ""
    # Sharing (the Windows app next to MSFS lets SkyDispatch on other computers connect to it)
    share_enabled: bool = False
    share_port: int = 8765
    share_token: str = ""
    sample_hz: float = 2.0
    simulated_speed: float = 8.0  # time acceleration for demo mode
    # Aircraft installed in the player's sim (comma-separated catalog ids; empty = unknown, don't restrict)
    restrict_to_installed: bool = True
    installed_aircraft: str = ""
    installed_auto: bool = True       # refresh the list automatically; manual edits switch this off
    packages_path: str = ""           # custom MSFS packages folder (the one containing Community/ and Official/)


@dataclass
class AISettings:
    base_url: str = "http://localhost:1234/v1"   # LM Studio default
    api_key: str = "lm-studio"                    # LM Studio ignores it
    model: str = ""                               # blank = whichever is loaded
    temperature: float = 0.8
    max_tokens: int = 500
    timeout_s: float = 90.0
    # "native" = OpenAI tool calling, "prompt" = text protocol (works with any model)
    tool_mode: str = "auto"
    persona: str = "marcus"
    copilot: str = "sam"
    copilot_enabled: bool = True
    copilot_callouts: bool = True     # proactive callouts (positive rate, top of descent, sink rate, ...)
    ai_job_flavour: bool = True       # let the LLM write job briefings
    proactive_comms: bool = True      # dispatcher comments on flight events


@dataclass
class VoiceSettings:
    tts_enabled: bool = True
    tts_engine: str = "auto"         # auto | piper | system | none
    tts_voice: str = ""              # engine-specific voice id
    tts_rate: float = 1.0
    tts_volume: float = 0.9
    piper_model: str = ""            # blank = the voice that suits the chosen dispatcher
    stt_enabled: bool = True
    stt_model: str = "base.en"       # faster-whisper model size
    stt_device: str = "auto"
    stt_language: str = "en"
    input_device: str = ""           # blank = system default
    output_device: str = ""
    push_to_talk_key: str = "F9"
    auto_speak_replies: bool = True
    output: str = "pc"               # where speech is heard: "pc" (this computer's speakers) or "phone" (the Android app)
    voices_prompted: bool = False    # asked once whether to download the natural character voices


@dataclass
class GameSettings:
    difficulty: str = "normal"       # relaxed | normal | realistic
    strict_aircraft: bool = False    # require the exact aircraft type in sim
    wear_enabled: bool = True
    job_count: int = 12
    start_balance: float = 25000.0
    max_job_distance_nm: float = 1500.0
    recency_half_life_days: int = 45   # recent experience halves after this many days without flying


@dataclass
class RemoteSettings:
    """Browser remote control: the Windows app serves a web UI that any device on your network can use."""
    enabled: bool = False
    port: int = 8766
    token: str = ""                  # the access code typed once on each device


@dataclass
class PlanSettings:
    """Flight planning and loading the sim aircraft."""
    simbrief_user: str = ""          # SimBrief username, or the numeric Pilot ID
    auto_sync_loadout: bool = True   # load fuel and payload into the sim when the aircraft is parked, engines off


@dataclass
class UISettings:
    units_distance: str = "nm"       # nm | km
    units_weight: str = "lb"         # lb | kg
    currency: str = "$"
    theme: str = "dark"              # dark | light
    first_run_complete: bool = False
    window_geometry: str = ""
    check_updates: bool = True       # look for a newer release on GitHub at startup


@dataclass
class Settings:
    pilot: PilotSettings = field(default_factory=PilotSettings)
    sim: SimSettings = field(default_factory=SimSettings)
    ai: AISettings = field(default_factory=AISettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    game: GameSettings = field(default_factory=GameSettings)
    ui: UISettings = field(default_factory=UISettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)
    plan: PlanSettings = field(default_factory=PlanSettings)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        return _build(cls, data)

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a crash can't leave a half-written settings file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.error("Could not read settings (%s); using defaults", exc)
            broken = path.with_suffix(".broken.json")
            try:
                path.replace(broken)
            except OSError:
                pass
            return cls()


def _build(cls, data: dict[str, Any]):
    """Create dataclass `cls` from dict, ignoring unknown keys and bad types."""
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default) and isinstance(value, dict):
            kwargs[f.name] = _build(type(default), value)
        elif isinstance(default, bool):
            kwargs[f.name] = bool(value)
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                kwargs[f.name] = type(default)(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, str):
            kwargs[f.name] = str(value)
    return cls(**kwargs)

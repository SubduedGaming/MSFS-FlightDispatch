"""Facade the UI talks to for all voice features."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..core.config import Settings
from .stt import MicRecorder, Transcriber, stt_available
from .tts import TTSManager

log = logging.getLogger(__name__)


class VoiceService:
    def __init__(self, settings: Settings, voice_hint: str = "", persona_voice: str = ""):
        self.settings = settings
        self.mic = MicRecorder(settings.voice)
        self.stt = Transcriber(settings.voice)
        self.tts = TTSManager(settings.voice, voice_hint, persona_voice)
        self._busy = False

    # -- capabilities (used by Settings and the setup wizard) --------------------
    def stt_status(self) -> tuple[bool, str]:
        return stt_available()

    def tts_status(self) -> tuple[bool, str]:
        e = self.tts.engine()
        if e:
            return True, f"Text-to-speech ready ({e.name})"
        return False, "No text-to-speech engine found. Install Piper (pip install skydispatch[tts]) or an OS voice."

    # -- speaking ----------------------------------------------------------------
    def say(self, text: str, voice: str = "", tag: str = "", speed: float = 1.0) -> None:
        self.tts.speak(text, voice, tag, speed)

    def drop_unless(self, tags: set[str]) -> None:
        """Stop speech that belongs to a conversation that is no longer being looked at."""
        self.tts.drop_unless(tags)

    def shut_up(self) -> None:
        self.tts.stop()

    # -- listening -----------------------------------------------------------------
    def start_listening(self) -> bool:
        if not self.settings.voice.stt_enabled:
            return False
        if self.mic.recording:
            return True
        self.tts.stop()                # barge-in: don't record the dispatcher talking
        try:
            self.mic.start()
            return True
        except Exception as exc:
            log.error("Microphone error: %s", exc)
            return False

    def stop_listening(self, on_text: Callable[[str], None], on_error: Callable[[str], None]) -> None:
        if not self.mic.recording:
            return
        audio = self.mic.stop()

        def work():
            try:
                text = self.stt.transcribe(audio)
                if text:
                    on_text(text)
                else:
                    on_error("I didn't hear anything. Try again.")
            except Exception as exc:
                log.exception("Transcription failed")
                on_error(f"Speech recognition failed: {exc}")

        threading.Thread(target=work, daemon=True, name="stt").start()

    def shutdown(self) -> None:
        self.tts.shutdown()
        if self.mic.recording:
            self.mic.stop()

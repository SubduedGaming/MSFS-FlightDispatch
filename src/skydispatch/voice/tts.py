"""Text-to-speech: Piper (neural, offline) with OS-voice fallbacks."""
from __future__ import annotations

import logging
import platform
import queue
import shutil
import subprocess
import sys
import threading
import urllib.request
import zlib
from pathlib import Path
from typing import Callable

from ..core.config import VoiceSettings
from ..core.paths import models_dir
from .text import speakable

log = logging.getLogger(__name__)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def piper_urls(voice: str) -> tuple[str, str]:
    """('en_GB-alan-medium') -> download URLs for the .onnx model and its json config."""
    try:
        lang_region, name, quality = voice.split("-", 2)
        family = lang_region.split("_")[0]
    except ValueError as exc:
        raise ValueError(f"Bad Piper voice id: {voice}") from exc
    base = f"{PIPER_BASE}/{family}/{lang_region}/{name}/{quality}/{voice}"
    return base + ".onnx", base + ".onnx.json"


def piper_paths(voice: str) -> tuple[Path, Path]:
    return models_dir() / f"{voice}.onnx", models_dir() / f"{voice}.onnx.json"


DEFAULT_PIPER_VOICE = "en_GB-alan-medium"


def piper_importable() -> bool:
    try:
        import piper  # noqa: F401
        import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


def installed_voices() -> list[str]:
    """Piper voices that are fully downloaded, sorted."""
    try:
        return sorted(m.name[:-5] for m in models_dir().glob("*.onnx") if (m.parent / (m.name + ".json")).exists())
    except OSError:
        return []


def resolve_piper_voice(cfg: VoiceSettings, persona_voice: str = "") -> str:
    """The voice to speak with: an explicit choice wins, then the character's own voice if it is downloaded.

    If it is not, pick a different installed voice per character (stable, from a hash of the character's voice id) so
    people still sound different from one another while only some voices are downloaded."""
    if cfg.piper_model:
        return cfg.piper_model
    if persona_voice and piper_installed(persona_voice):
        return persona_voice
    have = installed_voices()
    if have:
        return have[zlib.crc32(persona_voice.encode()) % len(have)] if persona_voice else have[0]
    return DEFAULT_PIPER_VOICE


def voice_size_mb(voice: str) -> int:
    return 115 if voice.endswith("-high") else 22 if voice.endswith("-low") else 63


def download_voices(voices: list[str], progress: Callable[[float, str], None] | None = None) -> list[str]:
    """Download every voice that is missing. Returns the ones that were fetched."""
    todo = [v for v in dict.fromkeys(voices) if not piper_installed(v)]
    for i, v in enumerate(todo):
        download_piper_voice(v, (lambda f, i=i, v=v: progress((i + f) / len(todo), v)) if progress else None)
    return todo


def piper_installed(voice: str) -> bool:
    m, c = piper_paths(voice)
    return m.exists() and c.exists()


def download_piper_voice(voice: str, progress: Callable[[float], None] | None = None) -> None:
    model_url, cfg_url = piper_urls(voice)
    model, cfg = piper_paths(voice)
    for url, dest in ((cfg_url, cfg), (model_url, model)):
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:   # noqa: S310
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        tmp.replace(dest)


class TTSEngine:
    name = "none"

    def available(self) -> bool:
        return False

    def speak(self, text: str, voice: str = "", speed: float = 1.0) -> None:   # blocking; `voice` = engine voice id
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def voices(self) -> list[str]:
        return []


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self, cfg: VoiceSettings, persona_voice: Callable[[], str] = lambda: ""):
        self.cfg = cfg
        self._persona_voice = persona_voice
        self._voice = None
        self._loaded_name = ""
        self._stop = threading.Event()
        self._synth_lock = threading.Lock()

    def voice_name(self, override: str = "") -> str:
        return resolve_piper_voice(self.cfg, override or self._persona_voice())

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return piper_installed(self.voice_name())

    def _load(self, override: str = ""):
        name = self.voice_name(override)
        if override and not piper_installed(name):
            name = self.voice_name()
        if self._voice is None or self._loaded_name != name:
            from piper import PiperVoice
            model, cfg = piper_paths(name)
            self._voice = PiperVoice.load(str(model), config_path=str(cfg))
            self._loaded_name = name
        return self._voice

    def can_synthesize(self, voice: str = "") -> bool:
        """True when Piper can render `voice` to audio data (no sound card needed: the phone plays it)."""
        try:
            import piper  # noqa: F401
        except Exception:
            return False
        return piper_installed(self.voice_name(voice))

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> tuple[bytes, int]:
        """Render `text` to 16-bit mono PCM. Returns (samples, sample rate); empty samples when there is nothing."""
        with self._synth_lock:                                  # the Piper voice object is not thread safe
            voice = self._load(voice)
            rate = getattr(getattr(voice, "config", None), "sample_rate", 22050)
            speed = max(0.5, min(2.0, speed * self.cfg.tts_rate))
            chunks: list[bytes] = []
            if hasattr(voice, "synthesize_stream_raw"):             # piper-tts 1.2
                chunks = list(voice.synthesize_stream_raw(text))
            else:                                                   # piper-tts >= 1.3
                # Speed changes the model's own timing (length_scale) so pitch stays natural; a little extra variation
                # in intonation (noise scales) makes the delivery less flat.
                try:
                    from piper import SynthesisConfig
                    cfg = SynthesisConfig(length_scale=1.0 / speed, noise_scale=0.75, noise_w_scale=0.9)
                    stream = voice.synthesize(text, syn_config=cfg)
                except (ImportError, TypeError):
                    stream = voice.synthesize(text)
                for c in stream:
                    rate = getattr(c, "sample_rate", rate)
                    chunks.append(c.audio_int16_bytes)
            return b"".join(chunks), int(rate)

    def speak(self, text: str, voice: str = "", speed: float = 1.0) -> None:
        import numpy as np
        import sounddevice as sd
        self._stop.clear()
        pcm, rate = self.synthesize(text, voice, speed)
        if not pcm or self._stop.is_set():
            return
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        audio *= max(0.0, min(1.0, self.cfg.tts_volume))
        from .stt import resolve_device
        dev = resolve_device(self.cfg.output_device, "output")
        sd.play(audio, int(rate), device=dev)
        while sd.get_stream().active and not self._stop.is_set():
            self._stop.wait(0.05)
        sd.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass


class SystemEngine(TTSEngine):
    """macOS `say`, Windows System.Speech via PowerShell, Linux espeak-ng/spd-say."""
    name = "system"

    def __init__(self, cfg: VoiceSettings, voice_hint: str = ""):
        self.cfg = cfg
        self.voice_hint = voice_hint
        self._proc: subprocess.Popen | None = None
        self._os = platform.system()

    def _cmd(self, text: str) -> list[str] | None:
        rate = max(0.5, min(2.0, self.cfg.tts_rate))
        voice = self.cfg.tts_voice
        if self._os == "Darwin" and shutil.which("say"):
            cmd = ["say", "-r", str(int(175 * rate))]
            if voice:
                cmd += ["-v", voice]
            return cmd + [text]
        if self._os == "Windows":
            esc = text.replace("'", "''")
            sel = f"$s.SelectVoice('{voice}');" if voice else ""
            ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"{sel}$s.Rate={int(round((rate - 1) * 6))};$s.Volume={int(self.cfg.tts_volume * 100)};"
                  f"$s.Speak('{esc}')")
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
        for exe in ("espeak-ng", "espeak"):
            if shutil.which(exe):
                cmd = [exe, "-s", str(int(165 * rate)), "-a", str(int(self.cfg.tts_volume * 200))]
                if voice:
                    cmd += ["-v", voice]
                return cmd + [text]
        if shutil.which("spd-say"):
            return ["spd-say", "-w", text]
        return None

    def available(self) -> bool:
        return self._cmd("x") is not None

    def speak(self, text: str, voice: str = "", speed: float = 1.0) -> None:
        cmd = self._cmd(text)
        if not cmd:
            return
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # noqa: S603
                                      creationflags=flags)
        self._proc.wait()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def voices(self) -> list[str]:
        try:
            if self._os == "Darwin":
                out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
                return [ln.split()[0] for ln in out.splitlines() if ln.strip()]
            if self._os == "Windows":
                ps = ("Add-Type -AssemblyName System.Speech;(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                      ".GetInstalledVoices()|%{$_.VoiceInfo.Name}")
                out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                                     text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                return [ln.strip() for ln in out.splitlines() if ln.strip()]
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            if exe:
                out = subprocess.run([exe, "--voices=en"], capture_output=True, text=True, timeout=10).stdout
                return [ln.split()[4] for ln in out.splitlines()[1:] if len(ln.split()) > 4]
        except Exception:
            log.debug("could not list system voices", exc_info=True)
        return []


class TTSManager:
    """Non-blocking speech queue with barge-in (stop) support."""

    def __init__(self, cfg: VoiceSettings, voice_hint: str = "", persona_voice: str = "",
                 on_start: Callable[[], None] | None = None, on_end: Callable[[], None] | None = None):
        self.cfg = cfg
        self.voice_hint = voice_hint
        self.persona_voice = persona_voice
        self.on_start, self.on_end = on_start, on_end
        self._q: queue.Queue[tuple[str, str, str, float] | None] = queue.Queue()
        self._current_tag = ""
        self._engine: TTSEngine | None = None
        self._synth_engine: PiperEngine | None = None      # renders audio for phones (no sound card needed)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tts")
        self._thread.start()
        self.last_error = ""

    def engine(self) -> TTSEngine | None:
        with self._lock:
            want = self.cfg.tts_engine
            if self._engine and (want in ("auto", self._engine.name)) and self._engine.available():
                return self._engine
            candidates: list[TTSEngine] = []
            if want in ("auto", "piper"):
                candidates.append(PiperEngine(self.cfg, lambda: self.persona_voice))
            if want in ("auto", "system"):
                candidates.append(SystemEngine(self.cfg, self.voice_hint))
            for c in candidates:
                if c.available():
                    self._engine = c
                    return c
            self._engine = None
            return None

    def _piper(self) -> PiperEngine:
        if self._synth_engine is None:
            self._synth_engine = PiperEngine(self.cfg, lambda: self.persona_voice)
        return self._synth_engine

    def can_synthesize(self, voice: str = "") -> bool:
        """Can speech be rendered to audio data (for a phone)? Only the natural Piper voices can; OS voices cannot."""
        return (self.cfg.tts_engine in ("auto", "piper") and self._piper().can_synthesize(voice))

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> bytes | None:
        """A WAV of `text` spoken by `voice`, or None when this PC cannot render audio."""
        if not self.can_synthesize(voice):
            return None
        from .audio import pcm_to_wav
        pcm, rate = self._piper().synthesize(speakable(text), voice, speed)
        return pcm_to_wav(pcm, rate) if pcm else None

    def active_engine_name(self) -> str:
        e = self.engine()
        return e.name if e else "none"

    def speak(self, text: str, voice: str = "", tag: str = "", speed: float = 1.0) -> None:
        """Queue speech. `tag` names the conversation it belongs to (see drop_unless)."""
        if not self.cfg.tts_enabled or self.cfg.tts_engine == "none":
            return
        clean = speakable(text)
        if clean:
            self._q.put((clean, voice, tag, speed))

    def drop_unless(self, allowed: set[str]) -> None:
        """Cancel queued and current speech whose tag is not in `allowed` (untagged speech is always kept)."""
        kept: list = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if item is None or not item[2] or item[2] in allowed:
                kept.append(item)
        for item in kept:
            self._q.put(item)
        if self._current_tag and self._current_tag not in allowed and self._engine:
            self._engine.stop()

    def stop(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._engine:
            self._engine.stop()

    def shutdown(self) -> None:
        self.stop()
        self._q.put(None)

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            text, voice, tag, speed = item
            self._current_tag = tag
            engine = self.engine()
            if engine is None:
                self.last_error = "No text-to-speech engine available"
                continue
            try:
                if self.on_start:
                    self.on_start()
                engine.speak(text, voice, speed)
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"{engine.name}: {exc}"
                log.exception("TTS failed")
                if engine.name == "piper":          # fall back to the OS voice next time
                    self._engine = SystemEngine(self.cfg, self.voice_hint)
            finally:
                self._current_tag = ""
                if self.on_end:
                    self.on_end()

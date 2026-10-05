"""Speech-to-text: microphone capture + faster-whisper (offline)."""
from __future__ import annotations

import logging
import threading
from typing import Any

from ..core.config import VoiceSettings
from . import gpu

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
AVIATION_PROMPT = ("Flight dispatcher conversation. Terms: accept job, decline job, hangar, refuel, ICAO, runway, "
                   "METAR, Cessna, Skyhawk, Caravan, Citation, Boeing, Airbus, Heathrow, Gatwick, Charlie, Alpha.")


def stt_available() -> tuple[bool, str]:
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
    except Exception as exc:
        return False, f"Microphone support missing ({exc}). Install with: pip install skydispatch[stt]"
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False, "faster-whisper is not installed. Install with: pip install skydispatch[stt]"
    return True, "Speech recognition ready"


def _devices(kind: str) -> list[tuple[int, dict]]:
    """(index, info) for devices that can do input/output, with the default host API's devices first.

    Windows lists every physical device once per audio API (MME, DirectSound, WASAPI, WDM-KS), so a bare
    device name is ambiguous. The default API is the safest (it resamples), so it wins."""
    import sounddevice as sd
    key = f"max_{kind}_channels"
    devs = [(i, d) for i, d in enumerate(sd.query_devices()) if d.get(key, 0) > 0]
    try:
        default_api = sd.query_hostapis(sd.default.hostapi)["name"] if sd.default.hostapi >= 0 else ""
        apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
    except Exception:
        return devs
    devs.sort(key=lambda t: (apis.get(t[1].get("hostapi"), "") != default_api, t[0]))
    return devs


def _names(kind: str) -> list[str]:
    try:
        seen: list[str] = []
        for _, d in _devices(kind):
            if d["name"] not in seen:
                seen.append(d["name"])
        return seen
    except Exception:
        return []


def resolve_device(name: str, kind: str):
    """Turn a saved device name into a sounddevice index (None = system default)."""
    if not name:
        return None
    try:
        for i, d in _devices(kind):
            if d["name"] == name:
                return i
    except Exception:
        pass
    return None            # unplugged or renamed: fall back to the default rather than failing


def list_input_devices() -> list[str]:
    return _names("input")


def list_output_devices() -> list[str]:
    return _names("output")


class MicRecorder:
    """Push-to-talk recorder. start() ... stop() -> float32 mono samples at 16 kHz."""

    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        self._stream = None
        self._frames: list[Any] = []
        self._lock = threading.Lock()
        self.level = 0.0

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        import numpy as np
        import sounddevice as sd
        if self._stream:
            return
        self._frames = []

        def cb(indata, frames, t, status):   # runs in the audio thread
            chunk = indata[:, 0].copy()
            self.level = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
            with self._lock:
                self._frames.append(chunk)

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      device=resolve_device(self.cfg.input_device, "input"), callback=cb)
        self._stream.start()

    def stop(self):
        import numpy as np
        stream, self._stream = self._stream, None
        if stream:
            stream.stop()
            stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
        self.level = 0.0
        return np.concatenate(frames) if frames else np.zeros(0, dtype="float32")


def _is_gpu_library_error(exc: BaseException) -> bool:
    """CUDA runtime DLLs (cuBLAS/cuDNN) are not bundled, so a GPU chosen by 'auto' can fail at the first use."""
    text = str(exc).lower()
    return any(w in text for w in ("cublas", "cudnn", "cuda", "cannot be loaded", "is not found"))


class Transcriber:
    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        self._model = None
        self._model_key = ""
        self._cpu_only = False            # set once the GPU turned out to be unusable
        self._lock = threading.Lock()

    def _device(self) -> str:
        """cpu or cuda. The GPU is only used when its libraries are present (see voice/gpu.py)."""
        want = "cpu" if self._cpu_only else self.cfg.stt_device
        if want == "cpu":
            return "cpu"
        if gpu.available() and gpu.has_cuda_device():
            gpu.activate()
            return "cuda"
        if want == "cuda":
            log.info("GPU speech recognition needs the NVIDIA libraries (Settings > Voice); using the CPU")
        return "cpu"

    def _load(self):
        device = self._device()
        key = f"{self.cfg.stt_model}|{device}"
        if self._model is None or self._model_key != key:
            from faster_whisper import WhisperModel
            compute = "float16" if device == "cuda" else "int8"
            log.info("Loading Whisper model %s on %s", self.cfg.stt_model, device)
            self._model = WhisperModel(self.cfg.stt_model, device=device, compute_type=compute)
            self._model_key = key
        return self._model

    def _fall_back_to_cpu(self, exc: BaseException) -> bool:
        if self._cpu_only or self.cfg.stt_device == "cpu" or not _is_gpu_library_error(exc):
            return False
        log.warning("GPU speech recognition is unavailable (%s); using the CPU instead", exc)
        self._cpu_only, self._model = True, None
        return True

    def preload(self) -> None:
        with self._lock:
            try:
                self._load()
            except Exception as exc:
                if not self._fall_back_to_cpu(exc):
                    raise
                self._load()

    def transcribe(self, audio) -> str:
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            return ""
        with self._lock:
            try:
                return self._run(audio)
            except Exception as exc:
                if not self._fall_back_to_cpu(exc):
                    raise
                return self._run(audio)           # the failed model is discarded; retry once on the CPU

    def _run(self, audio) -> str:
        segments, _ = self._load().transcribe(
            audio, language=self.cfg.stt_language or None, vad_filter=True, beam_size=1,
            initial_prompt=AVIATION_PROMPT, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()      # segments are decoded lazily, here

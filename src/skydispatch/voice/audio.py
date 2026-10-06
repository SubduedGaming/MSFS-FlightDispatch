"""WAV helpers for the phone: wrap synthesised speech for download, and turn an uploaded recording into the 16 kHz mono
float samples the speech recogniser wants."""
from __future__ import annotations

import io
import wave

TARGET_RATE = 16000
MAX_SECONDS = 120


class AudioError(Exception):
    """The uploaded audio cannot be used; the message is safe to show."""


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Little-endian 16-bit PCM samples in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_mono16k(data: bytes):
    """Parse a PCM16 WAV (mono or stereo, any sample rate) into a float32 numpy array at 16 kHz."""
    try:
        import numpy as np
    except ImportError as exc:                                   # numpy ships with the speech extras
        raise AudioError("Speech recognition is not installed on the PC (numpy is missing).") from exc
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if w.getcomptype() != "NONE" or width != 2:
                raise AudioError("Send 16-bit PCM audio (a plain .wav).")
            if channels not in (1, 2) or not 8000 <= rate <= 96000:
                raise AudioError("Send mono or stereo audio between 8 kHz and 96 kHz.")
            if frames / rate > MAX_SECONDS:
                raise AudioError(f"That recording is longer than {MAX_SECONDS} seconds.")
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise AudioError("That is not a readable .wav file.") from exc
    samples = np.frombuffer(raw[: len(raw) - len(raw) % (2 * channels)], dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    if rate != TARGET_RATE and len(samples):
        n = max(1, int(round(len(samples) * TARGET_RATE / rate)))
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)
    return samples

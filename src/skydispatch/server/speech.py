"""Utterances waiting for a phone to fetch their audio."""
from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

KEEP = 50
TTL_S = 600


@dataclass
class Utterance:
    id: str
    thread: str
    text: str                      # already cleaned for speaking
    voice: str                     # the Piper voice wanted ("" = the dispatcher's default)
    speed: float
    created: float = field(default_factory=time.time)
    wav: bytes | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class SpeechStore:
    """The last few utterances, so a phone can fetch the audio for a `speech` event it just received."""

    def __init__(self, keep: int = KEEP, ttl: float = TTL_S):
        self._keep, self._ttl = keep, ttl
        self._items: "OrderedDict[str, Utterance]" = OrderedDict()
        self._lock = threading.Lock()

    def add(self, thread: str, text: str, voice: str, speed: float) -> Utterance:
        utt = Utterance(secrets.token_hex(8), thread, text, voice, speed)
        with self._lock:
            self._prune()
            self._items[utt.id] = utt
            while len(self._items) > self._keep:
                self._items.popitem(last=False)
        return utt

    def get(self, utterance_id: str) -> Utterance | None:
        with self._lock:
            self._prune()
            return self._items.get(utterance_id)

    def _prune(self) -> None:
        cutoff = time.time() - self._ttl
        for key in [k for k, u in self._items.items() if u.created < cutoff]:
            del self._items[key]

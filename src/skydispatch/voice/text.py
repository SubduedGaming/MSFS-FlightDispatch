"""Prepare text for speech."""
import re

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]+")


def speakable(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"\bnm\b", "nautical miles", text)
    text = re.sub(r"\bfpm\b", "feet per minute", text)
    text = re.sub(r"\bkts?\b", "knots", text)
    text = re.sub(r"\blbs?\b", "pounds", text)
    text = re.sub(r"\bETA\b", "E T A", text)
    text = re.sub(r"\b([A-Z]{4})\b", lambda m: " ".join(m.group(1)) if _looks_icao(m.group(1)) else m.group(1), text)
    return re.sub(r"\s+", " ", text).strip()


def _looks_icao(word: str) -> bool:
    # Four capitals starting with a region letter (K, E, L, C, Y, ...) are read letter by letter.
    return word[0] in "KCEYLRVZOPNFUSMTWBHGDAI" and word not in {"HOLD", "STOP", "WIND", "FUEL", "LAND", "GOOD"}

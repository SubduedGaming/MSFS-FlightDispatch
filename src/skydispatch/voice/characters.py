"""Who speaks with which voice, and which voices are worth downloading."""
from __future__ import annotations

from ..ai.personas import HR_VOICE, get_persona
from ..copilot.personas import get_copilot
from ..data.employers import get_employer


def voices_in_use(settings, employer_ids: list[str]) -> list[tuple[str, str]]:
    """(voice id, who) for everyone the pilot talks to now: ops desk, their employers, HR and the copilot."""
    out: list[tuple[str, str]] = []
    p = get_persona(settings.ai.persona)
    out.append((p.piper_voice, p.name))
    for eid in employer_ids:
        e = get_employer(eid)
        if e:
            q = get_persona(e.persona)
            out.append((q.piper_voice, f"{q.name} ({e.name})"))
    c = get_copilot(settings.ai.copilot)
    out.append((c.piper_voice, f"{c.name} (copilot)"))
    out.append((HR_VOICE, "HR"))
    seen: set[str] = set()
    return [(v, who) for v, who in out if not (v in seen or seen.add(v))]

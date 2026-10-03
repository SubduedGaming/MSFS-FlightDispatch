"""Turns raw flight metrics into a score, payout multiplier and consequences."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FlightMetrics:
    outcome: str = "completed"        # completed | diverted | crashed | aborted
    block_min: float = 0.0
    air_min: float = 0.0
    distance_nm: float = 0.0
    fuel_used_gal: float = 0.0
    landing_fpm: float | None = None
    landing_g: float = 1.0
    max_g: float = 1.0
    overspeed_s: float = 0.0
    bounces: int = 0
    slews: int = 0
    low_fuel: bool = False
    fuel_exhausted: bool = False
    deadline_min: int = 0             # 0 = none (free flight)
    on_time: bool = True
    minutes_late: float = 0.0


@dataclass
class ScoreResult:
    score: float
    payout_mult: float
    reputation_delta: float
    xp: int
    penalties: list[str] = field(default_factory=list)
    grade: str = "C"


def landing_label(fpm: float | None) -> str:
    if fpm is None:
        return "unknown"
    v = abs(fpm)
    if v <= 100:
        return "butter"
    if v <= 200:
        return "smooth"
    if v <= 300:
        return "firm"
    if v <= 500:
        return "hard"
    if v <= 800:
        return "very hard"
    return "crash-level"


def score_flight(m: FlightMetrics, difficulty: str = "normal") -> ScoreResult:
    pen: list[str] = []
    score = 100.0
    strict = {"relaxed": 0.6, "normal": 1.0, "realistic": 1.4}.get(difficulty, 1.0)

    if m.outcome == "crashed":
        return ScoreResult(0.0, 0.0, -8.0, 0, ["Aircraft crashed"], "F")
    if m.outcome == "aborted":
        return ScoreResult(0.0, 0.0, -2.0, 0, ["Flight abandoned"], "F")
    if m.outcome == "diverted":
        return ScoreResult(25.0, 0.0, -3.0, int(m.air_min), ["Landed at the wrong airport - contract void"], "F")

    if m.landing_fpm is not None:
        v = abs(m.landing_fpm)
        deduction = 0.0 if v <= 150 else 5 if v <= 250 else 15 if v <= 400 else 35 if v <= 600 else 55
        if deduction:
            deduction *= strict
            score -= deduction
            pen.append(f"{landing_label(m.landing_fpm).capitalize()} landing ({v:.0f} fpm): -{deduction:.0f}")
    if m.overspeed_s > 2:
        d = min(25.0, m.overspeed_s * 0.5) * strict
        score -= d
        pen.append(f"Overspeed for {m.overspeed_s:.0f}s: -{d:.0f}")
    if m.max_g > 1.8:
        d = min(20.0, (m.max_g - 1.8) * 15) * strict
        score -= d
        pen.append(f"Peak {m.max_g:.1f} G: -{d:.0f}")
    if m.bounces:
        d = min(10.0, 5.0 * m.bounces)
        score -= d
        pen.append(f"{m.bounces} bounce(s): -{d:.0f}")
    if m.deadline_min and not m.on_time:
        d = min(25.0, 3 + m.minutes_late * 0.5)
        score -= d
        pen.append(f"{m.minutes_late:.0f} min late: -{d:.0f}")
    if m.low_fuel:
        score -= 8
        pen.append("Landed with fuel reserves below 10%: -8")
    if m.fuel_exhausted:
        score -= 25
        pen.append("Ran out of fuel: -25")
    if m.slews:
        d = min(60.0, 30.0 * m.slews)
        score -= d
        pen.append(f"Position jumps detected (slew/teleport): -{d:.0f}")
    score = max(0.0, min(100.0, score))

    mult = 1.1 if score >= 92 else 1.0 if score >= 75 else 0.85 if score >= 50 else 0.6
    if m.slews:
        mult *= 0.5
    grade = "A" if score >= 92 else "B" if score >= 80 else "C" if score >= 65 else "D" if score >= 45 else "F"
    rep = max(-5.0, min(4.0, (score - 70) / 8.0))
    xp = int(m.air_min * 1.5 + m.distance_nm * 0.4 + (score / 10))
    return ScoreResult(round(score, 1), mult, round(rep, 2), xp, pen, grade)

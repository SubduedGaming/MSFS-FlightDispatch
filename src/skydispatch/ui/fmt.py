"""Display formatting that honours the user's unit settings."""
from __future__ import annotations

from datetime import datetime

from ..core.config import Settings
from ..core.geo import fmt_duration

NM_TO_KM = 1.852
LB_TO_KG = 0.45359237


def money(settings: Settings, amount: float, signed: bool = False) -> str:
    sym = settings.ui.currency
    sign = "+" if signed and amount > 0 else "-" if amount < 0 else ""
    return f"{sign}{sym}{abs(amount):,.0f}"


def dist(settings: Settings, nm: float) -> str:
    return f"{nm * NM_TO_KM:,.0f} km" if settings.ui.units_distance == "km" else f"{nm:,.0f} nm"


def weight(settings: Settings, lb: float) -> str:
    return f"{lb * LB_TO_KG:,.0f} kg" if settings.ui.units_weight == "kg" else f"{lb:,.0f} lb"


def when(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def duration(minutes: float) -> str:
    return fmt_duration(minutes)

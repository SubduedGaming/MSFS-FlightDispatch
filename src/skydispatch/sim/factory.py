from __future__ import annotations

from ..core.config import SimSettings
from .base import SimProvider


def make_provider(cfg: SimSettings) -> SimProvider:
    if cfg.mode == "simconnect":
        from .simconnect_provider import SimConnectProvider
        return SimConnectProvider(cfg.sample_hz)
    if cfg.mode == "bridge":
        from .bridge import BridgeClientProvider
        return BridgeClientProvider(cfg.bridge_host, cfg.bridge_port, cfg.bridge_token, cfg.sample_hz)
    from .simulated import SimulatedProvider
    return SimulatedProvider(cfg.sample_hz, cfg.simulated_speed)

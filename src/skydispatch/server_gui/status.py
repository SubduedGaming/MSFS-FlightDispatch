"""What the Status tab says, as plain data so it can be tested without a window."""
from __future__ import annotations

from dataclasses import dataclass

from ..core import fmt


@dataclass
class Row:
    key: str
    state: str          # ok | busy | bad | off
    title: str
    text: str


def status_rows(ctx) -> list[Row]:
    s = ctx.settings
    rows: list[Row] = []

    web = ctx.web
    if web is not None and web.running:
        from ..sim.bridge_server import local_addresses
        addrs = ", ".join(f"http://{a}:{s.remote.port}/" for a in local_addresses()) or f"port {s.remote.port}"
        rows.append(Row("server", "ok", "Phone access", f"Running at {addrs}"))
    elif not s.remote.enabled:
        rows.append(Row("server", "off", "Phone access", "Off. Turn it on to use the phone app."))
    else:
        rows.append(Row("server", "bad", "Phone access", ctx.web_error or "Not running."))

    paired = len(ctx.pairing.devices())
    live = web.client_count if web is not None and web.running else 0
    rows.append(Row("phones", "ok" if live else "off", "Phones",
                    f"{paired} paired, {live} connected now" if paired else "No phone paired yet. Use the Phones tab."))

    prov = ctx.provider
    mode = {"simulated": "Simulated flights", "simconnect": "Microsoft Flight Simulator", "bridge": "Another SkyDispatch PC"}
    name = mode.get(s.sim.mode, s.sim.mode)
    status = prov.status if prov else "disconnected"
    rows.append(Row("sim", {"connected": "ok", "connecting": "busy", "error": "bad"}.get(status, "off"), "Simulator",
                    f"{name}: {status}"))

    online = ctx.dispatcher.online
    rows.append(Row("ai", "ok" if online else ("off" if online is None else "bad"), "AI dispatcher",
                    "Online" if online else ("Not checked yet." if online is None else f"Offline ({s.ai.base_url})")))

    tts_ok, tts_msg = ctx.voice.tts_status()
    stt_ok, stt_msg = ctx.voice.stt_status()
    rows.append(Row("speech", "ok" if tts_ok or stt_ok else "off", "Speech",
                    f"Out: {tts_msg}. In: {stt_msg}. Speech is heard on "
                    f"{'the phone' if s.voice.output == 'phone' else 'this PC'}."))

    pilot = ctx.db.pilot()
    rows.append(Row("career", "ok" if pilot else "off", "Career",
                    f"{pilot.rank} {pilot.name}, balance {fmt.money(s, pilot.balance)}" if pilot
                    else "No career yet. Create one in the phone app."))

    if ctx.update_info:
        rows.append(Row("update", "busy", "Update", f"Version {ctx.update_info.version} is available (Help > Check for updates)."))
    return rows

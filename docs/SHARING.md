# Streaming flight data to a second computer (advanced)

> Most people do not need this: in 2.0 the **phone** is the player's screen and it talks to the server directly (see the
> [README](../README.md)). This page is for running the SkyDispatch **server** on a different computer from MSFS, for
> example a Linux box on your network, while the Windows PC with MSFS only streams the aircraft's telemetry.

MSFS and its SimConnect interface only run on Windows, so the **Windows PC with MSFS is always the telemetry host**.

## On the Windows PC (with MSFS)
1. Open **Settings > Simulator**, keep *Source* on *Microsoft Flight Simulator on this PC*.
2. Tick **Let SkyDispatch on another computer connect to this one**.
3. Click **Generate** for a token, then **Save**. The page shows the addresses to use, for example `192.168.1.20:8765`.
4. Allow SkyDispatch through Windows Firewall (Private networks) if Windows asks.

The wizard has the same option on the simulator step.

## On the other computer
Run the SkyDispatch server there (`skydispatch-server --sim bridge`, or set **Settings > Simulator > Source:
SkyDispatch on my Windows PC**), enter the address, port and token, then restart it. The list of installed aircraft is
reported by the Windows PC too. Pair the phone with *that* server.

## Headless / development
`pip install "skydispatch[sim]"` then `skydispatch-bridge --token YOURSECRET` runs the same host without the app
(`--port`, `--host`, `--hz`, `--simulate` to stream a demo flight, `--packages-path`).

## Security
Only flight telemetry is sent and the shared token is required. The connection is not encrypted, so keep it on your
home network or a VPN.

# Using SkyDispatch on a Mac, Linux or second computer

MSFS and its SimConnect interface only run on Windows, so the **Windows PC is always the host**. SkyDispatch for
Windows has the sharing built in: there is nothing extra to install. Other computers run SkyDispatch normally and
connect to the Windows one.

## On the Windows PC (with MSFS)
1. Open **Settings > Simulator**, keep *Source* on *Microsoft Flight Simulator on this PC*.
2. Tick **Let SkyDispatch on my Mac/Linux/other PC connect to this one**.
3. Click **Generate** for a token, then **Save**. The page shows the addresses to use, for example `192.168.1.20:8765`.
4. Allow SkyDispatch through Windows Firewall (Private networks) if Windows asks.

The wizard has the same option on the simulator step.

## On the other computer
**Settings > Simulator > Source: SkyDispatch on my Windows PC**, enter the address, port and token, then
**Apply and reconnect now**. The list of installed aircraft is reported by the Windows PC too.

## Headless / development
`pip install "skydispatch[sim]"` then `skydispatch-bridge --token YOURSECRET` runs the same host without the app
(`--port`, `--host`, `--hz`, `--simulate` to stream a demo flight, `--packages-path`).

## Security
Only flight telemetry is sent and the shared token is required. The connection is not encrypted, so keep it on your
home network or a VPN.

# SkyDispatch Bridge (live MSFS data on macOS / Linux / another PC)

MSFS and its SimConnect interface only run on Windows. If SkyDispatch runs anywhere else, or you simply want it on a
second computer, run the **Bridge** on the Windows PC with MSFS and connect to it over your network.

## On the Windows PC (with MSFS)
- Installed with the Windows installer (tick *SkyDispatch Bridge*), then start it from the Start menu; or
- from source: `pip install "skydispatch[sim]"` then `skydispatch-bridge --token YOURSECRET`

Options: `--port 8765`, `--host 0.0.0.0`, `--hz 2`, `--simulate` (stream a demo flight to test the link).
Allow the program through Windows Firewall when prompted (private networks).

## In SkyDispatch
**Settings > Simulator > Source: SkyDispatch Bridge**, enter the Windows PC's address (for example `192.168.1.20`),
the port and the same token, then **Apply and reconnect now**.

## Security
The Bridge only sends flight telemetry and requires the shared token if you set one. Always set a token unless the
network is fully trusted. The connection is not encrypted, so keep it on your home network or a VPN.

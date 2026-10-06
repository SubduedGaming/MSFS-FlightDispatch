# Phone API (v1)

The Windows server exposes a JSON API for the Android app. The full contract is
[`docs/api/openapi.json`](api/openapi.json); a test keeps it in step with the server. This page explains how to connect.

## Running the server
- Windows app: **Settings > Simulator > Browser remote**, tick *Serve the web remote from this PC*.
- Without a window: `skydispatch-server` (add `--port 9000`, `--sim simulated|simconnect|bridge`, `--no-sim`,
  `--revoke-all`). It prints the address and a pairing code.

## Pairing
1. The server shows a **pairing code** (for example `ABCD-2345`, valid 5 minutes, one use) and a link
   `skydispatch://pair?host=192.168.1.20&port=8766&code=ABCD2345` that is also drawn as a QR code.
2. The app sends the code once:
   ```
   POST /api/v1/pair
   X-SkyDispatch: 1
   {"code": "ABCD-2345", "device_name": "Pixel 8"}
   ```
   and receives `{"token": "<device_id>.<secret>", "device_id": "...", "api": 1}`.
3. From then on every request carries `Authorization: Bearer <token>`. Keep the token private on the phone.
4. `POST /api/v1/unpair` removes this phone; the server can also remove any phone. A removed token stops working at once.

Five wrong codes within a minute lock that address out for a minute.

## Using it
- `GET /api/v1/ping` (no token) says whether this is a SkyDispatch server, which API version it speaks, and whether
  your token is still accepted.
- `GET /api/v1/state` tells you whether a career exists. If `has_career` is false, call `GET /api/v1/career/options`
  and then `POST /api/v1/career`. Other routes answer `409` until a career exists.
- `GET /api/v1/stream` is a Server-Sent Events stream (`state`, `sim_status`, `ai_status`, `toast`, `thread`, `busy`,
  `settings`, `career`, `plan`). Re-read the affected screen when an event arrives; reconnect after a drop and refetch.
- Errors are `{"error": "message"}` with a suitable status. The message is safe to show to the player.
- Values such as money and distances come preformatted in the player's units.

## Security
The connection is plain HTTP. Use it on your home network (or over a VPN). Tokens are random, stored hashed on the PC,
and revocable; changing nothing else is needed to sign a phone out.

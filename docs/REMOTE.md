# Browser remote: use SkyDispatch from a Mac, tablet or phone

The Windows app can serve a web version of itself. Open it in any browser on your network and you get the same
pages (Dashboard, Job Board, Messenger, Flight, Freelance, Hangar, Logbook, Finances, Settings) as a **remote
control** for the app on the PC. Nothing runs in the browser except the display:

- the sim connection, AI dispatcher, copilot, database and **voice** all stay on the Windows PC;
- replies are spoken by the PC and **Hold to talk** records from the PC's microphone;
- you can fly on a single monitor and keep the career screens, charts and chat on your Mac next to it.

## Turn it on (Windows PC)
1. **Settings > Simulator > Browser remote**, tick **Serve the web remote from this PC**.
2. Click **Generate** for an access code, then **Save**. The page lists the address, for example
   `http://192.168.1.20:8766/`.
3. Allow SkyDispatch through Windows Firewall (Private networks) if Windows asks.

## Use it
Open the address on the Mac (or iPad, phone) and type the access code once; the browser then stays signed in for
30 days. A link of the form `http://192.168.1.20:8766/#code=YOURCODE` signs in without typing (the code is removed
from the address bar immediately).

The pages update live: telemetry, dispatcher messages, flight events and notifications arrive as they happen.

## Security
- Everything except the empty page needs the access code. Wrong codes are rate limited (5 tries a minute per
  address).
- The session cookie is `HttpOnly` and `SameSite=Strict`, and every change needs a custom request header, so other
  websites cannot drive the remote.
- Changing the access code in Settings signs every device out.
- The connection is plain HTTP, not encrypted: use it on your home network or over a VPN, not the open internet.
- Hardware and file settings (simulator source, AI server, voice devices, folders, backups) can only be changed on
  the PC; the remote exposes just the gameplay preferences.

## Troubleshooting
| Symptom | Fix |
| --- | --- |
| Page does not load | Check the address/port in Settings, that the PC firewall allows SkyDispatch on Private networks, and that both devices are on the same network. |
| "Could not start the browser remote on port …" | Another program uses that port; pick a different one and save. |
| Hold to talk does nothing | The PC's microphone or speech recognition is unavailable; see Settings > Voice on the PC. |
| Signed out unexpectedly | The access code changed; type the new one. |

## Updates
SkyDispatch checks GitHub for a newer release at startup (Settings > General > Updates, or **Help > Check for
updates**). On Windows, choose **Download and install**: the installer is downloaded over HTTPS from GitHub,
verified against its published checksum, and run silently. SkyDispatch closes, upgrades in place (your career and
settings are kept) and starts again. Updates are never installed while a flight is being recorded. On other systems
the dialog opens the download page.

The check reads the repository's public releases. If the repository is private, GitHub shows nothing to anyone who
is not signed in, so the check reports that instead of updating. For a private repository, set the environment
variable `SKYDISPATCH_GITHUB_TOKEN` to a read-only token (Contents: read) on that computer.

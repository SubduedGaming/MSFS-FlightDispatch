<h1 align="center">&#9992; SkyDispatch</h1>
<p align="center"><b>A career-mode add-on for Microsoft Flight Simulator 2020 &amp; 2024 with an AI flight dispatcher.</b><br>
Your Windows PC runs the sim and the game. You play it from an <b>Android phone</b> on the same Wi-Fi: accept contracts,
fly them in the sim, get paid for how you fly, grow a hangar, and talk to your dispatcher by voice.</p>

## How it fits together

```
 Android phone  ──── home Wi-Fi ────►  Windows PC
 (the game: jobs, messages,            SkyDispatch server (small window + tray icon)
  flight, hangar, voice)                 ├─ MSFS link (SimConnect), flight recording, scoring
                                         ├─ career database, economy, hangar
                                         └─ AI dispatcher (your local LM Studio) and the voices
```

The Windows app has no game screens any more. Its window shows what is running, pairs phones (with a QR code), holds the
PC-side settings and shows the log. Everything you play happens on the phone.

## Features

| | |
|---|---|
| **Job board** | Apply to ten companies on a career ladder, from a bush operator to a long-haul airline. They check your **total time, recent experience, skill level and hours on their aircraft**. Recent experience decays every day you don't fly. See [docs/CAREER.md](docs/CAREER.md). |
| **Messenger** | Once hired, the company's dispatcher messages you. They ask **how long you have free** and offer flights that fit, as cards you accept or decline. New messages arrive as phone notifications. |
| **Copilot** | An AI first officer on the Flight screen: ask for advice by text or voice, run checklists, check fuel, plan the descent, brief the approach. It also makes callouts (positive rate, top of descent, 1000/500 ft, sink rate). |
| **Voice on the phone** | Hold the mic button and talk; the dispatcher answers out loud through the phone, in each character's own voice. Recognition and voices run on the PC (offline *faster-whisper* and *Piper*); the phone only records and plays. |
| **Freelance contracts** | For owner-operators: passenger, cargo, VIP charter, medevac and mail contracts for aircraft you own. |
| **Training and costs** | Licence and medical to renew, ratings to earn, monthly living costs and aircraft upkeep. |
| **SimBrief and loadout** | Import your SimBrief plan and load the sim aircraft's fuel and payload to match. |
| **Only your aircraft** | Jobs only use aircraft that are **installed in your simulator**, detected automatically. |
| **Automatic flight recording** | Connects to MSFS and records phases, takeoff and touchdown, landing rate, peak G, overspeed, fuel burn, distance and track into a local database. |
| **Scoring &amp; economy** | Grades every flight and pays accordingly. Operating costs, resale value, reputation, XP and ranks. |
| **Hangar** | Buy new or used aircraft, refuel, inspect, repair and sell. Rough flying causes wear; overdue inspections ground the aircraft. |
| **AI dispatcher** | A tool-calling agent powered by your **local LM Studio** server. Four personalities, proactive radio calls during the flight, briefings and debriefs. |

## Install

You need two things from the **Releases** page:

| | File | Notes |
|---|------|-------|
| Windows PC | `SkyDispatch-Setup-x.y.z.exe` | Windows 10/11. The installer can add a Windows Firewall rule so phones on your home network can connect (it asks when installed for all users). Uninstall from Settings &rarr; Apps. |
| Android phone | `SkyDispatch-x.y.z.apk` | Android 8 or newer. Android will ask you to allow installing from this source. |

The builds are not code-signed: Windows SmartScreen warns on first launch (*More info &rarr; Run anyway*).

### From source
```bash
git clone https://github.com/SubduedGaming/MSFS-FlightDispatch && cd MSFS-FlightDispatch
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[voice,sim,discovery]"
skydispatch-gui            # the server with its window; or `skydispatch-server` for no window at all
```

## Quick start

1. **Run SkyDispatch on the PC.** The window opens on the **Phones** tab and shows a QR code.
2. **AI dispatcher:** install [LM Studio](https://lmstudio.ai), load an instruct model and start its local server ([details](docs/AI_SETUP.md)). Check it on the **Status** tab.
3. **Simulator link:** with MSFS on the same PC choose *Microsoft Flight Simulator* in Settings &rarr; Simulator. No sim handy? Choose the *simulated flight engine* and use **Fly it for me (demo)** on the phone's Flight screen.
4. **Install the app on your phone**, open it and tap **Scan QR code** (or type the address and pairing code). Both devices must be on the same Wi-Fi.
5. **Create your pilot** on the phone (name, home airport, starter aircraft, experience).
6. Open **Jobs &rarr; Companies** and apply to **Bluebird Bush Air** (it is recruiting when you start). Open **Messages** and tell the dispatcher how long you have: they assign your flight. Or, with your own aircraft, take a **Freelance** job.
7. In MSFS, start your engines. SkyDispatch starts the clock, flies along with you and pays out when you park at the destination. Ask the **Copilot** for help any time on the Flight screen.

More: [docs/API.md](docs/API.md) (the phone API), [docs/REMOTE.md](docs/REMOTE.md) (the older browser page, still available), [docs/SHARING.md](docs/SHARING.md) (running the sim link from a second computer), [docs/BUILDING.md](docs/BUILDING.md).

## How the game works

- **Careers:** companies hire you on your logbook ([details and the full ladder](docs/CAREER.md)). Your dispatcher builds each flight to fit the time you have, in company aircraft, with fuel and running costs paid by the company.
- **Freelance jobs** pay by distance and load and are generated mostly for aircraft you own. A job needs an aircraft of the right class, enough seats/cargo/range/runway, parked at the departure airport with enough fuel and in airworthy condition.
- **Flights** start when an engine runs and the aircraft moves, and end when you park. Landing at the wrong airport voids the contract.
- **Score (0&ndash;100)** starts at 100 and loses points for hard landings, overspeed, G-load, bounces, lateness, low fuel and position jumps. Difficulty (*relaxed / normal / realistic*) scales strictness and pay.
- **Aircraft** lose condition with hours and rough handling and need regular inspections.

## Network and privacy

The phone talks to the PC over **plain HTTP on your home network** (or a VPN you set up yourself). Each phone gets its own
secret token when it pairs; remove a phone on the **Phones** tab and it stops working at once. The app only connects to
private (home-network) addresses. Nothing goes to any cloud service except the optional SimBrief and weather lookups the
PC makes. See [SECURITY.md](SECURITY.md).

## Where your data lives (on the PC)

| | |
|---|---|
| Career database &amp; settings | `%LOCALAPPDATA%\SkyDispatch\SkyDispatch` |
| Paired phones | `devices.json` in the same folder (tokens are stored hashed) |
| Logs | `...\Logs` |

Settings &rarr; *Data and Backup* creates/restores backups, imports the worldwide airport database
([OurAirports](https://ourairports.com/data/), public domain) and resets the career. Set `SKYDISPATCH_HOME` to relocate everything.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q                       # headless (Qt offscreen); about two minutes
python packaging/build.py                 # the Windows installer (docs/BUILDING.md)
cd android && gradlew testDebugUnitTest assembleDebug     # the Android app
```

```
src/skydispatch/
  server/          Engine (all game orchestration, no Qt), pairing, speech, discovery, `skydispatch-server`
  web/             the phone API (HTTP + event stream) and the older browser page
  server_gui/      the Windows admin window (Status, Phones, Settings, Logs, tray)
  career.py        rules engine: accept jobs, record flights, settle pay and wear
  db/ sim/ flight/ jobs/ hangar/ data/ pilot/ copilot/ ai/ voice/   the game itself
android/           the Android app (Kotlin, Jetpack Compose)
docs/api/          openapi.json, the contract between server and app
packaging/         PyInstaller spec and Inno Setup script (Windows)
tests/             unit, GUI and end-to-end tests
```

## Status and known limits

- This is a **pre-release**. Verified: the full Python test suite; the Android app against the real server in an Android
  emulator (pairing, career creation, jobs, messages, flight with live updates, notifications, spoken replies, voice input);
  and a built Windows installer. **Not yet verified:** the app on a physical phone, the QR scanner on a real camera, a
  real MSFS/SimConnect session, the installed Windows app's tray icon and *Start with Windows*, and Piper's neural voices.
- There is no automated CI: run `pytest` and the Gradle tests locally.
- Weather uses the public aviationweather.gov METAR API when internet is available.
- The airport database ships with 180 major airports; import the full OurAirports set for worldwide coverage.

## License

Apache License 2.0. SkyDispatch is an independent project and is not affiliated with or endorsed by Microsoft or Asobo Studio.

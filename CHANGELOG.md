# Changelog

## 2.0.0-beta.3 (pre-release)
- Fix: **the co-pilot gave canned or irrelevant answers.** Reasoning models (Qwen3.5 and similar) spent the whole reply
  budget thinking and returned nothing, and the app then showed a stock "Standing by, Captain" line. The AI client now sends
  `reasoning_effort: "none"` (Settings > AI > *Reasoning effort*; blank sends nothing).
- Changed: **no fallbacks in the co-pilot.** If the AI cannot answer, you get a notification with the real reason instead of
  a made-up reply. The keyword-guessing answers are gone; the quick buttons (checklist, fuel, descent, approach, weather,
  status, and the new *Job details*) remain as explicit reports.
- New: the co-pilot knows the job (departure, destination, aircraft, load, pay, deadline) and the pilot, treats the aircraft
  as parked until the flight starts (no more bogus distances from a sim that is not flying), and can look up the weather
  for any airport by itself (a `get_weather` tool).
- Changed: **no fallbacks in the dispatcher either.** Job briefings, debriefs, flight-event call-outs, HR replies, the
  "assigned your flight" line and the AI job descriptions are written by the model or not at all. When it cannot, you get a
  notification with the real reason (an assignment is not made at all if its message cannot be written; hiring still works).
  The template texts, the stock "Say again?" replies and the automatic switch to the text tool protocol are gone.
  **Tool calling** is now *Native* (default) or *Text protocol*, chosen in Settings > AI; the old *Automatic* option is removed.
- Fix: airfields with no METAR (the weather service answers with an empty body) were reported as "service unavailable".

## 2.0.0-beta.2 (pre-release)
- New: **the Android app** (`android/`, Kotlin + Jetpack Compose). Pair by QR code or by typing the address and code, create
  your pilot, then play: Home (dashboard), Flight (live numbers, plan and loadout, end flight, abandon, copilot chat),
  Jobs (freelance market, companies, apply, resign), Messages (dispatcher chat, offers, availability), Hangar and dealer,
  Logbook, Finances, Training and licences, Settings. A foreground service keeps the connection open while a job is
  active, and new dispatcher messages arrive as notifications. The app only connects to home-network addresses.
- New: **voice on the phone, in the app**: hold the mic button to talk (recognised on the PC), replies are played through
  the phone (the PC's own voice, or Android's text-to-speech when the PC has no voice for that character).
- Changed: **replies are spoken on the phone by default** (`speech_output` is now `phone`; set `pc` to hear them on the PC).
- Changed: the Windows installer can add a Windows Firewall rule (Private networks) so phones can connect, and removes it
  on uninstall. Releases and the build script are Windows-only; the macOS and Linux packaging scripts are gone.
- Changed: the updater treats `2.0.0` as newer than `2.0.0-beta.N`, and never offers a pre-release to a stable install.
- Fix: the server now reads the body of an upload before answering 401/403. Before, Windows reset the connection and a
  phone saw a network error instead of the real reason.
- Fix: `tests/test_redesign.py` no longer depends on today's date.
- Docs rewritten for the server + phone design (README, SECURITY, BUILDING, API); REMOTE and SHARING marked as advanced.

### Earlier in the 2.0 work
- New: **phone API v1** (`/api/v1/...`, described in `docs/api/openapi.json`). A phone pairs once with a one-time code
  (shown as a QR link by the server) and then uses a per-device bearer token. Tokens are stored hashed in
  `devices.json`, can be revoked per device, and wrong codes are rate limited. The browser remote keeps working.
- New: `POST /api/v1/career` and `GET /api/v1/career/options` create a career from the phone (no desktop wizard needed).
- New: **`skydispatch-server`**, a headless server with no window; prints the address and a pairing code.
- New: optional LAN announcement (`pip install skydispatch[discovery]`) so a phone can find the server.
- New: **voice on the phone**. Set `speech_output` to `phone` and the server sends `speech` events plus downloadable audio
  (`GET /api/v1/voice/audio/<id>`, the characters' own Piper voices) instead of playing on the PC;
  `POST /api/v1/voice/transcribe` turns a recording from the phone into text (and can send it into a conversation).
  The default stays `pc`. See `docs/API.md`.
- Changed: **the Windows app is now a server.** The desktop gameplay screens (dashboard, job board, messenger, flight,
  hangar, logbook, finances, training) and the setup wizard are gone; you play from the phone app. The window is a small
  control panel with **Status** (what is running), **Phones** (pairing QR code, paired phones, remove), **Settings** and
  **Logs**. Closing it keeps SkyDispatch running in the tray; *Start with Windows* is an option in Settings > General.
  The phone API switches itself on the first time the server edition starts. Settings, career and backups are unchanged.
- Removed: the global push-to-talk hotkey (`pynput`); hold-to-talk now happens on the phone.
- New dependency: `segno` (draws the QR code).
- Internal: the backend runs without Qt (`server/engine.py`); the desktop `AppContext` is a thin Qt layer on top of it.
- CI: macOS dropped from the test matrix (releases are Windows-only); `numpy` added to the test dependencies.

## 1.4.3
- Fix: **fuel was not loaded into the sim aircraft** (payload was). MSFS ignores writes to a tank's quantity on some
  aircraft such as the C172, so fuel is now set by tank level, falling back to quantity on the retry.
- New: **End flight** button (Flight page, desktop and web remote). If arrival was not detected because the engines were
  left running or the brake was off, it finishes the flight and settles it: pay, costs, logbook, hangar. A flight that
  never landed is logged as aborted; it is refused while the aircraft is still in the air.

## 1.4.2
- Fix: the update dialog (and the quit warning, and accepting a job) wrongly said "Flight in progress" right after
  landing. The recorder begins a new log as soon as the engines are running and the aircraft rolls or the brake is off,
  for example while taxiing to the stand. A recording now only counts as a flight once the aircraft has actually been
  airborne.
- Loading fuel and payload is likewise no longer refused just because that recording has begun on the ground, and the
  fuel-used baseline restarts from the loaded quantity.

## 1.4.1
- **PMDG 777F and 777-200LR** (B77F, B77L) are in the aircraft list, detected from your PMDG packages, flown by Atlas
  Global, and use the right SimBrief codes. The freighter only gets cargo work. PMDG aircraft manage their own fuel and
  payload in their EFB or CDU, so SkyDispatch does not write to them; it tells you what to enter there.
- **Loading fuel and payload into the sim is more reliable and honest:** it no longer refuses when the engines are running,
  waits a few seconds for MSFS to settle, checks that the sim actually accepted the load (retrying once, and reloading if
  MSFS undoes it), and warns when it only partly worked. When it is waiting it says why. Everything is now logged.
- **Help > Check sim loadout (diagnostics)** reads the tanks, payload stations and weights from the live sim (it writes
  nothing) for troubleshooting.

## 1.4.0
- **The career now has a point to the money.** Being employed no longer means you pay for nothing:
  - **Training:** a new Training page. Your licence (a year) and medical (90 days) must be current to fly contracts and
    cost money to renew. Ratings (instrument, multi-engine, turboprop, jet, airline) are earned by paying for a course
    that takes real days; companies and your own aircraft require them.
  - **Monthly bills** every 30 days: living costs by rank, plus hangar and insurance for each aircraft you own. At most
    two months are charged after a long break. Dashboard alerts warn you about expiries and bills.
  - Pilots from earlier versions keep the ratings they already use. Starter aircraft and starting experience bring theirs.
- **Companies recruit in bursts.** You can only apply while a company has a vacancy open (random, a few days each); a
  rejection locks you out of that company for two weeks. The Job Board shows who is recruiting, and your operations desk
  tells you when a company you qualify for opens one.
- **The dispatcher assigns your flight.** Tell them how long you have and they roster one flight that fits: no accept or
  decline. Abandoning a rostered flight costs more reputation. You cannot be rostered with an expired licence or medical.
- **Freelance contracts are for owner-operators** (pilots who own an aircraft).
- **Company pay is an hourly rate** that rises with the company's tier, instead of cargo and passenger revenue (an
  airline flight used to pay tens of thousands).
- **Airlines have ICAO codes and flight numbers** (for example BBA214). The same route always has the same number, and
  SimBrief's dispatch page is prefilled with the airline code and flight number.

## 1.3.0
- **Installed aircraft detection fixed** for the Microsoft Store/Xbox install of MSFS 2024: packages behind the
  LocalCache junction are now found through the junction's real target, and AI-traffic packages (FSLTL and similar)
  no longer make every airliner look installed. When detection fails, the message says why.
- **SimBrief:** a Flight plan and loadout card (desktop and browser remote). Plan on SimBrief opens the dispatch page
  prefilled for your contract; Import plan fetches your latest plan by username or Pilot ID and shows the route,
  altitude, ETE and fuel. Settings > General holds your SimBrief username.
- **Fuel and payload sync:** load the sim aircraft with the fuel and payload SkyDispatch expects, by button or
  automatically once per job. It only runs with the aircraft parked and engines off, only when the sim aircraft is the
  contract's type, never changes the pilot's station, and reads the result back. Refuel to plan buys hangar fuel up to
  the SimBrief block fuel.
- **Voices:** every company now has its own named dispatcher with a different voice, and HR has its own. Speech
  speed no longer shifts the pitch. If a character's voice is not downloaded, people still get different voices.
  A one-time prompt (and Settings > Voice > Download character voices) fetches the voices you need.
- **Speech follows what you are looking at:** a person only speaks while you are viewing their conversation (in the
  app or the browser remote) and stops when you switch. The copilot also speaks during a flight.
- Fix: speech recognition no longer fails on NVIDIA PCs without the CUDA libraries (CPU by default, optional GPU
  download).

## 1.2.2
- Fix: in-app updates on all-users (Program Files) installs rolled back and closed without updating. The updater now
  repeats the same kind of install (asking Windows for permission when needed), the installer waits for SkyDispatch
  to exit before replacing files, and it reopens the installed version if an update does not complete. The installer
  writes `installer.log` to the logs folder.
- Releases now ship the Windows installer only; Mac, Linux, tablet and phone users use the browser remote.
- Note: 1.2.0 and 1.2.1 cannot apply this update themselves; run the 1.2.2 installer once by hand.

## 1.2.1
- Fix: "Test microphone" failed with `cublas64_12.dll is not found` on PCs with an NVIDIA GPU. Speech recognition now
  uses the CPU unless the GPU libraries are installed, and falls back to the CPU if the GPU fails.
- Optional GPU speech recognition: Settings > Voice > GPU acceleration downloads the NVIDIA libraries once (~1.3 GB,
  checked against PyPI's SHA-256) into your data folder. The installer stays small.

## 1.2.0
- **Browser remote:** the Windows app can serve a web version of itself (Settings > Simulator > Browser remote).
  Open it from a Mac, iPad or phone to use every page as a remote control; voice, the sim and the AI keep running
  on the PC, including push-to-talk through the PC's microphone. Access-code login, live updates, no extra
  install. See docs/REMOTE.md.
- **Updater:** SkyDispatch checks GitHub for new releases at startup (switchable) and from Help > Check for updates.
  On Windows it downloads the installer over HTTPS, verifies it and upgrades in place, keeping your career.

## 1.1.2
- Fix: installed-aircraft detection now understands the MSFS 2024 folder layout (Community2024, Official2020,
  Official2024), so Store installs are no longer reported as "unknown".
- Fix: the MSFS packages folder picker can now open the Store install's LocalCache folder (the native dialog
  refused it with "untrusted mount point").

## 1.1.1
- Fix: microphone/speaker names that exist under several Windows audio APIs (e.g. a headset listed as MME, DirectSound
  and WASAPI) no longer fail with "Multiple input devices found"; the default API's device is used.
- App version now reports 1.1.1.
- **Sharing is built into the Windows app** (Settings > Simulator). The separate SkyDispatch Bridge is no longer
  installed; Mac/Linux connect to the Windows PC running MSFS. The `skydispatch-bridge` command remains for headless use.
- Fix: the Windows build now bundles SimConnect.dll, so live MSFS data works from the installer.
- **Job Board:** ten companies on a career ladder. Applications are decided on total time, recent experience, skill
  level and hours on the company's aircraft class, with a requirement checklist and an application history.
- **Recent experience** decays with every day without flying (45-day half-life, configurable). Skill rating follows
  your flight scores. Dashboard shows both.
- **Messenger** replaces the dispatcher chat: a thread per employer plus your operations desk. Dispatchers ask how
  long you have free and create flights that fit, as Accept/Decline cards. New AI tools: set_availability,
  offer_flights, list_employers, apply_to_employer, get_qualifications.
- **Copilot** on the Flight tab: AI advice from live telemetry, phase checklists, fuel/descent/approach/weather
  helpers, proactive callouts, two personalities, works offline, push-to-talk routing during flights.
- **Installed aircraft:** jobs, company flights, the dealer and starter choices only use aircraft installed in the
  sim (automatic detection of MSFS 2020/2024 packages, reported by the Bridge for remote setups, or manual choice).
- Starting experience presets in the setup wizard; pilot location tracked between flights.
- Database migration 2 (existing careers upgrade in place).

## 1.0.0
- Career engine: job market (passenger, cargo, VIP charter, medevac, mail), contracts with deadlines and payouts,
  reputation, XP and ranks.
- Live MSFS 2020/2024 telemetry via SimConnect (Windows), a network Bridge for macOS/Linux, and a built-in
  simulated flight engine for demos and tests.
- Automatic flight recording: phases, takeoff/landing, touchdown rate, G-force, overspeed, fuel, distance, slew
  detection; every aircraft you fly is logged, with or without a contract.
- Hangar: buy new/used aircraft, refuel, inspect, repair, sell; wear and damage from rough flying.
- AI dispatcher through a local LM Studio server (or any OpenAI-compatible endpoint): tool-calling agent,
  four personalities, proactive radio calls, briefings, debriefs, AI-written contracts.
- Voice: push-to-talk speech recognition (faster-whisper) and neural text-to-speech (Piper) with OS-voice fallback.
- Desktop app for Windows, macOS and Linux with guided first-run setup, settings, backup/restore and light/dark themes.
- Installers: Inno Setup (Windows), DMG + PKG (macOS), DEB + AppImage (Linux), each with uninstall support.

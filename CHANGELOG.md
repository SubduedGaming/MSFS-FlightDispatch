# Changelog

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

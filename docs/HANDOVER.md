# Handover: SkyDispatch server + Android app

Written for the next agent (or person) picking this up. Read this first, then `docs/API.md` and `docs/api/openapi.json`.
State: version **2.0.0-beta.2**, all milestones M1 to M6 built; it is a pre-release because several things have only been
run in an emulator or in tests (section 5).

## 1. The goal

The owner asked for this (their words, lightly shortened):

> I want the Windows app to become server-only. Then I want an Android app that connects to the Windows server. The
> Windows server still needs a GUI to manage the server/settings, but control and use is through the mobile/Android app.

Decisions the owner made (do not re-litigate them):

| Question | Decision |
|---|---|
| How to build the Android app | Native **Kotlin + Jetpack Compose** |
| Old desktop gameplay pages | **Deleted**; only a small admin window remains |
| Voice | Happens **on the phone** (phone mic in, phone speaker out); recognition and voices run on the PC |
| Network | **Home Wi-Fi/LAN only**, QR pairing, plain HTTP (VPN/Tailscale works but is not built for) |
| QR code in the admin window | The small `segno` package |
| macOS / Linux | Dropped. Releases are Windows + Android only |
| GitHub Actions / CI | **No CI, ever.** Actions are disabled and `.github/workflows/` removed. Do not add workflows back or wait for CI. Tests run locally. |

About the owner: they describe themselves as a somewhat beginner whose strongest language is Python; Kotlin is new to them.
Explain plainly. They interrupt long-running foreground commands, so keep foreground commands short (under about two
minutes) and run long test runs in the background. Ask before doing anything outward-facing they did not request
(pushing, releases, PRs).

## 2. Status by milestone

| | What | State |
|---|---|---|
| M1 | Qt-free backend (`Engine`, event bus, single engine thread) | Done |
| M2 | Phone API v1: QR pairing, per-device tokens, career setup, `skydispatch-server`, OpenAPI contract, optional mDNS | Done |
| M3 | Voice on the phone: `speech` events, audio download, transcribe | Done |
| M4 | Windows app is a server with a small admin window | Done; admin window never run by hand on real Windows (section 5) |
| M5 | Android app | Done (all screens, live stream, voice, notifications, service); tested in an emulator only |
| M6 | Packaging, docs | Done: Windows-only build, firewall rule in the installer, docs rewritten. Version is still a beta |

Pre-release `v2.0.0-beta.1` is on GitHub (Windows installer + APK). Later commits (Android screens, beta.2) are local until
the owner says to push and release.

## 3. How the system fits together

```
 Android app (android/) ──HTTP + SSE──►  web/server.py  (stdlib http.server, one thread per request)
                                              │  marshals every API call onto ONE engine thread
                                              ▼
                                  server/engine.py  Engine  ◄── sim thread (SimConnect / simulated)
                                  career, db, dispatcher (AI), copilot, voice, pairing, speech store
                                              ▲
               ui/context.py  AppContext(Engine, QObject)  ◄── server_gui/  (admin window, Qt)
               server/cli.py  `skydispatch-server` (no Qt at all)
```

Key rule: **the career, database and AI objects are only touched from the engine thread** (`SerialExecutor` in
`core/executor.py`; `ctx.main.run(fn)` / `ctx.main.post(fn)`). Slow work goes through `Engine.run_async(...)`. HTTP handlers
that are slow (audio render, transcription) run on the request thread and must not touch career state.

### Python side
| Path | Purpose |
|---|---|
| `core/events.py`, `executor.py`, `fmt.py` | `EventBus`, `SerialExecutor`, display formatting |
| `server/engine.py` | `Engine`: all non-visual orchestration. Plain Python. |
| `server/pairing.py`, `speech.py`, `discovery.py`, `cli.py` | one-time codes and device tokens; recent utterances for the phone; optional mDNS; headless entry point |
| `web/server.py`, `web/api.py` | HTTP server (bearer + cookie auth, SSE stream, audio, transcribe) and the ~57 JSON routes |
| `server_gui/` | The admin window: Status, Phones (QR, devices), Settings, Logs, tray, start with Windows |
| `resources/web/` | The old browser page, still served |
| `docs/api/openapi.json` | The contract; a test keeps it in step with the server |

### Android side (`android/app/src/main/java/app/skydispatch/`)
| File | Purpose |
|---|---|
| `Api.kt` | OkHttp client: reads (retried once), writes, SSE `stream()` (emits a synthetic `open` event when connected), audio download, transcribe. Refuses non-private hosts. |
| `Hub.kt` | Process-wide singleton: owns the stream, reconnects with backoff, bumps `version` so screens refetch, `live` = latest `state` event, tells the PC which conversation is on screen (`view`) |
| `Ui.kt` | `Loader` (fetch + refetch on `Hub.version`, auto-retry), `rememberAct` (run a call, toast on error), shared widgets |
| `Json.kt` | Lenient readers (`str`, `int`, `list`...): the server's JSON is display-shaped and screens must not crash on a missing field |
| `MainActivity.kt` | App/gate (pair, career, ready), `Shell` (bottom tabs + back stack of `Dest`), pairing screen, QR scan (Google code scanner) |
| `HomeScreens.kt`, `JobsScreens.kt`, `MessagesScreens.kt`, `MoreScreens.kt`, `CareerScreen.kt`, `Chat.kt` | The screens |
| `Speaker.kt`, `Recorder.kt` | Plays `speech` events (WAV via MediaPlayer, else Android TTS); records 16 kHz mono WAV |
| `Notify.kt` | Message notifications while the app is hidden; `FlightService` keeps the process alive during a job |

## 4. How to run things

```bash
pip install -e ".[dev,voice,sim,discovery]"
python -m pytest -q                 # 398 tests, about 2.5 minutes; run in the background
python -m skydispatch.server.cli --port 18766 --sim simulated   # headless server; prints a pairing code and link
cd android && gradlew testDebugUnitTest assembleDebug            # 12 Android unit tests + the APK
```

The dev toolchain on the owner's PC lives in `C:\Users\George\android-dev` (JDK 17, SDK, Gradle, an AVD called `pixel`;
`ANDROID_HOME` and `JAVA_HOME` are not set globally, set them per shell). `android/local.properties` is not committed.

**Testing the app in the emulator against a real server** (this is how it was verified):
1. Start the emulator: `emulator -avd pixel -no-snapshot -gpu swiftshader_indirect` (acceleration is WHPX).
2. Start the server with a throwaway data folder: `SKYDISPATCH_HOME=C:\tmp\sd_test python -m skydispatch.server.cli --port 18766 --sim simulated`
   (use `PYTHONUNBUFFERED=1` or the pairing code is not printed).
3. `adb install -r app-debug.apk`, then open the link the server prints with
   `adb shell am start -a android.intent.action.VIEW -d "skydispatch://pair?host=<PC LAN ip>&port=18766&code=<code>" app.skydispatch`
   (the emulator reaches the PC's LAN address; `10.0.2.2` also works if typed by hand).
4. Drive and read the UI with `adb shell input tap/text`, `adb exec-out screencap -p`, and `uiautomator dump` to find buttons by text.
5. To test spoken replies without an AI model, run a script that builds an `Engine`, stubs `render_utterance` and emits
   `engine.speech` events on a timer (done during development; `dumpsys audio` shows the MediaPlayer/AudioTrack state).

## 5. What is and is not verified

Verified:
- Full Python suite on Python 3.12, Windows: all pass.
- Android in the emulator against the real server: pairing (deep link, real LAN address), revoked-token handling,
  reconnect after a server restart, career creation, job market, accepting a job, live flight numbers, plan/loadout,
  dispatcher chat (send/receive), hangar, dealer, logbook, finances, training, settings, notifications and the foreground
  service, spoken replies (WAV through MediaPlayer, text through Android TTS), hold-to-talk with real faster-whisper.
- The Windows installer builds (beta.1 and beta.2); it has never been installed and run.

NOT verified:
- The app on a physical phone; the QR scanner on a real camera (only the pairing link was tested).
- The installed Windows app: tray icon, *Start with Windows* registry key, high-DPI, the firewall task (built, never run).
- A real MSFS/SimConnect session; Piper neural voices; mDNS on a real network.
- Whisper understanding real speech (the emulator microphone is silent, so only "I didn't hear anything" was exercised).
- Flights being completed from the phone and settled (the demo flight was started and ran, not watched to the end).

## 6. Known problems and ideas

- Python 3.11 was not re-run; an interpreter-exit abort was seen once on CI Linux/3.11 before (not reproduced since).
- `tests/conftest.py` switches off automatic garbage collection and runs `gc.collect()` after every test: without it the Qt
  web tests segfault. Fixtures that build an `AppContext` must end with `ctx.shutdown()`. SSE tests must keep the response object.
- Adding or changing an API route: update `docs/api/openapi.json` or `test_openapi_contract.py` fails.
- `Settings.load` ignores list fields, which is why paired devices live in `devices.json`.
- Android: no app icon yet (system default); the Messages notification uses a system icon; SpeechRecognizer fallback for a PC without
  recognition is not built (the app tells you to type). Map/route drawing is not built. The Gameplay settings tab (difficulty,
  job count) is still on the PC only.
- The Android Gradle cache and `android/local.properties` are per-machine.
- Optional hardening: self-signed HTTPS with certificate pinning (the connection is plain HTTP).

## 7. Working agreements and cautions

- **No fallbacks (owner's rule).** A feature either works or fails visibly with the real error: no canned replies, no
  "closest alternative" data, no silent retries with other settings. The co-pilot follows this. The **dispatcher still has
  fallbacks** from the original design (template job briefings, debriefs and event comments in `ai/dispatcher.py` when the
  model fails, plus a stock "got it" reply); the owner has not yet decided whether to remove them.
- Reasoning models: the AI client sends `reasoning_effort` (default `none`) so models like Qwen3.5 do not spend the reply on thinking.

- **No CI.** Do not add workflows back; the GitHub integration cannot cancel runs.
- Commit messages end with the co-author trailer already used on the branch. Git identity is not configured on the owner's
  PC: commits use `-c user.name=SubduedGaming -c user.email=76713813+SubduedGaming@users.noreply.github.com`.
- Do not rewrite history. Ask before pushing, releasing, or opening/merging/closing pull requests.
- Releases are built by hand: `python packaging/build.py` (Windows installer, needs Inno Setup 6), `gradlew assembleDebug`
  (APK, debug-signed), then `gh release create vX --prerelease ...`.

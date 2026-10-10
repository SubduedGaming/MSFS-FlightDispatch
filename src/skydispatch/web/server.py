"""HTTP server for the browser remote: static page, JSON API and a Server-Sent Events stream for live updates.

Security model: the page and script are public (they hold no data); everything under /api needs the session cookie
that ``POST /api/login`` sets after the access code is typed once. The cookie is HttpOnly + SameSite=Strict, writes
need a custom header, and wrong codes are rate limited. Nothing here ever touches the sim or database off the GUI
thread: requests are marshalled onto the engine thread (``ctx.main``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import queue
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .. import __version__
from ..server.pairing import PairingError
from ..voice.audio import AudioError, wav_to_mono16k
from .api import Csv, RemoteApi

log = logging.getLogger("skydispatch.web")

API_VERSION = 1
V1 = "/api/v1/"
MAX_BODY = 64 * 1024
MAX_AUDIO = 2 * 1024 * 1024         # a recorded voice clip (about a minute of 16 kHz speech)
AUDIO_ID = re.compile(r"^/api/voice/audio/([0-9a-f]{8,32})$")
THREAD_RX = re.compile(r"^(general|copilot|employer:[a-z0-9_-]{1,40})$")
MAX_STREAMS = 16
COOKIE = "sd_session"
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def web_root() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for cand in (base / "resources" / "web", base / "skydispatch" / "resources" / "web"):
        if cand.is_dir():
            return cand
    return base / "resources" / "web"


def session_value(token: str) -> str:
    """Stateless session id: changes when the access code changes, which signs every device out."""
    return hmac.new(token.encode(), b"skydispatch-remote-session", hashlib.sha256).hexdigest()


class Hub:
    """Fans server-sent events out to every connected browser. ``publish`` is safe from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue | None:
        with self._lock:
            if len(self._clients) >= MAX_STREAMS:
                return None
            q: queue.Queue = queue.Queue(maxsize=256)
            self._clients.add(q)
            return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: str, data: dict) -> None:
        payload = (event, json.dumps(data, separators=(",", ":"), default=str))
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass                                # a stalled browser just misses updates

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # See sim/bridge_server.py: on Windows SO_REUSEADDR lets a second server steal a live port.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Handler(BaseHTTPRequestHandler):
    remote: "WebRemote"
    server_version = "SkyDispatchRemote"
    sys_version = ""
    timeout = 30                  # a stalled client can't hold a worker thread forever

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---------------------------------------------------------------- plumbing
    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj, extra: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store", **(extra or {})})

    def _bearer_device(self):
        """The paired phone whose device token is in the Authorization header, or None."""
        header = self.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        pairing = getattr(self.remote.ctx, "pairing", None)
        return pairing.authenticate(value.strip()) if pairing else None

    def _authed(self) -> bool:
        if self._bearer_device() is not None:
            return True
        token = self.remote.token
        if not token:
            return False                              # an unset access code never lets anyone in
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value, session_value(token)):
                return True
        return False

    def _read_body(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > MAX_BODY:
            return None
        if n == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(n))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ---------------------------------------------------------------- verbs
    @staticmethod
    def _v1(path: str) -> str:
        """The app's /api/v1/... is the same API as the browser's /api/..."""
        return "/api/" + path[len(V1):] if path.startswith(V1) else path

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path, query = self._v1(url.path), dict(parse_qsl(url.query))
        if not path.startswith("/api/"):
            return self._static(path)
        if path == "/api/ping":
            return self._json(200, {"app": "SkyDispatch", "version": __version__, "api": API_VERSION,
                                    "authed": self._authed()})
        if not self._authed():
            return self._json(401, {"error": "Sign in with your access code."})
        if path == "/api/stream":
            return self._stream(query.get("v", ""))
        m = AUDIO_ID.match(path)
        if m:
            return self._audio(m.group(1))
        self._api("GET", path, query, None)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        path = self._v1(url.path)
        device = self._bearer_device()
        # Browsers send cookies on their own, so cookie requests must prove they are ours with a custom header; an
        # explicit Authorization header cannot be forged by another website.
        if device is None and self.headers.get("X-SkyDispatch") != "1":
            return self._json(403, {"error": "Forbidden"})
        if path == "/api/voice/transcribe":                # a recording, not JSON
            if device is None and not self._authed():
                return self._json(401, {"error": "Sign in with your access code."})
            return self._transcribe(dict(parse_qsl(url.query)))
        body = self._read_body()
        if body is None:
            return self._json(400, {"error": "Invalid request body"})
        if path == "/api/login":
            return self._login(body)
        if path == "/api/pair":
            return self._pair(body)
        if path == "/api/logout":
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
        if device is None and not self._authed():
            return self._json(401, {"error": "Sign in with your access code."})
        if path == "/api/unpair":
            if device is None:
                return self._json(400, {"error": "Only a paired phone can unpair itself."})
            self.remote.ctx.pairing.revoke(device.id)
            return self._json(200, {"ok": True})
        self._api("POST", path, dict(parse_qsl(url.query)), body)

    # ---------------------------------------------------------------- handlers
    def _login(self, body: dict) -> None:
        ip = self.client_address[0]
        wait = self.remote.lockout_remaining(ip)
        if wait > 0:
            return self._json(429, {"error": f"Too many wrong codes. Try again in {wait:.0f} s."})
        token = self.remote.token
        given = str(body.get("token", "")).strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            self.remote.note_failure(ip)
            log.warning("Rejected remote sign-in from %s", ip)
            return self._json(401, {"error": "That access code is not right."})
        self.remote.clear_failures(ip)
        cookie = f"{COOKIE}={session_value(token)}; Path=/; Max-Age={30 * 86400}; HttpOnly; SameSite=Strict"
        self._json(200, {"ok": True}, {"Set-Cookie": cookie})

    def _audio(self, utterance_id: str) -> None:
        """The spoken audio for a `speech` event. Rendered here, off the engine thread, because it takes a while."""
        ctx = self.remote.ctx
        utt = ctx.speech_store.get(utterance_id)
        if utt is None:
            return self._json(404, {"error": "That speech is no longer available."})
        try:
            wav = ctx.render_utterance(utt)
        except Exception as exc:
            log.exception("could not render speech")
            return self._json(500, {"error": f"Could not make the audio: {exc}"})
        if not wav:
            return self._json(503, {"error": "This PC cannot make audio for that voice. Speak the text on the phone."})
        self._send(200, wav, "audio/wav", {"Cache-Control": "no-store"})

    def _transcribe(self, query: dict) -> None:
        """Turn a recorded clip (WAV) into text, and optionally say it in a conversation (?send=<thread>)."""
        ctx = self.remote.ctx
        send = query.get("send", "")
        if send and not THREAD_RX.match(send):
            return self._json(400, {"error": "Unknown conversation."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0:
            return self._json(400, {"error": "Send the recording as the request body."})
        if n > MAX_AUDIO:
            return self._json(413, {"error": "That recording is too long."})
        ok, msg = ctx.voice.stt_status()
        if not ok or not ctx.settings.voice.stt_enabled:
            return self._json(503, {"error": msg if not ok else "Speech recognition is turned off on the PC."})
        try:
            audio = wav_to_mono16k(self.rfile.read(n))
        except AudioError as exc:
            return self._json(400, {"error": str(exc)})
        try:
            text = ctx.voice.stt.transcribe(audio)
        except Exception as exc:
            log.exception("transcription failed")
            return self._json(500, {"error": f"Speech recognition failed: {exc}"})
        if not text:
            return self._json(422, {"error": "I didn't hear anything. Try again."})
        if send:
            ctx.main.post(lambda: ctx.submit_player_text(text, send))
        self._json(200, {"text": text, "sent": bool(send)})

    def _pair(self, body: dict) -> None:
        ip = self.client_address[0]
        wait = self.remote.lockout_remaining(ip)
        if wait > 0:
            return self._json(429, {"error": f"Too many wrong codes. Try again in {wait:.0f} s."})
        try:
            device, token = self.remote.ctx.pairing.redeem(str(body.get("code", "")), str(body.get("device_name", "")))
        except PairingError as exc:
            self.remote.note_failure(ip)
            log.warning("Rejected pairing request from %s", ip)
            return self._json(401, {"error": str(exc)})
        self.remote.clear_failures(ip)
        self._json(200, {"token": token, "device_id": device.id, "app": "SkyDispatch", "version": __version__,
                         "api": API_VERSION})

    def _api(self, method: str, path: str, query: dict, body: dict | None) -> None:
        try:
            status, payload = self.remote.main.run(lambda: self.remote.api.handle(method, path, query, body))
        except TimeoutError as exc:
            return self._json(503, {"error": str(exc)})
        except Exception as exc:
            log.exception("remote request failed")
            return self._json(500, {"error": str(exc)})
        if isinstance(payload, Csv):
            return self._send(status, payload.encode("utf-8"), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="logbook.csv"', "Cache-Control": "no-store"})
        self._json(status, payload)

    def _stream(self, viewer: str = "") -> None:
        q = self.remote.hub.subscribe()
        if q is None:
            return self._json(503, {"error": "Too many open browser windows."})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not self.remote.stopping:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if item is None:
                    break
                event, data = item
                self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
                self.wfile.flush()
        except OSError:
            pass                                      # browser went away
        finally:
            self.remote.hub.unsubscribe(q)
            if viewer:
                self.remote.viewer_gone(viewer)           # a closed browser tab is no longer listening

    def _static(self, path: str) -> None:
        names = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                 "/favicon.svg": "favicon.svg"}
        name = names.get(path)
        f = web_root() / name if name else None
        if not f or not f.is_file():
            return self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".js"):
            ctype = "text/javascript"
        self._send(200, f.read_bytes(), f"{ctype}; charset=utf-8" if ctype.startswith("text") else ctype,
                   {"Cache-Control": "no-cache"})


class WebRemote:
    """Owns the HTTP server. Create it once; set host/port/token and start/stop as often as needed."""

    def __init__(self, ctx, host: str = "0.0.0.0", port: int = 0, token: str = ""):
        self.ctx, self.host, self.port, self.token = ctx, host, port, token
        self.hub = Hub()
        self.main = ctx.main                  # the engine thread: .post(fn) and .run(fn, timeout)
        self.api = RemoteApi(ctx, self.hub.publish, self.main.post)
        self.stopping = False
        self._server: _Server | None = None
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def client_count(self) -> int:
        return self.hub.count

    def viewer_gone(self, viewer: str) -> None:
        self.main.post(lambda: self.api.clear_viewer(viewer))

    # ---- login rate limiting: 5 wrong codes inside a minute lock that address out for a minute
    def note_failure(self, ip: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._fails[ip] = [t for t in self._fails.get(ip, []) if now - t < 60] + [now]

    def clear_failures(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def lockout_remaining(self, ip: str) -> float:
        with self._lock:
            now = time.monotonic()
            recent = [t for t in self._fails.get(ip, []) if now - t < 60]
            return 60 - (now - recent[-1]) if len(recent) >= 5 else 0.0

    def start(self) -> None:
        """Raises OSError (for example when the port is already in use)."""
        if self._server:
            return
        self.stopping = False
        handler = type("Handler", (_Handler,), {"remote": self})
        self._server = _Server((self.host, self.port), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True, name="web-remote").start()
        log.info("Browser remote on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self.stopping = True
        self.hub.close()
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
            log.info("Stopped the browser remote")

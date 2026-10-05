from __future__ import annotations

import logging
import secrets
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import __version__
from ...ai.llm import LMStudioClient
from ...ai.personas import PERSONAS
from ...copilot.personas import COPILOTS
from ...sim.installed import parse_ids
from ..folder_picker import pick_packages_folder
from ...core import paths
from ...core.config import AISettings
from ...sim.simconnect_provider import simconnect_available
from ...voice import gpu as gpu_mod
from ...voice import stt as stt_mod
from ...voice import tts as tts_mod
from ...voice.hotkey import hotkey_available
from ..widgets import heading, muted
from ..dialogs import InstalledAircraftDialog
from ..workers import run_async
from .base import Page

log = logging.getLogger(__name__)

PIPER_VOICES = ["en_GB-alan-medium", "en_GB-jenny_dioco-medium", "en_GB-northern_english_male-medium",
                "en_US-ryan-medium", "en_US-amy-medium", "en_US-lessac-medium", "en_US-joe-medium"]
WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "small", "medium", "large-v3"]
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"


def _combo_value(c: QComboBox):
    """Data of the selected item; for editable boxes, typed text wins unless it matches an item."""
    if c.isEditable():
        text = c.currentText().strip()
        i = c.findText(text)
        if i >= 0 and c.itemData(i) is not None:
            return c.itemData(i)
        return text
    return c.currentData()


def _combo(items: list[tuple[str, str]], editable: bool = False) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for label, value in items:
        c.addItem(label, value)
    return c


class SettingsPage(Page):
    title = "Settings"
    run_wizard = Signal()
    theme_changed = Signal(str)
    progress = Signal(float)
    gpu_progress = Signal(float, str)

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(heading("Settings"))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._bindings: list[tuple] = []
        self.tabs.addTab(self._general(), "General")
        self.tabs.addTab(self._simulator(), "Simulator")
        self.tabs.addTab(self._ai(), "AI Dispatcher")
        self.tabs.addTab(self._voice(), "Voice")
        self.tabs.addTab(self._gameplay(), "Gameplay")
        self.tabs.addTab(self._data(), "Data and Backup")

        bar = QHBoxLayout()
        self.saved = muted("", wrap=False)
        revert = QPushButton("Revert")
        revert.clicked.connect(self.refresh)
        save = QPushButton("Save changes")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        bar.addWidget(self.saved)
        bar.addStretch(1)
        bar.addWidget(revert)
        bar.addWidget(save)
        root.addLayout(bar)
        self.progress.connect(lambda f: self.dl_bar.setValue(int(f * 100)))
        ctx.settings_changed.connect(self._update_installed_label)
        self.refresh()

    # --------------------------------------------------------- binding helper
    def _bind(self, widget, obj_name: str, field: str):
        self._bindings.append((widget, obj_name, field))
        return widget

    def _section(self, name):
        return getattr(self.settings, name)

    def refresh(self) -> None:
        pilot = self.db.pilot()
        if pilot:          # the career file is the source of truth for who you are
            self.settings.pilot.name, self.settings.pilot.callsign = pilot.name, pilot.callsign
            self.settings.pilot.home_icao = pilot.home_icao
        for w, sec, field in self._bindings:
            v = getattr(self._section(sec), field)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QComboBox):
                i = w.findData(v)
                if i >= 0:
                    w.setCurrentIndex(i)
                elif w.isEditable():
                    w.setEditText(str(v))
                else:
                    w.setCurrentIndex(0)
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(v)
            elif isinstance(w, QLineEdit):
                w.setText(str(v))
        self.saved.setText("")
        self._update_sim_visibility()
        self._update_installed_label()

    def save(self) -> None:
        old_sim = (self.settings.sim.mode, self.settings.sim.bridge_host, self.settings.sim.bridge_port,
                   self.settings.sim.bridge_token, self.settings.sim.share_enabled,
                   self.settings.sim.share_port, self.settings.sim.share_token)
        old_theme = self.settings.ui.theme
        old_remote = (self.settings.remote.enabled, self.settings.remote.port, self.settings.remote.token)
        for w, sec, field in self._bindings:
            obj = self._section(sec)
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, QComboBox):
                v = _combo_value(w)
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                v = w.value()
            else:
                v = w.text().strip()
            setattr(obj, field, v)
        p = self.settings.pilot
        p.home_icao = p.home_icao.upper()
        pilot = self.db.pilot()
        if pilot:
            self.db.update_pilot(name=p.name or pilot.name, callsign=p.callsign or pilot.callsign,
                                 home_icao=p.home_icao if self.db.airport(p.home_icao) else pilot.home_icao)
        self.ctx.apply_settings()
        new_sim = (self.settings.sim.mode, self.settings.sim.bridge_host, self.settings.sim.bridge_port,
                   self.settings.sim.bridge_token, self.settings.sim.share_enabled,
                   self.settings.sim.share_port, self.settings.sim.share_token)
        if new_sim != old_sim:
            self.ctx.start_sim()
        r = self.settings.remote
        if (r.enabled, r.port, r.token) != old_remote or (r.enabled and not (self.ctx.web and self.ctx.web.running)):
            self.ctx.start_web()
            self._update_remote_info()
        if self.settings.ui.theme != old_theme:
            self.theme_changed.emit(self.settings.ui.theme)
        self.ctx.check_ai()
        self.saved.setText("Saved " + datetime.now().strftime("%H:%M:%S"))
        self.ctx.toast.emit("good", "Settings saved")

    # ------------------------------------------------------------------ tabs
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)
        return w, lay

    def _general(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("Pilot")
        f = QFormLayout(g)
        f.addRow("Name:", self._bind(QLineEdit(), "pilot", "name"))
        f.addRow("Callsign:", self._bind(QLineEdit(), "pilot", "callsign"))
        home = self._bind(QLineEdit(), "pilot", "home_icao")
        home.setMaxLength(4)
        f.addRow("Home airport (ICAO):", home)
        lay.addWidget(g)
        gp = QGroupBox("Flight planning")
        fp = QFormLayout(gp)
        fp.addRow("SimBrief username or Pilot ID:", self._bind(QLineEdit(), "plan", "simbrief_user"))
        fp.addRow(self._bind(QCheckBox("Load fuel and payload into the sim automatically when the aircraft is "
                                       "parked with engines off"), "plan", "auto_sync_loadout"))
        fp.addRow(muted("Plans are fetched from SimBrief by name; no password is needed. SkyDispatch only changes "
                        "the aircraft's fuel and payload, only before the flight starts, and only when the sim "
                        "aircraft is the one your contract uses."))
        lay.addWidget(gp)
        gu = QGroupBox("Updates")
        fu = QFormLayout(gu)
        fu.addRow(self._bind(QCheckBox("Check for a newer version when SkyDispatch starts"), "ui", "check_updates"))
        ur = QHBoxLayout()
        chk = QPushButton("Check now")
        chk.clicked.connect(lambda: self.ctx.check_for_updates(manual=True))
        ur.addWidget(chk)
        ur.addWidget(muted(f"You have version {__version__}.", wrap=False), 1)
        fu.addRow(ur)
        lay.addWidget(gu)
        g2 = QGroupBox("Display")
        f2 = QFormLayout(g2)
        f2.addRow("Theme:", self._bind(_combo([("Dark", "dark"), ("Light", "light")]), "ui", "theme"))
        f2.addRow("Distance:", self._bind(_combo([("Nautical miles", "nm"), ("Kilometres", "km")]), "ui", "units_distance"))
        f2.addRow("Weight:", self._bind(_combo([("Pounds", "lb"), ("Kilograms", "kg")]), "ui", "units_weight"))
        f2.addRow("Currency symbol:", self._bind(_combo([("$", "$"), ("£", "£"), ("€", "€"),
                                                          ("¥", "¥")]), "ui", "currency"))
        lay.addWidget(g2)
        rw = QPushButton("Run setup wizard again...")
        rw.clicked.connect(self.run_wizard.emit)
        lay.addWidget(rw)
        lay.addStretch(1)
        return w

    def _simulator(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("Simulator connection")
        f = QFormLayout(g)
        modes = [("Simulated flight engine (demo, no sim needed)", "simulated"),
                 ("Microsoft Flight Simulator on this PC (Windows)", "simconnect"),
                 ("SkyDispatch on my Windows PC (network)", "bridge")]
        self.sim_mode = self._bind(_combo(modes), "sim", "mode")
        self.sim_mode.currentIndexChanged.connect(self._update_sim_visibility)
        f.addRow("Source:", self.sim_mode)
        self.bridge_host = self._bind(QLineEdit(), "sim", "bridge_host")
        self.bridge_port = self._bind(QSpinBox(), "sim", "bridge_port")
        self.bridge_port.setRange(1024, 65535)
        self.bridge_token = self._bind(QLineEdit(), "sim", "bridge_token")
        self.bridge_token.setEchoMode(QLineEdit.Password)
        self.sim_speed = self._bind(QDoubleSpinBox(), "sim", "simulated_speed")
        self.sim_speed.setRange(1, 60)
        self.sim_speed.setSuffix("x")
        self.rows = {"host": (QLabel("Windows PC address:"), self.bridge_host), "port": (QLabel("Windows PC port:"), self.bridge_port),
                     "token": (QLabel("Shared token:"), self.bridge_token), "speed": (QLabel("Demo time speed:"), self.sim_speed)}
        for lbl, wid in self.rows.values():
            f.addRow(lbl, wid)
        self.sim_note = muted("")
        f.addRow(self.sim_note)
        lay.addWidget(g)
        row = QHBoxLayout()
        self.sim_status = QLabel("")
        test = QPushButton("Apply and reconnect now")
        test.clicked.connect(self._reconnect_sim)
        row.addWidget(test)
        row.addWidget(self.sim_status, 1)
        lay.addLayout(row)
        if sys.platform == "win32":
            lay.addWidget(self._share_group())
            lay.addWidget(self._remote_group())
        else:
            lay.addWidget(muted(
                "MSFS runs on Windows. To use this computer for your career, open SkyDispatch on the Windows PC, "
                "turn on 'Share flight data' in Settings > Simulator, then choose 'SkyDispatch on my Windows PC' "
                "here with its address and token."))
        self.ctx.sim_status.connect(lambda st, msg: self.sim_status.setText(f"{st}: {msg}"))
        g2 = QGroupBox("Aircraft installed in your simulator")
        f2 = QFormLayout(g2)
        f2.addRow(self._bind(QCheckBox("Only give me jobs and company flights in aircraft I have installed"), "sim",
                             "restrict_to_installed"))
        self.installed_lbl = QLabel("")
        self.installed_lbl.setWordWrap(True)
        f2.addRow(self.installed_lbl)
        path_row = QHBoxLayout()
        path_row.addWidget(self._bind(QLineEdit(), "sim", "packages_path"), 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_packages)
        path_row.addWidget(browse)
        f2.addRow("MSFS packages folder (optional):", path_row)
        btns = QHBoxLayout()
        detect = QPushButton("Detect installed aircraft")
        detect.clicked.connect(self._detect_installed)
        manual = QPushButton("Choose manually...")
        manual.clicked.connect(self._choose_installed)
        btns.addWidget(detect)
        btns.addWidget(manual)
        btns.addStretch(1)
        f2.addRow(btns)
        lay.addWidget(g2)
        lay.addWidget(muted("Detection reads your MSFS packages folder (Community and Official). When connected to your Windows PC, "
                            "it reports what it has installed. Aircraft you have flown also count as installed."))
        lay.addStretch(1)
        return w

    def _share_group(self) -> QWidget:
        g = QGroupBox("Share flight data with other computers")
        f = QFormLayout(g)
        f.addRow(self._bind(QCheckBox("Let SkyDispatch on my Mac/Linux/other PC connect to this one"), "sim",
                            "share_enabled"))
        self.share_port = self._bind(QSpinBox(), "sim", "share_port")
        self.share_port.setRange(1024, 65535)
        self.share_token = self._bind(QLineEdit(), "sim", "share_token")
        trow = QHBoxLayout()
        trow.addWidget(self.share_token, 1)
        gen = QPushButton("Generate")
        gen.clicked.connect(lambda: self.share_token.setText(secrets.token_urlsafe(12)))
        trow.addWidget(gen)
        f.addRow("Port:", self.share_port)
        f.addRow("Shared token:", trow)
        self.share_info = muted("")
        f.addRow(self.share_info)
        f.addRow(muted("Only share on a network you trust. Windows may ask to allow SkyDispatch through the "
                       "firewall the first time; allow it for Private networks."))
        self.ctx.sim_status.connect(lambda *_: self._update_share_info())
        self._update_share_info()
        return g

    def _remote_group(self) -> QWidget:
        g = QGroupBox("Browser remote (use SkyDispatch from a Mac, tablet or phone)")
        f = QFormLayout(g)
        f.addRow(self._bind(QCheckBox("Serve the web remote from this PC"), "remote", "enabled"))
        self.remote_port = self._bind(QSpinBox(), "remote", "port")
        self.remote_port.setRange(1024, 65535)
        self.remote_token = self._bind(QLineEdit(), "remote", "token")
        trow = QHBoxLayout()
        trow.addWidget(self.remote_token, 1)
        gen = QPushButton("Generate")
        gen.clicked.connect(lambda: self.remote_token.setText(secrets.token_urlsafe(9)))
        trow.addWidget(gen)
        f.addRow("Port:", self.remote_port)
        f.addRow("Access code:", trow)
        self.remote_info = muted("")
        f.addRow(self.remote_info)
        open_btn = QPushButton("Open on this PC")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"http://127.0.0.1:{self.settings.remote.port}/")))
        f.addRow(open_btn)
        f.addRow(muted("Open the address in a browser on your Mac and type the access code once. Voice, the sim and "
                       "the AI all keep running on this PC; the browser is only a remote control. Only enable this "
                       "on a network you trust. Changing the access code signs every device out."))
        self.ctx.settings_changed.connect(self._update_remote_info)
        self._update_remote_info()
        return g

    def _update_remote_info(self) -> None:
        if self.ctx.closed or not hasattr(self, "remote_info"):
            return
        web = self.ctx.web
        if web is not None and web.running:
            from ...sim.bridge_server import local_addresses
            addrs = "  or  ".join(f"http://{a}:{self.settings.remote.port}/" for a in local_addresses())
            self.remote_info.setText(f"On. Open {addrs} on your other device. Browser windows open: {web.client_count}.")
        else:
            self.remote_info.setText(self.ctx.web_error or "Off.")

    def _update_share_info(self) -> None:
        if self.ctx.closed:
            return
        host = self.ctx.share_host
        if host:
            from ...sim.bridge_server import local_addresses
            addrs = ", ".join(f"{a}:{self.settings.sim.share_port}" for a in local_addresses()) or "no network found"
            self.share_info.setText(f"Sharing is on. Connect from other computers to {addrs}. "
                                    f"Connected computers: {host.client_count}.")
        else:
            self.share_info.setText(self.ctx.share_error or "Sharing is off.")

    def _browse_packages(self) -> None:
        path = pick_packages_folder(self, "MSFS packages folder (contains Community and Official)",
                                    self.settings.sim.packages_path)
        if path:
            self.settings.sim.packages_path = path
            self.refresh()

    def _detect_installed(self) -> None:
        for w, sec, field in self._bindings:           # take the typed path without requiring Save first
            if (sec, field) == ("sim", "packages_path"):
                self.settings.sim.packages_path = w.text().strip()
        self.settings.sim.installed_auto = True
        self.ctx.detect_installed(force=True)

    def _choose_installed(self) -> None:
        InstalledAircraftDialog(self.ctx, self).exec()
        self.refresh()

    def _update_installed_label(self) -> None:
        ids = parse_ids(self.settings.sim.installed_aircraft)
        if not ids:
            text = "Installed aircraft: unknown, so jobs are not restricted."
        else:
            from ...data.aircraft import get_type
            names = ", ".join(get_type(i).name for i in sorted(ids) if get_type(i))
            text = f"Installed aircraft ({len(ids)}): {names}"
        mode = "detected automatically" if self.settings.sim.installed_auto else "chosen manually"
        self.installed_lbl.setText(text + (f"  [{mode}]" if ids else ""))

    def _update_sim_visibility(self) -> None:
        mode = self.sim_mode.currentData()
        show = {"host": mode == "bridge", "port": mode == "bridge", "token": mode == "bridge",
                "speed": mode == "simulated"}
        for key, (lbl, wid) in self.rows.items():
            lbl.setVisible(show[key])
            wid.setVisible(show[key])
        if mode == "simconnect":
            ok = simconnect_available()
            self.sim_note.setText("SimConnect package found." if ok else
                                  ("SimConnect is Windows-only. Use Bridge on this platform." if sys.platform != "win32"
                                   else "Python 'SimConnect' package missing: pip install SimConnect"))
        else:
            self.sim_note.setText("")

    def _reconnect_sim(self) -> None:
        self.save()

    def _ai(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("LM Studio / OpenAI-compatible server")
        f = QFormLayout(g)
        f.addRow("Server URL:", self._bind(QLineEdit(), "ai", "base_url"))
        key = self._bind(QLineEdit(), "ai", "api_key")
        key.setEchoMode(QLineEdit.Password)
        f.addRow("API key (optional):", key)
        self.model = self._bind(_combo([("Whichever model is loaded", "")], editable=True), "ai", "model")
        row = QHBoxLayout()
        row.addWidget(self.model, 1)
        b = QPushButton("Refresh list")
        b.clicked.connect(self._list_models)
        row.addWidget(b)
        f.addRow("Model:", row)
        temp = self._bind(QDoubleSpinBox(), "ai", "temperature")
        temp.setRange(0, 2)
        temp.setSingleStep(0.1)
        f.addRow("Creativity (temperature):", temp)
        mt = self._bind(QSpinBox(), "ai", "max_tokens")
        mt.setRange(64, 4000)
        f.addRow("Max reply length (tokens):", mt)
        to = self._bind(QDoubleSpinBox(), "ai", "timeout_s")
        to.setRange(5, 600)
        to.setSuffix(" s")
        f.addRow("Request timeout:", to)
        f.addRow("Tool calling:", self._bind(_combo([("Automatic (recommended)", "auto"),
                                                       ("Native tool calling", "native"),
                                                       ("Text protocol (any model)", "prompt")]), "ai", "tool_mode"))
        lay.addWidget(g)
        g2 = QGroupBox("Personality & behaviour")
        f2 = QFormLayout(g2)
        self.persona_combo = self._bind(_combo([(f"{p.name} - {p.title}", p.id) for p in PERSONAS.values()]),
                                        "ai", "persona")
        f2.addRow("Dispatcher:", self.persona_combo)
        f2.addRow("Copilot:", self._bind(_combo([(f"{p.name} - {p.title}", p.id) for p in COPILOTS.values()]),
                                         "ai", "copilot"))
        f2.addRow(self._bind(QCheckBox("Enable the copilot (ask for advice during flights)"), "ai", "copilot_enabled"))
        f2.addRow(self._bind(QCheckBox("Copilot callouts: positive rate, top of descent, approach, sink rate, fuel"),
                             "ai", "copilot_callouts"))
        f2.addRow(self._bind(QCheckBox("Dispatcher comments on my flight (takeoff, landing, warnings)"), "ai",
                             "proactive_comms"))
        f2.addRow(self._bind(QCheckBox("Let the AI write contract descriptions"), "ai", "ai_job_flavour"))
        lay.addWidget(g2)
        row2 = QHBoxLayout()
        t = QPushButton("Test connection")
        t.clicked.connect(self._test_ai)
        self.ai_result = QLabel("")
        self.ai_result.setWordWrap(True)
        row2.addWidget(t)
        row2.addWidget(self.ai_result, 1)
        lay.addLayout(row2)
        lay.addWidget(muted("In LM Studio: load a model (a tool-capable instruct model such as Qwen2.5-Instruct or "
                            "Llama-3.1-Instruct works best), open the Developer tab and start the server."))
        lay.addStretch(1)
        return w

    def _current_ai_cfg(self) -> AISettings:
        cfg = AISettings(**self.settings.ai.__dict__)
        for w, sec, field in self._bindings:
            if sec == "ai" and field in ("base_url", "api_key"):
                setattr(cfg, field, w.text().strip())
        return cfg

    def _list_models(self) -> None:
        client = LMStudioClient(self._current_ai_cfg())
        run_async(client.list_models, self._fill_models,
                  lambda e: self.ai_result.setText(f"<span style='color:#ff6b6b'>{e}</span>"), owner=self)

    def _fill_models(self, models: list[str]) -> None:
        cur = self.model.currentText()
        self.model.clear()
        self.model.addItem("Whichever model is loaded", "")
        for m in models:
            self.model.addItem(m, m)
        self.model.setEditText(cur) if cur and cur not in models else None
        self.ai_result.setText(f"{len(models)} model(s) found.")

    def _test_ai(self) -> None:
        self.ai_result.setText("Testing...")
        client = LMStudioClient(self._current_ai_cfg())

        def work():
            ok, msg = client.health()
            if ok:
                client.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=10)
                msg += " Model responded."
            return ok, msg

        def done(r):
            self.ai_result.setText(("<span style='color:#3ecf8e'>OK:</span> " if r[0] else
                                    "<span style='color:#ff6b6b'>Problem:</span> ") + r[1])
            if r[0]:
                self._list_models()

        run_async(work, done, lambda e: self.ai_result.setText(f"<span style='color:#ff6b6b'>{e}</span>"), owner=self)

    def _voice(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("Dispatcher voice (text-to-speech)")
        f = QFormLayout(g)
        f.addRow(self._bind(QCheckBox("Speak dispatcher messages"), "voice", "tts_enabled"))
        f.addRow("Engine:", self._bind(_combo([("Automatic", "auto"), ("Piper (neural, offline)", "piper"),
                                               ("Operating system voice", "system"), ("Off", "none")]),
                                       "voice", "tts_engine"))
        self.piper_combo = self._bind(_combo([("Match the dispatcher", "")] + [(v, v) for v in PIPER_VOICES], editable=True),
                                      "voice", "piper_model")
        row = QHBoxLayout()
        row.addWidget(self.piper_combo, 1)
        self.dl_btn = QPushButton("Download voice")
        self.dl_btn.clicked.connect(self._download_voice)
        row.addWidget(self.dl_btn)
        f.addRow("Piper voice:", row)
        self.dl_bar = QProgressBar()
        self.dl_bar.setRange(0, 100)
        self.dl_bar.hide()
        f.addRow(self.dl_bar)
        vrow = QHBoxLayout()
        self.cv_label = muted("", wrap=False)
        self.cv_btn = QPushButton("Download character voices")
        self.cv_btn.setToolTip("Natural voices for the people you talk to, so each one sounds different")
        self.cv_btn.clicked.connect(self._download_character_voices)
        vrow.addWidget(self.cv_btn)
        vrow.addWidget(self.cv_label, 1)
        f.addRow("Character voices:", vrow)
        self._refresh_character_voices()
        self.ctx.settings_changed.connect(self._refresh_character_voices)
        f.addRow("OS voice name (optional):", self._bind(QLineEdit(), "voice", "tts_voice"))
        rate = self._bind(QDoubleSpinBox(), "voice", "tts_rate")
        rate.setRange(0.5, 2.0)
        rate.setSingleStep(0.1)
        f.addRow("Speaking speed:", rate)
        vol = self._bind(QDoubleSpinBox(), "voice", "tts_volume")
        vol.setRange(0.0, 1.0)
        vol.setSingleStep(0.1)
        f.addRow("Volume:", vol)
        self.out_dev = self._bind(_combo([("System default", "")] + [(d, d) for d in stt_mod.list_output_devices()]),
                                  "voice", "output_device")
        f.addRow("Output device:", self.out_dev)
        f.addRow(self._bind(QCheckBox("Automatically speak replies"), "voice", "auto_speak_replies"))
        tv = QPushButton("Test voice")
        tv.clicked.connect(self._test_voice)
        self.tts_result = QLabel("")
        self.tts_result.setWordWrap(True)
        r2 = QHBoxLayout()
        r2.addWidget(tv)
        r2.addWidget(self.tts_result, 1)
        f.addRow(r2)
        lay.addWidget(g)

        g2 = QGroupBox("Speaking to the dispatcher (speech-to-text)")
        f2 = QFormLayout(g2)
        f2.addRow(self._bind(QCheckBox("Enable microphone / push-to-talk"), "voice", "stt_enabled"))
        f2.addRow("Recognition model:", self._bind(_combo([(m, m) for m in WHISPER_MODELS]), "voice", "stt_model"))
        f2.addRow("Processor:", self._bind(_combo([("Automatic", "auto"), ("CPU", "cpu"), ("NVIDIA GPU (CUDA)", "cuda")]),
                                          "voice", "stt_device"))
        f2.addRow(self._gpu_row())
        f2.addRow("Language:", self._bind(_combo([("English", "en"), ("Auto-detect", ""), ("German", "de"),
                                                  ("French", "fr"), ("Spanish", "es"), ("Italian", "it")]),
                                          "voice", "stt_language"))
        self.in_dev = self._bind(_combo([("System default", "")] + [(d, d) for d in stt_mod.list_input_devices()]),
                                 "voice", "input_device")
        f2.addRow("Microphone:", self.in_dev)
        f2.addRow("Push-to-talk key:", self._bind(_combo([(k, k) for k in ("F9", "F8", "F7", "F10", "caps_lock",
                                                                            "scroll_lock", "pause")]),
                                                 "voice", "push_to_talk_key"))
        tm = QPushButton("Test microphone (3 seconds)")
        tm.clicked.connect(self._test_mic)
        self.stt_result = QLabel("")
        self.stt_result.setWordWrap(True)
        r3 = QHBoxLayout()
        r3.addWidget(tm)
        r3.addWidget(self.stt_result, 1)
        f2.addRow(r3)
        lay.addWidget(g2)
        ok, msg = stt_mod.stt_available()
        lay.addWidget(muted(f"Speech recognition: {msg}. Global hotkey while MSFS has focus: "
                            f"{'available' if hotkey_available() else 'install pynput (pip install pynput)'}."))
        lay.addStretch(1)
        return w

    # --------------------------------------------------- optional GPU libraries
    def _gpu_row(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.gpu_btn = QPushButton("")
        self.gpu_btn.clicked.connect(self._gpu_clicked)
        self.gpu_label = muted("")
        row.addWidget(QLabel("GPU acceleration:"))
        row.addWidget(self.gpu_btn)
        row.addWidget(self.gpu_label, 1)
        lay.addLayout(row)
        self.gpu_bar = QProgressBar()
        self.gpu_bar.setRange(0, 100)
        self.gpu_bar.hide()
        lay.addWidget(self.gpu_bar)
        self.gpu_progress.connect(self._gpu_progress)
        self._gpu_refresh()
        return box

    def _gpu_refresh(self) -> None:
        state, msg = gpu_mod.status()
        self.gpu_label.setText(msg)
        self.gpu_btn.setVisible(state != "unsupported")
        self.gpu_btn.setText(f"Download NVIDIA libraries (~{gpu_mod.APPROX_SIZE_MB / 1000:.1f} GB)" if state == "missing"
                             else "Remove GPU libraries")
        self.gpu_btn.setEnabled(True)
        self.gpu_bar.hide()

    def _gpu_progress(self, frac: float, text: str) -> None:
        self.gpu_bar.setValue(int(frac * 100))
        self.gpu_label.setText(text)

    def _gpu_clicked(self) -> None:
        if gpu_mod.status()[0] == "ready":
            removed = gpu_mod.remove()
            self._gpu_refresh()
            if not removed:
                self.gpu_label.setText("Some files are in use. Restart SkyDispatch, then remove them again.")
            return
        self.gpu_btn.setEnabled(False)
        self.gpu_bar.setValue(0)
        self.gpu_bar.show()
        run_async(lambda: gpu_mod.install(lambda f, t: self.gpu_progress.emit(f, t)),
                  lambda _: (self._gpu_refresh(), self.ctx.toast.emit("good", "GPU libraries installed.")),
                  self._gpu_failed, owner=self)

    def _gpu_failed(self, err: str) -> None:
        self._gpu_refresh()
        self.gpu_label.setText(f"<span style='color:#ff6b6b'>{err}</span>")

    def _refresh_character_voices(self) -> None:
        if self.ctx.closed or not hasattr(self, "cv_label"):
            return
        needed, missing = self.ctx.character_voices()
        have = len(needed) - len(missing)
        mb = sum(tts_mod.voice_size_mb(v) for v in missing)
        self.cv_label.setText(f"{have} of {len(needed)} downloaded" + (f" (about {mb} MB to fetch)" if missing else
                                                                       ": everyone has their own voice"))
        self.cv_btn.setVisible(bool(missing) and tts_mod.piper_importable())

    def _download_character_voices(self) -> None:
        self.cv_btn.setEnabled(False)
        self.dl_bar.setValue(0)
        self.dl_bar.show()

        def finished(_fetched):
            self.cv_btn.setEnabled(True)
            self.dl_bar.hide()
            self._refresh_character_voices()
        self.ctx.download_character_voices(lambda f, v: self.progress.emit(f), finished)

    def _download_voice(self) -> None:
        from ...ai.personas import get_persona
        chosen = _combo_value(self.piper_combo)
        voice = chosen or get_persona(_combo_value(self.persona_combo) or self.settings.ai.persona).piper_voice
        self.dl_btn.setEnabled(False)
        self.dl_bar.setValue(0)
        self.dl_bar.show()

        def work():
            tts_mod.download_piper_voice(voice, progress=lambda f: self.progress.emit(f))
            return voice

        def done(_):
            self.dl_btn.setEnabled(True)
            self.dl_bar.hide()
            self.tts_result.setText(f"Downloaded {voice}.")

        def failed(e):
            self.dl_btn.setEnabled(True)
            self.dl_bar.hide()
            self.tts_result.setText(f"<span style='color:#ff6b6b'>Download failed: {e}</span>")

        run_async(work, done, failed, owner=self)

    def _test_voice(self) -> None:
        self.save()
        ok, msg = self.ctx.voice.tts_status()
        self.tts_result.setText(msg)
        if ok:
            self.ctx.voice.say("Dispatch to Captain. Radio check, how do you read?")

    def _test_mic(self) -> None:
        ok, msg = stt_mod.stt_available()
        if not ok:
            self.stt_result.setText(f"<span style='color:#ff6b6b'>{msg}</span>")
            return
        self.save()
        self.stt_result.setText("Speak now... (loading the model the first time can take a while)")
        voice = self.ctx.voice

        def work():
            import time
            if not voice.mic.recording:
                voice.mic.start()
            time.sleep(3)
            return voice.stt.transcribe(voice.mic.stop())

        run_async(work, lambda t: self.stt_result.setText(f"I heard: “{t or '(nothing)'}”"),
                  lambda e: self.stt_result.setText(f"<span style='color:#ff6b6b'>{e}</span>"), owner=self)

    def _gameplay(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("Career rules")
        f = QFormLayout(g)
        f.addRow("Difficulty:", self._bind(_combo([("Relaxed - generous pay, forgiving scoring", "relaxed"),
                                                   ("Normal", "normal"),
                                                   ("Realistic - strict scoring, lower pay", "realistic")]),
                                           "game", "difficulty"))
        f.addRow(self._bind(QCheckBox("Strict aircraft: contract void if I fly a different aircraft type"),
                            "game", "strict_aircraft"))
        f.addRow(self._bind(QCheckBox("Aircraft wear and damage"), "game", "wear_enabled"))
        jc = self._bind(QSpinBox(), "game", "job_count")
        jc.setRange(4, 40)
        f.addRow("Contracts on the board:", jc)
        md = self._bind(QDoubleSpinBox(), "game", "max_job_distance_nm")
        md.setRange(100, 9000)
        md.setSuffix(" nm")
        f.addRow("Longest job distance:", md)
        hl = self._bind(QSpinBox(), "game", "recency_half_life_days")
        hl.setRange(7, 365)
        hl.setSuffix(" days")
        f.addRow("Recent experience halves after:", hl)
        lay.addWidget(g)
        lay.addWidget(muted("Difficulty and fleet settings take effect on newly generated contracts and the next flight."))
        lay.addStretch(1)
        return w

    def _data(self) -> QWidget:
        w, lay = self._page()
        g = QGroupBox("Career data")
        v = QVBoxLayout(g)
        v.addWidget(muted(f"Database: {paths.database_path()}\nLogs: {paths.log_dir()}"))
        row = QHBoxLayout()
        for text, fn in (("Back up now", self._backup), ("Restore from backup...", self._restore),
                         ("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir())))),
                         ("Open logs folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_dir()))))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        lay.addWidget(g)
        g2 = QGroupBox("Airports")
        v2 = QVBoxLayout(g2)
        self.apt_label = muted("")
        v2.addWidget(self.apt_label)
        row2 = QHBoxLayout()
        b1 = QPushButton("Download worldwide airport database")
        b1.clicked.connect(self._download_airports)
        b2 = QPushButton("Import airports.csv file...")
        b2.clicked.connect(self._import_airports)
        row2.addWidget(b1)
        row2.addWidget(b2)
        v2.addLayout(row2)
        lay.addWidget(g2)
        g3 = QGroupBox("Danger zone")
        v3 = QHBoxLayout(g3)
        reset = QPushButton("Reset career...")
        reset.setObjectName("danger")
        reset.clicked.connect(self._reset)
        v3.addWidget(reset)
        v3.addWidget(muted("Deletes your pilot, hangar, jobs, logbook and chat history (a backup is made first)."), 1)
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    def _backup(self) -> None:
        dest = paths.backups_dir() / f"career-{datetime.now():%Y%m%d-%H%M%S}.db"
        self.db.backup(dest)
        keep = sorted(paths.backups_dir().glob("career-*.db"))[:-20]      # keep the newest 20
        for old in keep:
            old.unlink(missing_ok=True)
        self.ctx.toast.emit("good", f"Backup saved: {dest.name}")

    def _restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Restore backup", str(paths.backups_dir()), "Career files (*.db)")
        if not path:
            return
        if QMessageBox.question(self, "Restore backup", "The backup replaces your current career when SkyDispatch "
                                "restarts. Continue?") != QMessageBox.Yes:
            return
        shutil.copyfile(path, paths.data_dir() / "pending_restore.db")
        QMessageBox.information(self, "Restart required", "Restart SkyDispatch to finish restoring the backup.")

    def _reset(self) -> None:
        if QMessageBox.warning(self, "Reset career", "This permanently deletes your whole career. A backup will be "
                               "saved first. Continue?", QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self._backup()
        self.db.reset_career()
        self.ctx.career._fire("career_reset")
        self.run_wizard.emit()

    def _download_airports(self) -> None:
        self.apt_label.setText("Downloading...")

        def work():
            def get(url):
                with urllib.request.urlopen(url, timeout=60) as r:    # noqa: S310
                    return r.read().decode("utf-8", "replace")
            return self.db.import_airports_csv(get(AIRPORTS_URL), get(RUNWAYS_URL))

        run_async(work, lambda n: (self.apt_label.setText(f"Added {n} airports."), self.refresh_airport_count()),
                  lambda e: self.apt_label.setText(f"Download failed: {e}"), owner=self)

    def _import_airports(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "airports.csv", "", "CSV (*.csv)")
        if not path:
            return
        runways = Path(path).with_name("runways.csv")
        n = self.db.import_airports_csv(Path(path).read_text(encoding="utf-8", errors="replace"),
                                        runways.read_text(encoding="utf-8", errors="replace") if runways.exists() else None)
        self.apt_label.setText(f"Added {n} airports.")
        self.refresh_airport_count()

    def refresh_airport_count(self) -> None:
        n = self.db.q1("SELECT COUNT(*) c FROM airports")["c"]
        self.apt_label.setText(f"{n:,} airports in database.")

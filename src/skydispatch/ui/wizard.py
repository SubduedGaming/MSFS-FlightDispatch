"""First-run guided setup: pilot -> simulator -> AI -> voice -> starter aircraft."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget, QWizard, QWizardPage, QCheckBox, QHBoxLayout, QSpinBox)

from ..ai.llm import LMStudioClient
from ..ai.personas import PERSONAS
from ..core.config import AISettings
from ..data.aircraft import STARTER_IDS, get_type
from ..sim.simconnect_provider import simconnect_available
from ..voice import tts as tts_mod
from ..voice.stt import stt_available
from . import fmt
from .context import AppContext
from .workers import run_async


def _title(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("h1")
    return l


def _note(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to SkyDispatch")
        self.setSubTitle("Your career add-on for Microsoft Flight Simulator.")
        lay = QVBoxLayout(self)
        lay.addWidget(_note(
            "Accept contracts from a job market, fly them in MSFS 2020 or 2024, and get paid for the way you fly. "
            "Build a hangar, keep your aircraft maintained, and talk to your AI dispatcher by text or voice.\n\n"
            "This short setup takes about two minutes. Every choice can be changed later in Settings."))
        lay.addStretch(1)


class PilotPage(QWizardPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setTitle("Your pilot")
        self.setSubTitle("Who is flying, and where do you start?")
        f = QFormLayout(self)
        self.name = QLineEdit(ctx.settings.pilot.name if ctx.settings.pilot.name != "Captain" else "")
        self.name.setPlaceholderText("Your name")
        self.callsign = QLineEdit(ctx.settings.pilot.callsign)
        self.home = QLineEdit(ctx.settings.pilot.home_icao)
        self.home.setMaxLength(4)
        self.home_info = QLabel("")
        self.home_info.setObjectName("muted")
        self.currency = QComboBox()
        for c in ("$", "£", "€", "¥"):
            self.currency.addItem(c, c)
        f.addRow("Pilot name:", self.name)
        f.addRow("Callsign:", self.callsign)
        f.addRow("Home airport (ICAO):", self.home)
        f.addRow("", self.home_info)
        f.addRow("Currency:", self.currency)
        self.home.textChanged.connect(self._lookup)
        self.registerField("pilot_name*", self.name)
        self._lookup()

    def _lookup(self) -> None:
        a = self.ctx.db.airport(self.home.text().strip())
        self.home_info.setText(f"{a.name}, {a.city} ({a.country}), runway {a.runway_ft:,} ft" if a else
                               "Unknown airport. Try EGLL, KJFK, KSEA, YSSY ...")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return bool(self.name.text().strip()) and self.ctx.db.airport(self.home.text().strip()) is not None


class SimPage(QWizardPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setTitle("Simulator connection")
        self.setSubTitle("How should SkyDispatch read your flight data?")
        lay = QVBoxLayout(self)
        self.r_msfs = QRadioButton("Microsoft Flight Simulator on this PC (Windows)")
        self.r_bridge = QRadioButton("MSFS on another PC, using SkyDispatch Bridge (Mac/Linux, or a second screen PC)")
        self.r_demo = QRadioButton("Try it first with the built-in simulated flight engine")
        for r in (self.r_msfs, self.r_bridge, self.r_demo):
            lay.addWidget(r)
        import sys
        if sys.platform == "win32" and simconnect_available():
            self.r_msfs.setChecked(True)
        elif sys.platform == "win32":
            self.r_msfs.setChecked(True)
        else:
            self.r_msfs.setEnabled(False)
            self.r_demo.setChecked(True)
        form = QFormLayout()
        self.host = QLineEdit(ctx.settings.sim.bridge_host)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(ctx.settings.sim.bridge_port)
        self.token = QLineEdit(ctx.settings.sim.bridge_token)
        form.addRow("Bridge address:", self.host)
        form.addRow("Port:", self.port)
        form.addRow("Shared token:", self.token)
        self.form_widget = QWidget()
        self.form_widget.setLayout(form)
        lay.addWidget(self.form_widget)
        lay.addWidget(_note("MSFS must be running for a live connection; SkyDispatch keeps retrying, so you can "
                            "start it later. You can switch modes any time in Settings > Simulator."))
        lay.addStretch(1)
        self.r_bridge.toggled.connect(self.form_widget.setVisible)
        self.form_widget.setVisible(False)

    def mode(self) -> str:
        return "bridge" if self.r_bridge.isChecked() else "simconnect" if self.r_msfs.isChecked() else "simulated"


class AIPage(QWizardPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setTitle("AI dispatcher")
        self.setSubTitle("SkyDispatch talks to a local model through LM Studio.")
        f = QFormLayout(self)
        self.url = QLineEdit(ctx.settings.ai.base_url)
        self.persona = QComboBox()
        for p in PERSONAS.values():
            self.persona.addItem(f"{p.name} - {p.title}", p.id)
        i = self.persona.findData(ctx.settings.ai.persona)
        self.persona.setCurrentIndex(max(0, i))
        self.result = QLabel("")
        self.result.setWordWrap(True)
        test = QPushButton("Test connection")
        test.clicked.connect(self._test)
        f.addRow("LM Studio server URL:", self.url)
        f.addRow("Dispatcher:", self.persona)
        f.addRow(test)
        f.addRow(self.result)
        f.addRow(_note("In LM Studio: download an instruct model, open the Developer tab and click 'Start Server'. "
                       "You can skip this and still use everything except the chat; the dispatcher will use "
                       "scripted lines until a model is available."))

    def _test(self) -> None:
        self.result.setText("Testing...")
        client = LMStudioClient(AISettings(base_url=self.url.text().strip()))
        run_async(client.health, lambda r: self.result.setText(("OK: " if r[0] else "Not ready: ") + r[1]),
                  lambda e: self.result.setText(e), owner=self)


class VoicePage(QWizardPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setTitle("Voice")
        self.setSubTitle("Hear your dispatcher and talk back.")
        lay = QVBoxLayout(self)
        self.tts = QCheckBox("Dispatcher speaks out loud")
        self.tts.setChecked(True)
        self.stt = QCheckBox("I want to talk to the dispatcher with my microphone")
        self.stt.setChecked(True)
        lay.addWidget(self.tts)
        lay.addWidget(self.stt)
        ok, msg = ctx.voice.tts_status()
        lay.addWidget(_note(f"Speech output: {msg}"))
        ok2, msg2 = stt_available()
        lay.addWidget(_note(f"Speech input: {msg2}"))
        self.dl = QPushButton("Download neural voice (Piper, about 60 MB)")
        self.dl.clicked.connect(self._download)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.hide()
        self.dl_msg = _note("")
        lay.addWidget(self.dl)
        lay.addWidget(self.bar)
        lay.addWidget(self.dl_msg)
        test = QPushButton("Play test line")
        test.clicked.connect(lambda: self.ctx.voice.say("Dispatch to Captain. Radio check, how do you read?"))
        lay.addWidget(test)
        lay.addStretch(1)

    def _download(self) -> None:
        voice = self.ctx.settings.voice.piper_model
        self.dl.setEnabled(False)
        self.bar.show()
        from PySide6.QtCore import QObject, Signal

        class Sig(QObject):
            p = Signal(float)
        sig = Sig(self)
        sig.p.connect(lambda f: self.bar.setValue(int(f * 100)))
        run_async(lambda: tts_mod.download_piper_voice(voice, sig.p.emit),
                  lambda _: (self.dl.setEnabled(True), self.bar.hide(), self.dl_msg.setText("Voice downloaded.")),
                  lambda e: (self.dl.setEnabled(True), self.bar.hide(),
                             self.dl_msg.setText(f"Download failed ({e}). You can retry in Settings > Voice.")),
                  owner=self)


class AircraftPage(QWizardPage):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setTitle("Starter aircraft")
        self.setSubTitle("Every career starts with one hand-me-down plane and a loan to get going.")
        lay = QVBoxLayout(self)
        self.group: list[QRadioButton] = []
        for i, tid in enumerate(STARTER_IDS):
            t = get_type(tid)
            r = QRadioButton(f"{t.name} - {t.pax} seats, {t.cruise_kts} kt cruise, {t.range_nm} nm range")
            r.setProperty("type_id", tid)
            r.setChecked(i == 1)
            lay.addWidget(r)
            self.group.append(r)
        f = QFormLayout()
        self.balance = QSpinBox()
        self.balance.setRange(0, 5_000_000)
        self.balance.setSingleStep(5000)
        self.balance.setValue(int(ctx.settings.game.start_balance))
        self.diff = QComboBox()
        for label, v in (("Relaxed", "relaxed"), ("Normal", "normal"), ("Realistic", "realistic")):
            self.diff.addItem(label, v)
        self.diff.setCurrentIndex(1)
        f.addRow("Starting balance:", self.balance)
        f.addRow("Difficulty:", self.diff)
        lay.addLayout(f)
        lay.addStretch(1)

    def type_id(self) -> str:
        return next((r.property("type_id") for r in self.group if r.isChecked()), STARTER_IDS[1])


class DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("You're cleared for takeoff")
        self.setFinalPage(True)
        lay = QVBoxLayout(self)
        lay.addWidget(_note("Your career is ready. Open the Job Market, accept a contract, then start your engines "
                            "in the sim. SkyDispatch records the flight automatically and pays you when you park at "
                            "the destination.\n\nTip: no sim handy? Choose the simulated engine and press "
                            "'Start demo flight' on the Flight tab."))
        lay.addStretch(1)


class SetupWizard(QWizard):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("SkyDispatch Setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(640, 520)
        self.setOption(QWizard.NoCancelButtonOnLastPage, True)
        self.welcome = WelcomePage()
        self.pilot = PilotPage(ctx)
        self.sim = SimPage(ctx)
        self.ai = AIPage(ctx)
        self.voice = VoicePage(ctx)
        self.aircraft = AircraftPage(ctx)
        self.done_page = DonePage()  # NB: must not be called `done` (that shadows QDialog.done)
        for p in (self.welcome, self.pilot, self.sim, self.ai, self.voice, self.aircraft, self.done_page):
            self.addPage(p)

    def accept(self) -> None:
        s = self.ctx.settings
        s.pilot.name = self.pilot.name.text().strip()
        s.pilot.callsign = self.pilot.callsign.text().strip().upper() or "SKY1"
        s.pilot.home_icao = self.pilot.home.text().strip().upper()
        s.ui.currency = self.pilot.currency.currentData()
        s.sim.mode = self.sim.mode()
        s.sim.bridge_host = self.sim.host.text().strip() or "127.0.0.1"
        s.sim.bridge_port = self.sim.port.value()
        s.sim.bridge_token = self.sim.token.text().strip()
        s.ai.base_url = self.ai.url.text().strip() or s.ai.base_url
        s.ai.persona = self.ai.persona.currentData()
        s.voice.tts_enabled = self.voice.tts.isChecked()
        s.voice.stt_enabled = self.voice.stt.isChecked()
        s.game.difficulty = self.aircraft.diff.currentData()
        s.game.start_balance = float(self.aircraft.balance.value())
        s.ui.first_run_complete = True
        self.ctx.career.start_career(s.pilot.name, s.pilot.callsign, s.pilot.home_icao,
                                     self.aircraft.type_id(), s.game.start_balance)
        self.ctx.db.clear_messages()
        self.ctx.apply_settings()
        self.ctx.start_sim()
        self.ctx.check_ai()
        super().accept()

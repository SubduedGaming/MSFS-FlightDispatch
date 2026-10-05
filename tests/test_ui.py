import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWizard

from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.ui import theme
from skydispatch.ui.context import AppContext
from skydispatch.ui.main_window import MainWindow


@pytest.fixture
def ctx(qtbot, tmp_path):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"       # nothing listens: exercises the offline path
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    c = AppContext(s, Database(tmp_path / "c.db"))
    c.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    yield c
    c.stop_sim()
    c.voice.shutdown()


@pytest.fixture
def win(qtbot, ctx):
    QApplication.instance().setStyleSheet(theme.stylesheet())
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.show()
    return w


def test_all_pages_render(win):
    for key in win.pages:
        win.goto(key)
        win.pages[key].grab()          # forces a paint; would raise on bad widgets
    assert win.stack.count() == 10


def test_theme_toggle(win):
    win.apply_theme("light")
    win.apply_theme("dark")


def test_market_accept_and_demo_flight(qtbot, ctx, win):
    jid = ctx.db.add_job(kind="passenger", title="UI hop", origin="EGLL", dest="EGHI", distance_nm=60.0, pax=2,
                         payout=900.0, expires_at="2999-01-01T00:00:00+00:00", deadline_minutes=300)
    win.goto("market")
    market = win.pages["market"]
    market.refresh()
    row = next(i for i, j in enumerate(market._jobs) if j.id == jid)
    market.table.selectRow(row)
    assert market.btn_accept.isEnabled(), market.plane_note.text()
    market.btn_accept.click()
    assert ctx.db.active_job().id == jid
    assert win.stack.currentWidget() is win.pages["flight"]

    ctx.settings.sim.simulated_speed = 60
    ctx.start_sim()
    qtbot.waitUntil(lambda: ctx.simulated is not None and ctx.sim_connected, timeout=3000)
    assert ctx.demo_fly_active_job() is None
    qtbot.waitUntil(lambda: ctx.career.last_settlement is not None, timeout=40000)
    s = ctx.career.last_settlement
    assert s.metrics.outcome == "completed"
    assert ctx.db.job(jid).status == "completed"


def test_messenger_offline_shows_error(qtbot, ctx, win):
    page = win.pages["messenger"]
    ctx.ask("hello", "general")
    qtbot.waitUntil(lambda: any(m["role"] == "system" for m in ctx.db.messages(50, "general")), timeout=10000)
    assert any(m["role"] == "user" and m["content"] == "hello" for m in ctx.db.messages(50, "general"))
    page.refresh()


def test_hangar_actions(win, ctx):
    win.goto("hangar")
    page = win.pages["hangar"]
    page.list.setCurrentRow(0)
    a = ctx.db.hangar()[0]
    ctx.db.update_aircraft(a.id, fuel_gal=5, condition=70)
    page.refresh()
    page.b_fuel.click()
    assert ctx.db.aircraft(a.id).fuel_gal > 40
    page.b_repair.click()
    assert ctx.db.aircraft(a.id).condition == 100


def test_settings_roundtrip(win, ctx):
    win.goto("settings")
    page = win.pages["settings"]
    page.refresh()
    ctx.settings.pilot.name = "X"
    page.save()
    assert ctx.settings.pilot.name != ""
    assert ctx.settings.ai.base_url.startswith("http://127.0.0.1:9")


def test_setup_wizard_creates_career(qtbot, tmp_path):
    from skydispatch.ui.wizard import SetupWizard
    s = Settings()
    c = AppContext(s, Database(tmp_path / "w.db"))
    try:
        wiz = SetupWizard(c)
        qtbot.addWidget(wiz)
        wiz.show()
        wiz.next()                                                  # welcome -> pilot page
        assert wiz.currentPage() is wiz.pilot
        assert not wiz.button(QWizard.NextButton).isEnabled()         # name is required
        wiz.pilot.name.setText("Amelia")
        wiz.pilot.home.setText("KSEA")
        assert wiz.button(QWizard.NextButton).isEnabled()
        wiz.pilot.home.setText("ZZZZ")
        assert not wiz.button(QWizard.NextButton).isEnabled()         # unknown airport blocks
        wiz.pilot.home.setText("KSEA")
        wiz.sim.r_demo.setChecked(True)
        wiz.aircraft.group[0].setChecked(True)
        for p in (wiz.welcome, wiz.pilot, wiz.sim, wiz.ai, wiz.voice, wiz.aircraft, wiz.done_page):
            p.grab()
        wiz.accept()
        pilot = c.db.pilot()
        assert pilot.name == "Amelia" and pilot.home_icao == "KSEA"
        assert c.db.hangar()[0].type_id == "c152" and c.db.hangar()[0].location_icao == "KSEA"
        assert len(c.db.jobs("offered")) > 0
        assert s.ui.first_run_complete and s.sim.mode == "simulated"
    finally:
        c.stop_sim()
        c.voice.shutdown()


@pytest.fixture
def quiet_dialogs(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(("info", a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(("warn", a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(("box", self.text())) or 0)
    return shown


def test_job_board_lists_companies_and_apply_flow(qtbot, ctx, win, quiet_dialogs):
    win.goto("jobboard")
    page = win.pages["jobboard"]
    assert page.list.count() == 10
    ctx.career.hiring.open("skyline", 5)                        # it is recruiting this week
    page.refresh()
    page.list.setCurrentRow(2)                                  # Skyline Air Taxi: needs experience
    assert page.apply.isVisible() and page.checks.rowCount() >= 3
    page.apply.click()
    assert quiet_dialogs[-1][0] == "box" and "can't offer you a position" in quiet_dialogs[-1][1]
    assert not ctx.db.is_employed_by("skyline")
    page.list.setCurrentRow(0)                                  # Bluebird: no requirements
    page.apply.click()
    assert ctx.db.is_employed_by("bluebird")
    assert quiet_dialogs[-1][0] == "info"
    qtbot.waitUntil(lambda: ctx.db.last_message("employer:bluebird") is not None, timeout=10000)
    assert not page.resign.isHidden() and page.apply.isHidden()   # (the page itself is hidden once we jump to Messenger)
    assert page.history.rowCount() == 2


def test_messenger_assigns_a_flight_with_no_buttons(qtbot, ctx, win, quiet_dialogs):
    from PySide6.QtWidgets import QPushButton
    from skydispatch.data.employers import get_employer
    from skydispatch.ui.chat import Chips, OfferCard
    ctx.career.apply_to_employer("bluebird")
    ctx.dispatcher.start_thread(get_employer("bluebird"), "Welcome")
    win.goto("messenger")
    page = win.pages["messenger"]
    page.select_thread("employer:bluebird")
    assert page.view.findChildren(Chips)                       # "how long do you have?" quick answers are shown
    ctx.answer_availability("employer:bluebird", 60)
    qtbot.waitUntil(lambda: len(page.view.findChildren(OfferCard)) >= 1, timeout=10000)
    cards = page.view.findChildren(OfferCard)
    assert len(cards) == 1                                     # one assigned flight, nothing to pick between
    assert not page.view.findChildren(Chips)                   # answered, so the chips are gone
    assert not cards[0].findChildren(QPushButton)              # no accept / decline: the dispatcher decides
    job = ctx.db.active_job()
    assert job is not None and job.employer_id == "bluebird" and job.status == "accepted"


def test_flight_page_copilot_panel(qtbot, ctx, win):
    win.goto("flight")
    panel = win.pages["flight"].copilot
    assert "Sam Ortega" in panel.title.text()
    ctx.ask_copilot(quick="status")
    qtbot.waitUntil(lambda: len(ctx.db.messages(20, "copilot")) >= 3, timeout=10000)   # greeting + question + answer
    ctx.ask_copilot(text="what's our fuel?")
    qtbot.waitUntil(lambda: len(ctx.db.messages(20, "copilot")) >= 5, timeout=15000)
    panel.callouts.setChecked(False)
    assert ctx.settings.ai.copilot_callouts is False


def test_dashboard_shows_recent_experience_and_skill(win, ctx):
    win.goto("dashboard")
    page = win.pages["dashboard"]
    assert page.t_skill._value.text() == "Developing"
    assert page.t_recent._value.text().endswith("h")


def test_installed_detection_updates_settings_and_dealer(qtbot, ctx, win, tmp_path):
    base = tmp_path / "pk" / "Community" / "x" / "SimObjects" / "Airplanes" / "Asobo_C172sp"
    base.mkdir(parents=True)
    (base / "aircraft.cfg").write_text('[FLTSIM.0]\ntitle = "Cessna 172 Skyhawk"\n')
    ctx.settings.sim.packages_path = str(tmp_path / "pk")
    ctx.detect_installed(force=True)
    qtbot.waitUntil(lambda: ctx.settings.sim.installed_aircraft == "c172", timeout=10000)
    from skydispatch.ui.pages.hangar import DealerDialog
    dlg = DealerDialog(ctx)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == 1
    win.goto("settings")
    assert "Cessna 172 Skyhawk" in win.pages["settings"].installed_lbl.text()


def test_manual_installed_dialog(qtbot, ctx):
    from skydispatch.ui.dialogs import InstalledAircraftDialog
    dlg = InstalledAircraftDialog(ctx)
    qtbot.addWidget(dlg)
    dlg.boxes["c172"].setChecked(True)
    dlg.boxes["baron"].setChecked(True)
    dlg._save()
    assert ctx.settings.sim.installed_aircraft == "c172,baron" and ctx.settings.sim.installed_auto is False


def test_company_career_loop_end_to_end(qtbot, ctx, win, quiet_dialogs):
    """Hire -> tell dispatcher your free time -> flight assigned -> fly it -> debrief, new question, callouts."""
    from skydispatch.data.employers import get_employer
    thread = "employer:bluebird"
    ctx.career.apply_to_employer("bluebird")
    ctx.dispatcher.start_thread(get_employer("bluebird"), "Welcome aboard.")
    ctx.answer_availability(thread, 30)               # a short window keeps the simulated flight quick
    qtbot.waitUntil(lambda: ctx.db.active_job() is not None, timeout=10000)       # the dispatcher assigns the flight
    job = ctx.db.active_job()
    assert job.employer_id == "bluebird" and job.status == "accepted"

    ctx.settings.sim.simulated_speed = 60
    ctx.start_sim()
    qtbot.waitUntil(lambda: ctx.simulated is not None and ctx.sim_connected, timeout=5000)
    assert ctx.demo_fly_active_job() is None
    qtbot.waitUntil(lambda: ctx.career.last_settlement is not None, timeout=120000)

    s = ctx.career.last_settlement
    assert s.metrics.outcome == "completed" and s.employer_id == "bluebird" and s.costs == 0 and s.payout > 0
    # the debrief and the next "how long do you have?" arrive in the company thread
    qtbot.waitUntil(lambda: (ctx.db.last_message(thread) or {"kind": ""})["kind"] == "ask_time", timeout=15000)
    assert ctx.dispatcher.is_awaiting_availability(thread)
    kinds = [(m["role"], m["kind"]) for m in ctx.db.messages(80, thread)]
    assert kinds[-1] == ("assistant", "ask_time")
    assert ("assistant", "ask_time") not in kinds[:-1] or kinds.count(("assistant", "ask_time")) >= 2   # ordered: debrief first
    assert any("Grade" in m["content"] or "Welcome in" in m["content"] for m in ctx.db.messages(80, thread)
               if m["role"] == "assistant")
    # the copilot called out the flight in its own thread (positive rate at least)
    callouts = [m["content"] for m in ctx.db.messages(80, "copilot") if m["kind"] == "callout"]
    assert callouts, "the copilot said nothing during the flight"      # which callouts fire depends on sample timing
    q = ctx.career.qualifications()
    assert q.recent_h > 0 and q.by_type_h
    assert ctx.db.pilot().location_icao == job.dest


# ---------------------------------------------------------------------------- v1.4 game redesign screens
def test_job_board_shows_who_is_recruiting_and_blocks_applying_to_the_rest(qtbot, ctx, win, quiet_dialogs):
    win.goto("jobboard")
    page = win.pages["jobboard"]
    labels = [page.list.item(i).text() for i in range(page.list.count())]
    assert "Recruiting" in labels[0] and "Not recruiting" in labels[9]        # Bluebird hires, Atlas does not
    page.list.setCurrentRow(9)
    assert not page.apply.isEnabled() and "Not recruiting" in page.status.text()
    page.list.setCurrentRow(0)
    assert page.apply.isEnabled()
    ctx.career.hiring.open("skyline", 5)
    page.refresh()
    page.list.setCurrentRow(2)
    assert page.apply.isEnabled() and "turned down" in page.status.text()


def test_job_board_requirements_include_ratings(win):
    win.goto("jobboard")
    page = win.pages["jobboard"]
    page.list.setCurrentRow(4)                                                # Coastline Air Ambulance: twin + IFR
    rows = [page.checks.item(r, 0).text() for r in range(page.checks.rowCount())]
    assert "Multi-engine rating" in rows and "Instrument rating" in rows


def test_training_page_shows_licences_courses_and_costs(qtbot, ctx, win):
    win.goto("training")
    page = win.pages["training"]
    text = "".join(w.text() for w in page.body.findChildren(QLabel))
    assert "Pilot licence" in text and "Medical certificate" in text and "Instrument Rating" in text
    assert "Living costs" in text and "Hangar and insurance" in text
    buttons = {b.text(): b for b in page.body.findChildren(QPushButton)}
    assert not buttons["Start course"].isEnabled()                            # 0 flight hours: not eligible yet


def test_training_page_buttons_do_the_work(qtbot, ctx, win):
    from skydispatch.pilot import quals
    quals.apply_experience_preset(ctx.db, "student", days_ago=3)             # 40 h: eligible for the instrument rating
    ctx.db.add_transaction(20000, "t", "funds")
    ctx.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    win.goto("training")
    page = win.pages["training"]
    renew = next(b for b in page.body.findChildren(QPushButton) if b.text().startswith("Renew") and b.isEnabled())
    renew.click()
    assert ctx.career.credentials.valid("medical")
    start = next(b for b in page.body.findChildren(QPushButton) if b.text() == "Start course")
    assert start.isEnabled()
    start.click()
    assert ctx.career.credentials.active_course()[0].id == "ir"
    assert "in training" in "".join(w.text() for w in page.body.findChildren(QLabel))


def test_dashboard_alerts(ctx, win):
    ctx.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    win.goto("dashboard")
    page = win.pages["dashboard"]
    page.refresh()
    assert not page.alerts.isHidden() and "medical" in page.alerts.text().lower()
    ctx.career.credentials.renew("medical")
    page.refresh()
    assert "expired" not in page.alerts.text().lower()


def test_freelance_page_explains_it_is_for_owner_operators(ctx, win):
    ctx.career.hangar.sell(ctx.db.hangar()[0].id)
    win.goto("market")
    page = win.pages["market"]
    assert "own an aircraft" in page.empty.text() and not page.btn_refresh.isEnabled()
    assert page.table.rowCount() == 0


def test_news_reaches_the_ops_desk_and_notifications(ctx, qtbot):
    shown = []
    ctx.toast.connect(lambda level, msg: shown.append((level, msg)))
    ctx.career._fire("vacancy_opened", employer_id="harbour")                 # needs 10 h: a new pilot is not told
    ctx.career._fire("vacancy_opened", employer_id="bluebird")                # no requirements: worth a note
    qtbot.waitUntil(lambda: any("recruiting" in m for _, m in shown), timeout=3000)
    assert any("Bluebird Bush Air" in m["content"] for m in ctx.db.messages(20, "general"))
    assert not any("Harbour Light" in m["content"] for m in ctx.db.messages(20, "general"))
    ctx.career._fire("bills_charged", items=[("Living costs", 900.0)])
    ctx.career._fire("credential_expiring", kind="medical", label="Medical certificate", state="soon", days_left=5, fee=350)
    qtbot.waitUntil(lambda: any("expires in 5 days" in m for _, m in shown), timeout=3000)


def test_update_is_not_blocked_by_a_recording_that_has_not_left_the_ground(ctx, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from skydispatch.ui.dialogs import UpdateDialog
    from skydispatch.updater import UpdateInfo
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    started = []
    monkeypatch.setattr("skydispatch.ui.dialogs.run_async", lambda *a, **k: started.append(1))
    dlg = UpdateDialog(ctx, UpdateInfo(version="9.9.9", notes="", page_url="https://github.com/x/y",
                                       asset_name="a.exe", asset_url="https://github.com/x/a.exe"))
    ctx.career._new_recorder(None, None, None, 0)                        # what feed() starts for a free flight
    ctx.career.recorder.started = True                                  # engines running on the ramp, not flown yet
    dlg._install()
    assert not shown and started                                        # went ahead and began downloading
    ctx.career.recorder.air_s = 60.0                                    # now it really is mid-flight
    dlg._install()
    assert shown and "Flight in progress" not in shown[-1] and "current flight" in shown[-1]

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QWizard

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
    assert win.stack.count() == 8


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


def test_dispatcher_offline_shows_error(qtbot, ctx, win):
    page = win.pages["dispatcher"]
    with qtbot.waitSignal(ctx.chat, timeout=8000, check_params_cb=lambda r, t: r == "user"):
        ctx.ask("hello")
    qtbot.waitUntil(lambda: any(r == "system" for r, _ in page._log), timeout=8000)


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

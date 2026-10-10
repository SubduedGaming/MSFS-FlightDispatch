"""The server's admin window: status, phone pairing, settings, logs, the tray and Windows startup."""
import http.client
import json
import socket
import sys
import time
import types

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from skydispatch.core import paths
from skydispatch.core.config import Settings
from skydispatch.db.database import Database
from skydispatch.server_gui import autostart
from skydispatch.server_gui.logtail import tail
from skydispatch.server_gui.qr import qr_png
from skydispatch.server_gui.status import status_rows
from skydispatch.server_gui.window import AdminWindow
from skydispatch.ui import theme
from skydispatch.ui.context import AppContext


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ctx(qtbot, tmp_path, monkeypatch):
    s = Settings()
    s.sim.mode = "simulated"
    s.ai.base_url = "http://127.0.0.1:9/v1"
    s.ai.timeout_s = 1
    s.ui.first_run_complete = True
    s.remote.port = free_port()
    monkeypatch.setattr("skydispatch.server.engine.lan_addresses", lambda: ["192.168.1.20"])
    monkeypatch.setattr("skydispatch.sim.bridge_server.local_addresses", lambda: ["192.168.1.20"])
    c = AppContext(s, Database(tmp_path / "c.db"))
    c.career.start_career("Test Pilot", "TST1", "EGLL", "c172", 25000)
    yield c
    c.shutdown()


@pytest.fixture
def win(qtbot, ctx):
    QApplication.instance().setStyleSheet(theme.stylesheet())
    w = AdminWindow(ctx, use_tray=False)
    qtbot.addWidget(w)
    w.show()
    return w


def rows(ctx):
    return {r.key: r for r in status_rows(ctx)}


# ------------------------------------------------------------------------------------------ window
def test_the_window_has_its_four_tabs_and_paints(win):
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Status", "Phones", "Settings", "Logs"]
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        win.tabs.currentWidget().grab()                       # forces a paint; would raise on bad widgets


def test_theme_toggle(win):
    win.apply_theme("light")
    win.apply_theme("dark")


def test_no_gameplay_pages_are_left():
    import importlib.util
    for name in ("pages", "wizard", "main_window", "chat", "copilot_panel", "plan_card"):
        assert importlib.util.find_spec(f"skydispatch.ui.{name}") is None


# ------------------------------------------------------------------------------------------ status
def test_status_rows_describe_the_server(ctx):
    r = rows(ctx)
    assert r["server"].state == "off" and "Off" in r["server"].text            # not switched on yet
    assert "No phone paired" in r["phones"].text
    assert "Test Pilot" in r["career"].text
    assert r["ai"].state == "off" and "update" not in r
    ctx.settings.remote.enabled = True
    ctx.start_web()
    r = rows(ctx)
    assert r["server"].state == "ok" and f"http://192.168.1.20:{ctx.settings.remote.port}/" in r["server"].text
    ctx.pairing.new_code()
    ctx.pairing.redeem(ctx.pairing.code_state()[0], "Pixel")
    assert "1 paired, 0 connected" in rows(ctx)["phones"].text


def test_status_says_when_there_is_no_career_or_an_update(ctx):
    from skydispatch.updater import UpdateInfo
    ctx.db.reset_career()
    assert "Create one in the phone app" in rows(ctx)["career"].text
    ctx.update_info = UpdateInfo(version="9.9.9", notes="", page_url="https://github.com/x/y")
    assert "9.9.9" in rows(ctx)["update"].text


def test_the_phone_server_can_be_switched_from_the_status_tab(win, ctx):
    tab = win.status_tab
    assert tab.toggle.text() == "Start phone access"
    tab.toggle.click()
    assert ctx.web is not None and ctx.web.running and ctx.settings.remote.enabled
    assert tab.toggle.text() == "Stop phone access"
    tab.toggle.click()
    assert not ctx.web.running and not ctx.settings.remote.enabled


# ------------------------------------------------------------------------------------------ pairing
def test_pairing_tab_shows_a_code_a_qr_and_the_address(win, ctx):
    ctx.settings.remote.enabled = True
    ctx.start_web()
    tab = win.phones_tab
    tab.refresh()
    assert len(tab.code.text()) == 9 and tab.code.text()[4] == "-"
    assert f"http://192.168.1.20:{ctx.settings.remote.port}/" in tab.addresses.text()
    assert not tab.qr.pixmap().isNull() and tab.problem.text() == ""
    old = tab.code.text()
    tab.refresh(new_code=True)
    assert tab.code.text() != old


def test_pairing_tab_explains_when_phone_access_is_off(win, ctx):
    tab = win.phones_tab
    tab.refresh()
    assert "off" in tab.problem.text().lower()
    assert tab.qr.pixmap() is None or tab.qr.pixmap().isNull()


def test_a_phone_pairs_over_http_and_appears_in_the_list(win, ctx, qtbot):
    ctx.settings.remote.enabled = True
    ctx.start_web()
    tab = win.phones_tab
    win.tabs.setCurrentWidget(tab)
    tab.refresh()
    code = tab.code.text()
    c = http.client.HTTPConnection("127.0.0.1", ctx.settings.remote.port, timeout=10)
    out = []
    import threading

    def pair():
        c.request("POST", "/api/v1/pair", json.dumps({"code": code, "device_name": "Pixel 8"}), {"X-SkyDispatch": "1"})
        out.append(c.getresponse().status)
    threading.Thread(target=pair, daemon=True).start()
    qtbot.waitUntil(lambda: bool(out), timeout=8000)
    assert out == [200]
    tab._tick()                                                    # what the one-second timer does
    assert tab.table.rowCount() == 1 and tab.table.item(0, 0).text() == "Pixel 8"
    assert tab.code.text() != code                                 # a fresh code replaces the spent one


def test_opening_the_tab_shows_phones_paired_meanwhile(win, ctx):
    assert win.tabs.currentWidget() is win.status_tab
    ctx.pairing.new_code()
    ctx.pairing.redeem(ctx.pairing.code_state()[0], "Tablet")
    assert win.phones_tab.table.rowCount() == 0                    # not looking at it yet
    win.tabs.setCurrentWidget(win.phones_tab)
    assert win.phones_tab.table.rowCount() == 1 and win.phones_tab.table.item(0, 0).text() == "Tablet"


def test_removing_phones(win, ctx, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    tokens = []
    for name in ("A", "B", "C"):
        ctx.pairing.new_code()
        tokens.append(ctx.pairing.redeem(ctx.pairing.code_state()[0], name)[1])
    tab = win.phones_tab
    tab.refresh()
    assert tab.table.rowCount() == 3
    tab.table.selectRow(1)
    tab.remove.click()
    assert [d.name for d in ctx.pairing.devices()] == ["A", "C"]
    assert ctx.pairing.authenticate(tokens[1]) is None and ctx.pairing.authenticate(tokens[0]) is not None
    tab.remove_all.click()
    assert ctx.pairing.devices() == [] and tab.table.rowCount() == 0 and not tab.remove.isEnabled()


def test_an_expired_code_is_replaced_when_the_tab_is_visible(win, ctx):
    tab = win.phones_tab
    win.tabs.setCurrentWidget(tab)
    tab.refresh()
    ctx.pairing.new_code(ttl=-1)
    tab._tick()
    assert ctx.pairing.code_state()[0] != ""


def test_qr_code_helper():
    png = qr_png("skydispatch://pair?host=192.168.1.20&port=8766&code=ABCD2345")
    assert png and png.startswith(b"\x89PNG") and qr_png("") is None


# ------------------------------------------------------------------------------------------ settings and logs
def test_settings_roundtrip_including_where_speech_is_heard(win, ctx):
    page = win.settings_tab
    page.refresh()
    assert page.tabs.count() == 6
    ctx.settings.voice.output = "phone"
    page.refresh()
    page.save()
    assert ctx.settings.voice.output == "phone" and ctx.settings.ai.base_url.startswith("http://127.0.0.1:9")
    page.remote_port.setValue(ctx.settings.remote.port)
    page.save()
    assert ctx.settings.remote.port == page.remote_port.value()


def test_phone_access_is_set_up_in_settings(win, ctx):
    page = win.settings_tab
    page.refresh()
    from PySide6.QtWidgets import QGroupBox
    assert "Phone access" in [g.title() for g in page.findChildren(QGroupBox)]
    assert not hasattr(page, "run_wizard")
    page.save()
    assert ctx.settings.remote.enabled is False                  # saving does not switch it on by itself


def test_logs_tab_shows_the_end_of_the_log(win):
    (paths.log_dir() / "skydispatch.log").write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
    win.logs_tab.refresh()
    text = win.logs_tab.view.toPlainText()
    assert text.splitlines()[-1] == "line 999" and "line 0" not in text and len(text.splitlines()) <= 300


def test_tail_helper(tmp_path):
    assert tail(tmp_path / "missing.log") == ""
    f = tmp_path / "big.log"
    f.write_text("x" * 500_000 + "\nlast line\n")
    assert tail(f).splitlines()[-1] == "last line"


# ------------------------------------------------------------------------------------------ tray and quitting
class FakeTray:
    def __init__(self):
        self.messages, self.hidden = [], False

    def isVisible(self):
        return not self.hidden

    def showMessage(self, *a):
        self.messages.append(a)

    def hide(self):
        self.hidden = True


def test_closing_the_window_hides_it_to_the_tray(win, ctx):
    win.tray = FakeTray()
    win.close()
    assert not win.isVisible() and not ctx.closed and len(win.tray.messages) == 1
    win.show()
    win.close()
    assert len(win.tray.messages) == 1                           # the hint is shown only once
    win.quit_app()
    assert ctx.closed and win.tray.hidden


def test_without_a_tray_closing_quits(win, ctx):
    assert win.tray is None
    win.close()
    assert ctx.closed


def test_quitting_asks_first_during_a_flight(win, ctx, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.No))
    ctx.career._new_recorder(None, None, None, 0)
    ctx.career.recorder.started, ctx.career.recorder.air_s = True, 120.0
    win.quit_app()
    assert asked and not ctx.closed
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.quit_app()
    assert ctx.closed


# ------------------------------------------------------------------------------------------ dialogs kept from the old UI
def test_manual_installed_dialog(qtbot, ctx):
    from skydispatch.ui.dialogs import InstalledAircraftDialog
    dlg = InstalledAircraftDialog(ctx)
    qtbot.addWidget(dlg)
    dlg.boxes["c172"].setChecked(True)
    dlg.boxes["baron"].setChecked(True)
    dlg._save()
    assert ctx.settings.sim.installed_aircraft == "c172,baron" and ctx.settings.sim.installed_auto is False


def test_update_is_not_blocked_by_a_recording_that_has_not_left_the_ground(ctx, monkeypatch):
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


# ------------------------------------------------------------------------------------------ Windows startup
class FakeWinreg:
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, store): self.store = store
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def OpenKey(self, root, path): return self._Key(self.values)

    def CreateKey(self, root, path): return self._Key(self.values)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise OSError("not found")
        return self.values[name], 1

    def SetValueEx(self, key, name, _r, _t, value): self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise OSError("not found")
        del self.values[name]


def test_start_with_windows(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert autostart.supported() and not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled() and "--minimized" in fake.values["SkyDispatch"]
    autostart.set_enabled(False)
    autostart.set_enabled(False)                                  # removing twice is fine
    assert not autostart.is_enabled()


def test_startup_command_is_quoted_and_starts_minimised(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\SkyDispatch\SkyDispatch.exe")
    assert autostart.command() == r'"C:\Program Files\SkyDispatch\SkyDispatch.exe" --minimized'
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python\python.exe")
    assert autostart.command() == r'"C:\Python\pythonw.exe" -m skydispatch --minimized'


def test_startup_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert not autostart.supported() and not autostart.is_enabled()
    autostart.set_enabled(True)                                   # nothing happens, nothing raises

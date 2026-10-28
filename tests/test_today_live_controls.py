"""Regression checks for Today battery views and the menu-bar live bridge."""
import asyncio
import datetime as dt
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("qasync")

from tests.test_gui_smoke import _app, _window
from tests.test_live import _report
from noiserep.gui.menubar import MenubarApp
from noiserep.live import basic_data, parse_basic


def test_battery_defaults_to_gauge_and_switches_without_losing_data():
    w = _window()
    try:
        page = w.today
        now = dt.datetime.now()
        page.battery_gauge.set([(now, 0), (now - dt.timedelta(hours=1), 80)])
        assert page.battery_views.currentIndex() == 0
        assert page.battery_gauge.value == 0
        page.battery_switch.setChecked(True)
        assert page.battery_views.currentWidget() is page.battery_chart
        page.battery_switch.setChecked(False)
        assert page.battery_gauge.value == 0
        page.battery_gauge.set([])
        assert page.battery_gauge.value is None
    finally:
        w.close()


def test_window_receives_fresh_menu_bar_report_and_clears_stale_hr():
    w = _window()
    try:
        w._menubar_proc = SimpleNamespace(poll=lambda: None)
        sample = parse_basic(_report(), dt.datetime.now())
        w.store.write_live(sample)
        w._poll_menubar()
        assert w._last_live.heart_rate == 73
        assert "Updated" in w.today.live_card.toolTip()
        w.store.write_live(replace(sample, at=sample.at - dt.timedelta(minutes=2)))
        w._poll_menubar()
        assert "fresh reading" in w.today.live_card.toolTip()
    finally:
        w._menubar_proc = None
        w.close()


def test_menu_bar_publishes_confirmed_basic_report():
    _app()
    mb = MenubarApp(_app(), db_path=":memory:", with_tray=False)
    sent = []

    class Band:
        async def send(self, frame):
            sent.append(frame)

        async def recv(self, timeout):
            mb._connected = False
            return _report()

    mb._band = Band()
    mb._connected = True
    try:
        assert asyncio.run(mb._realtime_phase())
        assert sent == [basic_data(True), basic_data(False)]
        assert mb.store.live()["heart_rate"] == 73
        assert mb.store.live()["spo2"] == 97
    finally:
        mb.store.close()

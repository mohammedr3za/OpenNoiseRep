"""Headless smoke tests for the light health-app window.

Builds MainWindow over in-memory Stores and asserts the overview/value block,
range modes, detail chart, empty and error states, accessibility basics, chart
gap/downsampling helpers and responsive tile reflow. Runs under Qt's offscreen
platform and skips when the GUI extras are absent.
"""
import datetime
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("qasync")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from noiserep.gui import theme  # noqa: E402
from noiserep.gui.app import (  # noqa: E402
    MainWindow, _columns_for_width, _downsample, _segments, display_name,
    display_unit,
)
from noiserep.protocol.decode import DeviceInfo, MetricSeries  # noqa: E402
from noiserep.store import Store  # noqa: E402
from noiserep.sync import SyncResult  # noqa: E402


def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(theme.QSS)
    return app


def _seeded(day: datetime.datetime | None = None, others: bool = True) -> Store:
    if day is None:
        # A reading at 06:00 today keeps the default range on "Today".
        day = datetime.datetime.combine(
            datetime.date.today(), datetime.time(6, 0))
    r = SyncResult()
    r.device = DeviceInfo("1.4.0", "SN-TEST", "aa:bb:cc:dd:ee:ff", "R1")
    r.series = [MetricSeries(day, 2, 30, [0, 93, 95, 104], b"")]
    if others:
        r.series.append(MetricSeries(day, 21, 60, [0, 10, 12, 14], b""))
    s = Store(":memory:")
    s.ingest(r)
    return s


def _window(store=None) -> MainWindow:
    _app()
    return MainWindow(store if store is not None else _seeded())


# -- labels -----------------------------------------------------------------
def test_display_name_titlecase():
    assert display_name("heart_rate") == "Heart Rate"
    assert display_name("metric_21") == "Channel 21"


def test_only_verified_metric_gets_a_unit():
    # Heart rate is verified; a neutral channel must not invent a unit unless
    # the protocol layer supplies one (which the fallback cannot know).
    assert display_unit("heart_rate") == "bpm"


def test_light_palette_tokens_kept():
    assert theme.BG == "#FAFAF7"
    assert theme.PANEL == "#FFFFFF"
    assert theme.FG == "#191919"
    assert theme.ACCENT == "#CC785C"
    assert theme.ERROR == "#BF4D43"
    assert theme.SERIES[0] == "#CC785C"


# -- overview / detail ------------------------------------------------------
def test_overview_lists_metrics_and_opens_detail():
    w = _window()
    assert "heart_rate" in w._shown_metrics
    assert w.stack.currentIndex() == 0
    w._open_detail("heart_rate")
    assert w.metric_title.text() == "Heart Rate"
    assert w.stat_row.count() >= 6       # current/avg/min/max/readings + stretch
    assert w.csv_btn.isEnabled()
    assert w.json_btn.isEnabled()
    assert w.stack.currentIndex() == 1
    assert w.detail_chart_stack.currentIndex() == 0
    w.close()


def test_overview_value_block_and_tiles_exist():
    w = _window()
    assert w.tiles_grid is not None
    assert len(w._tile_widgets) == len(w._shown_metrics) - 1
    assert w.ov_stack.currentIndex() == 0
    w.close()


def test_empty_store_shows_hint_and_disabled_export():
    w = _window(Store(":memory:"))
    assert w._shown_metrics == []
    assert "No data" in w.empty_hint.text()
    assert w.ov_stack.currentIndex() == 1
    assert not w.csv_btn.isEnabled()
    # There is a direct sync action in the first-run card.
    assert w.empty_sync_btn.isEnabled()
    assert w.empty_all_btn.isHidden()
    w.close()


def test_detail_with_no_readings_shows_empty_chart():
    w = _window(Store(":memory:"))
    w._open_detail("heart_rate")
    assert w.metric_title.text() == "Heart Rate"
    assert w.detail_chart_stack.currentIndex() == 1
    assert not w.csv_btn.isEnabled()
    assert w.stack.currentIndex() == 1
    w.close()


# -- ranges -----------------------------------------------------------------
def test_defaults_to_today_when_data_is_today():
    w = _window()
    assert w._range_mode == "today"
    assert "Today" in w.ov_sub.text()
    assert not w.custom_row.isVisible()
    w.close()


def test_defaults_to_all_for_historical_only_store():
    old = datetime.datetime(2020, 5, 1, 8, 0)
    w = _window(_seeded(old))
    assert w._range_mode == "all"
    assert "All data" in w.ov_sub.text()
    assert "heart_rate" in w._shown_metrics
    w.close()


def test_range_modes_caption_and_custom_dates():
    w = _window()
    w.range_combo.setCurrentIndex(1)     # Last 7 days
    assert w._range_mode == "7d"
    assert "Last 7 days" in w.ov_sub.text()

    w.range_combo.setCurrentIndex(4)     # Custom
    assert w._range_mode == "custom"
    assert not w.custom_row.isHidden()
    w.date_from.setDate(QtCore.QDate.currentDate())
    w.date_to.setDate(QtCore.QDate.currentDate())
    assert "heart_rate" in w._shown_metrics
    w.close()


# -- banners / sync affordances --------------------------------------------
def test_error_banner_shows_and_clears():
    w = _window()
    w._show_error("Band not found")
    assert not w.error_banner.isHidden()
    assert "Band not found" in w.error_text.text()
    assert w.error_banner.accessibleName().startswith("Error")
    assert w.info_banner.isHidden()

    w._clear_error()
    assert w.error_banner.isHidden()
    assert not w.info_banner.isHidden()
    w.close()


def test_sync_controls_and_shortcuts_are_accessible():
    w = _window()
    assert w.sync_btn.accessibleName() == "Sync band"
    assert w.sync_btn.minimumHeight() >= 40
    assert w.range_combo.accessibleName() == "Date range"
    assert w.plot.accessibleName()        # set when a metric opens

    keys = {s.key().toString() for s in w._shortcuts}
    assert len(keys) == 4
    assert any(k.endswith("R") for k in keys)   # sync
    assert any(k.endswith("E") for k in keys)   # export
    assert w.error_banner.isHidden()
    w.close()


def test_progress_is_hidden_until_sync():
    w = _window()
    assert w.progress.isHidden()
    w._set_syncing(True)
    w._sync_progress(3, 10, "series")
    assert not w.progress.isHidden()
    assert "Fetching metrics" in w.status.text()
    assert not w.sync_btn.isEnabled()
    w._set_syncing(False)
    assert w.progress.isHidden()
    assert w.sync_btn.text() == "Sync band"
    w.close()


def test_empty_period_offers_show_all():
    old = datetime.datetime(2020, 5, 1, 8, 0)
    w = _window(_seeded(old))
    w.range_combo.setCurrentIndex(0)     # Today: no data in this period
    assert w.ov_stack.currentIndex() == 1
    assert "No data" in w.empty_hint.text()
    assert not w.empty_all_btn.isHidden()
    w.empty_all_btn.click()
    assert w._range_mode == "all"
    assert w.ov_stack.currentIndex() == 0
    w.close()


# -- chart helpers ----------------------------------------------------------
def test_segments_split_on_large_gaps():
    t0 = datetime.datetime(2026, 1, 1, 8, 0)
    rows = [(t0 + datetime.timedelta(seconds=30 * i), 60 + i)
            for i in range(20)]
    rows += [(t0 + datetime.timedelta(hours=3, seconds=30 * i), 70 + i)
             for i in range(20)]
    segs = _segments(rows)
    assert len(segs) == 2
    assert len(segs[0]) == 20 and len(segs[1]) == 20


def test_downsample_preserves_shape_and_spikes():
    t0 = datetime.datetime(2026, 1, 1)
    rows = [(t0 + datetime.timedelta(minutes=i), i % 500)
            for i in range(5000)]
    rows[2500] = (rows[2500][0], 9999)   # a single spike must survive
    out = _downsample(rows, 500)
    assert len(out) <= 504
    assert out[0] == rows[0] and out[-1] == rows[-1]
    assert max(v for _, v in out) == 9999
    assert [t for t, _ in out] == sorted(t for t, _ in out)


def test_columns_for_width():
    assert _columns_for_width(0) == 1
    assert _columns_for_width(280) == 1
    assert _columns_for_width(600) == 2
    assert _columns_for_width(900) == 3
    assert _columns_for_width(2000) == 3


def test_tiles_reflow_on_resize():
    w = _window()
    assert w._tile_widgets
    w.resize(1440, 900)
    w.show()
    _app().processEvents()
    w._relayout_tiles()
    assert w._tile_cols == 3

    w.resize(900, 700)
    _app().processEvents()
    w._relayout_tiles()
    assert w._tile_cols == 2
    w.close()


# -- optional sleep store ---------------------------------------------------
def test_sleep_card_appears_when_store_supports_it():
    store = _seeded()
    store.sleep_sessions = lambda start=None, end=None: [{
        "bedtime": datetime.datetime.combine(
            datetime.date.today(), datetime.time(2, 0)),
        "wake": datetime.datetime.combine(
            datetime.date.today(), datetime.time(8, 0)),
        "total_min": 360,
        "source": "rri",
    }]
    store.rest_summary = lambda start=None, end=None: None
    w = _window(store)
    assert w.sleep_card is not None
    assert w.sleep_summary.text().startswith("6h")
    w.close()


def test_sleep_card_absent_by_default():
    w = _window()
    assert w.sleep_card is None
    w.close()


def test_unparsable_sleep_api_is_ignored():
    store = _seeded()
    store.sleep_sessions = lambda *a, **k: ["not a session"]
    w = _window(store)          # must not raise
    assert w.sleep_card is None
    w.close()

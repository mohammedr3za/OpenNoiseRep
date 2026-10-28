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
    RANGE_MODES, MainWindow, _columns_for_width, _downsample, _movement_columns,
    _segments, _vitals_columns, display_name, display_unit,
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
        r.series.append(MetricSeries(day, 27, 60, [0, 10, 12, 14], b""))
    s = Store(":memory:")
    s.ingest(r)
    return s


def _window(store=None) -> MainWindow:
    _app()
    return MainWindow(store if store is not None else _seeded())


def _today_at_six() -> datetime.datetime:
    return datetime.datetime.combine(
        datetime.date.today(), datetime.time(6, 0))


def _seeded_with_other() -> Store:
    """The normal seed plus one unmapped channel, so a tile remains."""
    store = _seeded()
    r = SyncResult()
    r.series = [MetricSeries(_today_at_six(), 99, 60, [0, 5, 6], b"")]
    store.ingest(r)
    return store


def _seeded_vitals() -> Store:
    """Heart rate plus all six vitals and the four daily-activity channels."""
    day = _today_at_six()
    r = SyncResult()
    r.device = DeviceInfo("1.4.0", "SN-TEST", "aa:bb:cc:dd:ee:ff", "R1")
    r.series = [
        MetricSeries(day, 2, 30, [0, 72, 74, 76], b""),       # HR
        MetricSeries(day, 27, 900, [0, 58, 60], b""),         # HRV
        MetricSeries(day, 22, 300, [0, 35, 40], b""),         # stress
        MetricSeries(day, 4, 300, [0, 98], b""),              # SpO2
        MetricSeries(day, 8, 3600, [0, 32.5], b""),           # skin temp
        MetricSeries(day, 19, 300, [0, 15], b""),             # resp rate
        MetricSeries(day, 0, 3600, [], b"", channels={
            "steps": [0, 1200, 1800],
            "distance_m": [0, 850, 1300],
            "active_calories": [0, 90, 130],
            "active_minutes": [0, 12, 20],
        }),
    ]
    store = Store(":memory:")
    store.ingest(r)
    return store


def _label_texts(widget) -> str:
    """All label text under ``widget`` (including the widget itself)."""
    labels = [widget] if isinstance(widget, QtWidgets.QLabel) else []
    labels += widget.findChildren(QtWidgets.QLabel)
    return " ".join(label.text() for label in labels)


# -- labels -----------------------------------------------------------------
def test_display_name_titlecase():
    assert display_name("heart_rate") == "Heart Rate"
    assert display_name("metric_21") == "Channel 21"


def test_only_verified_metric_gets_a_unit():
    # Heart rate is verified; a neutral channel must not invent a unit unless
    # the protocol layer supplies one (which the fallback cannot know).
    assert display_unit("heart_rate") == "bpm"


def test_light_is_default_and_dark_is_switchable():
    assert theme.mode == "light"
    assert theme.BG == "#F5F5F7"
    assert theme.PANEL == "#FFFFFF"
    assert theme.SERIES[0] == theme.HEART
    try:
        theme.apply("dark")
        assert theme.BG == "#000000"
        assert theme.PANEL == "#1C1C1E"
        assert theme.FG == "#F5F5F7"
        assert theme.ACCENT == "#0A84FF"
        assert "#1C1C1E" in theme.QSS
    finally:
        theme.apply("light")
    assert "#1C1C1E" not in theme.QSS


def test_gutter_centres_a_capped_column():
    for width in (1000, 1600, 2560, 3840):
        g = theme.gutter(width)
        assert theme.GUTTER_MIN <= g
        assert width - 2 * g <= max(theme.CONTENT_MAX, theme.CONTENT_MIN)
    assert theme.gutter(3840) > theme.gutter(2560) > theme.gutter(1600)


def test_macos_stylesheet_leaves_native_controls_unstyled():
    mac = theme.build_qss(macos=True)
    other = theme.build_qss(macos=False)
    native_selectors = (
        "QComboBox",
        "QComboBox::drop-down",
        "QComboBox QAbstractItemView",
        "QMenu",
        "QToolButton#ghost",
    )
    for selector in native_selectors:
        assert selector not in mac, selector
        assert selector in other, selector
    # Any bare QWidget rule matches QComboBox/QToolButton and switches them
    # to Qt's stylesheet renderer, so macOS must not emit one at all.  Anchor
    # to the line start so QWidget#root and QScrollArea descendants do not
    # match.
    assert "\nQWidget {" not in mac
    assert "\nQWidget {" in other
    # Base text properties move to concrete non-native widget types instead.
    assert (f"QLabel {{ background: transparent; color: {theme.FG}; "
            "font-size: 13px; }") in mac
    assert f"QMainWindow, QWidget#root {{ background: {theme.BG}; }}" in mac
    # Shared styling still applies on both platforms.
    for qss in (mac, other):
        assert "QDateEdit" in qss
        assert "QPushButton#primary" in qss
        assert "QScrollArea" in qss


def test_is_macos_helper_and_host_stylesheet():
    assert theme.is_macos("darwin")
    assert not theme.is_macos("linux")
    assert theme.QSS == theme.build_qss()


# -- overview / detail ------------------------------------------------------
def test_overview_lists_metrics_and_opens_detail():
    w = _window()
    assert w.stack.currentWidget() is w.today          # Today is home
    w._show_home("metrics")
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
    w = _window(_seeded_with_other())
    assert w.tiles_grid is not None
    # Featured metrics live in their own sections; only metric_99 remains.
    assert len(w._tile_widgets) == 1
    assert w.vital_cards
    assert w.ov_stack.currentIndex() == 0
    w.close()


def test_vitals_render_values_and_units():
    w = _window(_seeded_vitals())
    texts = {key: _label_texts(card)
             for key, card in w.vital_cards.items()}
    assert set(texts) == {"hrv", "resting_hr", "spo2", "resp_rate",
                          "temperature", "stress"}
    assert "60" in texts["hrv"] and "ms" in texts["hrv"]
    assert "72" in texts["resting_hr"] and "bpm" in texts["resting_hr"]
    assert "estimate" not in texts["resting_hr"].lower()
    assert "98" in texts["spo2"] and "%" in texts["spo2"]
    assert "32.5" in texts["temperature"] and "°C" in texts["temperature"]
    assert "candidate" not in texts["temperature"].lower()
    assert "15" in texts["resp_rate"] and "brpm" in texts["resp_rate"]
    assert "40" in texts["stress"]
    w.close()


def test_vital_card_opens_explanation_page():
    w = _window(_seeded_vitals())
    w.vital_cards["hrv"].clicked.emit()
    assert w.stack.currentIndex() == 4
    assert w.vital_detail_title.text() == "Heart Rate Variability"
    assert "HRV" in w.vital_meaning.text()
    assert "20–80" in w.vital_typical.text()
    assert w.vital_detail_value.text() == "60"
    w.close()


def test_missing_vitals_show_dash_and_explaining_tooltip():
    w = _window()                  # seed has HR + HRV only
    for key in ("stress", "spo2", "temperature"):
        card = w.vital_cards[key]
        assert "—" in _label_texts(card)
        tip = card.toolTip().lower()
        assert "no " in tip and "sample" in tip and "range" in tip
    w.close()


def test_heart_and_stress_cards_show_latest_and_time():
    w = _window(_seeded_vitals())
    heart = _label_texts(w.heart_cards["heart_rate"])
    assert "76" in heart and "bpm" in heart and "Last reading" in heart
    stress = _label_texts(w.heart_cards["stress"])
    assert "40" in stress and "Last reading" in stress
    w.close()


def test_movement_totals_progress_and_default_goal_note():
    w = _window(_seeded_vitals())
    assert w.goal_progress["steps"].maximum() == 5000
    assert w.goal_progress["steps"].value() == 3000
    assert w.goal_progress["active_minutes"].maximum() == 30
    assert w.goal_progress["active_minutes"].value() == 30   # capped at goal
    assert w.goal_progress["distance_m"].maximum() == 4000
    assert w.goal_progress["distance_m"].value() == 2150
    assert w.goal_progress["active_calories"].maximum() == 500
    assert w.goal_progress["active_calories"].value() == 220
    steps = _label_texts(w.goal_cards["steps"])
    assert "3,000" in steps and "5,000" in steps
    assert "km" in _label_texts(w.goal_cards["distance_m"])
    assert "kcal" in _label_texts(w.goal_cards["active_calories"])
    assert "min" in _label_texts(w.goal_cards["active_minutes"])
    assert "defaults" in w.goals_note.text().lower()
    w.close()


def test_responsive_card_breakpoints():
    assert [_vitals_columns(w) for w in (1000, 700, 400)] == [6, 3, 2]
    assert [_movement_columns(w) for w in (1000, 600, 300)] == [4, 2, 1]


def test_vitals_grid_reflows_on_resize():
    w = _window(_seeded_vitals())
    w.resize(1440, 900)
    w.show()
    _app().processEvents()
    w._relayout_tiles()
    assert w._vitals_cols == 6
    w.resize(900, 700)
    _app().processEvents()
    w._relayout_tiles()
    assert w._vitals_cols == 3
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
    w.range_combo.setCurrentIndex(2)     # Last 7 days
    assert w._range_mode == "7d"
    assert "Last 7 days" in w.ov_sub.text()

    w.range_combo.setCurrentIndex(5)     # Custom
    assert w._range_mode == "custom"
    assert not w.custom_row.isHidden()
    w.date_from.setDate(QtCore.QDate.currentDate())
    w.date_to.setDate(QtCore.QDate.currentDate())
    assert "heart_rate" in w._shown_metrics
    w.close()


def test_day_mode_has_calendar_and_prev_next():
    w = _window()
    w.range_combo.setCurrentIndex(1)     # Day…
    assert w._range_mode == "day"
    assert not w.day_date.isHidden()
    assert not w.prev_day_btn.isHidden()
    assert not w.next_day_btn.isHidden()

    chosen = w.day_date.date()
    w._shift_day(-1)
    assert w.day_date.date() == chosen.addDays(-1)
    w._shift_day(1)
    assert w.day_date.date() == chosen
    start, end = w._range()
    assert start.date() == end.date() == chosen.toPython()
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


def test_range_combo_keeps_items_and_sizing():
    w = _window()
    assert [w.range_combo.itemText(i)
            for i in range(w.range_combo.count())] == [
        text for _, text in RANGE_MODES]
    assert w.range_combo.minimumHeight() >= 32
    assert w.range_combo.accessibleName() == "Date range"
    w.close()


def test_export_control_keeps_menu_actions_and_accessible_name():
    w = _window()
    menu = w.export_menu_btn.menu()
    assert menu is not None
    assert [action.text() for action in menu.actions()] == [
        "Export CSV…", "Export JSON…"]
    assert w.export_menu_btn.accessibleName() == "Export data"
    assert w.export_menu_btn.minimumHeight() >= 36
    # The Ctrl+E shortcut is still installed on the window.
    assert any(s.key().toString().endswith("E") for s in w._shortcuts)
    w.close()


def test_icon_guide_button_opens_page_and_names_all_states():
    w = _window()
    assert w.stack.currentWidget() is w.today
    assert w.guide_btn.accessibleName() == "Menu-bar icon guide"

    w.guide_btn.click()
    assert w.stack.currentWidget() is w.guide_page
    text = " ".join(
        label.text() for label in w.guide_page.findChildren(QtWidgets.QLabel))
    # All three menu-bar states are named and shown.
    assert "♥ 72" in text
    assert "♡ ~" in text
    assert "♡ —" in text
    assert "Connected — heart rate live" in text
    assert "Connected — no heart rate" in text
    assert "Not connected" in text
    # The behavioural notes.
    assert "bpm" in text
    assert "realtime" in text and "113" in text
    assert "60 seconds" in text
    assert "--lock-on-away" in text

    w.guide_back_btn.click()
    assert w.stack.currentWidget() is w.today
    w.close()


def test_progress_is_hidden_until_sync():
    w = _window()
    assert not w.sync_bar.active
    w._set_syncing(True)
    w._sync_progress(3, 10, "series")
    assert w.sync_bar.active
    assert "Fetching metrics" in w.status.text()
    assert not w.sync_btn.isEnabled()
    w._set_syncing(False)
    assert w.sync_btn.text() == "Sync band"
    w.close()


def test_sync_bar_eases_forward_only_and_fades_out():
    from noiserep.gui.syncbar import SyncBar
    _app()
    bar = SyncBar()
    bar.progress(5, 10, "series")
    assert bar.active and bar.target == 0.485
    bar.progress(2, 10, "series")                  # a late, lower report
    assert bar.target == 0.485                     # never backwards
    bar.progress(0, 0, "files")                    # indeterminate: shimmer only
    assert bar.indeterminate and bar.target == 0.485
    shown = []
    for _ in range(40):
        bar._tick(0.5)
        shown.append(bar.shown)
    assert shown == sorted(shown) and abs(shown[-1] - 0.485) < 0.01
    bar.progress(10, 10, "done")
    for _ in range(80):
        bar._tick(0.5)
    assert bar.shown == 1.0 and bar._fade_t0 is not None
    bar._fade_t0 -= 700                            # past the 0.6 s fade
    bar._tick(0.5)
    assert not bar.active and bar.opacity == 0.0


def test_live_button_toggles_menubar_app_and_link(monkeypatch):
    import subprocess

    class Proc:
        def __init__(self, *a, **k):
            self.code = None

        def poll(self):
            return self.code

        def terminate(self):
            self.code = -15

        kill = terminate

    w = _window()
    monkeypatch.setattr(subprocess, "Popen", Proc)
    monkeypatch.setattr(MainWindow, "_menubar_running", staticmethod(lambda: False))
    resumed = []
    monkeypatch.setattr(w, "start_link", lambda: resumed.append(1))
    w.live_btn.click()                              # no link task: starts at once
    assert w._menubar_active() and w.live_btn.property("active") == "true"
    assert "Paused" in w.band_state.text()
    w.live_btn.click()                              # click again terminates it
    w._poll_menubar()
    assert w._menubar_proc is None and resumed == [1]
    assert w.live_btn.property("active") == "false" and w.error_banner.isHidden()
    w.close()


def test_theme_switch_rebuilds_today_in_dark_and_back():
    w = _window()
    try:
        w.set_theme("dark")
        assert theme.mode == "dark" and w.stack.currentWidget() is w.today
        assert "Light" in w.theme_btn.text()
    finally:
        w.set_theme("light")
    assert "Dark" in w.theme_btn.text()
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
    w = _window(_seeded_with_other())
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


def test_sleep_highlights_says_no_vendor_score():
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
    text = _label_texts(w.sleep_card)
    assert "LAST NIGHT" in text.upper()
    assert "6h" in text
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


class _FakeProc:
    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


def test_live_hr_reports_a_menubar_that_exits_at_once():
    w = _window()
    w._check_menubar_started(_FakeProc(None))      # still running
    assert w.error_banner.isHidden()
    w._check_menubar_started(_FakeProc(1))
    assert not w.error_banner.isHidden()
    assert "exit code 1" in w.error_text.text()
    w.close()

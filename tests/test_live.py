"""Live basic-data frames (synthetic, shaped like the band's 165 report)."""
import datetime as dt

from noiserep import wire
from noiserep.live import basic_data, parse_basic
from noiserep.store import Store

NOW = dt.datetime(2026, 10, 28, 22, 0)


def _report(hr=73, status=2):
    data = wire.encode([(1, 6095), (2, 2904), (3, 4630), (4, hr), (5, 97),
                        (7, wire.encode([(1, 74), (2, status)])),
                        (13, 41), (14, 2934), (15, 1015)])
    return wire.encode([(1, 165), (12, wire.encode([(4, data)]))])


def test_start_and_stop_frames():
    assert basic_data(True) == bytes.fromhex("08a401 6202 1800".replace(" ", ""))
    assert wire.sub(basic_data(False), 12) == bytes.fromhex("1801")


def test_parse_report_and_reject_others():
    s = parse_basic(_report(), NOW)
    assert (s.steps, s.calories, s.active_calories, s.distance_m) == (6095, 2934, 1015, 4630)
    assert (s.heart_rate, s.spo2, s.stress, s.battery, s.charging) == (73, 97, 41, 74, False)
    assert parse_basic(_report(hr=0, status=1), NOW).heart_rate is None
    assert parse_basic(_report(status=1), NOW).charging is True
    assert parse_basic(wire.encode([(1, 113)]), NOW) is None


def test_wear_reply_and_place():
    from dataclasses import replace
    from noiserep.live import parse_wear

    on_charger = wire.encode([(1, 420), (12, wire.encode([(12, 0)]))])
    worn = wire.encode([(1, 420), (12, wire.encode([(12, 1)]))])
    assert parse_wear(on_charger) is False and parse_wear(worn) is True
    assert parse_wear(_report()) is None
    s = parse_basic(_report(), NOW)
    assert replace(s, worn=True).place == "wrist"
    assert replace(s, worn=False).place == "off_wrist"
    assert s.place == "unknown"
    assert parse_basic(_report(status=1), NOW).place == "charging"


def test_window_shows_band_place_and_live_values():
    from dataclasses import replace
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from tests.test_gui_smoke import _window
    w = _window()
    w._on_live(replace(parse_basic(_report(), NOW), worn=True))
    assert w.band_state.text() == "On wrist · 74%"
    w._on_live(parse_basic(_report(status=1), NOW))
    assert w.band_state.text().startswith("⚡ Charging")
    w.close()


def test_store_keeps_latest_live_and_hr_series():
    s = Store(":memory:")
    s.write_live(parse_basic(_report(hr=70), NOW))
    s.write_live(parse_basic(_report(hr=75), NOW + dt.timedelta(seconds=10)))
    assert s.live()["heart_rate"] == 75
    assert [v for _, v in s.samples("live_hr")] == [70.0, 75.0]
    s.close()

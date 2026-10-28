"""Metric-0 (daily activity) decoding: vendor capture shape plus synthetic
big-endian / malformed inputs.  No hardware."""
import pytest

from noiserep.protocol.decode import (
    DecodeError,
    decode_daily_activity,
    decode_payload,
    decode_series,
)
from noiserep.wire import encode


def _date_message(year=2026, month=10, day=27):
    return encode([(1, year), (2, month), (3, day)])


def _be16(values):
    return b"".join(int(v).to_bytes(2, "big") for v in values)


def _payload(*, metric=0, arrays=None, scalars=None, cmd_type=113):
    """Build a minimal type-113/115 payload with one f9 series body."""
    body = [
        (1, encode([(1, _date_message()), (2, metric)])),
        (2, 60),
    ]
    for fno, value in (arrays or {}).items():
        body.append((fno, value))
    for fno, value in (scalars or {}).items():
        body.append((fno, value))
    return encode([(1, cmd_type), (9, encode([(4, encode(body))]))])


def _capture_metric_zero(payloads):
    for p in payloads:
        if (p.direction == "recv" and p.cmd_type in (113, 115)
                and len(p.raw) > 5):
            try:
                series = decode_series(p.raw)
            except DecodeError:
                continue
            if series.metric_id == 0:
                return p.raw, series
    pytest.fail("capture has no metric-0 series")


# -- vendor capture ---------------------------------------------------------

def test_capture_metric_zero_has_four_hourly_channels(capture_payloads):
    _raw, series = _capture_metric_zero(capture_payloads)

    assert set(series.channels) == {
        "steps", "distance_m", "calories", "active_calories"}
    assert all(len(values) == 24 for values in series.channels.values())

    # Big-endian decoding is visible: hourly steps exceed one byte.
    assert max(series.channels["steps"]) > 255

    # Scalars f9/f10/f13 are the vendor totals; distance is array-summed.
    assert series.totals["steps"] == 2644
    assert series.totals["distance_m"] == 1887
    assert series.totals["active_calories"] == 194
    assert series.totals["calories"] in (1511, 1512)  # 113/115 differ by 1
    assert sum(series.channels["calories"]) == series.totals["calories"]
    assert sum(series.channels["steps"]) == series.totals["steps"]
    assert sum(series.channels["active_calories"]) == series.totals["active_calories"]


def test_capture_samples_stay_backwards_compatible(capture_payloads):
    _raw, series = _capture_metric_zero(capture_payloads)
    # The raw f3 bytes remain in samples; channels carry the decoded arrays.
    assert len(series.samples) == 48
    assert all(0 <= value <= 255 for value in series.samples)
    assert series.interval_s == 60


def test_decode_daily_activity_matches_decode_series(capture_payloads):
    raw, series = _capture_metric_zero(capture_payloads)
    activity = decode_daily_activity(raw)
    assert activity is not None
    assert activity.channels == series.channels
    assert activity.totals == series.totals


# -- synthetic --------------------------------------------------------------

def test_synthetic_big_endian_and_scalar_totals():
    series = decode_payload(_payload(
        arrays={3: _be16([1, 2, 256]), 5: _be16([10, 20, 30]),
                7: _be16([1, 2, 3]), 15: _be16([0, 1, 0])},
        scalars={9: 999, 10: 6, 13: 1},
    ))
    assert series.channels["steps"] == [1, 2, 256]   # 256 == 0x0100
    assert series.channels["distance_m"] == [10, 20, 30]
    assert series.totals == {
        "steps": 999,              # vendor scalar wins over array sum (259)
        "distance_m": 60,          # no scalar: sum of the array
        "calories": 6,
        "active_calories": 1,
    }


def test_synthetic_totals_fall_back_to_array_sums():
    series = decode_payload(_payload(
        arrays={3: _be16([4, 5]), 7: _be16([1, 2]),
                15: _be16([0, 3])},
    ))
    assert series.totals == {
        "steps": 9, "calories": 3, "active_calories": 3}


def test_short_odd_and_overlong_arrays_are_robust():
    series = decode_payload(_payload(
        arrays={
            3: _be16([1, 2]) + b"\x99",   # odd trailing byte ignored
            5: _be16(range(1, 31)),       # 30 values: truncate to 24 hours
            7: b"\x01",                   # too short: channel omitted
            15: b"",                      # empty: channel omitted
        },
    ))
    assert series.channels["steps"] == [1, 2]
    assert len(series.channels["distance_m"]) == 24
    assert series.channels["distance_m"][0] == 1
    assert series.channels["distance_m"][-1] == 24
    assert "calories" not in series.channels
    assert "active_calories" not in series.channels
    assert series.totals["steps"] == 3
    assert series.totals["distance_m"] == 300
    assert "calories" not in series.totals


def test_missing_arrays_yield_no_channels_or_totals():
    series = decode_payload(_payload(scalars={9: 2644, 10: 1511, 13: 194}))
    assert series.channels == {}
    assert series.totals == {}
    assert series.samples == []


def test_all_zero_arrays_are_present_not_dropped():
    series = decode_payload(_payload(arrays={3: _be16([0] * 24)}))
    assert series.channels["steps"] == [0] * 24
    assert series.totals == {"steps": 0}


def test_non_zero_metric_never_gets_channels():
    raw = _payload(metric=2, arrays={3: _be16([1, 2, 3])})
    series = decode_payload(raw)
    assert series.metric_id == 2
    assert series.samples == [0, 1, 0, 2, 0, 3]   # raw f3 bytes, as before
    assert series.channels == {}
    assert series.totals == {}
    assert decode_daily_activity(raw) is None


def test_decode_daily_activity_without_container_returns_none():
    assert decode_daily_activity(bytes.fromhex("08 71")) is None

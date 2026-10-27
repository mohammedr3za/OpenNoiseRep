import datetime as dt

import pytest

from noiserep.protocol.decode import DecodeError, MetricSeries, decode_series

SCALAR_ONLY = {17}  # known: body carries only scalars


def _all_series(payloads):
    return [decode_series(p.raw) for p in payloads
            if p.direction == "recv" and p.cmd_type in (113, 115) and len(p.raw) > 5]


def test_many_metrics_decode(capture_payloads):
    series = _all_series(capture_payloads)
    assert len(series) >= 14
    assert len({s.metric_id for s in series}) >= 14
    assert all(isinstance(s, MetricSeries) for s in series)
    assert all(isinstance(s.date, dt.datetime) for s in series)
    assert all(0 <= v <= 255 for s in series for v in s.samples)


def test_no_silently_empty_series(capture_payloads):
    for s in _all_series(capture_payloads):
        if s.metric_id in SCALAR_ONLY:
            assert not s.samples and not s.raw and s.interval_s is None
        else:
            assert s.samples or s.raw, f"metric {s.metric_id} decoded empty"
        if not s.samples:
            assert s.interval_s is None  # no cadence without sample bytes


def test_heart_rate_series_structure(capture_payloads):
    s = next(s for s in _all_series(capture_payloads) if s.metric_id == 2)
    assert s.interval_s == 30
    assert len(s.samples) == 2880
    assert all(0 <= v <= 255 for v in s.samples)
    assert len(s.samples) * s.interval_s == 86400


def test_no_body_raises_decode_error():
    with pytest.raises(DecodeError):
        decode_series(bytes.fromhex("08 71"))

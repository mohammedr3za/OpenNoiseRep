"""Store ingestion of metric-0 decoded channels (synthetic and vendor capture)."""
import datetime

from noiserep.protocol.decode import MetricSeries, decode_series
from noiserep.store import Store
from noiserep.sync import SyncResult

DAY = datetime.datetime(2026, 10, 27)


def _channel_result():
    result = SyncResult()
    result.series = [
        MetricSeries(
            DAY, 0, 60, list(range(48)), b"",
            channels={
                "steps": [0, 10, 0, 20],
                "distance_m": [0, 7, 0, 14],
                "calories": [1, 0, 2, 0],
                "active_minutes": [0, 0, 0, 5],
            },
            totals={
                "steps": 30, "distance_m": 21,
                "calories": 3, "active_minutes": 5,
            },
        )
    ]
    return result


def test_channels_are_stored_at_hourly_cadence_and_zero_skipped():
    s = Store(":memory:")
    counts = s.ingest(_channel_result())
    # steps 2 + distance 2 + calories 2 + active_minutes 1 non-zero hours
    assert counts["samples"] == 7

    assert s.samples("steps") == [
        (DAY + datetime.timedelta(hours=1), 10.0),
        (DAY + datetime.timedelta(hours=3), 20.0),
    ]
    assert s.samples("distance_m") == [
        (DAY + datetime.timedelta(hours=1), 7.0),
        (DAY + datetime.timedelta(hours=3), 14.0),
    ]
    assert s.samples("calories") == [
        (DAY, 1.0), (DAY + datetime.timedelta(hours=2), 2.0)]
    assert s.samples("active_minutes") == [
        (DAY + datetime.timedelta(hours=3), 5.0)]
    s.close()


def test_composite_raw_array_is_not_stored_as_a_series():
    s = Store(":memory:")
    s.ingest(_channel_result())
    assert "daily_activity" not in s.metrics()
    assert set(s.metrics()) == {
        "steps", "distance_m", "calories", "active_minutes"}
    s.close()


def test_channel_reingest_is_idempotent():
    s = Store(":memory:")
    s.ingest(_channel_result())
    before = s.samples("steps")
    s.ingest(_channel_result())
    assert s.samples("steps") == before
    assert len(s.samples("steps")) == 2
    s.close()


def test_channel_range_query():
    s = Store(":memory:")
    s.ingest(_channel_result())
    rows = s.samples("steps", start=DAY + datetime.timedelta(hours=1),
                     end=DAY + datetime.timedelta(hours=2))
    assert [v for _, v in rows] == [10.0]
    s.close()


def test_all_zero_channels_store_no_rows_but_no_composite_either():
    r = SyncResult()
    r.series = [MetricSeries(
        DAY, 0, 60, list(range(48)), b"",
        channels={"steps": [0] * 24},
        totals={"steps": 0},
    )]
    s = Store(":memory:")
    counts = s.ingest(r)
    assert counts["samples"] == 0
    assert s.metrics() == []
    s.close()


def test_series_without_channels_keeps_the_legacy_path():
    """Older callers constructing metric 0 without channels are unchanged."""
    r = SyncResult()
    r.series = [MetricSeries(DAY, 0, 60, [1, 0, 2], b"")]
    s = Store(":memory:")
    counts = s.ingest(r)
    assert counts["samples"] == 2
    assert s.metrics() == ["daily_activity"]
    s.close()


def test_capture_metric_zero_ingests_hourly_sums(capture_payloads):
    raw = None
    for payload in capture_payloads:
        if (payload.direction == "recv" and payload.cmd_type in (113, 115)
                and len(payload.raw) > 5):
            try:
                series = decode_series(payload.raw)
            except Exception:
                continue
            if series.metric_id == 0:
                raw = series
                break
    assert raw is not None, "capture has no metric-0 series"

    r = SyncResult()
    r.series = [raw]
    s = Store(":memory:")
    s.ingest(r)

    steps = s.samples("steps")
    assert sum(v for _, v in steps) == 2644
    assert sum(v for _, v in s.samples("distance_m")) == 1887
    assert sum(v for _, v in s.samples("active_minutes")) == 194
    assert sum(v for _, v in s.samples("calories")) in (1511, 1512)
    # Stored step samples land on whole-hour boundaries (zero hours skipped).
    gaps = [b[0] - a[0] for a, b in zip(steps, steps[1:])]
    assert gaps and all(gap.total_seconds() % 3600 == 0 for gap in gaps)
    assert "daily_activity" not in s.metrics()
    s.close()

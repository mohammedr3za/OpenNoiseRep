"""Pure unit tests for the realtime builders/parsers: no BLE, no Qt."""
from noiserep.protocol import realtime
from noiserep.wire import encode


def _event(hr=72, steps=1234, calories=56, standing=3):
    stats = encode([(1, steps), (2, calories), (3, 7), (4, hr),
                    (5, 0), (6, standing)])
    health = encode([(realtime.REALTIME_STATS_FIELD, stats)])
    return encode([(1, realtime.HEALTH_TYPE), (2, realtime.REALTIME_EVENT),
                   (realtime.HEALTH_FIELD, health)])


def _series(metric=2, samples=(0, 61, 0, 72)):
    date = encode([(1, 2026), (2, 10), (3, 28), (4, 8), (5, 30), (6, 0)])
    entry = encode([(1, date), (2, metric)])
    body = encode([(1, entry), (2, 30), (3, bytes(samples))])
    # The decoder locates the body by shape, not by this field number.
    container = encode([(1, body)])
    return encode([(1, 113), (9, container)])


def test_start_and_stop_frames_byte_exact():
    assert realtime.start_frame() == bytes([0x08, 0x08, 0x10, 0x2D])
    assert realtime.stop_frame() == bytes([0x08, 0x08, 0x10, 0x2E])


def test_parse_event_fields():
    sample = realtime.parse_event(_event())
    assert sample == realtime.RealtimeSample(
        heart_rate=72, steps=1234, calories=56, standing_hours=3)
    assert realtime.parse_bpm(_event()) == 72


def test_zero_heart_rate_is_not_a_reading():
    assert realtime.parse_event(_event(hr=0)) is not None
    assert realtime.parse_bpm(_event(hr=0)) is None


def test_non_event_frames_are_ignored():
    status = encode([(1, realtime.HEALTH_TYPE), (2, realtime.REALTIME_START),
                     (100, 1)])
    assert realtime.parse_event(status) is None
    assert realtime.parse_event(b"") is None
    assert realtime.parse_event(b"\x08") is None          # truncated varint
    assert realtime.parse_event(_series()) is None


def test_is_event():
    assert realtime.is_event(_event())
    assert not realtime.is_event(realtime.stop_frame())
    assert not realtime.is_event(b"not protobuf")


def test_latest_series_bpm_takes_last_nonzero():
    assert realtime.latest_series_bpm(_series()) == 72
    assert realtime.latest_series_bpm(_series(samples=(0, 0, 0))) is None
    assert realtime.latest_series_bpm(_series(samples=(91, 0, 0, 0))) == 91


def test_latest_series_bpm_ignores_other_metrics_and_malformed():
    assert realtime.latest_series_bpm(_series(metric=4)) is None
    assert realtime.latest_series_bpm(encode([(1, 113), (100, 1)])) is None
    assert realtime.latest_series_bpm(b"\xff\xff") is None
    assert realtime.latest_series_bpm(_event()) is None

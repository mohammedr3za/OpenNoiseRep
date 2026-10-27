import datetime
from noiserep.protocol import commands


def test_device_info_frame():
    assert commands.device_info() == bytes.fromhex("0820")


def test_data_index_frame():
    assert commands.data_index() == bytes.fromhex("08704a03e80103")


def test_series_113_heart_rate_since_capture_date():
    dt = datetime.datetime(2026, 10, 27, 0, 0, 0)
    frame = commands.series(113, dt, 2)
    assert frame == bytes.fromhex("08714a130a110a0d08ea0f100a181b2000280030001002")


def test_series_115_channel_and_other_metric():
    dt = datetime.datetime(2026, 10, 27, 0, 0, 0)
    # type 115 (0x73), metric 4 -> only the type byte and trailing metric differ
    assert commands.series(115, dt, 4) == bytes.fromhex("08734a130a110a0d08ea0f100a181b2000280030001004")


def test_control_frames():
    assert commands.control_117() == bytes.fromhex("0875")
    assert commands.control_123() == bytes.fromhex("087b4a03e00101")


def test_metric_ids_match_capture_order():
    assert commands.METRIC_IDS == (2, 4, 8, 14, 18, 19, 17, 0, 21, 22, 23, 26, 27, 25, 11, 12)

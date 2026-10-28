"""Metric-0 channel metadata resolves through the existing metric helpers."""
from noiserep.protocol.metrics import (
    CHANNELS,
    ChannelInfo,
    metric_confidence,
    metric_display,
    metric_name,
    metric_unit,
)

EXPECTED = {
    "steps": ("steps", "Steps"),
    "distance_m": ("m", "Distance"),
    "calories": ("kcal", "Active Calories"),
    "active_minutes": ("min", "Active Minutes"),
}


def test_channel_table_is_complete_and_consistent():
    assert set(CHANNELS) == set(EXPECTED)
    for name, info in CHANNELS.items():
        assert isinstance(info, ChannelInfo)
        assert info.name == name
        assert info.confidence in {"verified", "likely", "unknown"}
        assert info.display_name and info.source


def test_channel_names_resolve_through_metric_helpers():
    for name, (unit, display) in EXPECTED.items():
        assert metric_unit(name) == unit
        assert metric_display(name) == display
        assert metric_confidence(name) == "likely"


def test_metric_name_fallbacks_are_unchanged():
    assert metric_name(0) == "daily_activity"
    assert metric_unit(0) is None
    assert metric_name(999) == "metric_999"
    assert metric_display(999) == "Channel 999"
    assert metric_display("metric_0") == "Channel 0"
    assert metric_unit("metric_0") is None
    assert metric_display("not_a_metric") is None

"""Unit tests for the metric-id -> meaning table (no hardware, no real data)."""
from noiserep.protocol.metrics import (
    METRICS,
    MetricInfo,
    metric_display,
    metric_name,
    metric_unit,
)

# Ids that the protocol work has identified; every other id must stay neutral.
KNOWN = {
    0: ("daily_activity", None, "Daily Activity"),
    2: ("heart_rate", "bpm", "Heart Rate"),
    4: ("blood_oxygen", "%", "Blood Oxygen (SpO2)"),
    21: ("heart_rate_variability", "ms", "Heart Rate Variability"),
    22: ("stress", None, "Stress"),
    27: ("rr_interval", "10 ms", "R-R Interval"),
}

# One representative for every still-unidentified id from the capture.
UNKNOWN_IDS = (1, 3, 8, 11, 12, 14, 17, 18, 19, 23, 25, 26, 99)


def test_known_metric_mapping():
    for metric_id, (name, unit, display) in KNOWN.items():
        assert metric_name(metric_id) == name
        assert metric_unit(metric_id) == unit
        assert metric_display(metric_id) == display


def test_heart_rate_is_verified():
    assert METRICS[2].confidence == "verified"
    assert metric_unit(2) == "bpm"


def test_unknown_ids_stay_neutral():
    for metric_id in UNKNOWN_IDS:
        assert metric_id not in METRICS
        assert metric_name(metric_id) == f"metric_{metric_id}"
        assert metric_unit(metric_id) is None
        assert metric_display(metric_id) == f"Channel {metric_id}"


def test_metric_info_table_is_internally_consistent():
    assert set(METRICS) == set(KNOWN)
    for metric_id, info in METRICS.items():
        assert isinstance(info, MetricInfo)
        assert info.id == metric_id
        assert info.confidence in {"verified", "likely", "unknown"}
        assert info.name and info.name == info.name.strip()
        assert info.display_name and info.source
        # The table only carries identified metrics, never "unknown" stubs.
        assert info.confidence != "unknown"


def test_name_key_aliases_are_accepted():
    # Decoded series are keyed by metric_name(); feeding that key back in
    # must resolve to the same metadata.
    assert metric_display("heart_rate") == "Heart Rate"
    assert metric_unit("heart_rate") == "bpm"
    assert metric_display("stress") == "Stress"
    assert metric_unit("stress") is None


def test_neutral_string_key_never_gains_a_unit():
    # The GUI uses "metric_N" as its fallback key; it must stay unitless even
    # though integer id 21 is now identified.
    assert metric_display("metric_21") == "Channel 21"
    assert metric_unit("metric_21") is None
    assert metric_display("not_a_metric") is None

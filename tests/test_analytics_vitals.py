"""Vitals: robust resting-HR estimate and latest-metric lookups (synthetic)."""
import datetime

from noiserep import analytics
from noiserep.protocol.decode import MetricSeries
from noiserep.store import Store
from noiserep.sync import SyncResult

DAY = datetime.datetime(2026, 10, 28)


def _store(series):
    s = Store(":memory:")
    result = SyncResult()
    result.series = series
    s.ingest(result)
    return s


# -- resting HR estimate ----------------------------------------------------

def test_resting_hr_is_median_of_the_lowest_decile():
    samples = [(DAY + datetime.timedelta(minutes=i), value)
               for i, value in enumerate(range(50, 150))]
    assert analytics.resting_hr_estimate(samples) == 54.5


def test_resting_hr_ignores_zero_and_implausible_values():
    samples = [
        (DAY, 0), (DAY, 300), (DAY, 10),
        (DAY, 60), (DAY, 70), (DAY, 80),
    ]
    # 0, 300 and 10 are not usable readings; the three real values remain.
    # ceil(3 * 0.10) == 1, so the estimate is the lowest of those three.
    assert analytics.resting_hr_estimate(samples) == 60.0
    assert analytics.resting_hr_estimate([(DAY, 0), (DAY, 0)]) is None
    assert analytics.resting_hr_estimate([]) is None


def test_resting_hr_lowest_decile_resists_one_low_artifact():
    samples = [(DAY, 25)] + [(DAY, 60)] * 99        # one artifact, 100 values
    # Lowest 10 by count are [25, 60*9]; their median is 60, not 25.
    assert analytics.resting_hr_estimate(samples) == 60.0


# -- vitals -----------------------------------------------------------------

def _vitals_store():
    return _store([
        MetricSeries(DAY, 2, 60, list(range(50, 150)), b""),
        MetricSeries(DAY, 21, 60, [70, 72, 74], b""),
        MetricSeries(DAY, 22, 60, [20, 30, 42], b""),
        MetricSeries(DAY, 4, 60, [0, 98, 0, 97], b""),
        MetricSeries(DAY, 23, 60, [0, 31, 30], b""),
    ])


def test_vitals_values_units_and_timestamps():
    s = _vitals_store()
    vitals = analytics.vitals(s)

    assert tuple(vitals) == analytics.VITAL_KEYS
    assert all(v.available for v in vitals.values())

    resting = vitals["resting_hr"]
    assert resting.value == 54.5
    assert resting.unit == "bpm"
    assert resting.confidence == "estimate"
    assert "lowest 10%" in resting.source
    assert "Not the vendor" in resting.note
    assert resting.at is None

    assert vitals["hrv"].value == 74.0
    assert vitals["hrv"].unit == "ms"
    assert vitals["hrv"].confidence == "likely"
    assert vitals["hrv"].at == DAY + datetime.timedelta(minutes=2)

    assert vitals["stress"].value == 42.0
    assert vitals["stress"].unit is None
    assert vitals["stress"].confidence == "likely"

    assert vitals["spo2"].value == 97.0          # zero readings skipped
    assert vitals["spo2"].unit == "%"
    assert vitals["spo2"].at == DAY + datetime.timedelta(minutes=3)

    temperature = vitals["temperature"]
    assert temperature.value == 30.0
    assert temperature.unit is None              # unit not established
    assert temperature.confidence == "candidate"
    assert "metric_23" in temperature.source
    assert temperature.at == DAY + datetime.timedelta(minutes=2)
    s.close()


def test_vitals_absent_metrics_are_none_not_invented():
    s = Store(":memory:")
    vitals = analytics.vitals(s)
    assert tuple(vitals) == analytics.VITAL_KEYS
    assert all(v.value is None for v in vitals.values())
    assert all(not v.available for v in vitals.values())
    # Evidence labels still describe what would have been used.
    assert vitals["resting_hr"].confidence == "estimate"
    assert vitals["hrv"].confidence == "likely"
    assert vitals["temperature"].confidence == "candidate"
    assert "none stored" in vitals["resting_hr"].source
    s.close()


def test_vitals_respect_a_range():
    s = _vitals_store()
    # Ranges are inclusive, so end=DAY keeps only HRV's index-0 sample.
    before = analytics.vitals(s, end=DAY)
    assert before["hrv"].value == 70.0
    after = analytics.vitals(s, start=DAY + datetime.timedelta(minutes=2))
    assert after["hrv"].value == 74.0
    s.close()

"""Daily score building blocks (synthetic data)."""
import datetime as dt
import math

from noiserep import scores

T0 = dt.datetime(2026, 10, 28, 2, 0)


def test_malik_rule_drops_an_ectopic_beat_and_rmssd_skips_the_hole():
    rr = [1000, 1010] * 20 + [600] + [1000, 1010] * 20
    clean = scores.clean_rr(rr)
    assert clean[40] is None
    assert math.isclose(scores.rmssd(clean), 10.0)


def test_rmssd_needs_enough_beats():
    assert scores.rmssd([1000.0] * 10) is None


def test_nightly_hrv_is_median_of_window_rmssd_and_collapses_repeats():
    pairs, ts = [], T0
    for i in range(600):
        rr = 1000 + (20 if i % 2 else 0)
        pairs += [(ts, rr), (ts + dt.timedelta(seconds=1), rr)]   # slot repeat
        ts += dt.timedelta(seconds=2)
    assert scores.nightly_hrv(pairs, T0, ts) == 20.0


def test_nightly_rhr_is_lowest_full_five_minute_bin():
    hr = [(T0 + dt.timedelta(seconds=30 * i), 60 if i < 10 else 52) for i in range(20)]
    hr.append((T0 + dt.timedelta(hours=1), 40))           # lone reading, no bin
    assert scores.nightly_rhr(hr, T0, T0 + dt.timedelta(hours=2)) == 52.0


def test_hr_max_uses_tanaka_unless_observed_is_higher():
    assert scores.hr_max(30, [150] * 2000) == 187.0
    assert scores.hr_max(30, [195] * 2000) == 195.0


def test_strain_counts_minutes_per_edwards_zone():
    hr = [(T0, 100)] * 4 + [(T0, 170)] * 2 + [(T0, 80)] * 10     # hrmax 190
    score, minutes, trimp = scores.strain(hr, 190)
    assert minutes == [2.0, 0.0, 0.0, 1.0, 0.0]
    assert trimp == 6.0
    assert 0 < score < 10


def test_sleep_need_and_debt():
    assert scores.sleep_need_min(30) == 480 and scores.sleep_need_min(16) == 540
    assert scores.sleep_debt_min([420, 500, 450], 480) == 90


def test_recovery_waits_for_a_baseline_then_tracks_hrv():
    history = [{"hrv": 60 + i % 3, "rhr": 55} for i in range(10)]
    assert scores.recovery(70, 55, None, history[:3]) is None
    high = scores.recovery(75, 52, 85, history)
    low = scores.recovery(45, 60, 50, history)
    baseline = scores.recovery(61, 55, None, history)
    assert low < baseline < high
    assert 55 <= baseline <= 65

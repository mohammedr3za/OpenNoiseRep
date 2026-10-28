"""Synthetic tests for RRI-derived rest/sleep summaries."""
import datetime

from noiserep import analytics

BASE = datetime.datetime(2026, 10, 28, 2, 0, 0)


def _beats(count, rr_ms=1000, start=BASE, step=None):
    step = step or datetime.timedelta(milliseconds=rr_ms)
    return [(start + i * step, rr_ms) for i in range(count)]


def test_summarize_steady_rest():
    summary = analytics.summarize(_beats(600))
    assert summary is not None
    assert summary.duration_min == 10
    assert summary.beats == 600
    assert summary.avg_hr == 60.0
    assert summary.rmssd_ms == 0.0
    assert summary.coverage_pct > 99


def test_outliers_do_not_define_the_range():
    samples = _beats(100)
    samples[10] = (samples[10][0], 3000)   # 20 bpm artifact
    samples[20] = (samples[20][0], 250)    # 240 bpm artifact
    summary = analytics.summarize(samples)
    assert summary is not None
    assert summary.min_hr >= 50
    assert summary.max_hr <= 70


def test_rest_windows_split_and_merge_by_gap():
    first = _beats(600)
    second = _beats(600, start=BASE + datetime.timedelta(minutes=20))
    # A 20-minute hole: split at the default 10-minute threshold...
    assert len(analytics.rest_windows(first + second, gap_s=600,
                                      min_minutes=5)) == 2
    # ...but merge when the threshold allows a sleep-sized gap.
    merged = analytics.rest_windows(first + second, gap_s=1800,
                                    min_minutes=5)
    assert len(merged) == 1
    assert merged[0].beats == 1200


def test_overnight_rest_picks_the_longest_window():
    short = _beats(100, start=BASE)
    long = _beats(1200, start=BASE + datetime.timedelta(hours=2))
    summary = analytics.overnight_rest(short + long, gap_s=600,
                                       min_minutes=5)
    assert summary is not None
    assert summary.beats == 1200


def test_hr_series_buckets_by_minute_and_omits_empty_buckets():
    samples = _beats(120)                  # two minutes at 1 s
    series = analytics.hr_series(samples, bucket_s=60)
    assert len(series) == 2
    assert all(hr == 60.0 for _, hr in series)
    assert series[0][0].second == 0


def test_insights_are_non_medical_and_mention_hrv():
    summary = analytics.summarize(_beats(600))
    lines = analytics.insights(summary)
    text = " ".join(lines)
    assert "HRV" in text and "RMSSD" in text
    assert "baseline" in text
    assert "diagnose" not in text.lower()

"""Rest and recovery summaries derived from beat-to-beat RRI data.

The band does not always send a staged sleep file; it does send RRI (RR
interval) records.  This module turns those intervals into an honest rest
summary: the time span the band recorded, the beats inside it, heart rate,
HRV (RMSSD/SDNN/pNN50), signal coverage and gaps.  It never invents sleep
stages, and it does not claim a medical interpretation.

All timestamps are naive band wall-clock, matching ``Store``.  Everything here
is pure standard library.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from .protocol.metrics import metric_confidence

#: A silence longer than this splits one rest window into two.  Sleep RRI is
#: recorded in bursts, so allow a 30-minute hole before splitting a night.
DEFAULT_GAP_S = 1800

#: Ignore short fragments; a rest summary needs at least this span.
DEFAULT_MIN_MINUTES = 30

#: The resting-HR estimate uses the lowest tenth of usable readings by count.
RESTING_HR_LOWEST_FRACTION = 0.10

#: Plausible heart-rate bounds (bpm); a value outside them is not a reading.
RESTING_HR_MIN_BPM = 20
RESTING_HR_MAX_BPM = 250


@dataclass(frozen=True)
class RestSummary:
    """One continuous RRI window."""

    start: dt.datetime
    end: dt.datetime
    duration_min: int
    beats: int
    avg_hr: float | None
    min_hr: int | None
    max_hr: int | None
    rmssd_ms: float | None
    sdnn_ms: float | None
    pnn50: float | None
    coverage_pct: float | None

    @property
    def duration_text(self) -> str:
        hours, minutes = divmod(self.duration_min, 60)
        return f"{hours}h {minutes:02d}m"


def _as_pairs(samples: Iterable) -> list[tuple[dt.datetime, int]]:
    """Accept ``(ts, rr_ms)`` pairs, RriSample objects or dicts."""
    out: list[tuple[dt.datetime, int]] = []
    for item in samples or []:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            ts, rr = item[0], item[1]
        elif isinstance(item, dict):
            ts, rr = item.get("ts"), item.get("rr_ms")
        else:
            ts, rr = getattr(item, "ts", None), getattr(item, "rr_ms", None)
        if isinstance(ts, dt.datetime) and isinstance(rr, (int, float)) and rr > 0:
            out.append((ts, int(rr)))
    out.sort(key=lambda pair: pair[0])
    return out


def rest_windows(samples: Iterable, gap_s: int = DEFAULT_GAP_S,
                 min_minutes: int = DEFAULT_MIN_MINUTES) -> list[RestSummary]:
    """Split RRI samples into continuous rest windows, longest first.

    A window is a run of beats with no silence longer than ``gap_s``.  Only
    windows spanning at least ``min_minutes`` are returned.
    """
    pairs = _as_pairs(samples)
    groups: list[list[tuple[dt.datetime, int]]] = []
    current: list[tuple[dt.datetime, int]] = []
    previous: dt.datetime | None = None
    for ts, rr in pairs:
        if previous is not None and (ts - previous).total_seconds() > gap_s:
            if current:
                groups.append(current)
            current = []
        current.append((ts, rr))
        previous = ts
    if current:
        groups.append(current)

    summaries = []
    for group in groups:
        summary = summarize(group)
        if summary is not None:
            summaries.append(summary)
    summaries = [s for s in summaries if s.duration_min >= min_minutes]
    summaries.sort(key=lambda s: (s.duration_min, s.beats), reverse=True)
    return summaries


def summarize(samples: Sequence[tuple[dt.datetime, int]]) -> RestSummary | None:
    """Summarise one contiguous group of ``(ts, rr_ms)`` pairs."""
    pairs = _as_pairs(samples)
    if len(pairs) < 10:
        return None
    start, end = pairs[0][0], pairs[-1][0]
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return None

    rr_all = [float(value) for _, value in pairs]
    # Drop clearly non-physiological intervals (30-200 bpm) before computing
    # rate/HRV; one bad interval otherwise dominates RMSSD and the extremes.
    rr = [value for value in rr_all if 300 <= value <= 2000] or rr_all
    hr = sorted(60000.0 / value for value in rr if value > 0)
    diffs = [rr[i] - rr[i - 1] for i in range(1, len(rr))]
    diffs = [d for d in diffs if abs(d) <= 200]
    rmssd = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if diffs else None
    sdnn = statistics.pstdev(rr) if len(rr) > 1 else None
    pnn50 = (100.0 * sum(abs(d) > 50 for d in diffs) / len(diffs)
             if diffs else None)

    # RRI is recorded in bursts, so coverage is normally well below 100%.
    recorded_s = sum(rr_all) / 1000.0
    coverage = min(100.0, 100.0 * recorded_s / span_s)

    def pct(values, fraction):
        if not values:
            return None
        pos = fraction * (len(values) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)

    return RestSummary(
        start=start,
        end=end,
        duration_min=int(round(span_s / 60.0)),
        beats=len(rr),
        avg_hr=round(statistics.fmean(hr), 1) if hr else None,
        min_hr=round(pct(hr, 0.05)) if hr else None,
        max_hr=round(pct(hr, 0.95)) if hr else None,
        rmssd_ms=round(rmssd, 1) if rmssd is not None else None,
        sdnn_ms=round(sdnn, 1) if sdnn is not None else None,
        pnn50=round(pnn50, 1) if pnn50 is not None else None,
        coverage_pct=round(coverage, 1),
    )


def hr_series(samples: Iterable, bucket_s: int = 60) -> list[tuple[dt.datetime, float]]:
    """Average heart rate per time bucket, for charting a rest window.

    ``bucket_s=60`` gives one point per minute.  Empty buckets are omitted, so
    charts show real recorded time rather than invented zeros.
    """
    pairs = _as_pairs(samples)
    if bucket_s <= 0:
        raise ValueError("bucket_s must be positive")
    buckets: dict[dt.datetime, list[float]] = {}
    for ts, rr in pairs:
        if bucket_s % 60 == 0:
            minutes = bucket_s // 60
            bucket = ts.replace(second=0, microsecond=0) - dt.timedelta(
                minutes=ts.minute % minutes)
        else:
            epoch = int((ts - dt.datetime(1970, 1, 1)).total_seconds())
            bucket = dt.datetime(1970, 1, 1) + dt.timedelta(
                seconds=(epoch // bucket_s) * bucket_s)
        buckets.setdefault(bucket, []).append(60000.0 / rr)
    return [(bucket, round(statistics.fmean(values), 1))
            for bucket, values in sorted(buckets.items())]


def overnight_rest(samples: Iterable, gap_s: int = DEFAULT_GAP_S,
                   min_minutes: int = DEFAULT_MIN_MINUTES) -> RestSummary | None:
    """The longest RRI window, i.e. the likely overnight rest period."""
    windows = rest_windows(samples, gap_s=gap_s, min_minutes=min_minutes)
    return windows[0] if windows else None


def insights(summary: RestSummary) -> list[str]:
    """Short, non-medical observations about a rest window."""
    lines = [
        f"RRI spans {summary.start:%H:%M}–{summary.end:%H:%M} "
        f"({summary.duration_text}), with {summary.beats:,} beats."
    ]
    if summary.avg_hr is not None:
        lines.append(
            f"Average heart rate {summary.avg_hr:.0f} bpm "
            f"(5–95%: {summary.min_hr}–{summary.max_hr})."
        )
    if summary.rmssd_ms is not None:
        lines.append(
            f"HRV: RMSSD {summary.rmssd_ms:.0f} ms, "
            f"SDNN {summary.sdnn_ms:.0f} ms, pNN50 {summary.pnn50:.0f}%."
        )
    if summary.coverage_pct is not None and summary.coverage_pct < 80:
        lines.append(
            f"Beats cover {summary.coverage_pct:.0f}% of the span; the band "
            "records RRI in bursts, so treat the span as rest time, not "
            "continuous sleep."
        )
    if summary.avg_hr is not None and summary.avg_hr > 80:
        lines.append(
            "Average heart rate was on the higher side for rest; movement, "
            "a loose strap or caffeine can raise it."
        )
    lines.append(
        "HRV trends are most useful against your own baseline, not a fixed "
        "target."
    )
    return lines


# ---------------------------------------------------------------------------
# Vitals
# ---------------------------------------------------------------------------

#: Keys always present in :func:`vitals`'s result, in display order.
VITAL_KEYS = ("resting_hr", "hrv", "stress", "spo2", "temperature")


@dataclass(frozen=True)
class Vital:
    """One derived vital, with the evidence needed to render it honestly.

    ``value`` is ``None`` when the metric is absent.  ``source`` says where the
    value came from (a metric name, or the estimate recipe), and ``confidence``
    is one of ``"verified"`` / ``"likely"`` / ``"candidate"`` / ``"estimate"``
    so a candidate is never shown as certain health data.
    """

    key: str
    label: str
    value: float | None
    unit: str | None
    source: str
    confidence: str
    at: dt.datetime | None = None
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


def _value(item):
    """Read a sample value from a ``(ts, value)`` pair, dict or object."""
    if isinstance(item, (tuple, list)) and len(item) >= 2:
        return item[1]
    if isinstance(item, dict):
        return item.get("value")
    return getattr(item, "value", None)


def resting_hr_estimate(samples: Iterable) -> float | None:
    """Median of the lowest decile of non-zero, plausible heart-rate readings.

    This is a *robust estimate* computed from the band's heart-rate series: it
    is deliberately not the vendor's resting-HR field (the band's daily-summary
    file has one, but this device does not push that file).  Taking the median
    of the lowest 10% by count resists a single spurious low reading, while
    still describing the rate the band actually recorded at rest.

    Returns ``None`` when there is no usable reading.
    """
    values: list[float] = []
    for item in samples or []:
        value = _value(item)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and RESTING_HR_MIN_BPM <= value <= RESTING_HR_MAX_BPM):
            values.append(float(value))
    if not values:
        return None
    values.sort()
    count = max(1, math.ceil(len(values) * RESTING_HR_LOWEST_FRACTION))
    return statistics.median(values[:count])


def _latest(store, metric: str, start=None, end=None):
    rows = store.samples(metric, start, end)
    return rows[-1] if rows else None


def vitals(store, start=None, end=None) -> dict[str, Vital]:
    """Derived latest vitals from a :class:`~noiserep.store.Store`.

    ``store`` only needs a ``samples(metric, start, end)`` method (the real
    :class:`~noiserep.store.Store` and test doubles both qualify).

    The result always has every key in :data:`VITAL_KEYS`; a missing metric
    yields ``Vital.value is None`` rather than an invented number.  ``hrv``,
    ``stress`` and ``spo2`` are the latest stored samples of metrics 21, 22 and
    4; ``temperature`` is metric 23, labelled a *candidate* because its unit is
    not established (the vendor capture suggests ~27-31 but a live unworn
    reading was 213).
    """
    hr_rows = store.samples("heart_rate", start, end)
    resting = resting_hr_estimate(hr_rows)
    if hr_rows:
        resting_source = (
            "computed from heart_rate: median of the lowest 10% of "
            f"{len(hr_rows):,} readings"
        )
    else:
        resting_source = (
            "computed from heart_rate: median of the lowest 10% of readings; "
            "none stored"
        )

    out: dict[str, Vital] = {
        "resting_hr": Vital(
            key="resting_hr",
            label="Resting HR estimate",
            value=round(resting, 1) if resting is not None else None,
            unit="bpm",
            source=resting_source,
            confidence="estimate",
            note="Not the vendor's resting-HR figure; a low-decile estimate.",
        ),
    }

    specs = (
        ("hrv", "Heart Rate Variability", "heart_rate_variability", "ms"),
        ("stress", "Stress", "stress", None),
        ("spo2", "Blood Oxygen (SpO2)", "blood_oxygen", "%"),
    )
    for key, label, metric, unit in specs:
        row = _latest(store, metric, start, end)
        out[key] = Vital(
            key=key,
            label=label,
            value=float(row[1]) if row is not None else None,
            unit=unit,
            source=f"latest {metric} sample",
            confidence=metric_confidence(metric) or "candidate",
            at=row[0] if row is not None else None,
        )

    # Metric 23 is an hourly 0-255 array whose meaning is not established.  A
    # vendor day reads 27-31 (consistent with degrees Celsius) while an unworn
    # live day reads 0 and 213, so expose it as a candidate without a unit.
    temp = _latest(store, "metric_23", start, end)
    out["temperature"] = Vital(
        key="temperature",
        label="Temperature (candidate)",
        value=float(temp[1]) if temp is not None else None,
        unit=None,
        source="latest metric_23 sample (temperature candidate)",
        confidence="candidate",
        at=temp[0] if temp is not None else None,
        note="Unit and meaning unverified; do not treat as body temperature.",
    )
    return out


__all__ = [
    "DEFAULT_GAP_S",
    "DEFAULT_MIN_MINUTES",
    "RESTING_HR_LOWEST_FRACTION",
    "RESTING_HR_MAX_BPM",
    "RESTING_HR_MIN_BPM",
    "RestSummary",
    "VITAL_KEYS",
    "Vital",
    "hr_series",
    "insights",
    "overnight_rest",
    "rest_windows",
    "resting_hr_estimate",
    "summarize",
    "vitals",
]

"""Metric id -> meaning, unit, and evidence-backed confidence.

Metric ids appear in the type-112 data index and in the ``f1`` entry of every
type-113/115 series body. Identity is inferred from two sources:

* ``noisefit.pklg`` -- a full vendor-app sync (24 h of data, band worn, one
  manual health measurement around 15:15).
* the live captures under ``data/`` (a briefly-worn day and two unpaired,
  clock-unset syncs).

``confidence`` is deliberately conservative:

* ``verified`` -- the value range and cadence are unambiguous (heart rate).
* ``likely``   -- range, cadence and cross-metric behaviour agree, but the
  vendor app's own labels have not been read side by side.
* ids with no satisfactory evidence are **not** in :data:`METRICS`; they keep
  the neutral ``metric_N`` name / ``Channel N`` display and unit ``None``.

Cadence note: the series body's ``f2`` field is a cadence *number* whose unit
is not consistent across metrics. Metric 2's ``f2=30`` is seconds (2880
values/day); metric 4/22's ``f2=5``, metric 21's ``f2=15`` and metric
11/12/23's ``f2=60`` are minutes (288 / 96 / 24 values per day). The decoder
currently exposes the raw ``f2`` as ``MetricSeries.interval_s``; do not assume
seconds for every metric.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricInfo:
    """What one metric id means, and how strongly that is established."""

    id: int
    name: str
    unit: str | None
    display_name: str
    confidence: str  # "verified" | "likely" | "unknown"
    source: str


@dataclass(frozen=True)
class ChannelInfo:
    """A named channel inside a composite metric (currently metric 0).

    Unlike :class:`MetricInfo` these are not metric ids: they are the four
    hourly arrays carried by the daily-activity body.  ``name`` is the key the
    store writes samples under, so ``Store.samples("steps")`` works directly.
    """

    name: str
    unit: str | None
    display_name: str
    confidence: str  # "verified" | "likely" | "unknown"
    source: str


#: Channels decoded from metric 0.  The vendor capture establishes the sums
#: (steps = f9, calories = f10, active minutes = f13) and the ~0.71 m/step
#: ratio; the display units are conventional, not read from the app.
CHANNELS: dict[str, ChannelInfo] = {
    "steps": ChannelInfo(
        name="steps",
        unit="steps",
        display_name="Steps",
        confidence="likely",
        source=(
            "vendor capture: hourly u16-BE array (f3) sums to f9; ~0.71 m/step "
            "against the distance array"
        ),
    ),
    "distance_m": ChannelInfo(
        name="distance_m",
        unit="m",
        display_name="Distance",
        confidence="likely",
        source=(
            "vendor capture: hourly u16-BE array (f5) in metres; sums to 1887 m "
            "for 2644 steps (~0.71 m/step)"
        ),
    ),
    "calories": ChannelInfo(
        name="calories",
        unit="kcal",
        display_name="Active Calories",
        confidence="likely",
        source=(
            "vendor capture: hourly u16-BE array (f7) sums to f10; f14 is the "
            "cumulative form of the same values"
        ),
    ),
    "active_minutes": ChannelInfo(
        name="active_minutes",
        unit="min",
        display_name="Active Minutes",
        confidence="likely",
        source=(
            "vendor capture: hourly u16-BE array (f15) sums to f13, the "
            "vendor's active-minutes total"
        ),
    ),
}


METRICS: dict[int, MetricInfo] = {
    0: MetricInfo(
        id=0,
        name="daily_activity",
        unit=None,  # composite: first channel is steps, plus distance/calories
        display_name="Daily Activity",
        confidence="likely",
        source=(
            "vendor capture: 24 hourly u16-BE values per channel; f3 sums to "
            "f9 (total steps), f7 to f10 (total calories), f15 to f13 (active "
            "minutes)"
        ),
    ),
    2: MetricInfo(
        id=2,
        name="heart_rate",
        unit="bpm",
        display_name="Heart Rate",
        confidence="verified",
        source=(
            "live + vendor capture: 2880 values/day (f2=30 s), range ~60-160, "
            "matches wrist heart rate"
        ),
    ),
    4: MetricInfo(
        id=4,
        name="blood_oxygen",
        unit="%",
        display_name="Blood Oxygen (SpO2)",
        confidence="likely",
        source=(
            "vendor capture: single 100 at a manual-measurement moment, zero "
            "elsewhere; 0-100 range"
        ),
    ),
    21: MetricInfo(
        id=21,
        name="heart_rate_variability",
        unit="ms",
        display_name="Heart Rate Variability",
        confidence="likely",
        source=(
            "vendor capture: 96 values/day (15 min); inverse to simultaneous "
            "HR (hourly r=-0.81), ~74-77 in sleep vs ~51-53 active"
        ),
    ),
    22: MetricInfo(
        id=22,
        name="stress",
        unit=None,  # 0-100 score
        display_name="Stress",
        confidence="likely",
        source=(
            "vendor capture: 288 values/day (5 min), tracks HR (hourly "
            "r=+0.91), low in sleep and higher while awake"
        ),
    ),
    27: MetricInfo(
        id=27,
        name="rr_interval",
        unit="10 ms",
        display_name="R-R Interval",
        confidence="likely",
        source=(
            "vendor capture: 16 values 49-86 during a measurement are "
            "consistent with 60000/HR/10 (mean error ~5.5 units); vendor "
            "RRI_Data files"
        ),
    ),
}


def _resolve(metric):
    """Accept an int id, a canonical name, ``metric_N``, or channel name.

    Returns a :class:`MetricInfo`, a :class:`ChannelInfo`, or ``None``.  String
    names are accepted so callers that key series by ``metric_name()`` can feed
    that key straight back in; the metric-0 channel names resolve the same way
    so the GUI can label stored channel series.
    """
    if isinstance(metric, (MetricInfo, ChannelInfo)):
        return metric
    if isinstance(metric, int):
        return METRICS.get(metric)
    if isinstance(metric, str):
        for info in METRICS.values():
            if info.name == metric:
                return info
        if metric.startswith("metric_"):
            try:
                return METRICS.get(int(metric[len("metric_"):]))
            except ValueError:
                return None
        return CHANNELS.get(metric)
    return None


def metric_name(metric_id: int) -> str:
    """Canonical snake_case name, or ``metric_N`` for an unmapped id."""
    info = METRICS.get(metric_id)
    return info.name if info else f"metric_{metric_id}"


def metric_unit(metric_id) -> str | None:
    """Unit string, or ``None`` when unknown/unmapped.

    ``metric_N`` strings are treated as neutral channel keys and always yield
    ``None`` even if that integer id is mapped.
    """
    if isinstance(metric_id, str) and metric_id.startswith("metric_"):
        return None
    info = _resolve(metric_id)
    return info.unit if info else None


def metric_confidence(metric_id) -> str | None:
    """``"verified"``, ``"likely"``, or ``None`` for an unmapped id.

    The GUI uses this to mark an evidence-based but not vendor-confirmed label,
    so a "likely" channel is never presented as certain health data.
    """
    info = _resolve(metric_id)
    return info.confidence if info else None


def metric_display(metric_id) -> str | None:
    """Human label, or ``Channel N`` for an unmapped numeric id.

    ``metric_N`` strings keep the neutral ``Channel N`` label (they are the
    fallback keys used when an id could not be identified). An unknown
    non-numeric name returns ``None`` so callers can apply their own fallback.
    """
    if isinstance(metric_id, str) and metric_id.startswith("metric_"):
        return f"Channel {metric_id[len('metric_'):]}"
    info = _resolve(metric_id)
    if info:
        return info.display_name
    if isinstance(metric_id, int):
        return f"Channel {metric_id}"
    return None

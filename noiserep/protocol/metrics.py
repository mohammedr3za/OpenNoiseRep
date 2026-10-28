"""Metric id -> meaning, unit, and evidence-backed confidence.

Metric ids appear in the type-112 data index and in the ``f1`` entry of every
type-113/115 series body. They are the vendor's ``SEFitnessFunctionType``
enum, recovered with the rest of the protobuf schema from the NoiseFit APK
(see docs/PROTOCOL.md). Identities are checked against live band data.

``confidence``:

* ``verified`` -- vendor schema name, and live values fit it.
* ``likely``   -- vendor schema name, but the unit or scale is inferred.
* ids not in :data:`METRICS` keep the neutral ``metric_N`` / ``Channel N``.

Cadence is ``86400 / sample count`` (2880 -> 30 s, 288 -> 5 min, 96 -> 15 min,
24 -> 1 h); the body's frequency field uses mixed units and is not trusted.
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


#: Named channels derived from composite records (stored hourly or daily).
#: ``name`` is the key the store writes samples under.
CHANNELS: dict[str, ChannelInfo] = {
    "steps": ChannelInfo(
        name="steps",
        unit="steps",
        display_name="Steps",
        confidence="verified",
        source="SEDailyData.steps_data (f3), hourly u16-BE; total f9",
    ),
    "distance_m": ChannelInfo(
        name="distance_m",
        unit="m",
        display_name="Distance",
        confidence="verified",
        source="SEDailyData.distance_data (f5), hourly u16-BE metres",
    ),
    "calories": ChannelInfo(
        name="calories",
        unit="kcal",
        display_name="Total Calories",
        confidence="likely",
        source=(
            "SEDailyData.calorie_data (f7), hourly u16-BE; total f10. ~85 "
            "kcal/h asleep, so this includes resting energy"
        ),
    ),
    "active_calories": ChannelInfo(
        name="active_calories",
        unit="kcal",
        display_name="Active Calories",
        confidence="likely",
        source=(
            "SEDailyData.today_sport_calorie_hourly_data (f15), hourly "
            "u16-BE; total today_sport_calorie_data (f13)"
        ),
    ),
    "active_minutes": ChannelInfo(
        name="active_minutes",
        unit="min",
        display_name="Active Minutes",
        confidence="likely",
        source=(
            "NoiseFit's rule: 5 min x count(movement level 1-3) per hour, "
            "from metric 14"
        ),
    ),
    "resting_hr": ChannelInfo(
        name="resting_hr",
        unit="bpm",
        display_name="Resting HR (band)",
        confidence="verified",
        source="SEContinuousHeartRateData.resting_heart_rate_value (f6)",
    ),
    "health_score": ChannelInfo(
        name="health_score",
        unit=None,
        display_name="Health Score",
        confidence="verified",
        source="SERingHealthScoreData.healthscore (GoMore, metric 17)",
    ),
    "sedentary_min": ChannelInfo(
        name="sedentary_min",
        unit="min",
        display_name="Sedentary Time",
        confidence="likely",
        source="SERingHealthScoreData.Sedentary_duration (seconds) / 60",
    ),
}


def _m(mid: int, name: str, unit, display: str, confidence: str,
       source: str) -> MetricInfo:
    return MetricInfo(mid, name, unit, display, confidence, source)


METRICS: dict[int, MetricInfo] = {m.id: m for m in (
    _m(0, "daily_activity", None, "Daily Activity", "verified",
       "DAILY_DATA -> SEDailyData: hourly steps/distance/calories"),
    _m(2, "heart_rate", "bpm", "Heart Rate", "verified",
       "CONTINUOUS_HEART_RATE_DATA: 2880/day (30 s), live range 49-152"),
    _m(4, "blood_oxygen", "%", "Blood Oxygen (SpO2)", "verified",
       "CONTINUOUS_BLOOD_OXYGEN_DATA: 288/day (5 min), live range 75-100"),
    _m(8, "temperature", "°C", "Skin Temperature", "likely",
       "CONTINUOUS_TEMPERATURE_DATA: 288 u16-BE/day; raw/100 gives "
       "30.9-33.8 worn (scale inferred)"),
    _m(11, "standing", None, "Standing Hours", "verified",
       "EFFECTIVE_STANDING_DATA: 24 hourly 0/1 flags"),
    _m(12, "activity_duration", "min", "Activity Duration", "likely",
       "ACTIVITY_DURATION_DATA: 24 hourly minutes; NoiseFit ignores it "
       "for REP"),
    _m(14, "movement_level", None, "Movement Level", "verified",
       "GoMore SEOverallDayMovementData: 288/day level codes; NoiseFit "
       "counts levels 1-3 as active"),
    _m(15, "sleep_result", None, "Sleep", "verified",
       "GoMore SERingSleepResultData: on-band sleep window, stages, score"),
    _m(16, "sleep_nap", None, "Naps", "verified",
       "GoMore SERingSleepNapData"),
    _m(17, "health_score_record", None, "Health Score", "verified",
       "GoMore SERingHealthScoreData: healthscore + sedentary seconds"),
    _m(18, "activity_indicator", None, "Activity Indicator", "likely",
       "GoMore SETodayActiveTypeData: 288/day codes, meaning not decoded"),
    _m(19, "respiratory_rate", "brpm", "Respiratory Rate", "verified",
       "GoMore SETodayRespiratoryRateData: 288/day, live range 6-22"),
    _m(20, "auto_workout", None, "Auto-detected Workouts", "verified",
       "GoMore SEAutoActiveSportData: walk/run sessions"),
    _m(21, "body_battery", None, "Body Battery", "verified",
       "GoMore SEBodyBatteryData: 96/day (15 min), rises 69->97 overnight"),
    _m(22, "stress", None, "Stress", "verified",
       "GoMore SEStressDetectionData: 288/day (5 min), 0-100"),
    _m(23, "band_battery", "%", "Band Battery", "verified",
       "RING_BATTERY_DATA: hourly; matches device-info battery"),
    _m(25, "post_workout_hr", "bpm", "Post-workout HR", "likely",
       "CONTINUOUS_HEART_RATE_SPORT_FIVEMIN_AFTER_DATA (raw kept)"),
    _m(26, "sleep_rri", "ms", "Sleep R-R Intervals", "likely",
       "Sleep_RRI_DATA (raw kept; NoiseFit drops it)"),
    _m(27, "sleep_hrv", "ms", "Sleep HRV", "likely",
       "Sleep_HRV_DATA: 2880/day (30 s), 1 B/sample; NoiseFit's only HRV "
       "source for REP. RMSSD vs vendor index unknown"),
)}


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

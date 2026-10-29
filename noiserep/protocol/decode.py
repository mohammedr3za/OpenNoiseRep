"""Decode reassembled Noise REP command payloads into typed records."""
from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, field as dc_field

from ..wire import field, field_all, parse, sub
from .files import (
    STAGE_AWAKE, STAGE_DEEP, STAGE_LIGHT, STAGE_REM, STAGE_UNKNOWN,
    SleepSession, SleepStage,
)


class DecodeError(Exception):
    pass


def _guarded(fn):
    """Translate low-level parse errors on malformed input into DecodeError."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DecodeError:
            raise
        except (ValueError, TypeError, IndexError, OverflowError) as exc:
            raise DecodeError(f"malformed payload in {fn.__name__}: {exc}") from exc
    return wrapper


@dataclass
class DeviceInfo:
    firmware: str
    serial: str
    mac: str
    model: str


def _s(buf: bytes, fno: int) -> str:
    v = field(buf, fno, b"")
    return v.decode("ascii", "replace") if isinstance(v, (bytes, bytearray)) else ""


@_guarded
def decode_device_info(payload: bytes) -> DeviceInfo:
    di = sub(payload, 4, 1)
    if di is None:
        raise DecodeError("no device-info sub-message (field 4 -> 1) in payload")
    return DeviceInfo(_s(di, 1), _s(di, 2), _s(di, 3), _s(di, 4))


@_guarded
def decode_date(buf: bytes) -> dt.datetime:
    return dt.datetime(
        field(buf, 1), field(buf, 2), field(buf, 3),
        field(buf, 4, 0), field(buf, 5, 0), field(buf, 6, 0),
    )


@dataclass
class IndexRecord:
    date: dt.datetime
    metric_id: int


@dataclass
class DataIndex:
    records: list["IndexRecord"]


@_guarded
def decode_index(payload: bytes) -> DataIndex:
    container = sub(payload, 9, 2)
    if container is None:
        raise DecodeError("no index container (field 9 -> 2) in payload")
    records = []
    for entry in field_all(container, 1):
        records.append(IndexRecord(decode_date(field(entry, 1)), field(entry, 2)))
    return DataIndex(records)


#: Metric 0 (SEDailyData) is a composite record whose arrays are one value per
#: hour.  Channel name -> body field number.  Arrays are big-endian u16.
DAILY_CHANNELS: tuple[tuple[str, int], ...] = (
    ("steps", 3),
    ("distance_m", 5),
    ("calories", 7),
    ("active_calories", 15),
)

#: Vendor-computed day totals that accompany the hourly arrays (field number).
#: Distance has no scalar total, so it is summed from its array.
DAILY_TOTAL_FIELDS: dict[str, int] = {
    "steps": 9,
    "calories": 10,
    "active_calories": 13,
}

#: Array metrics: id -> (body field with the samples, bytes/sample, scale).
#: Field numbers are the vendor schema's; u16 is big-endian like metric 0.
ARRAY_LAYOUT: dict[int, tuple[int, int, float]] = {
    2: (3, 1, 1), 4: (3, 1, 1), 8: (3, 2, 0.01), 11: (3, 1, 1),
    12: (3, 1, 1), 14: (2, 1, 1), 18: (2, 1, 1), 19: (2, 1, 1),
    21: (3, 1, 1), 22: (3, 1, 1), 23: (3, 1, 1), 27: (2, 1, 1),
}

#: Movement levels NoiseFit counts as active (DataConverter.V).
ACTIVE_LEVELS = range(1, 4)

#: GoMore stage enum -> canonical STAGE_* codes.
GOMORE_STAGE = {0: STAGE_AWAKE, 1: STAGE_LIGHT, 2: STAGE_DEEP, 3: STAGE_REM}

AUTO_WORKOUT_KIND = {0: "activity", 1: "walk", 2: "run"}
AUTO_WORKOUT_LEVEL = {0: "low", 1: "medium", 2: "high"}

#: The activity arrays cover a whole day at one value per hour.
DAILY_HOURS = 24


@dataclass
class DailyActivity:
    """Decoded metric-0 hourly channels and their day totals."""

    channels: dict[str, list[int]]
    totals: dict[str, int]


def _hourly_u16(value, hours: int = DAILY_HOURS) -> list[int] | None:
    """Decode up to ``hours`` big-endian u16 values from a byte string.

    Returns ``None`` when the field is absent or too short to hold one value.
    An odd trailing byte is ignored rather than shifting every later value, and
    an over-long array is truncated, so a malformed length cannot manufacture
    hours.  A missing channel stays absent; a present all-zero channel stays a
    row of zeros (the store's zero-skipping decides what to persist).
    """
    if not isinstance(value, (bytes, bytearray)) or len(value) < 2:
        return None
    count = min(len(value) // 2, hours)
    return [int.from_bytes(value[i * 2:i * 2 + 2], "big")
            for i in range(count)]


def decode_daily_channels(body: bytes) -> DailyActivity:
    """Decode one metric-0 series body into hourly channels and day totals.

    The body (SEDailyData) carries the hourly arrays in ``f3`` steps, ``f5``
    distance in metres, ``f7`` total calories and ``f15`` active (sport)
    calories.  Scalars ``f9``/``f10``/``f13`` are the vendor totals for steps /
    calories / active calories; when a scalar is absent (or truncated to a
    non-integer) the array is summed instead.  ``f14`` is a cumulative calorie
    array, not an independent total, and is deliberately not exposed.

    Only channels whose field is a valid u16 array are returned; a channel with
    no evidence is omitted rather than filled with zeros.
    """
    channels: dict[str, list[int]] = {}
    for name, fno in DAILY_CHANNELS:
        values = _hourly_u16(field(body, fno))
        if values is not None:
            channels[name] = values

    totals: dict[str, int] = {}
    for name, _ in DAILY_CHANNELS:
        if name not in channels:
            continue
        if name == "distance_m":
            totals[name] = sum(channels[name])
            continue
        scalar = field(body, DAILY_TOTAL_FIELDS[name])
        totals[name] = scalar if isinstance(scalar, int) else sum(channels[name])
    return DailyActivity(channels=channels, totals=totals)


@_guarded
def decode_daily_activity(payload: bytes) -> DailyActivity | None:
    """Decode the metric-0 series in a full type-113/115 payload.

    Returns ``None`` when the payload has no metric-0 body.  This is a
    convenience over :func:`decode_series` for callers that only want the
    daily-activity channels.
    """
    container = field(payload, 9)
    if not isinstance(container, (bytes, bytearray)):
        return None
    for _, wt, body in parse(container):
        if wt != 2:
            continue
        entry = field(body, 1)
        if not isinstance(entry, (bytes, bytearray)):
            continue
        if field(entry, 2) != 0:
            continue
        return decode_daily_channels(body)
    return None


@dataclass
class MetricSeries:
    date: dt.datetime
    metric_id: int
    interval_s: int | None
    samples: list[int]
    raw: bytes = b""
    #: Metric 0 only: decoded hourly arrays keyed by channel name.
    channels: dict[str, list[int]] = dc_field(default_factory=dict)
    #: Metric 0 only: day totals keyed by channel name.
    totals: dict[str, int] = dc_field(default_factory=dict)


def _find_body(payload: bytes) -> tuple[int, dt.datetime, bytes]:
    """Locate the f9 sub-message that carries an entry (f1 = date + id).

    The sub-field number is the vendor's oneof slot and varies per metric, so
    the body is found by shape, not by number.
    """
    container = field(payload, 9)
    if not isinstance(container, (bytes, bytearray)):
        raise DecodeError("no series container (field 9) in payload")
    for _, wt, body in parse(container):
        if wt != 2:
            continue
        entry = field(body, 1)
        if not isinstance(entry, (bytes, bytearray)):
            continue
        date_buf, metric_id = field(entry, 1), field(entry, 2)
        if isinstance(date_buf, (bytes, bytearray)) and metric_id is not None:
            return metric_id, decode_date(date_buf), body
    raise DecodeError("no series body (f9 sub-message with entry) in payload")


def _array(value, width: int, scale: float) -> list:
    """Big-endian samples; an all-ones value (0xff / 0xffff) means no reading."""
    if not isinstance(value, (bytes, bytearray)) or len(value) < width:
        return []
    blank = (1 << (8 * width)) - 1
    out = []
    for i in range(0, len(value) - width + 1, width):
        v = int.from_bytes(value[i:i + width], "big")
        out.append(0 if v == blank else (round(v * scale, 2) if scale != 1 else v))
    return out


def _active_minutes(levels: list) -> list[int]:
    """Hourly active minutes: slot length x count of active-level slots."""
    per_hour = len(levels) // DAILY_HOURS
    if not per_hour:
        return []
    slot_min = 60 // per_hour
    return [slot_min * sum(1 for v in levels[h * per_hour:(h + 1) * per_hour]
                           if v in ACTIVE_LEVELS)
            for h in range(DAILY_HOURS)]


def _channels_for(metric_id: int, body: bytes, samples: list) -> dict:
    """Derived per-day/hour channels for metrics that carry them."""
    if metric_id == 2 and isinstance(field(body, 6), int) and field(body, 6):
        return {"resting_hr": [field(body, 6)]}
    if metric_id == 14:
        return {"active_minutes": _active_minutes(samples)}
    if metric_id == 17:
        score, sedentary = field(body, 2), field(body, 3)
        out = {}
        if isinstance(score, int):
            out["health_score"] = [score]
        if isinstance(sedentary, int):
            out["sedentary_min"] = [sedentary // 60]
        return out
    return {}


@_guarded
def decode_series(payload: bytes) -> MetricSeries:
    """Decode one type-113 record body into samples and derived channels.

    Known array metrics use :data:`ARRAY_LAYOUT`; cadence is 86400 / count.
    Metric 0 keeps its raw f3 bytes in ``samples`` (backwards compatibility)
    and adds hourly ``channels``/``totals``.  Unknown layouts fall back to the
    old shape: f3 bytes as samples, else f2 bytes kept in ``raw``.
    """
    metric_id, date, body = _find_body(payload)
    f2, f3 = field(body, 2), field(body, 3)
    raw = b""
    if metric_id in ARRAY_LAYOUT:
        fno, width, scale = ARRAY_LAYOUT[metric_id]
        samples = _array(field(body, fno), width, scale)
        interval = 86400 // len(samples) if samples and not 86400 % len(samples) else None
    elif isinstance(f3, (bytes, bytearray)):
        samples = list(f3)
        interval = f2 if isinstance(f2, int) else None
    else:
        samples, interval = [], None
        raw = bytes(f2) if isinstance(f2, (bytes, bytearray)) else b""
    if metric_id == 0:
        activity = decode_daily_channels(body)
        return MetricSeries(date, 0, interval, samples, raw,
                            activity.channels, activity.totals)
    return MetricSeries(date, metric_id, interval, samples, raw,
                        _channels_for(metric_id, body, samples))


def _local(ts: int) -> dt.datetime:
    # ponytail: band epochs are UTC; shown in this machine's zone, assumed to
    # match the band's (it gets UTC + offset from the phone). Pass the band
    # offset through here if the two ever differ.
    return dt.datetime.fromtimestamp(ts)


def _minutes(value) -> int | None:
    return value // 60 if isinstance(value, int) else None


def _list_items(body: bytes, fno: int) -> list[bytes]:
    """Items of a vendor ``SEList`` (repeated field 1) held in ``body.fno``."""
    holder = sub(body, fno)
    return field_all(holder, 1) if holder is not None else []


def decode_sleep_result(body: bytes) -> SleepSession | None:
    """GoMore SERingSleepResultData (metric 15): the band's own sleep analysis.

    Durations arrive in seconds.  ``None`` when the band says no sleep.
    """
    entry, wake = field(body, 3), field(body, 4)
    if not field(body, 2) or not entry or not wake:
        return None
    stages = []
    for item in _list_items(body, 14):
        start, kind = field(item, 1), field(item, 3, 0)
        if start:
            stages.append(SleepStage(_local(start),
                                     GOMORE_STAGE.get(kind, STAGE_UNKNOWN), kind))
    return SleepSession(
        bedtime=_local(entry), wake=_local(wake),
        total_min=_minutes(field(body, 5)), deep_min=_minutes(field(body, 12)),
        light_min=_minutes(field(body, 11)), rem_min=_minutes(field(body, 13)),
        awake_min=_minutes(field(body, 10)), quality=field(body, 9),
        stages=stages, source="band",
        latency_min=_minutes(field(body, 7)), efficiency=field(body, 8),
        time_in_bed_min=_minutes(field(body, 6)),
    )


def decode_naps(body: bytes) -> list[SleepSession]:
    """GoMore SERingSleepNapData (metric 16)."""
    naps = []
    for item in _list_items(body, 2):
        start, end = field(item, 2), field(item, 3)
        if field(item, 1) and start and end:
            naps.append(SleepSession(bedtime=_local(start), wake=_local(end),
                                     total_min=_minutes(field(item, 4)),
                                     source="nap"))
    return naps


@dataclass(frozen=True)
class Workout:
    """One workout session; ``source`` is ``"auto"`` for band-detected ones."""

    start: dt.datetime
    kind: str
    duration_s: int | None
    level: str | None = None
    steps: int | None = None
    kcal: int | None = None
    source: str = "auto"


def decode_auto_workouts(body: bytes) -> list[Workout]:
    """GoMore SEAutoActiveSportData (metric 20): band-detected walks/runs.

    The duration unit is assumed seconds (unverified until a live record).
    """
    out = []
    for item in _list_items(body, 2):
        start = field(item, 3)
        if not start:
            continue
        out.append(Workout(
            start=_local(start),
            kind=AUTO_WORKOUT_KIND.get(field(item, 1, 0), "activity"),
            duration_s=field(item, 4), level=AUTO_WORKOUT_LEVEL.get(field(item, 2, 0)),
            steps=field(item, 5), kcal=field(item, 6)))
    return out


#: Record metrics: id -> decoder returning a list of records.
RECORD_DECODERS = {
    15: lambda body: [s for s in [decode_sleep_result(body)] if s],
    16: decode_naps,
    20: decode_auto_workouts,
}


@dataclass
class SportIds:
    """Reply to type 117 (GET_FITNESS_SPORT_ID_LIST): stored workout ids.

    The id encoding is not known yet; ``raw`` is kept for when a workout
    recorded on the band makes it decodable.
    """

    raw: bytes


@_guarded
def decode_fitness(payload: bytes):
    """A type-113/115 payload as a MetricSeries, or a list of records."""
    metric_id, _, body = _find_body(payload)
    if metric_id in RECORD_DECODERS:
        return RECORD_DECODERS[metric_id](body)
    return decode_series(payload)


@dataclass
class DeviceFile:
    name: str
    data: bytes
    flag: int


@_guarded
def decode_file(payload: bytes) -> DeviceFile:
    f = sub(payload, 16, 1)
    if f is None:
        raise DecodeError("no file sub-message (field 16 -> 1) in payload")
    name = field(f, 1, b"")
    return DeviceFile(
        name=name.decode("ascii", "replace") if isinstance(name, (bytes, bytearray)) else "",
        data=field(f, 2, b""),
        flag=field(f, 3, 0),
    )


@dataclass
class Status:
    type: int
    status: int


@dataclass
class Unknown:
    type: int
    raw: bytes


@_guarded
def decode_payload(payload: bytes):
    t = field(payload, 1)
    status = field(payload, 100)
    if status is not None:
        return Status(t, status)
    if t == 32:
        return decode_device_info(payload)
    if t == 112:
        return decode_index(payload)
    if t in (113, 115):
        return decode_fitness(payload)
    if t == 117:
        ids = sub(payload, 9)
        raw = field(ids, 3, b"") if ids is not None else b""
        return SportIds(bytes(raw) if isinstance(raw, (bytes, bytearray)) else b"")
    if t == 4097:
        return decode_file(payload)
    return Unknown(t, payload)

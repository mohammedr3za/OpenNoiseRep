"""Decode reassembled Noise REP command payloads into typed records."""
from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, field as dc_field

from ..wire import field, field_all, parse, sub


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


#: Metric 0 is a composite daily-activity record whose arrays are one value per
#: hour.  Channel name -> body field number.  Arrays are big-endian u16.
DAILY_CHANNELS: tuple[tuple[str, int], ...] = (
    ("steps", 3),
    ("distance_m", 5),
    ("calories", 7),
    ("active_minutes", 15),
)

#: Vendor-computed day totals that accompany the hourly arrays (field number).
#: Distance has no scalar total, so it is summed from its array.
DAILY_TOTAL_FIELDS: dict[str, int] = {
    "steps": 9,
    "calories": 10,
    "active_minutes": 13,
}

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

    The body (a type-113/115 ``f9`` sub-message) carries the hourly arrays in
    ``f3`` steps, ``f5`` distance in metres, ``f7`` active calories and ``f15``
    active minutes.  Scalars ``f9``/``f10``/``f13`` are the vendor totals for
    steps / calories / active minutes; when a scalar is absent (or truncated to
    a non-integer) the array is summed instead.  ``f14`` is a cumulative
    calorie array, not an independent total, and is deliberately not exposed.

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


@_guarded
def decode_series(payload: bytes) -> MetricSeries:
    """Each series lives in its own f9 sub-field (6, 8, 12, ...); find the one
    whose message carries an entry (f1 = date + metric id).

    samples / interval_s come only from f3 (a byte array whose cadence is f2).
    Without f3, samples is empty and interval_s is None; if f2 is bytes they
    are kept untouched in `raw` (structure not yet understood).

    For metric 0 (daily activity) the raw f3 bytes stay in ``samples`` for
    backwards compatibility, while the decoded 24-hour u16 arrays are added in
    ``channels`` and ``totals`` (see :func:`decode_daily_channels`).
    """
    container = field(payload, 9)
    if not isinstance(container, (bytes, bytearray)):
        raise DecodeError("no series container (field 9) in payload")
    for fno, wt, body in parse(container):
        if wt != 2:
            continue
        entry = field(body, 1)
        if not isinstance(entry, (bytes, bytearray)):
            continue
        date_buf = field(entry, 1)
        metric_id = field(entry, 2)
        if not isinstance(date_buf, (bytes, bytearray)) or metric_id is None:
            continue
        f2, f3 = field(body, 2), field(body, 3)
        if isinstance(f3, (bytes, bytearray)):
            samples = list(f3)
            interval = f2 if isinstance(f2, int) else None
            raw = b""
        else:
            samples, interval = [], None
            raw = bytes(f2) if isinstance(f2, (bytes, bytearray)) else b""
        channels: dict[str, list[int]] = {}
        totals: dict[str, int] = {}
        if metric_id == 0:
            activity = decode_daily_channels(body)
            channels, totals = activity.channels, activity.totals
        return MetricSeries(decode_date(date_buf), metric_id, interval, samples,
                            raw, channels, totals)
    raise DecodeError("no series body (f9 sub-message with entry) in payload")


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
        return decode_series(payload)
    if t == 4097:
        return decode_file(payload)
    return Unknown(t, payload)

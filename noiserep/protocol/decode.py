"""Decode reassembled Noise REP command payloads into typed records."""
from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass

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


@dataclass
class MetricSeries:
    date: dt.datetime
    metric_id: int
    interval_s: int | None
    samples: list[int]
    raw: bytes = b""


@_guarded
def decode_series(payload: bytes) -> MetricSeries:
    """Each series lives in its own f9 sub-field (6, 8, 12, ...); find the one
    whose message carries an entry (f1 = date + metric id).

    samples / interval_s come only from f3 (a byte array whose cadence is f2).
    Without f3, samples is empty and interval_s is None; if f2 is bytes they
    are kept untouched in `raw` (structure not yet understood)."""
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
        return MetricSeries(decode_date(date_buf), metric_id, interval, samples, raw)
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

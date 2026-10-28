"""Real-time heart-rate commands and parsers (Xiaomi wear protocol).

The realtime path is the vendor's legacy stream:

* ``type=8 health`` (field 1) with ``subtype`` 45 (start), 46 (stop) and 47
  (periodic event), as used by the Gadgetbridge Xiaomi health service and by
  this project's early ``live_hr.py`` / ``rt2.py`` probes.
* An event carries ``health`` in field 10, and inside it ``realTimeStats`` in
  field 39: ``steps`` (1), ``calories`` (2), ``heartRate`` (4),
  ``standingHours`` (6).

Confidence: the field layout is taken from the reference ``xiaomi.proto`` and
matches every legacy script, but the command has **not** been confirmed on this
band with the current chunked transport. Treat the stream as experimental; the
menu bar falls back to polling the confirmed type-113 metric-2 series whenever
it is unavailable. Every frame here is still sent through ``Band.send()``
(i.e. chunked), never as a type-2 single frame.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..wire import encode, field
from .decode import DecodeError, decode_series

HEALTH_TYPE = 8
REALTIME_START = 45
REALTIME_STOP = 46
REALTIME_EVENT = 47

# Command envelope: field 1 = type, field 2 = subtype, field 10 = health.
HEALTH_FIELD = 10
# Health.RealTimeStats is field 39; its heart rate is field 4.
REALTIME_STATS_FIELD = 39
HR_FIELD = 4
STEPS_FIELD = 1
CALORIES_FIELD = 2
STANDING_FIELD = 6

# The confirmed fallback: type 113, metric 2, 30 s cadence heart rate.
HEART_RATE_METRIC = 2


def start_frame() -> bytes:
    """``Command{type: 8, subtype: 45}`` -> enable the realtime stream."""
    return encode([(1, HEALTH_TYPE), (2, REALTIME_START)])


def stop_frame() -> bytes:
    """``Command{type: 8, subtype: 46}`` -> disable the realtime stream."""
    return encode([(1, HEALTH_TYPE), (2, REALTIME_STOP)])


def is_event(payload: bytes) -> bool:
    """True when ``payload`` is a realtime event envelope."""
    try:
        return field(payload, 1) == HEALTH_TYPE and field(payload, 2) == REALTIME_EVENT
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class RealtimeSample:
    """One periodic realtime sample. ``heart_rate`` 0 means "no reading"."""

    heart_rate: int = 0
    steps: int = 0
    calories: int = 0
    standing_hours: int = 0


def _uint(buf: bytes, fno: int) -> int:
    value = field(buf, fno, 0)
    return int(value) if isinstance(value, int) else 0


def parse_event(payload: bytes) -> RealtimeSample | None:
    """Parse a subtype-47 reply into a sample, or return ``None``.

    Never raises on a non-event or malformed frame: the band also echoes
    plain status replies on this connection, and those must be ignored.
    """
    try:
        if field(payload, 1) != HEALTH_TYPE or field(payload, 2) != REALTIME_EVENT:
            return None
        health = field(payload, HEALTH_FIELD)
        if not isinstance(health, (bytes, bytearray)):
            return None
        stats = field(health, REALTIME_STATS_FIELD)
        if not isinstance(stats, (bytes, bytearray)):
            return None
        return RealtimeSample(
            heart_rate=_uint(stats, HR_FIELD),
            steps=_uint(stats, STEPS_FIELD),
            calories=_uint(stats, CALORIES_FIELD),
            standing_hours=_uint(stats, STANDING_FIELD),
        )
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


def parse_bpm(payload: bytes) -> int | None:
    """Heart rate from a realtime event, or ``None`` when not a usable sample."""
    sample = parse_event(payload)
    if sample is None or sample.heart_rate <= 0:
        return None
    return sample.heart_rate


def latest_series_bpm(payload: bytes) -> int | None:
    """Last non-zero heart rate in a confirmed type-113 metric-2 reply.

    The band pads each day's sample array with zeros for "no reading", so the
    newest actual reading is the last non-zero value. Returns ``None`` for a
    status/empty reply, another metric, or malformed bytes.
    """
    try:
        series = decode_series(payload)
    except (DecodeError, ValueError, TypeError, IndexError):
        return None
    if series.metric_id != HEART_RATE_METRIC:
        return None
    for value in reversed(series.samples):
        if value:
            return int(value)
    return None


__all__ = [
    "HEALTH_TYPE", "REALTIME_START", "REALTIME_STOP", "REALTIME_EVENT",
    "HEART_RATE_METRIC", "RealtimeSample",
    "start_frame", "stop_frame", "is_event", "parse_event", "parse_bpm",
    "latest_series_bpm",
]

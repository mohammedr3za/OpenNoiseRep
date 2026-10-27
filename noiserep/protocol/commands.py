"""Builders for the confirmed Noise REP command frames. Every frame here was
lifted byte-for-byte from a real NoiseFit sync capture and verified byte-exact
against it. Frames are
the protobuf Command envelope: field 1 is the command type, field 9 carries
parameters."""
from __future__ import annotations

import datetime

from .. import wire

# The metric ids NoiseFit requests, in its own order (from the capture).
METRIC_IDS: tuple[int, ...] = (2, 4, 8, 14, 18, 19, 17, 0, 21, 22, 23, 26, 27, 25, 11, 12)


def device_info() -> bytes:
    """type 32 -> firmware / serial / mac / model."""
    return wire.encode([(1, 32)])


def data_index() -> bytes:
    """type 112 -> list of available day/metric records. f9 = {field 29: 3}."""
    return wire.encode([(1, 112), (9, wire.encode([(29, 3)]))])


def series(cmd_type: int, dt: datetime.datetime, metric_id: int) -> bytes:
    """type 113/115 -> a metric's sample series since `dt`.

    Envelope: Command{ f1=cmd_type, f9{ f1{ f1=<date>, f2=metric_id } } }
    where date = { f1=year, f2=month, f3=day, f4=hour, f5=min, f6=sec }.
    """
    date = wire.encode([
        (1, dt.year), (2, dt.month), (3, dt.day),
        (4, dt.hour), (5, dt.minute), (6, dt.second),
    ])
    entry = wire.encode([(1, date), (2, metric_id)])
    query = wire.encode([(1, entry)])
    return wire.encode([(1, cmd_type), (9, query)])


def control_117() -> bytes:
    """Short control command sent late in the sync (part of the file trigger)."""
    return wire.encode([(1, 117)])


def control_123() -> bytes:
    """Short control command sent late in the sync. f9 = {field 28: 1}."""
    return wire.encode([(1, 123), (9, wire.encode([(28, 1)]))])

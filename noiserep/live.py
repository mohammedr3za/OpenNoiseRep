"""Always-on band link: live values every few seconds plus periodic history.

The live path is the vendor's own: ``SEWear{164 PHONE_SEND_BASIC_DATA_REPORT,
micro_function{basic_data_mode: START}}`` makes the band answer with ``165
REPORT_BASIC_DATA`` (``SEBasicData``) at once and again whenever a value
changes.  Re-sending START every ``interval`` seconds therefore doubles as a
poll.  The band accepts one central, so history syncs run on the same link
with the live stream paused.  Any drop reconnects after ``retry_s``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import time
from dataclasses import dataclass

from . import wire

BASIC_DATA_REPORT = 164
REPORT_BASIC_DATA = 165
GET_RING_WEAR_STATUS = 420
SCAN_WINDOW_S = 60.0
MAX_SILENT_POLLS = 3
MAX_RETRY_S = 30.0
MICRO_FUNCTION_FIELD = 12
WEARING_FIELD = 12          # SEMicroFunction.inquiry_wearing_status


def wear_query() -> bytes:
    return wire.encode([(1, GET_RING_WEAR_STATUS)])


def parse_wear(payload: bytes) -> bool | None:
    """``420`` reply -> worn?  Reads 0 on the charger.

    ponytail: any non-zero value is taken as "on the wrist"; confirm the exact
    worn code on a live band before trusting other values.
    """
    try:
        if wire.field(payload, 1) != GET_RING_WEAR_STATUS:
            return None
        mf = wire.sub(payload, MICRO_FUNCTION_FIELD)
        value = wire.field(mf, WEARING_FIELD) if mf is not None else None
        return bool(value) if isinstance(value, int) else None
    except (ValueError, TypeError):
        return None


def basic_data(start: bool) -> bytes:
    """``164`` with ``basic_data_mode`` START (0) or STOP (1)."""
    return wire.encode([(1, BASIC_DATA_REPORT),
                        (MICRO_FUNCTION_FIELD, wire.encode([(3, 0 if start else 1)]))])


@dataclass(frozen=True)
class LiveSample:
    """One ``SEBasicData`` report; ``None`` where the band left a field out."""

    at: dt.datetime
    steps: int | None
    calories: int | None          # today_oura_calorie_data (total), else f2
    active_calories: int | None
    distance_m: int | None
    heart_rate: int | None        # 0 while charging/off-wrist -> None
    spo2: int | None
    stress: int | None
    battery: int | None
    charging: bool | None
    worn: bool | None = None

    @property
    def place(self) -> str:
        """``"charging"``, ``"wrist"``, ``"off_wrist"`` or ``"unknown"``."""
        if self.charging:
            return "charging"
        if self.worn is None:
            return "unknown"
        return "wrist" if self.worn else "off_wrist"


def _int(buf: bytes, fno: int):
    value = wire.field(buf, fno)
    return value if isinstance(value, int) else None


def parse_basic(payload: bytes, now: dt.datetime | None = None) -> LiveSample | None:
    """Decode a ``165`` report, or ``None`` for any other frame."""
    try:
        if wire.field(payload, 1) != REPORT_BASIC_DATA:
            return None
        data = wire.sub(payload, MICRO_FUNCTION_FIELD, 4)
        if data is None:
            return None
        battery = wire.sub(data, 7)
        status = _int(battery, 2) if battery is not None else None
        return LiveSample(
            at=now or dt.datetime.now().replace(microsecond=0),
            steps=_int(data, 1),
            calories=_int(data, 14) or _int(data, 2),
            active_calories=_int(data, 15),
            distance_m=_int(data, 3),
            heart_rate=_int(data, 4) or None,
            spo2=_int(data, 5) or None,
            stress=_int(data, 13) or None,
            battery=_int(battery, 1) if battery is not None else None,
            charging=None if status in (None, 0) else status in (1, 3),
        )
    except (ValueError, TypeError):
        return None


async def _drain(band, max_messages: int = 32) -> None:
    """Wait for a quiet stream; never feed stale live packets into sync."""
    from .errors import TransportError

    deadline = time.monotonic() + 2.0
    for _ in range(max_messages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            await band.recv(timeout=min(remaining, 0.5))
        except asyncio.TimeoutError:
            return
    raise TransportError("Live stream did not become quiet before history sync")


async def _history(band, store, on_progress=None) -> None:
    from .sync import run_sync

    await band.send(basic_data(False))
    await _drain(band)
    result = await run_sync(band, dt.datetime(2000, 1, 1), progress=on_progress)
    store.ingest(result, note="live link history sync")


async def _poll(band, store, interval: float, on_sample, wake=None) -> bool:
    """One poll: wear status, then live reports until ``interval`` or ``wake``."""
    await band.send(wear_query())
    await band.send(basic_data(True))
    deadline = time.monotonic() + interval
    last, worn = None, None
    received = False
    while (left := deadline - time.monotonic()) > 0:
        if wake is not None and wake.is_set():
            return received
        try:
            msg = await band.recv(timeout=min(left, 1.0))
        except asyncio.TimeoutError:
            continue
        if msg == last:
            continue                  # the band often sends a report twice
        last = msg
        state = parse_wear(msg)
        if state is not None:
            worn = state
            continue
        sample = parse_basic(msg)
        if sample is not None:
            sample = dataclasses.replace(sample, worn=worn)
            store.write_live(sample)
            on_sample(sample)
            received = True
    return received


async def keep_linked(store, name: str | None = None, interval: float = 10.0,
                      history_every: float = 1800.0, retry_s: float = 3.0,
                      on_sample=lambda s: None, on_status=lambda s: None,
                      sync_now: asyncio.Event | None = None,
                      on_history=lambda: None, on_progress=None) -> None:
    """Run forever: connect, sync history, poll live values, reconnect.

    Setting ``sync_now`` forces a history sync at the next poll boundary.
    ``on_progress(done, total, stage)`` is forwarded to ``run_sync``.
    """
    from bleak.exc import BleakError

    from .ble.connection import connect, remembered_address
    from .errors import BandNotFound, ConnectionCleanupError, TransportError

    if interval <= 0 or history_every <= 0 or retry_s < 0:
        raise ValueError("Live/history intervals must be positive; retry delay cannot be negative")
    address = remembered_address(store, name)
    failures = 0
    while True:
        try:
            async with connect(name, scan_timeout=15.0 if address else SCAN_WINDOW_S,
                               address=address, on_status=on_status) as band:
                address = getattr(band, "address", None) or address
                name = name or getattr(band, "name", None)
                on_status("connected")
                # Deliver a current report before the potentially long history sync.
                initial_report = await _poll(band, store, min(interval, 3.0), on_sample)
                silent = 0 if initial_report else 1
                if initial_report:
                    failures = 0
                    on_status("live")
                last_history = float("-inf")
                while band.c.is_connected:
                    forced = sync_now is not None and sync_now.is_set()
                    if forced or (silent == 0 and time.monotonic() - last_history >= history_every):
                        if sync_now is not None:
                            sync_now.clear()
                        on_status("syncing history")
                        await _history(band, store, on_progress)
                        last_history = time.monotonic()
                        on_history()
                        on_status("live")
                    report = await _poll(band, store, interval, on_sample, sync_now)
                    if report:
                        if silent:
                            on_status("live")
                        silent = 0
                        failures = 0
                    elif sync_now is None or not sync_now.is_set():
                        silent += 1
                        if silent >= MAX_SILENT_POLLS:
                            raise TransportError("Band link stopped delivering live reports")
        except ConnectionCleanupError as exc:
            on_status(f"disconnected ({exc}); close and reopen the app before retrying")
            raise  # the owner must know release failed; a handoff is unsafe
        except (BandNotFound, TransportError, BleakError, OSError,
                asyncio.TimeoutError) as exc:
            on_status(f"disconnected ({str(exc) or type(exc).__name__}); retrying")
        failures = min(failures + 1, 6)
        delay = min(MAX_RETRY_S, retry_s * 2 ** (failures - 1))
        on_status(f"retrying in {delay:g} seconds")
        await asyncio.sleep(delay)

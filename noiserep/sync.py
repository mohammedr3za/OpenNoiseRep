"""Drive the confirmed Noise REP historical-sync choreography over a Band and
collect decoded records. Pure orchestration: no bleak, no user-facing text."""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import time
from dataclasses import dataclass, field

from .errors import TransportError
from .protocol import commands
from .protocol.decode import (
    decode_payload, DeviceInfo, DataIndex, IndexRecord, MetricSeries,
    DeviceFile, SportIds, Workout,
)
from .protocol.files import (
    RriFile, SleepSession, assemble_activity_files, decode_activity_file,
)
from .protocol.metrics import metric_name


@dataclass
class SyncResult:
    device: "DeviceInfo | None" = None
    index: "DataIndex | None" = None
    series: list = field(default_factory=list)
    files: list = field(default_factory=list)
    raw: list = field(default_factory=list)      # list[tuple[label, bytes]]
    rri: list = field(default_factory=list)      # list[RriFile]
    sleep: list = field(default_factory=list)    # list[SleepSession]
    workouts: list = field(default_factory=list)  # list[Workout]
    sport_ids: "SportIds | None" = None


def _decode_pushed_files(result: SyncResult) -> None:
    """Assemble and decode type-4097 chunks into RRI / sleep records."""
    for activity in assemble_activity_files(result.files):
        # The name stamps the end of an RRI window; decode_activity_file
        # converts it to a slot-0 start.
        decoded = decode_activity_file(activity.name, activity.data)
        if isinstance(decoded, RriFile):
            result.rri.append(decoded)
        elif isinstance(decoded, SleepSession):
            result.sleep.append(decoded)


def _sort(rec, result: SyncResult) -> None:
    if isinstance(rec, list):
        for item in rec:
            _sort(item, result)
    elif isinstance(rec, DeviceInfo):
        result.device = rec
    elif isinstance(rec, DataIndex):
        result.index = rec
    elif isinstance(rec, MetricSeries):
        result.series.append(rec)
    elif isinstance(rec, SleepSession):
        result.sleep.append(rec)
    elif isinstance(rec, Workout):
        result.workouts.append(rec)
    elif isinstance(rec, SportIds):
        result.sport_ids = rec
    elif isinstance(rec, DeviceFile):
        result.files.append(rec)


async def _ask(band, payload: bytes, label: str, result: SyncResult,
               timeout: float) -> bool:
    """Send one command; True when its reply arrived and decoded."""
    try:
        await band.send(payload)
        reply = await band.recv(timeout=timeout)
    except (asyncio.TimeoutError, TransportError):
        return False
    result.raw.append((label, reply))
    try:
        _sort(decode_payload(reply), result)
    except Exception:
        return False    # keep raw; a single odd frame must not abort the sync
    return True


def _records_to_fetch(result: SyncResult, since: datetime.datetime) -> list:
    """Every (date, metric) record the band listed, or the legacy fixed list.

    The band keeps one record per day per metric until it is confirmed, so a
    single fetch-since date would skip all but one day of each metric.
    """
    if result.index is not None and result.index.records:
        return list(result.index.records)
    return [IndexRecord(since, m) for m in commands.METRIC_IDS]


async def run_sync(band, since: datetime.datetime, want_files: bool = True,
                   reply_timeout: float = 6.0, file_timeout: float = 12.0,
                   overall_timeout: float = 90.0, progress=None) -> SyncResult:
    """Fetch history from a live band and collect decoded records.

    ``progress``, if given, is called ``progress(done, total, stage)`` as the
    sync advances, for a UI progress indicator. ``stage`` is a machine tag —
    ``"device"``, ``"index"``, ``"series"``, ``"control"``, ``"files"``
    (reported with ``total == 0`` because the file count is unknown ahead of
    time, i.e. indeterminate), then ``"done"``. The tags carry no user-facing
    wording, keeping this layer text-free; the caller maps them to labels.
    Exceptions raised by the callback are swallowed.
    """
    start = time.monotonic()
    result = SyncResult()
    total = 2 + len(commands.METRIC_IDS) * 2 + 2
    done = 0

    def report(stage: str, indeterminate: bool = False) -> None:
        if progress is None:
            return
        try:
            progress(done, 0 if indeterminate else total, stage)
        except Exception:
            pass

    await _ask(band, commands.device_info(), "devinfo", result, reply_timeout)
    done += 1
    report("device")
    await _ask(band, commands.data_index(), "index", result, reply_timeout)
    done += 1
    report("index")
    records = [r for r in _records_to_fetch(result, since) if r.date >= since]
    total = 2 + len(records) * 2 + 2
    for rec in records:
        if time.monotonic() - start > overall_timeout:
            break
        tag = f"{rec.metric_id}-{rec.date:%Y%m%d}"
        got = await _ask(band, commands.series(113, rec.date, rec.metric_id),
                         f"series-113-{tag}", result, reply_timeout)
        done += 1
        # 115 tells the band the record was delivered and may be dropped, so
        # only confirm what actually arrived and decoded.
        if got:
            await _ask(band, commands.series(115, rec.date, rec.metric_id),
                       f"series-115-{tag}", result, reply_timeout)
        done += 1
        report("series")
    await _ask(band, commands.control_123(), "ctl123", result, reply_timeout)
    done += 1
    report("control")
    await _ask(band, commands.control_117(), "ctl117", result, reply_timeout)
    done += 1
    report("control")
    if want_files:
        report("files", indeterminate=True)
        while True:
            if time.monotonic() - start > overall_timeout:
                break
            try:
                payload = await band.recv(timeout=file_timeout)
            except asyncio.TimeoutError:
                break
            result.raw.append(("file", payload))
            try:
                _sort(decode_payload(payload), result)
            except Exception:
                pass
    _decode_pushed_files(result)
    report("done")
    return result


def write_result(result: SyncResult, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    for i, (label, blob) in enumerate(result.raw):
        with open(os.path.join(out_dir, f"{i:03d}_{label}.bin"), "wb") as fh:
            fh.write(blob)
    doc = {
        "device": None if result.device is None else {
            "firmware": result.device.firmware, "serial": result.device.serial,
            "mac": result.device.mac, "model": result.device.model,
        },
        "index": None if result.index is None else [
            {"date": r.date.isoformat(), "metric_id": r.metric_id,
             "metric": metric_name(r.metric_id)} for r in result.index.records
        ],
        "series": [
            {"date": s.date.isoformat(), "metric_id": s.metric_id,
             "metric": metric_name(s.metric_id), "interval_s": s.interval_s,
             "samples": len(s.samples), "values": s.samples,
             "raw_bytes": len(s.raw)} for s in result.series
        ],
        "files": [
            {"name": f.name, "bytes": len(f.data), "flag": f.flag} for f in result.files
        ],
        "rri": [
            {"start": None if r.start is None else r.start.isoformat(),
             "samples": len(r.samples), "rejected": r.rejected,
             "slots": r.total_slots} for r in result.rri
        ],
        "sleep": [
            {"bedtime": s.bedtime.isoformat(), "wake": s.wake.isoformat(),
             "total_min": s.total_min, "deep_min": s.deep_min,
             "light_min": s.light_min, "rem_min": s.rem_min,
             "awake_min": s.awake_min, "quality": s.quality,
             "source": s.source} for s in result.sleep
        ],
        "workouts": [
            {"start": w.start.isoformat(), "kind": w.kind,
             "duration_s": w.duration_s, "level": w.level, "steps": w.steps,
             "kcal": w.kcal, "source": w.source} for w in result.workouts
        ],
        "sport_ids": None if result.sport_ids is None else result.sport_ids.raw.hex(),
    }
    with open(os.path.join(out_dir, "decoded.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    return out_dir

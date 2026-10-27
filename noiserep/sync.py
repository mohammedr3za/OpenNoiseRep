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
    decode_payload, DeviceInfo, DataIndex, MetricSeries, DeviceFile,
)
from .protocol.metrics import metric_name


@dataclass
class SyncResult:
    device: "DeviceInfo | None" = None
    index: "DataIndex | None" = None
    series: list = field(default_factory=list)
    files: list = field(default_factory=list)
    raw: list = field(default_factory=list)      # list[tuple[label, bytes]]


def _sort(rec, result: SyncResult) -> None:
    if isinstance(rec, DeviceInfo):
        result.device = rec
    elif isinstance(rec, DataIndex):
        result.index = rec
    elif isinstance(rec, MetricSeries):
        result.series.append(rec)
    elif isinstance(rec, DeviceFile):
        result.files.append(rec)


async def _ask(band, payload: bytes, label: str, result: SyncResult, timeout: float):
    try:
        await band.send(payload)
        reply = await band.recv(timeout=timeout)
    except (asyncio.TimeoutError, TransportError):
        return
    result.raw.append((label, reply))
    try:
        _sort(decode_payload(reply), result)
    except Exception:
        pass            # keep raw; a single odd frame must not abort the sync


async def run_sync(band, since: datetime.datetime, want_files: bool = True,
                   reply_timeout: float = 6.0, file_timeout: float = 12.0,
                   overall_timeout: float = 90.0) -> SyncResult:
    start = time.monotonic()
    result = SyncResult()
    await _ask(band, commands.device_info(), "devinfo", result, reply_timeout)
    await _ask(band, commands.data_index(), "index", result, reply_timeout)
    for metric_id in commands.METRIC_IDS:
        if time.monotonic() - start > overall_timeout:
            break
        for cmd_type in (113, 115):
            await _ask(band, commands.series(cmd_type, since, metric_id),
                       f"series-{cmd_type}-{metric_id}", result, reply_timeout)
    await _ask(band, commands.control_123(), "ctl123", result, reply_timeout)
    await _ask(band, commands.control_117(), "ctl117", result, reply_timeout)
    if want_files:
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
    }
    with open(os.path.join(out_dir, "decoded.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    return out_dir

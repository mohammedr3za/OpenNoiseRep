"""noiserep command-line interface. Phase 0: offline `decode` of a capture."""
from __future__ import annotations

import argparse
import json
import sys

from .capture import reassemble
from .protocol.decode import (
    decode_payload, DeviceInfo, DataIndex, MetricSeries, DeviceFile, Status, Unknown,
)
from .protocol.metrics import metric_name


def _decode(path: str, json_path: str | None) -> int:
    payloads = [p for p in reassemble(path) if p.direction == "recv"]
    device = None
    index = None
    series = []
    files = []
    failed = 0
    for p in payloads:
        try:
            rec = decode_payload(p.raw)
        except Exception as exc:  # one odd payload must not abort the run
            failed += 1
            continue
        if isinstance(rec, DeviceInfo):
            device = rec
        elif isinstance(rec, DataIndex):
            index = rec
        elif isinstance(rec, MetricSeries):
            series.append(rec)
        elif isinstance(rec, DeviceFile):
            files.append(rec)
        elif isinstance(rec, (Status, Unknown)):
            pass

    if failed:
        print(f"! {failed} frame(s) not decoded", file=sys.stderr)
    if device:
        print(f"device: fw {device.firmware}  model {device.model}  "
              f"mac {device.mac}  serial {device.serial}")
    if index:
        days = sorted({r.date.date().isoformat() for r in index.records})
        print(f"index: {len(index.records)} records across {days}")
    for s in series:
        head = (f"series: {s.date.date().isoformat()}  metric {s.metric_id} "
                f"({metric_name(s.metric_id)}): ")
        if s.samples:
            cadence = s.interval_s if s.interval_s is not None else "?"
            print(f"{head}{len(s.samples)} samples @ {cadence}s")
        elif s.raw:
            print(f"{head}{len(s.raw)} raw bytes (structure TBD)")
        else:
            print(f"{head}scalar/empty")
    for f in files:
        print(f"file: {f.name}  {len(f.data)} bytes  flag {f.flag}")

    if json_path:
        doc = {
            "device": None if not device else {
                "firmware": device.firmware, "serial": device.serial,
                "mac": device.mac, "model": device.model,
            },
            "index": None if not index else [
                {"date": r.date.isoformat(), "metric_id": r.metric_id,
                 "metric": metric_name(r.metric_id)} for r in index.records
            ],
            "series": [
                {"date": s.date.isoformat(), "metric_id": s.metric_id,
                 "metric": metric_name(s.metric_id), "interval_s": s.interval_s,
                 "samples": len(s.samples), "values": s.samples,
                 "raw_bytes": len(s.raw)} for s in series
            ],
            "files": [
                {"name": f.name, "bytes": len(f.data), "flag": f.flag} for f in files
            ],
        }
        with open(json_path, "w") as fh:
            json.dump(doc, fh, indent=1)
        print(f"wrote {json_path}")
    return 0


def _parse_since(value):
    import datetime
    if value:
        d = datetime.datetime.strptime(value, "%Y-%m-%d")
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _run_sync_async(name, since, want_files, debug):
    from .ble.connection import connect
    from .sync import run_sync
    async with connect(name, debug=debug) as band:
        return await run_sync(band, since, want_files=want_files)


def _sync(name, since_str, out, no_files, verbose):
    import asyncio
    import datetime
    from .sync import write_result
    from .errors import BandNotFound, TransportError
    since = _parse_since(since_str)
    try:
        result = asyncio.run(_run_sync_async(name, since, not no_files, verbose))
    except BandNotFound as exc:
        print(f"{exc}. Put the band on its charger, keep the phone's Bluetooth OFF, "
              f"and retry.", file=sys.stderr)
        return 3
    except TransportError as exc:
        print(f"transport error: {exc}", file=sys.stderr)
        return 4
    out_dir = out or f"data/{datetime.datetime.now():%Y%m%d-%H%M%S}"
    write_result(result, out_dir)
    if result.device:
        d = result.device
        print(f"device: fw {d.firmware}  model {d.model}  mac {d.mac}  serial {d.serial}")
    if result.index:
        print(f"index: {len(result.index.records)} records")
    with_samples = [s for s in result.series if s.samples]
    print(f"series: {len(result.series)} ({len(with_samples)} with samples)")
    print(f"files: {len(result.files)}")
    print(f"wrote {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noiserep")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode a .pklg capture offline")
    d.add_argument("pklg")
    d.add_argument("--json", dest="json_path", default=None)
    s = sub.add_parser("sync", help="fetch history from a live band over BLE")
    s.add_argument("--since", dest="since", default=None, help="YYYY-MM-DD; default today")
    s.add_argument("--name", dest="name", default="Noise_REP_33E3", help="advertised band name")
    s.add_argument("--out", dest="out", default=None, help="output dir; default data/<ts>")
    s.add_argument("--no-files", dest="no_files", action="store_true")
    s.add_argument("-v", "--verbose", dest="verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "decode":
        return _decode(args.pklg, args.json_path)
    if args.cmd == "sync":
        return _sync(args.name, args.since, args.out, args.no_files, args.verbose)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

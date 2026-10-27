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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noiserep")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode a .pklg capture offline")
    d.add_argument("pklg")
    d.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "decode":
        return _decode(args.pklg, args.json_path)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

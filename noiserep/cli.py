"""noiserep command-line interface. Phase 0: offline `decode` of a capture."""
from __future__ import annotations

import argparse
import json
import os
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
    # A freshly-unpaired band's clock reads ~2002, so "today" would filter out
    # everything. Ask for the whole device timeline; the band ignores dates it
    # has no data for.
    return datetime.datetime(2000, 1, 1)


async def _run_sync_async(name, since, want_files, debug):
    from .ble.connection import connect
    from .sync import run_sync
    async with connect(name, debug=debug) as band:
        return await run_sync(band, since, want_files=want_files)


def _parse_until(value):
    import datetime
    d = datetime.datetime.strptime(value, "%Y-%m-%d")
    return d.replace(hour=23, minute=59, second=59, microsecond=0)


def _resolve_name(name):
    """Resolve the band name: --name, then local config, then auto-detect."""
    if name:
        return name
    try:
        from .config import ConfigError, load
        return load().band_name
    except (ConfigError, OSError):
        return None


def _live(name, db, interval, history_every):
    import asyncio
    import datetime
    from .live import keep_linked
    from .store import Store, default_db_path

    def show(s):
        print(f"{s.at:%H:%M:%S}  HR {s.heart_rate or '-'}  SpO2 {s.spo2 or '-'}  "
              f"stress {s.stress or '-'}  steps {s.steps}  kcal {s.calories}  "
              f"battery {s.battery}%{' charging' if s.charging else ''}", flush=True)

    def status(text):
        print(f"{datetime.datetime.now():%H:%M:%S}  [{text}]", flush=True)

    with Store(db or default_db_path()) as store:
        try:
            asyncio.run(keep_linked(store, _resolve_name(name), interval,
                                    history_every, on_sample=show, on_status=status))
        except KeyboardInterrupt:
            pass
    return 0


def _sync(name, since_str, out, no_files, verbose, db):
    import asyncio
    import datetime
    from .sync import write_result
    from .store import Store, default_db_path
    from .errors import BandNotFound, TransportError
    try:
        since = _parse_since(since_str)
    except ValueError:
        print(f"invalid --since '{since_str}': expected YYYY-MM-DD", file=sys.stderr)
        return 2
    name = _resolve_name(name)
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
    db_path = db or default_db_path()
    with Store(db_path) as store:
        counts = store.ingest(result, note=f"sync {out_dir}")
    if result.device is not None:
        d = result.device
        print(f"device: fw {d.firmware}  model {d.model}  mac {d.mac}  serial {d.serial}")
    if result.index is not None:
        print(f"index: {len(result.index.records)} records")
    with_samples = [s for s in result.series if s.samples]
    print(f"series: {len(result.series)} ({len(with_samples)} with samples)")
    print(f"files: {len(result.files)}")
    print(f"stored {counts['samples']} samples, {counts['files']} files, "
          f"{counts.get('rri', 0)} RRI, {counts.get('sleep', 0)} sleep -> {db_path}")
    print(f"wrote {out_dir}")
    return 0


async def _buzz_async(name, pattern, duration, strength, envelope, debug):
    import asyncio
    from .ble.connection import connect
    from .protocol import vibration

    if pattern in vibration.FIND_PATTERNS:
        if pattern in vibration.STOP_PATTERNS:
            async with connect(name, debug=debug) as band:
                await band.send(vibration.find_band_stop(envelope=envelope))
            return 0
        seconds = 3.0 if duration is None else min(
            float(duration), vibration.MAX_DURATION_MS / 1000)
        if seconds <= 0:
            raise ValueError("duration must be positive")
        async with connect(name, debug=debug) as band:
            await band.send(vibration.find_band_start(envelope=envelope))
            try:
                await asyncio.sleep(seconds)
            finally:
                await asyncio.shield(
                    band.send(vibration.find_band_stop(envelope=envelope)))
        return 0

    ms = None if duration is None else int(duration)
    async with connect(name, debug=debug) as band:
        await band.send(vibration.vibration(
            pattern=pattern, duration_ms=ms, strength=strength,
            envelope=envelope))
    return 0


def _buzz(name, pattern, duration, strength, envelope, verbose):
    """EXPERIMENTAL write path: vibrate the band. Never called by sync/GUI."""
    import asyncio
    from .errors import BandNotFound, TransportError
    name = _resolve_name(name)
    try:
        return asyncio.run(
            _buzz_async(name, pattern, duration, strength, envelope, verbose))
    except (BandNotFound, TransportError) as exc:
        print(f"buzz failed: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"buzz refused: {exc}", file=sys.stderr)
        return 2


def _export(db, metric, since_str, until_str, fmt, out):
    from .store import Store, default_db_path
    from .export import export_csv, export_json
    try:
        start = _parse_since(since_str) if since_str else None
        end = _parse_until(until_str) if until_str else None
    except ValueError:
        print("invalid --since/--until: expected YYYY-MM-DD", file=sys.stderr)
        return 2
    path = db or default_db_path()
    if not os.path.exists(path):
        print(f"no database at {path}; run `noiserep sync` first", file=sys.stderr)
        return 5
    writer = export_csv if fmt == "csv" else export_json
    with Store(path) as store:
        n = writer(store, out, metric, start, end)
    print(f"wrote {n} rows to {out}")
    return 0


def _sleep(db, json_path):
    import json
    from . import analytics
    from .store import Store, default_db_path
    path = db or default_db_path()
    if not os.path.exists(path):
        print(f"no database at {path}; run `noiserep sync` first", file=sys.stderr)
        return 5
    with Store(path) as store:
        sessions = store.sleep_sessions()
        if not sessions:
            print("no rest window stored yet; sync the band first",
                  file=sys.stderr)
            return 0
        session = sessions[-1]
        summary = store.rest_summary(session["bedtime"], session["wake"])
        doc = {
            "session": {
                "bedtime": session["bedtime"].isoformat(),
                "wake": session["wake"].isoformat(),
                "total_min": session["total_min"],
                "source": session["source"],
            },
            "summary": None if summary is None else {
                "avg_hr": summary.avg_hr, "min_hr": summary.min_hr,
                "max_hr": summary.max_hr, "rmssd_ms": summary.rmssd_ms,
                "sdnn_ms": summary.sdnn_ms, "pnn50": summary.pnn50,
                "coverage_pct": summary.coverage_pct, "beats": summary.beats,
            },
            "insights": [] if summary is None else analytics.insights(summary),
        }
        if json_path:
            with open(json_path, "w") as fh:
                json.dump(doc, fh, indent=1)
            print(f"wrote {json_path}")
            return 0
        s = doc["session"]
        print(f"last night: {s['total_min'] // 60}h {s['total_min'] % 60:02d}m "
              f"{s['bedtime'][11:16]}–{s['wake'][11:16]}  ({s['source']})")
        if summary is not None:
            print(f"heart rate: {summary.avg_hr:.0f} bpm "
                  f"(5-95% {summary.min_hr}-{summary.max_hr})")
            print(f"HRV: RMSSD {summary.rmssd_ms:.0f} ms, "
                  f"SDNN {summary.sdnn_ms:.0f} ms, pNN50 {summary.pnn50:.0f}%")
            print(f"coverage: {summary.coverage_pct:.0f}% "
                  f"({summary.beats:,} beats)")
            for line in doc["insights"]:
                print(f"- {line}")
    return 0


def _workouts(db):
    from .store import Store, default_db_path
    path = db or default_db_path()
    if not os.path.exists(path):
        print(f"no database at {path}; run `noiserep sync` first", file=sys.stderr)
        return 5
    with Store(path) as store:
        workouts = store.workouts()
    if not workouts:
        print("no workout files received. This band's sync has only pushed "
              "RRI files; record a workout on the band and sync again.")
        return 0
    for workout in workouts:
        when = workout.get("time")
        stamp = f"{when:%Y-%m-%d %H:%M}" if when else "unknown time"
        print(f"workout: {workout.get('id')}  {stamp}  "
              f"{workout.get('size', 0):,} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import __version__
    parser = argparse.ArgumentParser(prog="noiserep")
    parser.add_argument("--version", action="version",
                        version=f"noiserep {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode", help="decode a .pklg capture offline")
    d.add_argument("pklg")
    d.add_argument("--json", dest="json_path", default=None)
    s = sub.add_parser("sync", help="fetch history from a live band over BLE")
    s.add_argument("--since", dest="since", default=None,
                   help="YYYY-MM-DD; default whole device history")
    s.add_argument("--name", dest="name", default=None,
                   help="advertised band name; default config or auto-detect")
    s.add_argument("--out", dest="out", default=None, help="output dir; default data/<ts>")
    s.add_argument("--no-files", dest="no_files", action="store_true")
    s.add_argument("--db", dest="db", default=None,
                   help="SQLite DB path; default per-OS user-data dir")
    s.add_argument("-v", "--verbose", dest="verbose", action="store_true")
    e = sub.add_parser("export", help="export stored samples to CSV/JSON")
    e.add_argument("--db", dest="db", default=None, help="SQLite DB path; default per-OS")
    e.add_argument("--metric", dest="metric", default=None, help="metric name; default all")
    e.add_argument("--since", dest="since", default=None, help="YYYY-MM-DD")
    e.add_argument("--until", dest="until", default=None, help="YYYY-MM-DD")
    e.add_argument("--format", dest="fmt", choices=["csv", "json"], default="csv")
    e.add_argument("--out", dest="out", required=True, help="output file path")
    g = sub.add_parser("gui", help="launch the desktop app")
    g.add_argument("--reload", action="store_true", help="restart automatically after source edits (development)")
    lv = sub.add_parser("live", help="stay connected: live values + periodic history")
    lv.add_argument("--name", dest="name", default=None)
    lv.add_argument("--db", dest="db", default=None)
    lv.add_argument("--interval", type=float, default=10.0, help="live poll seconds")
    lv.add_argument("--history-every", type=float, default=1800.0,
                    help="seconds between full history syncs")
    sl = sub.add_parser("sleep", help="summarise last night's rest from stored RRI")
    sl.add_argument("--db", dest="db", default=None,
                    help="SQLite DB path; default per-OS user-data dir")
    sl.add_argument("--json", dest="json_path", default=None,
                    help="write the summary as JSON")
    wo = sub.add_parser("workouts", help="list stored workout files")
    wo.add_argument("--db", dest="db", default=None,
                    help="SQLite DB path; default per-OS user-data dir")
    m = sub.add_parser("menubar", help="live heart rate in the macOS menu bar")
    m.add_argument("--name", dest="name", default=None,
                   help="advertised band name; default auto-detect Noise_REP_*")
    m.add_argument("--db", dest="db", default=None,
                   help="SQLite DB path; default per-OS user-data dir")
    m.add_argument("--parent-control", action="store_true", help=argparse.SUPPRESS)
    m.add_argument("--poll", dest="poll", type=float, default=30.0,
                   help="seconds between heart-rate polls (default 30)")
    m.add_argument("--lock-on-away", dest="lock_on_away", action="store_true",
                   help="lock the Mac if the band disconnects and does not "
                        "come back within --away-seconds")
    m.add_argument("--away-seconds", dest="away_seconds", type=float,
                   default=15.0,
                   help="grace period before the away-lock (default 15)")
    m.add_argument("--retry-seconds", dest="retry_seconds", type=float,
                   default=60.0,
                   help="retry a failed connection after this many seconds; "
                        "0 disables (default 60)")
    b = sub.add_parser(
        "buzz", help="EXPERIMENTAL: vibrate the band (write; requires --yes)")
    from .protocol import vibration
    b.add_argument("--pattern", dest="pattern", default="short",
                   choices=sorted(set(vibration.PRESETS) | vibration.FIND_PATTERNS))
    b.add_argument("--duration", dest="duration", type=float, default=None,
                   help="find-band seconds (max 10); single-pulse patterns: ms")
    b.add_argument("--strength", dest="strength", type=int, default=100,
                   help="1-100")
    b.add_argument("--envelope", dest="envelope", default="vendor",
                   choices=["vendor", "flat"],
                   help="command envelope; 'vendor' is the documented default, "
                        "'flat' is an experimental fallback")
    b.add_argument("--name", dest="name", default=None,
                   help="advertised band name; default config or auto-detect")
    b.add_argument("--yes", action="store_true",
                   help="required: confirm this write to the band")
    b.add_argument("-v", "--verbose", dest="verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "decode":
        return _decode(args.pklg, args.json_path)
    if args.cmd == "sync":
        return _sync(args.name, args.since, args.out, args.no_files, args.verbose, args.db)
    if args.cmd == "export":
        return _export(args.db, args.metric, args.since, args.until, args.fmt, args.out)
    if args.cmd == "gui":
        from .gui.app import main as gui_main
        return gui_main(["--reload"] if args.reload else [])
    if args.cmd == "live":
        return _live(args.name, args.db, args.interval, args.history_every)
    if args.cmd == "sleep":
        return _sleep(args.db, args.json_path)
    if args.cmd == "workouts":
        return _workouts(args.db)
    if args.cmd == "menubar":
        if args.poll <= 0:
            print("invalid --poll: must be greater than 0", file=sys.stderr)
            return 2
        if args.away_seconds < 0:
            print("invalid --away-seconds: must not be negative", file=sys.stderr)
            return 2
        if args.retry_seconds < 0:
            print("invalid --retry-seconds: must not be negative",
                  file=sys.stderr)
            return 2
        from .gui.menubar import main as menubar_main
        return menubar_main(band_name=args.name, db_path=args.db,
                            poll_seconds=args.poll,
                            lock_on_away=args.lock_on_away,
                            away_seconds=args.away_seconds,
                            retry_seconds=args.retry_seconds,
                            parent_control=args.parent_control)
    if args.cmd == "buzz":
        if not args.yes:
            print("refusing to vibrate the band without --yes "
                  "(experimental write)", file=sys.stderr)
            return 2
        return _buzz(args.name, args.pattern, args.duration, args.strength,
                     args.envelope, args.verbose)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

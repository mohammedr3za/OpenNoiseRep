"""SQLite store for noiserep: accumulates decoded sync history without
duplicates, and serves the CLI/GUI read queries and the exporters.

Schema (design spec §6):
  device(mac PK, fw, model, serial, last_seen)
  samples(metric, ts, value, PK(metric, ts))      -- per-sample rows
  activity_files(id PK, kind, ts, raw BLOB, decoded_json)
  sync_log(ts, n_records, note)
  sleep_sessions(id PK, bedtime, wake, total_min, deep_min, light_min,
                 rem_min, awake_min, quality, source, UNIQUE(bedtime, wake))
  sleep_stages(id PK, session_id, ts, stage, UNIQUE(session_id, ts))
  rri_samples(ts PK, rr_ms)

The sleep/RRI tables were added after the first release.  They are created
with ``CREATE TABLE IF NOT EXISTS`` and ``_migrate`` fills in any column a
partially-created older database is missing, so opening an existing database
upgrades it in place without a separate migration step.

Re-ingesting a sync upserts each (metric, ts) rather than duplicating it, so
syncing overlapping days converges instead of growing. Only non-zero samples
are stored: in this band's dense daily arrays 0 means "no reading" (a 24h HR
log is mostly zeros), so keeping them would bloat the DB and clutter charts.
The GUI and exporters therefore see gaps, not runs of zeros.

Timestamps are stored as epoch seconds of the band's wall clock interpreted as
UTC, so a given reading maps to the same integer regardless of the machine's
local timezone; `_from_ts` recovers the original naive datetime.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sqlite3
import sys
import time

from ..protocol.files import assemble_activity_files
from ..protocol.metrics import metric_name

_UTC = _dt.timezone.utc

#: Metric 0's decoded channels are one value per hour.
_CHANNEL_INTERVAL_S = 3600


def _to_ts(d: _dt.datetime) -> int:
    return int(d.replace(tzinfo=_UTC).timestamp())


def _from_ts(ts: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, _UTC).replace(tzinfo=None)


def default_db_path() -> str:
    """Per-OS user-data location for the accumulated database."""
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "noiserep"
    elif os.name == "nt":
        base = pathlib.Path(os.environ.get("APPDATA", home)) / "noiserep"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (pathlib.Path(xdg) if xdg else home / ".local" / "share") / "noiserep"
    return str(base / "noiserep.db")


def _file_kind(name: str) -> str:
    low = name.lower()
    if "rri" in low:
        return "RRI"
    if "sleep" in low or "slp" in low:
        return "sleep"
    if any(word in low for word in (
            "workout", "sport", "training", "exercise",
            "run", "walk", "cycle", "swim")):
        return "workout"
    if "hrv" in low:
        return "HRV"
    if "luna" in low or ".log" in low:
        return "log"
    return "file"


def _range_conds(start, end) -> tuple[str, list]:
    conds, args = [], []
    if start is not None:
        conds.append("ts>=?")
        args.append(_to_ts(start))
    if end is not None:
        conds.append("ts<=?")
        args.append(_to_ts(end))
    return (" AND ".join(conds), args)


def _attr(obj, name, default=None):
    """Read ``name`` from a dict or an object (decoders return dataclasses)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _ts_value(value) -> int | None:
    """Coerce a datetime (band wall clock) or epoch number to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return _to_ts(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return None


def _as_sequence(value) -> list:
    """Accept a single decoded object or a list of them."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]



_SCHEMA = """
CREATE TABLE IF NOT EXISTS device(
    mac TEXT PRIMARY KEY, fw TEXT, model TEXT, serial TEXT, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS samples(
    metric TEXT, ts INTEGER, value REAL, PRIMARY KEY(metric, ts));
CREATE TABLE IF NOT EXISTS activity_files(
    id TEXT PRIMARY KEY, kind TEXT, ts INTEGER, raw BLOB, decoded_json TEXT);
CREATE TABLE IF NOT EXISTS sync_log(
    ts INTEGER, n_records INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS sleep_sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bedtime INTEGER,
    wake INTEGER,
    total_min INTEGER,
    deep_min INTEGER,
    light_min INTEGER,
    rem_min INTEGER,
    awake_min INTEGER,
    quality INTEGER,
    source TEXT,
    UNIQUE(bedtime, wake));
CREATE TABLE IF NOT EXISTS sleep_stages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ts INTEGER,
    stage INTEGER,
    UNIQUE(session_id, ts));
CREATE TABLE IF NOT EXISTS rri_samples(
    ts INTEGER PRIMARY KEY,
    rr_ms INTEGER);
CREATE TABLE IF NOT EXISTS live(
    id INTEGER PRIMARY KEY CHECK(id=1), ts INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS workouts(
    start INTEGER, kind TEXT, duration_s INTEGER, level TEXT,
    steps INTEGER, kcal INTEGER, source TEXT,
    PRIMARY KEY(start, kind));
CREATE INDEX IF NOT EXISTS idx_samples_metric_ts ON samples(metric, ts);
CREATE INDEX IF NOT EXISTS idx_sleep_sessions_bedtime ON sleep_sessions(bedtime);
CREATE INDEX IF NOT EXISTS idx_sleep_stages_session ON sleep_stages(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_rri_samples_ts ON rri_samples(ts);
"""

# Columns added after a table's first release.  ``_migrate`` adds them to an
# existing database when ``CREATE TABLE IF NOT EXISTS`` leaves an older shape
# in place.  All are nullable, so the ALTER is safe.
_MIGRATIONS = {
    "activity_files": (("decoded_json", "TEXT"),),
    "sleep_sessions": (
        ("total_min", "INTEGER"), ("deep_min", "INTEGER"),
        ("light_min", "INTEGER"), ("rem_min", "INTEGER"),
        ("awake_min", "INTEGER"), ("quality", "INTEGER"), ("source", "TEXT"),
        ("latency_min", "INTEGER"), ("efficiency", "INTEGER"),
        ("time_in_bed_min", "INTEGER"),
    ),
    "sleep_stages": (("session_id", "INTEGER"), ("ts", "INTEGER"),
                     ("stage", "INTEGER")),
    "rri_samples": (("rr_ms", "INTEGER"),),
}


def _derive_rest_sessions(conn: sqlite3.Connection) -> int:
    """Derive one RRI-based rest session, replacing the previous derived one.

    This is not a staged sleep file: it is the span of the longest RRI window,
    with only ``total_min`` known.  ``source='rri'`` keeps that distinction
    visible in the UI, and a real sleep-file session is never touched.
    """
    from .. import analytics

    rows = conn.execute("SELECT ts, rr_ms FROM rri_samples ORDER BY ts").fetchall()
    if len(rows) < 10:
        return 0
    summary = analytics.overnight_rest(
        ((_from_ts(ts), rr) for ts, rr in rows))
    if summary is None:
        return 0
    conn.execute("DELETE FROM sleep_sessions WHERE source='rri'")
    if conn.execute(
            "SELECT 1 FROM sleep_sessions WHERE wake>=? AND bedtime<=?",
            (_to_ts(summary.start), _to_ts(summary.end))).fetchone():
        return 0                 # the band's own session covers this night
    conn.execute(
        "INSERT INTO sleep_sessions(bedtime, wake, total_min, source) "
        "VALUES(?,?,?,?) ON CONFLICT(bedtime, wake) DO UPDATE SET "
        "total_min=excluded.total_min, source=excluded.source",
        (_to_ts(summary.start), _to_ts(summary.end), summary.duration_min, "rri"),
    )
    return 1


def _migrate_metric_names(conn: sqlite3.Connection) -> None:
    """Rename samples stored under the old neutral ``metric_N`` keys.

    Metric identities were added after the first store release, so a database
    synced before that has rows under ``metric_21`` while a new sync writes
    ``heart_rate_variability``. Without this, one channel would appear twice.
    """
    from ..protocol.metrics import METRICS

    for metric_id, info in METRICS.items():
        old = f"metric_{metric_id}"
        new = info.name
        if old == new:
            continue
        conn.execute(
            "DELETE FROM samples WHERE metric=? AND ts IN "
            "(SELECT ts FROM samples WHERE metric=?)", (old, new))
        conn.execute("UPDATE samples SET metric=? WHERE metric=?", (new, old))


#: Before v1 only these series had correct timestamps: heart rate's cadence
#: field is seconds and metric 0's channels used a fixed hour.  Every other
#: metric was spaced by a cadence field that is really minutes, so its rows
#: sit at wrong times (re-sync or re-ingest a dump to restore them).
_V1_KEEP = ("heart_rate", "metric_2", "steps", "distance_m", "calories",
            "active_minutes")


def _fix_v1(conn: sqlite3.Connection) -> None:
    """One-time cleanup of pre-schema rows; ``user_version`` makes it run once.

    Drops the mis-timed series and renames daily ``f15`` rows, stored as
    ``active_minutes``, to what they are: ``active_calories``.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 1:
        return
    marks = ",".join("?" * len(_V1_KEEP))
    conn.execute(f"DELETE FROM samples WHERE metric NOT IN ({marks})", _V1_KEEP)
    conn.execute(
        "UPDATE samples SET metric='active_calories' WHERE metric='active_minutes'")
    conn.execute("PRAGMA user_version=1")


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema, in place."""
    cur = conn.cursor()
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in _MIGRATIONS.items():
        if table not in tables:
            continue
        existing = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        for column, decl in columns:
            if column not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")



class Store:
    """A SQLite-backed history. Pass ``":memory:"`` for an ephemeral store."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        _fix_v1(self._conn)
        _migrate_metric_names(self._conn)
        _derive_rest_sessions(self._conn)
        self._conn.commit()

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- write -----------------------------------------------------------
    def ingest(self, result, note: str = "") -> dict:
        """Upsert a SyncResult's device / samples / files / sleep / RRI.

        ``sleep`` and ``rri`` are optional attributes on ``result`` (a
        ``SleepSession`` or list of them, and a ``RriFile`` / ``RriSample``
        list respectively); when absent the call behaves exactly as before.

        A metric series with decoded ``channels`` (currently metric 0's hourly
        steps / distance / calories / active minutes) stores one sample per
        non-zero hour under each channel name, instead of its packed raw byte
        array.  This is what makes ``Store.samples("steps")`` work; re-ingest
        stays idempotent through the usual ``(metric, ts)`` upsert.

        Returns counts of rows written this call:
        ``{"samples", "files", "sleep", "sleep_stages", "rri"}``.
        """
        now = int(time.time())
        cur = self._conn.cursor()
        counts = {"samples": 0, "files": 0, "sleep": 0, "sleep_stages": 0,
                  "rri": 0, "workouts": 0}

        dev = getattr(result, "device", None)
        if dev is not None and getattr(dev, "mac", ""):
            cur.execute(
                "INSERT INTO device(mac, fw, model, serial, last_seen) "
                "VALUES(?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET "
                "fw=excluded.fw, model=excluded.model, serial=excluded.serial, "
                "last_seen=excluded.last_seen",
                (dev.mac, dev.firmware, dev.model, dev.serial, now),
            )

        for s in getattr(result, "series", []):
            # Metric 0's `samples` are its packed byte array, not a time
            # series.  When the decoder supplied hourly channels, persist those
            # instead so the raw bytes never masquerade as samples.
            channels = getattr(s, "channels", None) or {}
            if channels:
                rows = []
                for name, values in channels.items():
                    if not isinstance(values, (list, tuple)):
                        continue
                    for i, v in enumerate(values):
                        if not v:
                            continue          # zero means "no reading"
                        ts = _to_ts(s.date + _dt.timedelta(
                            seconds=i * _CHANNEL_INTERVAL_S))
                        rows.append((name, ts, float(v)))
                if rows:
                    cur.executemany(
                        "INSERT INTO samples(metric, ts, value) VALUES(?,?,?) "
                        "ON CONFLICT(metric, ts) DO UPDATE SET value=excluded.value",
                        rows,
                    )
                    counts["samples"] += len(rows)
                if s.metric_id == 0:
                    continue          # its samples are packed bytes, not a series
            if not s.samples or not s.interval_s:
                continue
            metric = metric_name(s.metric_id)
            rows = []
            for i, v in enumerate(s.samples):
                if not v:
                    continue
                ts = _to_ts(s.date + _dt.timedelta(seconds=i * s.interval_s))
                rows.append((metric, ts, float(v)))
            if rows:
                cur.executemany(
                    "INSERT INTO samples(metric, ts, value) VALUES(?,?,?) "
                    "ON CONFLICT(metric, ts) DO UPDATE SET value=excluded.value",
                    rows,
                )
                counts["samples"] += len(rows)

        # Store one row per logical file, not one per 7200-byte chunk.  Keeping
        # the last chunk under the shared name loses the rest of the file.
        logical_files = assemble_activity_files(
            getattr(result, "files", []) or [], include_incomplete=True)
        for f in logical_files:
            cur.execute(
                "INSERT INTO activity_files(id, kind, ts, raw, decoded_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "kind=excluded.kind, ts=excluded.ts, raw=excluded.raw",
                (f.name, _file_kind(f.name), now, f.data, None),
            )
            counts["files"] += 1

        for session in _as_sequence(getattr(result, "sleep", None)):
            written = self._write_sleep(cur, session)
            if written is not None:
                counts["sleep"] += 1
                counts["sleep_stages"] += written[1]

        for w in getattr(result, "workouts", None) or []:
            cur.execute(
                "INSERT INTO workouts(start, kind, duration_s, level, steps, "
                "kcal, source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(start, kind) "
                "DO UPDATE SET duration_s=excluded.duration_s, "
                "level=excluded.level, steps=excluded.steps, kcal=excluded.kcal",
                (_to_ts(w.start), w.kind, w.duration_s, w.level, w.steps,
                 w.kcal, w.source))
            counts["workouts"] += 1

        for samples in _as_sequence(getattr(result, "rri", None)):
            counts["rri"] += self._write_rri(cur, samples)
        # RRI is the only overnight signal this band sends; derive a rest
        # session from it unless a real sleep file already supplied one.
        if not _as_sequence(getattr(result, "sleep", None)):
            counts["sleep"] += _derive_rest_sessions(self._conn)

        cur.execute(
            "INSERT INTO sync_log(ts, n_records, note) VALUES(?,?,?)",
            (now, sum(counts.values()), note),
        )
        self._conn.commit()
        return counts

    # -- sleep / RRI writes ----------------------------------------------
    def _write_sleep(self, cur, session, source: str | None = None):
        """Upsert one session and its stages; return (session_id, n_stages)."""
        bedtime = _ts_value(_attr(session, "bedtime"))
        wake = _ts_value(_attr(session, "wake"))
        if bedtime is None or wake is None:
            return None
        src = source or _attr(session, "source") or "file"
        cur.execute(
            "INSERT INTO sleep_sessions(bedtime, wake, total_min, deep_min, "
            "light_min, rem_min, awake_min, quality, source, latency_min, "
            "efficiency, time_in_bed_min) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bedtime, wake) DO UPDATE SET "
            "total_min=excluded.total_min, deep_min=excluded.deep_min, "
            "light_min=excluded.light_min, rem_min=excluded.rem_min, "
            "awake_min=excluded.awake_min, quality=excluded.quality, "
            "source=excluded.source, latency_min=excluded.latency_min, "
            "efficiency=excluded.efficiency, "
            "time_in_bed_min=excluded.time_in_bed_min",
            (
                bedtime, wake,
                _attr(session, "total_min"), _attr(session, "deep_min"),
                _attr(session, "light_min"), _attr(session, "rem_min"),
                _attr(session, "awake_min"), _attr(session, "quality"), src,
                _attr(session, "latency_min"), _attr(session, "efficiency"),
                _attr(session, "time_in_bed_min"),
            ),
        )
        session_id = cur.execute(
            "SELECT id FROM sleep_sessions WHERE bedtime=? AND wake=?",
            (bedtime, wake),
        ).fetchone()[0]

        stages_written = 0
        for stage in _attr(session, "stages", None) or []:
            ts = _ts_value(_attr(stage, "ts"))
            value = _attr(stage, "stage")
            if ts is None or value is None:
                continue
            cur.execute(
                "INSERT INTO sleep_stages(session_id, ts, stage) VALUES(?,?,?) "
                "ON CONFLICT(session_id, ts) DO UPDATE SET stage=excluded.stage",
                (session_id, ts, int(value)),
            )
            stages_written += 1
        return session_id, stages_written

    def _write_rri(self, cur, samples) -> int:
        """Upsert RR samples.  Accepts an RriFile, or an iterable of samples
        (RriSample objects, dicts, or ``(ts, rr_ms)`` pairs)."""
        items = _attr(samples, "samples", None)
        if items is None:
            if (isinstance(samples, (tuple, list)) and len(samples) == 2
                    and _ts_value(samples[0]) is not None
                    and isinstance(samples[1], (int, float))):
                items = [samples]        # a single (ts, rr_ms) pair
            elif isinstance(samples, (tuple, list)):
                items = samples
            else:
                items = [samples]        # a single RriSample-like object
        written = 0
        for sample in items or []:
            ts = _ts_value(_attr(sample, "ts"))
            rr = _attr(sample, "rr_ms")
            if ts is None and isinstance(sample, (tuple, list)) and len(sample) == 2:
                ts, rr = _ts_value(sample[0]), sample[1]
            if ts is None or rr is None or rr <= 0:
                continue             # zero means "no reading", as elsewhere
            cur.execute(
                "INSERT INTO rri_samples(ts, rr_ms) VALUES(?,?) "
                "ON CONFLICT(ts) DO UPDATE SET rr_ms=excluded.rr_ms",
                (ts, int(rr)),
            )
            written += 1
        return written

    def write_live(self, sample) -> None:
        """Keep the latest live report; heart rate also joins ``live_hr``."""
        data = {k: v for k, v in vars(sample).items() if k != "at"}
        ts = _to_ts(sample.at)
        self._conn.execute(
            "INSERT INTO live(id, ts, json) VALUES(1,?,?) ON CONFLICT(id) DO "
            "UPDATE SET ts=excluded.ts, json=excluded.json", (ts, json.dumps(data)))
        if sample.heart_rate:
            self._conn.execute(
                "INSERT INTO samples(metric, ts, value) VALUES('live_hr',?,?) "
                "ON CONFLICT(metric, ts) DO UPDATE SET value=excluded.value",
                (ts, float(sample.heart_rate)))
        self._conn.commit()

    def live(self) -> dict | None:
        """The latest live report with its time, or ``None``."""
        row = self._conn.execute("SELECT ts, json FROM live WHERE id=1").fetchone()
        if row is None:
            return None
        return {"at": _from_ts(row["ts"]), **json.loads(row["json"])}

    def ingest_sleep(self, session, source: str | None = None) -> tuple | None:
        """Upsert a decoded sleep session; return ``(session_id, n_stages)``."""
        cur = self._conn.cursor()
        written = self._write_sleep(cur, session, source)
        self._conn.commit()
        return written

    def ingest_rri(self, samples) -> int:
        """Upsert decoded RR samples; return the number written."""
        cur = self._conn.cursor()
        written = self._write_rri(cur, samples)
        self._conn.commit()
        return written


    # -- read ------------------------------------------------------------
    def metrics(self, start=None, end=None) -> list[str]:
        q = "SELECT DISTINCT metric FROM samples"
        conds, args = _range_conds(start, end)
        if conds:
            q += " WHERE " + conds
        q += " ORDER BY metric"
        return [r[0] for r in self._conn.execute(q, args)]

    def samples(self, metric: str, start=None, end=None) -> list[tuple]:
        q = "SELECT ts, value FROM samples WHERE metric=?"
        args: list = [metric]
        conds, extra = _range_conds(start, end)
        if conds:
            q += " AND " + conds
            args += extra
        q += " ORDER BY ts"
        return [(_from_ts(ts), value) for ts, value in self._conn.execute(q, args)]

    def sample_bounds(self, metric: str | None = None) -> tuple:
        q = "SELECT MIN(ts), MAX(ts) FROM samples"
        args: list = []
        if metric is not None:
            q += " WHERE metric=?"
            args.append(metric)
        lo, hi = self._conn.execute(q, args).fetchone()
        if lo is None:
            return (None, None)
        return (_from_ts(lo), _from_ts(hi))

    def device(self) -> dict | None:
        row = self._conn.execute(
            "SELECT mac, fw, model, serial, last_seen FROM device "
            "ORDER BY last_seen DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"mac": row["mac"], "fw": row["fw"], "model": row["model"],
                "serial": row["serial"], "last_seen": _from_ts(row["last_seen"])}

    def activity_files(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, ts, length(raw) AS size FROM activity_files "
            "ORDER BY ts DESC, id"
        ).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "time": _from_ts(r["ts"]),
                 "size": r["size"]} for r in rows]

    def sleep_sessions(self, start=None, end=None) -> list[dict]:
        """Sessions overlapping ``[start, end]`` (a session overlaps when its
        wake is at/after ``start`` and its bedtime is at/before ``end``)."""
        q = ("SELECT id, bedtime, wake, total_min, deep_min, light_min, "
             "rem_min, awake_min, quality, source, latency_min, efficiency, "
             "time_in_bed_min FROM sleep_sessions")
        conds, args = [], []
        if start is not None:
            conds.append("wake>=?")
            args.append(_to_ts(start))
        if end is not None:
            conds.append("bedtime<=?")
            args.append(_to_ts(end))
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY bedtime, wake"
        return [
            {
                "id": r["id"],
                "bedtime": _from_ts(r["bedtime"]),
                "wake": _from_ts(r["wake"]),
                "total_min": r["total_min"],
                "deep_min": r["deep_min"],
                "light_min": r["light_min"],
                "rem_min": r["rem_min"],
                "awake_min": r["awake_min"],
                "quality": r["quality"],
                "source": r["source"],
                "latency_min": r["latency_min"],
                "efficiency": r["efficiency"],
                "time_in_bed_min": r["time_in_bed_min"],
            }
            for r in self._conn.execute(q, args)
        ]

    def sleep_stages(self, session_id: int) -> list[dict]:
        """Stage changes for one session, ordered by timestamp."""
        rows = self._conn.execute(
            "SELECT id, session_id, ts, stage FROM sleep_stages "
            "WHERE session_id=? ORDER BY ts",
            (session_id,),
        )
        return [
            {"id": r["id"], "session_id": r["session_id"],
             "ts": _from_ts(r["ts"]), "stage": r["stage"]}
            for r in rows
        ]

    def rri(self, start=None, end=None) -> list[tuple]:
        """RR intervals as ``(datetime, rr_ms)``, ordered by time."""
        q = "SELECT ts, rr_ms FROM rri_samples"
        conds, args = _range_conds(start, end)
        if conds:
            q += " WHERE " + conds
        q += " ORDER BY ts"
        return [(_from_ts(ts), rr_ms)
                for ts, rr_ms in self._conn.execute(q, args)]

    def rest_summary(self, start=None, end=None):
        """Longest RRI window in the range, or ``None``."""
        from .. import analytics
        return analytics.overnight_rest(self.rri(start, end))

    def workouts(self) -> list[dict]:
        """Band-detected sessions (newest first), then workout files."""
        rows = self._conn.execute(
            "SELECT start, kind, duration_s, level, steps, kcal, source "
            "FROM workouts ORDER BY start DESC").fetchall()
        sessions = [{"id": f"{r['kind']} {_from_ts(r['start']):%Y-%m-%d %H:%M}",
                     "kind": r["kind"], "time": _from_ts(r["start"]),
                     "duration_s": r["duration_s"], "level": r["level"],
                     "steps": r["steps"], "kcal": r["kcal"],
                     "source": r["source"], "size": None} for r in rows]
        files = [f for f in self.activity_files() if f["kind"] == "workout"]
        return sessions + files

    def last_sync(self) -> dict | None:
        row = self._conn.execute(
            "SELECT ts, n_records, note FROM sync_log ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"time": _from_ts(row["ts"]), "n_records": row["n_records"],
                "note": row["note"]}

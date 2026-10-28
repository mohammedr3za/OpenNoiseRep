"""Store tests: sample expansion, zero-skipping, idempotent re-ingest, and
the device / file / query surface, plus sleep/RRI storage. All in-memory, no
hardware; sleep/RRI fixtures are synthetic."""
import datetime
import sqlite3

from noiserep.sync import SyncResult
from noiserep.protocol.decode import DeviceInfo, MetricSeries, DeviceFile
from noiserep.protocol.files import RriFile, RriSample, SleepSession, SleepStage
from noiserep.store import Store


def _result():
    r = SyncResult()
    r.device = DeviceInfo("1.4.0", "SN-TEST", "aa:bb:cc:dd:ee:ff", "R1")
    # samples at 30s: non-zero at index 1 (01:00:30), 3 (01:01:30), 4 (01:02:00)
    r.series = [MetricSeries(datetime.datetime(2026, 10, 28, 1, 0, 0),
                             2, 30, [0, 93, 0, 95, 104], b"")]
    r.files = [DeviceFile("20261028_RRI_Data.bin.txt", b"\x00\x01\x02", 1)]
    return r


def test_ingest_expands_and_skips_zero():
    s = Store(":memory:")
    counts = s.ingest(_result())
    assert counts["samples"] == 3 and counts["files"] == 1
    rows = s.samples("heart_rate")
    assert [v for _, v in rows] == [93.0, 95.0, 104.0]
    assert rows[0][0] == datetime.datetime(2026, 10, 28, 1, 0, 30)
    assert rows[-1][0] == datetime.datetime(2026, 10, 28, 1, 2, 0)


def test_reingest_is_idempotent():
    s = Store(":memory:")
    s.ingest(_result())
    s.ingest(_result())
    assert len(s.samples("heart_rate")) == 3       # upsert, no duplicates
    assert len(s.activity_files()) == 1            # file upsert by id
    assert s.last_sync() is not None               # but each sync is logged


def test_legacy_metric_keys_are_migrated(tmp_path):
    path = str(tmp_path / "legacy.db")
    s = Store(path)
    s._conn.execute("INSERT INTO samples(metric, ts, value) VALUES('metric_21', 1000, 70)")
    s._conn.commit()
    s.close()

    s = Store(path)
    assert s.metrics() == ["heart_rate_variability"]
    assert [v for _, v in s.samples("heart_rate_variability")] == [70.0]
    s.close()


def test_legacy_metric_migration_keeps_the_newer_row(tmp_path):
    path = str(tmp_path / "legacy-dup.db")
    s = Store(path)
    s._conn.execute("INSERT INTO samples(metric, ts, value) VALUES('metric_21', 1000, 1)")
    s._conn.execute("INSERT INTO samples(metric, ts, value) "
                    "VALUES('heart_rate_variability', 1000, 2)")
    s._conn.commit()
    s.close()

    s = Store(path)
    assert [v for _, v in s.samples("heart_rate_variability")] == [2.0]
    s.close()


def test_device_and_file_metadata():
    s = Store(":memory:")
    s.ingest(_result())
    dev = s.device()
    assert dev["mac"] == "aa:bb:cc:dd:ee:ff" and dev["fw"] == "1.4.0" and dev["model"] == "R1"
    files = s.activity_files()
    assert files[0]["kind"] == "RRI" and files[0]["size"] == 3


def test_metrics_and_bounds():
    s = Store(":memory:")
    s.ingest(_result())
    assert s.metrics() == ["heart_rate"]
    lo, hi = s.sample_bounds()
    assert lo == datetime.datetime(2026, 10, 28, 1, 0, 30)
    assert hi == datetime.datetime(2026, 10, 28, 1, 2, 0)


def test_range_filter():
    s = Store(":memory:")
    s.ingest(_result())
    window = s.samples("heart_rate",
                       start=datetime.datetime(2026, 10, 28, 1, 1, 0),
                       end=datetime.datetime(2026, 10, 28, 1, 2, 0))
    assert [v for _, v in window] == [95.0, 104.0]


# -- sleep / RRI --------------------------------------------------------------

BED = datetime.datetime(2026, 10, 28, 23, 0, 0)
WAKE = datetime.datetime(2026, 10, 29, 7, 0, 0)
START = datetime.datetime(2026, 10, 28, 1, 8, 0)


def _decoded_result():
    r = SyncResult()
    r.sleep = [
        SleepSession(
            BED, WAKE, total_min=480, deep_min=90, light_min=300,
            rem_min=90, awake_min=30, quality=80,
            stages=[
                SleepStage(BED, 2),
                SleepStage(BED + datetime.timedelta(hours=2), 3),
                SleepStage(BED + datetime.timedelta(hours=5), 4),
            ],
            source="sleep_details",
        )
    ]
    r.rri = [
        RriFile(start=START, samples=[
            RriSample(0, 600, START),
            RriSample(1, 0, START + datetime.timedelta(seconds=1)),   # zero: skip
            RriSample(2, 640, START + datetime.timedelta(seconds=2)),
            RriSample(3, 680, None),                                  # no ts: skip
        ])
    ]
    return r


def test_ingest_stores_sleep_and_rri():
    s = Store(":memory:")
    counts = s.ingest(_decoded_result())
    assert counts["sleep"] == 1 and counts["sleep_stages"] == 3
    assert counts["rri"] == 2          # zero and timestamp-less samples skipped

    sessions = s.sleep_sessions()
    assert len(sessions) == 1
    session = sessions[0]
    assert session["bedtime"] == BED and session["wake"] == WAKE
    assert (session["total_min"], session["deep_min"], session["light_min"],
            session["rem_min"], session["awake_min"], session["quality"]) == (
                480, 90, 300, 90, 30, 80)
    assert session["source"] == "sleep_details"

    stages = s.sleep_stages(session["id"])
    assert [(st["ts"], st["stage"]) for st in stages] == [
        (BED, 2),
        (BED + datetime.timedelta(hours=2), 3),
        (BED + datetime.timedelta(hours=5), 4),
    ]

    assert s.rri() == [
        (START, 600), (START + datetime.timedelta(seconds=2), 640)]


def test_sleep_and_rri_reingest_is_idempotent():
    s = Store(":memory:")
    s.ingest(_decoded_result())
    s.ingest(_decoded_result())
    assert len(s.sleep_sessions()) == 1
    assert len(s.sleep_stages(s.sleep_sessions()[0]["id"])) == 3
    assert len(s.rri()) == 2


def test_sleep_and_rri_range_queries():
    s = Store(":memory:")
    s.ingest(_decoded_result())

    # Overlap semantics: a window entirely before bedtime is empty.
    before = s.sleep_sessions(end=datetime.datetime(2026, 10, 28, 22, 0, 0))
    assert before == []
    window = s.sleep_sessions(
        start=datetime.datetime(2026, 10, 29, 0, 0, 0),
        end=datetime.datetime(2026, 10, 29, 8, 0, 0))
    assert len(window) == 1

    assert s.rri(start=START + datetime.timedelta(seconds=1)) == [
        (START + datetime.timedelta(seconds=2), 640)]
    assert s.rri(end=START) == [(START, 600)]


def test_direct_ingest_helpers_and_stage_upsert():
    s = Store(":memory:")
    session = _decoded_result().sleep[0]
    session_id, n = s.ingest_sleep(session)
    assert n == 3 and isinstance(session_id, int)

    # Re-ingesting the same session with an updated stage updates in place.
    changed = SleepSession(
        BED, WAKE, total_min=500, stages=[SleepStage(BED, 5)],
        source="sleep_details")
    same_id, n2 = s.ingest_sleep(changed)
    assert same_id == session_id and n2 == 1
    assert s.sleep_sessions()[0]["total_min"] == 500

    written = s.ingest_rri([RriSample(0, 500, START), (START, 0)])
    assert written == 1                # (ts, rr_ms) tuple, zero skipped


def test_existing_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "legacy.db"
    # A pre-sleep database: no sleep tables and no activity_files.decoded_json.
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE device(mac TEXT PRIMARY KEY, fw TEXT, model TEXT, "
        "serial TEXT, last_seen INTEGER);"
        "CREATE TABLE samples(metric TEXT, ts INTEGER, value REAL, "
        "PRIMARY KEY(metric, ts));"
        "CREATE TABLE activity_files(id TEXT PRIMARY KEY, kind TEXT, ts INTEGER, "
        "raw BLOB);"
        "CREATE TABLE sync_log(ts INTEGER, n_records INTEGER, note TEXT);"
    )
    conn.commit()
    conn.close()

    with Store(str(path)) as s:
        cols = {r[1] for r in s._conn.execute("PRAGMA table_info(activity_files)")}
        assert "decoded_json" in cols
        assert s.ingest(_decoded_result())["sleep"] == 1
        assert len(s.sleep_sessions()) == 1
        assert len(s.rri()) == 2


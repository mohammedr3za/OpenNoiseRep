"""Estimated sleep staging, its persistence, and the band-record path."""
import datetime as dt

from noiserep import scores
from noiserep.protocol.decode import decode_payload
from noiserep.protocol.files import STAGE_AWAKE, STAGE_DEEP, STAGE_LIGHT, STAGE_REM
from noiserep.sleepstage import stage_night
from noiserep.store import Store
from noiserep.sync import SyncResult
from tests.test_fitness_records import _sleep_reply

BED = dt.datetime(2026, 10, 28, 23, 0)
WAKE = BED + dt.timedelta(hours=8)


def _hr(fn):
    return [(BED + dt.timedelta(seconds=30 * i), fn(i)) for i in range(960)]


def _night_hr(i):
    """Settling 20 min, quiet early (deep-like), jittery late (REM-like), wake at end."""
    minute = i / 2
    if minute < 20:
        return 78
    if minute > 460:
        return 85
    if minute < 200:
        return 52 + (i % 2)
    return 60 + 6 * ((i * 7) % 5 - 2) / 2


def test_staging_rollup_is_consistent_and_plausible():
    n = stage_night(BED, WAKE, _hr(_night_hr), [])
    assert n and n.estimated
    assert n.latency_min >= 15
    assert n.deep_min + n.light_min + n.rem_min + n.awake_min == n.time_in_bed_min
    assert n.total_min == n.deep_min + n.light_min + n.rem_min
    assert n.efficiency == round(100 * n.total_min / n.time_in_bed_min)
    assert n.deep_min > 0 and n.rem_min > 0
    assert n.deep_min <= 0.22 * n.total_min + 5 and n.rem_min <= 0.27 * n.total_min + 5
    assert n.stages[0] == (BED, STAGE_AWAKE)
    deep = [t for t, s in n.stages if s == STAGE_DEEP]
    assert deep and min(deep) < BED + dt.timedelta(hours=4)      # deep early
    assert all(t >= BED + dt.timedelta(minutes=80)
               for t, s in n.stages if s == STAGE_REM)           # REM latency


def test_movement_marks_wake_and_deterministic():
    moves = [(BED + dt.timedelta(minutes=300 + 5 * k), 4) for k in range(6)]
    n = stage_night(BED, WAKE, _hr(_night_hr), moves)
    quiet = stage_night(BED, WAKE, _hr(_night_hr), [])
    assert n.awake_min >= quiet.awake_min + 25
    assert n == stage_night(BED, WAKE, _hr(_night_hr), moves)


def test_too_little_data_yields_none():
    assert stage_night(BED, WAKE, _hr(_night_hr)[:100], []) is None
    assert stage_night(BED, BED + dt.timedelta(minutes=30), _hr(_night_hr), []) is None
    awake_hr = _hr(lambda i: 90 if i % 2 else 50)    # never settles
    assert stage_night(BED, WAKE, awake_hr, [(BED + dt.timedelta(minutes=5 * k), 5)
                                             for k in range(96)]) is None


def _store_with_night():
    s = Store(":memory:")
    cur = s._conn.cursor()
    cur.executemany("INSERT INTO samples(metric, ts, value) VALUES('heart_rate',?,?)",
                    [(int((t - dt.datetime(1970, 1, 1)).total_seconds()), v)
                     for t, v in _hr(_night_hr)])
    cur.executemany("INSERT INTO rri_samples(ts, rr_ms) VALUES(?,?)",
                    [(int((BED + dt.timedelta(seconds=i)).replace(
                        tzinfo=dt.timezone.utc).timestamp()), 1000 + (i % 7) * 10)
                     for i in range(0, 8 * 3600)])
    s._conn.commit()
    return s


def test_derived_session_persists_idempotently_and_feeds_day_scores():
    from noiserep.store.db import _derive_rest_sessions
    s = _store_with_night()
    assert _derive_rest_sessions(s._conn) == 1
    _derive_rest_sessions(s._conn)
    [row] = s.sleep_sessions()
    assert row["source"] == "derived" and row["deep_min"] is not None
    first = s.sleep_stages(row["id"])
    assert first
    _derive_rest_sessions(s._conn)
    [row2] = s.sleep_sessions()
    assert [(x["ts"], x["stage"]) for x in s.sleep_stages(row2["id"])] == \
        [(x["ts"], x["stage"]) for x in first]
    sleep = scores.day_scores(s, WAKE.date()).sleep
    assert sleep["source"] == "derived" and sleep["efficiency"] == row["efficiency"]
    assert sleep["light_min"] is not None and sleep["awake_min"] is not None
    s.close()


def test_band_session_wins_and_is_never_overwritten():
    from noiserep.store.db import _derive_rest_sessions
    s = _store_with_night()
    _derive_rest_sessions(s._conn)
    [session] = decode_payload(_sleep_reply())          # 23:30 -> 07:00, overlaps
    s.ingest(SyncResult(sleep=[session]))
    assert [r["source"] for r in s.sleep_sessions()] == ["band"]
    _derive_rest_sessions(s._conn)                      # re-derive: band stays alone
    [row] = s.sleep_sessions()
    assert row["source"] == "band" and row["deep_min"] == 60
    s.close()


def test_band_record_reaches_day_scores_end_to_end():
    [session] = decode_payload(_sleep_reply())
    s = Store(":memory:")
    s.ingest(SyncResult(sleep=[session]))
    sleep = scores.day_scores(s, dt.date(2026, 10, 29)).sleep
    assert (sleep["deep_min"], sleep["rem_min"], sleep["light_min"],
            sleep["awake_min"]) == (60, 90, 240, 10)
    assert (sleep["efficiency"], sleep["latency_min"], sleep["quality"]) == (93, 15, 81)
    assert [r["stage"] for r in s.sleep_stages(sleep["id"])] == [STAGE_DEEP, STAGE_AWAKE]
    s.close()

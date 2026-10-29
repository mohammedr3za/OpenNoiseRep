"""Vendor-schema layouts, sleep/nap/workout records and index-driven sync.

All payloads are synthetic, built with the same wire encoder the band speaks.
"""
import asyncio
import datetime

from noiserep import wire
from noiserep.protocol.decode import (
    MetricSeries, SportIds, Workout, decode_payload,
)
from noiserep.protocol.files import STAGE_AWAKE, STAGE_DEEP, SleepSession
from noiserep.store import Store
from noiserep.sync import SyncResult, run_sync

DAY = datetime.datetime(2026, 10, 28)
BED = datetime.datetime(2026, 10, 28, 23, 30)
WAKE = datetime.datetime(2026, 10, 29, 7, 0)


def _epoch(d: datetime.datetime) -> int:
    return int(d.timestamp())          # local wall clock, as the band's UTC epoch


def _date(d: datetime.datetime) -> bytes:
    return wire.encode([(1, d.year), (2, d.month), (3, d.day),
                        (4, d.hour), (5, d.minute), (6, d.second)])


def _entry(d: datetime.datetime, metric: int) -> bytes:
    return wire.encode([(1, _date(d)), (2, metric)])


def _reply(oneof: int, metric: int, fields, d=DAY, cmd=113) -> bytes:
    body = wire.encode([(1, _entry(d, metric))] + list(fields))
    return wire.encode([(1, cmd), (9, wire.encode([(oneof, body)]))])


def _items(*messages) -> bytes:
    return wire.encode([(1, m) for m in messages])


def _sleep_reply(exists: int = 1, d=DAY) -> bytes:
    stages = _items(
        wire.encode([(1, _epoch(BED)), (2, 3600), (3, 2)]),     # deep
        wire.encode([(1, _epoch(WAKE) - 600), (2, 600), (3, 0)]),  # awake
    )
    return _reply(19, 15, [
        (2, exists), (3, _epoch(BED)), (4, _epoch(WAKE)), (5, 7 * 3600),
        (6, 7 * 3600 + 1800), (7, 900), (8, 93), (9, 81), (10, 600),
        (11, 4 * 3600), (12, 3600), (13, 5400), (14, stages), (15, b""),
    ], d=d)


# -- array layouts --------------------------------------------------------

def test_temperature_is_big_endian_hundredths_and_ff_is_blank():
    s = decode_payload(_reply(12, 8, [(2, 5), (3, bytes.fromhex("0c1b ffff 0000"))]))
    assert s.samples == [30.99, 0, 0]
    assert s.interval_s == 86400 // 3


def test_movement_levels_give_hourly_active_minutes():
    levels = [1, 2, 3, 4, 5, 0] + [0] * 6 + [2] * 12 + [0] * 264   # 288 slots
    s = decode_payload(_reply(18, 14, [(2, bytes(levels))]))
    assert s.interval_s == 300
    assert s.channels["active_minutes"][:3] == [15, 60, 0]


def test_respiratory_rate_lives_in_field_two():
    s = decode_payload(_reply(23, 19, [(2, bytes([0, 14, 16, 0]))]))
    assert s.samples == [0, 14, 16, 0]


def test_health_score_and_band_resting_hr_become_channels():
    hs = decode_payload(_reply(21, 17, [(2, 75), (3, 36216)]))
    assert hs.channels == {"health_score": [75], "sedentary_min": [603]}
    hr = decode_payload(_reply(6, 2, [(2, 30), (3, bytes([60, 61])), (6, 52)]))
    assert hr.samples == [60, 61]
    assert hr.channels == {"resting_hr": [52]}


# -- records --------------------------------------------------------------

def test_band_sleep_result_decodes_stages_and_scores():
    [session] = decode_payload(_sleep_reply())
    assert isinstance(session, SleepSession)
    assert (session.bedtime, session.wake) == (BED, WAKE)
    assert session.source == "band"
    assert (session.total_min, session.deep_min, session.light_min,
            session.rem_min, session.awake_min) == (420, 60, 240, 90, 10)
    assert (session.quality, session.efficiency, session.latency_min,
            session.time_in_bed_min) == (81, 93, 15, 450)
    assert [st.stage for st in session.stages] == [STAGE_DEEP, STAGE_AWAKE]
    assert session.stages[0].ts == BED


def test_no_sleep_flag_yields_no_session():
    assert decode_payload(_sleep_reply(exists=0)) == []


def test_naps_and_auto_workouts():
    nap_start = datetime.datetime(2026, 10, 28, 14, 0)
    naps = decode_payload(_reply(20, 16, [(2, _items(wire.encode([
        (1, 1), (2, _epoch(nap_start)), (3, _epoch(nap_start) + 1800),
        (4, 1800)])))]))
    assert [(n.bedtime, n.total_min, n.source) for n in naps] == [
        (nap_start, 30, "nap")]

    walk_start = datetime.datetime(2026, 10, 28, 17, 5)
    workouts = decode_payload(_reply(24, 20, [(2, _items(wire.encode([
        (1, 1), (2, 1), (3, _epoch(walk_start)), (4, 1200), (5, 2000),
        (6, 90)])))]))
    assert workouts == [Workout(walk_start, "walk", 1200, "medium", 2000, 90)]


def test_sport_id_list_reply():
    reply = wire.encode([(1, 117), (9, wire.encode([(3, b"")]))])
    assert decode_payload(reply) == SportIds(b"")


# -- sync -----------------------------------------------------------------

class FakeBand:
    def __init__(self, replies):
        self._q = list(replies)
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)

    async def recv(self, timeout=10.0):
        if not self._q:
            raise asyncio.TimeoutError
        return self._q.pop(0)


def test_sync_fetches_every_indexed_record_and_confirms_only_good_ones():
    day2 = DAY + datetime.timedelta(days=1)
    devinfo = wire.encode([(1, 32), (4, wire.encode([(1, wire.encode([
        (1, b"1.4.3"), (2, b"1"), (3, b"aa:bb"), (4, b"R1")]))]))])
    index = wire.encode([(1, 112), (9, wire.encode([(2, _items(
        _entry(DAY, 2), _entry(day2, 2), _entry(day2, 15)))]))])
    ok = wire.encode([(1, 115), (100, 0)])
    broken = wire.encode([(1, 113), (9, b"\x0a\x05\x01")])
    band = FakeBand([
        devinfo, index,
        _reply(6, 2, [(2, 30), (3, bytes([70]))]), ok,   # day 1 HR + confirm
        broken,                                          # day 2 HR: no confirm
        _sleep_reply(d=day2), ok,                        # sleep + confirm
    ])
    result = asyncio.run(run_sync(band, datetime.datetime(2000, 1, 1),
                                  want_files=False, reply_timeout=0.05))

    def sent(cmd):
        return [(wire.field(wire.sub(p, 9, 1, 1), 3), wire.field(wire.sub(p, 9, 1), 2))
                for p in band.sent if wire.field(p, 1) == cmd]

    assert sent(113) == [(28, 2), (29, 2), (29, 15)]   # each record's own day
    assert sent(115) == [(28, 2), (29, 15)]            # broken reply not confirmed
    assert [s.metric_id for s in result.series] == [2]
    assert [s.source for s in result.sleep] == ["band"]


def test_store_keeps_band_sleep_extras_and_workouts():
    [session] = decode_payload(_sleep_reply())
    walk = Workout(datetime.datetime(2026, 10, 28, 17, 5), "walk", 1200,
                   "medium", 2000, 90)
    s = Store(":memory:")
    counts = s.ingest(SyncResult(sleep=[session], workouts=[walk]))
    assert counts["workouts"] == 1 and counts["sleep_stages"] == 2
    [row] = s.sleep_sessions()
    assert (row["quality"], row["efficiency"], row["latency_min"]) == (81, 93, 15)
    [w] = s.workouts()
    assert (w["kind"], w["duration_s"], w["steps"]) == ("walk", 1200, 2000)
    s.close()


def test_derived_channels_are_stored_next_to_samples():
    s = Store(":memory:")
    s.ingest(SyncResult(series=[MetricSeries(
        DAY, 2, 30, [0, 61], b"", channels={"resting_hr": [52]})]))
    assert [v for _, v in s.samples("heart_rate")] == [61.0]
    assert s.samples("resting_hr") == [(DAY, 52.0)]
    s.close()

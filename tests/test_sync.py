# tests/test_sync.py
import asyncio
import datetime

from noiserep.sync import run_sync, write_result, SyncResult
from noiserep.protocol.decode import DeviceInfo, DataIndex, MetricSeries, DeviceFile


class FakeBand:
    """Replays a queue of captured recv payloads; recv() raises TimeoutError
    when the queue is empty (mimicking a quiet band)."""
    def __init__(self, replies):
        self._q = list(replies)
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)

    async def recv(self, timeout=10.0):
        if not self._q:
            raise asyncio.TimeoutError
        return self._q.pop(0)


def _recv_by_type(payloads, cmd_type, longest=False):
    hits = [p.raw for p in payloads if p.direction == "recv" and p.cmd_type == cmd_type]
    if not hits:
        return None
    return max(hits, key=len) if longest else hits[0]


def test_run_sync_sorts_replies(capture_payloads):
    dev = _recv_by_type(capture_payloads, 32)
    idx = _recv_by_type(capture_payloads, 112)
    hr = _recv_by_type(capture_payloads, 113, longest=True)   # HR, 2880 samples
    afile = _recv_by_type(capture_payloads, 4097)
    queue = [p for p in (dev, idx, hr, afile) if p is not None]

    band = FakeBand(queue)
    since = datetime.datetime(2026, 10, 27)
    result = asyncio.run(run_sync(band, since, want_files=True, reply_timeout=0.5, file_timeout=0.5))

    assert isinstance(result, SyncResult)
    assert isinstance(result.device, DeviceInfo)
    assert isinstance(result.index, DataIndex)
    assert any(isinstance(s, MetricSeries) and s.metric_id == 2 and s.samples for s in result.series)
    assert any(isinstance(f, DeviceFile) for f in result.files)
    assert band.sent  # it actually issued commands


def test_run_sync_uses_an_indexed_date_when_since_is_epoch(capture_payloads):
    from noiserep import wire

    idx = _recv_by_type(capture_payloads, 112)
    band = FakeBand([idx])
    asyncio.run(run_sync(band, datetime.datetime(2000, 1, 1),
                         want_files=False, reply_timeout=0.2))
    series = [p for p in band.sent if wire.field(p, 1) in (113, 115)]
    assert series
    date = wire.sub(series[0], 9, 1, 1)
    assert wire.field(date, 1) == 2026      # the index's year, not 2000


def test_write_result_writes_files(tmp_path, capture_payloads):
    dev = _recv_by_type(capture_payloads, 32)
    hr = _recv_by_type(capture_payloads, 113, longest=True)
    band = FakeBand([p for p in (dev, hr) if p is not None])
    result = asyncio.run(run_sync(band, datetime.datetime(2026, 10, 27),
                                  want_files=False, reply_timeout=0.3))
    out = write_result(result, str(tmp_path / "run"))
    import os, json
    assert os.path.isfile(os.path.join(out, "decoded.json"))
    doc = json.load(open(os.path.join(out, "decoded.json")))
    assert "series" in doc and "device" in doc

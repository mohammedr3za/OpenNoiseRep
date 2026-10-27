import datetime as dt
from noiserep.protocol.decode import decode_index


def _first(payloads, cmd_type, direction="recv", minlen=0):
    for p in payloads:
        if p.cmd_type == cmd_type and p.direction == direction and len(p.raw) >= minlen:
            return p.raw
    raise AssertionError(f"no {direction} type={cmd_type} payload >= {minlen}B")


def test_index(capture_payloads):
    idx = decode_index(_first(capture_payloads, 112, minlen=20))
    assert len(idx.records) >= 10
    assert idx.records[0].date == dt.datetime(2026, 10, 27, 0, 0, 0)
    metric_ids = {r.metric_id for r in idx.records}
    assert {0, 2, 4, 8}.issubset(metric_ids)

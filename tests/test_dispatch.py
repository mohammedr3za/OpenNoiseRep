from noiserep.protocol.metrics import metric_name
from noiserep.protocol.decode import (
    decode_payload, DeviceInfo, DataIndex, MetricSeries, DeviceFile, Status,
)


def test_metric_name_default():
    assert metric_name(999) == "metric_999"


def _recv(payloads, cmd_type, pick=max):
    cands = [p.raw for p in payloads if p.cmd_type == cmd_type and p.direction == "recv"]
    assert cands
    return pick(cands, key=len)


def test_dispatch_types(capture_payloads):
    assert isinstance(decode_payload(_recv(capture_payloads, 32)), DeviceInfo)
    assert isinstance(decode_payload(_recv(capture_payloads, 112)), DataIndex)
    assert isinstance(decode_payload(_recv(capture_payloads, 113)), MetricSeries)
    assert isinstance(decode_payload(_recv(capture_payloads, 4097)), DeviceFile)


def test_dispatch_status(capture_payloads):
    # the smallest type-113 recv is the completion frame: {f1=113, f100=0}
    small = _recv(capture_payloads, 113, pick=min)
    r = decode_payload(small)
    assert isinstance(r, Status)
    assert r.type == 113 and r.status == 0

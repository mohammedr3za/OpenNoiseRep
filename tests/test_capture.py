from collections import Counter

def test_reassembles_expected_totals(capture_payloads):
    assert len(capture_payloads) == 171

def test_type_histogram(capture_payloads):
    recv = Counter(p.cmd_type for p in capture_payloads if p.direction == "recv")
    sent = Counter(p.cmd_type for p in capture_payloads if p.direction == "sent")
    assert recv[32] == 3
    assert recv[4097] == 25
    assert recv[113] == 32
    assert sent[112] == 2
    assert recv[112] == 2

def test_payload_shape(capture_payloads):
    p = capture_payloads[0]
    assert p.direction in ("sent", "recv")
    assert isinstance(p.raw, bytes)

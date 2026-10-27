from noiserep import wire


def test_write_varint_known_values():
    assert wire.write_varint(0) == b"\x00"
    assert wire.write_varint(32) == b"\x20"
    assert wire.write_varint(112) == b"\x70"
    assert wire.write_varint(2026) == b"\xea\x0f"      # the capture's year
    assert wire.write_varint(232) == b"\xe8\x01"


def test_encode_varint_and_bytes_fields():
    # field 1 varint 113, field 9 length-delimited b"\x01\x02"
    out = wire.encode([(1, 113), (9, b"\x01\x02")])
    assert out == b"\x08\x71\x4a\x02\x01\x02"


def test_encode_roundtrips_through_reader():
    out = wire.encode([(1, 32), (2, 10), (9, b"abc")])
    assert wire.parse(out) == [(1, 0, 32), (2, 0, 10), (9, 2, b"abc")]


def test_write_varint_rejects_negative():
    import pytest
    with pytest.raises(ValueError):
        wire.write_varint(-1)

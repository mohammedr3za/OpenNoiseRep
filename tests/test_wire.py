from noiserep import wire

def test_read_varint_multibyte():
    # 0xea 0x0f -> 2026
    assert wire.read_varint(bytes.fromhex("ea 0f"), 0) == (2026, 2)

def test_parse_scalar_and_bytes():
    # f1=32 (08 20), f4="ab" (22 02 61 62)
    fields = wire.parse(bytes.fromhex("08 20 22 02 61 62"))
    assert fields == [(1, 0, 32), (4, 2, b"ab")]

def test_field_and_field_all():
    buf = bytes.fromhex("08 02 08 04 08 08")  # f1 repeated: 2,4,8
    assert wire.field(buf, 1) == 2
    assert wire.field(buf, 9, default=-1) == -1
    assert wire.field_all(buf, 1) == [2, 4, 8]

def test_sub_drills_nested():
    # outer f4 { inner f1 { leaf f1 = 7 } }
    leaf = bytes.fromhex("08 07")            # f1=7
    inner = bytes([0x0a, len(leaf)]) + leaf  # f1 = leaf
    outer = bytes([0x22, len(inner)]) + inner  # f4 = inner
    assert wire.sub(outer, 4, 1) == leaf
    assert wire.field(wire.sub(outer, 4, 1), 1) == 7
    assert wire.sub(outer, 9) is None


def test_parse_rejects_truncated_length_delimited():
    import pytest
    # f4 declares 5 bytes but only 2 remain
    with pytest.raises(ValueError, match="truncated length-delimited field"):
        wire.parse(bytes.fromhex("22 05 61 62"))

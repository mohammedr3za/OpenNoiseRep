"""Synthetic tests for activity-file assembly and the RRI / sleep decoders.

All fixtures are built here from the documented layouts; no capture data is
committed.  The real band payloads are all-zero (not worn), so these tests
pin the structure rather than the vendor's exact values.
"""
import datetime
import struct

from noiserep.protocol.decode import DeviceFile
from noiserep.protocol.files import (
    STAGE_AWAKE,
    STAGE_DEEP,
    STAGE_LIGHT,
    STAGE_REM,
    ActivityFileError,
    assemble_activity_files,
    decode_activity_file,
    decode_rri_file,
    decode_sleep,
    file_kind_from_name,
    parse_activity_file_id,
    parse_name_start,
)

BED = datetime.datetime(2026, 10, 28, 23, 0, 0)
WAKE = datetime.datetime(2026, 10, 29, 4, 0, 0)
FILE_TS = int(datetime.datetime(2026, 10, 28, 1, 8, 0).replace(
    tzinfo=datetime.timezone.utc).timestamp())


def _file_header(subtype: int, version: int, ts: int = FILE_TS) -> bytes:
    """7-byte Xiaomi activity-file header + the standard zero padding byte."""
    flags = (subtype << 2) & 0x7F        # type 0 (activity), detail 0
    return struct.pack("<IBBB", ts, 0, version, flags) + b"\x00"


# ---------------------------------------------------------------------------
# Chunk assembly
# ---------------------------------------------------------------------------

def test_assemble_orders_chunks_even_if_they_arrive_shuffled():
    chunks = [
        DeviceFile("a_RRI_Data.bin.txt", b"middle-1", 1),
        DeviceFile("a_RRI_Data.bin.txt", b"last", 2),
        DeviceFile("a_RRI_Data.bin.txt", b"first", 0),
        DeviceFile("a_RRI_Data.bin.txt", b"middle-2", 1),
    ]
    out = assemble_activity_files(chunks)
    assert len(out) == 1
    assert out[0].data == b"firstmiddle-1middle-2last"
    assert out[0].n_chunks == 4 and out[0].complete


def test_assemble_groups_by_name():
    chunks = [
        DeviceFile("one", b"A", 0), DeviceFile("one", b"B", 2),
        DeviceFile("two", b"C", 0), DeviceFile("two", b"D", 2),
    ]
    out = {f.name: f.data for f in assemble_activity_files(chunks)}
    assert out == {"one": b"AB", "two": b"CD"}


def test_assemble_skips_missing_first_chunk_by_default():
    chunks = [DeviceFile("x", b"middle", 1), DeviceFile("x", b"last", 2)]
    assert assemble_activity_files(chunks) == []

    raised = False
    try:
        assemble_activity_files(chunks, on_gap="raise")
    except ActivityFileError:
        raised = True
    assert raised

    kept = assemble_activity_files(chunks, include_incomplete=True)
    assert len(kept) == 1 and not kept[0].complete
    assert "first chunk" in kept[0].reason


def test_assemble_flags_missing_terminator():
    chunks = [DeviceFile("x", b"first", 0), DeviceFile("x", b"middle", 1)]
    kept = assemble_activity_files(chunks, include_incomplete=True)
    assert not kept[0].complete and "last chunk" in kept[0].reason


# ---------------------------------------------------------------------------
# RRI
# ---------------------------------------------------------------------------

def test_rri_all_zero_is_empty_not_an_error():
    decoded = decode_rri_file(b"\x00" * 64)
    assert decoded is not None
    assert decoded.samples == []
    assert decoded.total_slots == 32


def test_rri_too_short_returns_none():
    assert decode_rri_file(b"") is None
    assert decode_rri_file(b"\x00") is None
    assert decode_rri_file(None) is None


def test_rri_headerless_layout_skips_sentinels_and_outliers():
    # 0, 600, 0, 0xFFFF, 700, 120  (120 ms is an implausible interval)
    data = struct.pack("<6H", 0, 600, 0, 0xFFFF, 700, 120)
    start = datetime.datetime(2026, 1, 1, 0, 0, 0)
    decoded = decode_rri_file(data, start=start)
    assert decoded is not None
    assert not decoded.has_header
    assert decoded.total_slots == 6
    assert decoded.rejected == 1
    assert [(s.index, s.rr_ms) for s in decoded.samples] == [(1, 600), (4, 700)]
    assert decoded.samples[0].ts == start + datetime.timedelta(seconds=1)
    assert decoded.samples[1].ts == start + datetime.timedelta(seconds=4)


def test_rri_with_documented_seven_byte_header():
    values = struct.pack("<3H", 640, 704, 768)
    decoded = decode_rri_file(_file_header(subtype=0x00, version=3) + values)
    assert decoded is not None and decoded.has_header and decoded.version == 3
    assert decoded.start == datetime.datetime(2026, 10, 28, 1, 8, 0)
    assert [(s.index, s.rr_ms) for s in decoded.samples] == [
        (0, 640), (1, 704), (2, 768)]


def test_parse_activity_file_id_rejects_all_zero():
    assert parse_activity_file_id(b"\x00" * 16) is None


# ---------------------------------------------------------------------------
# Sleep: fixed "stages" layout
# ---------------------------------------------------------------------------

def _sleep_stages_payload():
    bed = int(BED.replace(tzinfo=datetime.timezone.utc).timestamp())
    wake = int(WAKE.replace(tzinfo=datetime.timezone.utc).timestamp())
    body = b"\x00" * 7
    body += struct.pack("<HII", 300, bed, wake)
    body += b"\x00" * 3
    body += struct.pack("<HHHH", 60, 180, 40, 20)
    body += b"\x00"
    body += struct.pack("<IB", bed, STAGE_LIGHT)
    body += struct.pack("<IB", bed + 3600, STAGE_DEEP)
    return _file_header(subtype=0x03, version=2) + body


def test_decode_sleep_stages_layout():
    session = decode_sleep(_sleep_stages_payload())
    assert session is not None
    assert session.source == "sleep_stages" and session.version == 2
    assert session.bedtime == BED and session.wake == WAKE
    assert (session.total_min, session.deep_min, session.light_min,
            session.rem_min, session.awake_min) == (300, 60, 180, 40, 20)
    assert session.quality is None
    assert [(s.ts, s.stage) for s in session.stages] == [
        (BED, STAGE_LIGHT), (BED + datetime.timedelta(hours=1), STAGE_DEEP)]


# ---------------------------------------------------------------------------
# Sleep: packetised "details" layout
# ---------------------------------------------------------------------------

def _packet(ts: int, packet_type: int, data: bytes) -> bytes:
    return (struct.pack("<I", 0xFFFCFAFB) + bytes([17])
            + struct.pack("<q", ts) + bytes([0, packet_type])
            + struct.pack(">H", len(data)) + data)


def _sleep_details_payload(with_summary: bool = True):
    bed = int(BED.replace(tzinfo=datetime.timezone.utc).timestamp())
    wake = int(WAKE.replace(tzinfo=datetime.timezone.utc).timestamp())

    # bitmap bit 3 -> quality is present; blocks at bits 4/5/6 are absent.
    body = bytes([0x10, 0])             # bitmap, isAwake
    body += struct.pack("<II", bed, wake)
    body += bytes([88])                 # quality

    if with_summary:
        summary = (bytes([0]) + struct.pack(">HHHHH", 270, 30, 120, 90, 60)
                   + bytes([0, 0]))
        body += _packet(bed, 16, summary)

    # Stage nibbles: 2=deep, 1=light, 0=awake, 3=rem; low 12 bits = minutes.
    spec = [(2, 60), (1, 120), (0, 30), (3, 90)]
    stages = b"".join(struct.pack(">H", (raw << 12) | offset)
                      for raw, offset in spec)
    body += _packet(bed, 17, stages)
    return _file_header(subtype=0x08, version=4) + body


def test_decode_sleep_details_with_summary():
    session = decode_sleep(_sleep_details_payload())
    assert session is not None
    assert session.source == "sleep_details" and session.version == 4
    assert session.bedtime == BED and session.wake == WAKE
    assert (session.total_min, session.deep_min, session.light_min,
            session.rem_min, session.awake_min) == (270, 60, 120, 90, 30)
    assert session.quality == 88 and not session.derived
    assert [s.stage for s in session.stages] == [
        STAGE_DEEP, STAGE_LIGHT, STAGE_AWAKE, STAGE_REM]
    assert session.stages[0].ts == BED
    assert session.stages[1].ts == BED + datetime.timedelta(minutes=60)
    assert session.stages[2].ts == BED + datetime.timedelta(minutes=180)
    assert session.stages[3].ts == BED + datetime.timedelta(minutes=210)


def test_decode_sleep_details_derives_durations_without_summary():
    session = decode_sleep(_sleep_details_payload(with_summary=False))
    assert session is not None and session.derived
    assert (session.total_min, session.deep_min, session.light_min,
            session.rem_min, session.awake_min) == (270, 60, 120, 90, 30)


def test_decode_sleep_rejects_non_sleep_payloads():
    assert decode_sleep(b"\x00" * 64) is None
    assert decode_sleep(b"not a sleep file at all, just text") is None
    assert decode_sleep(b"") is None


def test_file_kind_hint():
    assert file_kind_from_name("2026-10-28-0108_RRI_Data.bin.txt") == "rri"
    assert file_kind_from_name("2026-10-28_Sleep_Data.bin") == "sleep"
    assert file_kind_from_name("whatever.bin") == "file"


def test_parse_name_start_handles_unpadded_band_names():
    assert parse_name_start("2026-10-28-0108_RRI_Data.bin.txt") == \
        datetime.datetime(2026, 10, 28, 1, 8)
    assert parse_name_start("2002-3-1-0612_RRI_Data.bin.txt") == \
        datetime.datetime(2002, 3, 1, 6, 12)


def test_parse_name_start_rejects_unparseable_names():
    assert parse_name_start("RRI_Data.bin.txt") is None
    assert parse_name_start("2026-13-40-9999_RRI.bin") is None


def test_rri_name_is_the_window_end_not_the_start():
    name = "2026-10-28-0108_RRI_Data.bin.txt"
    data = struct.pack("<4H", 0, 600, 0, 700)      # four one-second slots
    decoded = decode_activity_file(name, data)
    assert decoded is not None
    end = datetime.datetime(2026, 10, 28, 1, 8)
    assert decoded.start == end - datetime.timedelta(seconds=4)
    assert [(s.index, s.rr_ms, s.ts) for s in decoded.samples] == [
        (1, 600, decoded.start + datetime.timedelta(seconds=1)),
        (3, 700, decoded.start + datetime.timedelta(seconds=3)),
    ]

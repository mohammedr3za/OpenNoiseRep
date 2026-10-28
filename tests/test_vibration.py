"""Byte-exact and round-trip tests for the experimental vibration builders.

No hardware is touched: these tests only assert the protobuf encoding and the
documented safety limits.
"""
import inspect

import pytest

from noiserep import wire
from noiserep.protocol import vibration


def _system(frame: bytes) -> bytes:
    return wire.field(frame, 4)


def _vibrations(frame: bytes) -> list[list[tuple[int, int, object]]]:
    system = _system(frame)
    test = wire.field(system, vibration.VIBRATION_TEST_FIELD)
    return [wire.parse(v) for v in wire.field_all(test, 1)]


def _vibration_fields(parsed):
    return {fno: value for fno, _wt, value in parsed}


# ---------------------------------------------------------------- find-wear

def test_find_band_start_is_byte_exact():
    assert vibration.find_band(True).hex(" ") == "08 02 10 12 22 02 28 00"
    assert vibration.find_band_start() == bytes.fromhex("0802101222022800")


def test_find_band_stop_is_byte_exact():
    assert vibration.find_band(False).hex(" ") == "08 02 10 12 22 02 28 01"
    assert vibration.find_band_stop() == bytes.fromhex("0802101222022801")


def test_find_band_round_trips_through_wire_parse():
    frame = vibration.find_band(start=True)
    assert wire.field(frame, 1) == vibration.SYSTEM_TYPE
    assert wire.field(frame, 2) == vibration.FIND_WEAR_SUBTYPE
    system = _system(frame)
    assert system is not None
    assert wire.field(system, vibration.FIND_DEVICE_FIELD) == 0

    stop = vibration.find_band(start=False)
    assert wire.field(_system(stop), vibration.FIND_DEVICE_FIELD) == 1


def test_find_pattern_aliases():
    assert vibration.vibration("find-band") == vibration.find_band_start()
    assert vibration.vibration("find") == vibration.find_band_start()
    assert vibration.vibration("stop") == vibration.find_band_stop()
    assert vibration.vibration("off") == vibration.find_band_stop()


# ------------------------------------------------------------ vibration test

def test_short_pattern_is_byte_exact():
    assert vibration.vibration("short").hex(" ") == (
        "08 02 10 3b 22 0c ca 02 09 0a 07 08 01 10 c8 01 18 64"
    )


def test_long_pattern_and_duration_override_are_byte_exact():
    assert vibration.vibration("long").hex(" ") == (
        "08 02 10 3b 22 0c ca 02 09 0a 07 08 01 10 e8 07 18 64"
    )
    # duration is in milliseconds and replaces the single pulse's on-time.
    assert vibration.vibration("short", duration=500).hex(" ") == (
        "08 02 10 3b 22 0c ca 02 09 0a 07 08 01 10 f4 03 18 64"
    )
    assert vibration.vibration("short", duration_ms=500) == \
        vibration.vibration("short", duration=500)


def test_double_pattern_is_byte_exact():
    assert vibration.vibration("double").hex(" ") == (
        "08 02 10 3b 22 19 ca 02 16 "
        "0a 06 08 01 10 78 18 64 "
        "0a 04 08 00 10 78 "
        "0a 06 08 01 10 78 18 64"
    )


def test_vibration_test_round_trips_through_wire_parse():
    frame = vibration.vibration("double", strength=55)
    assert wire.field(frame, 1) == vibration.SYSTEM_TYPE
    assert wire.field(frame, 2) == vibration.VIBRATION_TEST_SUBTYPE

    parsed = _vibrations(frame)
    assert len(parsed) == 3

    first = _vibration_fields(parsed[0])
    assert first[1] == 1          # vibrate on
    assert first[2] == 120        # ms
    assert first[3] == 55         # strength

    pause = _vibration_fields(parsed[1])
    assert pause[1] == 0          # vibrate off
    assert pause[2] == 120
    assert 3 not in pause         # strength is omitted while off


def test_repetitions_repeat_the_pattern():
    frame = vibration.vibration("short", repetitions=2)
    assert len(_vibrations(frame)) == 2


def test_round_trip_preserves_every_preset():
    for name in vibration.PRESETS:
        frame = vibration.vibration(name)
        assert wire.field(frame, 1) == 2
        assert wire.field(frame, 2) == 59
        assert len(_vibrations(frame)) == len(vibration.PRESETS[name])


# ----------------------------------------------------------- flat experiment

def test_flat_envelope_is_documented_experiment():
    assert vibration.find_band(True, envelope="flat").hex(" ") == (
        "08 20 22 02 28 00"
    )
    frame = vibration.vibration("short", envelope="flat")
    assert wire.field(frame, 1) == vibration.FLAT_SYSTEM_TYPE
    assert wire.field(frame, 2) is None
    assert wire.field(_system(frame), vibration.VIBRATION_TEST_FIELD) is not None


# -------------------------------------------------------------- safety rails

def test_unknown_pattern_rejected():
    with pytest.raises(ValueError, match="unknown pattern"):
        vibration.vibration("rave")


def test_duration_cap_rejected():
    with pytest.raises(ValueError, match="safety cap"):
        vibration.vibration("short", duration=vibration.MAX_DURATION_MS + 1)
    with pytest.raises(ValueError, match="positive"):
        vibration.vibration("short", duration=0)


def test_duration_rejected_for_multi_segment_and_find_patterns():
    with pytest.raises(ValueError, match="single-pulse"):
        vibration.vibration("double", duration=500)
    with pytest.raises(ValueError, match="start and a stop"):
        vibration.vibration("find-band", duration=5000)


def test_strength_range_enforced():
    with pytest.raises(ValueError, match="strength"):
        vibration.vibration("short", strength=0)
    with pytest.raises(ValueError, match="strength"):
        vibration.vibration("short", strength=101)


def test_repetition_cap_enforced():
    with pytest.raises(ValueError, match="repetitions"):
        vibration.vibration("short", repetitions=vibration.MAX_REPETITIONS + 1)
    with pytest.raises(ValueError, match="repetitions"):
        vibration.vibration("short", repetitions=0)


def test_segment_and_total_caps_enforced():
    too_many = [(True, 100)] * (vibration.MAX_SEGMENTS + 1)
    with pytest.raises(ValueError, match="at most"):
        vibration.vibration_test(too_many)
    too_long = [(True, 2000)] * 6          # 12 s of vibration
    with pytest.raises(ValueError, match="total vibration"):
        vibration.vibration_test(too_long)


def test_unknown_envelope_rejected():
    with pytest.raises(ValueError, match="unknown envelope"):
        vibration.find_band(True, envelope="magic")


def test_boolean_is_not_accepted_as_duration_or_strength():
    with pytest.raises(TypeError):
        vibration.vibration("short", duration=True)
    with pytest.raises(TypeError):
        vibration.vibration("short", strength=True)


# ------------------------------------------------------------- documentation

def test_request_metadata_records_confidence_and_unverified_status():
    for request in (vibration.FIND_WEAR, vibration.VIBRATION_TEST):
        assert request.confidence
        assert request.hardware_verified is False
    assert vibration.FIND_WEAR.subtype == 18
    assert vibration.FIND_WEAR.system_field == 5
    assert vibration.VIBRATION_TEST.subtype == 59
    assert vibration.VIBRATION_TEST.system_field == 41


def test_module_has_no_ble_import():
    source = inspect.getsource(vibration)
    assert "bleak" not in source
    assert "noiserep.ble" not in source
    assert "from ..ble" not in source

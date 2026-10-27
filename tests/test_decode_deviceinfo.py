import re

from noiserep.protocol.decode import decode_device_info


def _first(payloads, cmd_type, direction="recv", minlen=0):
    for p in payloads:
        if p.cmd_type == cmd_type and p.direction == direction and len(p.raw) >= minlen:
            return p.raw
    raise AssertionError(f"no {direction} type={cmd_type} payload >= {minlen}B")


def test_device_info(capture_payloads):
    di = decode_device_info(_first(capture_payloads, 32, minlen=10))
    assert re.fullmatch(r"\d+\.\d+\.\d+", di.firmware)
    assert di.serial and di.serial.isdigit()
    assert re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", di.mac)
    assert di.model.startswith("R")

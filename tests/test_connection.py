import asyncio
import pytest

from noiserep.ble import connection
from noiserep.errors import BandNotFound


def test_uuids_are_the_vendor_service_chars():
    assert connection.WRITE_UUID.startswith("16186f02")
    assert connection.READ_UUID.startswith("16186f01")
    assert connection.ACT_UUID.startswith("16186f03")


def test_connect_raises_band_not_found_when_scan_empty(monkeypatch):
    async def fake_find(name, timeout=None):
        return None

    fake_scanner = type("S", (), {"find_device_by_name": staticmethod(fake_find)})
    monkeypatch.setattr(connection, "_import_bleak", lambda: (fake_scanner, object))

    async def scenario():
        async with connection.connect("Noise_REP_XXXX", scan_timeout=0.1):
            pass

    with pytest.raises(BandNotFound):
        asyncio.run(scenario())


class _Dev:
    def __init__(self, name):
        self.name = name


def _scanner(devices):
    async def discover(timeout=None):
        return devices

    return type("S", (), {"discover": staticmethod(discover)})


def test_find_band_auto_detects_the_family_prefix():
    scanner = _scanner([_Dev("Other"), _Dev("Noise_REP_ABCD")])
    device = asyncio.run(connection.find_band(scanner, None, 0.1))
    assert device.name == "Noise_REP_ABCD"


def test_find_band_auto_detect_requires_exactly_one():
    with pytest.raises(BandNotFound, match="multiple"):
        asyncio.run(connection.find_band(
            _scanner([_Dev("Noise_REP_AAAA"), _Dev("Noise_REP_BBBB")]), None, 0.1))


def test_find_band_auto_detect_missing():
    with pytest.raises(BandNotFound, match="Noise_REP"):
        asyncio.run(connection.find_band(_scanner([_Dev("Other")]), None, 0.1))

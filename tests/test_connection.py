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

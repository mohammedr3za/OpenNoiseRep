"""Scan for the band by advertised name, connect, subscribe to the notify
characteristics, and hand back a Band. bleak is imported lazily so the rest of
noiserep works without it."""
from __future__ import annotations

from contextlib import asynccontextmanager

from ..errors import BandNotFound
from .transport import Band

READ_UUID = "16186f01-0000-1000-8000-00807f9b34fb"
WRITE_UUID = "16186f02-0000-1000-8000-00807f9b34fb"
ACT_UUID = "16186f03-0000-1000-8000-00807f9b34fb"


def _import_bleak():
    try:
        from bleak import BleakScanner, BleakClient
    except ImportError as exc:                       # pragma: no cover - env dependent
        raise BandNotFound(
            "bleak is not installed; install the live extra: pip install 'noiserep[ble]'"
        ) from exc
    return BleakScanner, BleakClient


@asynccontextmanager
async def connect(name: str, scan_timeout: float = 20.0, debug: bool = False):
    BleakScanner, BleakClient = _import_bleak()
    device = await BleakScanner.find_device_by_name(name, timeout=scan_timeout)
    if device is None:
        raise BandNotFound(f"band '{name}' not found")
    client = BleakClient(device, timeout=25.0)
    await client.__aenter__()
    try:
        band = Band(client, WRITE_UUID, debug=debug)
        for uuid in (READ_UUID, WRITE_UUID, ACT_UUID):
            await client.start_notify(
                uuid,
                (lambda u: (lambda _, d: band.on_notify(u, bytes(d))))(uuid),
            )
        yield band
    finally:
        await client.__aexit__(None, None, None)

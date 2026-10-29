"""Scan for the band by advertised name, connect, subscribe to the notify
characteristics, and hand back a Band. bleak is imported lazily so the rest of
noiserep works without it."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ..errors import BandNotFound, TransportError
from .transport import Band

READ_UUID = "16186f01-0000-1000-8000-00807f9b34fb"
WRITE_UUID = "16186f02-0000-1000-8000-00807f9b34fb"
ACT_UUID = "16186f03-0000-1000-8000-00807f9b34fb"

# The suffix is derived from the band's MAC, so it differs per device. Match
# the family prefix instead of hard-coding one user's band name.
BAND_PREFIX = "Noise_REP_"


async def find_band(scanner, name: str | None, timeout: float):
    """Return the BLEDevice to connect to.

    With an explicit ``name`` this is a plain lookup. With ``name=None`` it
    discovers ``Noise_REP_*`` devices and requires exactly one match, so two
    nearby bands never silently connect to the wrong one.
    """
    if name:
        device = await scanner.find_device_by_name(name, timeout=timeout)
        if device is None:
            raise BandNotFound(f"band '{name}' not found")
        return device

    finder = getattr(scanner, "find_device_by_filter", None)
    if finder is not None:
        # Return on the first advertisement instead of scanning the whole
        # timeout. ponytail: first band seen wins; use --name with two bands.
        device = await finder(
            lambda d, adv: (d.name or adv.local_name or "").startswith(BAND_PREFIX),
            timeout=timeout)
        if device is None:
            raise BandNotFound(f"no {BAND_PREFIX}* band found")
        return device

    devices = await scanner.discover(timeout=timeout)
    matches = sorted(
        (d for d in devices if (getattr(d, "name", None) or "").startswith(BAND_PREFIX)),
        key=lambda d: d.name,
    )
    if not matches:
        raise BandNotFound(f"no {BAND_PREFIX}* band found")
    if len(matches) > 1:
        names = ", ".join(d.name for d in matches)
        raise BandNotFound(
            f"multiple {BAND_PREFIX}* bands found ({names}); pass --name"
        )
    return matches[0]


def _import_bleak():
    try:
        from bleak import BleakScanner, BleakClient
    except ImportError as exc:                       # pragma: no cover - env dependent
        raise BandNotFound(
            "bleak is not installed; install the live extra: pip install 'noiserep[ble]'"
        ) from exc
    return BleakScanner, BleakClient


@asynccontextmanager
async def connect(name: str | None = None, scan_timeout: float = 20.0,
                  debug: bool = False):
    """Connect to a band by advertised name, or discover the only Noise_REP_*.

    ``name=None`` means auto-detect.  Windows' cached services are used: an
    uncached read fails on this band ("Catastrophic failure").  If repeated
    interrupted links leave the cache with only generic services, turning
    the PC's Bluetooth off and on rebuilds it.
    """
    BleakScanner, BleakClient = _import_bleak()
    device = await find_band(BleakScanner, name, scan_timeout)
    client = BleakClient(device, timeout=25.0)
    stage = "opening the band's GATT services"
    try:
        await asyncio.wait_for(client.__aenter__(), timeout=35.0)
        stage = "subscribing to band notifications"
        band = Band(client, WRITE_UUID, debug=debug)
        band.name = getattr(device, "name", None)
        for uuid in (READ_UUID, WRITE_UUID, ACT_UUID):
            await asyncio.wait_for(client.start_notify(
                uuid,
                (lambda u: (lambda _, d: band.on_notify(u, bytes(d))))(uuid),
            ), timeout=10.0)
        yield band
    except asyncio.TimeoutError as exc:
        raise TransportError(f"Timed out while {stage}; disconnecting before retry") from exc
    finally:
        try:
            await asyncio.wait_for(client.__aexit__(None, None, None), timeout=5.0)
        except (OSError, asyncio.TimeoutError):
            pass  # a failed Windows link may already have disappeared

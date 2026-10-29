"""Scan for the band by advertised name, connect, subscribe to the notify
characteristics, and hand back a Band. bleak is imported lazily so the rest of
noiserep works without it."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager, AsyncExitStack

from ..errors import BandNotFound, ConnectionCleanupError, TransportError
from .transport import Band

READ_UUID = "16186f01-0000-1000-8000-00807f9b34fb"
WRITE_UUID = "16186f02-0000-1000-8000-00807f9b34fb"
ACT_UUID = "16186f03-0000-1000-8000-00807f9b34fb"

# The suffix is derived from the band's MAC, so it differs per device. Match
# the family prefix instead of hard-coding one user's band name.
BAND_PREFIX = "Noise_REP_"
SERVICE_UUID = "16186f00-0000-1000-8000-00807f9b34fb"
CONNECT_TIMEOUT_S = 20.0
DISCONNECT_TIMEOUT_S = 8.0
logger = logging.getLogger(__name__)


async def find_band(scanner, name: str | None, timeout: float, address=None):
    """Return the BLEDevice to connect to.

    A remembered address matches even an advertisement without a local name.
    With no selected identity, use the first matching family advertisement.
    """
    if address:
        device = await scanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            raise BandNotFound("Remembered band not advertising; waiting for it to become available")
        return device
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
            lambda d, adv: any((label or "").startswith(BAND_PREFIX) for label in (d.name, adv.local_name)),
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


def remembered_address(store, name=None):
    """Use only a previously synced band, respecting an explicit name choice."""
    record = store.device()
    address = record.get("mac", "") if record else ""
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address):
        return None
    expected = BAND_PREFIX + address.replace(":", "")[-4:].upper()
    if name is not None and name.casefold() != expected.casefold():
        return None
    return address


def _remembered_windows_device(address, name):
    # In Bleak's Windows backend, BLEDevice supplies the numeric identity
    # directly; passing a string instead silently starts another scan.
    from bleak.backends.device import BLEDevice
    return BLEDevice(address, name or BAND_PREFIX + address.replace(":", "")[-4:].upper(), None)


@asynccontextmanager
async def _open_device(client_type, device, timeout):
    # A cached/general enumeration repeatedly stalled on this Windows/band
    # combination. Fresh discovery limited to the vendor service is verified
    # on hardware and avoids unrelated services/characteristics.
    options = ({"services": [SERVICE_UUID], "winrt": {"use_cached_services": False}}
               if sys.platform == "win32" else {})
    client = client_type(device, timeout=timeout, **options)
    band = None
    stage = "opening the band's GATT services"
    try:
        # Give Bleak's own timeout time to clean up before our outer guard.
        await asyncio.wait_for(client.__aenter__(), timeout=timeout + 5.0)
        stage = "subscribing to band notifications"
        band = Band(client, WRITE_UUID)
        band.name = getattr(device, "name", None)
        band.address = getattr(device, "address", None)
        for uuid in (READ_UUID, WRITE_UUID, ACT_UUID):
            await asyncio.wait_for(client.start_notify(
                uuid, (lambda u: (lambda _, d: band.on_notify(u, bytes(d))))(uuid)),
                timeout=6.0)
        stage = None
        yield band
    except asyncio.TimeoutError as exc:
        if stage is None:
            raise  # a caller's data timeout is not a connection failure
        raise TransportError(f"Timed out while {stage}; disconnecting before retry") from exc
    finally:
        await _close_device(client, band)


async def _close_device(client, band) -> None:
    """Finish bounded cleanup even if the owner is cancelled a second time."""
    async def release():
        try:
            if band is not None:
                await asyncio.wait_for(band.aclose(), timeout=2.0)
        finally:
            await asyncio.wait_for(client.__aexit__(None, None, None),
                                   timeout=DISCONNECT_TIMEOUT_S)

    cleanup = asyncio.create_task(release())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break  # inspect and report the task result below
    try:
        cleanup.result()
    except Exception as exc:
        logger.warning("BLE cleanup incomplete: %s", exc)
        raise ConnectionCleanupError("BLE cleanup incomplete; automatic reconnect paused") from exc
    if cancelled:
        raise asyncio.CancelledError()


@asynccontextmanager
async def connect(name=None, scan_timeout=20.0, debug=False, address=None,
                  on_status=lambda text: None):
    """Remembered Windows bands connect directly; discovery is a fallback.

    No OS pairing, adapter resets, band writes or address guessing are used.
    Do not start a second GATT request immediately after a timed-out one:
    Windows can keep the underlying request queued after cancellation.
    """
    from bleak.exc import BleakDeviceNotFoundError
    scanner, client_type = _import_bleak()
    async with AsyncExitStack() as stack:
        band = None
        if address and sys.platform == "win32":
            on_status("connecting remembered band")
            device = _remembered_windows_device(address, name)
            try:
                band = await stack.enter_async_context(
                    _open_device(client_type, device, CONNECT_TIMEOUT_S))
            except BleakDeviceNotFoundError:
                on_status("remembered device unavailable in Windows; scanning remembered band")
        if band is None:
            on_status("scanning remembered band" if address else "scanning for band")
            device = (await find_band(scanner, name, scan_timeout, address)
                      if address else await find_band(scanner, name, scan_timeout))
            on_status("opening band services")
            band = await stack.enter_async_context(
                _open_device(client_type, device, CONNECT_TIMEOUT_S))
        band.debug = debug
        yield band

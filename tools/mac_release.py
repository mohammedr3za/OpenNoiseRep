#!/usr/bin/env python3
"""Release the Noise REP band from this Mac so the phone can reclaim it.

SAFETY: this helper works ONLY at the BLE link layer (scan / connect /
disconnect). It NEVER sends any application-level command to the band (no
clear, reset, unbind or settings write) and never reads or writes band data.
It is non-destructive toward the band.

The band is unencrypted, so macOS normally keeps no bond for it; "release"
just means making sure this Mac holds no connection to it.

  --check       (default) read-only: look for the band in system_profiler
  --disconnect  scan for the band, open then immediately close a link
"""
import argparse
import asyncio
import re
import subprocess

DEFAULT_NAME = None          # None -> match the Noise_REP_* family
BAND_PREFIX = "Noise_REP_"
MAC_RE = re.compile(r"Address:\s*([0-9A-Fa-f:-]{17})")


def check(name=None):
    """Report whether macOS lists the band (read-only)."""
    needle = name or BAND_PREFIX
    out = subprocess.run(["/usr/sbin/system_profiler", "SPBluetoothDataType"],
                         capture_output=True, text=True, timeout=60).stdout
    lines = out.splitlines()
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    if not hits:
        print(f"{needle}: not listed by macOS (no bond, no active link).")
        return False
    for i in hits:
        block = lines[i:i + 12]
        addr = next((m.group(1) for ln in block if (m := MAC_RE.search(ln))), "<band-mac>")
        state = next((ln.strip() for ln in block if "Connected:" in ln), "Connected: unknown")
        print(f"{needle}: listed by macOS, address {addr}, {state}")
    return True


async def _disconnect(name, timeout):
    from bleak import BleakClient, BleakScanner  # lazy: bleak only when needed
    from noiserep.ble.connection import find_band

    try:
        dev = await find_band(BleakScanner, name, timeout)
    except Exception as exc:                   # BandNotFound or scan failure
        print(f"{name or BAND_PREFIX + '*'}: {exc}. It may be asleep, powered "
              "off, or already held by another central; nothing to drop.")
        return False
    async with BleakClient(dev, timeout=timeout):
        pass  # connect, then leave the context: link-layer disconnect only
    print(f"{dev.name}: connected and disconnected (link layer only).")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only status (default)")
    mode.add_argument("--disconnect", action="store_true", help="drop any lingering link")
    ap.add_argument("--name", default=DEFAULT_NAME,
                    help="band name (default: auto-detect Noise_REP_*)")
    ap.add_argument("--timeout", type=float, default=20.0, help="scan/connect seconds")
    args = ap.parse_args()

    if args.disconnect:
        asyncio.run(_disconnect(args.name, args.timeout))
    else:
        check(args.name)
    print("\nNext steps: if the band is still held, toggle Mac Bluetooth off "
          "(System Settings > Bluetooth), then turn the phone's Bluetooth on "
          "and reopen NoiseFit. See MAC_UNPAIR.md.")


if __name__ == "__main__":
    main()

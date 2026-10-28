# Vibration control (experimental, opt-in)

**Status: prototyped offline; not yet verified on hardware.**
This document covers a *write* to the band — the first feature in this project
that is not read-only. It is isolated in
[`noiserep/protocol/vibration.py`](../noiserep/protocol/vibration.py), is never
imported by sync/export/GUI code paths, and must only be triggered by an
explicit user opt-in. Nothing here runs automatically.

## What the band supports (as far as the evidence goes)

| Primitive | Wire shape | Evidence | Confidence |
|---|---|---|---|
| **Find band / find wear** (continuous vibration or ringing) | `Command{ type=2, subtype=18, system{ findDevice=0 (start) / 1 (stop) } }` | Gadgetbridge `XiaomiSystemService.CMD_FIND_WATCH` and `onFindWatch()`; the decompiled NoiseFit vendor SDK builds the same command (`SystemID.FIND_WEAR=0x12`, `FindMode.FIND_START/STOP=0/1`, `System.findMode` field 5) | **High for the protocol family** (two independent implementations agree); **unverified on the Noise REP** |
| **Vibration test pattern** (short/long/double, strength) | `Command{ type=2, subtype=59, system{ vibrationTestCustom{ repeated Vibration{vibrate=1/0, ms, strength} } } }` | `xiaomi.proto` declares `System.vibrationTestCustom = 41` (subtype 59) and the `Vibration` message; Gadgetbridge never calls it and no vendor capture of it was seen | **Low–medium**: schema exists, behaviour unproven |
| **Custom vibration patterns** (create/delete/assign) | System subtypes 46 (get), 58 (create), 61 (remove); `System` fields 38–43 | Gadgetbridge `XiaomiVibrationManager`; gated by `supportsCustomVibrationPatterns()`, which is only true for Mi Band 10 in the reference checkout | **Not applicable to the REP**; do not use |
| **Notification / alarm vibration** | Notification service (type 7) or alarm/schedule payloads | Not investigated here | Out of scope |

The module exposes the first two. Named presets (`short`, `long`, `double`,
`triple`) are encoded as `VibrationTest` patterns; `find-band` is the find-wear
primitive.

### The command-type discrepancy (important)

The project's own capture (`noisefit.pklg`) shows this band using **flat command
type values** in the outer envelope, e.g. `08 20` for device info, `08 70` for
the data index, `08 71`/`08 73` for series, and parameters in field 9
(`0x4a`). The current vendor SDK and Gadgetbridge use `type=2` plus a separate
`subtype`/`id` field instead (`08 02 10 …`). These are not the same encoding.

The capture contains **no find or vibration command**, so there is no direct
wire evidence that the band accepts the standard envelope. The builders
therefore default to the well-documented standard shape (`envelope="vendor"`)
and also expose an explicit `envelope="flat"` experiment matching the flat
field-1 type observed in this band's sync (`08 20` + `System` field 4). The
first hardware session must determine which one the firmware honours.

## Evidence details

Local, read-only references:

- `gb/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/service/devices/xiaomi/services/XiaomiSystemService.java`
  — `CMD_FIND_WATCH = 18`; `onFindWatch(start)` sends
  `type=2, subtype=18, System.findDevice = start ? 0 : 1`.
- `gb/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/service/devices/xiaomi/services/XiaomiVibrationManager.java`
  — custom-pattern management (`CMD_GET=46`, `CMD_ADD=58`, `CMD_REMOVE=61`);
  confirms the `VibratorError` codes (0 OK, 1 NUMBER_LIMIT, 2 ID_NOT_EXIST).
- `gb/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/service/devices/xiaomi/XiaomiSupport.java`
  — `triggerSleepAsAndroidHint()` repeats *find watch start → 500 ms → stop*
  with a 300 ms gap; a real vendor-adjacent "buzz" idiom.
- `gb/app/src/main/proto/xiaomi.proto` — `Command.system = 4`,
  `System.findDevice = 5`, `System.vibrationTestCustom = 41` (subtype 59),
  `VibrationTest{ repeated Vibration vibration = 1 }`, and
  `Vibration{ vibrate = 1, ms = 2, strength = 3 }`.
- `docs/PROTOCOL.md` — confirms the transport (chunked writes, start/end acks)
  and that this band is the non-encrypted Mi Watch Lite / Redmi Watch family.

External (vendor app) reference — the decompiled NoiseFit Android app, which
contains the ZhBraceletSDK and is the only stack in that APK using the REP's
GATT service UUIDs:

- `smali_classes9/com/zhapp/ble/utils/UuidUtils.smali` — maps
  `UUID_PROTOBUF_SERVICE` to `16186f00-…` and `CHAR_PROTOBUF_UUID_01..05` to
  `16186f01..05`.
- `smali_classes9/com/zh/wear/protobuf/WearProtos$WearPacket$Type.smali` —
  `SYSTEM = 2`.
- `smali_classes9/com/zh/wear/protobuf/SystemProtos$System$SystemID.smali` —
  `FIND_WEAR = 0x12` (18).
- `smali_classes9/com/zh/wear/protobuf/SystemProtos$FindMode.smali` —
  `FIND_START = 0`, `FIND_STOP = 1`.
- `smali_classes9/com/zh/wear/protobuf/SystemProtos$System.smali` —
  `FIND_MODE_FIELD_NUMBER = 5`, `DEVICE_INFO_FIELD_NUMBER = 3`.
- `smali_classes9/com/zjw/zhbraceletsdk/service/n/k.smali` — builds
  `WearPacket{type, id, payload}` and serializes it.

Web search was rate-limited during this investigation; the claims above rest on
the local Gadgetbridge checkout and the decompiled vendor APK, not on
third-party protocol write-ups.

## Safety rails

1. **Explicit opt-in only.** The planned `buzz` subcommand refuses to run
   without `--yes`. There is no prompt-in-the-middle, no environment-variable
   bypass, and no automatic trigger from sync, export, GUI startup, timers, or
   notifications.
2. **Read-only default preserved.** `noiserep/protocol/vibration.py` is pure
   stdlib and imports no BLE code. Existing commands (`sync`, `export`,
   `decode`, `gui`) are untouched; `vibration` is not imported by them.
3. **Hard duration cap.** `MAX_DURATION_MS = 10_000` per segment and in total,
   `MAX_SEGMENTS = 16`, `MAX_REPETITIONS = 5`. Find-band is continuous by
   nature, so the caller controls it and must keep the duration small.
4. **Always send stop.** Find-band start is paired with a stop frame in a
   `try/finally`; the stop should be `asyncio.shield`-ed so Ctrl+C still stops
   the motor. Disconnect after the run.
5. **Rate limiting.** One invocation = one pattern or one start/stop pair.
   `MIN_INTERVAL_S = 1.0` is documented; no loop/repeat CLI option is exposed.
6. **No pattern creation/deletion.** Subtypes 58/61 can leave stale or
   protected IDs on the band; they are deliberately not implemented.
7. **No automatic retry.** A NACK or `status=1` is reported, not retried. The
   flat-envelope experiment is a separate, explicit choice.

Residual risk: if the process is `SIGKILL`ed between start and stop, the band
may continue vibrating until its own timeout or until the user presses
"found it" on the band. Keep find-band runs to a few seconds, and do not test
while the band is being worn for a long period.

Hardware environment reminders: the band was previously observed to **power off
when unpaired/asleep**, and it accepts **one central at a time**. Put the band
nearby/on its charger, turn the phone's Bluetooth **off**, and retry if scan
fails. A scan failure is usually a sleeping band, not a protocol bug.

## UX ideas

- **Find my band**: `buzz --pattern find-band --duration 3 --yes` — start
  continuous vibration, sleep up to a few seconds, always stop.
- **Timer / alarm hint**: `buzz --pattern double --yes` — a short double pulse
  as a silent nudge.
- **Notification nudge**: `buzz --pattern short --yes` — one short pulse,
  suitable for a test button.
- **Haptic test / accessibility**: cycle `short`, `double`, `long` to confirm
  the motor and strength field.
- A future GUI control should sit behind a confirmation dialog, a visible
  "experimental write" label, and the same duration cap; it must not fire on
  data refresh or app launch.
- Do not present vibration as a medical alert or alarm guarantee.

## Hardware validation plan

Preconditions: band charged and nearby, phone Bluetooth off, no vendor app
connected, band not in a workout.

1. **Offline tests first**: `./venv/bin/python -m pytest tests/test_vibration.py -q`
   (byte-exact and round-trip; no hardware).
2. **Baseline read-only sync** with `-v`; confirm the band still answers
   `device_info` (32) and the sync completes. Record firmware/model.
3. **Lowest-energy write first**: `buzz --pattern short --yes`. Watch for:
   actual vibration, a `Command{type=2, status=0}` reply, or `status=1`.
4. **If no vibration / `status=1`**: try the documented flat experiment
   (`envelope="flat"`, bytes `08 20 …`) in a temporary script. Do not loop
   through guesses; capture the exact reply and request bytes.
5. **Find-band, 1 second only**: send start, wait 1 s, send stop. Confirm the
   motor stops before increasing duration. If stop is not honoured, press
   "found it" on the band/disconnect and treat find-band as unsafe on this
   firmware.
6. **Preset sweep**: `short`, `double`, `long`; note whether `ms` and
   `strength` are honoured or clamped.
7. **Regression checks**: band stays connected, battery/clock unaffected, a
   subsequent sync still works, no re-pairing or power-off. Re-run the
   read-only suite.
8. **Record results** (firmware, envelope used, exact bytes, reply status,
   observed behaviour) in this document and flip `hardware_verified` on the
   matching `VibrationRequest`.
9. **Rollback**: find and vibration-test commands are transient. If custom
   patterns are ever added later, delete created IDs (subtype 61) and verify
   protected IDs are untouched.

Expected reply shape (from Gadgetbridge, unverified here): the band echoes the
command type/subtype and carries a `status` field (`0` = success, `1` = generic
error). The REP's actual reply must be captured and documented.

## Planned CLI integration (not applied yet)

`noiserep/cli.py` is owned by another change; this is the exact snippet to add.
It requires `--yes`, sends at most one command, and always stops find-band.

```python
# --- in main(), next to the other subparsers ---
b = sub.add_parser("buzz", help="EXPERIMENTAL: vibrate the band (write; needs --yes)")
b.add_argument("--pattern", choices=sorted(set(vibration.PRESETS) | vibration.FIND_PATTERNS),
               default="short")
b.add_argument("--duration", type=float, default=None,
               help="find-band seconds (max 10); single-pulse patterns: ms")
b.add_argument("--strength", type=int, default=100, help="1-100")
b.add_argument("--name", default=None)   # None -> auto-detect Noise_REP_*
b.add_argument("--yes", action="store_true", help="required: confirm the write")
b.add_argument("-v", "--verbose", dest="verbose", action="store_true")
# ... dispatch:
if args.cmd == "buzz":
    if not args.yes:
        print("refusing to vibrate the band without --yes (experimental write)",
              file=sys.stderr)
        return 2
    return _buzz(args.name, args.pattern, args.duration, args.strength, args.verbose)


# --- handlers ---
async def _buzz_async(name, pattern, duration, strength, debug):
    import asyncio
    from .ble.connection import connect
    from .protocol import vibration

    if pattern in vibration.FIND_PATTERNS:
        if pattern in vibration.STOP_PATTERNS:
            async with connect(name, debug=debug) as band:
                await band.send(vibration.find_band_stop())
            return 0
        seconds = 3.0 if duration is None else min(float(duration),
                                                  vibration.MAX_DURATION_MS / 1000)
        if seconds <= 0:
            raise ValueError("duration must be positive")
        async with connect(name, debug=debug) as band:
            await band.send(vibration.find_band_start())
            try:
                await asyncio.sleep(seconds)
            finally:
                await asyncio.shield(band.send(vibration.find_band_stop()))
        return 0

    ms = None if duration is None else int(duration)
    async with connect(name, debug=debug) as band:
        await band.send(vibration.vibration(
            pattern=pattern, duration=ms, strength=strength))
    return 0


def _buzz(name, pattern, duration, strength, verbose):
    import asyncio
    from .errors import BandNotFound, TransportError
    from .protocol import vibration
    try:
        return asyncio.run(_buzz_async(name, pattern, duration, strength, verbose))
    except (BandNotFound, TransportError) as exc:
        print(f"buzz failed: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"buzz refused: {exc}", file=sys.stderr)
        return 2
```

Note: import `vibration` lazily (inside the handler) so the default read-only
paths never load the write module.

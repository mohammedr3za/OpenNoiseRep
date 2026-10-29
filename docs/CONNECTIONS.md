# Connection reliability: Windows investigation, 2026-10-29

## Observed failure and evidence

The band was discovered, including connectable advertisements, but Windows
GATT service/session setup timed out. This happened outside the GUI too.
The earlier Bluetooth-radio restart was followed by another failed attempt;
it was not sufficient by itself to establish the cause. A later app launch
connected in about two seconds. Missing OS pairing was not a blocker: live
reports were read without bonding.

The old cached/general service path also timed out in a controlled follow-up.
Fresh service discovery restricted to the Noise vendor service succeeded in
three consecutive wrist reconnects, without further radio resets:

| Trial | GATT ready | First live report | Wrist status received |
| --- | ---: | ---: | --- |
| 1 | 4.25 s | 4.53 s | yes |
| 2 | 5.72 s | 6.01 s | yes |
| 3 | 2.78 s | 3.06 s | yes |

Those trials did not force a public/random address type. They used the saved
device identity, fresh discovery, the vendor-service filter, all three notify
subscriptions, a basic-data request and a clean disconnect. They demonstrate
this recovery path on the tested laptop/band, not guaranteed instant pairing
on every Windows driver or firmware. The precise internal driver/cache fault
cannot be established from application-level logs alone.

## Implemented behavior

- Windows reconnect uses the previously synced identity without an implicit
  name scan. Fresh discovery is restricted to
  `16186f00-0000-1000-8000-00807f9b34fb`; the three existing characteristics
  and the band command protocol are unchanged.
- Device-not-found can fall back to address discovery. GATT timeouts do not
  immediately queue a second connection. Failed attempts back off from 3 to
  30 seconds. A fresh basic-data report resets the failure count.
- Cleanup drains/cancels notification writers before disconnecting, finishes
  across repeated cancellation, and reports incomplete release. An uncertain
  release blocks another owner until the process is restarted.
- Duplicate app launches focus/refuse the existing instance instead of killing
  it. Live-helper shutdown uses a parent control pipe and waits for normal
  asynchronous teardown and process exit before resuming the desktop link.
- Auto-reload waits for history sync and normal shutdown, and releases the
  instance lock before launching its replacement.
- Live data is requested before history. Three silent polling windows trigger
  recovery; stale live numbers are cleared after loss. Sync draining and GATT
  writes are bounded instead of waiting indefinitely.

History download can take substantially longer than initial connection. The
UI reports it as syncing rather than treating the whole download as pairing.
The application never resets the radio or unpairs devices automatically.

## Validation

Offline regressions cover discovery identity, timeout cleanup, repeated
cancellation, backoff, stale streams, blocked handoffs, graceful helper exit,
duplicate launches, reload lock ordering and shared-database shutdown order.
Hardware timing above is separate from mocked tests. Capture-dependent tests
require a local vendor capture and are not substitutes for physical testing.

Windows documents variable GATT timing and queued requests, including that
cancelling an application's wait does not cancel the connection process:
[Microsoft Bluetooth GATT Client](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/gatt-client).

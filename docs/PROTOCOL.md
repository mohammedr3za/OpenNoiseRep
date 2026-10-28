# Noise REP — BLE protocol notes

Reverse-engineered from a packet capture of an official sync plus live probing
against the band. This is a working reference, not a vendor specification;
corrections via issues/PRs are welcome.

## Summary

The Noise REP advertises the standard Heart Rate Service (`0x180D`) but does
**not** implement it. It speaks the **Xiaomi wear protocol** (the same family as
the Mi Watch Lite / Redmi Watch) over a vendor GATT service. Crucially for this
project:

- **No authentication.** A full vendor sync contains no auth/handshake command,
  no user id, no key, and no nonce. The band answers the very first data command
  with `status=0`.
- **No link security.** No SMP pairing and no encryption-change events are needed;
  traffic is cleartext and the band accepts an unbonded central (one at a time —
  keep the phone's Bluetooth off while using this tool).

Earlier notes in this project chased a "missing credential" theory; the capture
disproved it. The real blocker had been sending the wrong command set.

## GATT layout

```
service 16186f00-0000-1000-8000-00807f9b34fb      (note 00807f, not the SIG 00805f)
  16186f01   command read / notify   <- protobuf replies arrive here
  16186f02   command write           <- send commands here
  16186f03   activity data / notify  <- bulk file fragments
  16186f04   data upload
  16186f05   vendor-specific (unused here)
```

The device also exposes the ARM Cordio sample-stack proprietary characteristic,
i.e. the firmware is a Cordio ODM build. Device-Information strings are stock
Cordio placeholders and are not a reliable identification signal; identify the
band by its advertised name (`Noise_REP_XXXX`, where `XXXX` is derived from the
MAC) instead.

## Chunk framing

Every payload is chunked — this band is a non-encrypted Xiaomi device, and such
devices chunk unconditionally. Single-command frames are rejected.

```
start frame :  00 00 | 00 | 00 | numChunks(u16)      header  <HBBH
chunk       :  idx(u16, 1-based) + up to 242 bytes
acks        :  00 00 01 01  start   |  00 00 01 00  end  |  00 00 01 02  NACK
```

Send sequence: write the start frame, wait for the start ack, write each chunk,
wait for the end ack. When the **band** initiates a chunked transfer (on
`16186f01`), the central must send the same start/end acks back.

## Command payloads

Payloads are protobuf. Each command carries a `type` (protobuf field 1) and the
band echoes it on reply; completion frames carry a status field (field 100,
`0 = ok`). Confirmed command types observed in a real sync and replayed live:

| type | meaning                     | notes |
|-----:|-----------------------------|-------|
| 32   | device info                 | firmware, serial, MAC, model |
| 112  | data index                  | list of available day/metric records |
| 113  | heart-rate / activity series| per-day sample arrays |
| 115  | activity series (2nd shape) | same structure as 113 |
| 117  | control / config            | short command |
| 123  | control / config            | short command |
| 232  | control / config            | short command |
| 4097 | named file transfer         | e.g. RRI / HRV files, bytes carried in fragments |

Requests carry a timestamp that acts as a **fetch-since** filter.

## Payload field structure (decode from raw fields)

The band's sub-messages do not line up with Gadgetbridge's `xiaomi.proto`, so
this project walks raw protobuf fields rather than trusting a generated schema.
Observed shapes:

- **Device info (type 32):** `field 4 -> field 1 -> { f1 firmware, f2 serial,
  f3 mac, f4 model }`. (In captures the mac reads like `aa:bb:cc:dd:ee:ff` and
  matches the advertised name suffix.)
- **Data index (type 112):** `field 9 -> field 2 -> repeated field 1 entries`,
  each `{ f1 date, f2 metric_id }`. A date is `{ f1 year, f2 month, f3 day,
  f4 hour, f5 min, f6 sec }`.
- **Series (type 113 / 115):** `field 9 -> <one sub-field> -> { f1 entry
  (date + metric_id), f2 interval or blob, f3 sample bytes }`. The body's f9
  sub-field number **varies per metric** (observed 4, 6, 8, 11..32), so decode by
  locating the sub-message that has an entry, not by a fixed field number.
- **File transfer (type 4097):** `field 16 -> field 1 -> { f1 name, f2 bytes,
  f3 flag }`.

### Per-metric identification

Each metric id appears in the data index (type 112) and in the `f1` entry of
its type-113/115 series body. The table is derived from `noisefit.pklg` (a real
vendor-app sync with a full day of data and one manual health measurement
around 15:15) plus the live captures. **likely** means the range, cadence and
cross-metric behaviour agree but a vendor-app label has not been read side by
side; ids without satisfactory evidence stay `metric_N` / "Channel N".

| id | meaning | unit | cadence | evidence | confidence |
|---:|---------|------|---------|----------|------------|
| 0 | daily activity (composite) | — | 60 min | 24 hourly u16-BE values per channel: f3 sums to f9 (total steps), f7 to f10 (total calories), f15 to f13 (active minutes) | likely |
| 2 | heart rate | bpm | 30 s | 2880 values/day, ~60-160, matches wrist HR | verified |
| 4 | blood oxygen (SpO2) | % | 5 min | single value 100 at a manual-measurement moment, zero elsewhere; 0-100 range | likely |
| 21 | heart-rate variability | ms | 15 min | 96 values/day; inverse to simultaneous HR (hourly r=-0.81), ~74-77 in sleep vs ~51-53 active | likely |
| 22 | stress | — (0-100) | 5 min | 288 values/day; tracks HR (hourly r=+0.91), low in sleep and higher while awake | likely |
| 27 | R-R interval | 10 ms | per beat (event) | 16 values 49-86 during a measurement match 60000/HR/10 within ~5.5 units; vendor `RRI_Data` files | likely |

Cadence is the body's `f2` value, whose unit is **not consistent across
metrics**: metric 2's `f2=30` is seconds (2880/day), while metric 4/22 `f2=5`,
metric 21 `f2=15` and metric 11/12/23 `f2=60` are minutes (288 / 96 / 24 values
per day). `decode.MetricSeries.interval_s` therefore carries the raw `f2`
number, not necessarily seconds. The `f9` sub-field number that holds the body
also varies per metric (observed 4, 6, 8, 12, 14, 15, 18, 21, 22, 23, 25, 26,
27, 31, 32), so decode by locating the sub-message that carries an entry.

Metric 0 is a multi-array record: five 48-byte arrays (f3, f5, f7, f14, f15)
each holding 24 big-endian u16 hourly values; only the first array is exposed
today. Unidentified ids keep their raw shape:

- **8:** 576 byte values, `f2=5`; four non-zero values (13, 50, 13, 32) only at
  the manual-measurement moment. Either a 2.5 min series or two interleaved
  5 min channels; unresolved.
- **11, 12:** 24 hourly 0/1 flags. 11 is set at hours 0-1 and 13-17; 12 at hour
  14. Purpose (standing/abnormal-event flag) not established.
- **14, 18:** 288 five-minute code bytes (0, 1, 3, 5; 0xff = not yet recorded).
  The codes do not follow sleep or activity blocks.
- **17:** scalars only (`f2`, `f3`); 14735/56 in the vendor capture vs 213/0 on a
  freshly unpaired band, consistent with a cumulative counter of unknown unit.
- **19:** 288 five-minute bytes, all zero except 18 and 22 at a manual
  measurement; unit unknown.
- **25:** nested protobuf in `f2`: six 308-byte records, each `{f1=0, f2=300
  zero bytes}`; meaning unknown.
- **26:** listed in the index, but the band answers with an empty status frame.

## Live confirmation (2026-10-28)

First run of the Phase 1 read-only client against the real band. A bare
connect was sufficient: scan by advertised name, subscribe to the three notify
characteristics, no auth, no BLE bond, and none of the vendor app's session
preamble (see "Commands sent by the vendor app" below). The band answered
`device_info` (32), `data_index` (112, 16 metric records), the `113`/`115` series
loop and the `123`/`117` controls, then auto-pushed activity files (4097).
Transport chunking and the start/end acks are confirmed working live.

The decoder round-trips live frames. DeviceInfo decoded all four fields
correctly (firmware `1.4.0`, model `R1…`, `<serial>`, `<mac>`; the last two are
redacted here). The index decoded to 16 metric records, and 13 activity files
were received and decoded to name / size / flag.

This disproves the earlier working theory (in the project's local notes, not in
this document) that the application layer rejects everything with `status=1`
and that an auth credential is required.

### Finding 1: the device clock is unset on a freshly-unpaired band

All index and file dates came back around `2002-03-01`. The band's RTC is not
set until something sends a time-sync. The vendor app sends a type-48 time-sync
(14-byte payload carrying a Unix epoch); the read-only client deliberately does
**not**, because that would be a write to the band. Consequence: timestamps
are device-relative / epoch-ish until an opt-in time-sync is added. That is a
write, so it is out of scope for the read-only fetch and belongs to a later
phase.

### Finding 2: detailed data arrives as pushed files, not the per-metric series

The `113` per-metric series returned empty for the query window: a 14-byte "no
data" reply, `08 71 a0 06 ff…`. The detailed data instead arrived as type-4097
files named like `…_RRI_Data.bin.txt` (about 7200 bytes, flag 1; the final file
carried flag 2). RRI is R-R interval (inter-beat / HRV) data. The internal byte
format of the RRI payload is **not yet known** and is still under analysis;
treat it as raw bytes until decoded (later phase / TBD).

### Commands sent by the vendor app (not required for read-only fetch)

A separate capture of the vendor app's full pair + sync shows a large session
preamble that our read-only client omits. The command types below were observed
in that traffic. They are **sent by the vendor app; NOT required for the
read-only historical fetch; relevant to Phase 2** (writes, time-sync, targeted
file fetch). Payload layouts are not documented here unless stated.

| type(s)     | observed role (as far as known) |
|-------------|---------------------------------|
| 0           | session open |
| 16 / 17 / 18 | config read / write |
| 48          | time-sync (14-byte payload carrying a Unix epoch) |
| 164 / 165   | observed in the preamble; purpose not determined |
| 420         | observed in the preamble; purpose not determined |
| 767         | observed in the preamble; purpose not determined |
| 753 / 756   | observed in the preamble; purpose not determined |
| 232 / 233   | observed in the preamble; purpose not determined |
| 64 / 68 / 69 | observed in the preamble; purpose not determined |
| 215         | observed in the preamble; purpose not determined |
| 21 / 23     | observed in the preamble; purpose not determined |
| 4109        | explicit file request (`08 8d 20`); the band replies with a `4097` file |

## Credits

The [Gadgetbridge](https://gadgetbridge.org/) project's Xiaomi device support was
an invaluable reference for the transport layer and chunk framing. This project
ships no Gadgetbridge-derived code; the decoders are written from the observed
wire format.

## Appendix: command frame bytes (from the capture)

All frames are the Command envelope `f1 = type`, optional `f9` = parameters.

- device info: `08 20`  (type 32)
- data index:  `08 70 4a 03 e8 01 03`  (type 112, f9 = {f29: 3})
- series:      `08 <type> 4a 13 0a 11 0a 0d 08 <year> 10 <month> 18 <day> 20 <h> 28 <m> 30 <s> 10 <metric>`
  (type 113 = `0x71`, 115 = `0x73`; date shown is the fetch-since filter)
- controls:    `08 75` (117), `08 7b 4a 03 e0 01 01` (123, f9 = {f28: 1}), `08 e8 01` (232)

**Sync choreography** (no handshake): send 112 (index), then for each metric in
`2,4,8,14,18,19,17,0,21,22,23,26,27,25,11,12` send 113 then 115, then send 123
and 117 — after which the band pushes its `4097` files unsolicited.

**Also observed (a separate connect/settings session, not the data sync):**
`16`, `17` (config; 75-byte reply), `48` (time sync — carries a Unix epoch),
and `4109` (`08 8d 20`) which explicitly requests a named file and gets a `4097`
reply. These are not needed for the historical fetch but document the wider
command set for later phases (time sync, targeted file fetch).

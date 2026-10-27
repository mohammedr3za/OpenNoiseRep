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

### Per-metric encoding (Phase-0 findings)

Metric identity and units are not yet fully mapped (that needs the live client
plus cross-checking against the vendor app — planned for a later phase). What the
capture shows so far:

- Heart rate (metric id 2): `field 3` sample bytes, 30 s cadence, 2880 samples
  (a full 24 h day). This decodes end-to-end today.
- Metrics 4, 8, 22: `field 3` samples at 5 s cadence.
- Metric 21: `field 3` samples at 15 s cadence.
- Metrics 0, 11, 12, 23: `field 3` samples at 60 s cadence (metric 0 is a
  multi-array record; only its first array is currently exposed).
- Metrics 14, 18, 19, 25, 27: data carried in `field 2` as a byte blob (metric 25
  is a nested protobuf); surfaced as raw bytes, not yet interpreted.
- Metric 17: scalars only, no sample array.

Sample-array lengths range from 24 to 2880 depending on cadence.

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

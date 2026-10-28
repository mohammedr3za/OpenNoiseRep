# Pushed activity files (type 4097) — format notes

This document records what is known about the files the band pushes during a
sync (`Command.type = 4097`), the decoders in
`noiserep/protocol/files.py`, and how confident each inference is. It is a
living log: update it as new hardware observations arrive.

## Wire envelope

A file chunk arrives as a protobuf `Command`:

```
Command {
  type = 4097
  field 16 -> field 1 {
    f1 = file name   (e.g. "2026-10-28-0108_RRI_Data.bin.txt")
    f2 = chunk data  (bytes)
    f3 = flag        (0 = first, 1 = middle, 2 = last)
  }
}
```

The chunk message itself carries **no chunk index or count**; only the flag and
the repeated name. Boundary presence is therefore the only structural
completeness signal available. `assemble_activity_files()` groups chunks by
name (the band repeats the identical name on every chunk), orders them as
first / middles in arrival order / last, and reports incomplete groups
(missing or duplicated boundary, unknown middle flag). Default behaviour skips
them; `on_gap="raise"` or `include_incomplete=True` are available.

In every capture so far the data chunks are exactly 7200 bytes and the final
chunk is shorter (1042 B in `data/live-hr-worn`, 5862 B in the NoiseFit
capture). 7200 is a transfer chunk size, not a record size — do not infer a
sample width from it.

### Observed state

* `data/live-hr-worn`: 25 chunks, every data byte zero (band not worn / RRI
  not recorded). All-zero payloads decode to an empty result, never an error.
* `noisefit.pklg` (NoiseFit's own sync): one logical
  `*_RRI_Data.bin.txt` file, 25 chunks (24 × 7200 + 5862 = 178 662 bytes),
  containing non-zero data. This is the only real payload available and is the
  basis for the RRI layout below. No sleep file has been observed from this
  device; the sleep layouts come from Gadgetbridge.

## Optional 7-byte activity-file header

Standard Xiaomi activity files begin with a 7-byte id (little-endian
`u32` timestamp, `u8` timezone in 15-minute blocks, `u8` version, `u8` packed
`type/subtype/detail`), usually followed by one zero padding byte. The
decoder accepts it only when it looks plausible (timestamp 2000–2100,
timezone ≤ 96, version ≤ 32) and returns the payload offset.

**The observed Noise RRI file does not have this header**: its first four bytes
are zero, so there is no plausible timestamp. It starts directly with sample
slots, and its date comes from the file name. Header support is retained for
other firmware, but treat it as unverified for this band.

## RRI files

### Inferred layout

After the optional header/padding the payload is a dense array of
**little-endian `u16` slots, one slot per second**:

```
offset 0      u16 rr_slot[0]
offset 2      u16 rr_slot[1]
...
```

* `0x0000` = no beat recorded — the ordinary gap sentinel.
* `0xFFFF` = no beat / invalid — also skipped. The observed file has one
  contiguous run of 2656 × `0xFFFF` slots, so it is clearly a marker rather
  than a real 65.5 s interval.
* Any other value is an RR interval in **milliseconds**. Values outside
  250–3000 ms are dropped and counted (`RriFile.rejected`).
* A non-empty `RriFile.samples` entry is `RriSample(index, rr_ms, ts)`, where
  `ts = start + index seconds` when the start time is known.

The band names an RRI file with the **end** of its window, not the start.
`decode_activity_file(name, data)` therefore parses `YYYY-M-D-HHMM_...` and
computes `start = name_timestamp - total_slots seconds`. Pass an explicit
`start` only when you know it is slot 0's time.

This was confirmed against the vendor capture: with the end interpretation the
RR burst lands at ~15:15, matching the capture's manual measurement; treating
the name as the start would place the same burst 24 hours later. A
caller-supplied explicit start still wins, and a 7-byte header timestamp (not
present on this band) is used as a start.

### Evidence and confidence

| Claim | Confidence | Evidence |
|---|---|---|
| Chunks join in flag order under one name | **High** | Flags 0/1/…/2 observed; identical names; deterministic sizes |
| Slots are u16 little-endian | **High** | Even-aligned LE values are a tight plausible range; odd-aligned/BE produce 16 386+ nonsense values |
| One slot per second / 24 h array | **Medium-high** | Body is 172 800 bytes = 86 400 × 2 B = exactly 24 h at 1 Hz; observed values occupy a contiguous slot run |
| Value unit is milliseconds | **Medium** | Values sit in a plausible RR range. The alternative (1/1024 s ticks) scales by 1000/1024 (−2.3%), within the band's quantisation |
| `0x0000`/`0xFFFF` are gap sentinels | **High** | Zero dominates unworn periods; the 0xFFFF run cannot be valid RR |
| File name stamps the window end | **High** | With `start = name - slots`, the vendor capture's RR burst lands at ~15:15 (its manual measurement); the start interpretation is +24 h |
| 7-byte header | **N/A for this band** | Not present in the observed file (leading timestamp bytes are zero); supported defensively |
| Plausible RR bounds 250–3000 ms | **Medium** | Restricts to 20–240 bpm; adjustable via arguments |

Observed values are quantised in steps of 64 (all multiples of 64 in the real
file). That is consistent with a firmware storing RR at 1/16-second resolution
(or `tick * 64 ms`). It does not change the millisecond-scale interpretation
enough to matter for storage, but it is a useful fingerprint when validating a
new capture.

With no `start` the decoder still returns intervals; only their absolute
timestamps are `None`. The store skips timestamp-less RRI samples.

## Sleep files

`decode_sleep()` recognises the two layouts implemented by Gadgetbridge for
this device family. It only returns a session when the payload structurally
validates (plausible epoch bedtime/wake, wake after bedtime); otherwise it
returns `None`. No values are invented.

### "Sleep stages" layout (subtype 0x03, version 2)

After the 7-byte id + padding:

| Size | Field |
|---|---|
| 7 | unknown |
| u16 | total sleep duration (minutes) |
| u32 | bedtime (epoch s) |
| u32 | wake time (epoch s) |
| 3 | unknown |
| u16 ×4 | deep, light, REM, awake durations (minutes) |
| 1 | unknown |
| repeated | u32 change time (epoch s), u8 stage |

Stage bytes use the same code space as Gadgetbridge's Xiaomi mapping:
`0` not sleep, `1` unknown/N/A, `2` deep, `3` light, `4` REM, `5` awake.
This layout carries no quality value.

### "Sleep details" layout (subtype 0x08, versions 1–5)

After the header/padding:

* a `1`-byte validity bitmap (versions 1–4) or `2` bytes (version 5);
* `u8` isAwake, `u32` bedtime, `u32` wake time;
* version ≥ 4: optional `u8` sleep quality (present when bitmap bit 3 is set);
* version 5: 9 unknown bytes, `u32` earlier bedtime, `u32` wake time;
* optional HR / SpO₂ / snore blocks, each present only when its bitmap bit is
  set and shaped `u16 unit, u16 count, [u32 first-record time if v≥2],
  count × (1|1|4) samples`;
* packet stream: magic `0x fffc fafb`, `u8` header length (17), `i64` RTC
  timestamp, `u8` parity, `u8` packet type, big-endian `u16` data length, data.

Relevant packet types:

* **16 — summary** (13-byte big-endian body):
  `u8 flags`, `u16 sleep`, `u16 awake`, `u16 light`, `u16 REM`, `u16 deep`,
  `u8 flags`, `u8 unknown`. These become the session durations.
* **17 — stages**: a sequence of big-endian `u16` values; high nibble = stage,
  low 12 bits = minutes until the next change, accumulated from the packet
  timestamp. Nibble mapping: `0`→awake, `1`→light, `2`→deep, `3`→REM,
  `4`→not-sleep, other→unknown. Stage timestamps are reconstructed from the
  first timestamp plus the offsets.
* **1 — RR intervals**: `u8` deltas in 10 ms units. Not part of the session
  model; ignored by `decode_sleep()`.
* Packet types 2, 3, 9, 12, 13, 14, 15 are header-only and carry no data.

If no summary packet is present, durations are summed from the stage
timestamps (`SleepSession.derived = True`); `total_min` is then
deep + light + REM, excluding awake. If neither summary nor stages exist the
durations stay `None`.

### Sleep confidence

| Claim | Confidence | Evidence |
|---|---|---|
| Sleep-stages layout | **Medium** | Matches Gadgetbridge `SleepStagesParser`; not observed on this band |
| Sleep-details layout | **Medium** | Matches `SleepDetailsParser` (v1–v5); not observed on this band |
| Packet magic / BE fields | **Medium-high** | Explicit in Gadgetbridge source; not independently observed here |
| This band's sleep subtype/version | **Unknown** | No sleep file captured yet |

The header is authoritative when present (subtype picks the layout). Without
a header, only the packetised layout is self-identifying (magic), and it needs
the header's version, so `decode_sleep()` declines rather than guessing.

## Store integration

`noiserep/store/db.py` now has `sleep_sessions`, `sleep_stages` and
`rri_samples` tables plus `ingest_sleep()`, `ingest_rri()`, and read methods
`sleep_sessions()`, `sleep_stages()`, `rri()`. `Store.ingest(SyncResult)`
picks up optional `result.sleep` and `result.rri` attributes, so no schema or
call-site change is needed once the sync layer populates them.

**Hook the sync owner must wire** (not edited here): after
`assemble_activity_files(result.files)` and decoding with
`decode_activity_file(name, blob, start)`, attach the results to the
`SyncResult` before calling `Store.ingest`, e.g.

```python
result.sleep = [session for session in decoded if isinstance(session, SleepSession)]
result.rri = [rrifile for rrifile in decoded if isinstance(rrifile, RriFile)]
```

Alternatively call `store.ingest_sleep(session)` / `store.ingest_rri(rrifile)`
directly. Start times for RRI files should be parsed from the band's file name
(`YYYY-MM-DD-HHMM_...`); otherwise the absolute timestamps are absent and the
store will skip those samples.

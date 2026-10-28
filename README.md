# noiserep

Pull raw heart-rate and activity data off the **Noise REP** fitness band without
the vendor app. Cross-platform (Linux + macOS), open source, no cloud account.

The band advertises the standard Heart Rate Service but doesn't implement it — it
speaks the Xiaomi wear protocol over a vendor GATT service, with **no
authentication and no BLE bond**. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for
the full reverse-engineering notes.

> **Read-only by default.** noiserep sends only the historical-fetch commands
> (`32`, `112`, `113`, `115`, `117`, `123`) and the transport acks they require.
> It does not write settings, alarms or the band clock, and it never deletes
> on-device history. The one explicit exception is the **experimental,
> `--yes`-gated `noiserep buzz`** haptics command; sync and the GUI never call it.

## Status

### What works

- **Offline decode (Phase 0, done).** `noiserep decode` reassembles a PacketLogger
  `.pklg` capture and decodes device info, the data index, metric series and named
  files. The core is pure standard library, and the suite runs without hardware.
- **Live historical sync (Phase 1, confirmed on hardware 2026-10-28).** Scan by
  advertised name, subscribe to the three notify characteristics, then fetch
  device info (`32`), the data index (`112`), the `113`/`115` series and the
  `117`/`123` controls; the band then pushes its `4097` activity files. Chunking
  and start/end acks work live.
- **Local store + export (Phase 2, implemented).** SQLite with idempotent upserts,
  CSV/JSON export, and the `sync` / `export` commands. Unit-tested with an
  in-memory store. Sleep sessions/stages and RR intervals have dedicated tables
  and queries.
- **Desktop app (Phase 3, implemented).** A PySide6 GUI (`noiserep gui`) with an
  overview, per-metric charts and export. Covered by an offscreen smoke test; a
  full hardware run of the GUI sync path is still pending.
- **Live menu bar (experimental).** `noiserep menubar` shows the current heart
  rate in the macOS menu bar. It tries the Xiaomi realtime command and falls
  back to polling the confirmed `113` heart-rate series.
- **RRI + sleep decoding (implemented, offline-validated).** A vendor capture
  yields 425 RR intervals (448–960 ms) whose timestamps land at ~15:15, matching
  the capture's manual measurement. Sleep layouts from the Xiaomi reference are
  implemented; no live sleep file has been captured yet. See
  [`docs/FILES.md`](docs/FILES.md).
- **Release tooling (Phase 4, in progress).** GitHub Actions CI, a PyInstaller
  build script, issue forms and a security policy. No signed or published
  binaries yet.

### Known limits

- **Metric identity is evidence-based, not vendor-confirmed.** Heart rate (2) is
  verified. Daily activity (0), SpO2 (4), HRV (21), stress (22) and RR interval
  (27) are marked **likely** and shown with a `(likely)` suffix in the GUI. The
  rest stay neutral (`Channel N`). Do not treat a likely channel as a medical
  reading; see the evidence table in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).
- **RRI decoding is implemented; the millisecond unit is inferred.** The layout
  is validated against a real vendor file, and the store/GUI consume it, but the
  unit was not confirmed against the band's own readout. Sleep decoders are
  implemented from the Xiaomi reference and are untested on a real sleep file.
- **A freshly-unpaired band's clock is unset**, so timestamps can read around
  2002. The read-only client does not send a time-sync; timestamps are
  device-relative. `sync` now defaults to the whole device timeline rather than
  the host's today, so this no longer hides the band's series data.
- The GUI's live sync uses the same tested orchestration as `noiserep sync`, but
  the window itself has only had offscreen smoke tests.
- macOS and Linux are supported; Windows is untested.
- This is not a medical device. Values are raw sensor readings.

## Install

```sh
python -m venv venv && . venv/bin/activate
pip install -e '.[ble,gui]'          # live sync + desktop app
```

The headless core has no third-party dependencies. Install only what you need:

```sh
pip install -e '.[dev]'              # pytest
pip install -e '.[ble]'              # CLI live sync (bleak)
pip install -e '.[gui]'              # desktop app (PySide6, pyqtgraph, qasync)
```

Python 3.10+.

## Usage

Decode a capture offline:

```sh
noiserep decode path/to/capture.pklg            # human-readable summary
noiserep decode path/to/capture.pklg --json out.json
```

Example output (device identifiers redacted):

```
device: fw 1.4.0  model R1...  mac aa:bb:cc:dd:ee:ff  serial <redacted>
index: 16 records across ['2002-03-01']
series: 2002-03-01  metric 2 (heart_rate): 2880 samples @ 30s
series: 2002-03-01  metric 14 (metric_14): 240 raw bytes (structure TBD)
file: 2002-03-01-0000_RRI_Data.bin.txt  7200 bytes  flag 1
```

Sync a live band (band nearby or on its charger, phone Bluetooth **off**):

```sh
noiserep sync                               # auto-detect a single Noise_REP_* band
noiserep sync --since 2026-10-01            # only the recent window
noiserep sync --name Noise_REP_ABCD         # pick a band when several are nearby
noiserep sync --no-files                    # skip the pushed RRI files
noiserep sync --db ~/my-noiserep.db         # override the database path
```

Without `--since`, sync asks for the whole device timeline (the band's clock may
read 2002 until the vendor app sets it).

`sync` writes raw frames plus `decoded.json` under `data/<timestamp>/` and
ingests the decoded samples into the local SQLite database. Re-syncing the same
day updates rows instead of duplicating them.

Export the stored history:

```sh
noiserep export --format csv --out all.csv
noiserep export --metric heart_rate --since 2000-03-01 --until 2000-03-31 --out hr.csv
noiserep export --format json --out all.json
```

Launch the desktop app:

```sh
noiserep gui
```

The app reads the same database, shows a Today overview plus per-metric charts,
and exports the selected range.

Live heart rate in the menu bar (experimental; band awake, phone Bluetooth off):

```sh
noiserep menubar --poll 30
```

Vibrate the band (experimental **write**, opt-in, never used by sync/GUI):

```sh
noiserep buzz --pattern short --yes
noiserep buzz --pattern find-band --duration 3 --yes
```

See [`docs/VIBRATION.md`](docs/VIBRATION.md) for the evidence and safety rails.

## Privacy

- No network access, no account, no telemetry. Your data stays on this machine.
- The SQLite database lives in the per-OS user-data directory
  (`~/Library/Application Support/noiserep/` on macOS,
  `~/.local/share/noiserep/` on Linux). `--db` overrides it.
- `data/`, exports, `.pklg` captures and the database contain **health data**;
  captures also contain the band **MAC and serial**. Never commit or share them.
- CLI output includes the MAC and serial. Redact them (and `-v` frame dumps)
  before posting logs or issues.
- See [`SECURITY.md`](SECURITY.md) for the vulnerability-reporting process.

## How to capture a `.pklg` (optional, for contributors)

On macOS, install Apple's *Bluetooth* logging profile and use **PacketLogger**
(part of the Additional Tools for Xcode) to record a sync, then export the trace.
A capture contains your device's MAC and your own health data — treat it as
private and never commit it.

Point the tests at your own capture with `NOISEREP_TEST_PKLG=/path/to/capture.pklg`;
without it, capture-dependent tests skip.

## Roadmap

| Phase | Scope | State |
|------:|-------|-------|
| 0 | Offline decoders + tests | **done** |
| 1 | Live BLE historical fetch | **confirmed live 2026-10-28**; RRI layout decoded offline |
| 2 | SQLite store + CSV/JSON export + `sync`/`export` CLI | **implemented**, unit-tested |
| 3 | PySide6 desktop GUI | **implemented**, offscreen smoke-tested; hardware run pending |
| 4 | CI, PyInstaller bundles, issue forms, first release | **in progress** (CI/build script landed; binaries pending) |
| 5+ | Metric validation, sleep/HRV analytics, settings, auto-sync, menu bar, alerts | see [`docs/ROADMAP.md`](docs/ROADMAP.md) |

Metric identity validation did **not** happen in Phase 1 as originally planned.
It moved to the top of the roadmap because showing a neutral channel is better
than guessing a health metric. The review of the original spec and plans is in
[`docs/SPEC-REVIEW.md`](docs/SPEC-REVIEW.md).

## Contributing

Protocol findings, decoders for the unmapped metrics, and the live client are all
welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md). Never include a real MAC,
serial or health value in an issue or test.

## License

MIT — see [`LICENSE`](LICENSE). This project ships no Gadgetbridge-derived code;
Gadgetbridge was a reference for the transport layer only.

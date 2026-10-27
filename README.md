# noiserep

Pull raw heart-rate and activity data off the **Noise REP** fitness band without
the vendor app. Cross-platform (Linux + macOS), open source, no cloud account.

The band advertises the standard Heart Rate Service but doesn't implement it — it
speaks the Xiaomi wear protocol over a vendor GATT service, with **no
authentication and no BLE bond**. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for
the full reverse-engineering notes.

## Status

This is an early, actively developed project. What works **today**:

- **Offline decode** of a captured sync (a PacketLogger `.pklg`) with zero
  hardware: device info, the data index, per-day metric series, and named file
  transfers — all parsed straight from the raw wire format.
- All ~15 metric series in a capture are recovered. **Heart rate** decodes
  end-to-end (2880 samples/day @ 30 s). Other metrics are recovered as raw
  sample arrays or byte blobs; their exact units are still being mapped (see the
  roadmap).

The headless core is pure standard-library Python and fully unit-tested against a
capture — no band required to run the tests.

## Install

```sh
python -m venv venv && . venv/bin/activate      # Windows: venv\Scripts\activate
pip install -e .                                # add '.[dev]' for the test deps
```

Python 3.10+. The core has no third-party dependencies.

## Usage

Decode a capture offline:

```sh
noiserep decode path/to/capture.pklg            # human-readable summary
noiserep decode path/to/capture.pklg --json out.json
```

Example output:

```
device: fw 1.4.0  model R1...  mac aa:bb:cc:dd:ee:ff  serial ...
index: 60 records across ['2026-10-27']
series: 2026-10-27  metric 2 (heart_rate): 2880 samples @ 30s
series: 2026-10-27  metric 14 (...): 240 raw bytes (structure TBD)
file: 2026-10-27_RRI_Data.bin  7200 bytes  flag 0
```

## Roadmap

| Phase | Scope | State |
|------:|-------|-------|
| 0 | Offline decoders + tests against a capture | **done** |
| 1 | Live BLE fetch client (`bleak`): pull the index + series + files from the band | planned |
| 2 | SQLite store + CSV / JSON export | planned |
| 3 | PySide6 desktop GUI with charts | planned |
| 4 | Per-OS packaging (AppImage / macOS app) + first binary release | planned |

Mapping the remaining metric ids to their real units (steps, SpO2, sleep, HRV, …)
happens in Phase 1, where values can be cross-checked against the band live.

## How to capture a `.pklg` (optional, for contributors)

On macOS, install Apple's *Bluetooth* logging profile and use **PacketLogger**
(part of the Additional Tools for Xcode) to record a sync, then export the trace.
A capture contains your device's MAC and your own health data — treat it as
private and never commit it.

## Contributing

Protocol findings, decoders for the unmapped metrics, and the live client are all
welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT — see [`LICENSE`](LICENSE). This project ships no Gadgetbridge-derived code;
Gadgetbridge was a reference for the transport layer only.

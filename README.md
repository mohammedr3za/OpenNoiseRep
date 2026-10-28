<p align="center">
  <img src="docs/images/menubar.png" alt="OpenNoiseRep live heart rate in the macOS menu bar" width="820">
</p>

<h1 align="center">OpenNoiseRep</h1>
<p align="center">Your Noise REP band's data, without the vendor app.</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-CC785C">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-CC785C">
  <img alt="macOS and Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-CC785C">
</p>

OpenNoiseRep connects to the band over BLE, syncs its history, stores it in
SQLite, and shows it in a desktop app, a menu-bar heart rate, and a CLI. The
Python package and command are still named `noiserep`.

## Screenshots

| Today | Sleep |
|---|---|
| ![Overview with vitals, highlights, heart/stress and movement goals](docs/images/overview.png) | ![Sleep timeline, HRV stats and stage panel](docs/images/sleep.png) |

| Day navigation | Menu bar |
|---|---|
| ![Day mode with calendar and previous/next day](docs/images/day.png) | ![Menu-bar heart states: 72, tilde, dash](docs/images/menubar.png) |

## Features

- Historical sync: device info, data index, heart-rate/activity series and
  pushed activity files.
- Desktop app: vitals, sleep, heart/stress, movement goals, charts and export.
- Day picker with calendar and previous/next day, plus 7-day, 30-day, all-data
  and custom ranges.
- Menu-bar heart rate: `♥ 72` worn, `♥ ~` off-wrist, `♥ —` disconnected.
  Optional walk-away lock.
- RRI/HRV rest summaries: duration, average HR, RMSSD/SDNN/pNN50 and coverage.
- Steps, distance, active calories and active minutes.
- CSV/JSON export and offline `.pklg` decoding.

## Install

```sh
python -m venv venv && . venv/bin/activate
pip install -e '.[ble,gui]'
```

Python 3.10+.

## Use

```sh
noiserep sync                      # fetch history
noiserep gui                       # desktop app
noiserep menubar                   # live heart rate in the menu bar
noiserep sleep                     # last night's rest summary
noiserep workouts                  # workout files, when present
noiserep export --format csv --out all.csv
noiserep decode capture.pklg       # offline
```

The band accepts one BLE central at a time. Wake it (or put it on the charger)
and turn the phone's Bluetooth off while syncing.

## Docs

- [Protocol notes](docs/PROTOCOL.md)
- [RRI and sleep file formats](docs/FILES.md)
- [Roadmap](docs/ROADMAP.md)
- [Release notes and data caveats](docs/RELEASE-NOTES.md)

## License

MIT — see [LICENSE](LICENSE).

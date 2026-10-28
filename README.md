<p align="center">
  <img src="docs/images/overview.png" alt="OpenNoiseRep home page with heart rate, vitals, highlights and movement goals" width="900">
</p>

<h1 align="center">OpenNoiseRep</h1>
<p align="center">Your Noise REP band's data, without the vendor app.</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-CC785C">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-CC785C">
  <img alt="macOS and Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-CC785C">
</p>

OpenNoiseRep is an open-source desktop app and menu-bar tool that reads heart
rate, steps, HRV, sleep/rest and RRI data from the Noise REP band over BLE. It
stores everything locally, needs no NoiseFit account, and exports CSV/JSON —
with a live heart rate in the Mac menu bar. The Python package and command are
still named `noiserep`. Screenshots use synthetic demo data.

## Screenshots

| Sleep | Day navigation |
|---|---|
| ![Sleep timeline, HRV stats and stage panel](docs/images/sleep.png) | ![Day mode with calendar and previous/next day](docs/images/day.png) |

| Menu bar | All channels |
|---|---|
| ![Menu-bar heart states: 72, tilde, dash](docs/images/menubar.png) | ![All channels with daily activity, HRV and stress](docs/images/channels.png) |

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

## Further development

- Custom notification vibration: validate the `buzz` patterns and add triggers.
- Live HR: confirm the realtime command or start the optical sensor on demand.
- Sleep stages and score: fetch and decode a staged sleep file.
- Workout files: parse sport files when the band sends them.
- Temperature calibration for raw values such as 213.
- Packaging, signing, and Windows.

## Docs

- [Protocol notes](docs/PROTOCOL.md)
- [RRI and sleep file formats](docs/FILES.md)
- [Roadmap](docs/ROADMAP.md)
- [Release notes and data caveats](docs/RELEASE-NOTES.md)
- [Experimental haptics](docs/VIBRATION.md)

## Contributing

Open to contributions from open-source developers. Protocol work, decoders,
UI and docs are all welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

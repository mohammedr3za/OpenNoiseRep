# Contributing to noiserep

Thanks for your interest! This project reverse-engineers the Noise REP band, so
protocol findings are as valuable as code.

## Development setup

```sh
python -m venv venv && . venv/bin/activate
pip install -e '.[dev]'                 # headless core + tests
pip install -e '.[dev,ble]'             # + live BLE sync
pip install -e '.[dev,ble,gui]'         # + desktop app
pytest -q                               # no hardware required
```

The headless core (`noiserep/`, except `ble/` and `gui/`) must stay **pure
standard library** so it installs anywhere and stays easy to audit. `bleak` is
confined to `noiserep/ble/` and imported lazily; PySide6/pyqtgraph/qasync are
confined to `noiserep/gui/`. Keep user-facing text (prints, `sys.exit`) in
`noiserep/cli.py` or the GUI, not in the core decoders.

## Tests

Decoder tests run against a local capture fixture. **Captures are never
committed** — they contain a real device MAC and personal health data. Point the
tests at your own `.pklg`:

```sh
NOISEREP_TEST_PKLG=/path/to/capture.pklg pytest -q
```

Without it, capture-dependent tests skip. GUI tests also skip when Qt is not
installed. CI runs the core on Python 3.10–3.13 plus a GUI job with
`QT_QPA_PLATFORM=offscreen`.

Assert on shape, format, and ranges — **not** on your own real sample values.
Never put a real MAC, serial, or health value in a test, fixture, issue or PR.
Use placeholders such as `aa:bb:cc:dd:ee:ff`, `SN-0000`, and synthetic numbers.

## Working on the protocol

`docs/PROTOCOL.md` is the living reference. If you map one of the currently
unidentified metric ids to a real unit, or decode one of the raw byte blobs,
update that file in the same PR and record the evidence (cadence, value range,
and how you cross-checked it). A guessed label is worse than a neutral
`Channel N`.

## Scope and safety

This tool **reads** data from a band you own. The allowed traffic is the
historical-fetch commands `32`, `112`, `113`, `115`, `117`, `123` plus the
transport start/end acks. Do not add settings writes, alarms, time-sync, file
requests (`4109`), or deletes without a separate opt-in design and hardware
validation. Keep the band's on-device history intact.

The one write path is `noiserep buzz` (`protocol/vibration.py`): experimental,
`--yes`-gated, isolated, and never called by sync/GUI/menu bar. Any new haptics
work must keep those rails.

`gb/` is Gadgetbridge reference source (GPLv3): read it, never modify, build, or
ship it. `noiserep` ships no Gadgetbridge-derived code.

## Packaging

Build a local app bundle (requires PyInstaller):

```sh
pip install -e '.[ble,gui]' pyinstaller
python tools/build_app.py                 # onedir GUI app in dist/
python tools/build_app.py --onefile       # single-file GUI app
python tools/build_app.py --cli           # console CLI, no Qt
```

Tagged `v*` builds run in CI and upload Linux/macOS artifacts. macOS builds are
unsigned; document any first-launch Gatekeeper step in the release notes.

## Pull requests

- Keep changes focused; one concern per PR.
- Run `pytest -q` before opening the PR (and the offscreen GUI test if you
  touched `noiserep/gui/`).
- Describe what you observed on the wire if the change is protocol-related.
- Update `README.md` / `docs/ROADMAP.md` when the user-facing status changes.

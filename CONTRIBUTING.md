# Contributing to noiserep

Thanks for your interest! This project reverse-engineers the Noise REP band, so
protocol findings are as valuable as code.

## Development setup

```sh
python -m venv venv && . venv/bin/activate
pip install -e '.[dev]'
pytest                                   # the suite runs with no hardware
```

The headless core (`noiserep/`, minus any future GUI) must stay **pure standard
library** so it installs anywhere and stays easy to audit. Keep user-facing text
(prints, `sys.exit`) in `noiserep/cli.py`, not in the core decoders.

## Tests

Decoder tests run against a local capture fixture. **Captures are never committed**
— they contain a real device MAC and personal health data. Point the tests at
your own `.pklg` (see the capture instructions in the README) rather than adding
one to the repo. Assert on shape, format, and ranges, not on your own real sample
values.

## Working on the protocol

`docs/PROTOCOL.md` is the living reference. If you map one of the currently
unidentified metric ids to a real unit, or decode one of the raw byte blobs,
please update that file in the same PR.

## Pull requests

- Keep changes focused; one concern per PR.
- Run `pytest` before opening the PR.
- Describe what you observed on the wire if the change is protocol-related.

## Scope and safety

This tool only **reads** data from a band you own. It sends no auth and writes no
settings. Keep it that way: read-only data extraction, no bricking risk.

# Loop checks

`tools/check.py` is the repeatable health check for this repo. It is pure
stdlib, makes no BLE connection, and finishes in a few seconds (well under a
minute). It prints a PASS/FAIL/SKIP table with per-step durations and exits
non-zero only if a step actually failed.

## What it checks

| Step | What happens |
| --- | --- |
| `compileall` | Byte-compiles `noiserep/` and `tests/` (quiet). |
| `pytest` | Runs `python -m pytest -q`. Capture-dependent tests skip when `noisefit.pklg` is absent. |
| `cli help` | Runs top-level `--help`, discovers subcommands, then runs `--help` for each (`decode`, `sync`, `export`, `gui`, and anything new such as `menubar`). |
| `decode offline` | Decodes `noisefit.pklg` (or `$NOISEREP_TEST_PKLG`) and asserts `device:` and `series:` lines appear. Skips when the capture is absent. |
| `ble import` | Imports `noiserep.ble.connection`; never scans or connects. |
| `gui offscreen` | With `QT_QPA_PLATFORM=offscreen`, imports `noiserep.gui.app` and builds `MainWindow` over an in-memory `Store`. Skips if PySide6, pyqtgraph, or qasync are missing. |

SKIP is not a failure: it usually means an optional extra or the capture file
is not present on this machine. Only FAIL makes the script exit 1.

## Manual run

```sh
./venv/bin/python tools/check.py
make check                  # same thing
```

Other Makefile targets:

```sh
make test                   # pytest -q
make gui                    # launch the desktop app
make app                    # install /Applications/OpenNoiseRep.app (macOS)
make app ARGS=--menubar     # ...plus "OpenNoiseRep Menu Bar.app"
make sync ARGS="--since 2026-10-01 --no-files"
make clean                  # remove __pycache__ / .pytest_cache, never data or venv
```

## Foreground watch loop

Runs every 15 minutes until you press Ctrl-C. This is a normal foreground
process; nothing is installed as a daemon and no cron entry is created.

```sh
while true; do
  date
  ./venv/bin/python tools/check.py
  sleep 900
done
```

For cron or CI, the exit code is the whole contract:

```sh
./venv/bin/python tools/check.py >/tmp/noiserep-check.log 2>&1 || echo "check failed"
```

## Scheduled Claude Code prompt

Ask Claude Code to run the check on an interval and surface only problems, for
example:

> Every 30 minutes run `./venv/bin/python tools/check.py` and report only
> failures. If everything passes, reply with one line. Do not start BLE syncs
> or modify files.

A report-only prompt keeps the loop cheap: the table is concise, and Claude only
needs to summarize the FAILURES section. If a failure is caused by an agent
mid-edit, wait for the edit to settle and re-run before investigating.

## Recommended cadence

- **15-30 minutes** while several agents/people are editing `noiserep/` or
  `tests/` concurrently.
- **Hourly** during normal development.
- **Before every commit, merge, or release**, plus once after a dependency
  upgrade (pytest, PySide6, pyqtgraph, qasync, bleak).

Skips do not need action. A change from PASS to SKIP for `decode offline` means
the capture moved or was deleted; a change to SKIP for `gui offscreen` means the
GUI extras are no longer installed in the venv.

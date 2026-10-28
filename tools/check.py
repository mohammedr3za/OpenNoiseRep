#!/usr/bin/env python3
"""Repeatable "loop check" for this repository. Pure stdlib, no BLE connection.

Runs, in order:

  1. compileall over ``noiserep/`` and ``tests/``
  2. ``python -m pytest -q`` (capture-dependent tests skip without a .pklg)
  3. CLI help smoke: top level plus every subcommand discovered in it
  4. offline ``decode`` of noisefit.pklg, asserting device/series output
  5. ``import noiserep.ble.connection`` (import only -- never scans/connects)
  6. headless GUI smoke: build ``MainWindow`` over an in-memory Store

Prints a PASS/FAIL/SKIP table with per-step durations and exits non-zero if
anything failed. Skips are not failures: they mean an optional dependency or
the capture file is absent.

Usage:
    ./venv/bin/python tools/check.py

Watch loop (foreground, Ctrl-C to stop; deliberately does not daemonize):
    while true; do ./venv/bin/python tools/check.py; sleep 900; done

Suggested scheduled Claude Code prompt:
    "Every 30 minutes run ./venv/bin/python tools/check.py and report only
     failures."

See tools/README.md for cadence guidance and more examples.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Subcommands the CLI is expected to expose. Extra ones (for example a future
# `menubar`) are discovered from --help and checked automatically.
EXPECTED_COMMANDS = ("decode", "sync", "export", "gui")

GUI_SNIPPET = r"""
import importlib.util
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
missing = [
    name for name in ("PySide6", "pyqtgraph", "qasync")
    if importlib.util.find_spec(name) is None
]
if missing:
    print("SKIP: missing " + ", ".join(missing))
    raise SystemExit(0)

from PySide6 import QtWidgets
from noiserep.store import Store
from noiserep.gui.app import MainWindow

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
store = Store(":memory:")
window = MainWindow(store)
print("OK: MainWindow built over in-memory Store")
window.close()
store.close()
app.quit()
"""


@dataclass
class Step:
    name: str
    status: str          # PASS / FAIL / SKIP
    seconds: float
    detail: str = ""
    output: str = ""


def _python() -> str:
    """Prefer the project venv so the check behaves the same from any shell."""
    for candidate in (ROOT / "venv" / "bin" / "python",
                      ROOT / "venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _last_line(text: str) -> str:
    for line in reversed((text or "").strip().splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _short(text: str, limit: int = 120) -> str:
    line = _last_line(text)
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


def _run(name: str, args: list[str], timeout: float, *, env=None,
         ok_detail: str = "") -> Step:
    """Run a subprocess and turn its result into a Step."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return Step(name, "FAIL", time.perf_counter() - start,
                    f"timed out after {timeout:g}s")
    except OSError as exc:
        return Step(name, "FAIL", time.perf_counter() - start, str(exc))

    elapsed = time.perf_counter() - start
    out = proc.stdout or ""
    err = proc.stderr or ""
    combined = out + ("\n" if out and err else "") + err
    if proc.returncode != 0:
        return Step(name, "FAIL", elapsed,
                    _short(err) or _short(out) or f"exit {proc.returncode}",
                    combined)
    if ok_detail == "__last_line__":
        ok_detail = _last_line(out)
    return Step(name, "PASS", elapsed, ok_detail, combined)


def check_compile(py: str) -> Step:
    return _run(
        "compileall",
        [py, "-m", "compileall", "-q", "noiserep", "tests"],
        timeout=45,
        ok_detail="noiserep/ + tests/",
    )


def check_pytest(py: str) -> Step:
    return _run(
        "pytest",
        [py, "-m", "pytest", "-q"],
        timeout=120,
        ok_detail="__last_line__",
    )


def _cli_call(py: str, args: list[str], timeout: float = 30):
    """Run one CLI help command; return (CompletedProcess, error)."""
    try:
        return subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        ), None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g}s"
    except OSError as exc:
        return None, str(exc)


def check_cli_help(py: str) -> Step:
    """Top-level help, then --help for every subcommand it advertises."""
    start = time.perf_counter()
    outputs: list[str] = []

    proc, error = _cli_call(py, [py, "-m", "noiserep.cli", "--help"])
    if proc is None:
        return Step("cli help", "FAIL", time.perf_counter() - start, error)
    outputs.append(proc.stdout or "")
    outputs.append(proc.stderr or "")
    if proc.returncode != 0:
        return Step("cli help", "FAIL", time.perf_counter() - start,
                    _short(proc.stderr) or f"exit {proc.returncode}",
                    "\n".join(outputs))

    match = re.search(r"\{([^{}]*)\}", proc.stdout or "")
    discovered = [c.strip() for c in match.group(1).split(",")] if match else []
    discovered = [c for c in discovered if c and not c.startswith("-")]
    if not discovered:
        discovered = list(EXPECTED_COMMANDS)

    missing = [c for c in EXPECTED_COMMANDS if c not in discovered]
    ordered = [c for c in EXPECTED_COMMANDS if c in discovered]
    ordered += sorted(c for c in discovered if c not in EXPECTED_COMMANDS)

    failed: list[str] = []
    for cmd in ordered:
        cp, error = _cli_call(py, [py, "-m", "noiserep.cli", cmd, "--help"])
        if cp is None:
            failed.append(f"{cmd}: {error}")
            continue
        outputs.append(cp.stdout or "")
        outputs.append(cp.stderr or "")
        if cp.returncode != 0:
            failed.append(f"{cmd}: {_short(cp.stderr) or f'exit {cp.returncode}'}")

    elapsed = time.perf_counter() - start
    combined = "\n".join(outputs)
    if missing or failed:
        parts = []
        if missing:
            parts.append("missing subcommands: " + ", ".join(missing))
        if failed:
            parts.append("; ".join(failed))
        return Step("cli help", "FAIL", elapsed, _short(" | ".join(parts)), combined)
    return Step("cli help", "PASS", elapsed,
                f"checked {len(ordered)}: {', '.join(ordered)}", combined)


def check_decode(py: str) -> Step:
    env_path = os.environ.get("NOISEREP_TEST_PKLG")
    capture = Path(env_path).expanduser() if env_path else ROOT / "noisefit.pklg"
    if not capture.exists():
        return Step("decode offline", "SKIP", 0.0,
                    f"capture not found at {capture}")

    step = _run(
        "decode offline",
        [py, "-m", "noiserep.cli", "decode", str(capture)],
        timeout=60,
    )
    if step.status != "PASS":
        return step

    out = step.output
    has_device = re.search(r"(?m)^device:", out) is not None
    series = len(re.findall(r"(?m)^series:", out))
    if not has_device or not series:
        missing = []
        if not has_device:
            missing.append("device: line")
        if not series:
            missing.append("series: line")
        step.status = "FAIL"
        step.detail = "missing " + " and ".join(missing)
        return step
    step.detail = f"device line + {series} series lines"
    return step


def check_ble_import(py: str) -> Step:
    return _run(
        "ble import",
        [py, "-c", "import noiserep.ble.connection"],
        timeout=30,
        ok_detail="noiserep.ble.connection (import only)",
    )


def check_gui(py: str) -> Step:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    step = _run("gui offscreen", [py, "-c", GUI_SNIPPET], timeout=60, env=env)
    if step.status != "PASS":
        return step

    marker = next((ln.strip() for ln in step.output.splitlines()
                   if ln.strip().startswith(("SKIP:", "OK:"))), "")
    if marker.startswith("SKIP:"):
        step.status = "SKIP"
        step.detail = marker[len("SKIP:"):].strip()
    elif marker.startswith("OK:"):
        step.detail = marker[len("OK:"):].strip()
    else:
        step.status = "FAIL"
        step.detail = "smoke script produced no OK/SKIP marker"
    return step


def print_table(steps: list[Step], py: str, wall: float) -> None:
    print(f"NoiseREP loop check  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"root:   {ROOT}")
    print(f"python: {py}")
    print()

    width = max([len(s.name) for s in steps] + [len("TOTAL")])
    print(f"{'STEP':<{width}}  {'STATUS':<6}  {'TIME':>7}  DETAIL")
    for step in steps:
        print(f"{step.name:<{width}}  {step.status:<6}  "
              f"{step.seconds:>6.2f}s  {step.detail}")

    failed = sum(s.status == "FAIL" for s in steps)
    skipped = sum(s.status == "SKIP" for s in steps)
    passed = sum(s.status == "PASS" for s in steps)
    total_status = "FAIL" if failed else "PASS"
    print("-" * (width + 24))
    print(f"{'TOTAL':<{width}}  {total_status:<6}  {wall:>6.2f}s  "
          f"{passed} passed, {skipped} skipped, {failed} failed")

    if failed:
        print("\nFAILURES")
        for step in steps:
            if step.status != "FAIL":
                continue
            print(f"--- {step.name} ({step.seconds:.2f}s) ---")
            lines = [ln for ln in step.output.strip().splitlines() if ln.strip()]
            for line in lines[-20:]:
                print(line)
            if not lines:
                print(step.detail)


def main() -> int:
    py = _python()
    start = time.perf_counter()
    steps = [
        check_compile(py),
        check_pytest(py),
        check_cli_help(py),
        check_decode(py),
        check_ble_import(py),
        check_gui(py),
    ]
    wall = time.perf_counter() - start
    print_table(steps, py, wall)
    return 1 if any(s.status == "FAIL" for s in steps) else 0


if __name__ == "__main__":
    raise SystemExit(main())

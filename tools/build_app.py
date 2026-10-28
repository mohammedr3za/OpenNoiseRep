#!/usr/bin/env python3
"""Build a standalone noiserep bundle with PyInstaller.

Quick start::

    python -m pip install -e '.[ble,gui]' pyinstaller
    python tools/build_app.py                # GUI app, onedir bundle
    python tools/build_app.py --onefile      # GUI app, single file
    python tools/build_app.py --cli          # console CLI (no Qt)

Outputs land in ``dist/``. On macOS the GUI build is ``dist/noiserep.app`` (a
bundled .app); elsewhere it is ``dist/noiserep/noiserep``. PyInstaller cannot
code-sign or notarize the macOS app here, so macOS artifacts are **unsigned**.
This script only packages local code: it never connects to, reads, or writes the
band.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_GUI_LAUNCHER = """\
from noiserep.gui.app import main

raise SystemExit(main())
"""

_CLI_LAUNCHER = """\
from noiserep.cli import main

raise SystemExit(main())
"""

# Imports that happen lazily inside functions, so PyInstaller's static analysis
# does not see them. Keep this list in sync with cli.py / gui/app.py.
_LAZY_IMPORTS = [
    "noiserep.sync",
    "noiserep.store",
    "noiserep.export",
    "noiserep.ble.connection",
]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--name", default="noiserep",
                   help="bundle name (default: noiserep)")
    p.add_argument("--onefile", action="store_true",
                   help="build one executable instead of a directory")
    p.add_argument("--cli", action="store_true",
                   help="bundle the console CLI instead of the GUI (no Qt)")
    p.add_argument("--icon", default=None,
                   help="optional .icns/.ico/.png icon path")
    p.add_argument("--clean", action="store_true",
                   help="remove PyInstaller caches before building")
    return p


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def _add_data(source: pathlib.Path, dest: str) -> str:
    # PyInstaller uses ';' on Windows and ':' elsewhere.
    return f"{source}{os.pathsep}{dest}"


def build(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if not _module_available("PyInstaller"):
        print(
            "PyInstaller is not installed. Install the build dependencies first:\n"
            "  python -m pip install -e '.[ble,gui]' pyinstaller",
            file=sys.stderr,
        )
        return 2
    if pathlib.Path(args.name).name != args.name:
        print("--name must be a bare file name (no path separators)", file=sys.stderr)
        return 2

    icon: pathlib.Path | None = None
    if args.icon:
        icon = pathlib.Path(args.icon).expanduser().resolve()
        if not icon.is_file():
            print(f"icon not found: {icon}", file=sys.stderr)
            return 2

    (REPO_ROOT / "build").mkdir(exist_ok=True)
    (REPO_ROOT / "dist").mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="noiserep-pyinstaller-") as tmp:
        launcher = pathlib.Path(tmp) / "launcher.py"
        launcher.write_text(_CLI_LAUNCHER if args.cli else _GUI_LAUNCHER,
                            encoding="utf-8")

        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--onefile" if args.onefile else "--onedir",
            "--name", args.name,
            "--paths", str(REPO_ROOT),
            "--distpath", str(REPO_ROOT / "dist"),
            "--workpath", str(REPO_ROOT / "build" / "pyinstaller"),
            "--specpath", str(REPO_ROOT / "build"),
        ]
        for module in _LAZY_IMPORTS:
            cmd += ["--hidden-import", module]

        if args.cli:
            cmd.append("--console")
        else:
            cmd += [
                "--windowed",
                # Both packages select backends/plugins at import time.
                "--collect-submodules", "pyqtgraph",
                "--collect-submodules", "qasync",
                "--hidden-import", "noiserep.gui.app",
            ]
            if sys.platform == "darwin":
                # bleak's CoreBluetooth backend imports these dynamically.
                for module in ("objc", "Foundation", "CoreBluetooth"):
                    cmd += ["--hidden-import", module]

        # bleak is optional for the CLI, but required for a live sync. Collect
        # its backends when installed so a frozen app can scan on macOS/Linux.
        if _module_available("bleak"):
            cmd += ["--collect-submodules", "bleak"]

        if args.clean:
            cmd.append("--clean")
        if icon is not None:
            cmd += ["--icon", str(icon)]

        # Ship the licence and user-facing readme beside the executables.
        for name in ("LICENSE", "README.md"):
            path = REPO_ROOT / name
            if path.is_file():
                cmd += ["--add-data", _add_data(path, ".")]

        cmd.append(str(launcher))
        print("+", " ".join(cmd), flush=True)
        try:
            result = subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            return 130

    if result.returncode == 0:
        if sys.platform == "darwin" and not args.cli:
            print(f"Built {REPO_ROOT / 'dist' / (args.name + '.app')} (unsigned)")
        else:
            print(f"Built {REPO_ROOT / 'dist' / args.name}")
    return result.returncode


def main() -> int:
    return build()


if __name__ == "__main__":
    raise SystemExit(main())

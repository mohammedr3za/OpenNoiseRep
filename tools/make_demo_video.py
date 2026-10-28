#!/usr/bin/env python3
"""Render a short Reddit-friendly demo video/GIF from synthetic data.

    ./venv/bin/python tools/make_demo_video.py

Outputs ``docs/media/OpenNoiseRep-demo.mp4`` and ``.gif``.  Requires ffmpeg.
The store is the same synthetic demo used for the README screenshots, so no
personal health data is involved.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from make_readme_images import _synthetic_store  # noqa: E402
from noiserep.gui import theme  # noqa: E402
from noiserep.gui.app import MainWindow  # noqa: E402

WIDTH, HEIGHT, FPS = 1180, 760, 15
MEDIA = ROOT / "docs" / "media"


def _caption(pix: QtGui.QPixmap, text: str) -> QtGui.QPixmap:
    out = QtGui.QPixmap(pix)
    p = QtGui.QPainter(out)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    bar = QtCore.QRectF(0, HEIGHT - 54, WIDTH, 54)
    p.fillRect(bar, QtGui.QColor(20, 18, 16, 205))
    font = QtGui.QFont("Helvetica Neue")
    font.setPixelSize(19)
    font.setWeight(QtGui.QFont.DemiBold)
    p.setFont(font)
    p.setPen(QtGui.QColor("#FAFAF7"))
    p.drawText(bar.adjusted(26, 0, -26, 0),
               QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
    p.end()
    return out


def _menu_frame() -> QtGui.QPixmap:
    pix = QtGui.QPixmap(WIDTH, HEIGHT)
    pix.fill(QtGui.QColor(theme.BG))
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    title_font = QtGui.QFont("Helvetica Neue")
    title_font.setPixelSize(30)
    title_font.setWeight(QtGui.QFont.Bold)
    p.setFont(title_font)
    p.setPen(QtGui.QColor(theme.FG))
    p.drawText(QtCore.QRectF(0, 170, WIDTH, 50),
               QtCore.Qt.AlignCenter, "Live heart rate, no window")
    strip = QtGui.QPixmap(str(ROOT / "docs" / "images" / "menubar.png"))
    if not strip.isNull():
        target = QtCore.QRectF(140, 300, WIDTH - 280, 245)
        p.drawPixmap(target, strip, QtCore.QRectF(strip.rect()))
    p.end()
    return pix


def _frames(window: MainWindow, store) -> list[tuple[QtGui.QPixmap, str, float]]:
    frames: list[tuple[QtGui.QPixmap, str, float]] = []

    window._show_overview()
    window.resize(WIDTH, HEIGHT)
    window.range_combo.setCurrentIndex(0)          # Today
    window._load()
    frames.append((window.grab(), "Sync once. See your day.", 2.6))

    scroll = window.stack.widget(0)
    scroll.verticalScrollBar().setValue(560)
    frames.append((window.grab(),
                   "Vitals: HRV, resting HR, SpO2, temp, stress", 2.8))
    scroll.verticalScrollBar().setValue(0)

    window._open_vital_detail("hrv")
    frames.append((window.grab(),
                   "Tap a vital for meaning and usual range", 2.8))

    window._show_overview()
    session = store.sleep_sessions()[-1]
    window._open_sleep_detail(session)
    frames.append((window.grab(),
                   "Last night: HR, HRV and coverage", 2.8))

    window._show_overview()
    window.range_combo.setCurrentIndex(1)          # Day…
    frames.append((window.grab(),
                   "Any day: calendar and previous/next", 2.8))

    window._show_guide()
    frames.append((window.grab(), "The menu-bar icon, explained", 2.6))

    window._show_overview()
    frames.append((_menu_frame(), "Menu-bar heart rate", 2.6))
    return frames


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is required", file=sys.stderr)
        return 1
    MEDIA.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(theme.QSS)
    store = _synthetic_store()
    window = MainWindow(store, band_name=None)
    window.show()

    frames = _frames(window, store)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="noiserep-video-"))
    index = 0
    for pix, caption, seconds in frames:
        frame = _caption(pix, caption)
        count = max(1, int(round(seconds * FPS)))
        for _ in range(count):
            frame.save(str(tmp / f"f{index:05d}.png"))
            index += 1

    mp4 = MEDIA / "OpenNoiseRep-demo.mp4"
    gif = MEDIA / "OpenNoiseRep-demo.gif"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-i", str(tmp / "f%05d.png"), "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-i", str(tmp / "f%05d.png"), "-vf",
        "fps=12,scale=900:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        str(gif),
    ], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    window.close()
    store.close()
    for path in (mp4, gif):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the README screenshots from synthetic data (no personal health data).

Run with the project venv:

    ./venv/bin/python tools/make_readme_images.py

Writes PNGs under ``docs/images/``: ``overview.png``, ``channels.png``,
``sleep.png`` and ``menubar.png``.  Everything is deterministic and fake; the
device is always ``SN-TEST`` with a placeholder MAC.
"""
from __future__ import annotations

import datetime
import math
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from noiserep.gui import theme  # noqa: E402
from noiserep.gui.app import MainWindow  # noqa: E402
from noiserep.gui.menubar import make_icon  # noqa: E402
from noiserep.protocol.decode import DeviceInfo, MetricSeries  # noqa: E402
from noiserep.protocol.files import RriFile, RriSample  # noqa: E402
from noiserep.store import Store  # noqa: E402
from noiserep.sync import SyncResult  # noqa: E402

OUT = ROOT / "docs" / "images"
BASE = datetime.datetime(2026, 10, 28, 0, 0, 0)


def _synthetic_store() -> Store:
    r = SyncResult()
    r.device = DeviceInfo("1.4.0", "SN-TEST", "aa:bb:cc:dd:ee:ff", "R1")

    heart = [0] * 2880                       # 30 s samples, one day
    for i in range(360, 1321):               # 06:00-22:00
        heart[i] = int(66 + 12 * math.sin(i / 170) + (i % 7) - 3)

    hrv = [0] * 96                           # 15 min
    for i in range(96):
        if 8 <= i <= 32:                     # overnight
            hrv[i] = int(72 + 8 * math.sin(i / 4))
        elif i > 40:
            hrv[i] = int(52 + 6 * math.sin(i / 5))

    stress = [0] * 288                       # 5 min
    for i in range(288):
        if 8 <= i <= 96:
            stress[i] = int(18 + 6 * math.sin(i / 9))
        elif i > 100:
            stress[i] = int(34 + 10 * math.sin(i / 11))

    steps = [0] * 24                         # 1 h
    for hour in (7, 8, 9, 12, 13, 18, 19, 20):
        steps[hour] = 260 + hour * 23
    distance = [int(value * 0.714) for value in steps]
    calories = [int(value * 0.16) for value in steps]
    active = [1 if value else 0 for value in steps]

    imports = [0] * 24                       # 1 h (metric 11: unknown flag)
    imports[8] = 1

    spo2 = [0] * 288                         # 5 min; sparse real readings
    for i in (40, 90, 140, 190, 240):
        spo2[i] = 96 + i % 4

    temp = [0] * 24                          # 1 h, candidate skin temperature
    for hour in range(6, 23):
        temp[hour] = 30 + hour % 3

    r.series = [
        MetricSeries(BASE, 2, 30, heart, b""),
        MetricSeries(BASE, 21, 900, hrv, b""),
        MetricSeries(BASE, 22, 300, stress, b""),
        MetricSeries(BASE, 4, 300, spo2, b""),
        MetricSeries(BASE, 23, 3600, temp, b""),
        MetricSeries(
            BASE, 0, 3600, steps, b"",
            channels={
                "steps": steps,
                "distance_m": distance,
                "calories": calories,
                "active_minutes": active,
            },
            totals={
                "steps": sum(steps),
                "distance_m": sum(distance),
                "calories": sum(calories),
                "active_minutes": sum(active),
            },
        ),
        MetricSeries(BASE, 11, 3600, imports, b""),
    ]

    # A synthetic overnight RRI window: 02:00-08:00, one beat per second.
    start = BASE + datetime.timedelta(hours=2)
    samples = []
    for i in range(6 * 3600):
        rr = 900 + int(90 * math.sin(i / 420)) + (i * 37 % 41) - 20
        samples.append(RriSample(i, rr, start + datetime.timedelta(seconds=i)))
    r.rri = [RriFile(start=start, samples=samples, total_slots=6 * 3600)]

    store = Store(":memory:")
    store.ingest(r)
    return store


def _menubar_mock() -> None:
    width, height = 1200, 250
    pix = QtGui.QPixmap(width, height)
    pix.fill(QtGui.QColor(theme.BG))
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing)

    strip = QtCore.QRectF(30, 28, width - 60, 64)
    p.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER), 1))
    p.setBrush(QtGui.QColor("#F4F4F1"))
    p.drawRoundedRect(strip, 12, 12)

    font = QtGui.QFont("Helvetica Neue")
    font.setPixelSize(26)
    font.setWeight(QtGui.QFont.Bold)
    p.setFont(font)
    p.setPen(QtGui.QColor(theme.FG))
    p.drawText(QtCore.QRectF(52, 28, 360, 64),
               QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, "NoiseREP")

    states = [
        (72, False, "worn · live"),
        (None, True, "removed · ~"),
        (None, False, "not connected · —"),
    ]
    x = width - 640
    for bpm, removed, caption in states:
        icon = make_icon(bpm is not None, bpm, removed)
        icon.paint(p, QtCore.QRect(x, 48, 150, 30))
        small = QtGui.QFont("Helvetica Neue")
        small.setPixelSize(15)
        p.setFont(small)
        p.setPen(QtGui.QColor(theme.FG_MUTED))
        p.drawText(QtCore.QRectF(x - 10, 96, 220, 24),
                   QtCore.Qt.AlignCenter, caption)
        x += 205

    p.end()
    pix.save(str(OUT / "menubar.png"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(theme.QSS)

    store = _synthetic_store()
    window = MainWindow(store, band_name=None)
    window.resize(1180, 1500)
    window.show()
    app.processEvents()
    window.grab().save(str(OUT / "overview.png"))

    window.range_combo.setCurrentIndex(3)    # All data
    app.processEvents()
    window.grab().save(str(OUT / "channels.png"))

    session = store.sleep_sessions()[-1]
    window._open_sleep_detail(session)
    window.resize(1180, 1150)
    app.processEvents()
    window.grab().save(str(OUT / "sleep.png"))
    window.close()

    _menubar_mock()

    for name in ("overview.png", "channels.png", "sleep.png", "menubar.png"):
        path = OUT / name
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

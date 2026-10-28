"""A thin glowing sync progress bar (NoiseFit style).

Custom-painted, one looping ``QVariantAnimation`` for the shimmer sweep, no
graphics effects.  Feed it ``progress(done, total, stage)`` (the callback
``noiserep.sync.run_sync`` takes); ``total == 0`` means indeterminate.  The
shown value eases towards the target and never moves backwards; ``finish``
completes it and fades the bar out over ``FADE_MS``.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

FADE_MS = 600
HEIGHT = 6
EASE = 0.12            # fraction of the remaining distance covered per frame
CAP = 0.97             # determinate progress stops here until "done"


class SyncBar(QtWidgets.QWidget):
    percent = QtCore.Signal(int)
    activeChanged = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(HEIGHT)
        self.target = self.shown = 0.0
        self.opacity = 0.0
        self.phase = 0.0
        self.indeterminate = False
        self.active = False
        self._finishing = False
        self._fade_t0: int | None = None
        self._clock = QtCore.QElapsedTimer()
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1300)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self._pct = -1

    # feeding -----------------------------------------------------------------
    def progress(self, done: int, total: int, stage: str = "") -> None:
        if stage == "done":
            self.finish()
            return
        if not self.active or self._finishing:
            self.begin()
        if total > 0:
            self.indeterminate = False
            self.target = max(self.target, min(done / total, 1.0) * CAP)
        else:
            self.indeterminate = True

    def begin(self) -> None:
        self.target = self.shown = 0.0
        self.opacity = 1.0
        self.indeterminate = True
        self._finishing = False
        self._fade_t0 = None
        self._clock.start()
        self._set_active(True)
        self._anim.start()

    def finish(self, ok: bool = True) -> None:
        """Complete (``ok``) or abort the bar, then fade it out."""
        if not self.active or self._finishing:
            return
        self._finishing = True
        self.indeterminate = False
        if ok:
            self.target = 1.0
        else:
            self._fade_t0 = self._clock.elapsed()

    # animation ---------------------------------------------------------------
    def _tick(self, phase) -> None:
        self.phase = float(phase)
        gap = self.target - self.shown
        self.shown = self.target if gap < 0.002 else self.shown + gap * EASE
        if self._finishing and self._fade_t0 is None and self.shown >= 0.999:
            self._fade_t0 = self._clock.elapsed()
        if self._fade_t0 is not None:
            self.opacity = 1 - (self._clock.elapsed() - self._fade_t0) / FADE_MS
            if self.opacity <= 0:
                self.opacity = 0.0
                self._anim.stop()
                self._set_active(False)
        pct = int(self.shown * 100 + 0.5)
        if pct != self._pct:
            self._pct = pct
            self.percent.emit(pct)
        self.update()

    def _set_active(self, on: bool) -> None:
        if on != self.active:
            self.active = on
            self.activeChanged.emit(on)

    # painting ----------------------------------------------------------------
    def paintEvent(self, _):  # noqa: N802 - Qt API
        if self.opacity <= 0:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        w, h = self.width(), self.height()
        accent = QtGui.QColor(theme.ACCENT)
        track = QtGui.QColor(accent)
        track.setAlphaF(0.12)
        p.fillRect(QtCore.QRectF(0, h / 2 - 1, w, 2), track)
        fill_w = max(w * self.shown, 0.0)
        if fill_w < 1:
            return
        glow = QtGui.QColor(accent)
        glow.setAlphaF(0.22)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(glow)
        p.drawRoundedRect(QtCore.QRectF(0, 0, fill_w, h), h / 2, h / 2)
        core = QtGui.QLinearGradient(0, 0, fill_w, 0)
        core.setColorAt(0, accent)
        core.setColorAt(1, accent.lighter(130))
        p.setBrush(core)
        p.drawRoundedRect(QtCore.QRectF(0, h / 2 - 1.5, fill_w, 3), 1.5, 1.5)
        # shimmer: a soft highlight sweeping left to right across the fill
        band = 140.0
        x = self.phase * (fill_w + band) - band
        sheen = QtGui.QLinearGradient(x, 0, x + band, 0)
        white = QtGui.QColor(255, 255, 255, 0)
        sheen.setColorAt(0, white)
        sheen.setColorAt(0.5, QtGui.QColor(255, 255, 255, 190 if self.indeterminate else 120))
        sheen.setColorAt(1, white)
        p.setClipRect(QtCore.QRectF(0, 0, fill_w, h))
        p.setBrush(sheen)
        p.drawRoundedRect(QtCore.QRectF(0, h / 2 - 1.5, fill_w, 3), 1.5, 1.5)

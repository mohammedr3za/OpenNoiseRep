"""Vector icon buttons (sun, moon, chevrons) drawn with QPainter.

Thin rounded strokes in the style of SF Symbols, so they stay crisp at any
DPI and follow the theme colour instead of font glyphs.
"""
from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


def draw_icon(p: QtGui.QPainter, name: str, rect: QtCore.QRectF, color: QtGui.QColor) -> None:
    c = rect.center()
    s = min(rect.width(), rect.height())
    pen = QtGui.QPen(color, max(1.6, s * 0.085))
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    if name == "sun":
        r = s * 0.2
        p.drawEllipse(c, r, r)
        for i in range(8):
            a = math.radians(i * 45)
            inner, outer = s * 0.32, s * 0.44
            p.drawLine(QtCore.QPointF(c.x() + inner * math.cos(a), c.y() + inner * math.sin(a)),
                       QtCore.QPointF(c.x() + outer * math.cos(a), c.y() + outer * math.sin(a)))
    elif name == "moon":
        r = s * 0.36
        outer = QtGui.QPainterPath()
        outer.addEllipse(c, r, r)
        bite = QtGui.QPainterPath()
        bite.addEllipse(QtCore.QPointF(c.x() + r * 0.62, c.y() - r * 0.48), r * 0.86, r * 0.86)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color)
        p.drawPath(outer.subtracted(bite))
    elif name in ("left", "right"):
        d = s * 0.16 * (1 if name == "left" else -1)
        h = s * 0.26
        path = QtGui.QPainterPath(QtCore.QPointF(c.x() + d, c.y() - h))
        path.lineTo(c.x() - d, c.y())
        path.lineTo(c.x() + d, c.y() + h)
        p.drawPath(path)


class IconButton(QtWidgets.QAbstractButton):
    """A round, borderless button showing one vector icon with a soft hover."""

    def __init__(self, icon: str, size: int = 36, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.setFixedSize(size, size)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._hover = 0.0
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._set_hover)
        self.filled = True

    def set_icon(self, icon: str) -> None:
        self.icon_name = icon
        self.update()

    def _set_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def _fade(self, to: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(to)
        self._anim.start()

    def enterEvent(self, e):  # noqa: N802 - Qt API
        self._fade(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802 - Qt API
        self._fade(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = QtGui.QColor(theme.PANEL_HOVER if self.filled else theme.BG)
        if not self.filled:
            base.setAlphaF(0.0)
        hover = QtGui.QColor(theme.BORDER)
        mix = QtGui.QColor.fromRgbF(
            base.redF() + (hover.redF() - base.redF()) * self._hover,
            base.greenF() + (hover.greenF() - base.greenF()) * self._hover,
            base.blueF() + (hover.blueF() - base.blueF()) * self._hover,
            max(base.alphaF(), self._hover))
        if self.isDown():
            mix = mix.darker(108)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(mix)
        p.drawEllipse(rect)
        color = QtGui.QColor(theme.FG if self.isEnabled() else theme.FG_MUTED)
        inset = rect.width() * 0.27
        draw_icon(p, self.icon_name, rect.adjusted(inset, inset, -inset, -inset), color)

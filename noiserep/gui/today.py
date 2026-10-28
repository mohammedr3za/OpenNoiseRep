"""Today: the Apple-style home page.

Painted widgets (rings, charts, bars) follow one ``progress`` (0..1) driven by
a single short ``QVariantAnimation`` (OutCubic) that only plays when the page
is first filled or the day changes -- never on live ticks.  Heavy static
content (chart axes + series) is cached in pixmaps, so hover and animation
frames are blits.  Data is computed off the UI thread and cached per day.
The content column is centred between ``theme.gutter`` side gutters and the
grid is 12 columns, reflowing to 6 and then 1.
"""
from __future__ import annotations

import bisect
import datetime as dt
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .. import analytics, scores
from ..protocol.files import STAGE_AWAKE, STAGE_DEEP, STAGE_LIGHT, STAGE_REM
from . import theme

FAMILIES = ["SF Pro Display", "Segoe UI Variable Display", "Segoe UI",
            "Helvetica Neue"]
STAGE_ROWS = ((STAGE_AWAKE, "Awake", "#FF9500"), (STAGE_REM, "REM", "#5AC8FA"),
              (STAGE_LIGHT, "Core", "#007AFF"), (STAGE_DEEP, "Deep", "#5856D6"))
ZONE_COLORS = ("#5AC8FA", "#34C759", "#FFCC00", "#FF9500", "#FF2D55")


def font(px: int, weight=QtGui.QFont.Weight.Normal) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


WEIGHTS = {QtGui.QFont.Weight.Normal: 400, QtGui.QFont.Weight.DemiBold: 600,
           QtGui.QFont.Weight.Bold: 700}


def style(label: QtWidgets.QLabel, px: int, weight=QtGui.QFont.Weight.Normal,
          color: str | None = None) -> QtWidgets.QLabel:
    """Size a label via its own stylesheet; the app QSS sets QLabel font-size,
    which would otherwise override setFont."""
    label.setStyleSheet(f"font-size: {px}px; font-weight: {WEIGHTS[weight]};"
                        f" color: {color or theme.FG}; background: transparent;")
    return label


def qcolor(hex_: str, alpha: float = 1.0) -> QtGui.QColor:
    c = QtGui.QColor(hex_)
    c.setAlphaF(alpha)
    return c


def recovery_color(value) -> str:
    if value is None:
        return theme.FG_MUTED
    if value >= 67:
        return theme.RECOVERY_GOOD
    return theme.RECOVERY_MID if value >= 34 else theme.RECOVERY_LOW


# -- data ----------------------------------------------------------------------

def today_data(store, day: dt.date, age: int | None = None) -> dict:
    """Everything the page draws for ``day`` (plain data, no Qt)."""
    start = dt.datetime.combine(day, dt.time())
    end = start + dt.timedelta(days=1) - dt.timedelta(seconds=1)
    s = scores.day_scores(store, day, age)
    stages = []
    if s.sleep and s.sleep.get("id"):
        stages = [(r["ts"], r["stage"]) for r in store.sleep_stages(s.sleep["id"])]
    window = s.sleep_window or (start, end)
    vitals = analytics.vitals(store, *window)
    day_vitals = analytics.vitals(store, start, end)
    for key in ("spo2", "stress", "temperature", "resp_rate"):
        if not vitals[key].available:
            vitals[key] = day_vitals[key]
    if s.hrv_ms is not None:
        vitals["hrv"] = analytics.Vital("hrv", "HRV", s.hrv_ms, "ms", "nightly", "verified")

    def series(metric):
        return [(t, v) for t, v in store.samples(metric, start, end) if v]

    def hourly(metric):
        out = [0.0] * 24
        for t, v in store.samples(metric, start, end):
            out[t.hour] += v
        return out

    hr = series("heart_rate")
    live_hr = series("live_hr")
    if hr and live_hr:
        live_hr = [p for p in live_hr if p[0] > hr[-1][0]]
    return {
        "day": day, "start": start, "end": end, "scores": s, "vitals": vitals,
        "stages": stages, "hr": sorted(hr + live_hr),
        "stress": series("stress"), "body_battery": series("body_battery"),
        "steps": hourly("steps"),
        "totals": {m: sum(hourly(m)) for m in
                   ("steps", "distance_m", "active_calories", "calories",
                    "active_minutes")},
    }




# -- painted widgets -----------------------------------------------------------

def _clamp(x: float) -> float:
    return min(max(x, 0.0), 1.0)


class Animated(QtWidgets.QWidget):
    """A widget whose painting follows ``progress`` (0..1), set by the page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress = 1.0

    def set_progress(self, value: float) -> None:
        if value == self.progress:
            return
        self.progress = value
        if not self.visibleRegion().isEmpty():
            self.update()


class Ring(Animated):
    """An activity ring: track, arc, value in the middle."""

    def __init__(self, color: str, size: int = 76, parent=None):
        super().__init__(parent)
        self.color = color
        self.value: float | None = None
        self.maximum = 100.0
        self.setFixedSize(size, size)

    def set(self, value, color: str | None = None) -> None:
        self.value = value
        if color:
            self.color = color
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        side = min(self.width(), self.height())
        thick = max(7.0, side * 0.12)
        rect = QtCore.QRectF(thick / 2, thick / 2, side - thick, side - thick)
        pen = QtGui.QPen(qcolor(self.color, 0.16), thick)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawEllipse(rect)
        frac = 0.0 if self.value is None else min(self.value / self.maximum, 1.0)
        if frac > 0:
            pen.setColor(qcolor(self.color))
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, int(-360 * 16 * frac * self.progress))
        p.setPen(qcolor(theme.FG if self.value is not None else theme.FG_MUTED))
        p.setFont(font(int(side * 0.30), QtGui.QFont.Weight.DemiBold))
        text = "—" if self.value is None else f"{round(self.value * self.progress)}"
        p.drawText(rect, QtCore.Qt.AlignCenter, text)


class RingCell(QtWidgets.QWidget):
    """Small ring with a coloured name and a short caption beside it."""

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        self.ring = Ring(color)
        h.addWidget(self.ring)
        v = QtWidgets.QVBoxLayout()
        v.setSpacing(1)
        self.name = style(QtWidgets.QLabel(title), 14, QtGui.QFont.Weight.DemiBold, color)
        self.caption = style(QtWidgets.QLabel(""), 12, color=theme.FG_MUTED)
        self.caption.setWordWrap(True)
        v.addStretch(1)
        v.addWidget(self.name)
        v.addWidget(self.caption)
        v.addStretch(1)
        h.addLayout(v, 1)

    def set(self, value, caption: str, color: str | None = None) -> None:
        self.ring.set(value, color)
        self.caption.setText(caption)
        if color:
            style(self.name, 14, QtGui.QFont.Weight.DemiBold, color)


class AreaChart(Animated):
    """A line + gradient area over one day with a hover readout.

    Axes and series are rendered once per (size, data, theme) into pixmaps;
    paint is two blits plus the small hover overlay.
    """

    def __init__(self, color: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.color, self.unit = color, unit
        self.points: list[tuple[dt.datetime, float]] = []
        self.x0 = self.x1 = None
        self.y_range: tuple[float, float] | None = None
        self.gap_s = 900
        self._ts: list[float] = []
        self._hover: int | None = None
        self._cache: tuple | None = None   # (key, axes pixmap, series pixmap, scale)
        self.setMouseTracking(True)
        self.setMinimumHeight(190)

    def set(self, points, x0, x1, y_range=None) -> None:
        self.points, self.x0, self.x1, self.y_range = list(points), x0, x1, y_range
        self._ts = [t.timestamp() for t, _ in self.points]
        self._hover = None
        self._cache = None
        self.update()

    def append(self, t: dt.datetime, v: float) -> None:
        self.points.append((t, v))
        self._ts.append(t.timestamp())
        self._cache = None
        self.update()

    def _area(self) -> QtCore.QRectF:
        left, right, top, bottom = 40, 8, 10, 24
        return QtCore.QRectF(left, top, self.width() - left - right,
                             self.height() - top - bottom)

    def _build(self):
        """Render axes + series into pixmaps for the current size and data."""
        dpr = self.devicePixelRatioF()
        area = self._area()
        w, h = self.width(), self.height()
        axes = QtGui.QPixmap(int(w * dpr), int(h * dpr))
        series = QtGui.QPixmap(int(w * dpr), int(h * dpr))
        for pm in (axes, series):
            pm.setDevicePixelRatio(dpr)
            pm.fill(QtCore.Qt.transparent)
        pa = QtGui.QPainter(axes)
        pa.setRenderHint(QtGui.QPainter.Antialiasing)
        self._axes(pa, area)
        if len(self.points) < 2 or self.x0 is None:
            pa.setPen(qcolor(theme.FG_MUTED))
            pa.setFont(font(13))
            pa.drawText(area, QtCore.Qt.AlignCenter, "No readings yet")
            pa.end()
            return axes, series, None
        lo, hi = self.y_range or (min(v for _, v in self.points),
                                  max(v for _, v in self.points))
        if self.y_range is None:
            pad = max((hi - lo) * 0.12, 1)
            lo, hi = lo - pad, hi + pad
        span = (self.x1 - self.x0).total_seconds() or 1
        scale = (lo, hi, span, self.x0.timestamp())
        pa.setFont(font(11))
        pa.setPen(qcolor(theme.FG_MUTED))
        for v in (lo, (lo + hi) / 2, hi):
            y = area.bottom() - area.height() * (v - lo) / (hi - lo)
            pa.drawText(QtCore.QRectF(0, y - 8, 34, 16),
                        QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{v:.0f}")
        pa.end()
        ps = QtGui.QPainter(series)
        ps.setRenderHint(QtGui.QPainter.Antialiasing)
        grad = QtGui.QLinearGradient(0, area.top(), 0, area.bottom())
        grad.setColorAt(0, qcolor(self.color, 0.30))
        grad.setColorAt(1, qcolor(self.color, 0.0))
        pen = QtGui.QPen(qcolor(self.color), 2.0)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        for pts in self._runs(area, scale):
            line = QtGui.QPainterPath()
            line.moveTo(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            fill = QtGui.QPainterPath(line)
            fill.lineTo(pts[-1].x(), area.bottom())
            fill.lineTo(pts[0].x(), area.bottom())
            ps.fillPath(fill, grad)
            ps.setPen(pen)
            ps.drawPath(line)
        ps.end()
        return axes, series, scale

    def _runs(self, area, scale):
        """Pixel-space polylines, one per contiguous run, one point per 1.5 px."""
        lo, hi, span, t0 = scale
        kx = area.width() / span
        ky = area.height() / (hi - lo)
        runs: list[list[QtCore.QPointF]] = []
        cur: list[QtCore.QPointF] = []
        bucket, acc, n, last_t = None, 0.0, 0, None

        def flush():
            if n:
                cur.append(QtCore.QPointF(area.left() + (bucket + 0.5) * 1.5,
                                          area.bottom() - (acc / n - lo) * ky))

        for ts, (_, v) in zip(self._ts, self.points):
            if last_t is not None and ts - last_t > self.gap_s:
                flush()
                acc, n, bucket = 0.0, 0, None
                runs.append(cur)
                cur = []
            last_t = ts
            b = int((ts - t0) * kx / 1.5)
            if b != bucket:
                flush()
                acc, n, bucket = 0.0, 0, b
            acc += v
            n += 1
        flush()
        runs.append(cur)
        return [r for r in runs if len(r) > 1]

    def paintEvent(self, _):  # noqa: N802 - Qt API
        key = (self.size().toTuple(), self.devicePixelRatioF(), theme.mode,
               len(self.points), self.color)
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, *self._build())
        _, axes, series, scale = self._cache
        p = QtGui.QPainter(self)
        p.drawPixmap(0, 0, axes)
        if scale is None:
            return
        area = self._area()
        p.setClipRect(QtCore.QRectF(0, 0, area.left() + area.width() * self.progress,
                                    self.height()))
        p.drawPixmap(0, 0, series)
        p.setClipping(False)
        if self._hover is not None and self.progress >= 1.0:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            self._draw_hover(p, area, scale)

    def _axes(self, p, area) -> None:
        p.setPen(QtGui.QPen(qcolor(theme.FG, 0.07), 1))
        for i in range(5):
            y = area.top() + area.height() * i / 4
            p.drawLine(QtCore.QPointF(area.left(), y), QtCore.QPointF(area.right(), y))
        if self.x0 is None:
            return
        p.setFont(font(11))
        p.setPen(qcolor(theme.FG_MUTED))
        span = (self.x1 - self.x0).total_seconds() or 1
        step = 3 if span > 12 * 3600 else 1
        t = self.x0.replace(minute=0, second=0)
        while t <= self.x1:
            if t >= self.x0 and t.hour % step == 0:
                x = area.left() + area.width() * (t - self.x0).total_seconds() / span
                p.drawText(QtCore.QRectF(x - 24, area.bottom() + 5, 48, 16),
                           QtCore.Qt.AlignCenter, f"{t:%H:%M}")
            t += dt.timedelta(hours=1)

    def _draw_hover(self, p, area, scale) -> None:
        if self._hover >= len(self.points):
            return
        lo, hi, span, t0 = scale
        t, v = self.points[self._hover]
        x = area.left() + area.width() * (self._ts[self._hover] - t0) / span
        y = area.bottom() - area.height() * (v - lo) / (hi - lo)
        p.setPen(QtGui.QPen(qcolor(theme.FG, 0.30), 1, QtCore.Qt.DashLine))
        p.drawLine(QtCore.QPointF(x, area.top()), QtCore.QPointF(x, area.bottom()))
        p.setPen(QtGui.QPen(qcolor(theme.PANEL), 2))
        p.setBrush(qcolor(self.color))
        p.drawEllipse(QtCore.QPointF(x, y), 4.5, 4.5)
        label = f"{v:.0f} {self.unit} · {t:%H:%M}".replace("  ", " ")
        p.setFont(font(12, QtGui.QFont.Weight.DemiBold))
        w = p.fontMetrics().horizontalAdvance(label) + 18
        box = QtCore.QRectF(min(max(x - w / 2, area.left()), area.right() - w),
                            area.top(), w, 24)
        p.setPen(QtGui.QPen(qcolor(theme.BORDER), 1))
        p.setBrush(qcolor(theme.PANEL))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(qcolor(theme.FG))
        p.drawText(box, QtCore.Qt.AlignCenter, label)

    def mouseMoveEvent(self, e):  # noqa: N802 - Qt API
        if self._cache is None or self._cache[3] is None or not self._ts:
            return
        area = self._area()
        _, _, span, t0 = self._cache[3]
        ts = t0 + (e.position().x() - area.left()) / area.width() * span
        i = bisect.bisect_left(self._ts, ts)
        if i > 0 and (i == len(self._ts) or ts - self._ts[i - 1] < self._ts[i] - ts):
            i -= 1
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _):  # noqa: N802 - Qt API
        self._hover = None
        self.update()


class Hypnogram(Animated):
    """Sleep stages as stepped rounded bars on four rows, Apple-Health style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stages: list[tuple[dt.datetime, int]] = []
        self.start = self.end = None
        self.setMinimumHeight(190)

    def set(self, stages, start, end) -> None:
        self.stages, self.start, self.end = sorted(stages), start, end
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        left, right, top, bottom = 56, 26, 6, 24
        area = QtCore.QRectF(left, top, self.width() - left - right,
                             self.height() - top - bottom)
        row_h = area.height() / len(STAGE_ROWS)
        p.setFont(font(12, QtGui.QFont.Weight.DemiBold))
        for i, (_, name, color) in enumerate(STAGE_ROWS):
            p.setPen(qcolor(color))
            p.drawText(QtCore.QRectF(0, area.top() + i * row_h, left - 10, row_h),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, name)
            p.setPen(QtGui.QPen(qcolor(theme.FG, 0.06), 1))
            y = area.top() + (i + 0.5) * row_h
            p.drawLine(QtCore.QPointF(area.left(), y), QtCore.QPointF(area.right(), y))
        if not self.stages or self.start is None:
            p.setPen(qcolor(theme.FG_MUTED))
            p.setFont(font(13))
            p.drawText(area, QtCore.Qt.AlignCenter,
                       "No sleep stages yet — they arrive with the band's sleep record")
            return
        span = (self.end - self.start).total_seconds() or 1
        fx = lambda t: area.left() + area.width() * (t - self.start).total_seconds() / span
        rows = {code: i for i, (code, _, _) in enumerate(STAGE_ROWS)}
        p.setClipRect(QtCore.QRectF(0, 0, area.left() + area.width() * self.progress,
                                    self.height()))
        bounds = self.stages + [(self.end, None)]
        p.setPen(QtCore.Qt.NoPen)
        for (t, code), (t2, _) in zip(bounds, bounds[1:]):
            row = rows.get(code)
            if row is None:
                continue
            rect = QtCore.QRectF(fx(t), area.top() + row * row_h + row_h * 0.18,
                                 max(fx(t2) - fx(t), 2), row_h * 0.64)
            p.setBrush(qcolor(STAGE_ROWS[row][2]))
            p.drawRoundedRect(rect, 4, 4)
        p.setClipping(False)
        p.setFont(font(11))
        p.setPen(qcolor(theme.FG_MUTED))
        for t in (self.start, self.start + (self.end - self.start) / 2, self.end):
            p.drawText(QtCore.QRectF(fx(t) - 30, area.bottom() + 5, 60, 16),
                       QtCore.Qt.AlignCenter, f"{t:%H:%M}")


class Bars(Animated):
    """24 hourly bars that grow from the baseline."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.values = [0.0] * 24
        self.setMinimumHeight(130)

    def set(self, values) -> None:
        self.values = list(values)
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        area = QtCore.QRectF(2, 4, self.width() - 4, self.height() - 26)
        top = max(self.values) or 1
        slot = area.width() / 24
        p.setPen(QtCore.Qt.NoPen)
        for i, v in enumerate(self.values):
            h = area.height() * v / top * self.progress
            p.setBrush(qcolor(self.color, 1.0 if v else 0.15))
            p.drawRoundedRect(QtCore.QRectF(area.left() + i * slot + slot * 0.18,
                                            area.bottom() - max(h, 3),
                                            slot * 0.64, max(h, 3)), 3, 3)
        p.setFont(font(11))
        p.setPen(qcolor(theme.FG_MUTED))
        for h in (0, 6, 12, 18):
            p.drawText(QtCore.QRectF(area.left() + h * slot - 4, area.bottom() + 5, 40, 16),
                       QtCore.Qt.AlignLeft, f"{h:02d}:00")


class ZoneBar(Animated):
    """Minutes per heart-rate zone as one rounded stacked bar plus legend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minutes = [0.0] * 5
        self.setMinimumHeight(96)

    def set(self, minutes) -> None:
        self.minutes = list(minutes) if minutes else [0.0] * 5
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        total = sum(self.minutes)
        track = QtCore.QRectF(0, 6, self.width(), 14)
        path = QtGui.QPainterPath()
        path.addRoundedRect(track, 7, 7)
        p.setClipPath(path)
        p.fillRect(track, qcolor(theme.FG, 0.07))
        x, full = 0.0, self.width() * self.progress
        for m, c in zip(self.minutes, ZONE_COLORS):
            w = full * (m / total) if total else 0
            p.fillRect(QtCore.QRectF(x, 6, w, 14), qcolor(c))
            x += w
        p.setClipping(False)
        col_w = self.width() / 5
        for i, (m, c) in enumerate(zip(self.minutes, ZONE_COLORS)):
            p.setFont(font(12))
            p.setPen(qcolor(c))
            p.drawText(QtCore.QRectF(i * col_w, 32, col_w, 18), QtCore.Qt.AlignLeft,
                       f"Zone {i + 1}")
            p.setFont(font(16, QtGui.QFont.Weight.DemiBold))
            p.setPen(qcolor(theme.FG))
            p.drawText(QtCore.QRectF(i * col_w, 50, col_w, 24), QtCore.Qt.AlignLeft,
                       f"{m:.0f}m")


# -- cards ---------------------------------------------------------------------

class Card(QtWidgets.QFrame):
    """A rounded hairline card: coloured title, optional headline, body."""

    def __init__(self, title: str, color: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("todayCard")
        self.body = QtWidgets.QVBoxLayout(self)
        self.body.setContentsMargins(22, 18, 22, 20)
        self.body.setSpacing(10)
        head = QtWidgets.QHBoxLayout()
        self.title = style(QtWidgets.QLabel(title), 14, QtGui.QFont.Weight.DemiBold,
                           color or theme.FG_MUTED)
        head.addWidget(self.title)
        head.addStretch(1)
        self.aside = style(QtWidgets.QLabel(""), 13, color=theme.FG_MUTED)
        head.addWidget(self.aside)
        self.body.addLayout(head)
        self.headline = style(QtWidgets.QLabel(""), 30, QtGui.QFont.Weight.Bold)
        self.headline.hide()
        self.body.addWidget(self.headline)

    def set_headline(self, text: str) -> None:
        self.headline.setText(text)
        self.headline.setVisible(bool(text))


class StatRow(QtWidgets.QWidget):
    """``● Name ............ value unit``; a dash, muted, when missing."""

    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 3, 0, 3)
        h.setSpacing(8)
        h.addWidget(style(QtWidgets.QLabel("●"), 9, color=color))
        h.addWidget(style(QtWidgets.QLabel(name), 14))
        h.addStretch(1)
        self.value = QtWidgets.QLabel("—")
        self._ok = None
        self._restyle(False)
        h.addWidget(self.value)
        self.unit = style(QtWidgets.QLabel(""), 12, color=theme.FG_MUTED)
        h.addWidget(self.unit)

    def _restyle(self, ok: bool) -> None:
        if ok != self._ok:
            self._ok = ok
            style(self.value, 17, QtGui.QFont.Weight.DemiBold,
                  theme.FG if ok else theme.FG_MUTED)

    def set(self, value, unit: str = "", fmt: str = "{:.0f}") -> None:
        self._restyle(value is not None)
        self.value.setText("—" if value is None else fmt.format(value))
        self.unit.setText(unit if value is not None else "")


class LiveMetric(QtWidgets.QWidget):
    """Big live value with unit and a coloured caption underneath."""

    def __init__(self, caption: str, unit: str, color: str, size: int, parent=None):
        super().__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.size = size
        self._ok = None
        self.value = QtWidgets.QLabel("—")
        self._restyle(False)
        row.addWidget(self.value, 0, QtCore.Qt.AlignBottom)
        row.addWidget(style(QtWidgets.QLabel(unit), 13, color=theme.FG_MUTED), 0,
                      QtCore.Qt.AlignBottom)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(style(QtWidgets.QLabel(caption), 12,
                          QtGui.QFont.Weight.DemiBold, color))

    def _restyle(self, ok: bool) -> None:
        if ok != self._ok:
            self._ok = ok
            style(self.value, self.size, QtGui.QFont.Weight.Bold,
                  theme.FG if ok else theme.FG_MUTED)

    def set(self, value) -> None:
        self._restyle(value is not None)
        self.value.setText("—" if value is None else f"{value}")


def _hm(minutes) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


# -- background loading --------------------------------------------------------

class _Done(QtCore.QObject):
    ready = QtCore.Signal(object, object)   # token, data | Exception


class _Job(QtCore.QRunnable):
    """Computes ``today_data`` on a pool thread with its own DB connection."""

    def __init__(self, path, day, age, token, done):
        super().__init__()
        self.path, self.day, self.age, self.token, self.done = path, day, age, token, done

    def run(self) -> None:
        from ..store import Store
        try:
            store = Store(self.path)
            try:
                out = today_data(store, self.day, self.age)
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001 - reported to the UI thread
            out = exc
        try:
            self.done.ready.emit(self.token, out)
        except RuntimeError:      # page was closed / rebuilt meanwhile
            pass


# -- the page ------------------------------------------------------------------

CACHE_TTL_S = 600     # today's cached data is refetched (silently) after this
ANIM_MS = 480


class TodayPage(QtWidgets.QScrollArea):
    """The home page; ``refresh`` after navigation, ``invalidate`` after new
    history, ``on_live`` for each live sample."""

    def __init__(self, store, age: int | None = None, on_open_metric=None,
                 parent=None, cache=None):
        super().__init__(parent)
        self.store, self.age = store, age
        self.day = dt.date.today()
        self.on_open_metric = on_open_metric or (lambda m: None)
        self.setWidgetResizable(True)
        self.setObjectName("todayScroll")
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        host = QtWidgets.QWidget()
        host.setObjectName("todayHost")
        self.setWidget(host)
        self._outer = QtWidgets.QVBoxLayout(host)
        self._outer.setSpacing(16)
        self._gutter = -1
        self._outer.addLayout(self._header())
        self.grid = QtWidgets.QGridLayout()
        self.grid.setSpacing(14)
        self._outer.addLayout(self.grid)
        self._outer.addStretch(1)
        self._build_cards()
        self._cols = 0
        self._cache: dict[dt.date, tuple[float, dict]] = {} if cache is None else cache
        self._filled_day: dt.date | None = None
        self._token = 0
        self._want_anim = None
        self._done = _Done(self)
        self._done.ready.connect(self._on_ready)
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setDuration(ANIM_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._tick)
        self._apply_gutter()
        self.refresh(animate=False)

    # header ------------------------------------------------------------------
    def _header(self):
        h = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(0)
        self.kicker = style(QtWidgets.QLabel(""), 12, QtGui.QFont.Weight.DemiBold,
                            theme.FG_MUTED)
        self.title = style(QtWidgets.QLabel("Today"), 32, QtGui.QFont.Weight.Bold)
        titles.addWidget(self.kicker)
        titles.addWidget(self.title)
        h.addLayout(titles)
        h.addStretch(1)
        for text, delta in (("‹", -1), ("Today", 0), ("›", 1)):
            b = QtWidgets.QPushButton(text)
            b.setObjectName("pillBtn")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=delta: self.shift(d))
            h.addWidget(b, 0, QtCore.Qt.AlignBottom)
        return h

    def shift(self, delta: int) -> None:
        self.day = dt.date.today() if delta == 0 else self.day + dt.timedelta(days=delta)
        self.day = min(self.day, dt.date.today())
        self.refresh()

    # cards -------------------------------------------------------------------
    def _build_cards(self) -> None:
        self.rings_card = Card("Summary")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(18)
        self.cell_recovery = RingCell("Recovery", theme.RECOVERY_GOOD)
        self.cell_strain = RingCell("Strain", theme.STRAIN)
        self.cell_sleep = RingCell("Sleep", theme.SLEEP)
        for c in (self.cell_recovery, self.cell_strain, self.cell_sleep):
            row.addWidget(c, 1)
        self.rings_card.body.addLayout(row)
        self.rings_card.body.addStretch(1)

        self.live_card = Card("Live", theme.HEART)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(28)
        self.live_hr = LiveMetric("Heart rate", "bpm", theme.HEART, 44)
        self.live_spo2 = LiveMetric("Blood oxygen", "%", theme.OXYGEN, 30)
        self.live_stress = LiveMetric("Stress", "", theme.STRESS, 30)
        row.addWidget(self.live_hr, 3)
        row.addWidget(self.live_spo2, 2)
        row.addWidget(self.live_stress, 2)
        self.live_card.body.addLayout(row)
        status = QtWidgets.QHBoxLayout()
        status.setSpacing(6)
        self.live_dot = style(QtWidgets.QLabel("●"), 10, color=theme.FG_MUTED)
        self.live_place = style(QtWidgets.QLabel("Waiting for the band…"), 13,
                                color=theme.FG_MUTED)
        self.live_battery = style(QtWidgets.QLabel(""), 13,
                                  QtGui.QFont.Weight.DemiBold)
        status.addWidget(self.live_dot)
        status.addWidget(self.live_place)
        status.addStretch(1)
        status.addWidget(self.live_battery)
        self.live_card.body.addLayout(status)

        self.hr_card = Card("Heart Rate", theme.HEART)
        self.hr_chart = AreaChart(theme.HEART, "bpm")
        self.hr_card.body.addWidget(self.hr_chart, 1)

        self.vitals_card = Card("Vitals", theme.OXYGEN)
        self.vital_rows = {k: StatRow(n, c) for k, n, c in (
            ("hrv", "Heart rate variability", theme.SLEEP),
            ("resting_hr", "Resting heart rate", theme.HEART),
            ("resp_rate", "Respiratory rate", theme.OXYGEN),
            ("spo2", "Blood oxygen", theme.OXYGEN),
            ("temperature", "Skin temperature", theme.STRESS))}
        for r in self.vital_rows.values():
            self.vitals_card.body.addWidget(r)
        self.vitals_card.body.addStretch(1)

        self.sleep_card = Card("Sleep", theme.SLEEP)
        self.hypnogram = Hypnogram()
        self.sleep_card.body.addWidget(self.hypnogram, 1)

        self.sleep_stats = Card("Last Night", theme.SLEEP)
        self.sleep_rows = {k: StatRow(n, c) for k, n, c in (
            ("deep", "Deep", STAGE_ROWS[3][2]), ("rem", "REM", STAGE_ROWS[1][2]),
            ("light", "Core", STAGE_ROWS[2][2]), ("awake", "Awake", STAGE_ROWS[0][2]),
            ("efficiency", "Efficiency", theme.FG_MUTED),
            ("debt", "Sleep debt", theme.FG_MUTED))}
        for r in self.sleep_rows.values():
            self.sleep_stats.body.addWidget(r)
        self.sleep_stats.body.addStretch(1)

        self.steps_card = Card("Steps", theme.STEPS)
        self.steps_bars = Bars(theme.STEPS)
        self.steps_card.body.addWidget(self.steps_bars, 1)

        self.zones_card = Card("Heart Rate Zones", theme.STRAIN)
        self.zone_bar = ZoneBar()
        self.zones_card.body.addWidget(self.zone_bar, 1)

        self.stress_card = Card("Stress", theme.STRESS)
        self.stress_chart = AreaChart(theme.STRESS)
        self.stress_card.body.addWidget(self.stress_chart, 1)

        self.battery_card = Card("Body Battery", theme.RECOVERY_GOOD)
        self.battery_chart = AreaChart(theme.RECOVERY_GOOD)
        self.battery_card.body.addWidget(self.battery_chart, 1)

        self.cards = [self.rings_card, self.live_card, self.hr_card, self.vitals_card,
                      self.sleep_card, self.sleep_stats, self.steps_card,
                      self.zones_card, self.stress_card, self.battery_card]
        self.animated = [self.cell_recovery.ring, self.cell_strain.ring,
                         self.cell_sleep.ring, self.hr_chart, self.hypnogram,
                         self.steps_bars, self.zone_bar, self.stress_chart,
                         self.battery_chart]

    #: (card, row, col, row span, col span) for 12 / 6 / 1 column grids.
    def _layout_for(self, cols: int):
        c = self.cards
        if cols == 12:
            return [(c[0], 0, 0, 1, 6), (c[1], 0, 6, 1, 6), (c[2], 1, 0, 1, 8),
                    (c[3], 1, 8, 1, 4), (c[4], 2, 0, 1, 8), (c[5], 2, 8, 1, 4),
                    (c[6], 3, 0, 1, 3), (c[7], 3, 3, 1, 3), (c[8], 3, 6, 1, 3),
                    (c[9], 3, 9, 1, 3)]
        if cols == 6:
            return [(c[0], 0, 0, 1, 6), (c[1], 1, 0, 1, 6), (c[2], 2, 0, 1, 6),
                    (c[4], 3, 0, 1, 6), (c[3], 4, 0, 1, 3), (c[5], 4, 3, 1, 3),
                    (c[6], 5, 0, 1, 3), (c[7], 5, 3, 1, 3), (c[8], 6, 0, 1, 3),
                    (c[9], 6, 3, 1, 3)]
        return [(card, i, 0, 1, 1) for i, card in enumerate(c)]

    def _relayout(self) -> None:
        content = self.viewport().width() - 2 * max(self._gutter, 0)
        cols = 12 if content >= 1000 else 6 if content >= 640 else 1
        if cols == self._cols:
            return
        self._cols = cols
        for card in self.cards:
            self.grid.removeWidget(card)
        for i in range(12):
            self.grid.setColumnStretch(i, 0)
        for card, r, col, rs, cs in self._layout_for(cols):
            self.grid.addWidget(card, r, col, rs, cs)
        for i in range(cols):
            self.grid.setColumnStretch(i, 1)

    def _apply_gutter(self) -> None:
        g = theme.gutter(self.viewport().width())
        if g != self._gutter:
            self._gutter = g
            self._outer.setContentsMargins(g, 22, g, 36)

    def resizeEvent(self, e):  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._apply_gutter()
        self._relayout()

    # data --------------------------------------------------------------------
    def invalidate(self) -> None:
        """Forget cached data (new history arrived); next refresh recomputes."""
        self._cache.clear()

    def refresh(self, animate: bool | None = None) -> None:
        """Show ``self.day``: cached data at once, else computed on a worker.

        ``animate=None`` animates only when the day differs from what is shown.
        """
        self._apply_gutter()
        self._relayout()
        today = self.day == dt.date.today()
        self.kicker.setText(f"{self.day:%A}".upper() if today else f"{self.day:%Y}")
        self.title.setText("Today" if today else f"{self.day:%A, %d %B}")
        hit = self._cache.get(self.day)
        if hit is not None:
            self._show(hit[1], animate)
            if not (today and time.monotonic() - hit[0] > CACHE_TTL_S):
                return
            animate = False
        self._load(animate)

    def _load(self, animate) -> None:
        self._token += 1
        self._want_anim = animate
        if self.store.path == ":memory:":     # no second connection possible
            self._on_ready((self._token, self.day),
                           today_data(self.store, self.day, self.age))
            return
        QtCore.QThreadPool.globalInstance().start(
            _Job(self.store.path, self.day, self.age, (self._token, self.day),
                 self._done))

    def _on_ready(self, token, data) -> None:
        n, day = token
        if isinstance(data, Exception):
            return
        self._cache[day] = (time.monotonic(), data)
        if n == self._token and day == self.day:
            self._show(data, self._want_anim)

    def _show(self, data: dict, animate) -> None:
        if animate is None:
            animate = self._filled_day != data["day"]
        self._filled_day = data["day"]
        self._fill(data)
        self._anim.stop()
        if animate:
            self._tick(0.0)
            self._anim.start()
        else:
            self._tick(1.0)

    def _tick(self, v) -> None:
        n, lag = len(self.animated), 0.25
        for i, w in enumerate(self.animated):
            w.set_progress(_clamp(float(v) * (1 + lag) - lag * i / (n - 1)))

    def _fill(self, d: dict) -> None:
        s = d["scores"]
        sleep = s.sleep or {}
        need = s.sleep_need_min
        if s.recovery is None:
            self.cell_recovery.set(None, f"Calibrating · {min(s.baseline_nights, 4)}/4 nights")
        else:
            self.cell_recovery.set(s.recovery, "of 100", recovery_color(s.recovery))
        self.cell_strain.set(s.strain, f"TRIMP {s.trimp:.0f}" if s.trimp else "No heart data")
        slept = sleep.get("total_min")
        if sleep.get("quality") is not None:
            self.cell_sleep.set(sleep["quality"], f"{_hm(slept)} · band score")
        elif slept and need:
            self.cell_sleep.set(min(100 * slept / need, 100), f"{_hm(slept)} · vs need")
        else:
            self.cell_sleep.set(None, "No sleep recorded")

        self.hr_chart.set(d["hr"], d["start"], d["end"])
        self.hr_card.aside.setText(f"Resting {s.rhr_bpm:.0f} bpm" if s.rhr_bpm else "")
        self.stress_chart.set(d["stress"], d["start"], d["end"], (0, 100))
        self.battery_chart.set(d["body_battery"], d["start"], d["end"], (0, 100))
        self.steps_bars.set(d["steps"])
        t = d["totals"]
        self.steps_card.aside.setText(
            f"{t['steps']:,.0f} steps · {t['distance_m'] / 1000:.1f} km")
        self.zone_bar.set(s.zone_minutes)
        self.zones_card.aside.setText(
            f"{t['active_minutes']:.0f} min · {t['active_calories']:,.0f} kcal")

        v = d["vitals"]
        self.vital_rows["hrv"].set(v["hrv"].value, "ms")
        self.vital_rows["resting_hr"].set(s.rhr_bpm or v["resting_hr"].value, "bpm")
        self.vital_rows["resp_rate"].set(v["resp_rate"].value, "brpm")
        self.vital_rows["spo2"].set(v["spo2"].value, "%")
        self.vital_rows["temperature"].set(v["temperature"].value, "°C", "{:.1f}")

        window = s.sleep_window
        self.hypnogram.set(d["stages"], *(window or (None, None)))
        self.sleep_card.aside.setText(
            f"{window[0]:%H:%M} – {window[1]:%H:%M}" if window else "")
        for key, field in (("deep", "deep_min"), ("rem", "rem_min"),
                           ("light", "light_min"), ("awake", "awake_min")):
            self.sleep_rows[key].set(sleep.get(field), "", "{:.0f} min")
        self.sleep_rows["efficiency"].set(sleep.get("efficiency"), "%")
        self.sleep_rows["debt"].set(s.sleep_debt_min, "", "{:.0f} min")
        self.sleep_stats.set_headline(_hm(slept) if slept else "")
        self.sleep_stats.aside.setText({
            "band": "Band sleep record",
            "derived": "Estimate · from HR & movement"}.get(sleep.get("source"), ""))

    def on_live(self, sample) -> None:
        charging = sample.place == "charging"
        bpm = None if charging else sample.heart_rate
        self.live_hr.set(bpm or None)
        self.live_spo2.set(sample.spo2)
        self.live_stress.set(sample.stress)
        text, color = {
            "charging": ("Charging", theme.RECOVERY_MID),
            "wrist": ("On wrist", theme.RECOVERY_GOOD),
            "off_wrist": ("Off wrist", theme.FG_MUTED),
            "unknown": ("Connected", theme.RECOVERY_GOOD)}[sample.place]
        style(self.live_dot, 10, color=color)
        self.live_place.setText(f"{text} · updated {sample.at:%H:%M:%S}")
        self.live_battery.setText("" if sample.battery is None else (
            f"{'⚡ ' if charging else ''}Battery {sample.battery}%"))
        if bpm and self.day == dt.date.today() and self.hr_chart.points:
            if sample.at > self.hr_chart.points[-1][0]:
                self.hr_chart.append(sample.at, float(bpm))

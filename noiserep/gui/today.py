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
    """Today adapter for the same gap-aware chart used on All Metrics."""

    def __init__(self, color: str, unit: str = "", parent=None):
        super().__init__(parent)
        # Lazy import: app imports TodayPage before defining ChartView.
        from .app import ChartView

        self.unit = unit
        self.points = []
        self.x0 = self.x1 = None
        self.y_range = None
        self.chart = ChartView(interactive=True, height=170)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.chart)
        self.setFixedHeight(170)

    def set(self, points, x0, x1, y_range=None) -> None:
        self.points = sorted(points, key=lambda row: row[0])
        self.x0, self.x1, self.y_range = x0, x1, y_range
        self.chart.retheme()
        self.chart.set_data(self.points, self.unit or None)
        if not self.points:
            self.chart.getPlotItem().setTitle("No readings yet", color=theme.FG_MUTED)
        else:
            self.chart.getPlotItem().setTitle(None)
        if y_range is not None:
            self.chart.setYRange(*y_range, padding=0.08)

    def append(self, t: dt.datetime, v: float) -> None:
        self.set([*self.points, (t, v)], self.x0, self.x1, self.y_range)

    def set_progress(self, value: float) -> None:
        # Keep the axes and recorded samples stable while the page animates.
        self.progress = value


class Hypnogram(Animated):
    """Sleep stages as stepped rounded bars on four rows, Apple-Health style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stages: list[tuple[dt.datetime, int]] = []
        self.start = self.end = None
        self.setFixedHeight(170)

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
            p.drawText(area, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
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
        self.setFixedHeight(140)

    def set(self, values) -> None:
        self.values = list(values)
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        area = QtCore.QRectF(42, 10, max(1, self.width() - 52), self.height() - 36)
        top = max(self.values) or 1
        p.setFont(font(11))
        for fraction in (0.0, 0.5, 1.0):
            y = area.bottom() - area.height() * fraction
            p.setPen(QtGui.QPen(qcolor(theme.FG, 0.12), 1))
            p.drawLine(QtCore.QPointF(area.left(), y), QtCore.QPointF(area.right(), y))
            p.setPen(qcolor(theme.FG_MUTED))
            p.drawText(QtCore.QRectF(0, y - 8, 34, 16),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{top * fraction:,.0f}")
        slot = area.width() / 24
        p.setPen(QtCore.Qt.NoPen)
        for i, v in enumerate(self.values):
            h = area.height() * v / top * self.progress
            if not v:
                continue
            p.setBrush(qcolor(theme.ACCENT, 0.85))
            p.drawRoundedRect(QtCore.QRectF(area.left() + i * slot + slot * 0.18,
                                            area.bottom() - max(h, 3),
                                            slot * 0.64, max(h, 3)), 3, 3)
        p.setFont(font(11))
        p.setPen(qcolor(theme.FG_MUTED))
        for h in (0, 6, 12, 18):
            p.drawText(QtCore.QRectF(area.left() + h * slot - 4, area.bottom() + 5, 40, 16),
                       QtCore.Qt.AlignLeft, f"{h:02d}:00")


class BodyBattery(Animated):
    """Battery silhouette filled from the latest recorded body-battery value."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = None
        self.recorded_at = None
        self.setFixedHeight(170)
        self.setAccessibleName("Body battery: no readings")

    def set(self, points):
        valid = [(t, float(v)) for t, v in points if 0 <= v <= 100]
        self.recorded_at, self.value = max(valid, key=lambda r: r[0]) if valid else (None, None)
        self.setAccessibleName("Body battery: no readings" if self.value is None
                               else f"Body battery: {self.value:.0f} percent")
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        width = min(280, self.width() - 36)
        rect = QtCore.QRectF((self.width() - width - 9) / 2, 22, width, 88)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(qcolor(theme.FG_MUTED, 0.5))
        p.drawRoundedRect(QtCore.QRectF(rect.right() + 4, rect.top() + 29, 7, 30), 3, 3)
        p.setPen(QtGui.QPen(qcolor(theme.FG_MUTED, 0.65), 2))
        p.setBrush(qcolor(theme.FG, 0.04))
        p.drawRoundedRect(rect, 14, 14)
        inner = rect.adjusted(6, 6, -6, -6)
        if self.value is not None and self.value > 0:
            clip = QtGui.QPainterPath()
            clip.addRoundedRect(inner, 9, 9)
            p.setClipPath(clip)
            fill = QtCore.QRectF(inner)
            fill.setWidth(inner.width() * self.value / 100 * self.progress)
            p.fillRect(fill, qcolor(theme.ACCENT, 0.28))
            p.setClipping(False)
        p.setPen(qcolor(theme.FG))
        p.setFont(font(36, QtGui.QFont.Weight.Bold))
        p.drawText(rect, QtCore.Qt.AlignCenter, "—" if self.value is None else f"{self.value:.0f}%")
        p.setFont(font(12))
        p.setPen(qcolor(theme.FG_MUTED))
        caption = "No readings for this day" if self.recorded_at is None else f"Last recorded at {self.recorded_at:%H:%M}"
        p.drawText(QtCore.QRectF(0, 124, self.width(), 24), QtCore.Qt.AlignCenter, caption)


class ZoneBar(Animated):
    """Minutes per heart-rate zone as one rounded stacked bar plus legend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.minutes = [0.0] * 5
        self.setFixedHeight(170)

    def set(self, minutes) -> None:
        self.minutes = list(minutes) if minutes else [0.0] * 5
        self.update()

    def paintEvent(self, _):  # noqa: N802 - Qt API
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        maximum = max(self.minutes) or 1
        row_h = self.height() / 5
        for i, (minutes, color) in enumerate(zip(self.minutes, ZONE_COLORS)):
            top = i * row_h
            p.setFont(font(12))
            p.setPen(qcolor(theme.FG_MUTED))
            p.drawText(QtCore.QRectF(0, top, 54, row_h),
                       QtCore.Qt.AlignVCenter, f"Zone {i + 1}")
            track = QtCore.QRectF(62, top + (row_h - 8) / 2,
                                  max(1, self.width() - 125), 8)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(qcolor(theme.FG, 0.07))
            p.drawRoundedRect(track, 4, 4)
            if minutes:
                bar = QtCore.QRectF(track)
                bar.setWidth(track.width() * minutes / maximum * self.progress)
                p.setBrush(qcolor(color))
                p.drawRoundedRect(bar, 4, 4)
            p.setPen(qcolor(theme.FG))
            p.setFont(font(13, QtGui.QFont.Weight.DemiBold))
            p.drawText(QtCore.QRectF(self.width() - 58, top, 58, row_h),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                       f"{minutes:.0f} min")


# -- cards ---------------------------------------------------------------------

class Card(QtWidgets.QFrame):
    """A rounded hairline card: coloured title, optional headline, body."""

    def __init__(self, title: str, color: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("todayCard")
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.body = QtWidgets.QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 14)
        self.body.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        self.title = style(QtWidgets.QLabel(title), 14, QtGui.QFont.Weight.DemiBold,
                           theme.FG)
        head.addWidget(self.title)
        head.addStretch(1)
        self.aside = style(QtWidgets.QLabel(""), 12, color=theme.FG_MUTED)
        self.aside.setWordWrap(True)
        self.aside.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        head.addWidget(self.aside, 1)
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
        label = style(QtWidgets.QLabel(name), 13)
        label.setWordWrap(True)
        h.addWidget(label, 1)
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


class DayNav(QtWidgets.QFrame):
    """Segmented ‹ Today › control; emits -1 / 0 / +1."""

    shifted = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        from .icons import IconButton
        self.setObjectName("segmented")
        nl = QtWidgets.QHBoxLayout(self)
        nl.setContentsMargins(3, 3, 3, 3)
        nl.setSpacing(2)
        self.prev, self.next = IconButton("left", 30), IconButton("right", 30)
        for b in (self.prev, self.next):
            b.filled = False
        self.prev.setToolTip("Previous day")
        self.next.setToolTip("Next day")
        self.today = QtWidgets.QPushButton("Today")
        self.today.setObjectName("navTab")
        self.today.setCheckable(True)
        self.today.setCursor(QtCore.Qt.PointingHandCursor)
        for b, delta in ((self.prev, -1), (self.today, 0), (self.next, 1)):
            b.clicked.connect(lambda _=False, d=delta: self.shifted.emit(d))
            nl.addWidget(b)
        self.set_day(dt.date.today())

    def set_day(self, day) -> None:
        """Reflect ``day`` (``None`` = a multi-day range)."""
        today = dt.date.today()
        self.next.setEnabled(day is not None and day < today)
        self.today.setChecked(day == today)


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
        self.grid.setSpacing(12)
        self.grid.setAlignment(QtCore.Qt.AlignTop)
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
        nav = DayNav()
        nav.shifted.connect(self.shift)
        self.day_nav = nav
        h.addWidget(nav, 0, QtCore.Qt.AlignBottom)
        return h

    def shift(self, delta: int) -> None:
        self.day = dt.date.today() if delta == 0 else self.day + dt.timedelta(days=delta)
        self.day = min(self.day, dt.date.today())
        self.day_nav.set_day(self.day)
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

        self.hr_card = Card("Heart Rate", theme.ACCENT)
        self.hr_chart = AreaChart(theme.ACCENT, "bpm")
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
        self.vitals_card.body.setAlignment(QtCore.Qt.AlignTop)

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
        self.sleep_stats.body.setAlignment(QtCore.Qt.AlignTop)

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
        self.battery_gauge = BodyBattery()
        self.battery_views = QtWidgets.QStackedWidget()
        self.battery_views.setFixedHeight(170)
        self.battery_views.addWidget(self.battery_gauge)
        self.battery_views.addWidget(self.battery_chart)
        self.battery_switch = QtWidgets.QCheckBox("Graph view")
        self.battery_switch.setAccessibleName("Show body battery graph")
        self.battery_switch.toggled.connect(lambda graph: self.battery_views.setCurrentIndex(int(graph)))
        self.battery_card.body.itemAt(0).layout().addWidget(self.battery_switch)
        self.battery_card.body.addWidget(self.battery_views)

        self.cards = [self.rings_card, self.live_card, self.hr_card, self.vitals_card,
                      self.sleep_card, self.sleep_stats, self.steps_card,
                      self.zones_card, self.stress_card, self.battery_card]
        self.animated = [self.cell_recovery.ring, self.cell_strain.ring,
                         self.cell_sleep.ring, self.hr_chart, self.hypnogram,
                         self.steps_bars, self.zone_bar, self.stress_chart,
                         self.battery_chart, self.battery_gauge]

    #: (card, row, col, row span, col span) for 12 / 6 / 1 column grids.
    def _layout_for(self, cols: int):
        c = self.cards
        if cols == 12:
            return [(c[0], 0, 0, 1, 6), (c[1], 0, 6, 1, 6), (c[2], 1, 0, 1, 8),
                    (c[3], 1, 8, 1, 4), (c[4], 2, 0, 1, 8), (c[5], 2, 8, 1, 4),
                    (c[6], 3, 0, 1, 6), (c[7], 3, 6, 1, 6), (c[8], 4, 0, 1, 6),
                    (c[9], 4, 6, 1, 6)]
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
        self.battery_gauge.set(d["body_battery"])
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
        self.live_card.setToolTip(f"Updated {sample.at:%H:%M:%S}")
        if bpm and self.day == dt.date.today() and self.hr_chart.points:
            if sample.at > self.hr_chart.points[-1][0]:
                self.hr_chart.append(sample.at, float(bpm))

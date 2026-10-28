"""NoiseREP Sync — a light, health-style desktop app over the SQLite store.

Layout follows a native health-app feel: a minimal app bar (brand, device pill,
range picker, export, sync), a soft info banner, then airy content with a
greeting, one oversized value, a large chart card, small stat cards and — when
the band has recorded other channels — a responsive grid of neutral tiles.
Clicking a tile (or "View details") opens a full chart with gap-aware plotting,
downsampling, hover read-outs and CSV/JSON export.

BLE sync runs off the UI thread through qasync; progress and failures surface in
the banner, never as modal dialogs.
"""
from __future__ import annotations

import bisect
import datetime
import inspect
import math
import os
import subprocess
import sys

import pyqtgraph as pg
import qasync
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .syncbar import SyncBar
from .window_chrome import AppWindow, TitleSurface, WindowButton
from .today import TodayPage
from .. import analytics
from ..export import export_csv, export_json
from ..protocol.files import STAGE_NAMES
from ..store import Store, default_db_path

#: None means auto-detect a single ``Noise_REP_*`` band (see ble.connection).
DEFAULT_BAND = None
pg.setConfigOptions(antialias=True)

# Fallback unit for the one channel whose identity is verified. Other metrics
# either get a unit from protocol.metrics (when that module provides one) or
# none at all — we never guess a health name or unit.
UNITS = {"heart_rate": "bpm"}

# (mode, combo label)
RANGE_MODES = [
    ("today", "Today"),
    ("day", "Day…"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("all", "All data"),
    ("custom", "Custom…"),
]

_STAGE_TEXT = {
    "device": "Reading device info…",
    "index": "Reading data index…",
    "series": "Fetching metrics…",
    "control": "Finishing up…",
    "files": "Reading activity files…",
    "done": "Saving…",
}

# Optional, defensively-imported metric labels from the protocol layer.
try:  # pragma: no cover
    from ..protocol.metrics import metric_display as _metric_display
except Exception:  # noqa: BLE001 - absent/incomplete module is fine
    _metric_display = None
try:  # pragma: no cover
    from ..protocol.metrics import metric_unit as _metric_unit
except Exception:  # noqa: BLE001
    _metric_unit = None


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def display_name(metric: str) -> str:
    """'heart_rate' -> 'Heart Rate'; 'metric_21' -> 'Channel 21'."""
    if _metric_display is not None:
        try:
            name = _metric_display(metric)
            if name:
                return str(name)
        except Exception:  # noqa: BLE001 - never let labelling break the UI
            pass
    if metric.startswith("metric_"):
        return "Channel " + metric.split("_", 1)[1]
    return metric.replace("_", " ").title()


def display_unit(metric: str) -> str | None:
    """Unit for ``metric`` or ``None`` when it is not verified."""
    if _metric_unit is not None:
        try:
            unit = _metric_unit(metric)
            if unit:
                return str(unit)
        except Exception:  # noqa: BLE001
            pass
    return UNITS.get(metric)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0


def _fmt(v) -> str:
    """Compact number: 72.0 -> '72', 72.25 -> '72.25'."""
    try:
        return f"{v:g}"
    except (TypeError, ValueError):
        return "—"


def _fmt_avg(v) -> str:
    return _fmt(round(v, 1))


def _fmt_span(seconds: float) -> str:
    mins = int(round(max(0.0, seconds) / 60))
    if mins < 60:
        return f"{mins}m"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins:02d}m" if mins else f"{hours}h"


def _to_local(dt_utc_naive: datetime.datetime) -> datetime.datetime:
    """Stored timestamps are real epochs kept UTC-naive; show them local."""
    return dt_utc_naive.replace(tzinfo=datetime.timezone.utc).astimezone()


def _downsample(rows, max_points: int = 1200):
    """Min/max bucket downsampling that preserves spikes.

    ``rows`` is a list of ``(datetime, value)``. Returns at most roughly
    ``max_points`` rows, keeping the min and max of each bucket in time order.
    """
    n = len(rows)
    if n <= max_points:
        return list(rows)
    bucket = max(1, math.ceil(n / max(1, max_points // 2)))
    out = []
    for i in range(0, n, bucket):
        chunk = rows[i:i + bucket]
        if len(chunk) == 1:
            out.append(chunk[0])
            continue
        lo = min(chunk, key=lambda r: r[1])
        hi = max(chunk, key=lambda r: r[1])
        if lo is hi:
            out.append(lo)
        elif lo[0] <= hi[0]:
            out.extend((lo, hi))
        else:
            out.extend((hi, lo))
    return out


def _segments(rows, gap_factor: float = 4.0, min_gap: float = 180.0):
    """Split a series wherever the time gap is much larger than its cadence.

    The band stores 0 as "no reading", so dropped zeroes leave real holes. A
    single continuous line across those holes would invent data; splitting
    lets the chart show honest gaps instead.
    """
    if not rows:
        return []
    deltas = [(b[0] - a[0]).total_seconds() for a, b in zip(rows, rows[1:])]
    positive = sorted(d for d in deltas if d > 0)
    if not positive:
        return [list(rows)]
    median = positive[len(positive) // 2]
    threshold = max(median * gap_factor, min_gap)
    segs = [[rows[0]]]
    for delta, row in zip(deltas, rows[1:]):
        if delta > threshold:
            segs.append([row])
        else:
            segs[-1].append(row)
    return segs


def _columns_for_width(width: int) -> int:
    """How many tiles fit in ``width`` without becoming cramped."""
    if width <= 0:
        return 1
    return max(1, min(3, width // 300))


def _vitals_columns(width: int) -> int:
    """Six compact vitals at wide widths, reflowing to three then two."""
    if width >= 860:
        return 6
    if width >= 540:
        return 3
    return 2


def _movement_columns(width: int) -> int:
    """Four goal cards at wide widths, reflowing to two then one."""
    if width >= 720:
        return 4
    if width >= 380:
        return 2
    return 1


#: Metrics already given a dedicated overview section; they are not repeated
#: in the generic "Other channels" tile grid.
_FEATURED_METRICS = frozenset({
    "heart_rate", "resting_hr",
    "steps", "distance_m", "active_calories", "active_minutes",
    "sleep_hrv", "stress", "blood_oxygen", "temperature", "respiratory_rate",
})

#: Short caption for each of the six vital cards, in display order.
_VITAL_CARDS = (
    ("hrv", "HRV"),
    ("resting_hr", "Resting HR"),
    ("spo2", "SpO2"),
    ("resp_rate", "Resp. Rate"),
    ("temperature", "Temp"),
    ("stress", "Stress"),
)

#: What each vital means, its usual range, and the stored channel to chart.
VITAL_GUIDE = {
    "hrv": {
        "title": "Heart Rate Variability",
        "metric": "sleep_hrv",
        "meaning": (
            "HRV is the variation in time between heartbeats. The band "
            "records it during sleep, every 30 seconds. It reflects autonomic "
            "balance and recovery; higher often suggests more recovery, but "
            "normal varies widely with age and fitness."),
        "typical": (
            "Resting RMSSD is often roughly 20–80 ms in adults. Compare with "
            "your own trend rather than a single reading."),
    },
    "resp_rate": {
        "title": "Respiratory Rate",
        "metric": "respiratory_rate",
        "meaning": (
            "Breaths per minute, estimated by the band from heart-rate "
            "patterns every 5 minutes. Overnight values are the most stable."),
        "typical": "About 12–20 breaths per minute at rest in adults.",
    },
    "resting_hr": {
        "title": "Resting Heart Rate",
        "metric": "heart_rate",
        "meaning": (
            "Heart rate while at rest. A lower resting rate often reflects "
            "better cardiovascular fitness; it also rises with stress, "
            "illness, caffeine or poor sleep."),
        "typical": "60–100 bpm is a common adult resting range.",
    },
    "spo2": {
        "title": "Blood Oxygen (SpO2)",
        "metric": "blood_oxygen",
        "meaning": (
            "An estimate of oxygen saturation from the optical sensor. "
            "Readings are sensitive to strap fit and movement."),
        "typical": (
            "95–100% is typical at sea level. Persistent values below 90% "
            "warrant medical advice."),
    },
    "temperature": {
        "title": "Skin Temperature",
        "metric": "temperature",
        "meaning": (
            "Wrist skin temperature every 5 minutes. It runs several degrees "
            "below core body temperature and follows the room and strap fit; "
            "the overnight trend is the useful part."),
        "typical": "Roughly 30–35 °C at the wrist.",
    },
    "stress": {
        "title": "Stress",
        "metric": "stress",
        "meaning": (
            "A 0–100 stress score derived from heart-rate variability. It is "
            "a trend indicator, not a diagnosis; one reading is not decisive."),
        "typical": "0–33 low, 34–66 moderate, 67–100 high.",
    },
}

#: Movement channel, caption, display unit, default goal, and goal text.
#: Distance goals are in metres; the card converts its total to kilometres.
_MOVEMENT_GOALS = (
    ("steps", "Steps", "steps", 5000, "5,000 steps"),
    ("active_minutes", "Active Minutes", "min", 30, "30 min"),
    ("distance_m", "Distance", "km", 4000, "4.0 km"),
    ("active_calories", "Active Calories", "kcal", 500, "500 kcal"),
)


#: Live strip: SEBasicData field, caption, unit.
LIVE_FIELDS = (
    ("heart_rate", "Heart rate", "bpm"),
    ("spo2", "SpO2", "%"),
    ("stress", "Stress", None),
    ("steps", "Steps", None),
    ("active_calories", "Active", "kcal"),
    ("calories", "Total", "kcal"),
    ("distance_m", "Distance", "m"),
)


class AnimatedNumber(QtWidgets.QLabel):
    """An integer label that eases to each new value instead of jumping."""

    def __init__(self, parent=None):
        super().__init__("—", parent)
        self._value = None
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setDuration(650)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setText(f"{round(v):,}"))

    def animate_to(self, value) -> None:
        if value is None:
            self._anim.stop()
            self._value = None
            self.setText("—")
            return
        start = self._value if self._value is not None else value
        self._value = value
        self._anim.stop()
        self._anim.setStartValue(float(start))
        self._anim.setEndValue(float(value))
        self._anim.start()


def _end_tree(proc) -> None:
    """Stop a child and everything it started.

    On Windows a venv's python.exe is a launcher that spawns the real
    interpreter, so terminate() alone leaves the child alive holding the
    band's only BLE connection.
    """
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid:
        import subprocess
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
    proc.terminate()


def _end_stray_menubar() -> None:
    """Stop a Live helper this window did not start (or lost track of).

    The helper holds ``MENUBAR_LOCK``; its owner PID says which process to
    end, so a forgotten copy can never keep the band to itself.
    """
    from .menubar import MENUBAR_LOCK

    lock = QtCore.QLockFile(MENUBAR_LOCK)
    info = lock.getLockInfo()
    ok, pid = (info[0], info[1]) if isinstance(info, tuple) else (False, 0)
    if not ok or not pid or pid == os.getpid():
        return
    if os.name == "nt":
        import subprocess
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    lock.removeStaleLockFile()


def _profile_age() -> int | None:
    try:
        from ..config import ConfigError, load
        return load().profile.age
    except (ConfigError, OSError):
        return None


def _fmt_min(minutes) -> str:
    return _fmt_span(minutes * 60) if minutes is not None else "—"


def _chip_row(items) -> QtWidgets.QHBoxLayout:
    """A row of value-over-label chips."""
    chips = QtWidgets.QHBoxLayout()
    chips.setSpacing(26)
    for label, val in items:
        cell = QtWidgets.QVBoxLayout()
        cell.setSpacing(1)
        num = QtWidgets.QLabel(val)
        num.setObjectName("chipValue")
        lab = QtWidgets.QLabel(label)
        lab.setObjectName("statLabel")
        cell.addWidget(num)
        cell.addWidget(lab)
        chips.addLayout(cell)
    chips.addStretch(1)
    return chips


def _workout_meta(workout: dict) -> str:
    """One-line summary: time, then duration/steps/kcal or file size."""
    parts = []
    when = workout.get("time")
    if when:
        parts.append(f"{when:%d %b, %H:%M}")
    if workout.get("duration_s"):
        parts.append(_fmt_span(workout["duration_s"]))
    if workout.get("steps"):
        parts.append(f"{workout['steps']:,} steps")
    if workout.get("kcal"):
        parts.append(f"{workout['kcal']} kcal")
    if workout.get("size"):
        parts.append(f"{workout['size']:,} bytes")
    return " · ".join(parts)


def _shortcut_label(seq: str) -> str:
    if sys.platform == "darwin":
        return seq.replace("Ctrl+", "⌘").replace("Esc", "esc")
    return seq


def _add_shadow(widget: QtWidgets.QWidget, blur: int = 26, dy: int = 4,
                alpha: int = 14) -> None:
    """No-op: cards are flat hairline frames (graphics effects are costly)."""


class SmartDateAxis(pg.DateAxisItem):
    """Date axis whose tick format adapts to the visible span."""

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - Qt override
        if not values:
            return []
        span = max(values) - min(values)
        if span < 6 * 3600:
            fmt = "%H:%M"
        elif span < 2 * 86400:
            fmt = "%H:%M"
        elif span < 9 * 86400:
            fmt = "%a %d"
        elif span < 75 * 86400:
            fmt = "%d %b"
        else:
            fmt = "%b %Y"
        out = []
        for value in values:
            try:
                out.append(datetime.datetime.fromtimestamp(value).strftime(fmt))
            except (ValueError, OSError):
                out.append("")
        return out


class GutterScrollArea(QtWidgets.QScrollArea):
    """Recalculate page gutters when a previously hidden viewport is shown."""

    def sync_gutters(self):
        host = self.widget()
        if host is not None and host.layout() is not None:
            gutter = theme.page_gutter(self, self.viewport().width())
            host.layout().setContentsMargins(gutter, 22, gutter, 36)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.sync_gutters()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.sync_gutters()


class GlassReadout(pg.TextItem):
    """Rounded translucent chart readout, using the active theme palette."""

    def paint(self, painter, *args):
        super().paint(painter, *args)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        surface = QtGui.QColor(theme.PANEL)
        surface.setAlpha(238)
        edge = QtGui.QColor(theme.BORDER)
        edge.setAlpha(220)
        painter.setPen(QtGui.QPen(edge, 1))
        painter.setBrush(surface)
        painter.drawRoundedRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class ChartView(pg.PlotWidget):
    """A gap-aware line chart with hover read-out and optional click-through.

    Overview charts pass ``interactive=False`` so a click opens the metric;
    detail charts allow horizontal pan/zoom.
    """

    activated = QtCore.Signal()

    def __init__(self, interactive: bool = False, height: int | None = None,
                 parent=None):
        super().__init__(
            parent=parent,
            axisItems={"bottom": SmartDateAxis(orientation="bottom")},
        )
        self.interactive = interactive
        self.setBackground(theme.PANEL)
        self.setMenuEnabled(False)
        self.showGrid(x=False, y=True, alpha=0.12)
        pi = self.getPlotItem()
        pi.hideButtons()
        pi.hideAxis("right")
        pi.hideAxis("top")
        pi.getViewBox().setDefaultPadding(0.08)
        pi.getViewBox().setMouseEnabled(x=interactive, y=False)
        if height:
            self.setMinimumHeight(height)
        tick_font = QtGui.QFont()
        tick_font.setPointSize(10)
        for side in ("left", "bottom"):
            axis = pi.getAxis(side)
            axis.setPen(pg.mkPen(theme.BORDER))
            axis.setTextPen(pg.mkPen(theme.FG_MUTED))
            axis.setStyle(tickFont=tick_font, tickTextOffset=6)
        pi.getAxis("left").setWidth(46)

        self._rows: list[tuple] = []
        self._x: list[float] = []
        self.unit: str | None = None
        self._fill = True
        self._vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(theme.FG_MUTED, width=1,
                         style=QtCore.Qt.DashLine))
        self._marker = pg.ScatterPlotItem(
            [], [], size=9, pen=pg.mkPen(theme.PANEL, width=2),
            brush=pg.mkBrush(theme.ACCENT))
        self._label = GlassReadout("", anchor=(0, 0), color=theme.FG)
        self._label.textItem.document().setDocumentMargin(8)
        self._hover_anim = QtCore.QVariantAnimation(self)
        self._hover_anim.setDuration(130)
        self._hover_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._hover_opacity)
        self._hover_anim.finished.connect(self._hover_finished)
        self._hover = None
        self._show_hover(False)
        self._proxy = pg.SignalProxy(
            self.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse)

    def retheme(self) -> None:
        """Re-read the palette after a light/dark switch."""
        self.setBackground(theme.PANEL)
        pi = self.getPlotItem()
        for side in ("left", "bottom"):
            axis = pi.getAxis(side)
            axis.setPen(pg.mkPen(theme.BORDER))
            axis.setTextPen(pg.mkPen(theme.FG_MUTED))
        self._vline.setPen(pg.mkPen(theme.FG_MUTED, width=1, style=QtCore.Qt.DashLine))
        self._marker.setPen(pg.mkPen(theme.PANEL, width=2))
        self._marker.setBrush(pg.mkBrush(theme.ACCENT))
        self._label.setColor(theme.FG)
        self._label.update()

    # -- data ------------------------------------------------------------
    def set_data(self, rows, unit: str | None = None, fill: bool = True) -> int:
        """Plot ``rows`` (``[(datetime, value)]``) and return the point count."""
        pi = self.getPlotItem()
        self.clear()
        self._rows, self._x, self.unit, self._fill = [], [], unit, fill
        self._show_hover(False)
        if not rows:
            return 0

        plotted: list[tuple] = []
        y_all = [v for _, v in rows]
        ymin, ymax = min(y_all), max(y_all)
        baseline = ymin - max(1e-9, ymax - ymin) * 0.25
        pen = pg.mkPen(theme.ACCENT, width=2)
        brush = pg.mkBrush(204, 120, 92, 26)

        for seg in _segments(rows):
            seg = _downsample(seg, 1600 if self.interactive else 520)
            plotted.extend(seg)
            xs = [d.timestamp() for d, _ in seg]
            ys = [v for _, v in seg]
            item = self.plot(xs, ys, pen=pen)
            if fill and len(ys) > 1:
                item.setFillLevel(baseline)
                item.setFillBrush(brush)

        if len(plotted) <= 40:
            self.plot(
                [d.timestamp() for d, _ in plotted],
                [v for _, v in plotted],
                pen=None, symbol="o", symbolSize=6,
                symbolBrush=pg.mkBrush(theme.ACCENT),
                symbolPen=pg.mkPen(theme.PANEL, width=1))

        plotted.sort(key=lambda r: r[0])
        # Hover look-ups use the full resolution series so the read-out names
        # the true nearest sample, while the line itself stays downsampled.
        hover_rows = sorted(rows, key=lambda r: r[0])
        self._rows = hover_rows
        self._x = [d.timestamp() for d, _ in hover_rows]

        # `clear()` removed the hover items; put them back last so they sit
        # above the data, and never affect auto-ranging.
        pi.addItem(self._vline, ignoreBounds=True)
        pi.addItem(self._marker, ignoreBounds=True)
        pi.addItem(self._label, ignoreBounds=True)
        self._show_hover(False)
        self.getViewBox().enableAutoRange()
        return len(plotted)

    # -- hover -----------------------------------------------------------
    def _hover_opacity(self, value) -> None:
        for item in (self._vline, self._marker, self._label):
            item.setOpacity(float(value))

    def _hover_finished(self) -> None:
        if not self._hover:
            for item in (self._vline, self._marker, self._label):
                item.hide()

    def _show_hover(self, visible: bool) -> None:
        if self._hover == visible:
            return
        initial = self._hover is None
        self._hover = visible
        self._hover_anim.stop()
        if initial:
            self._hover_opacity(0.0)
            self._hover_finished()
            return
        if visible:
            for item in (self._vline, self._marker, self._label):
                item.show()
        self._hover_anim.setStartValue(self._label.opacity())
        self._hover_anim.setEndValue(1.0 if visible else 0.0)
        self._hover_anim.start()

    def _on_mouse(self, event) -> None:
        if not self._rows:
            return
        pos = event[0]
        vb = self.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            self._show_hover(False)
            return
        point = vb.mapSceneToView(pos)
        idx = bisect.bisect_left(self._x, point.x())
        candidates = [i for i in (idx - 1, idx) if 0 <= i < len(self._x)]
        if not candidates:
            self._show_hover(False)
            return
        j = min(candidates, key=lambda i: abs(self._x[i] - point.x()))
        when, value = self._rows[j]
        self._vline.setPos(self._x[j])
        self._marker.setData([self._x[j]], [value])

        span = self._x[-1] - self._x[0]
        if span < 2 * 86400:
            fmt = "%H:%M"
        elif span < 9 * 86400:
            fmt = "%a %H:%M"
        else:
            fmt = "%d %b %H:%M"
        text = f"{when.strftime(fmt)}  ·  {_fmt(value)}"
        if self.unit:
            text += f" {self.unit}"
        # Position in scene pixels: data-space offsets can clip long labels
        # at either edge, especially after zooming or on a narrow card.
        rect = vb.sceneBoundingRect().adjusted(8, 8, -8, -8)
        font = self._label.textItem.font()
        metrics = QtGui.QFontMetricsF(font)
        text = metrics.elidedText(text, QtCore.Qt.ElideRight,
                                  max(1, int(rect.width() - 20)))
        self._label.setText(text, color=theme.FG)
        self._label.setAnchor((0, 0))
        self._show_hover(True)
        width = self._label.boundingRect().width()
        sample_x = vb.mapViewToScene(QtCore.QPointF(self._x[j], value)).x()
        left = sample_x + 10
        if left + width > rect.right():
            left = sample_x - width - 10
        left = max(rect.left(), min(left, rect.right() - width))
        self._label.setPos(vb.mapSceneToView(QtCore.QPointF(left, rect.top())))

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._show_hover(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (not self.interactive and self._rows
                and event.button() == QtCore.Qt.LeftButton):
            self.activated.emit()
        super().mouseReleaseEvent(event)


def _sparkline(rows, height: int = 46):
    """A tiny axis-less accent line for a tile."""
    widget = pg.PlotWidget()
    widget.setBackground(None)
    widget.setFixedHeight(height)
    widget.setMenuEnabled(False)
    widget.hideButtons()
    widget.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    pi = widget.getPlotItem()
    pi.hideAxis("left")
    pi.hideAxis("bottom")
    pi.setContentsMargins(0, 0, 0, 0)
    vb = pi.getViewBox()
    vb.setMouseEnabled(False, False)
    vb.setDefaultPadding(0.12)
    data = _downsample(rows, 320)
    if data:
        x = [d.timestamp() for d, _ in data]
        y = [v for _, v in data]
        item = widget.plot(x, y, pen=pg.mkPen(theme.ACCENT, width=2))
        if len(y) > 1:
            item.setFillLevel(min(y))
            item.setFillBrush(pg.mkBrush(204, 120, 92, 22))
    return widget


class ClickableCard(QtWidgets.QFrame):
    """A card that emits ``clicked`` and supports Enter/Space activation."""

    clicked = QtCore.Signal()

    def __init__(self, object_name: str = "tile"):
        super().__init__()
        self.setObjectName(object_name)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter,
                           QtCore.Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(AppWindow):
    def __init__(self, store: Store, band_name: str | None = DEFAULT_BAND):
        super().__init__()
        if band_name is None:
            try:
                from ..config import ConfigError, load
                band_name = load().band_name
            except (ConfigError, OSError):
                band_name = None
        self.store = store
        self.band_name = band_name
        self.setWindowTitle("NoiseREP Sync")
        self.resize(1120, 760)
        self.setMinimumSize(860, 600)

        self._shown_metrics: list[str] = []
        self._detail_metric: str | None = None
        self._range_mode = "today"
        self._syncing = False
        self._sync_ok = False
        self._tile_widgets: list[QtWidgets.QWidget] = []
        self.tiles_grid: QtWidgets.QGridLayout | None = None
        self._tile_cols = 0
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(90)
        self._resize_timer.timeout.connect(self._relayout_tiles)
        self._did_initial_focus = False

        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_appbar())
        self._link_task = None
        self._sync_now = None
        self._menubar_proc = None
        self._menubar_stopping = False
        self._last_live = None
        self._menubar_poll = QtCore.QTimer(self)
        self._menubar_poll.setInterval(1000)
        self._menubar_poll.timeout.connect(self._poll_menubar)

        banner_host = QtWidgets.QWidget()
        banners = QtWidgets.QVBoxLayout(banner_host)
        self._banner_layout = banners
        banners.setContentsMargins(26, 14, 26, 0)
        banners.setSpacing(8)
        banners.addWidget(self._build_error_banner())
        banners.addWidget(self._build_info_banner())
        outer.addWidget(banner_host)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_overview())      # index 0
        self.stack.addWidget(self._build_detail())        # index 1
        self.stack.addWidget(self._build_sleep_detail())  # index 2
        self.guide_page = self._build_guide()
        self.stack.addWidget(self.guide_page)             # index 3
        self.stack.addWidget(self._build_vital_detail())  # index 4
        self.today = TodayPage(store, _profile_age(), on_open_metric=self._open_detail)
        self.stack.addWidget(self.today)                  # index 5
        self.settings_page = self._build_settings()
        self.stack.addWidget(self.settings_page)          # index 6
        outer.addWidget(self.stack, 1)

        self._install_shortcuts()
        self._load()
        self._clock = QtCore.QTimer(self)
        self._clock.timeout.connect(lambda: self.ov_title.setText(self._greeting()))
        self._clock.start(60_000)
        self._show_home("today")
        self._apply_gutter()

    # ------------------------------------------------------------------
    # app bar
    # ------------------------------------------------------------------
    def _build_appbar(self) -> QtWidgets.QWidget:
        bar = TitleSurface()
        bar.setObjectName("appbar")
        outer = QtWidgets.QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = TitleSurface()
        h = QtWidgets.QHBoxLayout(top)
        self._bar_layout = h
        h.setContentsMargins(26, 10, 26, 10)
        h.setSpacing(10)

        name = QtWidgets.QLabel("NoiseREP")
        name.setObjectName("appName")
        h.addWidget(name)
        h.addSpacing(10)

        pill = QtWidgets.QFrame()
        pill.setObjectName("devicePill")
        pl = QtWidgets.QHBoxLayout(pill)
        pl.setContentsMargins(11, 5, 11, 5)
        pl.setSpacing(7)
        self.dev_model = QtWidgets.QLabel("Noise REP")
        self.dev_fw = QtWidgets.QLabel("")
        self.dev_sync = QtWidgets.QLabel("")
        self.band_dot = QtWidgets.QLabel("●")
        self.band_dot.setObjectName("bandDot")
        self.band_state = QtWidgets.QLabel("Band link off")
        self.band_state.setObjectName("bandState")
        pl.addWidget(self.band_dot)
        pl.addWidget(self.band_state)
        for label, obj in ((self.dev_model, "settingValue"),
                           (self.dev_fw, "settingValue"),
                           (self.dev_sync, "settingValue")):
            label.setObjectName(obj)
        self._set_band_state("off", "Band link off")
        h.addWidget(pill)
        h.addStretch(1)
        self.nav_tabs = {}
        seg = QtWidgets.QFrame()
        seg.setObjectName("segmented")
        sl = QtWidgets.QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        group = QtWidgets.QButtonGroup(self)
        for key, text in (("today", "Today"), ("metrics", "Health Trends"),
                          ("settings", "Settings")):
            tab = QtWidgets.QPushButton(text)
            tab.setObjectName("navTab")
            tab.setCheckable(True)
            tab.setCursor(QtCore.Qt.PointingHandCursor)
            tab.clicked.connect(lambda _=False, k=key: self._show_home(k))
            group.addButton(tab)
            self.nav_tabs[key] = tab
            sl.addWidget(tab)
        h.addWidget(seg)
        h.addStretch(1)

        # QComboBox is native AppKit on macOS (theme.QSS omits its styling
        # there); the explicit height keeps it aligned with the app bar.
        self.range_combo = QtWidgets.QComboBox()
        for key, text in RANGE_MODES:
            if key not in ("today", "day"):
                self.range_combo.addItem(text, key)
        self.range_combo.setPlaceholderText("Date range")
        self.range_combo.setCurrentIndex(-1)
        self.range_combo.setAccessibleName("Date range")
        self.range_combo.setToolTip("Choose the period shown")
        self.range_combo.setMinimumHeight(32)
        self.range_combo.currentIndexChanged.connect(self._on_range_mode_changed)

        # Single-day navigation: ‹ [calendar date] ›.  Hidden until "Day…" is
        # selected so the app bar stays calm in the preset modes.

        self.day_date = QtWidgets.QDateEdit()
        self.day_date.setCalendarPopup(True)
        self.day_date.setDisplayFormat("ddd dd MMM yyyy")
        self.day_date.setMinimumHeight(32)
        self.day_date.setDate(QtCore.QDate.currentDate())
        self.day_date.setAccessibleName("Selected day")
        self.day_date.setToolTip("Pick any day with stored data")
        self.day_date.dateChanged.connect(self._on_day_changed)


        self.day_date.hide()

        # A QToolButton whose "ghost" frame exists only off macOS.  QPushButton
        # would inherit the app's custom rounded QPushButton rule, so on macOS
        # this stays a style-free tool button and AppKit draws it natively.
        self.export_menu_btn = QtWidgets.QToolButton()
        self.export_menu_btn.setObjectName("ghost")
        self.export_menu_btn.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.export_menu_btn.setText(
            "Export" if theme.is_macos() else "Export ▾")
        self.export_menu_btn.setToolTip(
            f"Export CSV ({_shortcut_label('Ctrl+E')})")
        self.export_menu_btn.setAccessibleName("Export data")
        menu = QtWidgets.QMenu(self.export_menu_btn)
        act_csv = menu.addAction("Export CSV…")
        act_csv.triggered.connect(lambda: self._export("csv"))
        act_json = menu.addAction("Export JSON…")
        act_json.triggered.connect(lambda: self._export("json"))
        self.export_menu_btn.setMenu(menu)
        self.export_menu_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_menu_btn.setMinimumHeight(36)

        self.live_btn = QtWidgets.QPushButton("♥ Live")
        self.live_btn.setObjectName("liveBtn")
        self.live_btn.setMinimumHeight(34)
        self.live_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.live_btn.clicked.connect(self._toggle_menubar)
        self._set_live_btn(False)

        self.guide_btn = QtWidgets.QPushButton("? Icon guide")
        self.guide_btn.setObjectName("ghost")
        self.guide_btn.setMinimumHeight(34)
        self.guide_btn.setToolTip("How to read the menu-bar heart icon")
        self.guide_btn.setAccessibleName("Menu-bar icon guide")
        self.guide_btn.clicked.connect(self._show_guide)

        from .icons import IconButton
        self.theme_btn = IconButton("moon", 38)
        self.theme_btn.setAccessibleName("Switch light or dark theme")
        self.theme_btn.clicked.connect(self._toggle_theme)
        self._sync_theme_btn()
        h.addWidget(self.theme_btn)

        self.sync_pct = QtWidgets.QLabel("0%")
        self.sync_pct.setObjectName("syncPct")
        self.sync_pct.setMinimumWidth(40)
        self.sync_pct.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.sync_pct.hide()
        h.addWidget(self.sync_pct)
        self.sync_btn = QtWidgets.QPushButton("Sync band")
        self.sync_btn.setObjectName("primary")
        self.sync_btn.setMinimumHeight(40)
        self.sync_btn.setToolTip(f"Sync with the band ({_shortcut_label('Ctrl+R')})")
        self.sync_btn.setAccessibleName("Sync band")
        self.sync_btn.setAccessibleDescription(
            "Scan for the band and download new history")
        self.sync_btn.clicked.connect(self._on_sync)
        h.addWidget(self.sync_btn)
        self.window_controls = QtWidgets.QWidget(bar)
        controls = QtWidgets.QHBoxLayout(self.window_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(3)
        self.minimize_button = WindowButton("minimize")
        self.maximize_button = WindowButton("maximize")
        self.close_button = WindowButton("close")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(self._toggle_maximized)
        self.close_button.clicked.connect(self.close)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            controls.addWidget(button)
        self.window_controls.setFixedSize(108, 32)

        outer.addWidget(top)
        self.sync_bar = SyncBar()
        self.sync_bar.percent.connect(lambda n: self.sync_pct.setText(f"{n}%"))
        self.sync_bar.activeChanged.connect(self.sync_pct.setVisible)
        outer.addWidget(self.sync_bar)

        self.custom_row = QtWidgets.QWidget()
        cr = QtWidgets.QHBoxLayout(self.custom_row)
        cr.setContentsMargins(26, 0, 26, 12)
        cr.setSpacing(8)
        cr.addStretch(1)
        frm = QtWidgets.QLabel("From")
        frm.setObjectName("muted")
        cr.addWidget(frm)
        self.date_from = QtWidgets.QDateEdit()
        self.date_to = QtWidgets.QDateEdit()
        for de in (self.date_from, self.date_to):
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM-dd")
            de.setMinimumHeight(32)
            de.dateChanged.connect(self._on_range_changed)
        to = QtWidgets.QLabel("to")
        to.setObjectName("muted")
        cr.addWidget(self.date_from)
        cr.addWidget(to)
        cr.addWidget(self.date_to)
        cr.addStretch(1)
        self.custom_row.hide()
        outer.addWidget(self.custom_row)
        return bar

    # ------------------------------------------------------------------
    # banners
    # ------------------------------------------------------------------
    def _build_info_banner(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("infoBanner")
        h = QtWidgets.QHBoxLayout(frame)
        h.setContentsMargins(16, 11, 16, 11)
        h.setSpacing(11)
        icon = QtWidgets.QLabel("i")
        icon.setObjectName("infoIcon")
        icon.setFixedWidth(14)
        h.addWidget(icon, 0, QtCore.Qt.AlignTop)
        v = QtWidgets.QVBoxLayout()
        v.setSpacing(6)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("infoText")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Status")
        v.addWidget(self.status)
        h.addLayout(v, 1)
        self.info_banner = frame
        return frame

    def _build_error_banner(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("errorBanner")
        h = QtWidgets.QHBoxLayout(frame)
        h.setContentsMargins(14, 9, 10, 9)
        h.setSpacing(10)
        self.error_icon = QtWidgets.QLabel("Error")
        self.error_icon.setObjectName("errorIcon")
        h.addWidget(self.error_icon, 0, QtCore.Qt.AlignTop)
        self.error_text = QtWidgets.QLabel("")
        self.error_text.setObjectName("errorText")
        self.error_text.setWordWrap(True)
        h.addWidget(self.error_text, 1)
        dismiss = QtWidgets.QPushButton("Dismiss")
        dismiss.setObjectName("errorDismiss")
        dismiss.setMinimumHeight(28)
        dismiss.setAccessibleName("Dismiss error")
        dismiss.clicked.connect(self._clear_error)
        h.addWidget(dismiss, 0, QtCore.Qt.AlignTop)
        frame.hide()
        self.error_banner = frame
        return frame

    # ------------------------------------------------------------------
    # overview
    # ------------------------------------------------------------------
    def _make_page(self):
        """Detail pages use the same window-aware gutters as Today and Trends."""
        scroll = GutterScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setSpacing(16)
        scroll.sync_gutters()
        return scroll, inner, lay

    def _build_overview(self) -> QtWidgets.QWidget:
        scroll = GutterScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        host = QtWidgets.QWidget()
        scroll.setWidget(host)
        lay = QtWidgets.QVBoxLayout(host)
        lay.setSpacing(16)
        self._trends_scroll, self._trends_layout = scroll, lay
        g = theme.page_gutter(self, scroll.viewport().width())
        lay.setContentsMargins(g, 22, g, 36)

        row = QtWidgets.QHBoxLayout()
        head = QtWidgets.QVBoxLayout()
        head.setSpacing(2)
        self.ov_title = QtWidgets.QLabel("Good morning")
        self.ov_title.setObjectName("greeting")
        self.ov_sub = QtWidgets.QLabel("")
        self.ov_sub.setObjectName("muted")
        head.addWidget(self.ov_title)
        head.addWidget(self.ov_sub)
        row.addLayout(head)
        row.addStretch(1)
        from .today import DayNav
        self.ov_day_nav = DayNav()
        self.ov_day_nav.shifted.connect(self._overview_shift)
        self.prev_day_btn, self.next_day_btn = self.ov_day_nav.prev, self.ov_day_nav.next
        for w in (self.range_combo, self.day_date, self.ov_day_nav):
            row.addWidget(w, 0, QtCore.Qt.AlignBottom)
        lay.addLayout(row)

        self.ov_stack = QtWidgets.QStackedWidget()

        cards = QtWidgets.QWidget()
        self.ov_body = QtWidgets.QVBoxLayout(cards)
        self.ov_body.setContentsMargins(0, 0, 0, 0)
        self.ov_body.setSpacing(18)
        self.ov_stack.addWidget(cards)          # index 0

        empty_page = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(empty_page)
        el.setContentsMargins(0, 8, 0, 8)
        el.addStretch(1)
        # Centre the card with stretches rather than a layout alignment flag:
        # alignment flags stop height-for-width propagation, which clips the
        # word-wrapped hint label.
        wrap = QtWidgets.QWidget()
        wh = QtWidgets.QHBoxLayout(wrap)
        wh.setContentsMargins(0, 0, 0, 0)
        wh.addStretch(1)
        wh.addWidget(self._build_empty_state())
        wh.addStretch(1)
        el.addWidget(wrap)
        el.addStretch(1)
        self.ov_stack.addWidget(empty_page)     # index 1

        lay.addWidget(self.ov_stack, 1)

        foot = QtWidgets.QLabel(
            f"{_shortcut_label('Ctrl+R')} sync   ·   "
            f"{_shortcut_label('Ctrl+E')} export   ·   "
            f"{_shortcut_label('Ctrl+1')} overview")
        foot.setObjectName("shortcuts")
        foot.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(foot)
        return scroll

    def _build_empty_state(self) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setObjectName("emptyCard")
        card.setMaximumWidth(540)
        card.setMinimumWidth(340)
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(42, 34, 42, 32)
        v.setSpacing(10)

        glyph = QtWidgets.QLabel("♡")
        glyph.setObjectName("emptyGlyph")
        glyph.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(glyph)

        self.empty_title = QtWidgets.QLabel("Welcome to NoiseREP")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(self.empty_title)

        self.empty_hint = QtWidgets.QLabel(
            "No data yet — sync your band to pull its history. Put the band "
            "nearby with your phone's Bluetooth off.")
        self.empty_hint.setObjectName("hint")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(self.empty_hint)

        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)
        btns.addStretch(1)
        self.empty_sync_btn = QtWidgets.QPushButton("Sync band")
        self.empty_sync_btn.setObjectName("primary")
        self.empty_sync_btn.setMinimumHeight(40)
        self.empty_sync_btn.setAccessibleName("Sync band")
        self.empty_sync_btn.clicked.connect(self._on_sync)
        btns.addWidget(self.empty_sync_btn)
        self.empty_all_btn = QtWidgets.QPushButton("Show all data")
        self.empty_all_btn.setObjectName("linkBtn")
        self.empty_all_btn.setMinimumHeight(40)
        self.empty_all_btn.setAccessibleName("Show all data")
        self.empty_all_btn.clicked.connect(self._show_all_data)
        btns.addWidget(self.empty_all_btn)
        btns.addStretch(1)
        v.addLayout(btns)

        hint = QtWidgets.QLabel(f"{_shortcut_label('Ctrl+R')} to sync")
        hint.setObjectName("shortcuts")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(hint)
        _add_shadow(card, blur=30, dy=6, alpha=16)
        return card

    # -- overview sections ------------------------------------------------
    def _populate_overview(self) -> None:
        self._clear_layout(self.ov_body)
        self._tile_widgets = []
        self.tiles_grid = None
        self._tile_cols = 0
        self.sleep_card = None
        self.sleep_summary = None
        self._vitals_grid = None
        self._vital_widgets = []
        self._vitals_cols = 0
        self._movement_grid = None
        self._movement_widgets = []
        self._movement_cols = 0
        self.vital_cards = {}
        self.goal_cards = {}
        self.goal_progress = {}
        self.heart_cards = {}
        self.goals_note = None

        start, end = self._range()
        metrics = self.store.metrics(start, end)
        self._shown_metrics = metrics
        if not metrics:
            self._populate_empty()
            self.ov_stack.setCurrentIndex(1)
            return

        hero = "heart_rate" if "heart_rate" in metrics else metrics[0]
        rows = self.store.samples(hero, start, end)
        self.ov_body.addWidget(self._value_block(hero, rows, link=True))
        self.ov_body.addWidget(self._overview_chart(hero, rows))
        self.ov_body.addWidget(self._overview_stat_cards(rows))
        self.ov_body.addWidget(self._vitals_section())
        sleep = self._maybe_sleep_card(start, end)
        if sleep is not None:
            self.ov_body.addWidget(self._section_label("HIGHLIGHTS"))
            self.ov_body.addWidget(sleep)
        self.ov_body.addWidget(self._heart_stress_section())
        self.ov_body.addWidget(self._movement_section())
        workouts = self._workouts_section()
        if workouts is not None:
            self.ov_body.addWidget(workouts)

        others = [m for m in metrics
                  if m != hero and m not in _FEATURED_METRICS]
        if others:
            self.ov_body.addWidget(self._section_label("OTHER CHANNELS"))
            self.ov_body.addWidget(self._tiles_section(others, start, end))

        self.ov_body.addStretch(1)
        self.ov_stack.setCurrentIndex(0)

    def _populate_empty(self) -> None:
        has_any = self.store.sample_bounds()[0] is not None
        if has_any:
            self.empty_title.setText("Nothing in this period")
            self.empty_hint.setText(
                "No data in this range — try a wider period or sync the band.")
            self.empty_all_btn.show()
            self.empty_sync_btn.setText("Sync band")
        else:
            self.empty_title.setText("Welcome to NoiseREP")
            self.empty_hint.setText(
                "No data yet — sync your band to pull its history. Put the "
                "band nearby with your phone's Bluetooth off.")
            self.empty_all_btn.hide()

    # -- vitals / highlights / movement -----------------------------------
    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _vitals_section(self) -> QtWidgets.QWidget:
        start, end = self._range()
        try:
            vits = analytics.vitals(self.store, start, end)
        except Exception:  # noqa: BLE001 - never break the overview
            vits = {}

        wrap = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self._section_label("VITALS"))

        host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self._vitals_grid = grid
        self._vital_widgets = []
        self.vital_cards = {}
        for key, caption in _VITAL_CARDS:
            vital = vits.get(key)
            if vital is None:
                continue
            card = self._vital_card(key, caption, vital)
            self.vital_cards[key] = card
            self._vital_widgets.append(card)

        cols = _vitals_columns(self._content_width())
        self._place_cards(grid, self._vital_widgets, cols, len(_VITAL_CARDS))
        self._vitals_cols = cols
        outer.addWidget(host)
        return wrap

    def _vital_card(self, key: str, caption: str,
                    vital) -> QtWidgets.QFrame:
        card = ClickableCard("vitalCard")
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.clicked.connect(
            lambda _=None, k=key: self._open_vital_detail(k))
        card.setToolTip(f"Open {caption} details")
        card.setMinimumHeight(96)
        card.setMinimumWidth(120)
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Preferred)
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(15, 12, 15, 12)
        v.setSpacing(2)

        label = QtWidgets.QLabel(caption.upper())
        label.setObjectName("vitalLabel")
        v.addWidget(label)

        value_text = _fmt(vital.value) if vital.available else "—"
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        value = QtWidgets.QLabel(value_text)
        value.setObjectName("vitalValue")
        row.addWidget(value, 0, QtCore.Qt.AlignBottom)
        if vital.available and vital.unit:
            unit = QtWidgets.QLabel(vital.unit)
            unit.setObjectName("vitalUnit")
            row.addWidget(unit, 0, QtCore.Qt.AlignBottom)
        row.addStretch(1)
        v.addLayout(row)

        v.addStretch(1)

        card.setToolTip(self._vital_tooltip(vital))
        shown = f"{value_text} {vital.unit or ''}".strip()
        card.setAccessibleName(
            f"{caption}: {shown}" if vital.available
            else f"{caption}: not available")
        return card

    @staticmethod
    def _vital_tooltip(vital) -> str:
        if not vital.available:
            return f"No {vital.label.lower()} sample in this range."
        parts = [f"{vital.label}: {_fmt(vital.value)}"
                 + (f" {vital.unit}" if vital.unit else "")]
        if vital.at is not None:
            parts.append(f"latest {vital.at:%d %b, %H:%M}")
        return " · ".join(parts)

    def _heart_stress_section(self) -> QtWidgets.QWidget:
        start, end = self._range()
        wrap = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self._section_label("HEART & STRESS"))

        host = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.heart_cards = {}
        hr_card = self._reading_card(
            "Heart Rate", self.store.samples("heart_rate", start, end), "bpm")
        stress_card = self._reading_card(
            "Stress", self.store.samples("stress", start, end), None)
        self.heart_cards["heart_rate"] = hr_card
        self.heart_cards["stress"] = stress_card
        row.addWidget(hr_card, 1)
        row.addWidget(stress_card, 1)
        outer.addWidget(host)
        return wrap

    def _reading_card(self, caption: str, rows, unit: str | None
                      ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("statCard")
        card.setMinimumHeight(106)
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Preferred)
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(3)

        label = QtWidgets.QLabel(caption.upper())
        label.setObjectName("statLabel")
        v.addWidget(label)

        value_text = _fmt(rows[-1][1]) if rows else "—"
        value_row = QtWidgets.QHBoxLayout()
        value_row.setSpacing(4)
        value = QtWidgets.QLabel(value_text)
        value.setObjectName("statNum")
        value_row.addWidget(value, 0, QtCore.Qt.AlignBottom)
        if rows and unit:
            unit_label = QtWidgets.QLabel(unit)
            unit_label.setObjectName("tileUnit")
            value_row.addWidget(unit_label, 0, QtCore.Qt.AlignBottom)
        value_row.addStretch(1)
        v.addLayout(value_row)

        if rows:
            when = rows[-1][0]
            if when.date() == datetime.date.today():
                when_text = f"Last reading {when:%H:%M}"
            else:
                when_text = f"Last reading {when:%d %b, %H:%M}"
        else:
            when_text = "No readings in this range"
        sub = QtWidgets.QLabel(when_text)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        v.addWidget(sub)
        v.addStretch(1)

        shown = f"{value_text} {unit or ''}".strip() if rows else "not available"
        card.setAccessibleName(f"{caption}: {shown}; {when_text}")
        if not rows:
            card.setToolTip(
                f"No {caption.lower()} samples were stored in this range.")
        return card

    def _movement_section(self) -> QtWidgets.QWidget:
        start, end = self._range()
        wrap = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self._section_label("MOVEMENT & GOALS"))

        host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self._movement_grid = grid
        self._movement_widgets = []
        self.goal_cards = {}
        self.goal_progress = {}
        for channel, caption, unit, goal, goal_text in _MOVEMENT_GOALS:
            card, progress = self._goal_card(
                channel, caption, unit, goal, goal_text,
                self.store.samples(channel, start, end))
            self.goal_cards[channel] = card
            self.goal_progress[channel] = progress
            self._movement_widgets.append(card)

        cols = _movement_columns(self._content_width())
        self._place_cards(grid, self._movement_widgets, cols,
                          len(_MOVEMENT_GOALS))
        self._movement_cols = cols
        outer.addWidget(host)

        self.goals_note = QtWidgets.QLabel("Goals are defaults.")
        self.goals_note.setObjectName("muted")
        self.goals_note.setWordWrap(True)
        outer.addWidget(self.goals_note)
        return wrap

    def _goal_card(self, channel: str, caption: str, unit: str, goal: int,
                   goal_text: str, rows) -> tuple[QtWidgets.QFrame,
                                                  QtWidgets.QProgressBar]:
        card = QtWidgets.QFrame()
        card.setObjectName("goalCard")
        card.setMinimumHeight(116)
        card.setMinimumWidth(140)
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Preferred)
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(15, 12, 15, 12)
        v.setSpacing(4)

        label = QtWidgets.QLabel(caption.upper())
        label.setObjectName("goalLabel")
        v.addWidget(label)

        total = sum(value for _, value in rows) if rows else 0.0
        if not rows:
            shown_value, shown_unit = "—", ""
        elif channel == "distance_m":
            shown_value, shown_unit = f"{total / 1000.0:.1f}", "km"
        else:
            shown_value, shown_unit = f"{int(round(total)):,}", unit

        value_row = QtWidgets.QHBoxLayout()
        value_row.setSpacing(4)
        value = QtWidgets.QLabel(shown_value)
        value.setObjectName("goalValue")
        value_row.addWidget(value, 0, QtCore.Qt.AlignBottom)
        if shown_unit:
            unit_label = QtWidgets.QLabel(shown_unit)
            unit_label.setObjectName("goalUnit")
            value_row.addWidget(unit_label, 0, QtCore.Qt.AlignBottom)
        value_row.addStretch(1)
        v.addLayout(value_row)

        progress = QtWidgets.QProgressBar()
        progress.setTextVisible(False)
        progress.setRange(0, goal)
        progress.setValue(int(round(min(total, float(goal)))) if rows else 0)
        progress.setAccessibleName(f"{caption} progress")
        v.addWidget(progress)

        if rows:
            pct = (total / goal * 100.0) if goal else 0.0
            meta_text = f"{pct:.0f}% of {goal_text} · default goal"
        else:
            meta_text = f"Goal {goal_text} (default) · no data in this range"
        meta = QtWidgets.QLabel(meta_text)
        meta.setObjectName("goalMeta")
        meta.setWordWrap(True)
        v.addWidget(meta)
        v.addStretch(1)

        card.setAccessibleName(f"{caption}: {shown_value} {shown_unit}".strip()
                               + f"; {meta_text}")
        if not rows:
            card.setToolTip(
                f"No {caption.lower()} samples were stored in this range.")
        return card, progress

    @staticmethod
    def _place_cards(grid: QtWidgets.QGridLayout, widgets, cols: int,
                     max_cols: int) -> None:
        """Reflow ``widgets`` into ``cols`` columns of an existing grid."""
        while grid.count():
            grid.takeAt(0)
        for c in range(max_cols):
            grid.setColumnStretch(c, 0)
        for i, widget in enumerate(widgets):
            grid.addWidget(widget, i // cols, i % cols)
        for c in range(cols):
            grid.setColumnStretch(c, 1)

    def _relayout_vitals(self) -> None:
        grid = getattr(self, "_vitals_grid", None)
        widgets = getattr(self, "_vital_widgets", [])
        if grid is None or not widgets:
            return
        cols = _vitals_columns(self._content_width())
        if cols == self._vitals_cols and grid.count() == len(widgets):
            return
        self._place_cards(grid, widgets, cols, len(_VITAL_CARDS))
        self._vitals_cols = cols

    def _relayout_movement(self) -> None:
        grid = getattr(self, "_movement_grid", None)
        widgets = getattr(self, "_movement_widgets", [])
        if grid is None or not widgets:
            return
        cols = _movement_columns(self._content_width())
        if cols == self._movement_cols and grid.count() == len(widgets):
            return
        self._place_cards(grid, widgets, cols, len(_MOVEMENT_GOALS))
        self._movement_cols = cols

    def _value_block(self, metric: str, rows, link: bool = False,
                     centered: bool = False) -> QtWidgets.QWidget:
        unit = display_unit(metric)
        y = [v for _, v in rows]
        block = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(block)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        name = QtWidgets.QLabel(display_name(metric))
        name.setObjectName("heroName")
        top.addWidget(name)
        top.addStretch(1)
        if link:
            btn = QtWidgets.QPushButton("View details  ›")
            btn.setObjectName("linkBtn")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setMinimumHeight(30)
            btn.setAccessibleName(f"View {display_name(metric)} details")
            btn.clicked.connect(lambda _=False, m=metric: self._open_detail(m))
            top.addWidget(btn)
        v.addLayout(top)

        value_row = QtWidgets.QHBoxLayout()
        value_row.setSpacing(9)
        value = QtWidgets.QLabel(_fmt(y[-1]) if y else "—")
        value.setObjectName("heroValue")
        value_row.addWidget(value, 0, QtCore.Qt.AlignBottom)
        if unit and y:
            u = QtWidgets.QLabel(unit)
            u.setObjectName("heroUnit")
            value_row.addWidget(u, 0, QtCore.Qt.AlignBottom)
        value_row.addStretch(1)
        if rows:
            last = rows[-1][0]
            when = (f"Latest {last:%H:%M}" if last.date() == datetime.date.today()
                    else f"Latest {last:%d %b, %H:%M}")
            latest = QtWidgets.QLabel(when)
            latest.setObjectName("muted")
            value_row.addWidget(latest, 0, QtCore.Qt.AlignBottom)
        v.addLayout(value_row)
        v.addWidget(self._chips(y))
        if centered:
            for i in range(v.count()):
                item = v.itemAt(i)
                if item.layout():
                    item.layout().setAlignment(QtCore.Qt.AlignHCenter)
        return block

    def _overview_chart(self, metric: str, rows) -> QtWidgets.QWidget:
        chart = ChartView(interactive=False)
        chart.setAccessibleName(f"{display_name(metric)} chart")
        chart.set_data(rows, display_unit(metric))
        chart.activated.connect(lambda m=metric: self._open_detail(m))
        chart.setCursor(QtCore.Qt.PointingHandCursor)
        chart.setToolTip(f"Open {display_name(metric)} details")
        # The overview is a fixed-height summary: without this the PlotWidget's
        # Expanding policy swallows the page and pushes the cards off-screen.
        chart.setFixedHeight(190)
        chart.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Fixed)
        return self._chart_card(
            chart, f"{display_name(metric)} trend", height=190)

    def _chart_card(self, chart: QtWidgets.QWidget, caption: str,
                    height: int) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("card")
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(20, 15, 20, 14)
        v.setSpacing(10)
        head = QtWidgets.QHBoxLayout()
        cap = QtWidgets.QLabel(caption)
        cap.setObjectName("sectionLabel")
        head.addWidget(cap)
        head.addStretch(1)
        rng = QtWidgets.QLabel(self._range_short())
        rng.setObjectName("muted")
        head.addWidget(rng)
        v.addLayout(head)
        chart.setMinimumHeight(height)
        v.addWidget(chart, 1)
        _add_shadow(frame)
        return frame

    def _overview_stat_cards(self, rows) -> QtWidgets.QWidget:
        y = [v for _, v in rows]
        if y:
            specs = [
                ("Average", _fmt_avg(_mean(y)),
                 f"{len(y)} readings in this period"),
                ("Peak", _fmt(max(y)), "highest reading in this period"),
            ]
        else:
            specs = [("Average", "—", "no readings"), ("Peak", "—", "no readings")]
        return self._stat_cards_row(specs)

    def _maybe_sleep_card(self, start, end):
        """Last night's rest, used as the compact Highlights card.

        A session is shown only when the store has one.  The card reports the
        duration, bedtime/wake and RRI summary; the band has no vendor sleep
        score, so it says so rather than inventing one.
        """
        self.sleep_card = None
        self.sleep_summary = None
        fn = getattr(self.store, "sleep_sessions", None)
        if fn is None:
            return None
        try:
            sessions = fn(start, end)
        except Exception:  # noqa: BLE001
            return None
        sessions = [s for s in (sessions or []) if isinstance(s, dict)]
        if not sessions:
            return None
        session = max(
            sessions, key=lambda s: s.get("wake") or datetime.datetime.min)
        bedtime = session.get("bedtime")
        wake = session.get("wake")
        total_min = session.get("total_min")
        if not (bedtime and wake and total_min):
            return None

        summary = None
        try:
            summary = self.store.rest_summary(bedtime, wake)
        except Exception:  # noqa: BLE001
            pass

        frame = ClickableCard("card")
        frame.setCursor(QtCore.Qt.PointingHandCursor)
        frame.clicked.connect(
            lambda _=None, s=session: self._open_sleep_detail(s))
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(22, 16, 22, 16)
        v.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        cap = QtWidgets.QLabel("LAST NIGHT")
        cap.setObjectName("sectionLabel")
        head.addWidget(cap)
        head.addStretch(1)
        v.addLayout(head)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(16)
        value = QtWidgets.QLabel(_fmt_span(total_min * 60))
        value.setObjectName("statNum")
        self.sleep_summary = value
        row.addWidget(value, 0, QtCore.Qt.AlignBottom)
        sub = QtWidgets.QLabel(f"{bedtime:%H:%M} – {wake:%H:%M}")
        sub.setObjectName("muted")
        row.addWidget(sub, 0, QtCore.Qt.AlignBottom)
        row.addStretch(1)
        v.addLayout(row)

        if session.get("quality") is not None:
            v.addLayout(_chip_row((
                ("SCORE", f"{session['quality']}"),
                ("DEEP", _fmt_min(session.get("deep_min"))),
                ("LIGHT", _fmt_min(session.get("light_min"))),
                ("REM", _fmt_min(session.get("rem_min"))),
                ("AWAKE", _fmt_min(session.get("awake_min"))),
                ("EFFICIENCY", f"{session['efficiency']}%"
                 if session.get("efficiency") is not None else "—"),
            )))
        if summary is not None:
            v.addLayout(_chip_row((
                ("AVG", f"{summary.avg_hr:.0f} bpm" if summary.avg_hr else "—"),
                ("HRV", f"{summary.rmssd_ms:.0f} ms" if summary.rmssd_ms else "—"),
                ("COVERAGE", f"{summary.coverage_pct:.0f}%"
                 if summary.coverage_pct is not None else "—"),
            )))
        elif session.get("quality") is None:
            hint = QtWidgets.QLabel("No RRI beats recorded in this window.")
            hint.setObjectName("muted")
            v.addWidget(hint)

        _add_shadow(frame)
        self.sleep_card = frame
        frame.setAccessibleName(
            f"Last night: {_fmt_span(total_min * 60)} from {bedtime:%H:%M} "
            f"to {wake:%H:%M}")
        return frame

    def _workouts_section(self):
        """Where workouts will appear; says so plainly when none arrived."""
        fn = getattr(self.store, "workouts", None)
        if fn is None:
            return None
        try:
            workouts = fn()
        except Exception:  # noqa: BLE001
            return None

        wrap = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        label = QtWidgets.QLabel("WORKOUTS")
        label.setObjectName("sectionLabel")
        outer.addWidget(label)

        card = QtWidgets.QFrame()
        card.setObjectName("card")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(4)
        if workouts:
            for workout in workouts[:6]:
                when = workout.get("time")
                row = QtWidgets.QHBoxLayout()
                name = QtWidgets.QLabel(workout.get("id") or "Workout")
                name.setObjectName("chipValue")
                row.addWidget(name)
                row.addStretch(1)
                meta = QtWidgets.QLabel(_workout_meta(workout))
                meta.setObjectName("muted")
                row.addWidget(meta)
                v.addLayout(row)
        else:
            title = QtWidgets.QLabel("No workouts received")
            title.setObjectName("chipValue")
            v.addWidget(title)
            hint = QtWidgets.QLabel(
                "The band holds a workout only until one app syncs it. Keep "
                "the phone's Bluetooth off after a walk, run or recorded "
                "workout, then sync here.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            v.addWidget(hint)
        _add_shadow(card)
        outer.addWidget(card)
        return wrap

    def _stat_cards_row(self, specs) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        for spec in specs:
            h.addWidget(self._stat_card(*spec), 1)
        return row

    def _stat_card(self, caption: str, value: str, sub: str) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setObjectName("statCard")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(20, 15, 20, 15)
        v.setSpacing(3)
        lab = QtWidgets.QLabel(caption.upper())
        lab.setObjectName("statLabel")
        val = QtWidgets.QLabel(value)
        val.setObjectName("statNum")
        sublab = QtWidgets.QLabel(sub)
        sublab.setObjectName("muted")
        sublab.setWordWrap(True)
        v.addWidget(lab)
        v.addWidget(val)
        v.addWidget(sublab)
        _add_shadow(card)
        return card

    def _tiles_section(self, metrics, start, end) -> QtWidgets.QWidget:
        """A readable channel list; no interpolated miniature curves for codes."""
        host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.tiles_grid = grid
        for i, metric in enumerate(metrics):
            row = self._tile(metric, start, end)
            self._tile_widgets.append(row)
            grid.addWidget(row, i, 0)
        grid.setColumnStretch(0, 1)
        self._tile_cols = 1
        return host

    def _tile(self, metric: str, start, end) -> QtWidgets.QWidget:
        rows = sorted(self.store.samples(metric, start, end), key=lambda r: r[0])
        unit = display_unit(metric)
        card = ClickableCard("tile")
        card.setAccessibleName(f"{display_name(metric)} channel, open details")
        card.setFocusPolicy(QtCore.Qt.StrongFocus)
        card.clicked.connect(lambda _=None, m=metric: self._open_detail(m))
        layout = QtWidgets.QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(16)
        details = QtWidgets.QVBoxLayout()
        name = QtWidgets.QLabel(display_name(metric))
        name.setObjectName("tileName")
        name.setWordWrap(True)
        details.addWidget(name)
        if rows:
            when, value = rows[-1]
            summary = f"Latest recorded · {when:%d %b, %H:%M} · {len(rows):,} readings"
        else:
            summary = "No readings in this period"
        hint = QtWidgets.QLabel(summary)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        details.addWidget(hint)
        layout.addLayout(details, 1)
        value_text = _fmt(rows[-1][1]) if rows else "—"
        if unit and rows:
            value_text += f" {unit}"
        value_label = QtWidgets.QLabel(value_text)
        value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(value_label)
        arrow = QtWidgets.QLabel("›")
        arrow.setObjectName("chevron")
        layout.addWidget(arrow)
        return card

    def _chips(self, y) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 0)
        h.setSpacing(30)

        def chip(label, value):
            c = QtWidgets.QVBoxLayout()
            c.setSpacing(0)
            n = QtWidgets.QLabel(value)
            n.setObjectName("chipValue")
            lab = QtWidgets.QLabel(label.upper())
            lab.setObjectName("statLabel")
            c.addWidget(n)
            c.addWidget(lab)
            return c

        if y:
            h.addLayout(chip("average", _fmt_avg(_mean(y))))
            h.addLayout(chip("min", _fmt(min(y))))
            h.addLayout(chip("max", _fmt(max(y))))
            h.addLayout(chip("readings", str(len(y))))
        h.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # detail
    # ------------------------------------------------------------------
    def _build_detail(self) -> QtWidgets.QWidget:
        scroll, _inner, lay = self._make_page()

        header = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.back_btn = QtWidgets.QPushButton("‹  Back")
        self.back_btn.setObjectName("backBtn")
        self.back_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.back_btn.setFixedWidth(110)
        self.back_btn.setMinimumHeight(36)
        self.back_btn.setAccessibleName("Back to overview")
        self.back_btn.clicked.connect(self._show_overview)
        h.addWidget(self.back_btn, 0, QtCore.Qt.AlignLeft)
        h.addStretch(1)
        center = QtWidgets.QVBoxLayout()
        center.setSpacing(1)
        self.metric_title = QtWidgets.QLabel("—")
        self.metric_title.setObjectName("metricTitle")
        self.metric_title.setAlignment(QtCore.Qt.AlignCenter)
        self.detail_range = QtWidgets.QLabel("")
        self.detail_range.setObjectName("rangeLabel")
        self.detail_range.setAlignment(QtCore.Qt.AlignCenter)
        center.addWidget(self.metric_title)
        center.addWidget(self.detail_range)
        h.addLayout(center)
        h.addStretch(1)
        spacer = QtWidgets.QWidget()
        spacer.setFixedWidth(110)
        h.addWidget(spacer)
        lay.addWidget(header)

        self.detail_value_block = QtWidgets.QWidget()
        vb = QtWidgets.QVBoxLayout(self.detail_value_block)
        vb.setContentsMargins(0, 4, 0, 0)
        vb.setSpacing(8)
        value_row = QtWidgets.QHBoxLayout()
        value_row.setSpacing(9)
        self.detail_value = QtWidgets.QLabel("—")
        self.detail_value.setObjectName("heroValue")
        self.detail_unit = QtWidgets.QLabel("")
        self.detail_unit.setObjectName("heroUnit")
        value_row.addStretch(1)
        value_row.addWidget(self.detail_value, 0, QtCore.Qt.AlignBottom)
        value_row.addWidget(self.detail_unit, 0, QtCore.Qt.AlignBottom)
        value_row.addStretch(1)
        vb.addLayout(value_row)
        self.detail_latest = QtWidgets.QLabel("")
        self.detail_latest.setObjectName("muted")
        self.detail_latest.setAlignment(QtCore.Qt.AlignCenter)
        vb.addWidget(self.detail_latest)
        lay.addWidget(self.detail_value_block)

        stat_bar = QtWidgets.QFrame()
        stat_bar.setObjectName("card")
        sl = QtWidgets.QHBoxLayout(stat_bar)
        sl.setContentsMargins(22, 14, 22, 14)
        sl.setSpacing(0)
        self.stat_row = QtWidgets.QHBoxLayout()
        self.stat_row.setContentsMargins(0, 0, 0, 0)
        self.stat_row.setSpacing(30)
        self.stat_row.addStretch(1)
        sl.addLayout(self.stat_row)
        self.detail_stat_bar = stat_bar
        lay.addWidget(stat_bar)

        self.detail_chart_stack = QtWidgets.QStackedWidget()
        self.plot = ChartView(interactive=True)
        self.plot.setAccessibleName("Metric chart")
        self.detail_chart_caption = "Trend"
        chart_frame = self._chart_card(self.plot, self.detail_chart_caption,
                                       height=270)
        self.detail_chart_caption_label = next(
            (label for label in chart_frame.findChildren(QtWidgets.QLabel)
             if label.objectName() == "sectionLabel"), None)
        self.detail_chart_stack.addWidget(chart_frame)   # index 0

        empty = QtWidgets.QFrame()
        empty.setObjectName("card")
        empty.setMinimumHeight(270)
        ev = QtWidgets.QVBoxLayout(empty)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.addStretch(1)
        no = QtWidgets.QLabel("No readings in this range")
        no.setObjectName("emptyTitle")
        no.setAlignment(QtCore.Qt.AlignCenter)
        hint = QtWidgets.QLabel("Try a wider period or sync the band.")
        hint.setObjectName("hint")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        ev.addWidget(no)
        ev.addWidget(hint)
        ev.addStretch(1)
        self.detail_chart_stack.addWidget(empty)         # index 1
        lay.addWidget(self.detail_chart_stack, 1)

        self.detail_stats = QtWidgets.QWidget()
        self.detail_stats_layout = QtWidgets.QHBoxLayout(self.detail_stats)
        self.detail_stats_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_stats_layout.setSpacing(16)
        lay.addWidget(self.detail_stats)

        exp = QtWidgets.QHBoxLayout()
        exp.setSpacing(10)
        cap = QtWidgets.QLabel("EXPORT THIS RANGE")
        cap.setObjectName("sectionLabel")
        exp.addWidget(cap)
        exp.addStretch(1)
        self.csv_btn = QtWidgets.QPushButton("Export CSV")
        self.json_btn = QtWidgets.QPushButton("Export JSON")
        self.csv_btn.setMinimumHeight(36)
        self.json_btn.setMinimumHeight(36)
        self.csv_btn.setEnabled(False)
        self.json_btn.setEnabled(False)
        self.csv_btn.setAccessibleName("Export CSV")
        self.json_btn.setAccessibleName("Export JSON")
        self.csv_btn.clicked.connect(lambda: self._export("csv"))
        self.json_btn.clicked.connect(lambda: self._export("json"))
        exp.addWidget(self.csv_btn)
        exp.addWidget(self.json_btn)
        lay.addLayout(exp)
        lay.addStretch(1)
        return scroll

    def _open_detail(self, metric: str) -> None:
        self._detail_metric = metric
        self._plot_detail()
        self.stack.setCurrentIndex(1)
        self.back_btn.setFocus(QtCore.Qt.OtherFocusReason)

    def _show_overview(self) -> None:
        self._show_home(self._home)

    def _show_home(self, key: str) -> None:
        """Switch between the Today page and the all-metrics overview."""
        self._home = key
        self.nav_tabs[key].setChecked(True)
        if key == "today":
            self.stack.setCurrentWidget(self.today)
            self.today.refresh()
        elif key == "settings":
            self.stack.setCurrentWidget(self.settings_page)
        else:
            self.stack.setCurrentIndex(0)
            QtCore.QTimer.singleShot(0, self._relayout_tiles)
        self._show_day_nav()

    def _plot_detail(self) -> None:
        metric = self._detail_metric
        if not metric:
            return
        unit = display_unit(metric)
        self.metric_title.setText(display_name(metric))
        self.detail_range.setText(self._range_caption())
        self.detail_value.setAccessibleName(f"Latest {display_name(metric)}")

        start, end = self._range()
        rows = self.store.samples(metric, start, end)
        y = [v for _, v in rows]
        self._clear_stats()
        has_rows = bool(rows)
        # In the empty case the chart card carries the message; a giant em
        # dash and a "READINGS 0" bar would just add noise.
        self.detail_value_block.setVisible(has_rows)
        self.detail_stat_bar.setVisible(has_rows)
        self.detail_stats.setVisible(has_rows)

        if rows:
            self.detail_value.setText(_fmt(y[-1]))
            self.detail_unit.setText(unit or "")
            last = rows[-1][0]
            when = (f"Latest reading {last:%H:%M}" if
                    last.date() == datetime.date.today()
                    else f"Latest reading {last:%d %b, %H:%M}")
            self.detail_latest.setText(when)
            self._add_stat("Current", _fmt(y[-1]), True)
            self._add_stat("Average", _fmt_avg(_mean(y)), False)
            self._add_stat("Min", _fmt(min(y)), False)
            self._add_stat("Max", _fmt(max(y)), False)
            self._add_stat("Readings", str(len(y)), False)
        else:
            self.detail_value.setText("—")
            self.detail_unit.setText("")
            self.detail_latest.setText("No readings in this range")

        self.csv_btn.setEnabled(has_rows)
        self.json_btn.setEnabled(has_rows)
        self.plot.setAccessibleName(f"{display_name(metric)} chart")
        self.plot.set_data(rows, unit)
        self.detail_chart_stack.setCurrentIndex(0 if has_rows else 1)
        self._fill_detail_stat_cards(rows)
        if self.detail_chart_caption_label is not None:
            self.detail_chart_caption_label.setText(
                f"{display_name(metric)} trend")

    def _fill_detail_stat_cards(self, rows) -> None:
        self._clear_layout(self.detail_stats_layout)
        y = [v for _, v in rows]
        if rows:
            span = (rows[-1][0] - rows[0][0]).total_seconds()
            specs = [
                ("Average", _fmt_avg(_mean(y)), "across this period"),
                ("Time span", _fmt_span(span),
                 "between first and last reading"),
            ]
        else:
            specs = [("Average", "—", "no readings"),
                     ("Time span", "—", "no readings")]
        for spec in specs:
            self.detail_stats_layout.addWidget(self._stat_card(*spec), 1)

    # -- sleep detail ----------------------------------------------------
    def _build_sleep_detail(self) -> QtWidgets.QWidget:
        scroll, _inner, lay = self._make_page()

        header = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        back = QtWidgets.QPushButton("‹  Back")
        back.setObjectName("backBtn")
        back.setCursor(QtCore.Qt.PointingHandCursor)
        back.setFixedWidth(110)
        back.setMinimumHeight(36)
        back.setAccessibleName("Back to overview")
        back.clicked.connect(self._show_overview)
        h.addWidget(back, 0, QtCore.Qt.AlignLeft)
        h.addStretch(1)
        center = QtWidgets.QVBoxLayout()
        center.setSpacing(1)
        self.sleep_detail_title = QtWidgets.QLabel("Last Night")
        self.sleep_detail_title.setObjectName("metricTitle")
        self.sleep_detail_title.setAlignment(QtCore.Qt.AlignCenter)
        self.sleep_detail_sub = QtWidgets.QLabel("")
        self.sleep_detail_sub.setObjectName("rangeLabel")
        self.sleep_detail_sub.setAlignment(QtCore.Qt.AlignCenter)
        center.addWidget(self.sleep_detail_title)
        center.addWidget(self.sleep_detail_sub)
        h.addLayout(center)
        h.addStretch(1)
        spacer = QtWidgets.QWidget()
        spacer.setFixedWidth(110)
        h.addWidget(spacer)
        lay.addWidget(header)

        self.sleep_detail_value = QtWidgets.QLabel("—")
        self.sleep_detail_value.setObjectName("heroValue")
        self.sleep_detail_value.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.sleep_detail_value)

        stat_bar = QtWidgets.QFrame()
        stat_bar.setObjectName("card")
        self.sleep_stat_row = QtWidgets.QHBoxLayout(stat_bar)
        self.sleep_stat_row.setContentsMargins(22, 14, 22, 14)
        self.sleep_stat_row.setSpacing(30)
        self.sleep_stat_row.addStretch(1)
        lay.addWidget(stat_bar)

        self.sleep_chart = ChartView(interactive=True)
        self.sleep_chart.setAccessibleName("Heart rate across the sleep window")
        lay.addWidget(self._chart_card(
            self.sleep_chart, "Heart rate across the rest window", height=280))

        self.sleep_stages_host = QtWidgets.QWidget()
        self.sleep_stages_layout = QtWidgets.QVBoxLayout(self.sleep_stages_host)
        self.sleep_stages_layout.setContentsMargins(0, 0, 0, 0)
        self.sleep_stages_layout.setSpacing(8)
        lay.addWidget(self.sleep_stages_host)

        self.sleep_insight_host = QtWidgets.QWidget()
        self.sleep_insight_layout = QtWidgets.QVBoxLayout(self.sleep_insight_host)
        self.sleep_insight_layout.setContentsMargins(0, 0, 0, 0)
        self.sleep_insight_layout.setSpacing(4)
        lay.addWidget(self.sleep_insight_host)

        note = QtWidgets.QLabel("Recorded overnight rest window.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return scroll

    # -- menu-bar icon guide ---------------------------------------------
    def _build_guide(self) -> QtWidgets.QWidget:
        scroll, _inner, lay = self._make_page()
        lay.setSpacing(18)

        self.guide_back_btn = QtWidgets.QPushButton("‹  Back")
        self.guide_back_btn.setObjectName("backBtn")
        self.guide_back_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.guide_back_btn.setFixedWidth(96)
        self.guide_back_btn.setMinimumHeight(34)
        self.guide_back_btn.setAccessibleName("Back to overview")
        self.guide_back_btn.clicked.connect(self._show_overview)
        lay.addWidget(self.guide_back_btn, 0, QtCore.Qt.AlignLeft)

        title = QtWidgets.QLabel("Menu-bar icon guide")
        title.setObjectName("guideTitle")
        sub = QtWidgets.QLabel(
            "The Live HR app sits in the macOS menu bar and shows the band's "
            "state as a heart and a value.")
        sub.setObjectName("guideLead")
        sub.setWordWrap(True)
        head = QtWidgets.QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(title)
        head.addWidget(sub)
        lay.addLayout(head)

        states = [
            ("♥ 72", True,
             "Connected — heart rate live",
             "The band is connected and a heart rate is being read (worn). "
             "The number is the current beats per minute."),
            ("♡ ~", False,
             "Connected — no heart rate",
             "The band is connected but no heart rate is available: it may be "
             "off the wrist, removed, or the sensor has no reading yet."),
            ("♡ —", False,
             "Not connected",
             "The band is not connected: asleep or off its charger, out of "
             "range, or the phone is holding the BLE link."),
        ]
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)
        for glyph, on, state, detail in states:
            row.addWidget(self._guide_state_card(glyph, on, state, detail), 1)
        lay.addLayout(row)

        notes = QtWidgets.QFrame()
        notes.setObjectName("guideCard")
        nv = QtWidgets.QVBoxLayout(notes)
        nv.setContentsMargins(22, 18, 22, 18)
        nv.setSpacing(12)
        heading = QtWidgets.QLabel("Good to know")
        heading.setObjectName("guideState")
        nv.addWidget(heading)
        for i, line in enumerate((
            "The number is bpm — beats per minute.",
            "The app tries the realtime stream first; when that is unavailable "
            "or goes silent, it polls the confirmed 113 heart-rate series.",
            "A failed connection retries every 60 seconds.",
            "Run the menu-bar app with --lock-on-away to lock the Mac after "
            "the band disconnects.",
        ), 1):
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(12)
            num = QtWidgets.QLabel(str(i))
            num.setObjectName("guideNum")
            num.setAlignment(QtCore.Qt.AlignCenter)
            label = QtWidgets.QLabel(line)
            label.setObjectName("guideBody")
            label.setWordWrap(True)
            r.addWidget(num, 0, QtCore.Qt.AlignTop)
            r.addWidget(label, 1)
            nv.addLayout(r)
        lay.addWidget(notes)
        lay.addStretch(1)
        return scroll

    def _guide_state_card(self, glyph: str, on: bool, state: str,
                          detail: str) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setObjectName("guideCard")
        card.setAccessibleName(f"{state}: {glyph}")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(22, 20, 22, 22)
        v.setSpacing(10)
        preview = QtWidgets.QLabel(glyph)
        preview.setObjectName("guidePill")
        preview.setProperty("on", "true" if on else "false")
        preview.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(preview, 0, QtCore.Qt.AlignLeft)
        v.addSpacing(4)
        state_label = QtWidgets.QLabel(state)
        state_label.setObjectName("guideState")
        state_label.setWordWrap(True)
        body = QtWidgets.QLabel(detail)
        body.setObjectName("guideBody")
        body.setWordWrap(True)
        v.addWidget(state_label)
        v.addWidget(body)
        v.addStretch(1)
        return card

    def _build_settings(self) -> QtWidgets.QWidget:
        """Device details, pairing help, live menu-bar app, export, guide."""
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        host = QtWidgets.QWidget()
        scroll.setWidget(host)
        outer = QtWidgets.QVBoxLayout(host)
        self._settings_layout = outer
        outer.setSpacing(18)
        title = QtWidgets.QLabel("Settings")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        def section(name: str, note: str = "") -> QtWidgets.QVBoxLayout:
            card = QtWidgets.QFrame()
            card.setObjectName("settingsCard")
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(24, 20, 24, 20)
            v.setSpacing(10)
            head = QtWidgets.QLabel(name)
            head.setObjectName("settingsHead")
            v.addWidget(head)
            if note:
                n = QtWidgets.QLabel(note)
                n.setObjectName("settingsNote")
                n.setWordWrap(True)
                v.addWidget(n)
            outer.addWidget(card)
            return v

        band = section("Band")
        for caption, label in (("Model", self.dev_model), ("Firmware", self.dev_fw),
                               ("Last sync", self.dev_sync)):
            row = QtWidgets.QHBoxLayout()
            cap = QtWidgets.QLabel(caption)
            cap.setObjectName("settingsNote")
            row.addWidget(cap)
            row.addStretch(1)
            row.addWidget(label)
            band.addLayout(row)

        pair = section("Connecting your band", (
            "There is no pairing step. The app finds the band by name and "
            "connects on its own whenever it is open."))
        for i, step in enumerate((
                "Turn your phone's Bluetooth off, or close NoiseFit — the band "
                "talks to one device at a time.",
                "Keep the band within a few metres of this PC, on your wrist or "
                "on the charger. Either works.",
                "If it is not found within a minute, set it on the charger for a "
                "few seconds; that wakes its Bluetooth, then you can wear it "
                "again.",
                "Press Sync band: it stops anything else in this app that is "
                "holding the band, then syncs.",
                "Do not pair it in Windows Bluetooth settings; the app connects "
                "directly."), 1):
            lab = QtWidgets.QLabel(f"{i}.  {step}")
            lab.setObjectName("settingsStep")
            lab.setWordWrap(True)
            pair.addWidget(lab)

        live = section("Live heart rate in the menu bar", (
            "Runs as a separate small app. While it is on, this window pauses "
            "its own band connection, since the band accepts one connection."))
        live.addWidget(self.live_btn, 0, QtCore.Qt.AlignLeft)

        export = section("Export", "Save stored readings as CSV or JSON.")
        export.addWidget(self.export_menu_btn, 0, QtCore.Qt.AlignLeft)

        data = section("Your data", (
            "Everything the band sends is saved on this PC in one SQLite file "
            "and kept across restarts; nothing is uploaded. The band itself "
            "only holds data until it is synced, so older days appear here "
            "only from the first sync on."))
        path = QtWidgets.QLabel(self.store.path)
        path.setObjectName("settingValue")
        path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        path.setWordWrap(True)
        data.addWidget(path)
        open_btn = QtWidgets.QPushButton("Open folder")
        open_btn.setCursor(QtCore.Qt.PointingHandCursor)
        open_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(os.path.dirname(self.store.path))))
        data.addWidget(open_btn, 0, QtCore.Qt.AlignLeft)

        guide = section("Menu-bar icon guide", "What each heart icon state means.")
        guide.addWidget(self.guide_btn, 0, QtCore.Qt.AlignLeft)
        outer.addStretch(1)
        return scroll

    def _crossfade_from_snapshot(self) -> None:
        """Freeze the current look in an overlay, then fade it away."""
        if not self.isVisible():
            return
        shot = QtWidgets.QLabel(self)
        shot.setPixmap(self.grab())
        shot.setGeometry(self.rect())
        fx = QtWidgets.QGraphicsOpacityEffect(shot)
        shot.setGraphicsEffect(fx)
        shot.show()
        shot.raise_()
        fade = QtCore.QPropertyAnimation(fx, b"opacity", shot)
        fade.setDuration(420)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        fade.finished.connect(shot.deleteLater)
        QtCore.QTimer.singleShot(0, fade.start)

    def _show_guide(self) -> None:
        self.stack.setCurrentWidget(self.guide_page)
        self.guide_back_btn.setFocus(QtCore.Qt.OtherFocusReason)

    def _open_sleep_detail(self, session: dict) -> None:
        bedtime = session.get("bedtime")
        wake = session.get("wake")
        total_min = session.get("total_min")
        if not (bedtime and wake and total_min):
            return
        self._sleep_session = session
        self.sleep_detail_sub.setText(
            f"{bedtime:%A, %d %B} · {bedtime:%H:%M} – {wake:%H:%M}")
        self.sleep_detail_value.setText(_fmt_span(total_min * 60))

        summary = None
        try:
            summary = self.store.rest_summary(bedtime, wake)
        except Exception:  # noqa: BLE001
            pass

        self._clear_layout(self.sleep_stat_row)
        specs = [("Duration", _fmt_span(total_min * 60), True)]
        if summary is not None:
            specs += [
                ("Avg HR", f"{summary.avg_hr:.0f} bpm" if summary.avg_hr else "—",
                 False),
                ("HRV (RMSSD)",
                 f"{summary.rmssd_ms:.0f} ms" if summary.rmssd_ms else "—", False),
                ("Coverage",
                 f"{summary.coverage_pct:.0f}%" if summary.coverage_pct is not None
                 else "—", False),
                ("Beats", f"{summary.beats:,}", False),
            ]
        for label, value, accent in specs:
            self.sleep_stat_row.addWidget(self._sleep_stat_cell(
                label, value, accent))
        self.sleep_stat_row.addStretch(1)

        try:
            rows = analytics.hr_series(self.store.rri(bedtime, wake), bucket_s=60)
        except Exception:  # noqa: BLE001
            rows = []
        self.sleep_chart.set_data(rows, "bpm")
        self._render_sleep_stages(session, wake)

        self._clear_layout(self.sleep_insight_layout)
        if summary is not None:
            cap = QtWidgets.QLabel("WHAT THE DATA SAYS")
            cap.setObjectName("sectionLabel")
            self.sleep_insight_layout.addWidget(cap)
            for line in analytics.insights(summary):
                note = QtWidgets.QLabel(line)
                note.setObjectName("muted")
                note.setWordWrap(True)
                self.sleep_insight_layout.addWidget(note)

        self.stack.setCurrentIndex(2)

    def _sleep_stat_cell(self, label: str, value: str,
                         accent: bool) -> QtWidgets.QWidget:
        cell = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        num = QtWidgets.QLabel(value)
        num.setObjectName("statNumAccent" if accent else "statNum")
        lab = QtWidgets.QLabel(label.upper())
        lab.setObjectName("statLabel")
        v.addWidget(num)
        v.addWidget(lab)
        return cell

    def _render_sleep_stages(self, session: dict, wake: datetime.datetime) -> None:
        self._clear_layout(self.sleep_stages_layout)
        cap = QtWidgets.QLabel("SLEEP STAGES")
        cap.setObjectName("sectionLabel")
        self.sleep_stages_layout.addWidget(cap)

        session_id = session.get("id")
        stages = []
        if session_id is not None:
            try:
                stages = self.store.sleep_stages(session_id)
            except Exception:  # noqa: BLE001
                stages = []

        if not stages:
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(20, 14, 20, 14)
            v.setSpacing(4)
            title = QtWidgets.QLabel("Not available")
            title.setObjectName("chipValue")
            v.addWidget(title)
            hint = QtWidgets.QLabel(
                "The band has not sent a staged sleep file, so light, deep and "
                "REM cannot be shown. Only RRI beats arrived for this window; "
                "inventing stages would be worse than showing none.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            v.addWidget(hint)
            self.sleep_stages_layout.addWidget(card)
            return

        colors = {
            0: theme.FG_MUTED,      # not sleep
            1: "#BFBFBA",           # unknown
            2: theme.FG,            # deep
            3: theme.ACCENT_HOVER,  # light
            4: theme.FOCUS,         # rem
            5: theme.ACCENT,        # awake
        }
        bar = QtWidgets.QFrame()
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(1)
        for i, stage in enumerate(stages):
            start = stage.get("ts")
            end = stages[i + 1]["ts"] if i + 1 < len(stages) else wake
            if not start or not end or end <= start:
                continue
            minutes = max(1, int((end - start).total_seconds() // 60))
            seg = QtWidgets.QFrame()
            seg.setFixedHeight(18)
            seg.setStyleSheet(
                f"background:{colors.get(stage.get('stage'), theme.FG_MUTED)};"
                "border-radius:3px;")
            seg.setToolTip(
                f"{STAGE_NAMES.get(stage.get('stage'), 'stage')} · {minutes} min")
            h.addWidget(seg, minutes)
        self.sleep_stages_layout.addWidget(bar)

        legend = QtWidgets.QHBoxLayout()
        legend.setSpacing(16)
        for code, name in sorted(STAGE_NAMES.items()):
            dot = QtWidgets.QLabel("●")
            dot.setStyleSheet(f"color:{colors.get(code, theme.FG_MUTED)};")
            label = QtWidgets.QLabel(name.replace("_", " ").title())
            label.setObjectName("muted")
            legend.addWidget(dot)
            legend.addWidget(label)
        legend.addStretch(1)
        self.sleep_stages_layout.addLayout(legend)

    # -- vital detail ----------------------------------------------------
    def _build_vital_detail(self) -> QtWidgets.QWidget:
        scroll, _inner, lay = self._make_page()

        header = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        back = QtWidgets.QPushButton("‹  Back")
        back.setObjectName("backBtn")
        back.setCursor(QtCore.Qt.PointingHandCursor)
        back.setFixedWidth(110)
        back.setMinimumHeight(36)
        back.setAccessibleName("Back to overview")
        back.clicked.connect(self._show_overview)
        h.addWidget(back, 0, QtCore.Qt.AlignLeft)
        h.addStretch(1)
        center = QtWidgets.QVBoxLayout()
        center.setSpacing(1)
        self.vital_detail_title = QtWidgets.QLabel("—")
        self.vital_detail_title.setObjectName("metricTitle")
        self.vital_detail_title.setAlignment(QtCore.Qt.AlignCenter)
        self.vital_detail_sub = QtWidgets.QLabel("")
        self.vital_detail_sub.setObjectName("rangeLabel")
        self.vital_detail_sub.setAlignment(QtCore.Qt.AlignCenter)
        center.addWidget(self.vital_detail_title)
        center.addWidget(self.vital_detail_sub)
        h.addLayout(center)
        h.addStretch(1)
        spacer = QtWidgets.QWidget()
        spacer.setFixedWidth(110)
        h.addWidget(spacer)
        lay.addWidget(header)

        value_row = QtWidgets.QHBoxLayout()
        value_row.setSpacing(8)
        value_row.addStretch(1)
        self.vital_detail_value = QtWidgets.QLabel("—")
        self.vital_detail_value.setObjectName("heroValue")
        self.vital_detail_unit = QtWidgets.QLabel("")
        self.vital_detail_unit.setObjectName("heroUnit")
        value_row.addWidget(self.vital_detail_value, 0, QtCore.Qt.AlignBottom)
        value_row.addWidget(self.vital_detail_unit, 0, QtCore.Qt.AlignBottom)
        value_row.addStretch(1)
        lay.addLayout(value_row)

        stat_bar = QtWidgets.QFrame()
        stat_bar.setObjectName("card")
        self.vital_stat_row = QtWidgets.QHBoxLayout(stat_bar)
        self.vital_stat_row.setContentsMargins(22, 14, 22, 14)
        self.vital_stat_row.setSpacing(30)
        self.vital_stat_row.addStretch(1)
        lay.addWidget(stat_bar)

        self.vital_chart = ChartView(interactive=True)
        lay.addWidget(self._chart_card(self.vital_chart, "Trend", height=270))

        meaning_card = QtWidgets.QFrame()
        meaning_card.setObjectName("card")
        mv = QtWidgets.QVBoxLayout(meaning_card)
        mv.setContentsMargins(22, 16, 22, 16)
        mv.setSpacing(6)
        mv.addWidget(self._section_label("WHAT IT MEANS"))
        self.vital_meaning = QtWidgets.QLabel("")
        self.vital_meaning.setObjectName("bodyText")
        self.vital_meaning.setWordWrap(True)
        mv.addWidget(self.vital_meaning)
        lay.addWidget(meaning_card)

        typical_card = QtWidgets.QFrame()
        typical_card.setObjectName("card")
        tv = QtWidgets.QVBoxLayout(typical_card)
        tv.setContentsMargins(22, 16, 22, 16)
        tv.setSpacing(6)
        tv.addWidget(self._section_label("USUAL RANGE"))
        self.vital_typical = QtWidgets.QLabel("")
        self.vital_typical.setObjectName("bodyText")
        self.vital_typical.setWordWrap(True)
        tv.addWidget(self.vital_typical)
        lay.addWidget(typical_card)
        lay.addStretch(1)
        return scroll

    def _open_vital_detail(self, key: str) -> None:
        guide = VITAL_GUIDE.get(key)
        if guide is None:
            return
        start, end = self._range()
        try:
            vital = analytics.vitals(self.store, start, end).get(key)
        except Exception:  # noqa: BLE001
            vital = None
        rows = self.store.samples(guide["metric"], start, end)

        self.vital_detail_title.setText(guide["title"])
        self.vital_detail_sub.setText(self._range_caption())
        self.vital_detail_value.setText(
            _fmt(vital.value) if vital is not None and vital.available else "—")
        self.vital_detail_unit.setText(
            (vital.unit or "") if vital is not None and vital.available else "")
        self.vital_meaning.setText(guide["meaning"])
        self.vital_typical.setText(guide["typical"])

        self._clear_layout(self.vital_stat_row)
        y = [value for _, value in rows]
        stats = [("Current", self.vital_detail_value.text(), True)]
        if y:
            stats += [
                ("Average", _fmt_avg(_mean(y)), False),
                ("Min", _fmt(min(y)), False),
                ("Max", _fmt(max(y)), False),
                ("Readings", str(len(y)), False),
            ]
        for label, value, accent in stats:
            self.vital_stat_row.addWidget(
                self._sleep_stat_cell(label, value, accent))
        self.vital_stat_row.addStretch(1)

        self.vital_chart.set_data(rows, vital.unit if vital else None)
        self.stack.setCurrentIndex(4)

    # -- stat row helpers ------------------------------------------------
    def _clear_stats(self) -> None:
        while self.stat_row.count() > 1:  # keep the trailing stretch
            item = self.stat_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _add_stat(self, label: str, value: str, accent: bool) -> None:
        cell = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        num = QtWidgets.QLabel(value)
        num.setObjectName("statNumAccent" if accent else "statNum")
        lab = QtWidgets.QLabel(label.upper())
        lab.setObjectName("statLabel")
        cl.addWidget(num)
        cl.addWidget(lab)
        self.stat_row.insertWidget(self.stat_row.count() - 1, cell)

    @staticmethod
    def _clear_layout(lay) -> None:
        # Detach immediately, then delete. `deleteLater` alone leaves the old
        # widgets painted on top of the rebuilt layout until the event loop
        # processes the deferred delete, which shows up as ghost cards when a
        # range is switched in the same tick.
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                MainWindow._clear_layout(item.layout())

    # ------------------------------------------------------------------
    # data / lifecycle
    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._refresh_device()
        self._choose_default_range()
        self._sync_dates_to_bounds()
        self._refresh_header()
        self._refresh_status_hint()
        self._populate_overview()

    def _choose_default_range(self) -> None:
        lo, hi = self.store.sample_bounds()
        today = datetime.date.today()
        if lo is None:
            mode = "today"
        elif lo.date() <= today <= hi.date():
            mode = "today"
        else:
            mode = "all"
        self._range_mode = mode
        index = self.range_combo.findData(mode)
        self.range_combo.blockSignals(True)
        self.range_combo.setCurrentIndex(index)
        self.range_combo.blockSignals(False)
        self.custom_row.hide()
        self._show_day_nav()

    def _default_day(self) -> None:
        """When entering Day mode, fall back to the latest stored day."""
        lo, hi = self.store.sample_bounds()
        if hi is None:
            return
        today = datetime.date.today()
        if lo.date() <= today <= hi.date():
            return
        self.day_date.blockSignals(True)
        self.day_date.setDate(QtCore.QDate(hi.year, hi.month, hi.day))
        self.day_date.blockSignals(False)

    def _sync_dates_to_bounds(self) -> None:
        lo, hi = self.store.sample_bounds()
        if lo is None:
            lo = hi = datetime.datetime.now()
            self.day_date.setToolTip("No stored data yet")
        else:
            self.day_date.setToolTip(
                f"Stored data: {lo:%d %b %Y} – {hi:%d %b %Y}")
        for de, value in ((self.date_from, lo), (self.date_to, hi)):
            de.blockSignals(True)
            de.setDate(QtCore.QDate(value.year, value.month, value.day))
            de.blockSignals(False)

    def _refresh_device(self) -> None:
        d = self.store.device()
        self.dev_model.setText((d and d["model"]) or "Noise REP")
        self.dev_fw.setText(d["fw"] if d and d["fw"] else "—")
        last = self.store.last_sync()
        if last:
            self.dev_sync.setText(f"{_to_local(last['time']):%d %b, %H:%M}")
        else:
            self.dev_sync.setText("never synced")

    def _refresh_status_hint(self) -> None:
        if self._syncing:
            return
        last = self.store.last_sync()
        if last:
            self.status.setText(
                f"Last synced {_to_local(last['time']):%d %b, %H:%M} · "
                f"{last['n_records']} records.")
        else:
            self.status.setText(
                "No sync yet — connect the band and press Sync.")
        self._flash_info()
        self.error_banner.hide()

    def _refresh_header(self) -> None:
        self.ov_title.setText(self._greeting())
        self.ov_sub.setText(self._range_caption())
        if hasattr(self, "detail_range"):
            self.detail_range.setText(self._range_caption())

    def _greeting(self) -> str:
        hour = datetime.datetime.now().hour
        if 5 <= hour < 12:
            return "Good morning."
        if 12 <= hour < 17:
            return "Good afternoon."
        if 17 <= hour < 21:
            return "Good evening."
        return "Good night."

    def _range(self):
        mode = self._range_mode
        today = datetime.date.today()
        if mode == "today":
            start_d, end_d = today, today
        elif mode == "day":
            start_d = end_d = self.day_date.date().toPython()
        elif mode == "7d":
            start_d, end_d = today - datetime.timedelta(days=6), today
        elif mode == "30d":
            start_d, end_d = today - datetime.timedelta(days=29), today
        elif mode == "all":
            lo, hi = self.store.sample_bounds()
            if lo is None:
                start_d = end_d = today
            else:
                start_d, end_d = lo.date(), hi.date()
        else:  # custom
            start_d = self.date_from.date().toPython()
            end_d = self.date_to.date().toPython()
            if end_d < start_d:
                end_d = start_d
        start = datetime.datetime(start_d.year, start_d.month, start_d.day)
        end = datetime.datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59)
        return start, end

    def _range_caption(self) -> str:
        start, end = self._range()
        if start.date() == end.date():
            span = f"{start:%A, %d %B %Y}"
        elif start.year == end.year:
            span = f"{start:%d %b} – {end:%d %b %Y}"
        else:
            span = f"{start:%d %b %Y} – {end:%d %b %Y}"
        labels = {"today": "Today", "day": "Day", "7d": "Last 7 days",
                  "30d": "Last 30 days", "all": "All data",
                  "custom": "Selected range"}
        label = labels.get(self._range_mode, "")
        return f"{span} · {label}" if label else span

    def _range_short(self) -> str:
        start, end = self._range()
        if start.date() == end.date():
            return f"{start:%d %b}"
        return f"{start:%d %b} – {end:%d %b}"

    def _on_range_mode_changed(self, index: int) -> None:
        mode = self.range_combo.itemData(index)
        if mode is None:
            return
        if mode == "custom":
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Custom date range")
            layout = QtWidgets.QFormLayout(dialog)
            layout.setContentsMargins(24, 24, 24, 24)
            start = QtWidgets.QDateEdit(self.date_from.date())
            end = QtWidgets.QDateEdit(self.date_to.date())
            for field in (start, end):
                field.setCalendarPopup(True)
                field.setDisplayFormat("dd MMM yyyy")
            end.setMinimumDate(start.date())
            start.dateChanged.connect(end.setMinimumDate)
            layout.addRow("From", start)
            layout.addRow("To", end)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                blocker = QtCore.QSignalBlocker(self.range_combo)
                self.range_combo.setCurrentIndex(self.range_combo.findData(self._range_mode))
                return
            self.date_from.setDate(start.date())
            self.date_to.setDate(end.date())
        self._range_mode = mode
        self.custom_row.hide()
        self._show_day_nav()
        self._refresh_header()
        self._populate_overview()
        if self._detail_metric:
            self._plot_detail()

    def _show_day_nav(self) -> None:
        day = self._range_mode == "day"
        self.day_date.hide()
        if hasattr(self, "ov_day_nav"):
            self.ov_day_nav.set_day(
                self.day_date.date().toPython() if day
                else datetime.date.today() if self._range_mode == "today" else None)

    def _overview_shift(self, delta: int) -> None:
        """‹ Today › on Health Trends: step through single days."""
        today = QtCore.QDate.currentDate()
        if delta == 0:
            target = today
        else:
            base = self.day_date.date() if self._range_mode == "day" else today
            target = min(base.addDays(delta), today)
        mode = "today" if target == today else "day"
        self.day_date.setDate(target)
        self._range_mode = mode
        blocker = QtCore.QSignalBlocker(self.range_combo)
        self.range_combo.setCurrentIndex(-1)
        self._show_day_nav()
        self._refresh_header()
        self._populate_overview()
        if self._detail_metric:
            self._plot_detail()

    def _shift_day(self, delta: int) -> None:
        self.day_date.setDate(self.day_date.date().addDays(delta))

    def _on_day_changed(self, *_args) -> None:
        if self._range_mode != "day":
            return
        self._refresh_header()
        self._populate_overview()
        if self._detail_metric and self.stack.currentIndex() == 1:
            self._plot_detail()

    def _on_range_changed(self, *_args) -> None:
        if self._range_mode != "custom":
            return
        self._refresh_header()
        self._populate_overview()
        if self._detail_metric and self.stack.currentIndex() == 1:
            self._plot_detail()

    def _show_all_data(self) -> None:
        index = self.range_combo.findData("all")
        self.range_combo.setCurrentIndex(index)

    def _apply_gutter(self) -> None:
        """Align the app bar and banners with the centred content column."""
        width = self.width()
        g = theme.page_gutter(self, width)
        bar = max(26, min(g, (width - 1000) // 2))
        self._bar_layout.setContentsMargins(bar, 10, max(bar, 128), 10)
        self.window_controls.move(width - 118, 14)
        self.window_controls.raise_()
        self._banner_layout.setContentsMargins(g, 14, g, 0)
        self._settings_layout.setContentsMargins(g, 30, g, 40)
        if hasattr(self, "_trends_scroll"):
            content_gutter = theme.page_gutter(self, self._trends_scroll.viewport().width())
            self._trends_layout.setContentsMargins(content_gutter, 22, content_gutter, 36)

    def _sync_theme_btn(self) -> None:
        dark = theme.mode == "dark"
        self.theme_btn.set_icon("sun" if dark else "moon")
        self.theme_btn.setToolTip("Switch to the %s theme" % ("light" if dark else "dark"))

    def _toggle_theme(self) -> None:
        self.set_theme("light" if theme.mode == "dark" else "dark", save=True)

    def set_theme(self, mode: str, save: bool = False) -> None:
        """Switch light/dark everywhere, cross-fading from the old look."""
        self._crossfade_from_snapshot()
        theme.apply(mode)
        app = QtWidgets.QApplication.instance()
        app.setStyleSheet(theme.QSS)
        self._sync_theme_btn()
        old = self.today
        showing = self.stack.currentWidget() is old
        new = TodayPage(self.store, _profile_age(), on_open_metric=self._open_detail,
                        cache=old._cache)
        self.stack.insertWidget(self.stack.indexOf(old), new)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.today = new
        if showing:
            self.stack.setCurrentWidget(new)
        new.refresh(animate=False)
        if self._last_live is not None:
            new.on_live(self._last_live)
        for chart in self.findChildren(ChartView):
            chart.retheme()
        if save:
            QtCore.QSettings("NoiseREP", "Sync").setValue("theme", mode)

    def _toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.WindowStateChange and hasattr(self, "maximize_button"):
            label = "Restore" if self.isMaximized() else "Maximize"
            self.maximize_button.setAccessibleName(label)
            self.maximize_button.setToolTip(label)
            self.maximize_button.update()
            if hasattr(self, "today"):
                self._apply_gutter()
                self.today._apply_gutter()
                self.today._relayout()
                for page in self.findChildren(GutterScrollArea):
                    page.sync_gutters()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_gutter()
        self._resize_timer.start()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._relayout_tiles()
        if not self._did_initial_focus:
            # Do not open with a focus ring on the range picker; the content
            # itself takes initial focus and Tab still reaches every control.
            self._did_initial_focus = True
            root = self.centralWidget()
            root.setFocusPolicy(QtCore.Qt.StrongFocus)
            root.setFocus(QtCore.Qt.OtherFocusReason)

    def _content_width(self) -> int:
        width = self.width()
        return max(0, width - 2 * theme.page_gutter(self, width))

    def _relayout_tiles(self) -> None:
        # Channel rows remain full-width; vitals and goals still reflow.
        self._apply_gutter()
        self._relayout_vitals()
        self._relayout_movement()

    # ------------------------------------------------------------------
    # banner helpers
    # ------------------------------------------------------------------
    def _set_live_btn(self, active: bool, text: str | None = None) -> None:
        self.live_btn.setText(text or ("● Live on" if active else "♥ Live"))
        self.live_btn.setProperty("active", "true" if active else "false")
        self.live_btn.style().unpolish(self.live_btn)
        self.live_btn.style().polish(self.live_btn)
        self.live_btn.setToolTip(
            "Stop the menu-bar Live HR app and resume the in-app band link"
            if active else
            "Start live heart rate in the macOS menu bar (separate app; the "
            "band accepts one central, so the in-app link pauses meanwhile)")
        self.live_btn.setAccessibleName(
            "Stop live heart rate in the menu bar" if active
            else "Start live heart rate in the menu bar")

    def _menubar_active(self) -> bool:
        proc = self._menubar_proc
        return proc is not None and proc.poll() is None

    def _toggle_menubar(self) -> None:
        """Start the menu-bar HR app (pausing our band link), or stop it."""
        if self._menubar_active():
            self._stop_menubar()
        elif self._menubar_proc is None and self._menubar_running():
            self._set_status("Live HR is already running outside this window; "
                             "quit it from the menu bar first.")
        elif self._menubar_proc is None:
            self._set_live_btn(True, "Starting…")
            self.live_btn.setEnabled(False)
            self._release_link(self._start_menubar)

    def _release_link(self, then) -> None:
        """Cancel the in-app band link, then call ``then`` once it let go."""
        task = self._link_task
        if task is None or task.done():
            self._link_task = None
            return then()
        self._set_band_state("idle", "Releasing the band…")
        fired = []

        def go(*_):
            if not fired:
                fired.append(1)
                self._link_task = None
                then()

        task.add_done_callback(go)
        QtCore.QTimer.singleShot(5000, go)
        task.cancel()

    def _start_menubar(self) -> None:
        self.live_btn.setEnabled(True)
        try:
            self._menubar_proc = subprocess.Popen(
                [sys.executable, "-m", "noiserep.cli", "menubar", "--db", self.store.path],
                start_new_session=True)
        except OSError as exc:
            self._set_live_btn(False)
            self._show_error(f"Could not open the menu-bar app: {exc}")
            self.start_link()
            return
        self._menubar_stopping = False
        self._set_live_btn(True)
        self._set_band_state("idle", "Paused · Live HR app has the band")
        self._set_status("Live HR is running in the menu bar. The in-app band "
                         "link is paused until you click Live again.")
        self._menubar_poll.start()

    def _free_band(self) -> None:
        """Make sure nothing else in this app holds the band."""
        if self._menubar_active():
            self._stop_menubar()
        _end_stray_menubar()

    def _stop_menubar(self) -> None:
        proc = self._menubar_proc
        self._menubar_stopping = True
        self.live_btn.setEnabled(False)
        self._set_live_btn(True, "Stopping…")
        _end_tree(proc)

    def _poll_menubar(self) -> None:
        proc = self._menubar_proc
        if proc is None:
            return
        if proc.poll() is None:
            from ..live import LiveSample
            report = self.store.live()
            if report is not None:
                age = (datetime.datetime.now() - report["at"]).total_seconds()
                if 0 <= age <= 45:
                    self._on_live(LiveSample(**report))
                else:
                    self.today.live_hr.set(None)
                    self.today.live_card.setToolTip("Waiting for a fresh reading from the band…")
            return
        self._menubar_poll.stop()
        self._menubar_proc = None
        if not getattr(self, "_menubar_stopping", False):
            self._check_menubar_started(proc)
        self.live_btn.setEnabled(True)
        self._set_live_btn(False)
        self._set_status("Live HR stopped; resuming the band link.")
        self.start_link()

    def _check_menubar_started(self, proc) -> None:
        code = proc.poll()
        if code:
            self._show_error(
                f"Live HR stopped right after starting (exit code {code}). "
                "Run `noiserep menubar` in a terminal to see why.")

    @staticmethod
    def _menubar_running() -> bool:
        try:
            proc = subprocess.run(
                ["pgrep", "-f", "noiserep.cli menubar"],
                capture_output=True)
            return proc.returncode == 0
        except OSError:
            return False

    def _flash_info(self, ms: int = 4500) -> None:
        """Show the info banner briefly; status is never left on screen."""
        timer = getattr(self, "_info_timer", None)
        if timer is None:
            timer = self._info_timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.info_banner.hide)
        self.info_banner.show()
        timer.start(ms)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self._flash_info()

    def _show_error(self, message: str) -> None:
        self.error_text.setText(message)
        self.error_banner.setAccessibleName("Error: " + message)
        self.error_banner.show()
        self.info_banner.hide()
        self.sync_bar.finish(ok=False)

    def _clear_error(self) -> None:
        self.error_banner.hide()
        self._flash_info()

    # ------------------------------------------------------------------
    # keyboard shortcuts
    # ------------------------------------------------------------------
    def _install_shortcuts(self) -> None:
        self._shortcuts: list[QtGui.QShortcut] = []
        bindings = [
            ("Ctrl+R", self._on_sync),
            ("Ctrl+E", lambda: self._export("csv")),
            ("Ctrl+1", self._show_overview),
            ("Esc", self._on_escape),
        ]
        for seq, slot in bindings:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _on_escape(self) -> None:
        if self.stack.currentIndex() != 0:
            self._show_overview()

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    def _export(self, fmt: str) -> None:
        metric = (self._detail_metric
                  if self.stack.currentIndex() == 1 else None)
        start, end = self._range()
        stem = metric or f"noiserep_{start:%Y%m%d}_{end:%Y%m%d}"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export data", f"{stem}.{fmt}", f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        writer = export_csv if fmt == "csv" else export_json
        try:
            count = writer(self.store, path, metric, start, end)
        except Exception as exc:  # noqa: BLE001 - file/system errors vary
            message = f"Export failed: {exc}"
            self.status.setText(message)
            self._show_error(message)
            return
        self._clear_error()
        self._set_status(
            f"Exported {count} rows to {os.path.basename(path)} · {fmt.upper()}")

    # ------------------------------------------------------------------
    # live band link
    # ------------------------------------------------------------------
    def _set_band_state(self, kind: str, text: str) -> None:
        self.band_state.setText(text)
        self.band_dot.setProperty("state", kind)
        self.band_dot.style().unpolish(self.band_dot)
        self.band_dot.style().polish(self.band_dot)

    def start_link(self) -> None:
        """Keep the band connected for as long as the window is open."""
        import asyncio
        from ..live import keep_linked

        if self._link_task is not None:
            return
        self._sync_now = asyncio.Event()
        self._set_band_state("searching", "Searching for band…")
        self._link_task = asyncio.ensure_future(keep_linked(
            self.store, self.band_name, on_sample=self._on_live,
            on_status=self._on_link_status, sync_now=self._sync_now,
            on_history=self._after_history, on_progress=self._sync_progress))

    def _on_link_status(self, text: str) -> None:
        print(f"{datetime.datetime.now():%H:%M:%S} [band link] {text}", flush=True)
        if text == "connected":
            self._linked = True
            self._set_band_state("live", "Connected")
        elif text == "syncing history":
            self._set_band_state("live", "Syncing history…")
            self._set_status("Syncing history from the band…")
        elif text.startswith("disconnected"):
            self.sync_bar.finish(ok=False)
            self._set_band_state("searching", "Waiting for band…")
            self._linked = False

    def _on_live(self, sample) -> None:
        self._last_live = sample
        place = {"charging": ("charging", f"⚡ Charging · {sample.battery}%"),
                 "wrist": ("live", f"On wrist · {sample.battery}%"),
                 "off_wrist": ("idle", f"Off wrist · {sample.battery}%"),
                 "unknown": ("live", f"Live · {sample.battery}%")}[sample.place]
        self._set_band_state(*place)
        self.today.on_live(sample)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Disconnect the band before closing.

        A link that is dropped without a clean disconnect makes the band
        refuse the next connection until its supervision timeout passes.
        """
        if self._menubar_active():
            _end_tree(self._menubar_proc)
        task = self._link_task
        if task is None or task.done():
            return super().closeEvent(event)
        event.ignore()
        self._set_band_state("idle", "Disconnecting…")
        task.add_done_callback(lambda _: self.close())
        QtCore.QTimer.singleShot(5000, lambda: task.done() or self._force_close())
        task.cancel()

    def _force_close(self) -> None:
        self._link_task = None
        self.close()

    def _after_history(self) -> None:
        self._refresh_device()
        self._sync_dates_to_bounds()
        self._refresh_header()
        self._populate_overview()
        if self._detail_metric:
            self._plot_detail()
        self.today.invalidate()
        if self.stack.currentWidget() is self.today:
            self.today.refresh(animate=False)
        self._set_status("History synced from the band.")

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------
    def _sync_progress(self, done: int, total: int, stage: str) -> None:
        text = _STAGE_TEXT.get(stage, "Syncing…")
        self._set_status(f"{text}  {done}/{total}" if total > 0 and stage != "done"
                         else text)
        self.sync_bar.progress(done, total, stage)

    def _set_syncing(self, syncing: bool) -> None:
        self._syncing = syncing
        self.sync_btn.setEnabled(not syncing)
        self.empty_sync_btn.setEnabled(not syncing)
        self.sync_btn.setText("Syncing…" if syncing else "Sync band")
        self.empty_sync_btn.setText("Syncing…" if syncing else "Sync band")
        if syncing:
            self.sync_bar.begin()
        else:
            self.sync_bar.finish(ok=self._sync_ok)

    @qasync.asyncSlot()
    async def _on_sync(self) -> None:
        if self._syncing:
            return
        self._free_band()
        if self._sync_now is not None:
            # The link owns the band's one connection: (re)start it and
            # have it sync right away.
            if self._link_task is None or self._link_task.done():
                self._link_task = None
                self.start_link()
            self._sync_now.set()
            if getattr(self, "_linked", False):
                self._set_status("Syncing…")
            else:
                self._set_status(
                    "Waiting for the band — worn, it only announces itself "
                    "every ~30 s, so connecting can take a minute. A few "
                    "seconds on the charger wakes it at once. It syncs as "
                    "soon as it connects.")
            return

        self._clear_error()
        self._sync_ok = False
        self._set_syncing(True)
        self._set_status("Scanning for band…")
        try:
            from ..ble.connection import connect
            from ..sync import run_sync

            # Fetch the whole device timeline: a freshly-unpaired band's clock
            # reads ~2002, so "today" would filter out all of its data.
            since = datetime.datetime(2000, 1, 1)
            async with connect(self.band_name) as band:
                result = await run_sync(band, since, progress=self._sync_progress)
            counts = self.store.ingest(result, note="gui sync")
            self._sync_ok = True
            self._clear_error()
            extra = ""
            if counts.get("rri"):
                extra += f", +{counts['rri']} RRI"
            if counts.get("sleep"):
                extra += f", +{counts['sleep']} sleep"
            self._set_status(
                f"Synced +{counts['samples']} samples and +{counts['files']} "
                f"files{extra}.")
            self._refresh_device()
            self._sync_dates_to_bounds()
            self._refresh_header()
            self._populate_overview()
            if self._detail_metric:
                self._plot_detail()
        except Exception as exc:  # noqa: BLE001 - keep the window alive
            # Match by name so a missing optional BLE dependency cannot raise
            # NameError while resolving the except clause itself.
            kind = type(exc).__name__
            if kind == "BandNotFound":
                message = ("Band not found. Put it on the charger, turn your "
                           "phone's Bluetooth off, then try again.")
            elif kind == "TransportError":
                message = f"Transport error: {exc}"
            else:
                message = f"Sync failed: {exc}"
            self.status.setText(message)
            self._show_error(message)
        finally:
            self._set_syncing(False)


def main(argv: list[str] | None = None) -> int:
    import asyncio
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
        sys.argv if argv is None else argv)
    from .branding import apply_app_icon
    apply_app_icon(app)
    app.setApplicationName("NoiseREP Sync")
    app.setApplicationDisplayName("NoiseREP Sync")
    saved = QtCore.QSettings("NoiseREP", "Sync").value("theme", "light")
    theme.apply("dark" if saved == "dark" else "light")
    app.setStyleSheet(theme.QSS)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    store = Store(default_db_path())
    app.aboutToQuit.connect(store.close)
    win = MainWindow(store)
    reloader = None
    if "--reload" in (argv or []):
        from .dev_reload import DevReloader
        settings = QtCore.QSettings("NoiseREP", "Development")
        geometry = settings.value("geometry")
        if geometry is not None:
            win.restoreGeometry(geometry)
        win._show_home(settings.value("page", "today"))
        reloader = DevReloader(win)
    win.show()
    win.start_link()
    with loop:
        loop.run_forever()
    if reloader is not None and reloader.requested:
        subprocess.Popen([sys.executable, "-m", "noiserep.cli", "gui", "--reload"],
                         start_new_session=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

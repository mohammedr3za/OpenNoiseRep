"""Rest and recovery summaries derived from beat-to-beat RRI data.

The band does not always send a staged sleep file; it does send RRI (RR
interval) records.  This module turns those intervals into an honest rest
summary: the time span the band recorded, the beats inside it, heart rate,
HRV (RMSSD/SDNN/pNN50), signal coverage and gaps.  It never invents sleep
stages, and it does not claim a medical interpretation.

All timestamps are naive band wall-clock, matching ``Store``.  Everything here
is pure standard library.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

#: A silence longer than this splits one rest window into two.  Sleep RRI is
#: recorded in bursts, so allow a 30-minute hole before splitting a night.
DEFAULT_GAP_S = 1800

#: Ignore short fragments; a rest summary needs at least this span.
DEFAULT_MIN_MINUTES = 30


@dataclass(frozen=True)
class RestSummary:
    """One continuous RRI window."""

    start: dt.datetime
    end: dt.datetime
    duration_min: int
    beats: int
    avg_hr: float | None
    min_hr: int | None
    max_hr: int | None
    rmssd_ms: float | None
    sdnn_ms: float | None
    pnn50: float | None
    coverage_pct: float | None

    @property
    def duration_text(self) -> str:
        hours, minutes = divmod(self.duration_min, 60)
        return f"{hours}h {minutes:02d}m"


def _as_pairs(samples: Iterable) -> list[tuple[dt.datetime, int]]:
    """Accept ``(ts, rr_ms)`` pairs, RriSample objects or dicts."""
    out: list[tuple[dt.datetime, int]] = []
    for item in samples or []:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            ts, rr = item[0], item[1]
        elif isinstance(item, dict):
            ts, rr = item.get("ts"), item.get("rr_ms")
        else:
            ts, rr = getattr(item, "ts", None), getattr(item, "rr_ms", None)
        if isinstance(ts, dt.datetime) and isinstance(rr, (int, float)) and rr > 0:
            out.append((ts, int(rr)))
    out.sort(key=lambda pair: pair[0])
    return out


def rest_windows(samples: Iterable, gap_s: int = DEFAULT_GAP_S,
                 min_minutes: int = DEFAULT_MIN_MINUTES) -> list[RestSummary]:
    """Split RRI samples into continuous rest windows, longest first.

    A window is a run of beats with no silence longer than ``gap_s``.  Only
    windows spanning at least ``min_minutes`` are returned.
    """
    pairs = _as_pairs(samples)
    groups: list[list[tuple[dt.datetime, int]]] = []
    current: list[tuple[dt.datetime, int]] = []
    previous: dt.datetime | None = None
    for ts, rr in pairs:
        if previous is not None and (ts - previous).total_seconds() > gap_s:
            if current:
                groups.append(current)
            current = []
        current.append((ts, rr))
        previous = ts
    if current:
        groups.append(current)

    summaries = []
    for group in groups:
        summary = summarize(group)
        if summary is not None:
            summaries.append(summary)
    summaries = [s for s in summaries if s.duration_min >= min_minutes]
    summaries.sort(key=lambda s: (s.duration_min, s.beats), reverse=True)
    return summaries


def summarize(samples: Sequence[tuple[dt.datetime, int]]) -> RestSummary | None:
    """Summarise one contiguous group of ``(ts, rr_ms)`` pairs."""
    pairs = _as_pairs(samples)
    if len(pairs) < 10:
        return None
    start, end = pairs[0][0], pairs[-1][0]
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return None

    rr_all = [float(value) for _, value in pairs]
    # Drop clearly non-physiological intervals (30-200 bpm) before computing
    # rate/HRV; one bad interval otherwise dominates RMSSD and the extremes.
    rr = [value for value in rr_all if 300 <= value <= 2000] or rr_all
    hr = sorted(60000.0 / value for value in rr if value > 0)
    diffs = [rr[i] - rr[i - 1] for i in range(1, len(rr))]
    diffs = [d for d in diffs if abs(d) <= 200]
    rmssd = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if diffs else None
    sdnn = statistics.pstdev(rr) if len(rr) > 1 else None
    pnn50 = (100.0 * sum(abs(d) > 50 for d in diffs) / len(diffs)
             if diffs else None)

    # RRI is recorded in bursts, so coverage is normally well below 100%.
    recorded_s = sum(rr_all) / 1000.0
    coverage = min(100.0, 100.0 * recorded_s / span_s)

    def pct(values, fraction):
        if not values:
            return None
        pos = fraction * (len(values) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)

    return RestSummary(
        start=start,
        end=end,
        duration_min=int(round(span_s / 60.0)),
        beats=len(rr),
        avg_hr=round(statistics.fmean(hr), 1) if hr else None,
        min_hr=round(pct(hr, 0.05)) if hr else None,
        max_hr=round(pct(hr, 0.95)) if hr else None,
        rmssd_ms=round(rmssd, 1) if rmssd is not None else None,
        sdnn_ms=round(sdnn, 1) if sdnn is not None else None,
        pnn50=round(pnn50, 1) if pnn50 is not None else None,
        coverage_pct=round(coverage, 1),
    )


def overnight_rest(samples: Iterable, gap_s: int = DEFAULT_GAP_S,
                   min_minutes: int = DEFAULT_MIN_MINUTES) -> RestSummary | None:
    """The longest RRI window, i.e. the likely overnight rest period."""
    windows = rest_windows(samples, gap_s=gap_s, min_minutes=min_minutes)
    return windows[0] if windows else None


def insights(summary: RestSummary) -> list[str]:
    """Short, non-medical observations about a rest window."""
    lines = [
        f"RRI spans {summary.start:%H:%M}–{summary.end:%H:%M} "
        f"({summary.duration_text}), with {summary.beats:,} beats."
    ]
    if summary.avg_hr is not None:
        lines.append(
            f"Average heart rate {summary.avg_hr:.0f} bpm "
            f"(5–95%: {summary.min_hr}–{summary.max_hr})."
        )
    if summary.rmssd_ms is not None:
        lines.append(
            f"HRV: RMSSD {summary.rmssd_ms:.0f} ms, "
            f"SDNN {summary.sdnn_ms:.0f} ms, pNN50 {summary.pnn50:.0f}%."
        )
    if summary.coverage_pct is not None and summary.coverage_pct < 80:
        lines.append(
            f"Beats cover {summary.coverage_pct:.0f}% of the span; the band "
            "records RRI in bursts, so treat the span as rest time, not "
            "continuous sleep."
        )
    if summary.avg_hr is not None and summary.avg_hr > 80:
        lines.append(
            "Average heart rate was on the higher side for rest; movement, "
            "a loose strap or caffeine can raise it."
        )
    lines.append(
        "HRV trends are most useful against your own baseline, not a fixed "
        "target."
    )
    return lines


__all__ = [
    "DEFAULT_GAP_S",
    "DEFAULT_MIN_MINUTES",
    "RestSummary",
    "insights",
    "overnight_rest",
    "rest_windows",
    "summarize",
]

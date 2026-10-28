"""Daily scores from band data: HRV, resting HR, strain, sleep, recovery.

Clean-room implementations of published methods; each function names its
source.  Constants no paper fixes (score scales, weights) are this project's
own choice, marked ``OWN``: treat those scores as approximate trends.
Timestamps are naive band wall-clock, as in ``Store``.  Pure standard library.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field

# -- HRV ---------------------------------------------------------------------

RR_MIN_MS, RR_MAX_MS = 300, 2000
MALIK_TOLERANCE = 0.20          # Malik 1989: >20 % off the local median is ectopic
HRV_WINDOW_S = 300              # Task Force 1996 short-term recording length
MIN_WINDOW_BEATS = 30           # Baek 2015: RMSSD unreliable below ~30 beats


def clean_rr(rr: list[float]) -> list[float | None]:
    """Range filter + Malik ectopic rule; dropped beats become ``None``.

    Keeping the hole (rather than deleting it) lets :func:`rmssd` skip the
    difference that would span it.
    """
    out: list[float | None] = []
    for i, value in enumerate(rr):
        if not RR_MIN_MS <= value <= RR_MAX_MS:
            out.append(None)
            continue
        local = [v for v in rr[max(0, i - 2):i + 3] if RR_MIN_MS <= v <= RR_MAX_MS]
        median = statistics.median(local)
        out.append(value if abs(value - median) <= MALIK_TOLERANCE * median else None)
    return out


def rmssd(clean: list[float | None]) -> float | None:
    """RMSSD over successive *clean* beats only (Task Force ESC/NASPE 1996)."""
    diffs = [b - a for a, b in zip(clean, clean[1:]) if a is not None and b is not None]
    if len(diffs) < MIN_WINDOW_BEATS - 1:
        return None
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def nightly_hrv(rr_pairs, start: dt.datetime, end: dt.datetime) -> float | None:
    """Median of 5-minute RMSSDs inside ``[start, end]``.

    Median (OWN choice over the mean) keeps one movement-heavy window from
    dragging the night.
    """
    windows: dict[int, list[float]] = {}
    previous = None
    for ts, rr in rr_pairs:
        # The band's RRI file has one slot per second; a beat longer than a
        # second repeats into the next slot, so collapse exact repeats.
        if rr == previous:
            continue
        previous = rr
        if start <= ts <= end:
            windows.setdefault(int((ts - start).total_seconds()) // HRV_WINDOW_S,
                               []).append(float(rr))
    values = [v for v in (rmssd(clean_rr(w)) for w in windows.values()) if v is not None]
    return round(statistics.median(values), 1) if values else None


# -- heart rate --------------------------------------------------------------

def nightly_rhr(hr_pairs, start: dt.datetime, end: dt.datetime,
                bin_s: int = 300) -> float | None:
    """Lowest 5-minute mean heart rate in the sleep window (>=5 readings/bin)."""
    bins: dict[int, list[float]] = {}
    for ts, bpm in hr_pairs:
        if start <= ts <= end and bpm >= 25:
            bins.setdefault(int((ts - start).total_seconds()) // bin_s, []).append(bpm)
    means = [statistics.fmean(v) for v in bins.values() if len(v) >= 5]
    return round(min(means), 1) if means else None


def hr_max(age: int | None, hr_values: list[float]) -> float:
    """Tanaka 2001 (208 - 0.7 age), raised to the observed 99.5th percentile."""
    predicted = 208 - 0.7 * age if age else 190.0   # OWN default: age ~25
    if len(hr_values) >= 1000:
        ordered = sorted(hr_values)
        predicted = max(predicted, ordered[int(0.995 * (len(ordered) - 1))])
    return round(predicted, 1)


ZONE_FRACTIONS = (0.5, 0.6, 0.7, 0.8, 0.9)          # Edwards 1993, % HRmax


def zone_of(bpm: float, hrmax: float) -> int:
    """Edwards zone 1-5, or 0 below 50 % HRmax."""
    return sum(bpm >= f * hrmax for f in ZONE_FRACTIONS)


STRAIN_SCALE = 150.0    # OWN: TRIMP 150 (an hour around zone 2-3) maps to ~63


def strain(hr_pairs, hrmax: float, cadence_s: int = 30) -> tuple[float, list[float], float]:
    """Edwards TRIMP for the day -> (score 0-100, minutes per zone 1-5, TRIMP).

    TRIMP = sum(minutes in zone x zone number) (Edwards 1993).  The 0-100
    compression ``100 (1 - e^(-TRIMP/150))`` is OWN.
    """
    minutes = [0.0] * 5
    for _, bpm in hr_pairs:
        z = zone_of(bpm, hrmax)
        if z:
            minutes[z - 1] += cadence_s / 60
    trimp = sum((i + 1) * m for i, m in enumerate(minutes))
    score = 100 * (1 - math.exp(-trimp / STRAIN_SCALE))
    return round(score, 1), [round(m, 1) for m in minutes], round(trimp, 1)


# -- sleep -------------------------------------------------------------------

def sleep_need_min(age: int | None) -> int:
    """Population target: 8 h adults, 9 h under 18 (Hirshkowitz 2015)."""
    return 540 if age is not None and age < 18 else 480


def sleep_debt_min(slept_min: list[int], need_min: int) -> int:
    """Unrecovered shortfall over the given nights (OWN: plain sum, no bank).

    Debt does not repay hour-for-hour (Van Dongen 2003), so this is shown as a
    raw shortfall, not a physiological debt model.
    """
    return int(sum(max(0, need_min - s) for s in slept_min))


# -- recovery ----------------------------------------------------------------

BASELINE_NIGHTS = 30
MIN_BASELINE_NIGHTS = 4
#: OWN weights: HRV-led, as in Plews 2013 / Bellenger 2016 monitoring.
RECOVERY_WEIGHTS = {"hrv": 0.5, "rhr": 0.25, "sleep": 0.25}


def _z(value, history, invert=False, floor=1.0):
    if value is None or len(history) < MIN_BASELINE_NIGHTS:
        return None
    sd = max(statistics.pstdev(history), floor)
    z = (value - statistics.fmean(history)) / sd
    return -z if invert else z


def recovery(hrv, rhr, sleep_score, history: list[dict]) -> float | None:
    """0-100 from today's drivers vs the trailing 30-night personal baseline.

    z-scores (RHR inverted; sleep as (score-75)/12) are weighted, missing
    drivers dropped, and mapped through the normal CDF shifted so a baseline
    night reads ~60.  The shift and weights are OWN.  ``None`` until 4 nights.
    """
    past = history[-BASELINE_NIGHTS:]
    zs = {
        "hrv": _z(hrv, [h["hrv"] for h in past if h.get("hrv")], floor=5.0),
        "rhr": _z(rhr, [h["rhr"] for h in past if h.get("rhr")], invert=True, floor=2.0),
        "sleep": (sleep_score - 75) / 12 if sleep_score is not None else None,
    }
    if zs["hrv"] is None and zs["rhr"] is None:
        return None
    used = {k: v for k, v in zs.items() if v is not None}
    weight = sum(RECOVERY_WEIGHTS[k] for k in used)
    zbar = sum(RECOVERY_WEIGHTS[k] * v for k, v in used.items()) / weight
    cdf = 0.5 * (1 + math.erf((zbar + 0.25) / math.sqrt(2)))
    return round(100 * cdf, 1)


# -- one day -----------------------------------------------------------------

@dataclass
class DayScores:
    day: dt.date
    sleep: dict | None = None
    sleep_window: tuple | None = None
    hrv_ms: float | None = None
    rhr_bpm: float | None = None
    hrmax: float | None = None
    strain: float | None = None
    trimp: float | None = None
    zone_minutes: list = field(default_factory=list)
    sleep_need_min: int = 480
    sleep_debt_min: int | None = None
    recovery: float | None = None
    baseline_nights: int = 0


def _night(store, day: dt.date):
    """Sleep window ending on ``day``: band session, else longest RRI window."""
    lo = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(18))
    hi = dt.datetime.combine(day, dt.time(14))
    sessions = [s for s in store.sleep_sessions(lo, hi)
                if isinstance(s, dict) and s.get("source") != "nap"
                and s.get("wake") and lo <= s["wake"] <= hi]
    if sessions:
        best = max(sessions, key=lambda s: s.get("total_min") or 0)
        return best, (best["bedtime"], best["wake"])
    rest = store.rest_summary(lo, hi)
    return None, ((rest.start, rest.end) if rest else None)


def night_vitals(store, day: dt.date) -> tuple:
    """(session, window, HRV, RHR) for the night ending on ``day``."""
    session, window = _night(store, day)
    if window is None:
        return session, None, None, None
    start, end = window
    # The band's own sleep HRV comes from full-rate PPG; RRI slots are 64 ms
    # quantised peak times, so their RMSSD carries detection jitter.
    vendor = [v for _, v in store.samples("sleep_hrv", start, end) if v]
    hrv = (round(statistics.fmean(vendor), 1) if vendor
           else nightly_hrv(store.rri(start, end), start, end))
    band_rhr = store.samples("resting_hr", dt.datetime.combine(day, dt.time()),
                             dt.datetime.combine(day, dt.time(23, 59)))
    rhr = (band_rhr[-1][1] if band_rhr
           else nightly_rhr(store.samples("heart_rate", start, end), start, end))
    return session, window, hrv, rhr


def day_scores(store, day: dt.date, age: int | None = None) -> DayScores:
    """Everything for one calendar day; missing inputs leave fields ``None``."""
    out = DayScores(day=day, sleep_need_min=sleep_need_min(age))
    out.sleep, out.sleep_window, out.hrv_ms, out.rhr_bpm = night_vitals(store, day)

    day_start = dt.datetime.combine(day, dt.time())
    hr_day = store.samples("heart_rate", day_start, day_start + dt.timedelta(days=1))
    if hr_day:
        hist = store.samples("heart_rate", day_start - dt.timedelta(days=30))
        out.hrmax = hr_max(age, [v for _, v in hist])
        out.strain, out.zone_minutes, out.trimp = strain(hr_day, out.hrmax)

    history, slept = [], []
    for back in range(BASELINE_NIGHTS, 0, -1):
        prev = day - dt.timedelta(days=back)
        session, _, hrv, rhr = night_vitals(store, prev)
        history.append({"hrv": hrv, "rhr": rhr})
        if back <= 7 and session and session.get("total_min"):
            slept.append(session["total_min"])
    out.baseline_nights = sum(1 for h in history if h["hrv"])
    if out.sleep and out.sleep.get("total_min"):
        slept.append(out.sleep["total_min"])
    out.sleep_debt_min = sleep_debt_min(slept, out.sleep_need_min) if slept else None
    score = out.sleep.get("quality") if out.sleep else None
    out.recovery = recovery(out.hrv_ms, out.rhr_bpm, score, history)
    return out

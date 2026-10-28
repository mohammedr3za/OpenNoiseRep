"""Estimated sleep stages from movement level + heart rate (+ RR intervals).

Used only when the band's own staged sleep record (metric 15) is missing for
a night.  The output is an ESTIMATE: no accelerometer or EEG, 5-minute
epochs, rank/percentile rules.  Methods (clean-room, published only):

* sleep/wake - Cole, Kripke et al. 1992 (Sleep 15:461) weighted activity
  window, adapted from 1-minute counts to the band's 5-minute movement levels
  (centred 3-epoch kernel); wake also when HR sits well above the night's
  median (heart rate rises on awakening, Walch et al. 2019, Sleep 42:zsz164).
* sleep onset - first run of persistent sleep (cf. the 10-15 min persistent
  sleep onset convention in actigraphy scoring, Webster 1982).
* deep / REM / light - Walch 2019 style features: heart rate and its
  variability (HR spread, RMSSD per Task Force 1996) combined into one
  "arousal" rank; lowest arousal early in the night -> deep, highest arousal
  after the first REM latency -> REM, rest -> light (core).  Time-of-night
  priors and stage shares follow Carskadon & Dement 2011 / Ohayon 2004 norms.

Constants without a published source are marked ``OWN``.  Pure stdlib; timestamps
are naive band wall-clock like the rest of the app.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from . import scores
from .protocol.files import STAGE_AWAKE, STAGE_DEEP, STAGE_LIGHT, STAGE_REM

EPOCH_S = 300                       # the band's movement cadence
KERNEL = (0.25, 0.5, 0.25)          # OWN: Cole-Kripke shape squeezed to 3 epochs
ACTIVITY_WAKE = 1.5                 # OWN: kernel-weighted movement level => wake
HR_WAKE_BPM = 12.0                  # OWN: above night median => wake
ONSET_EPOCHS = 3                    # OWN: 15 min persistent sleep
DEEP_MAX, DEEP_RANK, DEEP_FRAC = 0.22, 0.35, 2 / 3     # OWN; shares: Ohayon 2004
REM_MAX, REM_RANK, REM_LATENCY_S = 0.27, 0.70, 3600    # OWN; REM latency: Carskadon
MIN_EPOCHS, MIN_HR_COVERAGE = 12, 0.5                  # OWN: need 1 h, half the HR
HRV_WINDOW_MIN_BEATS = scores.MIN_WINDOW_BEATS


@dataclass(frozen=True)
class StagedNight:
    """Stage changes plus the AASM-style roll-up; ``estimated`` is always True."""

    bedtime: dt.datetime
    wake: dt.datetime
    stages: list                    # [(datetime, STAGE_*)] at each change
    total_min: int                  # time asleep (TST)
    deep_min: int
    light_min: int
    rem_min: int
    awake_min: int                  # incl. latency and trailing wake
    latency_min: int
    efficiency: int                 # TST / time in bed, %
    time_in_bed_min: int
    estimated: bool = True


def _ranks(values: list) -> list:
    """Percentile rank 0..1 of each value among the non-None ones (None -> 0.5)."""
    known = sorted(v for v in values if v is not None)
    if len(known) < 2:
        return [0.5] * len(values)
    return [0.5 if v is None else
            (sum(k < v for k in known) + 0.5 * sum(k == v for k in known)) / len(known)
            for v in values]


def _epoch_rmssd(rri, bed: dt.datetime, n: int) -> list:
    """RMSSD per epoch from RR slots (exact repeats collapsed, as in scores)."""
    beats: list[list[float]] = [[] for _ in range(n)]
    previous = None
    for ts, rr in rri:
        if rr == previous:
            continue
        previous = rr
        i = int((ts - bed).total_seconds()) // EPOCH_S
        if 0 <= i < n:
            beats[i].append(float(rr))
    out = []
    for b in beats:
        value = scores.rmssd(scores.clean_rr(b)) if len(b) >= HRV_WINDOW_MIN_BEATS else None
        out.append(value)
    return out


def stage_night(bed: dt.datetime, wake: dt.datetime, hr, movement, rri=()) -> StagedNight | None:
    """Estimate stages for ``[bed, wake]``.

    ``hr``/``movement``/``rri`` are ``(datetime, value)`` iterables.  Movement
    is stored without zeros, so ``movement=None`` means "series not synced"
    (HR-only wake detection) while an empty list means "all still".
    """
    tib_s = (wake - bed).total_seconds()
    n = math.ceil(tib_s / EPOCH_S)
    if n < MIN_EPOCHS:
        return None
    hr_bins: list[list[float]] = [[] for _ in range(n)]
    for ts, bpm in hr:
        i = int((ts - bed).total_seconds()) // EPOCH_S
        if 0 <= i < n and bpm >= 25:
            hr_bins[i].append(float(bpm))
    if sum(1 for b in hr_bins if b) < MIN_HR_COVERAGE * n:
        return None
    hr_mean = [statistics.fmean(b) if b else None for b in hr_bins]
    base = statistics.median(v for v in hr_mean if v is not None)

    act = [0.0] * n
    for ts, level in movement or ():
        i = int((ts - bed).total_seconds()) // EPOCH_S
        if 0 <= i < n:
            act[i] = max(act[i], float(level))
    pad = [0.0] + act + [0.0]
    smooth = [sum(k * pad[i + j] for j, k in enumerate(KERNEL)) for i in range(n)]
    awake = [smooth[i] >= ACTIVITY_WAKE
             or (hr_mean[i] is not None and hr_mean[i] > base + HR_WAKE_BPM)
             for i in range(n)]

    onset = next((i for i in range(n - ONSET_EPOCHS + 1)
                  if not any(awake[i:i + ONSET_EPOCHS])), None)
    if onset is None:
        return None
    for i in range(onset):
        awake[i] = True

    # arousal: high HR, high HR spread, low RMSSD (Walch 2019 features)
    spread = []
    for i in range(n):
        window = [x for b in hr_bins[max(0, i - 1):i + 2] for x in b]
        spread.append(statistics.pstdev(window) if len(window) >= 5 else None)
    hrv = _epoch_rmssd(rri, bed, n)
    asleep = [i for i in range(n) if not awake[i]]
    sub = lambda xs: [xs[i] for i in asleep]
    r_hr, r_sp, r_hrv = _ranks(sub(hr_mean)), _ranks(sub(spread)), _ranks(sub(hrv))
    raw = {i: (r_hr[k] + r_sp[k] + (1 - r_hrv[k])) / 3 for k, i in enumerate(asleep)}
    arousal = {i: sum(w * raw.get(i + d, raw[i]) for d, w in zip((-1, 0, 1), KERNEL))
               for i in asleep}
    order = _ranks([arousal[i] for i in asleep])
    rank = dict(zip(asleep, order))

    stage = [STAGE_AWAKE if awake[i] else STAGE_LIGHT for i in range(n)]
    still = [i for i in asleep if smooth[i] == 0]
    deep_pool = sorted((i for i in still if rank[i] <= DEEP_RANK
                        and i < onset + DEEP_FRAC * (n - onset)),
                       key=lambda i: arousal[i])
    for i in deep_pool[:int(DEEP_MAX * len(asleep))]:
        stage[i] = STAGE_DEEP
    rem_from = onset + REM_LATENCY_S // EPOCH_S
    rem_pool = sorted((i for i in asleep if stage[i] == STAGE_LIGHT
                       and rank[i] >= REM_RANK and i >= rem_from),
                      key=lambda i: -arousal[i])
    for i in rem_pool[:int(REM_MAX * len(asleep))]:
        stage[i] = STAGE_REM
    # a lone epoch between two identical stages is noise: absorb it
    for i in range(1, n - 1):
        if stage[i - 1] == stage[i + 1] != stage[i] and stage[i] != STAGE_AWAKE:
            stage[i] = stage[i - 1]

    secs = {s: 0.0 for s in (STAGE_AWAKE, STAGE_LIGHT, STAGE_DEEP, STAGE_REM)}
    changes, previous = [], None
    for i, s in enumerate(stage):
        secs[s] += min(EPOCH_S, tib_s - i * EPOCH_S)
        if s != previous:
            changes.append((bed + dt.timedelta(seconds=i * EPOCH_S), s))
            previous = s
    mins = {s: round(v / 60) for s, v in secs.items()}
    tib = round(tib_s / 60)
    tst = tib - mins[STAGE_AWAKE]
    return StagedNight(
        bedtime=bed, wake=wake, stages=changes, total_min=tst,
        deep_min=mins[STAGE_DEEP], light_min=mins[STAGE_LIGHT], rem_min=mins[STAGE_REM],
        awake_min=mins[STAGE_AWAKE], latency_min=round(onset * EPOCH_S / 60),
        efficiency=round(100 * tst / tib), time_in_bed_min=tib)

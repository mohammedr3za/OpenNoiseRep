# Release notes and data caveats

This file holds the confidence and provenance notes that are intentionally kept
out of the main UI. Update it when a mapping is validated or a new payload is
decoded.

## Status

Alpha. The transport, historical sync, store, desktop app, menu-bar app and CLI
are implemented and tested without hardware. Hardware validation is still open
for realtime HR, haptics, staged sleep, workout files and the packaged app.

## Metric confidence

- **Heart rate (metric 2)** — verified against the vendor capture and live runs.
- **Daily activity (metric 0)** — the four arrays reconcile exactly with the
  payload's own totals (steps 2644 = f9, calories 1511 = f10, active minutes
  194 = f13; distance sums to 1887 m). The channel decode is strong; the
  per-channel labels are evidence-based.
- **SpO2 (metric 4)**, **HRV (21)**, **stress (22)** and **RR interval (27)** —
  evidence-based from cadence, value range and cross-metric behaviour, but not
  cross-checked against the vendor app's screen for the same day.
- **Temperature (metric 23)** — unit unconfirmed. A worn vendor day reads 27–31
  (consistent with °C); an off-wrist live day reads 213, which is most likely a
  raw sensor/ADC value. The app shows a value only when it is in the plausible
  20–45 band and never invents a unit.
- **Resting HR** — an estimate (median of the lowest decile of heart-rate
  samples), not the vendor's RHR.
- **Unknown ids** stay `Channel N`; the neutral fallback is deliberate.

## Sleep

The band has only ever sent `*_RRI_Data.bin.txt` files in our captures. There is
no staged sleep file, sleep score, or light/deep/REM payload in the vendor
capture or the live syncs. The app derives a rest window from RRI (duration,
average HR, RMSSD/SDNN/pNN50, coverage) and labels it as a rest window. The
vendor app's 6h46m / score 68 is app/cloud-side. Do not add a score until a
staged sleep file is captured and decoded.

## RRI unit

The RRI payload is little-endian u16 slots, one per second. The unit is inferred
as milliseconds from the range and the `RRI` filename; 1/1024 s would scale it
by −2.3%. Validate against the band's own readout before calling it exact.

## Workouts

No workout/sport file has been received. The GUI and `noiserep workouts` show an
explicit empty state; sport-name detection is ready for the first one.

## Safety

Read-only by default. The only write path is the experimental, `--yes`-gated
`buzz` haptics command; sync, the GUI and the menu bar never call it. Never
commit captures, databases or screenshots containing a real MAC/serial or
personal health values.

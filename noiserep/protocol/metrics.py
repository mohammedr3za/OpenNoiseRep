"""Metric id -> human name, plus Phase-0 reverse-engineering notes.

Names are filled in as they are verified against observed value ranges and
vendor-app readings; unknown ids fall back to a generic label. Metric 2's
samples sit in a plausible resting-HR range, so it is tentatively heart rate.

NOTES (type-113/115 replies): each metric lives in its own sub-field of f9;
the body always carries an entry (date + metric id) in f1. Sample counts
range 24..2880 per day.
- f3 sample bytes with cadence in f2: metric 2 = 30 s; 4, 8, 22 = 5 s;
  21 = 15 s; 0, 11, 12, 23 = 60 s.
- metrics 14, 18, 19, 27, 25 carry bytes in f2 and no cadence; metric 25's
  bytes are a nested protobuf.
- metric 0 is a multi-array record (only the first array is exposed).
- metric 17 is scalars only (no bytes).
Precise units and identities are Phase 1 work.
"""

_KNOWN = {
    2: "heart_rate",  # tentative; verify against vendor-app values
}


def metric_name(metric_id: int) -> str:
    return _KNOWN.get(metric_id, f"metric_{metric_id}")

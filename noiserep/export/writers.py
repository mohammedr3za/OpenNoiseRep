"""CSV and JSON exporters that read sample rows from a `Store`.

Both honour an optional metric filter and a start/end datetime window, and
return the number of sample points written. CSV is a flat
``metric,timestamp,value`` table; JSON groups points under each metric name.
"""
from __future__ import annotations

import csv
import json


def _metrics(store, metric, start, end) -> list[str]:
    return [metric] if metric else store.metrics(start, end)


def export_csv(store, path: str, metric: str | None = None,
               start=None, end=None) -> int:
    n = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "timestamp", "value"])
        for m in _metrics(store, metric, start, end):
            for t, v in store.samples(m, start, end):
                w.writerow([m, t.isoformat(), v])
                n += 1
    return n


def export_json(store, path: str, metric: str | None = None,
                start=None, end=None) -> int:
    doc: dict[str, list] = {}
    n = 0
    for m in _metrics(store, metric, start, end):
        pts = [{"t": t.isoformat(), "v": v} for t, v in store.samples(m, start, end)]
        doc[m] = pts
        n += len(pts)
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
    return n

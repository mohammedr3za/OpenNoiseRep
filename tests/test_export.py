"""Exporter tests: CSV shape and JSON grouping from an in-memory Store."""
import csv
import datetime
import json

from noiserep.sync import SyncResult
from noiserep.protocol.decode import MetricSeries
from noiserep.store import Store
from noiserep.export import export_csv, export_json


def _store():
    r = SyncResult()
    r.series = [MetricSeries(datetime.datetime(2026, 10, 28, 1, 0, 0),
                             2, 30, [0, 93, 95], b"")]
    s = Store(":memory:")
    s.ingest(r)
    return s


def test_export_csv_shape(tmp_path):
    p = tmp_path / "out.csv"
    n = export_csv(_store(), str(p))
    assert n == 2
    rows = list(csv.reader(open(p)))
    assert rows[0] == ["metric", "timestamp", "value"]
    assert rows[1][0] == "heart_rate" and float(rows[1][2]) == 93.0
    assert rows[1][1] == "2026-10-28T01:00:30"


def test_export_json_grouped_and_filtered(tmp_path):
    p = tmp_path / "out.json"
    n = export_json(_store(), str(p), metric="heart_rate")
    assert n == 2
    doc = json.load(open(p))
    assert list(doc) == ["heart_rate"]
    assert doc["heart_rate"][0]["v"] == 93.0 and doc["heart_rate"][1]["v"] == 95.0

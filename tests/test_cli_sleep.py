"""CLI tests for the RRI-derived sleep and workout views (no hardware)."""
import datetime
import json

from noiserep.cli import main
from noiserep.protocol.files import RriFile, RriSample
from noiserep.store import Store
from noiserep.sync import SyncResult


def _seed(tmp_path) -> str:
    base = datetime.datetime(2026, 10, 28, 2, 0, 0)
    samples = [RriSample(i, 800, base + datetime.timedelta(seconds=i))
               for i in range(3600)]          # one hour, above the 30-min floor
    result = SyncResult()
    result.rri = [RriFile(start=base, samples=samples, total_slots=3600)]
    path = str(tmp_path / "noiserep.db")
    store = Store(path)
    store.ingest(result)
    store.close()
    return path


def test_sleep_prints_summary_and_insights(tmp_path, capsys):
    path = _seed(tmp_path)
    assert main(["sleep", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "last night:" in out
    assert "RMSSD" in out
    assert "coverage:" in out
    assert "baseline" in out


def test_sleep_json(tmp_path, capsys):
    path = _seed(tmp_path)
    out_file = tmp_path / "sleep.json"
    assert main(["sleep", "--db", path, "--json", str(out_file)]) == 0
    doc = json.loads(out_file.read_text())
    assert doc["session"]["source"] == "rri"
    assert doc["summary"]["beats"] == 3600
    assert doc["insights"]


def test_workouts_explains_the_empty_state(tmp_path, capsys):
    path = _seed(tmp_path)
    assert main(["workouts", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "no workout files received" in out
    assert "RRI" in out


def test_sleep_without_a_database_returns_5(tmp_path, capsys):
    assert main(["sleep", "--db", str(tmp_path / "missing.db")]) == 5
    assert "run `noiserep sync` first" in capsys.readouterr().err

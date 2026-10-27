import json
from noiserep.cli import main


def test_decode_prints_summary(capture_path, capsys):
    rc = main(["decode", capture_path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1.4.0" in out
    assert "2026-10-27" in out
    assert "2880" in out


def test_decode_json(capture_path, tmp_path, capsys):
    out_file = tmp_path / "decoded.json"
    rc = main(["decode", capture_path, "--json", str(out_file)])
    assert rc == 0
    data = json.loads(out_file.read_text())
    assert data["device"]["firmware"] == "1.4.0"
    assert any(s["samples"] == 2880 for s in data["series"])
    assert any(f["name"].endswith("RRI_Data.bin.txt") for f in data["files"])

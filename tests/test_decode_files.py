from noiserep.protocol.decode import decode_file


def test_files(capture_payloads):
    files = [decode_file(p.raw) for p in capture_payloads
             if p.cmd_type == 4097 and p.direction == "recv"]
    assert len(files) == 25
    assert all(f.name for f in files)
    assert all(len(f.data) > 0 for f in files)
    assert any(f.name == "2026-10-27-1748_RRI_Data.bin.txt" for f in files)

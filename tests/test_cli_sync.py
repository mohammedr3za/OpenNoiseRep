import noiserep.cli as cli


def test_sync_reports_band_not_found(monkeypatch, capsys):
    from noiserep.errors import BandNotFound

    def boom(*a, **k):
        raise BandNotFound("band 'Noise_REP_XXXX' not found")

    monkeypatch.setattr(cli, "_run_sync_async", boom)
    rc = cli.main(["sync", "--name", "Noise_REP_XXXX"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "not found" in err and "charger" in err.lower()


def test_sync_invalid_since_returns_2(capsys):
    rc = cli.main(["sync", "--since", "2026-13-40"])
    assert rc == 2
    assert "invalid" in capsys.readouterr().err


def test_parse_since_defaults_to_whole_device_history():
    # The band's clock may be unset (~2002), so the default must not be today.
    dt = cli._parse_since(None)
    assert dt.year == 2000 and (dt.month, dt.day) == (1, 1)


def test_parse_since_accepts_iso_date():
    dt = cli._parse_since("2026-10-27")
    assert (dt.year, dt.month, dt.day) == (2026, 10, 27)

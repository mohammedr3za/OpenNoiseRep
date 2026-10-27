from noiserep.errors import BandNotFound, TransportError, SyncTimeout


def test_errors_are_exceptions():
    for exc in (BandNotFound, TransportError, SyncTimeout):
        assert issubclass(exc, Exception)


def test_band_not_found_carries_name():
    err = BandNotFound("Noise_REP_33E3")
    assert "Noise_REP_33E3" in str(err)

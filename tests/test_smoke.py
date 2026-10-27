import noiserep

def test_package_has_version():
    assert isinstance(noiserep.__version__, str)
    assert noiserep.__version__

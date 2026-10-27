import os
import pathlib
import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def capture_path() -> str:
    env = os.environ.get("NOISEREP_TEST_PKLG")
    candidate = pathlib.Path(env) if env else _REPO_ROOT / "noisefit.pklg"
    if not candidate.exists():
        pytest.skip(f"capture not found at {candidate}; set NOISEREP_TEST_PKLG to run")
    return str(candidate)


@pytest.fixture(scope="session")
def capture_payloads(capture_path):
    from noiserep.capture import reassemble
    return reassemble(capture_path)

"""Tests for local user settings: defaults, validation, and atomic round-trips."""
import json
import os

import pytest

from noiserep import config as C


def test_defaults_are_valid_and_roundtrip(tmp_path):
    cfg = C.Config()
    cfg.validate()
    path = C.save(cfg, str(tmp_path / "config.json"))
    assert C.load(path) == cfg
    assert json.loads((tmp_path / "config.json").read_text()) == {
        "band_name": None, "db_path": None, "units": "metric",
        "profile": {"age": None, "height_cm": None, "weight_kg": None, "sex": None},
    }


def test_missing_file_returns_defaults(tmp_path):
    assert C.load(str(tmp_path / "nope.json")) == C.Config()


def test_profile_roundtrip(tmp_path):
    cfg = C.Config(
        band_name="Noise_REP_ABCD",
        db_path=str(tmp_path / "history.db"),
        units="imperial",
        profile=C.Profile(age=40, height_cm=180, weight_kg=75.5, sex="other"),
    )
    path = C.save(cfg, str(tmp_path / "config.json"))
    assert C.load(path) == cfg


def test_unknown_keys_are_ignored_for_forward_compatibility(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "units": "metric",
        "future_setting": {"nested": True},
        "profile": {"age": 30, "future_profile_key": 1},
    }))
    cfg = C.load(str(path))
    assert cfg.units == "metric" and cfg.profile.age == 30


@pytest.mark.parametrize("units", ["furlongs", "", None, 3])
def test_invalid_units_rejected(units):
    with pytest.raises(C.ConfigError):
        C.Config(units=units).validate()


@pytest.mark.parametrize("profile", [
    C.Profile(age=0),
    C.Profile(age=121),
    C.Profile(age="40"),
    C.Profile(height_cm=49),
    C.Profile(height_cm=251),
    C.Profile(weight_kg=19),
    C.Profile(weight_kg=401),
    C.Profile(sex="unknown"),
])
def test_invalid_profile_rejected(profile):
    with pytest.raises(C.ConfigError):
        profile.validate()


@pytest.mark.parametrize("kwargs", [
    {"band_name": ""},
    {"band_name": "   "},
    {"db_path": ""},
    {"profile": {"age": 30}},  # not a Profile instance
])
def test_invalid_config_values_rejected(kwargs):
    with pytest.raises(C.ConfigError):
        C.Config(**kwargs).validate()


def test_save_rejects_invalid_without_writing(tmp_path):
    path = tmp_path / "config.json"
    with pytest.raises(C.ConfigError):
        C.save(C.Config(units="furlongs"), str(path))
    assert not path.exists()


def test_load_rejects_malformed_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(C.ConfigError, match="not valid JSON"):
        C.load(str(path))


def test_from_dict_rejects_non_object_root():
    with pytest.raises(C.ConfigError):
        C.from_dict([("units", "metric")])
    with pytest.raises(C.ConfigError):
        C.from_dict({"profile": []})


@pytest.mark.skipif(os.name == "nt", reason="XDG_CONFIG_HOME is POSIX-only")
def test_default_config_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(C.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert C.default_config_path() == str(tmp_path / "noiserep" / "config.json")


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = C.save(C.Config(), str(tmp_path / "nested" / "config.json"))
    assert path.endswith("config.json")
    assert [p.name for p in (tmp_path / "nested").iterdir()] == ["config.json"]

"""Local user settings for noiserep.

Settings live in a JSON file in the per-OS user config directory and cover
presentation and defaults only: band name, database path, units, and an optional
profile used for estimates such as calories. **Nothing here is ever sent to the
band** -- the fetch path stays read-only. Keeping the profile local also keeps
health-adjacent data off the band and off the network.

The database itself is separate; see ``noiserep.store.default_db_path``. A
``db_path`` of ``None`` means "use the store's per-OS default".
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from dataclasses import asdict, dataclass, field

UNITS = ("metric", "imperial")
SEXES = (None, "female", "male", "other")


class ConfigError(ValueError):
    """A settings file or value is invalid."""


def default_config_path() -> str:
    """Per-OS user config file (``config.json``)."""
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "noiserep"
    elif os.name == "nt":
        base = pathlib.Path(os.environ.get("APPDATA", home)) / "noiserep"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (pathlib.Path(xdg) if xdg else home / ".config") / "noiserep"
    return str(base / "config.json")


@dataclass
class Profile:
    """Optional local-only fields used for estimates (calories, distance)."""

    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    sex: str | None = None

    def validate(self) -> None:
        if self.age is not None:
            if isinstance(self.age, bool) or not isinstance(self.age, int):
                raise ConfigError("profile.age must be an integer or null")
            if not 1 <= self.age <= 120:
                raise ConfigError("profile.age must be between 1 and 120")
        for name, value, lo, hi in (
            ("profile.height_cm", self.height_cm, 50, 250),
            ("profile.weight_kg", self.weight_kg, 20, 400),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{name} must be a number or null")
            if not lo <= value <= hi:
                raise ConfigError(f"{name} must be between {lo} and {hi}")
        if self.sex not in SEXES:
            raise ConfigError(f"profile.sex must be one of {SEXES}")


@dataclass
class Config:
    """User-facing defaults. CLI flags and GUI controls override these."""

    # None means "auto-detect a Noise_REP_* band" rather than a hard-coded name.
    band_name: str | None = None
    # None means "use store.default_db_path()".
    db_path: str | None = None
    units: str = "metric"
    profile: Profile = field(default_factory=Profile)

    def validate(self) -> None:
        if not isinstance(self.units, str) or self.units not in UNITS:
            raise ConfigError(f"units must be one of {UNITS}")
        for name, value in (("band_name", self.band_name), ("db_path", self.db_path)):
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{name} must be a non-empty string or null")
        if not isinstance(self.profile, Profile):
            raise ConfigError("profile must be a Profile object")
        self.profile.validate()


def from_dict(data: object) -> Config:
    """Build a Config from parsed JSON, ignoring unknown keys.

    Unknown keys are ignored for forward compatibility: a newer release may add
    settings that an older one simply drops. Invalid *known* keys raise
    ConfigError.
    """
    if not isinstance(data, dict):
        raise ConfigError("settings root must be a JSON object")
    profile_data = data.get("profile", {})
    if profile_data is None:
        profile_data = {}
    if not isinstance(profile_data, dict):
        raise ConfigError("profile must be a JSON object")
    profile = Profile(
        age=profile_data.get("age"),
        height_cm=profile_data.get("height_cm"),
        weight_kg=profile_data.get("weight_kg"),
        sex=profile_data.get("sex"),
    )
    config = Config(
        band_name=data.get("band_name"),
        db_path=data.get("db_path"),
        units=data.get("units", "metric"),
        profile=profile,
    )
    config.validate()
    return config


def to_dict(config: Config) -> dict:
    return asdict(config)


def load(path: str | None = None) -> Config:
    """Load settings, returning defaults when the file does not exist."""
    target = pathlib.Path(path or default_config_path())
    if not target.exists():
        return Config()
    try:
        with target.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target} is not valid JSON: {exc}") from exc
    return from_dict(data)


def save(config: Config, path: str | None = None) -> str:
    """Validate and atomically write settings; returns the path written."""
    config.validate()
    target = pathlib.Path(path or default_config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(to_dict(config), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp",
                               dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return str(target)


__all__ = [
    "UNITS", "SEXES", "ConfigError", "Profile", "Config",
    "default_config_path", "from_dict", "to_dict", "load", "save",
]

"""Configuration loading and validation (SPEC §7)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MAX_RETENTION_DAYS = 90
MODES = ("home", "workplace")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
FEATURES = ("rssi_std", "rssi_mad", "delta_energy", "tx_rate_changes")


class ConfigError(ValueError):
    pass


def data_dir() -> Path:
    override = os.environ.get("WIFI_UTILITY_HOME")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "wifi_utility"


@dataclass
class DetectorConfig:
    trigger: float = 3.5
    trigger_secs: float = 3
    release: float = 2.0
    release_secs: float = 10
    calibration_secs: float = 180
    drift_alpha: float = 0.001
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "rssi_std": 1.0,
            "rssi_mad": 1.0,
            "delta_energy": 1.5,
            "tx_rate_changes": 0.5,
        }
    )


@dataclass
class HomeConfig:
    exit_delay_secs: float = 60
    schedule: list[str] = field(default_factory=list)
    webhook_url: str = ""
    ntfy_url: str = ""
    debug_capture: bool = False


@dataclass
class WorkplaceConfig:
    zone_id: str = "meeting-room-1"
    notice_days: float = 7
    retention_days: int = 90
    after_hours_alert: bool = False


@dataclass
class DashboardConfig:
    bind: str = "127.0.0.1"
    port: int = 8765
    password: str = ""


@dataclass
class Config:
    mode: str = "home"
    sample_hz: float = 5
    window_secs: float = 5
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    home: HomeConfig = field(default_factory=HomeConfig)
    workplace: WorkplaceConfig = field(default_factory=WorkplaceConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    source_path: Path | None = None

    def validate(self) -> Config:
        if self.mode not in MODES:
            raise ConfigError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not 1 <= self.sample_hz <= 20:
            raise ConfigError("sample_hz must be between 1 and 20")
        if self.window_secs < 2:
            raise ConfigError("window_secs must be at least 2")
        d = self.detector
        if d.trigger <= d.release:
            raise ConfigError("detector.trigger must be greater than detector.release")
        if d.trigger_secs < 0 or d.release_secs < 0:
            raise ConfigError("detector durations must not be negative")
        if d.calibration_secs < 30:
            raise ConfigError("detector.calibration_secs must be at least 30")
        if not 0 <= d.drift_alpha < 0.1:
            raise ConfigError("detector.drift_alpha must be in [0, 0.1)")
        unknown = set(d.weights) - set(FEATURES)
        if unknown:
            raise ConfigError(f"unknown detector weights: {sorted(unknown)}")
        if sum(d.weights.values()) <= 0:
            raise ConfigError("detector weights must sum to more than 0")
        w = self.workplace
        # Rejected, not clamped (SPEC §6.4).
        if not 1 <= w.retention_days <= MAX_RETENTION_DAYS:
            raise ConfigError(f"workplace.retention_days must be 1..{MAX_RETENTION_DAYS}")
        if w.notice_days < 0:
            raise ConfigError("workplace.notice_days must not be negative")
        if self.mode == "workplace" and self.home.debug_capture:
            raise ConfigError("debug capture is not allowed in workplace mode")
        if self.dashboard.bind not in LOOPBACK and not self.dashboard.password:
            raise ConfigError("dashboard.password is required when binding to a non-local address")
        return self


def _section(cls, raw: dict, name: str):
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ConfigError(f"[{name}] must be a table")
    allowed = cls.__dataclass_fields__
    unknown = set(values) - set(allowed)
    if unknown:
        raise ConfigError(f"unknown keys in [{name}]: {sorted(unknown)}")
    return cls(**values)


def default_config_path() -> Path | None:
    for candidate in (Path.cwd() / "config.toml", data_dir() / "config.toml"):
        if candidate.exists():
            return candidate
    return None


def load(path: str | Path | None = None, mode: str | None = None) -> Config:
    """Load config from `path` (or the default locations), apply a mode override, validate."""
    resolved = Path(path) if path else default_config_path()
    raw: dict = {}
    if resolved:
        if not resolved.exists():
            raise ConfigError(f"config file not found: {resolved}")
        with resolved.open("rb") as f:
            raw = tomllib.load(f)
    top = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    unknown = set(top) - {"mode", "sample_hz", "window_secs"}
    if unknown:
        raise ConfigError(f"unknown top-level keys: {sorted(unknown)}")
    cfg = Config(
        **top,
        detector=_section(DetectorConfig, raw, "detector"),
        home=_section(HomeConfig, raw, "home"),
        workplace=_section(WorkplaceConfig, raw, "workplace"),
        dashboard=_section(DashboardConfig, raw, "dashboard"),
        source_path=resolved,
    )
    if mode:
        cfg.mode = mode
    return cfg.validate()

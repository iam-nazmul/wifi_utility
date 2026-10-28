from __future__ import annotations

import pytest

from wifi_utility.config import Config
from wifi_utility.store import Store


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test's database and config away from the real user data directory."""
    monkeypatch.setenv("WIFI_UTILITY_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def cfg() -> Config:
    return Config().validate()


@pytest.fixture
def home_store(tmp_path) -> Store:
    return Store(tmp_path / "home.db")


@pytest.fixture
def wp_store(tmp_path) -> Store:
    return Store(tmp_path / "wp.db", workplace=True)

"""Where the app keeps its files (per-user, per-OS)."""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

from .. import APP_NAME, APP_ORG


def _override() -> Path | None:
    """Allow SKYDISPATCH_HOME to relocate everything (tests, portable installs)."""
    value = os.environ.get("SKYDISPATCH_HOME")
    return Path(value).expanduser() if value else None


def data_dir() -> Path:
    base = _override()
    path = base / "data" if base else Path(user_data_dir(APP_NAME, APP_ORG))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    base = _override()
    path = base / "config" if base else Path(user_config_dir(APP_NAME, APP_ORG))
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    base = _override()
    path = base / "logs" if base else Path(user_log_dir(APP_NAME, APP_ORG))
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "career.db"


def config_path() -> Path:
    return config_dir() / "settings.json"

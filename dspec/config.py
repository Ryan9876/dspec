from __future__ import annotations

import os
from pathlib import Path

HOST = "127.0.0.1"
PORT = 3210
APP_VERSION = "0.1.0"
BUILD_HASH = os.environ.get("DSPEC_BUILD_HASH", "dev-uncommitted")


def dspec_home() -> Path:
    override = os.environ.get("DSPEC_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".dspec"
    root.mkdir(parents=True, exist_ok=True)
    return root


def runtime_dir() -> Path:
    p = dspec_home() / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return runtime_dir() / "dspec.db"


def config_path() -> Path:
    return dspec_home() / "config.json"


def pid_path() -> Path:
    return runtime_dir() / "dspec.pid"


def log_path() -> Path:
    p = runtime_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "dspec.log"

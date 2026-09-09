from __future__ import annotations

import json
import os
from importlib import metadata
from pathlib import Path

HOST = "127.0.0.1"
PORT = 3210


def resolve_app_version(root: Path | None = None) -> str:
    override = os.environ.get("DSPEC_APP_VERSION")
    if override:
        return override.strip()
    package_root = root or Path(__file__).resolve().parents[1]
    info_path = package_root / "build-info.json"
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
        version = str(data.get("version", "")).strip().lstrip("v")
        if version:
            return version
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return metadata.version("dspec-ai")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


APP_VERSION = resolve_app_version()


def resolve_build_hash(root: Path | None = None) -> str:
    override = os.environ.get("DSPEC_BUILD_HASH")
    if override:
        return override
    package_root = root or Path(__file__).resolve().parents[1]
    info_path = package_root / "build-info.json"
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
        build_hash = str(data.get("build_hash", "")).strip()
        if build_hash:
            return build_hash
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return "dev-uncommitted"


BUILD_HASH = resolve_build_hash()


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

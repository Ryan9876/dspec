from __future__ import annotations

from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DSPEC_DB_PATH", str(tmp_path / "runtime" / "dspec.db"))
    monkeypatch.setenv("DSPEC_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("DSPEC_CREDENTIAL_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("DSPEC_DISABLE_KEYCHAIN", "1")
    from backend.dspec_app.db import init_db
    init_db()
    yield

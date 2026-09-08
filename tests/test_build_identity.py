from __future__ import annotations

import json
from pathlib import Path

from dspec.config import resolve_build_hash


def test_build_hash_defaults_to_dev_uncommitted(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DSPEC_BUILD_HASH", raising=False)
    assert resolve_build_hash(tmp_path) == "dev-uncommitted"


def test_build_hash_reads_packaged_build_info(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DSPEC_BUILD_HASH", raising=False)
    (tmp_path / "build-info.json").write_text(
        json.dumps({"build_hash": "abc123", "version": "0.1.0"}),
        encoding="utf-8",
    )
    assert resolve_build_hash(tmp_path) == "abc123"


def test_environment_build_hash_override_wins(tmp_path: Path, monkeypatch):
    (tmp_path / "build-info.json").write_text(
        json.dumps({"build_hash": "packaged"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DSPEC_BUILD_HASH", "explicit")
    assert resolve_build_hash(tmp_path) == "explicit"

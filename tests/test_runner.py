from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from dspec import runner


def write_release(root: Path, *, version: str, build: str, state: str = "validated", minimum_runner: str = "0.1.1") -> Path:
    current = root / "current"
    current.mkdir(parents=True, exist_ok=True)
    package = current / f"DSpec-v{version}.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("build-info.json", json.dumps({
            "version": version,
            "build_hash": build,
            "source_repository": "Ryan9876/dspec",
        }))
        archive.writestr("dspec/app.py", "# release fixture\n")
        archive.writestr("dspec/runner.py", "# release fixture\n")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = {
        "release_version": version,
        "build_hash": build,
        "minimum_runner_version": minimum_runner,
        "package_filename": package.name,
        "sha256": digest,
        "validation_state": state,
    }
    manifest_path = current / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


@pytest.fixture()
def release_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("DSPEC_HOME", str(home))
    monkeypatch.delenv("DSPEC_APP_ROOT", raising=False)
    monkeypatch.setattr(runner, "_install_release_dependencies", lambda _temp: None)
    return home


def test_semver_parser_is_simple_and_strict():
    assert runner._semver_tuple("0.1.1") == (0, 1, 1)
    assert runner._semver_tuple("v1.2.3") == (1, 2, 3)
    with pytest.raises(RuntimeError):
        runner._semver_tuple("candidate-1")


def test_validated_release_becomes_active_runtime(release_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = write_release(tmp_path / "drive", version="0.1.2", build="release-build-012")
    monkeypatch.setenv("DSPEC_RELEASE_MANIFEST", str(manifest))

    result = runner._verify_and_apply_release()

    assert result["status"] == "release_applied"
    assert result["release_version"] == "0.1.2"
    active = release_home / "runtime" / "app"
    assert json.loads((active / "build-info.json").read_text())["version"] == "0.1.2"
    assert runner._source_root() == active.resolve()
    marker = json.loads((release_home / "runtime" / "active-release.json").read_text())
    assert marker["version"] == "0.1.2"
    assert marker["build_hash"] == "release-build-012"


def test_candidate_release_is_never_auto_installed(release_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = write_release(tmp_path / "drive", version="0.1.2", build="candidate-build", state="candidate")
    monkeypatch.setenv("DSPEC_RELEASE_MANIFEST", str(manifest))

    result = runner._verify_and_apply_release()

    assert result["status"] == "manifest_not_validated"
    assert not (release_home / "runtime" / "app").exists()


def test_runner_version_gate_prevents_incompatible_update(release_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = write_release(
        tmp_path / "drive",
        version="0.2.0",
        build="future-build",
        minimum_runner="99.0.0",
    )
    monkeypatch.setenv("DSPEC_RELEASE_MANIFEST", str(manifest))

    result = runner._verify_and_apply_release()

    assert result["status"] == "runner_update_required"
    assert not (release_home / "runtime" / "app").exists()


def test_release_rollback_restores_prior_runtime(release_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first = write_release(tmp_path / "drive-one", version="0.1.2", build="build-one")
    monkeypatch.setenv("DSPEC_RELEASE_MANIFEST", str(first))
    assert runner._verify_and_apply_release()["status"] == "release_applied"

    second = write_release(tmp_path / "drive-two", version="0.1.3", build="build-two")
    monkeypatch.setenv("DSPEC_RELEASE_MANIFEST", str(second))
    assert runner._verify_and_apply_release()["status"] == "release_applied"

    rollback = runner._rollback_release()

    assert rollback["status"] == "rolled_back"
    active_info = json.loads((release_home / "runtime" / "app" / "build-info.json").read_text())
    assert active_info["version"] == "0.1.2"
    marker = json.loads((release_home / "runtime" / "active-release.json").read_text())
    assert marker["version"] == "0.1.2"

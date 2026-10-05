from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.solver.beat_runtime import discovery, paths


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(tmp_path / "hbb"))
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda *args, **kwargs: None)


def executable(path: Path, content: bytes = b"fake Julia") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755)
    return path


def test_precedence_and_record_has_no_readiness(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    selected = [executable(tmp_path / name) for name in ("explicit", "configured", "recorded", "path")]
    discovery.write_julia_record(root, selected[2], origin="external", version=None)
    monkeypatch.setattr(discovery.shutil, "which", lambda *args, **kwargs: str(selected[3]))
    monkeypatch.setenv(discovery.JULIA_ENV_VAR, str(selected[1]))
    assert discovery.discover_julia(str(selected[0])) == str(selected[0])
    assert discovery.discover_julia() == str(selected[1])
    monkeypatch.delenv(discovery.JULIA_ENV_VAR)
    assert discovery.discover_julia() == str(selected[2])
    record = discovery.read_julia_record(root)
    assert record == {
        "provider": paths.PROVIDER_ID, "state_schema": paths.STATE_SCHEMA,
        "julia_executable": str(selected[2]), "origin": "external",
        "julia_version": None, "julia_identity": discovery.executable_identity(selected[2]),
    }
    assert not list(root.glob("state*.json"))
    selected[2].unlink()
    assert discovery.discover_julia() == str(selected[3])


@pytest.mark.parametrize("source", ["explicit", "configured", "env"])
@pytest.mark.parametrize("kind", ["missing", "directory", "nonexecutable"])
def test_bad_selection_is_error_without_fallthrough(tmp_path, monkeypatch, source, kind):
    if kind == "nonexecutable" and discovery.os.name == "nt":
        pytest.skip("Windows executability is a file check")
    candidate = tmp_path / kind
    if kind == "directory":
        candidate.mkdir()
    if kind == "nonexecutable":
        candidate.write_bytes(b"not executable")
        candidate.chmod(0o600)
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid selection fell through")
    monkeypatch.setattr(discovery, "recorded_julia", forbidden)
    kwargs = {source: str(candidate)} if source != "env" else {}
    if source == "env":
        monkeypatch.setenv(discovery.JULIA_ENV_VAR, str(candidate))
    with pytest.raises(discovery.JuliaDiscoveryError, match="Invalid"):
        discovery.discover_julia(**kwargs)


@pytest.mark.parametrize("contents", ["{", "[]", '{"provider":"hornlab-beat"}', '{"provider":"wg-beat-engine","state_schema":2}'])
def test_corrupt_or_foreign_record_is_ignored(tmp_path, contents):
    root = paths.runtime_dir()
    root.mkdir(parents=True)
    (root / "julia.json").write_text(contents)
    assert discovery.read_julia_record() is None
    assert discovery.discover_julia() is None


def test_edited_recorded_binary_revokes_hint(tmp_path):
    path = executable(tmp_path / "julia")
    discovery.write_julia_record(paths.runtime_dir(), path, origin="external", version=None)
    path.write_bytes(b"replaced")
    assert discovery.discover_julia() is None


@pytest.mark.parametrize("name", ["state-cpu.json", "state-metal.json", "state.json"])
@pytest.mark.parametrize("status", ["ready", "failed", "in_progress"])
def test_legacy_record_is_only_a_readonly_hint(tmp_path, name, status):
    legacy = tmp_path / "hbb"
    path = executable(legacy / "julia-1.12.6/bin/julia")
    record = legacy / name
    content = json.dumps({"status": status, "julia_executable": str(path), "backend": "cpu"})
    record.write_text(content)
    before = sorted(p.relative_to(legacy) for p in legacy.rglob("*"))
    assert discovery.legacy_executable_hint(legacy) == str(path)
    assert discovery.discover_julia() is None
    assert not paths.runtime_dir().exists()
    assert record.read_text() == content
    assert sorted(p.relative_to(legacy) for p in legacy.rglob("*")) == before


def test_legacy_missing_or_invalid_binary(tmp_path):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    (legacy / "state-cpu.json").write_text('{"julia_executable":42}')
    (legacy / "state.json").write_text(json.dumps({"status": "ready", "julia_executable": str(legacy / "removed")}))
    assert discovery.legacy_executable_hint(legacy) is None


def test_injected_environment_and_whitespace(tmp_path, monkeypatch):
    path = executable(tmp_path / "julia")
    env = {paths.RUNTIME_DIR_ENV: str(tmp_path / "other"), discovery.JULIA_ENV_VAR: f" {path} "}
    assert discovery.discover_julia("  ", environ=env) == str(path)
    calls = []
    monkeypatch.setattr(discovery.shutil, "which", lambda name, **kw: calls.append(kw) or None)
    assert discovery.discover_julia(environ={"PATH": "/fake"}) is None
    assert calls == [{"path": "/fake"}]


def test_atomic_write_failure_preserves_old_record(tmp_path, monkeypatch):
    root = paths.runtime_dir()
    path = executable(tmp_path / "julia")
    discovery.write_julia_record(root, path, origin="external", version=None)
    before = (root / "julia.json").read_bytes()
    def fail(*args):
        raise OSError("interrupted replace")
    monkeypatch.setattr(discovery.os, "replace", fail)
    with pytest.raises(OSError, match="interrupted"):
        discovery.write_julia_record(root, path, origin="managed", version="1.12.7")
    assert (root / "julia.json").read_bytes() == before
    assert [p.name for p in root.iterdir()] == ["julia.json"]

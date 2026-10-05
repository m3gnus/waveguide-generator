from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.solver.beat_runtime import paths, state


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    return paths.runtime_dir()


@pytest.fixture
def record(tmp_path):
    return {
        "backend": "cpu", "status": "ready", "step": "done", "error": None,
        "project": str(tmp_path / "project"), "engine_fingerprint": "engine-sha",
        "runtime_fingerprint": "runtime-sha", "julia_executable": str(tmp_path / "julia"),
        "julia_version": "1.12.7", "julia_identity": "executable-sha",
        "depot": str(tmp_path / "depot"), "environment": {"JULIA_NUM_THREADS": "4"},
        "sysimage": None, "sysimage_identity": None, "probe_contract": "system-v1",
        "probe_fixture_identity": "fixture-sha",
        "completion": {"terminal_count": 1, "finite": True, "nonzero": True},
    }


def test_atomic_roundtrip_and_input_unchanged(directory, record, monkeypatch):
    assert state.read_state(backend="cpu") is None
    assert not directory.exists()
    saved = state.write_state(record)
    original_replace = state.os.replace

    def inspect(source, target):
        assert Path(source).parent == directory
        assert state.read_state(backend="cpu") == saved
        assert json.loads(Path(source).read_text())["status"] == "failed"
        original_replace(source, target)

    monkeypatch.setattr(state.os, "replace", inspect)
    replacement = state.write_state(dict(record, status="failed", error="offline"))
    assert state.read_state(backend="cpu") == replacement
    assert replacement["provider"] == paths.PROVIDER_ID
    assert replacement["state_schema"] == paths.STATE_SCHEMA
    assert "updated_at" not in record
    assert list(directory.iterdir()) == [directory / "state-cpu.json"]


@pytest.mark.parametrize("bad", ["{", "[]", "null", "42", "\xff"])
def test_corrupt_records_are_absent(directory, bad):
    directory.mkdir(parents=True)
    for name in ("state-cpu.json", "julia.json"):
        (directory / name).write_bytes(bad.encode("latin-1"))
    assert state.read_state(backend="cpu") is None
    assert state.read_julia() is None
    assert state.read_backend_states() == {}


@pytest.mark.parametrize(("field", "value"), [
    ("provider", None), ("provider", "hornlab-beat"),
    ("state_schema", 2), ("state_schema", True), ("state_schema", "1"),
    ("backend", "metal"), ("status", "unknown"), ("step", []),
    ("error", 12), ("engine_fingerprint", {}), ("environment", {"X": 2}),
    ("completion", []), ("completion", {"value": float("nan")}),
])
def test_foreign_or_malformed_backend_records(directory, record, field, value):
    saved = state.write_state(record)
    saved[field] = value
    (directory / "state-cpu.json").write_text(json.dumps(saved))
    assert state.read_state(backend="cpu") is None


@pytest.mark.parametrize("missing", ["provider", "state_schema", "project", "probe_contract", "completion"])
def test_incomplete_backend_records_are_absent(directory, record, missing):
    saved = state.write_state(record)
    del saved[missing]
    (directory / "state-cpu.json").write_text(json.dumps(saved))
    assert state.read_state(backend="cpu") is None


@pytest.mark.parametrize("failing", ["cpu", "metal"])
def test_backend_failure_never_erases_other_success_or_julia(directory, record, failing):
    other = "metal" if failing == "cpu" else "cpu"
    ready = state.write_state(dict(record, backend=other))
    julia = state.write_julia({"origin": "managed", "executable": record["julia_executable"],
                               "version": "1.12.7", "identity": "julia-sha"})
    before = (directory / f"state-{other}.json").read_bytes()
    state.write_state(dict(record, backend=failing))
    assert {name: value["status"] for name, value in state.read_backend_states().items()} == {
        "cpu": "ready", "metal": "ready",
    }
    for status in ("in_progress", "failed"):
        state.write_state(dict(record, backend=failing, status=status, error="offline"))
    assert state.read_backend_states()[other] == ready
    assert state.read_state(backend=failing)["status"] == "failed"
    assert (directory / f"state-{other}.json").read_bytes() == before
    assert state.read_julia() == julia
    assert not (directory / "state.json").exists()


def test_failed_replace_preserves_previous_record_and_cleans_scratch(directory, record, monkeypatch):
    ready = state.write_state(record)

    def refuse(*args):
        raise OSError("disk full")

    monkeypatch.setattr(state.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        state.write_state(dict(record, status="failed"))
    assert state.read_state(backend="cpu") == ready
    assert sorted(p.name for p in directory.iterdir()) == ["state-cpu.json"]


@pytest.mark.parametrize("backend", ["../escape", "", "cuda", "rocm", None])
def test_invalid_backend_writes_nothing(directory, record, backend):
    with pytest.raises(ValueError, match="Unsupported"):
        state.write_state(dict(record, backend=backend))
    assert not directory.exists()


def test_legacy_mirror_is_ignored(directory):
    directory.mkdir(parents=True)
    (directory / "state.json").write_text('{"backend":"cpu","status":"ready"}')
    assert state.read_state(backend="cpu") is None
    assert state.read_backend_states() == {}
    assert state.read_julia() is None


def test_unresolved_failure_retains_explicit_unknown_identities(directory, record):
    for name in ("project", "engine_fingerprint", "runtime_fingerprint", "julia_executable",
                 "julia_version", "julia_identity", "depot", "probe_contract", "probe_fixture_identity"):
        record[name] = None
    failed = state.write_state(dict(record, status="failed", step="lock", error="ENOLCK", completion={}))
    assert state.read_state(backend="cpu") == failed


def test_writes_ignore_hbb_directory_override(directory, record, tmp_path, monkeypatch):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    marker = legacy / "state-cpu.json"
    marker.write_bytes(b"old HBB state")
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    state.write_state(record)
    assert (directory / "state-cpu.json").exists()
    assert list(legacy.iterdir()) == [marker]
    assert marker.read_bytes() == b"old HBB state"


@pytest.mark.parametrize("origin", ["managed", "external"])
def test_julia_roundtrip_and_foreign_rejection(directory, origin):
    saved = state.write_julia({"origin": origin, "executable": str(directory / "julia"),
                               "version": "1.12.7", "identity": "julia-sha"})
    assert state.read_julia() == saved
    for field, value in (("provider", "hbb"), ("state_schema", 2), ("origin", "legacy"),
                         ("executable", ""), ("identity", None)):
        (directory / "julia.json").write_text(json.dumps(dict(saved, **{field: value})))
        assert state.read_julia() is None
        with pytest.raises(ValueError):
            state.write_julia(dict(saved, **{field: value}))


def test_writer_refuses_foreign_and_unserializable_records(directory, record):
    for extra in ({"provider": "hbb"}, {"state_schema": True}, {"completion": {"value": float("nan")}}):
        with pytest.raises(ValueError):
            state.write_state(dict(record, **extra))
    assert not directory.exists()


@pytest.mark.parametrize("failures", [1, 4, 5])
def test_windows_reader_replace_retry_is_bounded(directory, record, monkeypatch, failures):
    saved = state.write_state(record)
    replace = state.os.replace
    attempts, delays = [], []

    def sharing_violation(source, target):
        attempts.append((source, target))
        if len(attempts) <= failures:
            assert state.read_state(backend="cpu") == saved
            raise PermissionError("reader denies delete sharing")
        replace(source, target)

    monkeypatch.setattr(state, "_WINDOWS", True)
    monkeypatch.setattr(state.os, "replace", sharing_violation)
    monkeypatch.setattr(state.time, "sleep", delays.append)
    if failures == 5:
        with pytest.raises(PermissionError):
            state.write_state(dict(record, status="failed"))
        assert state.read_state(backend="cpu") == saved
    else:
        assert state.write_state(dict(record, status="failed"))["status"] == "failed"
    assert len(attempts) == min(failures + 1, 5)
    assert len(delays) == min(failures, 4) and sum(delays) <= 0.25
    assert len({str(source) for source, _ in attempts}) == 1
    assert [p.name for p in directory.iterdir()] == ["state-cpu.json"]


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_replace_other_errors_and_posix_are_not_retried(directory, record, monkeypatch, error):
    attempts = []

    def fail(*args):
        attempts.append(args)
        raise error("failed replacement")

    monkeypatch.setattr(state, "_WINDOWS", False)
    monkeypatch.setattr(state.os, "replace", fail)
    with pytest.raises(error):
        state.write_state(record)
    assert len(attempts) == 1 and list(directory.iterdir()) == []


def test_external_julia_unknown_version_is_explicit(directory):
    saved = state.write_julia({"origin": "external", "executable": "fake", "identity": "sha", "version": None})
    assert state.read_julia() == saved
    with pytest.raises(ValueError):
        state.write_julia({"origin": "external", "executable": "fake", "identity": "sha"})
    with pytest.raises(ValueError):
        state.write_julia({"origin": "managed", "executable": "fake", "identity": "sha", "version": None})


@pytest.mark.parametrize("api", ["write_state", "write_julia", "atomic_json"])
@pytest.mark.parametrize("alias", [False, True])
def test_explicit_directory_hbb_isolation_before_mutation(tmp_path, monkeypatch, record, api, alias):
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    keep = legacy / "keep"
    keep.write_bytes(b"HBB")
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    root = tmp_path / "alias" if alias else legacy
    if alias:
        root.symlink_to(legacy, target_is_directory=True)
    with pytest.raises(paths.RootConflict):
        if api == "write_state":
            state.write_state(record, root)
        elif api == "write_julia":
            state.write_julia({"origin": "external", "executable": "fake", "identity": "sha", "version": None}, root)
        else:
            state._atomic_write_json(root / "record.json", {})
    assert list(legacy.iterdir()) == [keep] and keep.read_bytes() == b"HBB"


@pytest.mark.parametrize("name", ["julia.json", "state-cpu.json"])
def test_recursive_state_json_is_absent(directory, name):
    directory.mkdir(parents=True)
    (directory / name).write_text('[' * 1500 + '0' + ']' * 1500)
    assert state.read_julia() is None
    assert state.read_state(backend="cpu") is None


@pytest.mark.parametrize("writer", ["state", "julia"])
def test_stale_temporary_sweep_preserves_active_and_linked_siblings(directory, record, writer):
    directory.mkdir(parents=True)
    name = "state-cpu.json" if writer == "state" else "julia.json"
    stale = directory / f".{name}.old.tmp"
    active = directory / f".{name}.active.tmp"
    linked = directory / f".{name}.link.tmp"
    stale.write_bytes(b"old")
    active.write_bytes(b"active")
    state.os.utime(stale, (0, 0))
    linked.symlink_to(stale)
    if writer == "state":
        state.write_state(record)
    else:
        state.write_julia({"origin": "external", "executable": "fake", "identity": "sha", "version": None})
    assert not stale.exists() and active.read_bytes() == b"active" and linked.is_symlink()


@pytest.mark.parametrize("failures", [1, 5])
def test_windows_julia_record_sharing_violation_retry(directory, monkeypatch, failures):
    saved = state.write_julia({"origin": "external", "executable": "old", "identity": "sha", "version": None})
    original = state.os.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        assert state.read_julia() == saved
        if len(calls) <= failures:
            raise PermissionError("sharing violation")
        original(source, target)

    monkeypatch.setattr(state, "_WINDOWS", True)
    monkeypatch.setattr(state.os, "replace", replace)
    monkeypatch.setattr(state.time, "sleep", lambda _: None)
    replacement = dict(saved, executable="new")
    if failures == 5:
        with pytest.raises(PermissionError):
            state.write_julia(replacement)
        assert state.read_julia() == saved
    else:
        assert state.write_julia(replacement)["executable"] == "new"
    assert len(calls) == min(failures + 1, 5)
    assert list(directory.iterdir()) == [directory / "julia.json"]

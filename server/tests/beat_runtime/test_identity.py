from pathlib import Path
import shutil

import pytest

from server.solver.beat_runtime.assets import EngineAssets
from server.solver.beat_runtime import identity


ENGINE_FILES = (
    "__init__.py", "worker.py", "nested/client.py", "beat_contract/worker.py",
    "beat_contract/system-v1.schema.json", "beat_contract/fixtures/request.json",
    "julia_local/solver.jl", "julia_local/coupled_solver.jl", "julia_local/src/nested/driver.jl",
    "julia_local/Project.toml", "julia_local/Manifest.toml", "julia_local/Manifest-v1.12.toml",
    "julia_metal/Project.toml", "julia_metal/Manifest-v1.12.toml",
    "julia_engine/CompiledCpu/Project.toml", "julia_engine/CompiledCpu/Manifest-v1.12.toml",
    "julia_engine/CompiledCpu/src/CompiledCpu.jl",
)


def _write(root, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"bytes of {name}")


def _assets(root):
    return EngineAssets(root, root / "julia_local", root / "julia_local/coupled_solver.jl",
                        root / "julia_local/solver.jl")


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / "engine"
    _write(root, ENGINE_FILES)
    return _assets(root)


def test_same_bytes_relocated_are_identical(engine, tmp_path):
    project, sysimage = tmp_path / "custom", tmp_path / "image.so"
    _write(project, ["Project.toml", "Manifest-v1.12.toml", "src/local.jl"])
    sysimage.write_bytes(b"sysimage")
    copied = tmp_path / "relocated"
    shutil.copytree(tmp_path / "engine", copied / "engine")
    shutil.copytree(project, copied / "custom")
    shutil.copyfile(sysimage, copied / "renamed-image.so")
    original = identity.engine_fingerprint(engine, julia_project=project, julia_sysimage=sysimage)
    assert identity.engine_fingerprint(_assets(copied / "engine"), julia_project=copied / "custom",
                                       julia_sysimage=copied / "renamed-image.so") == original


@pytest.mark.parametrize("name", ENGINE_FILES)
def test_every_engine_input_invalidates_without_a_cache(engine, name):
    before = identity.engine_fingerprint(engine)
    path = engine.root / name
    # Same length, no reliance on timestamp changes.
    path.write_bytes(b"x" * len(path.read_bytes()))
    assert identity.engine_fingerprint(engine) != before


@pytest.mark.parametrize("name", ["Project.toml", "Manifest.toml", "Manifest-v1.12.toml", "src/local.jl", "image.so"])
def test_custom_project_and_sysimage_edits_invalidate(engine, tmp_path, name):
    project = tmp_path / "custom"
    _write(project, ["Project.toml", "Manifest.toml", "Manifest-v1.12.toml", "src/local.jl"])
    sysimage = project / "image.so"
    sysimage.write_bytes(b"image")
    args = dict(julia_project=project / "Project.toml", julia_sysimage=sysimage)
    before = identity.engine_fingerprint(engine, **args)
    (project / name).write_text("changed")
    assert identity.engine_fingerprint(engine, **args) != before


def test_add_delete_and_rename_invalidate(engine):
    before = identity.engine_fingerprint(engine)
    added = engine.root / "new.py"
    added.write_text("new")
    assert identity.engine_fingerprint(engine) != before
    added.unlink()
    assert identity.engine_fingerprint(engine) == before
    (engine.root / "worker.py").rename(engine.root / "other.py")
    assert identity.engine_fingerprint(engine) != before


@pytest.mark.parametrize("name", ["__init__.py", "beat_contract/system-v1.schema.json", "julia_local/Project.toml", "julia_local/coupled_solver.jl", "julia_local/solver.jl"])
def test_required_missing_input_makes_identity_unavailable(engine, name):
    (engine.root / name).unlink()
    with pytest.raises(identity.IdentityUnavailable, match="Cannot read"):
        identity.engine_fingerprint(engine)


def test_unreadable_input_makes_identity_unavailable(engine, monkeypatch):
    original = Path.open

    def deny(path, *args, **kwargs):
        if path == engine.root / "worker.py":
            raise PermissionError("fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(identity.IdentityUnavailable, match="Cannot read"):
        identity.engine_fingerprint(engine)


def test_enumeration_failure_is_not_a_partial_hash(engine, monkeypatch):
    def fail(root, *, onerror):
        onerror(PermissionError("fixture"))
        return []

    monkeypatch.setattr(identity.os, "walk", fail)
    with pytest.raises(identity.IdentityUnavailable, match="Cannot enumerate"):
        identity.engine_fingerprint(engine)


@pytest.mark.parametrize("selected", ["julia_project", "julia_sysimage"])
def test_missing_selected_input_fails(engine, tmp_path, selected):
    with pytest.raises(identity.IdentityUnavailable):
        identity.engine_fingerprint(engine, **{selected: tmp_path / "missing"})


def test_missing_project_does_not_enumerate_its_parent(engine, tmp_path, monkeypatch):
    original = identity._files

    def guarded(root):
        assert root == engine.root
        return original(root)

    monkeypatch.setattr(identity, "_files", guarded)
    with pytest.raises(identity.IdentityUnavailable, match="Missing selected project"):
        identity.engine_fingerprint(engine, julia_project=tmp_path / "missing")


def test_wg_runtime_and_compiled_policy_identity_is_separate(engine, tmp_path):
    runtime, policy = tmp_path / "runtime", tmp_path / "request-policy.json"
    _write(runtime, ["__init__.py", "threads.py", "nested/policy.py"])
    policy.write_text("policy")
    args = dict(compiled_request_policy=policy)
    before = identity.runtime_fingerprint(runtime, **args)
    engine_before = identity.engine_fingerprint(engine)
    copied = tmp_path / "runtime-copy"
    shutil.copytree(runtime, copied)
    assert identity.runtime_fingerprint(copied, **args) == before
    (runtime / "nested/policy.py").write_text("new runtime")
    assert identity.runtime_fingerprint(runtime, **args) != before
    assert identity.engine_fingerprint(engine) == engine_before
    before = identity.runtime_fingerprint(runtime, **args)
    policy.write_text("new policy")
    assert identity.runtime_fingerprint(runtime, **args) != before
    policy.unlink()
    with pytest.raises(identity.IdentityUnavailable):
        identity.runtime_fingerprint(runtime, **args)

"""Independent observations use fake process outputs and isolated package assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.beat_conformance import verification


@pytest.fixture
def observer(tmp_path, monkeypatch):
    binary = tmp_path / "julia"
    binary.write_bytes(b"fake executable bytes")
    package = tmp_path / "beat_engine"
    package.mkdir()
    assets = SimpleNamespace(root=package, project=package / "julia_metal", system_solver=package / "solver.jl")
    class Distribution:
        def locate_file(self, name):
            return tmp_path / name
        def read_text(self, name):
            return json.dumps({"vcs_info": {"commit_id": "a" * 40}, "dir_info": {"editable": False}})
    monkeypatch.setattr(verification.importlib, "import_module", lambda name: SimpleNamespace(__file__=package / "__init__.py"))
    monkeypatch.setattr(verification.metadata, "distribution", lambda name: Distribution())
    monkeypatch.setattr(verification, "engine_assets", lambda backend: assets)
    monkeypatch.setattr(verification, "engine_fingerprint", lambda assets: "independent-content-hash")
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("JULIA_DEPOT_PATH", str(tmp_path / "depot"))
    state = {"commands": [], "environments": [], "assets": assets,
             "version": "julia version 1.12.7", "kernel": "WG_DEVICE=Test GPU\nWG_KERNEL_VERIFIED=true"}
    def run(command, **kwargs):
        state["commands"].append(command)
        state["environments"].append(kwargs.get("environment"))
        if "--version" in command:
            return state["version"]
        if "-e" in command:
            return "Observed test CPU"
        return state["kernel"]
    monkeypatch.setattr(verification, "_run", run)
    return binary, state


def test_independent_julia_executable_identity_is_run_and_hashed(observer):
    binary, state = observer
    facts = verification.verify_runtime(str(binary), "cpu")
    assert state["commands"][0] == [str(binary), "--startup-file=no", "--version"]
    assert facts["julia_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert facts["engine_revision"] == "a" * 40
    assert facts["engine_revision_status"] == "attested"
    assert facts["device_name"] == "Observed test CPU"
    state["version"] = "not Julia"
    with pytest.raises(ValueError, match="identify"):
        verification.verify_runtime(str(binary), "cpu")


def test_independent_metal_probe_requires_dispatched_kernel_not_functionality(observer):
    binary, state = observer
    facts = verification.verify_runtime(str(binary), "metal")
    assert facts["device_kernel_verified"] and facts["device_name"] == "Test GPU"
    command = state["commands"][-1]
    assert command[-1].endswith("assert_metal_device.jl")
    assert any(arg.startswith("--project=") for arg in command)
    # Failing control: a runner could formerly claim verified=True after
    # Metal.functional alone; the recorder now requires kernel proof itself.
    state["kernel"] = "Metal.functional() = true\nWG_DEVICE=Test GPU"
    with pytest.raises(ValueError, match="verified dispatched"):
        verification.verify_runtime(str(binary), "metal")


def test_probe_subprocess_failures_are_not_runner_attestations(observer, monkeypatch):
    binary, _ = observer
    def fail(command, **kwargs):
        raise OSError("binary cannot execute")
    monkeypatch.setattr(verification, "_run", fail)
    with pytest.raises(OSError, match="cannot execute"):
        verification.verify_runtime(str(binary), "cpu")


def test_real_process_probe_is_bounded_and_checked(monkeypatch):
    observed = {}
    def run(command, **kwargs):
        observed.update(kwargs)
        assert command == ["test-executable", "--version"]
        return SimpleNamespace(stdout="julia version test\n")
    monkeypatch.setattr(verification.subprocess, "run", run)
    assert verification._run(["test-executable", "--version"]) == "julia version test"
    assert observed == {"capture_output": True, "text": True, "check": True, "timeout": 60}


def test_loaded_package_cannot_borrow_unrelated_distribution_revision(observer, monkeypatch):
    binary, _ = observer
    class WrongDistribution:
        def read_text(self, name):
            return json.dumps({"vcs_info": {"commit_id": "a" * 40}})
        def locate_file(self, name):
            return binary.parent / "unrelated" / name
    monkeypatch.setattr(verification.metadata, "distribution", lambda name: WrongDistribution())
    with pytest.raises(ValueError, match="recorded installed distribution"):
        verification.verify_runtime(str(binary), "cpu")


@pytest.mark.parametrize("dirty", [False, True])
def test_source_revision_requires_tracked_clean_engine_tree(observer, monkeypatch, dirty):
    binary, state = observer
    class SourceDistribution:
        def read_text(self, name):
            return json.dumps({"dir_info": {"editable": True}})
    monkeypatch.setattr(verification.metadata, "distribution", lambda name: SourceDistribution())
    original = verification._run
    def run(command, **kwargs):
        if command[0] == "git":
            state["commands"].append(command)
            if "rev-parse" in command:
                return "b" * 40
            if "status" in command:
                return " M src/beat_engine/client.py" if dirty else ""
            return "src/beat_engine/__init__.py"
        return original(command, **kwargs)
    monkeypatch.setattr(verification, "_run", run)
    if dirty:
        with pytest.raises(ValueError, match="uncommitted"):
            verification.verify_runtime(str(binary), "cpu")
    else:
        facts = verification.verify_runtime(str(binary), "cpu")
        assert facts["engine_revision"] == "b" * 40 and facts["engine_revision_status"] == "observed"
        assert facts["artifact_kind"] == "source"
    assert any("ls-files" in command and "--error-unmatch" in command for command in state["commands"])


@pytest.mark.parametrize("defect", [None, "bytes", "extra", "missing", "dirty"])
def test_wheel_without_vcs_metadata_requires_complete_clean_source_byte_match(tmp_path, monkeypatch, defect):
    source = tmp_path / "candidate"
    package = tmp_path / "installed"
    package.mkdir()
    root = source / "src/beat_engine"
    root.mkdir(parents=True)
    for name in ("__init__.py", "julia_local/Manifest-v1.12.toml", "beat_contract/system-v1.schema.json"):
        for directory in (root, package):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"frozen package bytes")
    names = ["src/beat_engine/" + p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    if defect == "bytes":
        (package / "__init__.py").write_bytes(b"other package bytes")
    if defect == "extra":
        (package / "unexpected.jl").write_bytes(b"untracked")
    if defect == "missing":
        (package / "__init__.py").unlink()
    def run(command):
        if "status" in command:
            return " M src/beat_engine/__init__.py" if defect == "dirty" else ""
        if "ls-tree" in command:
            assert "c" * 40 in command
            return "\n".join(names)
        return "c" * 40
    monkeypatch.setattr(verification, "_run", run)
    monkeypatch.setattr(verification, "_blob", lambda source, revision, name: b"frozen package bytes")
    if defect:
        with pytest.raises(ValueError, match="differ|uncommitted"):
            verification._installed_source_revision(package, source)
    else:
        assert verification._installed_source_revision(package, source) == "c" * 40
        (package / "__pycache__").mkdir()
        (package / "__pycache__/ignored.pyc").write_bytes(b"cache")
        assert verification._installed_source_revision(package, source) == "c" * 40


def test_revision_verification_race_uses_captured_immutable_blobs(tmp_path, monkeypatch):
    package = tmp_path / "installed"
    source = tmp_path / "source"
    source.mkdir()
    package.mkdir()
    (package / "__init__.py").write_bytes(b"old bytes")
    heads = iter(["a" * 40, "b" * 40])
    calls = []
    def run(command):
        calls.append(command)
        if "rev-parse" in command:
            return next(heads)
        if "ls-tree" in command:
            assert "a" * 40 in command
            return "src/beat_engine/__init__.py"
        return ""
    def blob(root, revision, name):
        assert root == source and revision == "a" * 40
        assert name == "src/beat_engine/__init__.py"
        return b"old bytes"
    monkeypatch.setattr(verification, "_run", run)
    monkeypatch.setattr(verification, "_blob", blob)
    with pytest.raises(ValueError, match="HEAD changed"):
        verification._installed_source_revision(package, source)
    assert "rev-parse" in calls[0]


def test_revision_verification_blob_read_is_binary_and_bounded(tmp_path, monkeypatch):
    def run(command, **kwargs):
        assert command == ["git", "-C", str(tmp_path), "cat-file", "blob", "a" * 40 + ":src/beat_engine/__init__.py"]
        assert kwargs == {"capture_output": True, "check": True, "timeout": 60}
        return SimpleNamespace(stdout=b"exact bytes\x00\n")
    monkeypatch.setattr(verification.subprocess, "run", run)
    assert verification._blob(tmp_path, "a" * 40, "src/beat_engine/__init__.py") == b"exact bytes\x00\n"


@pytest.mark.parametrize("destination", ["depot", "relative_depot", "symlink_depot", "project"])
def test_metal_probe_destination_isolation_precedes_any_julia_launch(observer, monkeypatch, tmp_path, destination):
    binary, state = observer
    legacy = tmp_path / "hbb-runtime"
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    if destination == "project":
        state["assets"].project = legacy / "project"
    else:
        depot = legacy / "depot"
        if destination == "relative_depot":
            monkeypatch.chdir(tmp_path)
            depot = Path("hbb-runtime/depot")
        elif destination == "symlink_depot":
            alias = tmp_path / "alias"
            alias.symlink_to(legacy, target_is_directory=True)
            depot = alias / "depot"
        monkeypatch.setenv("JULIA_DEPOT_PATH", str(depot))
    with pytest.raises(ValueError, match="overlaps"):
        verification.verify_runtime(str(binary), "metal")
    assert not state["commands"] and not legacy.exists()


def test_metal_probe_receives_validated_environment_and_explicit_project(observer, monkeypatch, tmp_path):
    binary, state = observer
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JULIA_DEPOT_PATH", "isolated-depot")
    monkeypatch.setenv("JULIA_PROJECT", str(tmp_path / "hbb-project"))
    monkeypatch.setenv("JULIA_PKG_OFFLINE", "true")
    verification.verify_runtime(str(binary), "metal")
    environment = state["environments"][-1]
    assert environment["JULIA_DEPOT_PATH"] == str(tmp_path / "isolated-depot")
    assert environment["JULIA_PROJECT"] == str(state["assets"].project)
    assert environment["JULIA_PKG_OFFLINE"] == "true"
    assert f"--project={environment['JULIA_PROJECT']}" in state["commands"][-1]
    assert not (tmp_path / "isolated-depot").exists()


def test_probe_subprocess_passes_validated_environment(monkeypatch):
    environment = {"JULIA_DEPOT_PATH": "/fake/isolated-depot"}
    def run(command, **kwargs):
        assert kwargs["env"] == environment
        return SimpleNamespace(stdout="verified\n")
    monkeypatch.setattr(verification.subprocess, "run", run)
    assert verification._run(["fake-julia"], environment=environment) == "verified"

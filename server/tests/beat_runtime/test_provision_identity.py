from __future__ import annotations

import shutil

import pytest

from server.solver.beat_runtime import assets, discovery, provision, state


@pytest.mark.parametrize("change", ["project", "project_path", "executable", "threads", "fixture", "environment", "proof"])
def test_changed_identity_or_incomplete_proof_reprovisions(cpu_provisioning, change):
    root, engine, julia, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    if change == "project":
        (engine.project / "Project.toml").write_text("edited")
    elif change == "project_path":
        options["julia_project"] = root.parent / "moved-project"
        shutil.copytree(engine.project, options["julia_project"])
    elif change == "executable":
        julia.write_bytes(b"new Julia")
    elif change == "threads":
        options["julia_threads"] = 4
    elif change == "fixture":
        options["probe_fixture_identity"] = "changed"
    elif change == "environment":
        options["environ"]["BLAB_OPTIONS"] = "changed"
    else:
        state.write_state(dict(ready, completion={}), root)
    calls.clear()
    assert provision.provision_cpu(**options)["status"] == "ready"
    assert len(calls) == 3


def test_missing_optional_engine_records_failure(cpu_provisioning, monkeypatch):
    _, _, _, calls, options = cpu_provisioning

    def missing(backend):
        raise assets.AssetsUnavailable("Optional beat-engine package is not importable")

    monkeypatch.setattr(assets, "engine_assets", missing)
    result = provision.provision_cpu(**options)
    assert result["status"] == "failed" and result["step"] == "resolve_assets"
    assert calls == []


def test_instantiation_manifest_is_in_the_saved_identity(cpu_provisioning):
    _, engine, _, calls, options = cpu_provisioning
    original_step = options["run_step"]

    def instantiate(executable, code, **kwargs):
        original_step(executable, code, **kwargs)
        if "instantiate" in code:
            (engine.project / "Manifest-v1.12.toml").write_text("new manifest")

    options["run_step"] = instantiate
    ready = provision.provision_cpu(**options)
    calls.clear()
    assert provision.provision_cpu(**options) == ready
    assert calls == []


@pytest.mark.parametrize("field", ["probe_contract", "probe_fixture_identity"])
def test_probe_identity_is_required(cpu_provisioning, field):
    _, _, _, _, options = cpu_provisioning
    options[field] = None
    result = provision.provision_cpu(**options)
    assert result["status"] == "failed" and result["step"] == "cpu_probe"
    assert "identity are required" in result["error"]


def test_ready_old_managed_julia_still_goes_through_installer(cpu_provisioning):
    root, _, julia, calls, options = cpu_provisioning
    old = root / "julia/1.12.6-linux-x86_64/bin/julia"
    old.parent.mkdir(parents=True)
    old.write_bytes(julia.read_bytes())
    old.chmod(0o755)
    options["julia_executable"] = str(old)
    provision.provision_cpu(**options)
    options["julia_executable"] = None
    calls.clear()
    upgraded = []

    def upgrade(directory, **kwargs):
        upgraded.append(directory)
        discovery.write_julia_record(directory, julia, origin="managed", version="1.12.7")
        return str(julia)

    ready = provision.provision_cpu(**dict(options, ensure_julia=upgrade))
    assert ready["status"] == "ready" and ready["julia_executable"] == str(julia)
    assert upgraded == [root] and len(calls) == 3

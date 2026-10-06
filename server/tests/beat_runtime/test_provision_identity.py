from __future__ import annotations

import shutil
import json
import os

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


@pytest.mark.parametrize("alias", [False, True])
def test_provision_explicit_root_hbb_isolation_before_diagnostics(cpu_provisioning, tmp_path, monkeypatch, alias):
    from server.solver.beat_runtime import paths

    _, _, _, calls, options = cpu_provisioning
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    options["environ"]["HORNLAB_BEAT_RUNTIME_DIR"] = str(legacy)
    root = tmp_path / "alias" if alias else legacy
    if alias:
        root.symlink_to(legacy, target_is_directory=True)
    with pytest.raises(paths.RootConflict):
        provision.provision_cpu(root, **options)
    assert not list(legacy.iterdir()) and not calls


def test_provision_preserves_juliaup_launcher_for_subprocess(cpu_provisioning):
    root, _, actual, calls, options = cpu_provisioning
    launcher = actual.with_name("juliaup")
    launcher.symlink_to(actual)
    options["julia_executable"] = str(launcher)
    result = provision.provision_cpu(**options)
    assert result["status"] == "ready" and result["julia_executable"] == str(launcher)
    assert state.read_julia(root)["executable"] == str(launcher)
    assert calls[-1][1]["julia_executable"] == str(launcher)


def test_short_windows_version_directory_does_not_trigger_upgrade(cpu_provisioning):
    from server.solver.beat_runtime import installer

    root, _, actual, calls, options = cpu_provisioning
    tree = root / "julia" / installer.JULIA_VERSION
    current = tree / "bin" / ("julia.exe" if os.name == "nt" else "julia")
    current.parent.mkdir(parents=True)
    current.write_bytes(actual.read_bytes())
    current.chmod(0o755)
    # Real managed installs carry the ownership marker; Windows recovery checks
    # the short version directory before reusing its executable.
    spec = installer.julia_download("Windows", "x86_64")
    (tree / ".wg-julia.json").write_text(json.dumps({
        "provider": installer.PROVIDER_ID, "version": installer.JULIA_VERSION, "platform": spec.platform,
    }))
    discovery.write_julia_record(root, current, origin="managed", version=installer.JULIA_VERSION)
    options["julia_executable"] = None
    ready = provision.provision_cpu(**options)
    assert ready["status"] == "ready"
    calls.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("short current Windows directory mistaken for an outdated install")

    options["ensure_julia"] = forbidden
    assert provision.provision_cpu(**options) == ready
    assert calls == []


def test_hbb_record_and_path_cannot_shortcut_cpu_readiness(cpu_provisioning, tmp_path, monkeypatch):
    from server.solver.beat_runtime import paths

    root, _, actual, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    hbb_julia = legacy / "julia"
    hbb_julia.write_bytes(actual.read_bytes())
    hbb_julia.chmod(0o755)
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    options["environ"]["HORNLAB_BEAT_RUNTIME_DIR"] = str(legacy)
    options["julia_executable"] = None
    discovery.write_julia_record(root, hbb_julia, origin="external", version=None)
    state.write_state(dict(ready, julia_executable=str(hbb_julia),
                           julia_identity=discovery.executable_identity(hbb_julia)), root)
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: str(hbb_julia))
    copied = root / "julia" / "owned" / "bin/julia"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(actual.read_bytes())
    copied.chmod(0o755)
    installs = []

    def install(directory, **kwargs):
        installs.append(directory)
        discovery.write_julia_record(directory, copied, origin="managed", version="1.12.7")
        return str(copied)

    calls.clear()
    options["ensure_julia"] = install
    result = provision.provision_cpu(**options)
    assert result["status"] == "ready" and result["julia_executable"] == str(copied)
    assert installs == [root] and len(calls) == 3
    assert hbb_julia.read_bytes() == actual.read_bytes()
    assert list(legacy.iterdir()) == [hbb_julia]
    assert paths.hbb_executable(hbb_julia)

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.solver.beat_runtime import locks, provision, state


@pytest.mark.parametrize("destination", ["depot", "inherited_first", "inherited_later", "project", "depot_symlink"])
def test_julia_write_destinations_refuse_hbb_before_subprocess(cpu_provisioning, tmp_path, monkeypatch, destination):
    root, _, _, calls, options = cpu_provisioning
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    options["environ"]["HORNLAB_BEAT_RUNTIME_DIR"] = str(legacy)
    if destination == "depot":
        options["depot"] = legacy / "depot"
    elif destination.startswith("inherited"):
        entries = [str(legacy / "depot"), str(tmp_path / "safe-depot")]
        if destination == "inherited_later":
            entries.reverse()
        options["environ"]["JULIA_DEPOT_PATH"] = os.pathsep.join(entries)
    elif destination == "project":
        options["julia_project"] = legacy / "julia_metal"
    else:
        root.mkdir(parents=True)
        (root / "depot").symlink_to(legacy, target_is_directory=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("installer reached an unsafe Julia destination")

    options["ensure_julia"] = forbidden
    failed = provision.provision_cpu(**options)
    assert failed["status"] == "failed" and "overlaps" in failed["error"]
    assert not calls and list(legacy.iterdir()) == []


def test_default_wg_depot_passes_destination_checks(cpu_provisioning):
    root, _, _, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    assert ready["status"] == "ready" and ready["depot"] == str((root / "depot").resolve())
    assert all(call["environment"]["JULIA_DEPOT_PATH"] == ready["depot"] for _, call in calls)


@pytest.mark.parametrize("source", ["argument", "environment"])
@pytest.mark.parametrize("same_absolute_depot", [False, True])
def test_relative_depot_cwd_identity_prevents_false_readiness_reuse(
    cpu_provisioning, tmp_path, monkeypatch, source, same_absolute_depot,
):
    _, _, _, calls, options = cpu_provisioning
    cwd_a, cwd_b = tmp_path / "a", tmp_path / "b"
    cwd_a.mkdir()
    cwd_b.mkdir()
    monkeypatch.chdir(cwd_a)
    depot = str(cwd_a / "depot") if same_absolute_depot else "depot"
    if source == "argument":
        options["depot"] = Path(depot)
    else:
        options["environ"]["JULIA_DEPOT_PATH"] = depot
    ready_a = provision.provision_cpu(**options)
    assert ready_a["status"] == "ready" and ready_a["depot"] == str(cwd_a / "depot")
    calls.clear()
    monkeypatch.chdir(cwd_b)
    ready_b = provision.provision_cpu(**options)
    assert ready_b["status"] == "ready"
    assert ready_b["depot"] == str((cwd_a if same_absolute_depot else cwd_b) / "depot")
    assert len(calls) == (0 if same_absolute_depot else 3)
    assert all(call["environment"]["JULIA_DEPOT_PATH"] == ready_b["depot"] for _, call in calls)


def test_cpu_ready_fast_path_does_not_wait_for_metal_lock(cpu_provisioning, monkeypatch):
    root, _, _, calls, options = cpu_provisioning
    ready = provision.provision_cpu(**options)
    calls.clear()
    with locks.provisioning_lock(root, backend="metal"):
        def forbidden(*args, **kwargs):
            raise AssertionError("CPU readiness tried to wait for Metal provisioning")

        monkeypatch.setattr(locks, "provisioning_lock", forbidden)
        assert provision.provision_cpu(**options) == ready
    assert not calls


def test_refused_linked_root_never_receives_failure_diagnostics(cpu_provisioning, tmp_path, monkeypatch):
    _, _, _, calls, options = cpu_provisioning
    target = tmp_path / "other-wg"
    target.mkdir()
    root = tmp_path / "linked-root"
    root.symlink_to(target, target_is_directory=True)
    writes = []
    monkeypatch.setattr(state, "write_state", lambda *args: writes.append(args))
    with pytest.raises(RuntimeError, match="Linked runtime directory refused"):
        provision.provision_cpu(root, **options)
    assert not writes and not calls and list(target.iterdir()) == []


def test_lock_refused_after_root_becomes_linked_skips_diagnostics(cpu_provisioning, tmp_path, monkeypatch):
    root, _, _, calls, options = cpu_provisioning
    target = tmp_path / "other-wg"
    target.mkdir()
    root.parent.mkdir(parents=True)

    def refused(*args, **kwargs):
        root.symlink_to(target, target_is_directory=True)
        raise RuntimeError("Linked runtime directory refused")

    monkeypatch.setattr(locks, "provisioning_lock", refused)
    failed = provision.provision_cpu(**options)
    assert failed["status"] == "failed" and "Linked" in failed["error"]
    assert not calls and list(target.iterdir()) == []

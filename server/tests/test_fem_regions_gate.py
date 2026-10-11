"""Declared FEM regions stay unreachable until an explicit development opt-in."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server.cadlink.build_flags import FEM_REGIONS_ENV, fem_regions_enabled
from server.cadlink.fusion.fusion_delivery import (
    CAPABILITIES_FILENAME,
    advertise_fusion_delivery,
)
from server.cadlink.intake.isolation import ChildRefusal, child_environment
from server.cadlink.ipc import ipc_folder
from server.cadlink.wgreturn import WgReturnError, read_wgreturn
from server.tests.test_cadlink_wgreturn import _manifest, write_bundle


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "2", "1", " 1 "])
def test_only_one_enables_fem_regions(value: str | None) -> None:
    env = {} if value is None else {FEM_REGIONS_ENV: value}
    assert fem_regions_enabled(environ=env) is (value in {"1", " 1 "})


def test_capabilities_file_removes_fem_after_disabling_the_flag(tmp_path, monkeypatch):
    monkeypatch.setenv(FEM_REGIONS_ENV, "1")
    advertise_fusion_delivery(tmp_path)
    path = ipc_folder(tmp_path) / CAPABILITIES_FILENAME
    assert json.loads(path.read_text())["femRegions"] == 1

    monkeypatch.delenv(FEM_REGIONS_ENV)
    advertise_fusion_delivery(tmp_path)
    enabled = json.loads(path.read_text())
    assert "femRegions" not in enabled
    assert enabled["solveCommandDelivery"] == 4


def _fem_bundle(root: Path) -> Path:
    fem = b"FEM STEP"
    manifest = _manifest(b"STEP")
    manifest["required_features"].append("fem-regions-v1")
    manifest["scope"]["fem_regions"] = [{
        "region_id": "rear-chamber", "name": "Rear chamber", "object_id": "air-body",
        "file": "fem-regions/rear-chamber.step", "n_bodies_expected": 1,
        "mesh_size_mm": 8.0,
        "medium": {"sound_speed_m_per_s": 343.0, "density_kg_per_m3": 1.21, "bulk_loss_factor": 0.0},
        "surface_groups": [],
    }]
    manifest["files"]["fem-regions/rear-chamber.step"] = {
        "sha256": "sha256:" + hashlib.sha256(fem).hexdigest(), "size_bytes": len(fem),
        "media_type": "model/step", "purpose": "fem-region",
    }
    bundle = write_bundle(root, manifest)
    (bundle / "fem-regions").mkdir()
    (bundle / "fem-regions" / "rear-chamber.step").write_bytes(fem)
    return bundle


def test_bundle_is_refused_off_and_accepted_only_with_the_flag(tmp_path, monkeypatch):
    bundle = _fem_bundle(tmp_path)
    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    with pytest.raises(WgReturnError, match="This return declares FEM regions; this Waveguide Generator has FEM regions turned off\\."):
        read_wgreturn(bundle)

    monkeypatch.setenv(FEM_REGIONS_ENV, "1")
    accepted = read_wgreturn(bundle)
    assert accepted.manifest["scope"]["fem_regions"][0]["region_id"] == "rear-chamber"
    assert "fem-regions/rear-chamber.step" in accepted.members


@pytest.mark.parametrize("with_feature", [True, False], ids=["declared-feature", "scope-only"])
def test_ingest_refuses_fem_before_inspection_or_meshing(tmp_path, monkeypatch, with_feature):
    from server.cadlink.intake import ingest, isolated

    bundle = _fem_bundle(tmp_path)
    monkeypatch.setenv(FEM_REGIONS_ENV, "1")
    read_wgreturn(bundle)  # Accepted at Send; the restarted receiver disables FEM.
    if not with_feature:
        path = bundle / "wgreturn.json"
        manifest = json.loads(path.read_text())
        manifest["required_features"].remove("fem-regions-v1")
        path.write_text(json.dumps(manifest))
    monkeypatch.delenv(FEM_REGIONS_ENV)

    def unexpected(*_args, **_kwargs):
        pytest.fail("flag-off FEM intake must not inspect or mesh geometry")

    monkeypatch.setattr(isolated, "inspect_returned_step", unexpected)
    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal, match="This return declares FEM regions; this Waveguide Generator has FEM regions turned off\\.") as caught:
        ingest.ingest_bundle(bundle, {}, [], None, tmp_path)
    assert caught.value.stage == "stage 1 bundle validation"


def test_isolated_fem_does_not_start_a_child_without_the_flag(tmp_path, monkeypatch):
    from server.cadlink.intake import isolated

    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("flag-off FEM meshing must not start a child")

    monkeypatch.setattr(isolated, "isolated_step_task", unexpected)
    with pytest.raises(ChildRefusal, match="declared FEM regions are disabled"):
        isolated.build_fem_region_mesh_isolated(tmp_path / "absent.step", {})


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_child_receives_only_the_explicit_fem_opt_in(tmp_path, system):
    assert FEM_REGIONS_ENV not in child_environment(tmp_path, environ={}, system=system)
    env = child_environment(tmp_path, environ={FEM_REGIONS_ENV: "1"}, system=system)
    assert env[FEM_REGIONS_ENV] == "1"


def test_flag_off_child_and_default_imports_never_load_fem_dependencies(tmp_path, monkeypatch):
    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    result_path = tmp_path / "result.json"
    # A clean interpreter catches imports even when another test already used FEM.
    script = """
import builtins, io, json, sys
original_import = builtins.__import__
blocked = ('gmsh', 'meshio', 'beat_engine', 'server.mesh.fem_regions', 'server.solver.coupled_system')
def guarded_import(name, *args, **kwargs):
    assert not any(name == module or name.startswith(module + '.') for module in blocked), name
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.wgreturn import validate_manifest
from server.cadlink.intake import isolated, child_main
assert 'femRegions' not in capabilities()
envelope = {'protocol': 1, 'task': 'fem_region', 'result_path': sys.argv[1]}
sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(envelope).encode()))
assert child_main.main([]) == 1
result = json.load(open(sys.argv[1]))
assert result['error']['message'] == "unknown isolated CAD task 'fem_region'", result
assert not any(module in sys.modules for module in blocked)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(result_path)],
        cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

"""Declared FEM regions are enabled by default with an operator kill switch."""

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


@pytest.mark.parametrize("value,enabled", [
    (None, True), ("", True), ("1", True), (" 1 ", True), ("true", True),
    ("yes", True), ("2", True), ("0", False), ("false", False),
    ("off", False), ("no", False), (" FALSE ", False), (" OFF ", False),
])
def test_fem_regions_default_on_with_explicit_kill_switch(value, enabled):
    env = {} if value is None else {FEM_REGIONS_ENV: value}
    assert fem_regions_enabled(environ=env) is enabled


def test_capabilities_file_removes_fem_after_disabling_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    advertise_fusion_delivery(tmp_path)
    path = ipc_folder(tmp_path) / CAPABILITIES_FILENAME
    assert json.loads(path.read_text())["femRegions"] == 1

    monkeypatch.setenv(FEM_REGIONS_ENV, "0")
    advertise_fusion_delivery(tmp_path)
    enabled = json.loads(path.read_text())
    assert "femRegions" not in enabled
    assert enabled["solveCommandDelivery"] == 4
    monkeypatch.delenv(FEM_REGIONS_ENV)
    advertise_fusion_delivery(tmp_path)
    assert json.loads(path.read_text())["femRegions"] == 1


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


def test_bundle_is_refused_off_and_accepted_by_default(tmp_path, monkeypatch):
    bundle = _fem_bundle(tmp_path)
    monkeypatch.setenv(FEM_REGIONS_ENV, "0")
    with pytest.raises(WgReturnError, match="This return declares FEM regions; this Waveguide Generator has FEM regions turned off\\."):
        read_wgreturn(bundle)

    monkeypatch.delenv(FEM_REGIONS_ENV)
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
    monkeypatch.setenv(FEM_REGIONS_ENV, "0")

    def unexpected(*_args, **_kwargs):
        pytest.fail("flag-off FEM intake must not inspect or mesh geometry")

    monkeypatch.setattr(isolated, "inspect_returned_step", unexpected)
    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal, match="This return declares FEM regions; this Waveguide Generator has FEM regions turned off\\.") as caught:
        ingest.ingest_bundle(bundle, {}, [], None, tmp_path)
    assert caught.value.stage == "stage 1 bundle validation"


def test_isolated_fem_does_not_start_a_child_without_the_flag(tmp_path, monkeypatch):
    from server.cadlink.intake import isolated

    monkeypatch.setenv(FEM_REGIONS_ENV, "0")

    def unexpected(*_args, **_kwargs):
        pytest.fail("flag-off FEM meshing must not start a child")

    monkeypatch.setattr(isolated, "isolated_step_task", unexpected)
    with pytest.raises(ChildRefusal, match="declared FEM regions are disabled"):
        isolated.build_fem_region_mesh_isolated(tmp_path / "absent.step", {})


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Windows"])
def test_child_preserves_default_and_explicit_fem_override(tmp_path, system):
    default = child_environment(tmp_path, environ={}, system=system)
    assert FEM_REGIONS_ENV not in default
    assert fem_regions_enabled(environ=default)
    env = child_environment(tmp_path, environ={FEM_REGIONS_ENV: "1"}, system=system)
    assert env[FEM_REGIONS_ENV] == "1"
    for value in ("0", "false", "off", "no", " OFF "):
        env = child_environment(tmp_path, environ={FEM_REGIONS_ENV: value}, system=system)
        assert not fem_regions_enabled(environ=env)


@pytest.mark.parametrize("task", ["fem_region", "coupled_mesh"])
def test_flag_off_child_and_default_imports_never_load_fem_dependencies(tmp_path, monkeypatch, task):
    monkeypatch.setenv(FEM_REGIONS_ENV, "0")
    result_path = tmp_path / "result.json"
    # A clean interpreter catches imports even when another test already used FEM.
    script = """
import builtins, io, json, sys
original_import = builtins.__import__
blocked = ('gmsh', 'meshio', 'beat_engine', 'server.mesh.fem_regions', 'server.mesh.fem_coupling', 'server.solver.coupled_system')
def guarded_import(name, *args, **kwargs):
    assert not any(name == module or name.startswith(module + '.') for module in blocked), name
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from server.cadlink.fusion.fusion_delivery import capabilities
from server.cadlink.wgreturn import validate_manifest
from server.cadlink.intake import isolated, child_main
assert 'femRegions' not in capabilities()
envelope = {'protocol': 1, 'task': sys.argv[2], 'result_path': sys.argv[1]}
sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(envelope).encode()))
assert child_main.main([]) == 1
result = json.load(open(sys.argv[1]))
assert result['error']['message'] == f"unknown isolated CAD task {sys.argv[2]!r}", result
assert not any(module in sys.modules for module in blocked)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(result_path), task],
        cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_kill_switch_refuses_job_admission_before_engine_probe(monkeypatch, value):
    import asyncio
    from server.jobs.runtime import ImportedSolveRefusal
    from server.jobs.runtime.core import resolve_imported_submission
    from server.tests.test_imported_jobs import _request

    monkeypatch.setenv(FEM_REGIONS_ENV, value)
    class Registry:
        async def capabilities(self):
            pytest.fail("disabled FEM must refuse before engine probing")
    request = _request("wgi_" + "0" * 26)
    with pytest.raises(ImportedSolveRefusal, match="FEM regions are disabled"):
        asyncio.run(resolve_imported_submission(request, Registry(), imported_record={"fem_regions": [{}]}))


def test_frontend_capability_tracks_the_kill_switch(monkeypatch):
    import asyncio
    from server.diagnostics.capabilities import capabilities_payload
    class Registry:
        async def capabilities(self):
            return ()
    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    assert asyncio.run(capabilities_payload(Registry()))["femRegions"] is True
    monkeypatch.setenv(FEM_REGIONS_ENV, "0")
    assert asyncio.run(capabilities_payload(Registry()))["femRegions"] is False


@pytest.mark.parametrize("value", ["unexpected-fem-value", "  TYPO  ", "2"])
def test_unknown_kill_switch_warns_once_and_stays_enabled(caplog, monkeypatch, value):
    from server.platform import feature_flags
    monkeypatch.setattr(feature_flags, "_warned_fem_values", set())
    with caplog.at_level("WARNING", logger=feature_flags.__name__):
        assert fem_regions_enabled(environ={FEM_REGIONS_ENV: value})
        assert fem_regions_enabled(environ={FEM_REGIONS_ENV: value})
    assert len(caplog.records) == 1
    assert FEM_REGIONS_ENV in caplog.text
    assert repr(value) in caplog.text
    assert "FEM stays enabled" in caplog.text


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "1", "true", "yes", "on"])
def test_known_kill_switch_values_do_not_warn(caplog, value):
    with caplog.at_level("WARNING"):
        fem_regions_enabled(environ={} if value is None else {FEM_REGIONS_ENV: value})
    assert not caplog.records

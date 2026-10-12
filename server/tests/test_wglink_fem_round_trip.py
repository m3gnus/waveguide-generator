"""Pinned WGLink FEM authoring -> bundle bytes -> real meshes -> fake CPU job.

Runs in normal CI without Fusion or a native acoustic engine. Only CAD export
is represented by small OCC solids; declaration and serialization are the
packaged add-in's pure functions, and intake and job execution are WG's owners.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import sys

import numpy as np
import pytest

from server.cadlink.intake import ingest
from server.cadlink.intake.readings import confirm_frame
from server.cadlink.store import CadLinkStore
from server.cadlink.wgreturn import read_wgreturn, validate_manifest
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.result_contracts import RESULT_ENVELOPE_ADAPTER
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.mesh.artifact import read_verified_fem_region_mesh
from server.mesh.fem_regions import read_fem_region_domain
from server.solver.beat_adapter.results import acceleration_scale
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.fem_validation import _write_geometry, validation_request
from server.tests.test_cadlink_wgreturn import _manifest
from server.tests.test_fem_coupling import _write_assembly
from server.tests.test_fem_regions import _region, _write_step
from server.tests.test_official_beat_bridge import runtime as runtime_fixture
from server.tests.test_wglink_packaged_pair import _from_package, _verdict, packaged as packaged_fixture

runtime = runtime_fixture
packaged = packaged_fixture


def _addin_bundle(addin, root, *, coupled):
    root.mkdir()
    if coupled:
        assembly = _write_assembly(root / "material.step").read_bytes()
        air = _write_step(root / "air.step").read_bytes()
        bounds = [[-2, -2, -2], [40, 32, 22]]
    else:
        assembly, air = _write_geometry(root / "geometry", "sealed")
        bounds = [[-1, -1, -1], [41, 31, 21]]

    with (_from_package(addin, "wglink_author.py") as author, pytest.MonkeyPatch.context() as imports):
        imports.setitem(sys.modules, "wglink_author", author)
        return _write_addin_bundle(addin, root, assembly, air, bounds, coupled=coupled)


def _write_addin_bundle(addin, root, assembly, air, bounds, *, coupled):
    with (_from_package(addin, "wglink_fem.py") as fem,
          _from_package(addin, "wglink_return.py") as writer):
        faces = {"cone": fem.face_descriptor(
            area_cm2=12., centroid_cm=[2., 1.5, 0.], normal=[0., 0., 1.],
            bbox_min_cm=[0., 0., 0.], bbox_max_cm=[4., 3., 0.])}
        groups = [{"name": "Cone", "kind": "moving", "drive_role": "LF", "mesh_size_mm": 8.,
                   "faces": [{"key": "cone", "body_matches": True, "area_cm2": 12.}]}]
        if coupled:
            faces["port"] = fem.face_descriptor(
                area_cm2=6., centroid_cm=[4., 1.5, 1.], normal=[1., 0., 0.],
                bbox_min_cm=[4., 0., 0.], bbox_max_cm=[4., 3., 2.])
            groups.append({"name": "Port", "kind": "interface", "mesh_size_mm": 8.,
                           "faces": [{"key": "port", "body_matches": True, "area_cm2": 6.}]})
        declaration = fem.plan_region_declaration(
            name="Rear chamber", mesh_size_mm=10., body_is_solid=True, groups=groups)
        stored = fem.read_region_attribute(declaration.body_attribute())
        assert stored is not None
        regions = fem.plan_fem_regions([fem.RegionExport(
            object_id="air-body", declaration=stored,
            faces_by_group={key: [face] for key, face in faces.items()},
            face_ids_by_group={group.group_id: group.face_ids for group in declaration.groups})])
        assert regions[0]["surface_groups"][0]["drive_channel_id"] == "drive-lf"
        assert regions[0]["surface_groups"][0]["faces"][0]["area_mm2"] == 1200.
        assert regions[0]["n_bodies_expected"] == 1

        fields = _manifest(assembly)
        fields["return_record"] = fields.pop("return")
        fields.pop("acoustics")
        fields["instances"] = []
        fields["sources"] = []
        fields["coordinate_system"]["solver_anchor_instance_id"] = None
        fields["assembly"]["bbox_mm"] = bounds
        fields["scope"]["included"] = [{
            "object_id": "walls", "name": "Rigid walls", "component": "Cabinet",
            "body_kind": "solid", "visible": True, "external_reference": "none"}]
        fields["scope"]["fem_regions"] = regions
        fields["required_features"].append(fem.FEM_REGIONS_FEATURE)
        member = regions[0]["file"]
        fields["files"][member] = {
            "sha256": "sha256:" + hashlib.sha256(air).hexdigest(), "size_bytes": len(air),
            "media_type": "model/step", "purpose": "fem-region"}
        manifest = writer.build_return_manifest(**fields)
        raw = writer.dumps_return_manifest(manifest).encode("utf-8")
        assert writer.loads_return_manifest(raw) == manifest

    bundle = root / "golden.wgreturn"
    bundle.mkdir()
    members = {"wgreturn.json": raw, "assembly.step": assembly, member: air}
    for name, content in members.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return bundle, manifest, members


@pytest.mark.parametrize("coupled", [False, True], ids=["interior", "coupled"])
def test_pinned_fem_writer_reader_mesh_and_fake_job(packaged, runtime, tmp_path, monkeypatch, coupled):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    bundle, manifest, members = _addin_bundle(packaged, tmp_path / "returns", coupled=coupled)
    read = read_wgreturn(bundle)
    assert read.manifest == manifest
    manifest_sha = "sha256:" + hashlib.sha256(members["wgreturn.json"]).hexdigest()
    for name, file_record in manifest["files"].items():
        assert read.members[name].read_bytes() == members[name]
        assert file_record["sha256"] == "sha256:" + hashlib.sha256(members[name]).hexdigest()
        assert file_record["size_bytes"] == len(members[name])

    cad_store = CadLinkStore(tmp_path / "cadlink.db")
    registry = EngineRegistry(detector=lambda: [EngineInfo(
        "beat-cpu", True, "fake worker", "test", geometry_sources=("imported",),
        imported_features=("fem-regions",), symmetry_domains=("full",))])

    def fake_result(raw):
        raw.clear()
        raw.update(coupled_result(runtime.requests[-1], 100.))

    runtime.mutate = fake_result

    async def solve():
        jobs = JobRuntime(JobStore(tmp_path / "jobs.db"), cadlink_store=cad_store,
                          engine_registry=registry)
        try:
            record = ingest.ingest_bundle(bundle, {
                "rigid_size_mm": 12., "transition_mm": 12., "source_size_mm": {},
            }, [], cad_store, tmp_path / "data", defer_viewport=True)
            assert record["manifest_sha256"] == manifest_sha
            region, = record["fem_regions"]
            for key, value in manifest["scope"]["fem_regions"][0].items():
                assert region[key] == value
            assert region["stats"]["tetrahedra"] > 0
            domain = read_fem_region_domain(
                read_verified_fem_region_mesh(record, region["region_id"]), region["region_id"])
            assert len(domain.tetrahedra) == region["stats"]["tetrahedra"]
            if coupled:
                assert record["coupled_exterior"]["stats"]["open_edges"] == 0
            retained = json.loads(cad_store.get_ingest(record["ingest_id"])["record_json"])
            assert retained["fem_regions"] == record["fem_regions"]
            confirm_frame(cad_store, record, "+z")
            job_id = await jobs.submit(validation_request(record, manifest, [100.]))
            await jobs.wait_idle(timeout=30.)
            row = jobs.store.get_job_row(job_id)
            assert row["status"] == "complete", row["error_message"]
            assert row["task_metadata"]["has_fem_field_artifact"]
            envelope = jobs.store.get_results(job_id)
            RESULT_ENVELOPE_ADAPTER.validate_python(envelope)
            assert envelope["channel_order"] == ["drive-lf"]
            assert envelope["frequencies"] == [100.]
            assert envelope["metadata"]["ingest_id"] == record["ingest_id"]
            assert envelope["metadata"]["solve_path"] == (
                "coupled-fem-bem" if coupled else "interior-fem")
            assert envelope["metadata"]["fem"] == {
                "engine": "beat-cpu", "precision": "float64"}
            with np.load(BytesIO(jobs.store.get_fem_field(job_id)), allow_pickle=False) as field:
                assert field["schema"].item() == "wg-fem-field-store-v1"
                assert field["channel_ids"].tolist() == ["drive-lf"]
                assert field["region_ids"].tolist() == ["rear-chamber"]
                assert field["pressure"].shape == (1, 1, len(domain.points_mm))
                np.testing.assert_allclose(field["points_mm_0"], domain.points_mm)
                np.testing.assert_allclose(field["pressure"], (2 + 3j) * acceleration_scale(100.))
        finally:
            await jobs.shutdown()
            await registry.shutdown_prewarm()

    try:
        asyncio.run(solve())
        assert len(runtime.requests) == 1
        wire = runtime.requests[0]
        assert wire["solver_options"]["precision"] == "float64"
        assert wire["solver_options"]["bem_backend"] == "cpu"
        assert bool(wire["compiled_system"]["interfaces"]) is coupled
        for name, content in members.items():
            assert (bundle / name).read_bytes() == content
    finally:
        cad_store.close()


@pytest.mark.parametrize("mutation,expected", [
    ("region-key", "unknown member(s): extra"),
    ("medium-key", "unknown member(s): extra"),
    ("group-key", "unknown member(s): extra"),
    ("face-key", "unknown member(s): extra"),
    ("bool-count", "must be exactly 1"),
    ("float-count", "must be exactly 1"),
    ("uppercase-drive", "must use the exterior source drive-id pattern"),
    ("double-hyphen-drive", "must use the exterior source drive-id pattern"),
    ("unprefixed-drive", "must use the exterior source drive-id pattern"),
])
def test_pinned_fem_strict_refusals_agree_at_both_endpoints(packaged, monkeypatch, mutation, expected):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    manifest = _manifest(b"STEP")
    for body in manifest["scope"]["included"]:
        body.update(component="Cabinet", external_reference="none")
    region = deepcopy(_region())
    manifest["required_features"].append("fem-regions-v1")
    manifest["scope"]["fem_regions"] = [region]
    manifest["files"][region["file"]] = {
        "sha256": "sha256:" + hashlib.sha256(b"FEM STEP").hexdigest(), "size_bytes": 8,
        "media_type": "model/step", "purpose": "fem-region"}
    group = region["surface_groups"][0]
    with _from_package(packaged, "wglink_return.py") as writer:
        # A valid baseline prevents an unrelated admission failure from hiding
        # any of the three hardening changes made by this protocol revision.
        writer.validate_return_manifest(manifest)
        validate_manifest(manifest)
        targets = {"region-key": region, "medium-key": region["medium"],
                   "group-key": group, "face-key": group["faces"][0]}
        if mutation in targets:
            targets[mutation]["extra"] = "unexpected"
        elif mutation.endswith("count"):
            region["n_bodies_expected"] = True if mutation == "bool-count" else 1.0
        else:
            group["drive_channel_id"] = {"uppercase-drive": "drive-LF",
                                         "double-hyphen-drive": "drive--lf",
                                         "unprefixed-drive": "lf"}[mutation]
        addin_verdict = _verdict(lambda: writer.validate_return_manifest(manifest))
        wg_verdict = _verdict(lambda: validate_manifest(manifest))
        assert addin_verdict == wg_verdict
        assert not wg_verdict["accepted"]
        assert "$.scope.fem_regions[0]" in wg_verdict["message"]
        assert expected in wg_verdict["message"]

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
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime, resolve_imported_submission
from server.jobs.runtime.imported_refusals import _imported_request_refusal
from server.jobs.store import JobStore
from server.mesh.artifact import read_verified_fem_region_mesh
from server.mesh.fem_regions import read_fem_region_domain
from server.solver.beat_adapter.results import acceleration_scale
from server.solver.coupled_admission import fem_drive_problem
from server.solver.coupled_system import CoupledSystemError, FemRegionInput, build_coupled_system
from server.tests.beat_runtime.fake_host_worker import coupled_result
from server.tests.fem_validation import _write_geometry, validation_request
from server.tests.test_cadlink_wgreturn import _manifest
from server.tests.test_fem_coupling import _write_assembly
from server.tests.test_fem_regions import _region, _write_step
from server.tests.test_imported_jobs import _request
from server.tests.test_official_beat_bridge import runtime as runtime_fixture
from server.tests.test_wglink_packaged_pair import _from_package, _verdict, packaged as packaged_fixture

runtime = runtime_fixture
packaged = packaged_fixture


def _addin_bundle(addin, root, *, coupled, option=None, shared=None):
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
        return _write_addin_bundle(addin, root, assembly, air, bounds,
                                   coupled=coupled, option=option, shared=shared)


def _write_addin_bundle(addin, root, assembly, air, bounds, *, coupled, option, shared):
    with (_from_package(addin, "wglink_fem.py") as fem,
          _from_package(addin, "wglink_return.py") as writer):
        faces = {"cone": fem.face_descriptor(
            area_cm2=12., centroid_cm=[2., 1.5, 0.], normal=[0., 0., 1.],
            bbox_min_cm=[0., 0., 0.], bbox_max_cm=[4., 3., 0.])}
        groups = [{"name": "Cone", "kind": "moving", "drive_role": "LF", "mesh_size_mm": 8.,
                   "faces": [{"key": "cone", "body_matches": True, "area_cm2": 12.}]}]
        if option == "lining" or shared == "one-region":
            faces["wall"] = fem.face_descriptor(
                area_cm2=12., centroid_cm=[2., 1.5, 2.], normal=[0., 0., -1.],
                bbox_min_cm=[0., 0., 2.], bbox_max_cm=[4., 3., 2.])
            groups.append({"name": "Wall", "kind": "rigid", "mesh_size_mm": 8.,
                           "faces": [{"key": "wall", "body_matches": True, "area_cm2": 12.}]})
            if option == "lining":
                groups[-1]["lining"] = {"model": "miki", "thickness_mm": 30.,
                                        "flow_resistivity_pa_s_per_m2": 5000.}
            else:
                groups[-1].update(name="Surround", kind="moving", drive_role="LF")
                faces["surround"] = faces.pop("wall")
        elif option is not None:
            groups[0]["relative_level_db"] = option
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
        exports = [fem.RegionExport(
            object_id="air-body", declaration=stored,
            faces_by_group={key: [face] for key, face in faces.items()},
            face_ids_by_group={group.group_id: group.face_ids for group in declaration.groups})]
        if shared == "two-regions":
            front = fem.plan_region_declaration(
                name="Front chamber", mesh_size_mm=10., body_is_solid=True, groups=groups)
            exports.append(fem.RegionExport(
                object_id="front-body", declaration=fem.read_region_attribute(front.body_attribute()),
                faces_by_group={key: [face] for key, face in faces.items()},
                face_ids_by_group={group.group_id: group.face_ids for group in front.groups}))
        regions = fem.plan_fem_regions(exports)
        assert regions[0]["surface_groups"][0]["drive_channel_id"] == "drive-lf"
        assert regions[0]["surface_groups"][0]["faces"][0]["area_mm2"] == 1200.
        assert regions[0]["n_bodies_expected"] == 1
        if option == 0:
            # Authoring omits the default; the protocol also permits explicit zero.
            regions[0]["surface_groups"][0]["relative_level_db"] = option

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
        for region in regions:
            fields["files"][region["file"]] = {
                "sha256": "sha256:" + hashlib.sha256(air).hexdigest(), "size_bytes": len(air),
                "media_type": "model/step", "purpose": "fem-region"}
        manifest = writer.build_return_manifest(**fields)
        raw = writer.dumps_return_manifest(manifest).encode("utf-8")
        assert writer.loads_return_manifest(raw) == manifest

    bundle = root / "golden.wgreturn"
    bundle.mkdir()
    members = {"wgreturn.json": raw, "assembly.step": assembly,
               **{region["file"]: air for region in regions}}
    for name, content in members.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return bundle, manifest, members


@pytest.mark.parametrize("coupled", [False, True], ids=["interior", "coupled"])
@pytest.mark.parametrize("level", [None, 0], ids=["default-level", "explicit-zero"])
def test_pinned_fem_writer_reader_mesh_and_fake_job(packaged, runtime, tmp_path, monkeypatch, coupled, level):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    bundle, manifest, members = _addin_bundle(packaged, tmp_path / "returns", coupled=coupled, option=level)
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
                assert field["schema"].item() == "wg-fem-field-store-v2"
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


@pytest.mark.parametrize("option,code,group_name", [
    ("lining", "fem_lining_unsupported", "Wall"),
    (-6., "fem_motion_weight_unsupported", "Cone"),
    (-120., "fem_motion_weight_unsupported", "Cone"),
])
@pytest.mark.parametrize("coupled", [False, True], ids=["interior", "coupled"])
@pytest.mark.parametrize("shared", [None, "two-regions"], ids=["one-region", "two-regions"])
def test_pinned_optional_physics_round_trips_but_refuses_before_meshing_and_admission(
    packaged, tmp_path, monkeypatch, option, code, group_name, coupled, shared,
):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    bundle, manifest, _ = _addin_bundle(
        packaged, tmp_path / "returns", coupled=coupled, option=option, shared=shared)
    assert read_wgreturn(bundle).manifest == manifest
    regions = manifest["scope"]["fem_regions"]
    group = next(g for g in regions[0]["surface_groups"] if g["name"] == group_name)
    assert group["lining" if option == "lining" else "relative_level_db"] == (
        {"model": "miki", "thickness_mm": 30., "flow_resistivity_pa_s_per_m2": 5000.}
        if option == "lining" else option)

    def unexpected(*_args, **_kwargs):
        pytest.fail("unsupported FEM options must refuse before meshing")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", unexpected)
    store = CadLinkStore(tmp_path / "cadlink.db")
    try:
        with pytest.raises(ingest.IngestRefusal) as caught:
            ingest.ingest_bundle(bundle, {
                "rigid_size_mm": 12., "transition_mm": 12., "source_size_mm": {},
            }, [], store, tmp_path / "data", defer_viewport=True)
        assert caught.value.reason_code == code
        assert caught.value.stage == "stage 2 scope gate"
        assert code not in str(caught.value)
        assert "meshing" not in str(caught.value)
        for label in ("Rear chamber", group_name, "not supported yet in this WG build"):
            assert label in str(caught.value)
        for region in regions:
            assert f"{region['name']!r} ({region['region_id']!r})" in str(caught.value)
            assert f"{group_name!r} ({group['group_id']!r})" in str(caught.value)
        assert ("remove the lining (or set the relative level to 0 dB) "
                "on these groups in WGLink and send again") in str(caught.value)
    finally:
        store.close()

    # An older stored ingest must not bypass the same declaration refusal.
    request = _request("wgi_" + "0" * 26, drive_channels=[],
                       mesh={"rigid_size_mm": 12., "transition_mm": 12., "source_size_mm": {}})
    request.options.engine = "beat-cpu"
    record = {"fem_regions": regions}
    problem = _imported_request_refusal(request, record)
    assert problem[0] == code
    assert problem[1] in str(caught.value)

    class NoEngine:
        async def capabilities(self):
            pytest.fail("unsupported FEM options must refuse before engine selection")

    with pytest.raises(ImportedSolveRefusal) as admission:
        asyncio.run(resolve_imported_submission(request, NoEngine(), imported_record=record))
    assert admission.value.reason_code == code
    assert problem[1] in str(admission.value)
    with pytest.raises(CoupledSystemError, match=code):
        build_coupled_system(None, [FemRegionInput(region, None) for region in regions])


@pytest.mark.parametrize("level", [0, 0., -0.])
def test_pinned_zero_relative_level_is_accepted(packaged, tmp_path, monkeypatch, level):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    bundle, manifest, _ = _addin_bundle(packaged, tmp_path / "returns", coupled=False, option=level)
    assert read_wgreturn(bundle).manifest == manifest
    assert manifest["scope"]["fem_regions"][0]["surface_groups"][0]["relative_level_db"] == 0
    assert ingest._refuse_fem_domain(manifest, {}, [])
    assert fem_drive_problem(manifest["scope"]["fem_regions"], {}, painted_sources=set()) is None


@pytest.mark.parametrize("shared", ["one-region", "two-regions"])
def test_pinned_shared_diaphragm_writer_keeps_wg_existing_refusal(packaged, tmp_path, monkeypatch, shared):
    monkeypatch.delenv("WG2_FEM_REGIONS", raising=False)
    bundle, manifest, _ = _addin_bundle(packaged, tmp_path / "returns", coupled=False, shared=shared)
    assert read_wgreturn(bundle).manifest == manifest
    regions = manifest["scope"]["fem_regions"]
    diaphragms = ("rear-chamber/cone, rear-chamber/surround" if shared == "one-region"
                  else "front-chamber/cone, rear-chamber/cone")
    expected = ("fem_multiple_diaphragms_unsupported",
                f"Channel 'drive-lf', regions/diaphragms {diaphragms}: "
                "more than one FEM diaphragm on one drive channel is not supported yet")
    assert fem_drive_problem(regions, {}, painted_sources=set()) == expected
    request = _request("wgi_" + "0" * 26, drive_channels=[],
                       mesh={"rigid_size_mm": 12., "transition_mm": 12., "source_size_mm": {}})
    assert _imported_request_refusal(request, {"fem_regions": regions}) == expected


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
    ("moving-lining", "lining: is accepted only on a rigid group"),
    ("rigid-relative-level", "relative_level_db: is accepted only on a moving group"),
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
        elif mutation == "moving-lining":
            group["lining"] = {"model": "miki", "thickness_mm": 30.,
                               "flow_resistivity_pa_s_per_m2": 5000.}
        elif mutation == "rigid-relative-level":
            group["kind"] = "rigid"
            group.pop("drive_channel_id")
            group["relative_level_db"] = 0.
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

"""V0: terminations refuse every exterior route without running a live solve."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.cadlink.intake import ingest
from server.jobs.models import DriveChannel
from server.jobs.runtime import ImportedSolveRefusal, resolve_imported_submission
from server.jobs.runtime.imported_refusals import _imported_request_refusal
from server.solver.beat_adapter import coupled_request
from server.solver.coupled_system import CoupledSystemError, ExteriorSurface, build_coupled_system, solve_kind
from server.tests.test_cadlink_wgreturn import _manifest
from server.tests.test_fem_engine_route import region_input
from server.tests.test_fem_ingest import _bundle, _ingest, _interior_region
from server.tests.test_fem_regions import _region
from server.tests.test_imported_jobs import _request

CODE = "fem_termination_with_exterior"
MESSAGE = (
    "FEM region 'Rear chamber' ('rear-chamber'), group 'Port' ('port'): "
    "A plane-wave termination is supported only in an interior-only solve. "
    "Remove the painted sources and interfaces, or model the horn as exterior."
)


def _closed_domain():
    return {"observations": {"planes": {
        plane: {"rigid_cut_rim_edges": 0, "solver_plane": plane}
        for plane in ("x0", "y0", "z0")
    }}}


def _termination_input():
    item = region_input("rear-chamber", "drive-lf", interface=True)
    item.record["name"] = "Rear chamber"
    item.record["surface_groups"][1].update(name="Port", kind="plane-wave-termination")
    surfaces = item.domain.surfaces
    name = next(name for name in surfaces if "kind=interface" in name)
    surfaces[name.replace("kind=interface", "kind=plane-wave-termination")] = surfaces.pop(name)
    return item


@pytest.mark.parametrize("exterior", ["painted-source", "same-region-interface", "other-region-interface"])
def test_v0_ingest_refuses_before_any_meshing(tmp_path, monkeypatch, exterior):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    region = _interior_region()
    regions = [region]
    if exterior == "same-region-interface":
        region["surface_groups"].append({**_region()["surface_groups"][1],
                                         "group_id": "opening", "name": "Opening"})
    elif exterior == "other-region-interface":
        regions.append(_region(region_id="front-chamber", name="Front chamber", object_id="air-2",
                               file="fem-regions/front-chamber.step"))
    bundle = _bundle(tmp_path / "returns", regions=regions)
    if exterior == "painted-source":
        path = bundle / "wgreturn.json"
        manifest = json.loads(path.read_text())
        source = _manifest(b"STEP")["sources"][0]
        source["instance_id"] = None
        source["selectors"] = {"appearance_labels": ["HF"]}
        manifest["sources"] = [source]
        path.write_text(json.dumps(manifest))

    def unexpected(*_args, **_kwargs):
        pytest.fail("V0 must refuse before starting any mesher")

    monkeypatch.setattr(ingest, "build_imported_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_imported_viewport_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_coupled_mesh_isolated", unexpected)
    monkeypatch.setattr(ingest, "build_fem_region_mesh_isolated", unexpected)
    with pytest.raises(ingest.IngestRefusal) as caught:
        _ingest(bundle, tmp_path)
    assert caught.value.stage == "stage 7 meshing"
    assert str(caught.value) == f"stage 7 meshing: {CODE}: {MESSAGE}"


@pytest.mark.parametrize("exterior", ["painted-source", "drive-channel", "same-region-interface", "other-region-interface"])
def test_v0_admission_refuses_before_engine_selection(monkeypatch, exterior):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    request = _request("wgi_" + "0" * 26)
    regions = [_termination_input().record]
    record = {"fem_regions": regions, "domain_interpretation": _closed_domain()}
    if exterior in {"painted-source", "drive-channel"}:
        request.geometry.drive_channels = [DriveChannel(id="drive-lf", source_ids=["source-a"])]
    else:
        request.geometry.drive_channels = []
    if exterior == "painted-source":
        record["source_tags"] = {"source-a": 101}
    elif exterior == "same-region-interface":
        regions[0]["surface_groups"].append({"group_id": "opening", "kind": "interface"})
    elif exterior == "other-region-interface":
        regions.append(region_input("front-chamber", "front", interface=True).record)

    class NoEngine:
        async def capabilities(self):
            pytest.fail("V0 must refuse before engine selection")

    assert _imported_request_refusal(request, record) == (CODE, MESSAGE)
    with pytest.raises(ImportedSolveRefusal) as caught:
        asyncio.run(resolve_imported_submission(request, NoEngine(), imported_record=record))
    assert CODE in str(caught.value)
    assert MESSAGE in str(caught.value)


@pytest.mark.parametrize("exterior", ["painted-source", "drive-channel", "other-region-interface"])
def test_imported_builder_refuses_before_reading_mesh_artifacts(monkeypatch, exterior):
    item = _termination_input()
    record = {"fem_regions": [item.record]}
    geometry = SimpleNamespace(drive_channels=[])
    if exterior == "painted-source":
        record["sources"] = [{"id": "source-a"}]
    elif exterior == "drive-channel":
        geometry.drive_channels = [SimpleNamespace(id="drive-lf", source_ids=["source-a"])]
    else:
        record["fem_regions"].append(region_input("front-chamber", "front", interface=True).record)

    def unexpected(*_args, **_kwargs):
        pytest.fail("the builder must refuse before reading or compiling meshes")

    monkeypatch.setattr(coupled_request, "read_verified_fem_region_mesh", unexpected)
    monkeypatch.setattr(coupled_request, "read_verified_coupled_exterior_mesh", unexpected)
    with pytest.raises(ValueError) as caught:
        coupled_request.build_imported_coupled_request(record, geometry, None, frequencies_hz=[100.])
    assert str(caught.value) == f"{CODE}: {MESSAGE}"


@pytest.mark.parametrize("exterior", ["surface", "other-region-interface"])
def test_compiled_builder_backstop_names_termination(exterior):
    item = _termination_input()
    regions = [item]
    surface = None
    if exterior == "surface":
        # Even a rigid exterior selects the coupled engine path.
        surface = ExteriorSurface(item.domain.points_mm, item.domain.surfaces,
                                  {name: {"kind": "rigid"} for name in item.domain.surfaces})
    else:
        regions.append(region_input("front-chamber", "front", interface=True))
    with pytest.raises(CoupledSystemError) as caught:
        build_coupled_system(surface, regions)
    assert str(caught.value) == f"{CODE}: {MESSAGE}"


def test_interior_termination_still_passes_admission_and_compilation(monkeypatch):
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    item = _termination_input()
    request = _request("wgi_" + "0" * 26)
    request.geometry.drive_channels = []
    record = {"fem_regions": [item.record], "domain_interpretation": _closed_domain()}
    assert _imported_request_refusal(request, record) is None
    system = build_coupled_system(None, [item])
    assert solve_kind(system) == "interior_fem"
    assert {r["kind"] for r in system["regions"]} == {"bounded_air"}
    assert "plane_wave_tube_termination" in {b["kind"] for b in system["boundaries"]}


def test_exterior_only_choice_excludes_declared_termination():
    request = _request("wgi_" + "0" * 26)
    request.geometry.exterior_only = True
    record = {"fem_regions": [_termination_input().record], "source_tags": {"source-a": 101},
              "domain_interpretation": _closed_domain()}
    assert _imported_request_refusal(request, record) is None

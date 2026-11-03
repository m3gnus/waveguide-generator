"""Exact counts, isolated cache identities, and one build per estimate and Build."""

import asyncio

import pytest

from server.design.schema import DesignConfig
from server.mesh import builder
from server.mesh.file_import import parse_file_mesh
from server.tests.test_design_assembly_record import assembly, design, woofer


@pytest.fixture(autouse=True)
def clear_cache():
    builder.clear_solver_mesh_cache()
    yield
    builder.clear_solver_mesh_cache()


def test_estimate_then_build_uses_one_exact_artifact(monkeypatch):
    config = DesignConfig.model_validate(design(assembly=assembly(woofer())))
    calls = []

    async def build(fn, *args, **kwargs):
        calls.append(fn)
        return {"msh_text": "exact artifact", "stats": {"triangle_count": 123}, "metadata": {}}

    monkeypatch.setattr(builder, "run_mesh_build", build)

    async def scenario():
        estimated = await builder.estimate_solver_mesh(config)
        built = await builder.build_assembly_mesh(config)
        assert estimated["triangle_count"] == built["stats"]["triangle_count"] == 123
        built["metadata"]["changed"] = True
        assert (await builder.build_assembly_mesh(config))["metadata"] == {}
        assert await builder.estimate_solver_mesh(config) == estimated

    asyncio.run(scenario())
    assert calls == [builder._build_assembly_sync]


def test_plain_estimate_is_reused_by_solver_with_request_local_policy(monkeypatch):
    config = DesignConfig.model_validate(design())
    calls = []

    async def build(fn, *args, **kwargs):
        calls.append(fn)
        return {"msh_text": "exact artifact", "stats": {"triangle_count": 7, "warnings": []},
                "metadata": {}, "integrity": {"valid": True, "self_intersection": {"checked": True}}}

    async def advisory(*_args, **_kwargs):
        pass

    monkeypatch.setattr(builder, "run_mesh_build", build)
    monkeypatch.setattr(builder, "_preview_wall_advisory", advisory)

    async def scenario():
        assert (await builder.estimate_solver_mesh(config))["triangle_count"] == 7
        built = await builder.build_solver_mesh(config, {"mesh_validation_mode": "strict"})
        assert built["stats"]["mesh_cache_hit"] is True
        assert built["stats"]["mesh_validation_mode"] == "strict"

    asyncio.run(scenario())
    assert calls == [builder._build_sync]


@pytest.mark.parametrize("change", [
    {"radius_mm": 81}, {"enabled": False}, {"shape": "ath_cone"},
    {"resolution_mm": 26}, {"drive": "rigid"},
    {"placement": {"anchor": "below", "gap_mm": 11}},
    {"placement": {"anchor": "free", "x_mm": 1, "y_mm": -200}},
    {"id": "00000000-0000-4000-8000-0000000000ab"},
])
def test_assembly_cache_tracks_every_woofer_mesh_input(change):
    first = DesignConfig.model_validate(design(assembly=assembly(woofer())))
    changed = DesignConfig.model_validate(design(assembly=assembly(woofer(**change))))
    assert builder._solver_mesh_cache_key(first, assembly=True) != builder._solver_mesh_cache_key(changed, assembly=True)


def test_assembly_cache_tracks_ath_envelope_and_separates_artifact_kinds(monkeypatch):
    config = DesignConfig.model_validate(design(assembly=assembly()))
    ordinary = builder._solver_mesh_cache_key(config)
    original = builder._solver_mesh_cache_key(config, assembly=True)
    assert original != ordinary
    absent = DesignConfig.model_validate(design())
    assert builder._solver_mesh_cache_key(absent, assembly=True) != original
    monkeypatch.setattr(builder, "assembly_ath_envelope", lambda _design: True)
    assert builder._solver_mesh_cache_key(config, assembly=True) != original


@pytest.mark.parametrize("section,field,value", [
    (None, "R", 101), (None, "scale", 1.1),
    ("mesh", "throat_resolution", 9), ("mesh", "mouth_resolution", 21),
    ("mesh", "rear_resolution", 41), ("mesh", "angular_segments", 36),
    ("mesh", "length_segments", 16), ("mesh", "wall_thickness", 6),
    ("mesh", "surface_deviation_mm", 0.5), ("mesh", "max_triangles", 19000),
    ("enclosure", "depth", 201), ("enclosure", "front_resolution", 31),
    ("enclosure", "back_resolution", 51), ("enclosure", "edge_radius", 11),
    ("enclosure", "space_b", 251), ("source", "shape", 2),
])
def test_assembly_cache_tracks_horn_and_density_inputs(section, field, value):
    dump = design(assembly=assembly(woofer()))
    first = builder._solver_mesh_cache_key(DesignConfig.model_validate(dump), assembly=True)
    target = dump if section is None else dump.setdefault(section, {})
    target[field] = value
    changed = builder._solver_mesh_cache_key(DesignConfig.model_validate(dump), assembly=True)
    assert changed != first


def test_real_estimate_and_build_share_exact_triangles(monkeypatch):
    monkeypatch.setenv("WG2_TEST_MESH_IN_PROCESS", "1")
    config = DesignConfig.model_validate(design(assembly=assembly(woofer())))

    async def scenario():
        estimate = await builder.estimate_solver_mesh(config)
        built = await builder.build_assembly_mesh(config)
        assert estimate["triangle_count"] == len(parse_file_mesh(built["msh_text"]).triangles)

    asyncio.run(scenario())

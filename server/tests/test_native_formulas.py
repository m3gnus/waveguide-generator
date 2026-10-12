"""Consumer regressions for the pinned mesher's documented native recipes."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException
from hornlab_mesher.config_builder import build_from_config, resolve_geometry
from hornlab_mesher.preview.api import build_preview_geometry
import meshio
import numpy as np
import pytest
from pydantic import ValidationError

from server.contracts.geometry import normalise_quadrants
from server.design.native import NATIVE_FORMULAS
from server.design.schema import DesignConfig, Expr
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design, save_design
from server.exports.api import WgLinkExportRequest, _export_wglink_sync
from server.cadlink.store import CadLinkStore
from server.exports.core import _build_step_solid_sync, _build_stl_mesh_sync, build_profiles
from server.mesh.builder import (
    MAX_SOLVER_MESH_ARTIFACT_TRIANGLES, _build_sync, _dense_solver_memory_requirements,
    _enforce_dense_solver_memory_ceiling, _mesh_policy, _solver_mesher_config,
    _triangles_and_tags, resolve_dense_solver_memory_limit,
)
from server.platform.memory import PhysicalMemory
from server.preview.core import _recentred_preview_config, preview_options
from server.preview.translate import design_to_mesher_config, has_closed_outer_body
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode


# Canonical spellings of the examples in mesher docs/native-throat-adapter.md,
# native-adapter-controls.md, native-adapter-axial-scale.md,
# native-mouth-roundover.md, absolute-axial-scale.md,
# native-terminating-arc.md and standalone-source-body.md at the dependency pin.
BODY = {"L_mm": 120, "r0_mm": 12.7, "a_deg": 40, "a0_deg": 15.5, "k": 1, "s": 0}
ADAPTER = {
    "mode": "authored", "contract_revision": 1,
    "driver_exit_diameter_mm": 25.4, "exit_half_angle_deg": 10,
    "length_mm": 40, "join_t": .137, "driver_handle_mm": 10, "body_handle_mm": 15,
}
RECIPES = {
    "OSSE-ADAPTER": {"profile": BODY, "throat_adapter": ADAPTER},
    "OSSE-ADAPTER-CONTROLS": {
        "scale": .25,
        "profile": {"L_mm": 137, "r0_mm": 17, "a_deg": 37, "a0_deg": 6, "k": 1.25, "s": 0},
        "throat_adapter": {"mode": "controls", "contract_revision": 1, "join_t": 0,
                           "control_points_mm": [[-31.7, 16.2], [-22.1, 16.9], [-9.3, 20.4]]},
        "mesh": {"throat_res_mm": 1, "mouth_res_mm": 1.5, "rear_res_mm": 2,
                 "max_triangles": 250000, "allow_large_mesh": True},
    },
    "OSSE-ADAPTER-AXIAL": {
        "profile": BODY, "throat_adapter": ADAPTER, "scale": 2, "axial_scale": 1,
        "mesh": {"vertical_offset_mm": 7, "throat_res_mm": 5, "mouth_res_mm": 15, "rear_res_mm": 10},
    },
    "OSSE-ROUNDOVER": {
        "profile": {**BODY, "a_deg": 45, "a0_deg": 10}, "mode": "freestanding",
        "mesh": {"wall_thickness_mm": 5, "mouth_roundover_radius_mm": 25,
                 "mouth_res_mm": 8, "allow_large_mesh": True},
    },
    "OSSE-AXIAL": {
        "profile": {**BODY, "r0_mm": 18, "a0_deg": 8}, "scale": 2, "axial_scale": 1.2,
        "mesh": {"vertical_offset_mm": 7, "throat_res_mm": 4, "mouth_res_mm": 12},
    },
    "OSSE-ARC": {
        "profile": BODY,
        "terminating_arc": {"contract_revision": 1, "join_t": .05, "end_tangent_deg": 100},
        "mesh": {"throat_res_mm": 5, "mouth_res_mm": 15, "allow_large_mesh": True},
    },
    "SOURCE-DISK": {
        "mode": "standalone-source",
        "source_body": {"radius_mm": 18, "outer_radius_mm": 22, "depth_mm": 8},
        "mesh": {"size_mm": 3, "vertical_offset_mm": 7},
    },
}
FAMILIES = sorted(NATIVE_FORMULAS)
# macos-latest has 7 GiB; fix the figure so this guard fails the same way on every OS.
SMALLEST_HOSTED_RUNNER = PhysicalMemory(7 * 1024**3, "7 GiB hosted runner", True)


def documented_config(formula: str) -> dict:
    recipe = deepcopy(RECIPES[formula])
    mesh = {"quadrants": 1234, "vertical_offset_mm": 0, "max_triangles": 18000,
            "allow_large_mesh": False, "scale_to_metres": True, **recipe.pop("mesh", {})}
    if formula != "SOURCE-DISK":
        mesh.setdefault("wall_thickness_mm", 0)
        recipe.setdefault("scale", 1)
        recipe["source"] = {"source_shape": 0}
    return {"formula": formula, "mode": recipe.pop("mode", "bare"), **recipe, "mesh": mesh}


def design_payload(formula: str) -> dict:
    config = documented_config(formula)
    payload = {key: deepcopy(value) for key, value in config.items()
               if key not in {"mode", "profile", "mesh", "source"}}
    profile_names = {"L_mm": "L", "r0_mm": "r0", "a_deg": "a", "a0_deg": "a0"}
    payload.update({profile_names.get(key, key): value for key, value in config.get("profile", {}).items()})
    mesh_names = {"vertical_offset_mm": "vertical_offset", "wall_thickness_mm": "wall_thickness",
                  "throat_res_mm": "throat_resolution", "mouth_res_mm": "mouth_resolution", "rear_res_mm": "rear_resolution"}
    payload["mesh"] = {mesh_names.get(key, key): int(value) if type(value) is bool else value
                       for key, value in config["mesh"].items() if key != "scale_to_metres"}
    return payload


def small_design(formula: str) -> DesignConfig:
    payload = design_payload(formula)
    payload["mesh"].update(max_triangles=18000, allow_large_mesh=0, vertical_offset=7)
    if formula != "SOURCE-DISK":
        payload["scale"] = .1
        payload["mesh"].update(throat_resolution=.4, mouth_resolution=.6, rear_resolution=.8)
        if "axial_scale" in payload:
            payload["axial_scale"] = .1
        # These two horns stay compact: native walls use fixed absolute refinement
        # tolerances whatever the mm resolutions, so size keeps dense solves under
        # a 7 GiB host's 3.5 GiB ceiling. Native formulas require the full circular
        # domain, so symmetry reduction is unavailable.
        if formula == "OSSE-ADAPTER-CONTROLS":
            payload["scale"] = .015
        if formula == "OSSE-ARC":
            payload["scale"] = .08
            payload["L"] = 30
        if formula == "OSSE-ROUNDOVER":
            payload["L"] = 20
            payload["mesh"].update(mouth_roundover_radius_mm=.5, wall_thickness=.1)
    return DesignConfig.model_validate(payload)


@pytest.mark.parametrize("formula", FAMILIES)
def test_translation_matches_documented_recipe(formula: str) -> None:
    design = DesignConfig.model_validate(design_payload(formula))
    config = design_to_mesher_config(design)
    assert config == documented_config(formula)
    assert resolve_geometry(config) is not None


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_file_and_endpoint_round_trip(formula: str) -> None:
    payload = design_payload(formula)
    payload["extra_keys"] = {"Note": "literal ; braces {} and café"}
    payload["extra_blocks"] = {"Report": {"items": {"Title": "Native example"}, "entries": ["Title = Native example"]},
                               "WG.Solve": {"items": {"FrequencySpacing": "log"}, "entries": ["FrequencySpacing = log"]}}
    design = DesignConfig.model_validate(payload)
    saved = asyncio.run(save_design({"design": design.model_dump(mode="json")}))
    parsed = parse(saved["text"])
    assert parsed.design == design
    assert serialize(parsed) == saved["text"]
    reopened = asyncio.run(open_design(saved["text"]))
    assert DesignConfig.model_validate(reopened["design"]) == design
    # Force a rewrite after an edit, including all six independent cubic poles.
    parsed.design.root.mesh.vertical_offset = Expr(value=8.125678901234)
    assert parse(serialize(parsed)).design == parsed.design


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_preview_full_domain_and_solve_safety(formula: str) -> None:
    design = small_design(formula)
    preview_config = _recentred_preview_config(design)
    assert preview_config["mesh"]["vertical_offset_mm"] == 0
    geometry = build_preview_geometry(preview_config, preview_options("coarse"))
    assert geometry.surfaces
    for surface in geometry.surfaces:
        assert np.isfinite(surface.positions).all()
    resolution = resolve_symmetry(design)
    assert validate_symmetry_mode("auto", resolution) == 1234
    assert validate_symmetry_mode("full", resolution) == 1234
    with pytest.raises(ValueError, match="full circular mesh domain"):
        validate_symmetry_mode("quarter", resolution)
    config = _solver_mesher_config(design)
    assert config["mesh"]["vertical_offset_mm"] == 0
    assert _solver_mesher_config(design, keep_placement=True)["mesh"]["vertical_offset_mm"] == 7
    guarded, warning, multiplier = _mesh_policy(design, config)
    assert multiplier == 1
    assert warning == 18000
    assert guarded["mesh"]["max_triangles"] == MAX_SOLVER_MESH_ARTIFACT_TRIANGLES
    assert guarded["mesh"]["allow_large_mesh"] is False
    assert "maxTriangles" not in guarded["mesh"]
    assert has_closed_outer_body(design.root) == (formula in {"OSSE-ROUNDOVER", "SOURCE-DISK"})


@pytest.mark.parametrize("formula", FAMILIES)
def test_pinned_mesher_builds_native_mesh(formula: str, tmp_path: Path) -> None:
    config = _solver_mesher_config(small_design(formula))
    path = tmp_path / "native.msh"
    build_from_config(config, path)
    mesh = meshio.read(path)
    triangles = mesh.cells_dict["triangle"]
    assert 0 < len(triangles) < MAX_SOLVER_MESH_ARTIFACT_TRIANGLES
    assert np.isfinite(mesh.points).all()
    assert set(mesh.cell_data_dict["gmsh:physical"]["triangle"]) == {1, 2}


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_cad_link_and_profiles_refuse_complete_recipe_loss(formula: str, tmp_path: Path) -> None:
    design = small_design(formula)
    with pytest.raises(ValueError, match="cannot export profile CSVs"):
        build_profiles(design, "profiles")
    with pytest.raises(HTTPException, match="cannot be sent to CAD Link") as caught:
        _export_wglink_sync(WgLinkExportRequest(design=design, designRevision=1), CadLinkStore(":memory:"), tmp_path, "native")
    assert caught.value.status_code == 422
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_step_and_stl_export_complete_geometry(formula: str) -> None:
    design = small_design(formula)
    step = _build_step_solid_sync(design.model_dump(mode="json"))
    assert "ISO-10303-21" in step.step_text
    assert step.cad_info is not None
    mesh = _build_stl_mesh_sync(design.model_dump(mode="json"))
    assert mesh["vertices"] and mesh["indices"]
    assert set(mesh["surfaceTags"]) == {1, 2}


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_uses_real_solver_mesh_pipeline(formula: str, tmp_path: Path) -> None:
    result = _build_sync(small_design(formula).model_dump(mode="json"))
    stats = result["stats"]
    assert stats["triangle_count"] > 0
    assert stats["domain_multiplier"] == 1
    assert stats["dense_solver_domain_multiplier"] == 1
    assert {tag for tag, n in stats["tag_counts"].items() if n} == {"1", "2"}
    assert stats["integrity"]["valid"] is True
    path = tmp_path / "solver.msh"
    path.write_text(result["msh_text"], encoding="utf-8")
    mesh = meshio.read(path)
    assert {cells.type for cells in mesh.cells} == {"triangle"}
    assert {name: value.tolist() for name, value in mesh.field_data.items()} == {
        "SD1G0": [1, 2], "SD1D1001": [2, 2],
    }


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_fixture_fits_a_7_gib_runner_dense_solver_ceiling(formula: str, tmp_path: Path) -> None:
    """Catch the macos-latest RC failure everywhere; the pipeline fails only on low-RAM hosts."""
    limit = resolve_dense_solver_memory_limit(physical=SMALLEST_HOSTED_RUNNER, environ={})
    assert limit.bytes == 7 * 1024**3 // 2
    design = small_design(formula)
    config, _, _ = _mesh_policy(design, _solver_mesher_config(design))
    path = tmp_path / "native.msh"
    build_from_config(config, path, allow_large_mesh=False)
    triangles, tags = _triangles_and_tags(meshio.read(path))
    quadrants = normalise_quadrants(config["mesh"]["quadrants"])
    mode = str(config.get("mode", "")).strip().lower()
    requirements = _dense_solver_memory_requirements(triangles, quadrants, mode=mode, tags=tags)
    dofs = requirements["used_vertex_count"]
    metal_dofs = requirements["metal_dof_count"]
    metal_detail = f" ({metal_dofs:,} Metal DOFs)" if metal_dofs != dofs else ""
    est = requirements["estimated_bytes"]
    assert est <= limit.bytes, (
        f"{formula}: {dofs:,} P1 DOFs{metal_detail} estimate {est/1024**3:.2f} GiB of dense-solver memory, "
        f"above the {limit.bytes/1024**3:.2f} GiB ceiling of a 7 GiB hosted runner; make this fixture more compact"
    )
    _enforce_dense_solver_memory_ceiling(triangles, quadrants, mode=mode, tags=tags, limit=limit)


@pytest.mark.parametrize("formula, updates, message", [
    ("OSSE-ADAPTER", {"throat_adapter": None}, "requires throat_adapter"),
    ("OSSE-ADAPTER", {"throat_adapter": {**ADAPTER, "body_handle_mm": 1000}}, "adapter"),
    ("OSSE-ADAPTER-CONTROLS", {"throat_adapter": {"mode": "controls", "control_points_mm": [[0, 1]]}}, "control_points_mm"),
    ("OSSE-AXIAL", {"axial_scale": .01}, "axial_scale/scale"),
    ("OSSE-ARC", {"terminating_arc": {"join_t": .05, "end_tangent_deg": 180}}, "clear the source"),
    ("OSSE-ROUNDOVER", {"mesh": {"wall_thickness": 5, "mouth_roundover_radius_mm": 5}}, "radius"),
    ("SOURCE-DISK", {"source_body": {"radius_mm": 18, "outer_radius_mm": 18, "depth_mm": 8}}, "radius"),
    ("SOURCE-DISK", {"mesh": {"size_mm": True}}, "size_mm"),
    ("OSSE-AXIAL", {"a": "40 + p"}, "a"),
    ("OSSE-ADAPTER", {"source": {"shape": 1}}, "source.shape"),
    ("OSSE-ARC", {"morph": {"target_shape": 2}}, "morph.target_shape"),
    ("SOURCE-DISK", {"mesh": {"quadrants": 12}}, "mesh.quadrants"),
    ("OSSE-AXIAL", {"simulation": {"sim_type": "infinite-baffle"}}, "infinite baffles"),
])
def test_invalid_native_compositions_fail_at_schema(formula: str, updates: dict, message: str) -> None:
    payload = design_payload(formula)
    payload.update(updates)
    with pytest.raises(ValidationError, match=message):
        DesignConfig.model_validate(payload)


def test_native_file_refuses_legacy_controls_instead_of_dropping_them() -> None:
    text = serialize(small_design("SOURCE-DISK"))
    with pytest.raises(TextConfigError, match="cannot mix"):
        parse(text + "Length = 120\n")


@pytest.mark.parametrize("formula", FAMILIES)
def test_frontend_family_defaults_validate_and_round_trip(formula: str) -> None:
    fixtures = json.loads((Path(__file__).parent / "fixtures/native-family-defaults.json").read_text())
    design = DesignConfig.model_validate(fixtures[formula])
    assert parse(serialize(design)).design == design
    assert resolve_geometry(design_to_mesher_config(design)) is not None


@pytest.mark.parametrize("formula", FAMILIES)
@pytest.mark.parametrize("section", [{"exponent": 4}, {"aspect_ratio": 1.5}])
def test_native_formulas_refuse_nondefault_cross_sections(formula: str, section: dict) -> None:
    with pytest.raises(ValidationError, match=f"{formula} does not support cross_section"):
        DesignConfig.model_validate({**design_payload(formula), "cross_section": section})


@pytest.mark.parametrize("formula", FAMILIES)
def test_native_circular_defaults_do_not_change_wire_or_mesher_config(formula: str) -> None:
    payload = design_payload(formula)
    design = DesignConfig.model_validate(payload)
    explicit = DesignConfig.model_validate({**payload, "cross_section": {"exponent": 2, "aspect_ratio": 1}})
    assert "cross_section" not in explicit.model_dump(mode="json")
    assert design_to_mesher_config(explicit) == design_to_mesher_config(design)
    assert "cross_section" not in design_to_mesher_config(explicit)
    assert parse(serialize(explicit)).design == design


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE", "ICW", "FREEFORM"])
@pytest.mark.parametrize("field", ["size_mm", "mouth_roundover_radius_mm"])
def test_legacy_designs_refuse_native_mesh_controls(formula: str, field: str) -> None:
    payload = {"formula": formula, "mesh": {field: 3}}
    if formula == "FREEFORM":
        payload.update(length=100, profile_h={"points": [{"t": 0, "r": 10}, {"t": 1, "r": 50}]},
                       profile_v={"points": [{"t": 0, "r": 10}, {"t": 1, "r": 50}]},
                       cross_sections=[{"t": 0, "shape": "ellipse"}, {"t": 1, "shape": "ellipse"}])
    with pytest.raises(ValidationError, match=f"mesh.{field} requires its native formula marker"):
        DesignConfig.model_validate(payload)

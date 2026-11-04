"""Fresh ATH controls imports enter the existing native construction."""
import numpy as np
import pytest

from hornlab_mesher.config_builder import resolve_geometry
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.exports.core import _build_step_solid_sync, _build_stl_mesh_sync, build_profiles
from server.exports.geometry_identity import geometry_hash_for_design
from server.mesh.builder import _solver_mesh_cache_key, _solver_mesher_config
from server.preview.core import _cache_relevant_config
from server.preview.translate import design_to_mesher_config


TEXT = """OSSE = {
L = 120
r0 = 18
a = 40
a0 = 8
k = 1
s = 0
}
Throat.Ext.Ctrl = -38,17.78,-27.4,18.6,-12.8,21.441923768883
Source.Shape = 2
ABEC.SimType = 2
Mesh.WallThickness = 0
Mesh.Quadrants = 1234
Mesh.AngularSegments = 16
Mesh.LengthSegments = 32
"""


def test_fresh_controls_import_roundtrips_through_native_preview_solve_and_export():
    imported = parse(TEXT)
    design = imported.design
    assert design.formula == "OSSE-ADAPTER-CONTROLS"
    assert design.root.text_import_version is None
    assert "Throat.Ext.Ctrl" not in design.root.extra_keys
    assert imported.migrations
    model = resolve_geometry(design_to_mesher_config(design)).geometry.controls_meridian
    for copy in (parse(serialize(imported)).design,
                 DesignConfig.model_validate_json(design.model_dump_json())):
        actual = resolve_geometry(design_to_mesher_config(copy)).geometry.controls_meridian
        assert actual.fingerprint == model.fingerprint
        assert actual.cubic_poles.tobytes() == model.cubic_poles.tobytes()
    solver = resolve_geometry(_solver_mesher_config(design)).geometry.controls_meridian
    assert solver.fingerprint == model.fingerprint
    np.testing.assert_array_equal(solver.cubic_poles, model.cubic_poles)
    assert "WG.NativeDesign" in serialize(imported)


@pytest.mark.parametrize("version", [
    "ath-2026-08c-v1", "ath-2026-09a-v2", "ath-2026-09a-v3", "native-v1", None,
])
def test_saved_controls_remain_passthrough_without_geometry_change(version):
    saved = "; MWG config\n"
    if version is not None:
        saved += f"; Waveguide Generator geometry-interpretation: {version}\n"
    saved += TEXT
    original = parse(saved).design
    without = parse(saved.replace("Throat.Ext.Ctrl = -38,17.78,-27.4,18.6,-12.8,21.441923768883\n", "")).design
    assert original.formula == "OSSE"
    assert getattr(original.root, "throat_adapter", None) is None
    assert "Throat.Ext.Ctrl" in original.root.extra_keys
    assert design_to_mesher_config(original) == design_to_mesher_config(without)
    assert serialize(parse(saved)) == saved


def test_imported_control_edits_reach_preview_solve_and_export_identity():
    design = parse(TEXT).design
    payload = design.model_dump(mode="json")
    payload["throat_adapter"]["control_points_mm"][1][1] += .1
    changed = DesignConfig.model_validate(payload)
    assert _cache_relevant_config(design_to_mesher_config(design)) != _cache_relevant_config(design_to_mesher_config(changed))
    assert _solver_mesh_cache_key(design) != _solver_mesh_cache_key(changed)
    assert geometry_hash_for_design(design) != geometry_hash_for_design(changed)


def test_import_reaches_actual_native_exports_and_refuses_lossy_csv(tmp_path):
    design = parse(TEXT + "Scale = .005\n").design
    payload = design.model_dump(mode="json")
    step = _build_step_solid_sync(payload, work_dir=str(tmp_path / "step"))
    assert "ISO-10303-21" in step.step_text
    mesh = _build_stl_mesh_sync(payload, work_dir=str(tmp_path / "stl"))
    assert mesh["vertices"] and mesh["indices"]
    assert set(mesh["surfaceTags"]) == {1, 2}
    with pytest.raises(ValueError, match="Export STEP instead"):
        build_profiles(design, "profiles")


@pytest.mark.parametrize("change", [
    ("OSSE =", "R-OSSE ="), ("s = 0", "s = .5"),
    ("Mesh.WallThickness = 0", "Mesh.WallThickness = 2"),
    ("17.78", "17.78+1"),
])
def test_unsupported_controls_report_what_to_change(change):
    with pytest.raises(TextConfigError) as caught:
        parse(TEXT.replace(*change))
    assert "Throat.Ext.Ctrl" in str(caught.value)
    assert "re-import" in str(caught.value)

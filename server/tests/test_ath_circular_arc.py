"""Measured no-op ATH selectors use ordinary OSSE through every consumer."""
import pytest

from hornlab_mesher.config_builder import resolve_geometry
from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.exports.core import build_profiles
from server.mesh.builder import _solver_mesher_config
from server.preview.translate import design_to_mesher_config


BLOCK = """OSSE = {
L = 120
r0 = 18
a = 40
a0 = 8
k = 1
s = 0
%s}
Source.Shape = 2
ABEC.SimType = 2
Mesh.WallThickness = 0
Mesh.Quadrants = 1234
Mesh.AngularSegments = 16
Mesh.LengthSegments = 64
Mesh.ZMapPoints = 0,0,1,1
"""
KEYS = "Throat.Profile = 3\nCircArc.Radius = 80\nCircArc.TermAngle = 60\n"


@pytest.mark.parametrize("inside", [False, True])
def test_noop_arc_import_reports_keys_and_roundtrips_through_preview_solve_export(inside):
    opened = parse(BLOCK % KEYS if inside else BLOCK % "" + KEYS)
    baseline = parse(BLOCK % "").design
    design = opened.design
    assert design == baseline
    prefix = "OSSE." if inside else ""
    assert {item.key for item in opened.ignored_settings} == {
        prefix + key for key in ("Throat.Profile", "CircArc.Radius", "CircArc.TermAngle")}
    assert all("OSSE block" in item.note for item in opened.ignored_settings)
    # Exercise both lossless text and a rewrite after a nongeometry edit.
    payload = design.model_dump(mode="json")
    payload.setdefault("simulation", {})["f1"] = 200
    opened.design = DesignConfig.model_validate(payload)
    for copy in (parse(serialize(opened)).design,
                 DesignConfig.model_validate_json(design.model_dump_json())):
        for translate in (design_to_mesher_config, _solver_mesher_config):
            actual = resolve_geometry(translate(copy)).geometry.inner_points
            expected = resolve_geometry(translate(baseline)).geometry.inner_points
            assert actual.tobytes() == expected.tobytes()
        assert build_profiles(copy, "profiles") == build_profiles(baseline, "profiles")


@pytest.mark.parametrize("version", [None, "native-v1", "ath-2026-08c-v1", "ath-2026-09a-v2", "ath-2026-09a-v3"])
def test_saved_arc_fields_keep_their_interpretation_and_text(version):
    header = "; MWG config\n"
    if version:
        header += f"; Waveguide Generator geometry-interpretation: {version}\n"
    text = header + BLOCK % "" + KEYS
    if version == "ath-2026-09a-v3":
        with pytest.raises(TextConfigError, match="Throat.Profile"):
            parse(text)
        return
    opened = parse(text)
    assert opened.design.root.throat_profile.constant_value() == 3
    assert opened.design.root.circ_arc_radius.constant_value() == 80
    assert serialize(opened) == text
    baseline = parse(text.replace(KEYS, "")).design
    for translate in (design_to_mesher_config, _solver_mesher_config):
        assert (resolve_geometry(translate(opened.design)).geometry.inner_points.tobytes()
                == resolve_geometry(translate(baseline)).geometry.inner_points.tobytes())


def test_active_flat_arc_is_refused_with_the_geometry_boundary():
    with pytest.raises(TextConfigError) as caught:
        parse("Length = 120\nThroat.Profile = 3\nCircArc.Radius = 200\n")
    assert "active flat circular-arc" in str(caught.value)

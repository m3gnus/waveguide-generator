"""Import provenance must survive every representation consumed by geometry."""
from __future__ import annotations

import json

import numpy as np
import pytest
from pydantic import ValidationError

from server.design.schema import DesignConfig
from server.design.textcfg import TextConfigError, parse, serialize
from server.design.text_import import TEXT_IMPORT_VERSION
from server.preview.translate import design_to_mesher_config
from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.profile_sampling import build_point_grid_arrays

ATH = """OSSE = {
L = 137
r0 = 17
a = 37
a0 = 6
k = 1.25
s = .7
n = 4
q = .995
}
Slot.Length = 15
Mesh.AngularSegments = 16
Mesh.LengthSegments = 32
Mesh.WallThickness = 0
Source.Shape = 2
"""


def grid(design):
    config = design_to_mesher_config(design)
    p, _, _ = build_geometry_params(config)
    return build_point_grid_arrays(p)["inner_grid"]


def test_external_ath_import_survives_json_copy_text_save_and_reopen():
    parsed = parse(ATH)
    assert parsed.design.root.text_import_version == TEXT_IMPORT_VERSION
    saved = serialize(parsed.design)
    assert f"geometry-interpretation: {TEXT_IMPORT_VERSION}" in saved
    variants = [parsed.design, parsed.design.model_copy(deep=True),
                DesignConfig.model_validate(json.loads(parsed.design.model_dump_json())),
                parse(saved).design]
    for design in variants:
        config = design_to_mesher_config(design)
        assert config["_textImportVersion"] == TEXT_IMPORT_VERSION
        assert np.array_equal(grid(design), grid(parsed.design))
        assert grid(design)[0, -1, 0] == pytest.approx(146.879189, abs=1e-6)


def test_native_geometry_stamp_keeps_native_circle_dimensions_after_reopen():
    native = parse("; Parameter config\n" + ATH + "Morph.TargetShape = 2\nMorph.TargetWidth = 360\nMorph.TargetHeight = 360\n").design
    assert native.root.text_import_version is None
    saved = serialize(native)
    assert "geometry-interpretation: native-v1" in saved
    for design in (native, parse(saved).design):
        assert "_textImportVersion" not in design_to_mesher_config(design)
        assert np.hypot(grid(design)[:, -1, 0], grid(design)[:, -1, 1]) == pytest.approx(180, abs=1e-12)


def test_native_stamp_can_override_external_looking_text_and_is_not_a_geometry_control():
    native = parse("; Waveguide Generator geometry-interpretation: native-v1\n" + ATH).design
    assert native.root.text_import_version is None
    assert "text_import_version" not in native.model_dump(mode="json")


@pytest.mark.parametrize("stamps", [
    "; Waveguide Generator geometry-interpretation: future-v2\n",
    "; Waveguide Generator geometry-interpretation: native-v1\n; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n",
])
def test_future_or_conflicting_text_stamps_fail(stamps):
    with pytest.raises(TextConfigError, match="geometry interpretation"):
        parse(stamps + ATH)
    with pytest.raises(ValidationError):
        DesignConfig.model_validate({"formula":"OSSE", "text_import_version":"future-v2"})


def test_imported_circle_ignores_native_target_dimensions_after_reload():
    parsed = parse(ATH.replace("Slot.Length = 15", "Slot.Length = 0") + "Morph.TargetShape = 2\nMorph.TargetWidth = 360\nMorph.TargetHeight = 360\n")
    for design in (parsed.design, parse(serialize(parsed.design)).design):
        assert np.hypot(grid(design)[:, -1, 0], grid(design)[:, -1, 1]) == pytest.approx(164.219794, abs=1e-6)


def test_absent_enclosure_stays_absent_and_explicit_zero_depth_is_retained():
    absent = parse(ATH).design
    assert absent.root.enclosure is None
    assert "enclosure" not in design_to_mesher_config(absent)
    explicit = parse(ATH + "Mesh.Enclosure = {\nDepth = 0\n}\n").design
    assert design_to_mesher_config(explicit)["enclosure"]["depth"] == 0


def test_rewrite_does_not_keep_a_stale_geometry_stamp():
    parsed = parse("; Waveguide Generator geometry-interpretation: ath-2026-08c-v1\n" + ATH)
    parsed.design.root.text_import_version = None
    text = serialize(parsed)
    assert "ath-2026-08c-v1" not in text
    assert text.count("geometry-interpretation:") == 1
    assert parse(text).design.root.text_import_version is None

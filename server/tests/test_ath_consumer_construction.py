"""Density must not reconstruct an ATH no-shrink import's authored wall.

The cfg is copied verbatim from native 746c6b1's ATH blocker fixtures. Physical
regressions explicitly xfail on the old 866f pin, which lacks the native
construction handoff; they must all run when that handoff is installed.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import hornlab_mesher.config_builder as native
import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.exports import core, sizing
from server.preview.core import _cache_relevant_config
from server.preview.geometry_identity import geometry_hash_for_design
from server.preview.translate import design_to_mesher_config


CFG = (Path(__file__).parent / "fixtures/ath_consumer/r-m-240-360-a0-n32-l20.cfg").read_text()
HEIGHT_MM = 388.3281572999748
requires_construction = pytest.mark.xfail(
    not hasattr(native, "retain_ath_import_construction"),
    reason="native 866f lacks retain_ath_import_construction; physical G1 gates require 746c6b1",
    strict=True,
)


def _design() -> DesignConfig:
    return parse(CFG).design


def _grid(config):
    params, _, _ = native.build_geometry_params(config)
    grid = native.build_point_grid(params)
    return np.asarray(grid["inner_points"]).reshape(grid["grid_n_phi"], grid["grid_n_length"] + 1, 3)


def _height(points):
    return np.ptp(points[:, -1, 1])


@requires_construction
@pytest.mark.parametrize("grid", [None, (96, 56)])
def test_bare_grid_keeps_authored_mouth_before_density_override(grid):
    design = _design()
    assert _height(_grid(design_to_mesher_config(design))) == pytest.approx(HEIGHT_MM)
    assert _height(_grid(core._bare_grid_config(design, grid=grid))) == pytest.approx(HEIGHT_MM)


@requires_construction
@pytest.mark.parametrize("corners", [6, 12])
def test_corner_density_alone_does_not_change_the_authored_mouth(corners):
    config = core._bare_grid_config(_design())
    config["mesh"]["cornerSegments"] = corners
    assert _height(_grid(config)) == pytest.approx(HEIGHT_MM)


@requires_construction
def test_solid_step_planner_and_writer_keep_authored_mouth(monkeypatch):
    seen = []

    def plan(config, **kwargs):
        seen.append(_height(_grid(config)))
        return sizing.CadPlan(12, None)

    def write(config, path):
        seen.append(_height(_grid(config)))
        path.write_text("STEP transport checked separately by the STEP contract tests")
        return path, None

    monkeypatch.setattr(core, "plan_cad_resolution", plan)
    monkeypatch.setattr("hornlab_mesher.cad.write_step_from_config", write)
    monkeypatch.setattr(core, "_assert_step", lambda *_a, **_k: None)
    core._build_step_solid_sync(_design().model_dump(mode="json"))
    assert seen == pytest.approx([HEIGHT_MM, HEIGHT_MM])


@requires_construction
def test_planner_point_grid_keeps_the_authored_construction():
    params = core._geometry_params(design_to_mesher_config(_design()))
    for angular, length in [(32, 20), (96, 56), (192, 112)]:
        points, _, _ = sizing._point_grid(params, angular, length)
        assert _height(points) == pytest.approx(HEIGHT_MM)


@requires_construction
def test_preview_cache_retains_authored_sampling_identity():
    authored = design_to_mesher_config(_design())
    other = copy.deepcopy(authored)
    other["mesh"]["angularSegments"] = 48
    relevant = _cache_relevant_config(authored)
    assert _height(_grid(relevant)) == pytest.approx(HEIGHT_MM)
    assert relevant != _cache_relevant_config(other)


@requires_construction
def test_symmetry_probe_keeps_the_authored_wall_before_budget_override(monkeypatch):
    from server.solver import symmetry

    real = native.build_point_grid
    heights = []

    def sample(params):
        grid = real(params)
        if (params["angularSegments"], params["lengthSegments"]) == (
            symmetry.SYMMETRY_ANGULAR_SEGMENTS, symmetry.SYMMETRY_LENGTH_SEGMENTS
        ):
            points = np.asarray(grid["inner_points"]).reshape(grid["grid_n_phi"], grid["grid_n_length"] + 1, 3)
            heights.append(_height(points))
        return grid

    monkeypatch.setattr(native, "build_point_grid", sample)
    symmetry._resolve_symmetry_uncached(_design())
    assert heights == pytest.approx([HEIGHT_MM])


@requires_construction
def test_stl_written_grid_matches_the_planner_after_corner_override(monkeypatch):
    design = _design()
    params = core._geometry_params(core._stl_mesher_config(design))
    planned, _, _ = sizing._point_grid(params, 96, 56)
    written = []

    class Captured(Exception):
        pass

    def build(config, *_args, **_kwargs):
        assert config["mesh"]["cornerSegments"] == sizing.EXPORT_CORNER_SEGMENTS
        written.append(_grid(config))
        raise Captured

    monkeypatch.setattr(core, "_stl_grid_plan", lambda _d: (sizing.GridPlan(96, 56, 0, 1), params))
    monkeypatch.setattr(native, "build_from_config", build)
    with pytest.raises(Captured):
        core._build_stl_mesh_sync(design.model_dump(mode="json"))
    assert _height(planned) == pytest.approx(HEIGHT_MM)
    np.testing.assert_array_equal(written[0], planned)


def test_frozen_construction_never_enters_saved_design_or_geometry_stamp():
    design = _design()
    saved, text = design.model_dump_json(), serialize(design)
    def stamp():
        return geometry_hash_for_design(design, mesher_version_value="identity-test")

    if hasattr(native, "retain_ath_import_construction"):
        identity = stamp()
    else:
        # 866f refuses this smaller-target import even without a consumer
        # density override. That refusal must not become a saved stamp either.
        with pytest.raises(ValueError, match="no qualified construction"):
            stamp()
        identity = None
    plain = design_to_mesher_config(design)
    core._bare_grid_config(design, grid=(96, 56))
    _cache_relevant_config(plain)
    assert design.model_dump_json() == saved
    assert serialize(design) == text
    assert design_to_mesher_config(design) == plain
    assert not any(key.startswith("_acoustic") for key in plain)
    assert "_acoustic" not in saved and "_acoustic" not in text
    if identity is None:
        with pytest.raises(ValueError, match="no qualified construction"):
            stamp()
    else:
        assert stamp() == identity


def test_old_native_without_handoff_keeps_its_existing_config(monkeypatch):
    from server.preview.construction import retain_mesher_construction

    monkeypatch.delattr(native, "retain_ath_import_construction", raising=False)
    config = design_to_mesher_config(_design())
    assert retain_mesher_construction(config) == config


@pytest.fixture(params=['ath-2026-09a-v2', 'ath-2026-09a-v3'], autouse=True)
def import_version(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'CFG',
                        f'; Waveguide Generator geometry-interpretation: {request.param}\n' + CFG)

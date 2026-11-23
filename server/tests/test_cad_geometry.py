"""The one CAD geometry resolver behind File -> STEP and Send to CAD."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest

from server.exports.cad_geometry import cad_mesher_config, resolve_cad_geometry
from server.exports.sizing import _with_resolution
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.preview.geometry_identity import geometry_hash
from server.tests.freeform_oldconfig_support import normalize_step
from server.tests.test_exports_sizing import _seed


def _old_writer_config(config: dict, resolution: float) -> dict:
    """What ``write_step_from_config`` resolved before the planner was reused."""

    working = deepcopy(_with_resolution(config, resolution))
    working["mesh"] = {**working["mesh"], "quadrants": "1234"}
    working.pop("quadrants", None)
    return working


def test_the_written_geometry_is_the_planners_accepted_probe(tmp_path) -> None:
    """F8: the accepted probe is written, not resolved a second time.

    Output identity, byte for byte: the STEP written from the reused probe is
    the STEP ``write_step_from_config`` wrote from the planned config.
    """

    from hornlab_mesher.cad import write_step, write_step_from_config
    from hornlab_mesher.config_builder import resolve_geometry

    design = _seed()
    cad = resolve_cad_geometry(design)
    assert cad.plan.deviation_mm is not None  # an accepted plan
    assert cad.plan.resolved is cad.resolved  # reused, not resolved again

    config = cad_mesher_config(design)
    old = resolve_geometry(_old_writer_config(config, cad.plan.resolution_mm))
    assert geometry_hash(cad.resolved.geometry, "v") == geometry_hash(old.geometry, "v")

    def both() -> tuple[str, str]:
        new_path, _ = write_step(cad.resolved.geometry, tmp_path / "new.step")
        old_path, _ = write_step_from_config(
            _with_resolution(config, cad.plan.resolution_mm), tmp_path / "old.step"
        )
        return new_path.read_text(), old_path.read_text()

    new_text, old_text = _run_in_gmsh_session(both)
    assert normalize_step(new_text) == normalize_step(old_text)


def test_cad_geometry_is_the_full_throat_centred_model_with_export_corners() -> None:
    from server.exports.sizing import EXPORT_CORNER_SEGMENTS
    from server.design.schema import DesignConfig

    design = DesignConfig.model_validate(
        {"formula": "OSSE", "L": 120, "a": 45, "mesh": {"quadrants": 1, "vertical_offset": 12}}
    )
    config = cad_mesher_config(design)
    assert config["mesh"]["quadrants"] == "1234"
    assert "quadrants" not in config
    assert config["mesh"]["cornerSegments"] == EXPORT_CORNER_SEGMENTS
    # Intentional (23 Nov 2026): no CAD output applies mesh.vertical_offset;
    # the throat centre is the CAD origin, as it is the solve's.
    assert config["mesh"]["verticalOffset"] == 0.0

    cad = resolve_cad_geometry(design)
    assert cad.resolved.geometry.closed
    assert cad.resolved.geometry.vertical_offset_mm == 0.0
    throat = np.asarray(cad.resolved.geometry.inner_points)[:, 0, :]
    np.testing.assert_allclose(throat.mean(axis=0), [0.0, 0.0, 0.0], atol=1e-9)


@pytest.mark.parametrize("mesh", [{}, {"mouth_resolution": 3, "throat_resolution": 1.5}])
def test_cad_geometry_does_not_follow_the_solve(mesh) -> None:
    default = resolve_cad_geometry(_seed())
    other = resolve_cad_geometry(_seed(**mesh))
    np.testing.assert_array_equal(
        np.asarray(default.resolved.geometry.inner_points),
        np.asarray(other.resolved.geometry.inner_points),
    )

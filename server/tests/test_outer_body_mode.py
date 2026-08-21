"""One definition of "bare shell", shared by every consumer of the design.

An omitted ``mesh.wall_thickness`` and an explicit ``0`` are different document
states: the first means ATH's 5 mm shell, the second means no outer body at all.
The document field stays nullable so a CFG round-trip is lossless, so anything
that has to know whether a closed body exists must resolve the default the same
way the mesh generator did. These tests pin that agreement.
"""

from __future__ import annotations

import pytest

from server.design.schema import DesignConfig
from server.preview.translate import (
    design_to_mesher_config,
    effective_wall_thickness,
    has_closed_outer_body,
    outer_body_mode,
)
from server.solver.bempp import _closed_mode
from server.solver.context import SolverContext
from server.solver.metal import _native_check_open_edges


def _design(**mesh: object) -> DesignConfig:
    sim_type = mesh.pop("sim_type", None)
    payload: dict[str, object] = {"formula": "R-OSSE", "mesh": dict(mesh)}
    if sim_type is not None:
        payload["simulation"] = {"sim_type": sim_type}
    return DesignConfig.model_validate(payload)


UNSET = _design()
BARE = _design(wall_thickness=0)
WALLED = _design(wall_thickness=5)
THICK = _design(wall_thickness=12)
IB_UNSET = _design(sim_type="infinite-baffle")
IB_BARE = _design(wall_thickness=0, sim_type="infinite-baffle")


@pytest.mark.parametrize(
    ("design", "wall", "mode"),
    [
        # Omitted is not zero: ATH's free-standing default is 5 mm, so an
        # untouched design is a thickened waveguide and only an explicit 0 is a
        # bare shell.
        (UNSET, 5.0, "freestanding"),
        (BARE, 0.0, "bare"),
        (WALLED, 5.0, "freestanding"),
        (THICK, 12.0, "freestanding"),
        (IB_UNSET, 0.0, "infinite-baffle"),
        (IB_BARE, 0.0, "infinite-baffle"),
    ],
)
def test_wall_default_and_mode(design: DesignConfig, wall: float, mode: str) -> None:
    root = design.root
    assert effective_wall_thickness(root) == wall
    assert outer_body_mode(root) == mode


@pytest.mark.parametrize(
    "design", [UNSET, BARE, WALLED, THICK, IB_UNSET, IB_BARE]
)
def test_mesher_config_matches_the_shared_resolution(design: DesignConfig) -> None:
    """``design_to_mesher_config`` is the geometry; the helpers must describe it."""

    config = design_to_mesher_config(design)
    assert config["mode"] == outer_body_mode(design.root)
    assert config["mesh"]["wallThickness"] == effective_wall_thickness(design.root)


def test_enclosure_outranks_the_wall() -> None:
    design = DesignConfig.model_validate(
        {
            "formula": "R-OSSE",
            "mesh": {"wall_thickness": 0},
            "enclosure": {"depth": 280},
        }
    )
    assert outer_body_mode(design.root) == "enclosure"
    assert has_closed_outer_body(design.root) is True
    # A preconfigured, inactive enclosure is not an outer body.
    inactive = DesignConfig.model_validate(
        {
            "formula": "R-OSSE",
            "mesh": {"wall_thickness": 0},
            "enclosure": {"depth": 0, "edge_radius": 30},
        }
    )
    assert outer_body_mode(inactive.root) == "bare"
    assert has_closed_outer_body(inactive.root) is False


def _context(design: DesignConfig) -> SolverContext:
    return SolverContext(
        design=design,
        frequency_range=(200.0, 20_000.0),
        num_frequencies=8,
        mesh_validation_mode="strict",
        quadrants=1,
    )


@pytest.mark.parametrize(
    ("design", "closed"),
    [
        # The regression: an omitted wall thickness is meshed as a closed 5 mm
        # shell, so mesh validation must judge it closed. Reading the raw field
        # with a 0 fallback used to excuse it as a bare shell and skip the
        # free-edge requirement on a body that has no free edges.
        (UNSET, True),
        (BARE, False),
        (WALLED, True),
        (IB_UNSET, False),
    ],
)
def test_solver_closedness_uses_the_same_resolution(
    design: DesignConfig, closed: bool
) -> None:
    assert has_closed_outer_body(design.root) is closed
    assert _closed_mode(_context(design)) is closed
    assert _native_check_open_edges(_context(design)) is closed

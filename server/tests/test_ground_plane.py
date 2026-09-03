"""The rigid ground-plane mounting: naming, placement, and its symmetry cost.

The claim under test is that a ground plane is a *different boundary* from an
infinite baffle and is kept distinguishable from it everywhere -- in the
mounting vocabulary, in the wire contract, and in the copy -- and that a solve
which cannot be run honestly is refused before meshing rather than after.
"""

from __future__ import annotations

import numpy as np
import pytest

from server.engines.registry import EngineInfo, _mountings, detect_engines
from server.jobs.models import GroundPlaneConfig, SolveOptions
from server.solver.ground_plane import (
    GROUND_PLANE_AXES,
    NATIVE_GROUND_PLANE,
    GroundPlane,
    GroundPlaneClearance,
    model_extent,
    observation_below_ground_warning,
    place_above_ground,
)
from server.solver.symmetry import SymmetryResolution, restrict_for_ground_plane


def _msh(vertices):
    nodes = "\n".join(
        f"{index + 1} {x!r} {y!r} {z!r}"
        for index, (x, y, z) in enumerate(vertices)
    )
    return (
        "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"
        f"$Nodes\n{len(vertices)}\n{nodes}\n$EndNodes\n"
        "$Elements\n1\n1 2 2 1 1 1 2 3\n$EndElements\n"
    )


def _coordinates(msh_text):
    return np.asarray(
        [
            [float(value) for value in line.split()[1:4]]
            for line in msh_text.splitlines()
            if line[:1].isdigit() and len(line.split()) == 4
        ]
    )


# --- naming -----------------------------------------------------------------


def test_ground_plane_is_named_by_axis_never_by_an_axis_pair():
    """``xy`` means three different things across this stack, so WG uses none.

    The axis-pair spelling is allowed to exist in exactly one mapping, at the
    hornlab-bempp-bem boundary. Anywhere else it would be a fourth meaning.
    """

    assert GROUND_PLANE_AXES == ("x", "y", "z")
    assert all(len(axis) == 1 for axis in GROUND_PLANE_AXES)
    # The mapping is the one place a pair appears, and it is not the identity:
    # the pair names the plane the axis is normal to.
    assert NATIVE_GROUND_PLANE == {"x": "yz", "y": "xz", "z": "xy"}
    assert GroundPlane(axis="y", height_m=1.0).native_plane == "xz"
    assert GroundPlane(axis="z", height_m=1.0).native_plane == "xy"


def test_the_mounting_value_cannot_be_confused_with_the_symmetry_token():
    mountings = _mountings(infinite_baffle=True, ground_plane_axes=("y",))
    assert mountings == ("free-standing", "infinite-baffle", "ground-plane")
    # No mounting is an axis-pair token, so no consumer can mistake one for a
    # symmetry plane by string equality.
    assert not ({"xy", "xz", "yz", "yz+xz"} & set(mountings))


def test_an_engine_without_a_ground_plane_does_not_advertise_the_mounting():
    assert _mountings(infinite_baffle=True, ground_plane_axes=()) == (
        "free-standing",
        "infinite-baffle",
    )
    assert _mountings(infinite_baffle=False, ground_plane_axes=()) == ("free-standing",)


def test_mounting_is_advertised_only_alongside_the_axes_that_back_it():
    """The vocabulary and the axis list cannot disagree on any real engine."""

    for engine in detect_engines():
        assert ("ground-plane" in engine.mountings) == bool(engine.ground_plane_axes)
        assert set(engine.ground_plane_axes) <= set(GROUND_PLANE_AXES)


def test_engine_info_defaults_to_no_ground_plane():
    engine = EngineInfo(name="x", available=True, reason="", version=None)
    assert engine.ground_plane_axes == ()
    assert "ground-plane" not in engine.mountings


# --- placement --------------------------------------------------------------


def test_placement_moves_only_the_ground_axis():
    text = _msh([(1.0, -2.0, 3.0), (-1.0, -0.5, 4.0)])
    moved = place_above_ground(text, GroundPlane(axis="y", height_m=2.0))
    assert _coordinates(moved).tolist() == [[1.0, 0.0, 3.0], [-1.0, 1.5, 4.0]]
    # Connectivity is untouched: only coordinates move.
    assert text.split("$Elements")[1] == moved.split("$Elements")[1]


@pytest.mark.parametrize("axis,component", [("x", 0), ("y", 1), ("z", 2)])
def test_placement_lifts_each_axis_independently(axis, component):
    before = [(-1.0, -1.0, -1.0), (2.0, 2.0, 2.0)]
    plane = GroundPlane(axis=axis, height_m=1.0)
    moved = place_above_ground(_msh(before), plane)
    shift = _coordinates(moved) - np.asarray(before)
    assert shift[:, component].tolist() == [1.0, 1.0]
    others = [index for index in range(3) if index != component]
    assert np.abs(shift[:, others]).max() == 0.0
    assert model_extent(moved, plane) == 0.0


def test_a_model_that_would_cross_the_plane_is_refused_before_the_solver_sees_it():
    text = _msh([(0.0, -0.5, 0.0), (0.0, 0.5, 0.0)])
    with pytest.raises(GroundPlaneClearance) as excinfo:
        place_above_ground(text, GroundPlane(axis="y", height_m=0.1))
    message = str(excinfo.value)
    # The refusal has to be actionable: it names the height that would work.
    assert "500.0 mm" in message
    assert "y = 0" in message


def test_resting_exactly_on_the_plane_is_allowed_here():
    """WG does not add a clearance rule the package does not have.

    A model whose lowest point lands exactly on the plane is the "resting on
    it" case, which hornlab-bempp-bem validates as a real cut. Refusing it here
    would pre-empt a check that belongs to the package.
    """

    text = _msh([(0.0, -0.5, 0.0), (0.0, 0.5, 0.0)])
    moved = place_above_ground(text, GroundPlane(axis="y", height_m=0.5))
    assert model_extent(moved, GroundPlane(axis="y", height_m=0.0)) == 0.0


def test_an_unknown_axis_is_rejected():
    with pytest.raises(ValueError, match="ground plane axis"):
        GroundPlane(axis="xy", height_m=1.0)


# --- the symmetry cost ------------------------------------------------------


def _resolution(*, xz: bool, yz: bool) -> SymmetryResolution:
    return SymmetryResolution(
        quadrants=1 if xz and yz else 12 if xz else 14 if yz else 1234,
        xz=xz,
        yz=yz,
        reasons={"xz": [], "yz": []},
        tolerance_mm=0.01,
    )


def test_a_floor_costs_the_xz_plane_and_degrades_quarter_to_half_yz():
    restricted = restrict_for_ground_plane(_resolution(xz=True, yz=True), "y")
    assert restricted.xz is False
    assert restricted.yz is True
    assert restricted.quadrants == 14
    assert any("ground plane" in reason for reason in restricted.reasons["xz"])


def test_a_side_wall_costs_the_yz_plane_instead():
    restricted = restrict_for_ground_plane(_resolution(xz=True, yz=True), "x")
    assert (restricted.xz, restricted.yz, restricted.quadrants) == (True, False, 12)


def test_a_rear_wall_costs_no_resolvable_plane():
    """It blocks only legacy ``xy`` bi-symmetry, which WG never resolves."""

    restricted = restrict_for_ground_plane(_resolution(xz=True, yz=True), "z")
    assert (restricted.xz, restricted.yz, restricted.quadrants) == (True, True, 1)


def test_no_ground_plane_leaves_the_resolution_untouched():
    resolution = _resolution(xz=True, yz=True)
    assert restrict_for_ground_plane(resolution, None) is resolution


def test_restriction_does_not_resurrect_a_plane_the_geometry_already_refused():
    restricted = restrict_for_ground_plane(_resolution(xz=False, yz=False), "y")
    assert (restricted.xz, restricted.yz, restricted.quadrants) == (False, False, 1234)


# --- the wire contract ------------------------------------------------------


def test_ground_plane_is_off_by_default_so_existing_solves_are_unchanged():
    options = SolveOptions()
    assert options.ground_plane.enabled is False
    assert options.ground_plane.axis == "y"


def test_a_negative_height_is_refused():
    with pytest.raises(ValueError, match="must not be negative"):
        GroundPlaneConfig(enabled=True, axis="y", height_m=-0.1)


def test_a_non_finite_height_is_refused():
    with pytest.raises(ValueError, match="must be finite"):
        GroundPlaneConfig(enabled=True, axis="y", height_m=float("inf"))


def test_axis_is_normalized_and_validated():
    assert GroundPlaneConfig(enabled=True, axis=" Y ").axis == "y"
    with pytest.raises(ValueError):
        GroundPlaneConfig(enabled=True, axis="xz")


# --- the cost is engine-dependent, and that is declared ------------------


def test_bempp_declares_that_a_ground_plane_composes_with_symmetry():
    """Measured against the pinned package, not assumed from its docs.

    hornlab-bempp-bem joins the symmetry spec and the ground plane into one
    reflection group, so a left-right-symmetric horn on a floor keeps its half
    mesh and pays four images on it. metal and BEAT each carry a single
    image-transform set and solve the full domain instead -- roughly four times
    the work for the same model -- so this is declared rather than assumed
    uniform, and the UI must not imply the engines are interchangeable here.
    """

    engines = {engine.name: engine for engine in detect_engines()}
    bempp = engines["bempp"]
    if not bempp.ground_plane_axes:
        pytest.skip("installed hornlab-bempp-bem has no ground plane")
    assert bempp.ground_plane_composes_with_symmetry is True
    # Confirm it end to end rather than trusting the probe's own claim.
    from hornlab_bempp_bem.symmetry import expand_symmetry_mesh

    # A plate in x >= 0 lifted clear of y = 0: it cuts on yz and stands above xz.
    vertices = np.array(
        [[0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 2.0, 0.0]],
        dtype=float,
    )
    triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    tags = np.array([1, 1], dtype=np.int32)
    expanded = expand_symmetry_mesh(
        vertices, triangles, tags, "yz", ground_plane="xz"
    )
    assert expanded.plane_spec == "yz+xz"
    assert len(expanded.image_signs) == 4
    # The ground plane is not a seam: the model stands clear of it.
    assert expanded.seam_planes == ("yz",)
    assert expanded.ground_plane == "xz"


def test_engines_without_the_mounting_do_not_claim_composition():
    for engine in detect_engines():
        if "ground-plane" not in engine.mountings:
            assert engine.ground_plane_composes_with_symmetry is False


# --- measurement points under the plane ---------------------------------


def test_the_default_height_and_distance_are_warned_about():
    """1 m height with WG's default 2 m distance puts a third of the arc under.

    The package logs this and solves on -- clipping the points would change
    array shapes and break angle indexing -- so the only symptom without a
    surfaced warning is a polar whose lower angles are quietly the upper ones.
    """

    warning = observation_below_ground_warning(
        GroundPlane(axis="y", height_m=1.0),
        {"distance": 2.0, "enabled_axes": ["horizontal", "vertical"]},
    )
    assert warning is not None
    assert "1000 mm below the ground plane" in warning
    assert "not a physical value" in warning


def test_no_warning_once_the_height_clears_the_measurement_distance():
    assert observation_below_ground_warning(
        GroundPlane(axis="y", height_m=2.5),
        {"distance": 2.0, "enabled_axes": ["horizontal", "vertical"]},
    ) is None


def test_a_horizontal_only_arc_never_descends():
    assert observation_below_ground_warning(
        GroundPlane(axis="y", height_m=0.1),
        {"distance": 2.0, "enabled_axes": ["horizontal"]},
    ) is None


def test_a_sphere_descends_even_with_only_the_horizontal_arc_enabled():
    assert observation_below_ground_warning(
        GroundPlane(axis="y", height_m=0.1),
        {"distance": 2.0, "enabled_axes": ["horizontal"], "spherical_sampling": True},
    ) is not None

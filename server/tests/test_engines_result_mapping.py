"""Golden v1-contract result mapping tests for Batch Q."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.solver.context import SolverContext
from server.solver.result_mapping import (
    REFERENCE_RHO_C,
    build_solver_response,
)


def _context(*, spherical: bool = False) -> SolverContext:
    context = SolverContext(
        design=DesignConfig.model_validate({"formula": "OSSE"}),
        frequency_range=(100.0, 200.0),
        num_frequencies=2,
    )
    context.polar_config["spherical_sampling"] = spherical
    if spherical:
        context.polar_config.update({"spherical_theta_count": 2, "spherical_phi_count": 3})
    return context


def _native_result(*, sphere: bool = False) -> SimpleNamespace:
    frequencies = np.asarray([100.0, 200.0])
    desired_impedance = np.asarray([1.0 + 2.0j, 2.0 - 0.5j])
    raw_impedance = np.conjugate(desired_impedance) * REFERENCE_RHO_C * 1j / (
        2.0 * np.pi * frequencies
    )
    result = SimpleNamespace(
        frequencies_hz=frequencies,
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        observation_planes=["horizontal", "vertical"],
        pressure_complex=np.asarray(
            [
                [[20.0e-6 + 0j, 10.0e-6 + 0j, 2.0e-6 + 0j], [20.0e-6, 10.0e-6, 2.0e-6]],
                [[0j, 10.0e-6j, 2.0e-6], [40.0e-6j, 10.0e-6j, 2.0e-6j]],
            ],
            dtype=np.complex128,
        ),
        directivity_db=np.asarray(
            [
                [[0.0, -6.0, -20.0], [0.0, -3.0, -18.0]],
                [[np.nan, -8.0, -24.0], [0.0, -4.0, -20.0]],
            ]
        ),
        impedance=raw_impedance,
    )
    if sphere:
        result.sphere_theta_deg = np.asarray([0, 0, 0, 90, 90, 90], dtype=float)
        result.sphere_phi_deg = np.asarray([0, 120, 240, 0, 120, 240], dtype=float)
        result.sphere_pressure_complex = np.asarray(
            [
                [20e-6, 20e-6, 20e-6, 10e-6, 10e-6, 10e-6],
                [40e-6, 40e-6, 40e-6, 10e-6, 10e-6, 10e-6],
            ],
            dtype=np.complex128,
        )
    return result


def _config(*, sphere_grid=None) -> SimpleNamespace:
    return SimpleNamespace(
        observation=SimpleNamespace(
            distance_m=2.0,
            origin="mouth",
            sphere_grid=sphere_grid,
        )
    )


def test_golden_units_phase_nulls_impedance_di_and_partial_warning() -> None:
    response = build_solver_response(
        result=_native_result(),
        config=_config(),
        context=_context(),
        start_time=0.0,
        metadata={
            "metal": {
                "solver_log": [
                    {"frequency_hz": 200.0, "lapack_info": 2},
                ]
            }
        },
    )
    assert response["frequencies"] == [100.0, 200.0]
    assert response["spl_on_axis"]["spl"][0] == pytest.approx(0.0)
    assert response["spl_on_axis"]["spl"][1] is None
    assert response["spl_on_axis"]["phase_degrees"] == [0.0, None]
    assert response["directivity"]["horizontal"][1][0][1] is None
    assert response["impedance"]["real"] == pytest.approx([1.0, 2.0])
    assert response["impedance"]["imaginary"] == pytest.approx([2.0, -0.5])
    assert response["metadata"]["partial_success"] is True
    assert response["metadata"]["warning_count"] == 1
    assert response["metadata"]["phase_quantity"] == "raw_wrapped_pressure_phase"
    assert response["metadata"]["impedance_drive"] == "unit_acceleration"
    assert response["metadata"]["balloon_sampling"]["status"] == "disabled"


@pytest.mark.parametrize(
    ("requested", "configured", "with_sphere", "status"),
    [
        (False, False, False, "disabled"),
        (True, False, False, "backend_unsupported"),
        (True, True, False, "missing_result"),
        (True, True, True, "available"),
    ],
)
def test_balloon_four_state_and_pole_normalization(
    requested: bool, configured: bool, with_sphere: bool, status: str
) -> None:
    response = build_solver_response(
        result=_native_result(sphere=with_sphere),
        config=_config(sphere_grid=(2, 3) if configured else None),
        context=_context(spherical=requested),
        start_time=0.0,
        metadata={},
    )
    assert response["metadata"]["balloon_sampling"]["status"] == status
    assert ("balloon" in response) is with_sphere
    if with_sphere:
        assert response["balloon"]["spl_norm_db"][0][0] == [0.0, 0.0, 0.0]
        assert response["balloon"]["spl_norm_db"][0][1] == pytest.approx([-6.02] * 3)
        assert response["balloon"]["hemisphere"] is True


def _cabinet_msh(*, back_z: float) -> str:
    """A source cap facing +z at the mouth, with a box stretching back to ``back_z``.

    Three physical-tag-2 nodes wound for a +z normal, plus wall triangles that
    set the extents: the mouth plane just ahead of the cap and the closed back
    panel ``back_z`` behind it.
    """

    return f"""$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
9
1 0 0 0
2 0.02 0 0
3 0 0.02 0
4 0 0 0.01
5 0.2 0 0.01
6 0 0.2 0.01
7 0 0 {back_z}
8 0.2 0 {back_z}
9 0 0.2 {back_z}
$EndNodes
$Elements
3
1 2 2 2 2 1 2 3
2 2 2 1 1 4 5 6
3 2 2 1 1 7 8 9
$EndElements
"""


@pytest.mark.parametrize("back_z", [-0.02, -0.16, -0.28, -2.0])
def test_a_cabinet_never_points_the_polar_arcs_out_through_its_back_panel(
    back_z: float,
) -> None:
    """The forward axis is the source cap's, at every cabinet depth.

    Deep boxes used to put the throat inside the front quarter of the mesh
    span, which flipped the axis and measured every polar two metres behind the
    closed back panel -- silent, and collapsing with frequency.
    """

    from server.solver.result_mapping import _gmsh22_observation_frame

    axis, origin, _horizontal, _vertical = _gmsh22_observation_frame(
        _cabinet_msh(back_z=back_z), origin_at="mouth", symmetry_plane="yz+xz"
    )
    assert axis.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert origin[0] == pytest.approx(0.0)
    assert origin[1] == pytest.approx(0.0)
    assert origin[2] > 0.0  # the mouth plane, never the back panel at back_z


def test_the_observation_frame_is_supplied_without_replacing_default_arcs() -> None:
    """The solve frame owns every frame-relative native feature, not just arcs."""

    from server.solver.result_mapping import native_observation_frame, observation_config

    class NativeObservationConfig:
        def __init__(self, *, custom_points: dict[str, object] | None = None, **_: object) -> None:
            self.custom_points = custom_points

    context = _context()
    frame = native_observation_frame(
        context,
        _cabinet_msh(back_z=-0.28),
        SimpleNamespace,
    )
    assert frame is not None
    assert frame.axis.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert frame.origin[2] > 0.0

    native = observation_config(
        context,
        NativeObservationConfig,
        RuntimeError,
        "test-adapter",
        msh_text=_cabinet_msh(back_z=-0.28),
    )
    # Default arcs are generated by the helper from frame_override. Reserving
    # custom_points for non-45-degree diagonals keeps Metal's sphere grid and
    # axial/source-profile calculations on that same explicit frame.
    assert native.custom_points is None


def test_deep_cabinet_sphere_grid_uses_the_same_forward_frame_as_polar_arcs() -> None:
    from hornlab_metal_bem import ObservationConfig
    from hornlab_metal_bem.observation import build_observation_points, build_sphere_grid_points

    from server.solver.result_mapping import native_observation_frame

    context = _context(spherical=True)
    frame = native_observation_frame(
        context,
        _cabinet_msh(back_z=-0.28),
        SimpleNamespace,
    )
    assert frame is not None
    observation = ObservationConfig(
        planes=list(context.polar_config["enabled_axes"]),
        distance_m=float(context.polar_config["distance"]),
        angle_min_deg=float(context.polar_config["angle_range"][0]),
        angle_max_deg=float(context.polar_config["angle_range"][1]),
        angle_count=int(context.polar_config["angle_range"][2]),
        origin=str(context.polar_config["observation_origin"]),
        sphere_grid=(2, 3),
    )
    arcs, _angles = build_observation_points(frame, observation)
    sphere, theta, _phi = build_sphere_grid_points(frame, observation)
    assert arcs[0, 0].tolist() == pytest.approx(sphere[0].tolist())
    assert theta[0] == pytest.approx(0.0)
    assert sphere[0, 2] > frame.origin[2]


def _infinite_baffle_msh() -> str:
    """Aperture tag 12 is deliberately not the mesh's furthest +z extent."""

    return """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
9
1 0 0 0
2 0.02 0 0
3 0 0.02 0
4 0 0 0.05
5 0.12 0 0.05
6 0 0.12 0.05
7 0 0 0.50
8 0.20 0 0.50
9 0 0.20 0.50
$EndNodes
$Elements
3
1 2 2 2 2 1 2 3
2 2 2 12 12 4 5 6
3 2 2 1 1 7 8 9
$EndElements
"""


def test_coupled_infinite_baffle_frame_uses_the_aperture_centroid() -> None:
    from server.solver.result_mapping import native_observation_frame

    context = _context()
    context.quadrants = 14  # yz symmetry projects x, but must preserve y and z.
    frame = native_observation_frame(
        context,
        _infinite_baffle_msh(),
        SimpleNamespace,
        aperture_tag=12,
    )
    assert frame is not None
    assert frame.axis.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert frame.origin.tolist() == pytest.approx([0.0, 0.04, 0.05])
    # The pinned Metal helper projects both the coupled aperture origin and its
    # diagnostic mouth_center onto native symmetry planes.
    assert frame.mouth_center.tolist() == pytest.approx([0.0, 0.04, 0.05])
    assert frame.origin[2] != pytest.approx(0.50)

    with pytest.raises(ValueError, match="no aperture triangles"):
        native_observation_frame(
            context,
            _cabinet_msh(back_z=-0.28),
            SimpleNamespace,
            aperture_tag=12,
        )


def test_a_mesh_without_a_source_tag_keeps_the_native_inference() -> None:
    """No source cap means no frame to supply; the backend still gets a config."""

    from server.solver.result_mapping import observation_config

    class NativeObservationConfig:
        def __init__(self, *, custom_points: dict[str, object] | None = None, **_: object) -> None:
            self.custom_points = custom_points

    sourceless = _cabinet_msh(back_z=-0.28).replace("1 2 2 2 2 1 2 3", "1 2 2 1 1 1 2 3")
    native = observation_config(
        _context(),
        NativeObservationConfig,
        RuntimeError,
        "test-adapter",
        msh_text=sourceless,
    )
    assert native.custom_points is None

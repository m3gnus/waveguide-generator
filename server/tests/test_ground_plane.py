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
from server.solver import beat as _beat_module
from server.solver import bempp as _bempp_module
from server.solver import circsym as _circsym_module
from server.solver import metal as _metal_module
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


#: Capability probes are NOT run by the tests in this file.
#:
#: ``detect_engines()`` calls the real probes, and on Windows and Linux the
#: BEAT-CPU one is not a lookup: ``server/engines/registry.py`` records that
#: "available" for that row means hornlab-beat-bem has instantiated the CPU
#: project and solved a 1 kHz probe through the precompiled engine bundle on
#: this machine. A unit test asking what an engine advertises has no business
#: starting a solver, and the work can outlive the test that started it -- this
#: module runs immediately before test_statusapp_controller.py, whose waits are
#: on child processes.
#:
#: The probes are imported inside ``detect_engines`` at call time, so stubbing
#: them on their own modules is what takes effect.
_STUB_STATUS = {"available": True, "reason": "stubbed for tests", "version": "test"}


@pytest.fixture(autouse=True)
def _no_real_solver_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _beat_module,
        "beat_backend_statuses",
        lambda: {
            backend: {**_STUB_STATUS, "backend": backend, "surface_traces": False}
            for backend in _beat_module.BEAT_BACKENDS
        },
    )
    monkeypatch.setattr(_bempp_module, "bempp_status", lambda: {
        **_STUB_STATUS,
        "coupled_infinite_baffle": True,
        "ground_plane_axes": ("x", "y", "z"),
        "ground_plane_composes_with_symmetry": True,
    })
    monkeypatch.setattr(_metal_module, "metal_status", lambda: dict(_STUB_STATUS))
    monkeypatch.setattr(_circsym_module, "circsym_status", lambda: dict(_STUB_STATUS))



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


def test_no_beat_engine_advertises_a_ground_plane_it_cannot_apply():
    """The advertisement must not outrun the adapter.

    hornlab-beat-bem CAN express a rigid half space at the pin this repository
    carries -- it exports GroundPlane, SolveConfig.ground_plane and
    GROUND_PLANE_AXES=("y",), and y is the floor in this application's frame.
    It is tempting to advertise that. But ``server/solver/beat.py`` never reads
    ``SolverContext.ground_plane``, and the mounting gate in
    ``resolve_auto_engine`` is the only thing keeping a grounded solve away
    from an engine that ignores it. Advertising the mounting first would let
    AUTO route a grounded solve to BEAT and return a FREE-STANDING answer to a
    question about a floor -- no error, just a wrong number.

    So this fails the moment someone re-adds the capability without wiring the
    adapter, which is the order the ``_ground_plane_axes`` docstring states.
    Delete this test in the same commit that makes BEAT consume the option.
    """
    for info in detect_engines():
        if not info.name.startswith("beat"):
            continue
        assert "ground-plane" not in info.mountings, info.name
        assert info.ground_plane_axes == (), info.name


def test_the_beat_adapter_really_does_not_consume_the_ground_plane():
    """Pins the evidence the decision above rests on, by enumeration.

    Greping for the word proves only that a spelling is absent. This reads
    every attribute the BEAT adapter takes off a solver context and asserts
    ``ground_plane`` is not among them, so the day someone wires it up this
    test fails and points at its sibling above.
    """
    import inspect
    import re

    from server.solver import beat as beat_module

    source = inspect.getsource(beat_module)
    named = set(re.findall(r"\bcontext\.([A-Za-z_]\w*)", source))
    dynamic = set(re.findall(r"getattr\(\s*context\s*,\s*[\"'](\w+)[\"']", source))

    assert "ground_plane" not in named | dynamic
    # And no escape hatch that would reach the field without naming it.
    for escape in ("vars(context", "asdict(context", "context.__dict__", "**context"):
        assert escape not in source, escape


def _grounded_request(*, engine: str = "auto", solver_mode: str = "auto"):
    from server.jobs.models import SolveRequest

    return SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {
                    "f1": 500,
                    "f2": 8000,
                    "num_frequencies": 3,
                    "sim_type": "freestanding",
                },
            },
            "options": {
                "engine": engine,
                "solver_mode": solver_mode,
                "symmetry": "auto",
                "ground_plane": {"enabled": True, "axis": "y", "height_m": 1.0},
            },
        }
    )


@pytest.mark.parametrize("engine", ["beat-cpu", "metal", "dryrun"])
def test_an_explicitly_chosen_engine_that_cannot_ground_is_refused(engine):
    """The AUTO mounting gate does not cover an explicitly selected engine.

    It runs only inside ``if engine_name == "auto"``. An engine named outright
    skips it entirely, so without a refusal at the submission boundary the
    solve is accepted and handed to an adapter that never reads
    ``SolverContext.ground_plane`` -- a free-standing answer to a question
    about a floor, with no error anywhere.
    """
    import asyncio

    from server.engines import registry
    from server.jobs.runtime import SymmetryValidationError, resolve_submission

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(engine, True, "test", "1", mountings=("free-standing",)),
            registry.EngineInfo(
                "bempp", True, "test", "1",
                mountings=("free-standing", "ground-plane"),
                ground_plane_axes=("x", "y", "z"),
            ),
        ],
        factory=lambda name: object(),
    )

    with pytest.raises(SymmetryValidationError, match="rigid ground plane"):
        asyncio.run(
            resolve_submission(_grounded_request(engine=engine), engine_registry)
        )


def test_an_engine_that_can_ground_is_accepted():
    """The refusal must not reject the engine that actually implements it."""
    import asyncio

    from server.engines import registry
    from server.jobs.runtime import resolve_submission

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "bempp", True, "test", "1",
                mountings=("free-standing", "ground-plane"),
                ground_plane_axes=("x", "y", "z"),
            ),
        ],
        factory=lambda name: object(),
    )

    resolution = asyncio.run(
        resolve_submission(_grounded_request(engine="bempp"), engine_registry)
    )
    assert resolution.engine_name == "bempp"


def test_an_axisymmetric_plan_does_not_smuggle_a_ground_plane_past_the_gate():
    """Finding that motivated the boundary refusal, pinned.

    The axisymmetric formulation is chosen BEFORE the AUTO mounting gate and
    sets the engine itself, so the gate never sees it. ``server/solver/
    circsym.py`` has no ground-plane handling at all, and this path is
    reachable from default frontend settings -- AUTO engine, AUTO solver mode,
    an axisym-eligible design -- with no explicit engine choice by the user.
    """
    import asyncio

    from server.engines import registry
    from server.jobs.runtime import SymmetryValidationError, resolve_submission
    from server.solver import circsym

    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo("axisym", True, "test", "1", mountings=("free-standing",)),
            registry.EngineInfo(
                "bempp", True, "test", "1",
                mountings=("free-standing", "ground-plane"),
                ground_plane_axes=("x", "y", "z"),
            ),
        ],
        factory=lambda name: object(),
    )

    original = circsym.axisymmetric_eligibility_reasons
    circsym.axisymmetric_eligibility_reasons = lambda _request: []
    try:
        with pytest.raises(SymmetryValidationError, match="rigid ground plane"):
            asyncio.run(
                resolve_submission(
                    _grounded_request(engine="auto", solver_mode="circsym"),
                    engine_registry,
                )
            )
    finally:
        circsym.axisymmetric_eligibility_reasons = original


def test_capability_tests_here_launch_no_solver_and_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe stub is the point of this file's autouse fixture, so pin it.

    ``detect_engines()`` is called by several tests here to read what an engine
    advertises. On Windows and Linux the real BEAT-CPU probe solves a 1 kHz
    problem through the precompiled engine bundle to decide "available", which
    is a solver run inside a unit test and can outlive it.

    Two independent assertions, because either alone is weak: no subprocess is
    created at all, and the BEAT probe specifically is the stub rather than the
    real one. Neutralise the fixture and the second fires immediately.
    """
    import subprocess

    def refuse(*args, **kwargs):  # pragma: no cover - the assertion is the point
        raise AssertionError(f"a unit test spawned a subprocess: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)

    probed: list[str] = []
    real = _beat_module.beat_backend_statuses
    monkeypatch.setattr(
        _beat_module,
        "beat_backend_statuses",
        lambda: probed.append("beat") or real(),
    )

    engines = {engine.name: engine for engine in detect_engines()}

    assert probed == ["beat"], "detect_engines must have asked the stubbed probe"
    assert engines["bempp"].available is True
    assert engines["bempp"].reason == "stubbed for tests", (
        "the real bempp probe ran; the autouse stub is not in effect"
    )


@pytest.mark.parametrize("engine", ["metal", "bempp"])
def test_a_baffle_and_a_ground_plane_are_refused_on_every_engine(engine):
    """The pair was refused only by the engine that implemented the check.

    bempp raised; Metal accepted the request and solved it, discarding one of
    the two half-space boundaries without saying which. It also cost a symmetry
    plane for nothing, because restrict_for_ground_plane subtracts the ground
    axis's mirror before any adapter sees the request -- so the user paid
    roughly double the mesh for a boundary the solve then threw away.
    """
    import asyncio

    from server.engines import registry
    from server.jobs.models import SolveRequest
    from server.jobs.runtime import SymmetryValidationError, resolve_submission

    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 120,
                "a": 45,
                "simulation": {
                    "f1": 500,
                    "f2": 8000,
                    "num_frequencies": 3,
                    "sim_type": "infinite-baffle",
                },
            },
            "options": {
                "engine": engine,
                "solver_mode": "auto",
                "symmetry": "auto",
                "ground_plane": {"enabled": True, "axis": "y", "height_m": 1.0},
            },
        }
    )
    engine_registry = registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "metal", True, "test", "1",
                mountings=("free-standing", "infinite-baffle"),
            ),
            registry.EngineInfo(
                "bempp", True, "test", "1",
                mountings=("free-standing", "infinite-baffle", "ground-plane"),
                ground_plane_axes=("x", "y", "z"),
            ),
        ],
        factory=lambda name: object(),
    )

    with pytest.raises(SymmetryValidationError, match="cannot be combined"):
        asyncio.run(resolve_submission(request, engine_registry))



def test_imported_cad_geometry_refuses_a_ground_plane():
    """Imported solves never reach the submission-boundary refusal.

    They are refused earlier as a separate path, so a ground plane left enabled
    in the parametric panel and carried into a CAD solve reached Metal, whose
    adapter does not read it -- a free-standing answer with full field traces
    and no warning. Refused where the imported context is built, which is the
    one place every imported solve passes through.
    """
    from server.jobs.models import SolveRequest
    from server.solver.context import SolverContext

    request = SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": "wgi_01J5A8QK3M9T2XVBH0RD7NWE6C",
                "manifest_sha256": "sha256:" + "a" * 64,
                "artifact_sha256": "sha256:" + "b" * 64,
                "drive_channels": [{"id": "main", "source_ids": ["source-main"]}],
                "mesh": {"rigid_size_mm": 20, "transition_mm": 40, "source_size_mm": {"source-main": 4}},
            },
            "options": {
                "engine": "metal",
                "frequency_range": [500, 8000],
                "num_frequencies": 3,
                "ground_plane": {"enabled": True, "axis": "y", "height_m": 1.0},
            },
        }
    )

    with pytest.raises(ValueError, match="not available for imported CAD"):
        SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")

"""Opt-in: declared FEM regions solved by the real BEAT worker.

Two boxes with closed-form expectations, built the way a CAD Link return is
(region STEP -> WG's tetrahedral mesher -> compiled system):

* a sealed 40 x 30 x 20 mm box driven by a 1 m/s piston, far below its first
  mode, must reach the lumped compliance pressure everywhere inside;
* the same box open on one face into the exterior air must radiate the
  monopole far field of the piston's volume velocity, with duct-scale pressure
  inside. This one pins the exterior winding: the opposite winding still
  passes BEAT's own interface diagnostics but puts kilopascals in the box.
"""

from __future__ import annotations

import base64
import copy
import math
import os

import numpy as np
import pytest

from server.mesh.fem_regions import read_fem_region_domain
from server.solver.coupled_system import ExteriorSurface, FemRegionInput, build_coupled_system
from server.tests.test_fem_regions import _mesh, _region, _write_step

_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1 and select -m live for real BEAT FEM qualification")]

RHO, C = 1.21, 343.0
AREA_M2 = 40e-3 * 30e-3
VOLUME_M3 = AREA_M2 * 20e-3


@pytest.fixture(scope="module")
def worker():
    pytest.importorskip("beat_engine")
    from server.solver.beat_runtime import readiness
    from server.solver.beat_runtime.manager import WorkerManager

    status = readiness.backend_status("cpu")
    julia = os.environ.get("WG2_TEST_BEAT_JULIA")
    if status["available"]:
        manager = WorkerManager(mode="child")
        try:
            yield manager.get_worker("cpu")
        finally:
            manager.shutdown()
    elif julia:
        # A development machine with beat_engine instantiated by hand but no
        # WG-provisioned runtime: drive the engine's own worker directly.
        from beat_engine.client import EngineWorker
        from beat_engine.paths import engine_paths

        paths = engine_paths("cpu")
        # The suite sandboxes HOME, so the hand-instantiated depot is named:
        # WG2_TEST_BEAT_DEPOT, or the account's real ~/.julia.
        depot = os.environ.get("WG2_TEST_BEAT_DEPOT")
        if depot is None:
            import pwd

            depot = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".julia")
        environment = dict(os.environ, JULIA_DEPOT_PATH=depot)
        engine = EngineWorker(julia_executable=julia, solver_script=paths.system_solver,
                              julia_threads=2, julia_project=paths.project, startup_timeout_s=900,
                              environment=environment)
        try:
            yield engine
        finally:
            engine.terminate()
    else:
        pytest.skip(status["reason"] + " (or set WG2_TEST_BEAT_JULIA)")


def _solve(worker, system, frequencies, outputs):
    request = {
        "schema_version": 1,
        "compiled_system": system,
        "frequencies_hz": frequencies,
        "excitation_port_ids": [port["id"] for port in system["excitation_ports"]],
        "outputs": outputs,
        "solver_options": {"phasor_convention": "exp(+i omega t)", "precision": "float64", "bem_backend": "cpu"},
    }
    results: dict[tuple[float, str], np.ndarray] = {}
    for event in worker.submit(request):
        if event.get("type") == "result":
            result = event.get("result", event)
            for quantity in result["quantities"]:
                values = quantity["values"]
                raw = base64.b64decode(values["content_base64"])
                results[(result["freq_hz"], quantity["id"])] = np.frombuffer(
                    raw, dtype=np.dtype(values["dtype"])
                ).reshape(values["shape"])
        elif event.get("type") in {"failed", "cancelled"}:
            pytest.fail(f"BEAT refused the FEM solve: {str(event)[:500]}")
    return results


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    step = _write_step(tmp_path_factory.mktemp("fem-live") / "box.step")
    region = _region(mesh_size_mm=6.0)
    for group in region["surface_groups"]:
        group["mesh_size_mm"] = 6.0
    return step, region


def test_a_sealed_box_reaches_the_lumped_compliance_pressure(worker, box):
    step, region = box
    sealed = copy.deepcopy(region)
    sealed["surface_groups"] = sealed["surface_groups"][:1]
    domain = read_fem_region_domain(_mesh(step, sealed)["msh_text"], "rear-chamber")
    system = build_coupled_system(None, [FemRegionInput(sealed, domain)])
    results = _solve(worker, system, [100.0],
                     [{"id": "p", "quantity": "fem_nodal_pressure", "target_ids": [], "options": {}}])
    pressure = np.abs(results[(100.0, "p")].ravel())
    lumped = RHO * C * C * AREA_M2 / (2 * math.pi * 100.0 * VOLUME_M3)
    assert pressure.mean() == pytest.approx(lumped, rel=2e-3)
    assert pressure.std() / pressure.mean() < 1e-3


def test_an_open_box_radiates_the_pistons_monopole(worker, box):
    step, region = box
    domain = read_fem_region_domain(_mesh(step, region)["msh_text"], "rear-chamber")
    cone = "wg-fem-v1|region=rear-chamber|group=cone|kind=moving"
    port = "wg-fem-v1|region=rear-chamber|group=port|kind=interface"
    walls = "wg-fem-v1|region=rear-chamber|group=_walls|kind=rigid"
    exterior = ExteriorSurface(
        points_mm=domain.points_mm,
        groups={"outside": np.concatenate([domain.surfaces[walls], domain.surfaces[cone]]),
                port: domain.surfaces[port]},
        kinds={"outside": {"kind": "rigid"}, port: {"kind": "interface"}},
    )
    system = build_coupled_system(exterior, [FemRegionInput(region, domain)])
    results = _solve(worker, system, [100.0], [
        {"id": "p", "quantity": "fem_nodal_pressure", "target_ids": [], "options": {}},
        {"id": "far", "quantity": "exterior_pressure", "target_ids": [],
         "options": {"points_m": [[2.0, 0.015, 0.01], [0.02, 0.015, 2.0]]}},
    ])
    monopole = RHO * 2 * math.pi * 100.0 * AREA_M2 / (4 * math.pi * 2.0)
    far = np.abs(results[(100.0, "far")].ravel())
    assert far == pytest.approx([monopole, monopole], rel=0.05)
    inside = np.abs(results[(100.0, "p")].ravel())
    # A 40 mm duct moved at 1 m/s: rho * omega * L * v is about 30 Pa.
    assert inside.max() < 100.0


def test_f7_open_box_radiates_the_pistons_monopole(worker, tmp_path, monkeypatch):
    """Qualify the real isolated F7 skin, including the material wall thickness."""
    from server.tests.test_fem_coupling import _coupled, _coupling_bundle, _system

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    region["mesh_size_mm"] = 6.0
    for group in region["surface_groups"]:
        group["mesh_size_mm"] = 6.0
    built = _coupled(bundle, manifest, region)
    system, _domain = _system(built, region)
    assert set(system["interfaces"][0]["topology"]["normal_sign"]) == {1}
    results = _solve(worker, system, [100.0], [
        {"id": "p", "quantity": "fem_nodal_pressure", "target_ids": [], "options": {}},
        {"id": "far", "quantity": "exterior_pressure", "target_ids": [],
         "options": {"points_m": [[2.0, 0.015, 0.01], [0.02, 0.015, 2.0]]}},
    ])
    monopole = RHO * 2 * math.pi * 100.0 * AREA_M2 / (4 * math.pi * 2.0)
    far = np.abs(results[(100.0, "far")].ravel())
    assert far == pytest.approx([monopole, monopole], rel=0.05)
    inside = np.abs(results[(100.0, "p")].ravel())
    assert 1.0 < inside.max() < 100.0


def test_f8_managed_cpu_bridge_maps_f7_fields(worker, tmp_path, monkeypatch):
    """Opt-in broker qualification of the managed F8 request/event route."""
    from types import SimpleNamespace
    from server.mesh.fem_coupling import read_coupled_exterior
    from server.solver.beat_adapter.coupled_request import build_coupled_request
    from server.solver.beat_adapter.observations import build_observations
    from server.solver.beat_adapter.results import acceleration_scale
    from server.solver.beat_runtime.manager import ManagedWorker
    from server.solver.official_beat import solve_coupled_compiled
    from server.tests.test_fem_coupling import _coupled, _coupling_bundle, _system

    if not isinstance(worker, ManagedWorker):
        pytest.skip("F8 managed bridge qualification requires a WG-provisioned CPU runtime")
    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    bundle, manifest, region = _coupling_bundle(tmp_path / "returns")
    built = _coupled(bundle, manifest, region)
    _system(built, region)
    surface = built["exterior"]
    points, groups = read_coupled_exterior(surface["msh_text"])
    exterior = ExteriorSurface(points, groups, surface["physical_names"])
    domain = read_fem_region_domain(built["regions"][region["region_id"]]["msh_text"], region["region_id"])
    layout = build_observations(angle_range=(0., 90., 2), planes=("horizontal",),
                                origin_m=(.02, .015, .01), sphere_grid=None, precision="float64")
    request = build_coupled_request(exterior, [FemRegionInput(region, domain)], layout=layout,
        frequencies_hz=[100.], frame={"origin": [.02, .015, .01], "axis": [0., 0., 1.],
                                     "u": [1., 0., 0.], "v": [0., 1., 0.]})
    progress = []
    sweep = solve_coupled_compiled(request, worker_manager=SimpleNamespace(get_worker=lambda *a, **k: worker),
                                   progress_callback=lambda *args: progress.append(args))
    assert progress == [(0, 1, 100.)] and not sweep.cancelled
    response = sweep.channels["drive-lf"]
    monopole = RHO * AREA_M2 / (4 * math.pi * 2.)
    assert np.abs(response.pressure_complex[0, 0]) == pytest.approx([monopole, monopole], rel=.05)
    velocity_pressure = np.abs(sweep.nodal_pressure / acceleration_scale(100.))
    assert 1. < velocity_pressure.max() < 100.

"""The compatible Metal path preserves WG's live and durable result contract."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.solver.beat_adapter.request import build_parametric_request
from server.solver.context import SolverContext
from server.solver import metal, metal_worker
from server.solver.metal_worker import solve_worker
from server.tests.test_official_beat_bridge import _result as worker_record
from server.tests.test_engines_adapters import _cabinet_msh, _context, _result as native_result
from server.tests.test_imported_jobs import _native_result as imported_result, _request, _roled_record


def test_unsupported_mesh_keeps_native_path_before_any_worker_solve(monkeypatch):
    monkeypatch.setattr(metal_worker, "worker_available", lambda: True)
    monkeypatch.setattr(metal_worker, "validate_worker_request", lambda _: (_ for _ in ()).throw(ValueError("mesh")))
    mesh = (Path(__file__).resolve().parents[1] / "solver" / "warmup_mesh.msh").read_text()
    selected, reason = metal_worker.select_worker_request(lambda: object(), msh_text=mesh, merge_tol=1e-9)
    assert selected is None
    assert reason == "The compatible interface does not accept this mesh or request"


@pytest.mark.parametrize("change", ["unused", "seam"])
def test_mesh_cleanup_stays_on_native_path(monkeypatch, change):
    monkeypatch.setattr(metal_worker, "worker_available", lambda: True)
    monkeypatch.setattr(metal_worker, "validate_worker_request", lambda _: pytest.fail("worker validated"))
    mesh = _cabinet_msh()
    if change == "unused":
        mesh = mesh.replace("$Nodes\n9\n", "$Nodes\n10\n").replace("$EndNodes", "10 5 5 5\n$EndNodes")
    else:
        mesh = mesh.replace("4 0 0 0.05", "4 0.0000000001 0 0")
    selected, reason = metal_worker.select_worker_request(lambda: object(), msh_text=mesh, merge_tol=1e-9)
    assert selected is None
    assert reason == "The native interface is required to preserve mesh loading"


def test_parametric_metal_routes_qualified_request_through_worker(monkeypatch):
    candidate = object()
    calls = []
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda *_: pytest.fail("native path used"))
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "version": "test"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal_worker, "select_worker_request", lambda *_, **__: (candidate, None))

    def solve(selected, **kwargs):
        calls.append((selected, kwargs["channel_id"]))
        return native_result()

    monkeypatch.setattr(metal_worker, "solve_worker", solve)
    response = metal.solve_metal_from_msh_text(_cabinet_msh(), _context(field_plane=False))
    assert calls == [(candidate, "source")]
    assert response["metadata"]["metal"]["interface"] == "beat-compatible"
    assert response["frequencies"] == [500., 1000.]


def test_axial_metal_keeps_native_interface(monkeypatch):
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda *_: native_result())
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "version": "test"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal_worker, "select_worker_request", lambda *_, **__: pytest.fail("worker selected"))
    response = metal.solve_metal_from_msh_text(_cabinet_msh(), _context(axial=True, field_plane=False))
    assert response["metadata"]["metal"]["interface"] == "native"


def test_imported_single_source_preserves_channel_live_results(monkeypatch, tmp_path):
    candidate = object()
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "ok"})
    monkeypatch.setattr(metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_solve_multi_source", lambda *_args, **_kwargs: pytest.fail("native path used"))
    monkeypatch.setattr(metal_worker, "select_worker_request", lambda *_, **__: (candidate, None))

    def solve(selected, **kwargs):
        assert selected is candidate and kwargs["channel_id"] == "left"
        kwargs["publish"](0, {"frequencies": [100.], "metadata": {}})
        return imported_result()

    monkeypatch.setattr(metal_worker, "solve_worker", solve)
    request = _request("wgi_" + "0" * 26, drive_channels=[{"id": "left", "source_ids": ["source-a"]}],
                       mesh={"rigid_size_mm": 8., "transition_mm": 20., "source_size_mm": {"source-a": 3.}})
    request.options.frequencies_hz = [100., 200.]
    mesh_path = tmp_path / "imported.msh"
    mesh_path.write_text("msh")
    streamed = []
    response = metal.solve_imported_metal_from_msh_text(
        "msh", request, _roled_record(mesh_path), result_callback=lambda i, value: streamed.append((i, value)),
    )
    assert response["metadata"]["solver_engine"]["interface"] == "beat-compatible"
    assert response["channel_order"] == ["left"]
    assert streamed[0][1]["channels"]["left"]["metadata"]["role"] == "HF"


def test_worker_keeps_live_results_power_traces_and_source_identity(monkeypatch):
    import beat_engine.beat_contract.worker as contract
    import hornlab_metal_bem

    state = SimpleNamespace(terminated=False, progress=[])
    monkeypatch.setattr(contract, "negotiate_submission", lambda *args: None)

    class Worker:
        def __init__(self, *, native_formulation, complex_k_shift, native_progress_cb):
            assert native_formulation == "complex_k" and complex_k_shift == .005
            self.callback = native_progress_cb
            self.worker_info = {}

        def ensure_started(self):
            pass

        def submit(self, request):
            count = sum(len(out["options"]["points_m"]) for out in request["outputs"]
                        if out["quantity"] == "exterior_pressure")
            for i, frequency in enumerate(request["frequencies_hz"]):
                try:
                    self.callback(i, frequency, {
                        "observation_pressure_complex": np.full((1, count), 3 + 4j), "impedance": 5 + 6j,
                        "lapack_info": 0, "native_diagnostics": {"mesh_resolution_suspect": True},
                    })
                except InterruptedError:
                    yield {"type": "cancelled", "solved_count": 0}
                    return
                record = worker_record(request, frequency)
                record["diagnostics"]["engine_provenance"] = {
                    "engine": {"version": "1", "source_sha256": "a" * 64},
                    "runtime": {"helper": {"sha256": "b" * 64},
                                "metal_library": {"source": "metallib", "sha256": "c" * 64}},
                    "execution": {"backend": "metal"},
                }
                yield {"type": "result", "result": record}
            yield {"type": "completed", "solved_count": len(request["frequencies_hz"])}

        def terminate(self):
            state.terminated = True

    monkeypatch.setattr(hornlab_metal_bem, "MetalEngineWorker", Worker, raising=False)
    mesh = (Path(__file__).resolve().parents[1] / "solver" / "warmup_mesh.msh").read_text()
    context = SolverContext(None, (500, 1000), 2, quadrants=1234,
                            polar_config={"enabled_axes": ["horizontal", "vertical"],
                                          "angle_range": [0, 180, 5], "distance": 2,
                                          "observation_origin": "mouth", "spherical_theta_count": 3,
                                          "spherical_phi_count": 4})
    compiled = build_parametric_request(mesh, context, engine_id="beat-metal", backend="metal",
                                       surface_traces=True)
    config = SimpleNamespace(complex_k_shift=.005, observation=SimpleNamespace(distance_m=2., origin="mouth"))
    published = []
    result = solve_worker(compiled, channel_id="source", context=context, config=config,
                          progress=lambda *args: state.progress.append(args), cancel=lambda: None,
                          publish=lambda index, response: published.append((index, response)))
    assert state.terminated
    assert len(state.progress) == len(published) == 2
    assert [index for index, _ in published] == [0, 1]
    assert all(response["metadata"]["provisional"] for _, response in published)
    assert all(point[1] == pytest.approx(0.)
               for _, response in published for plane in response["directivity"].values()
               for row in plane for point in row)
    assert result.pressure_complex.shape == (2, 2, 5)
    assert result.surface_pressure_complex is not None and result.surface_neumann_complex is not None
    assert result.sphere_pressure_complex.shape == (2, 12)
    assert np.isfinite(result.radiated_power_surface_w).all()
    assert np.isfinite(result.radiated_power_sphere_w).all()
    assert result.radiated_power_sphere_coverage_sr == pytest.approx(4 * np.pi)
    assert result.native_runtime["source"]["source_sha256"] == "a" * 64
    assert result.native_runtime["helper"]["sha256"] == "b" * 64
    assert result.native_runtime["metal_library"]["sha256"] == "c" * 64
    assert all(row["native_diagnostics"]["mesh_resolution_suspect"] for row in result.solver_log)
    assert all(row["lapack_info"] == 0 for row in result.solver_log)

    class Cancelled(RuntimeError):
        pass

    def cancel():
        raise Cancelled("stop")

    state.terminated = False
    with pytest.raises(Cancelled, match="stop"):
        solve_worker(compiled, channel_id="source", context=context, config=config,
                     progress=lambda *_: None, cancel=cancel, publish=None)
    assert state.terminated

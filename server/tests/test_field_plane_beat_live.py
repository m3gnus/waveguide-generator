"""Opt-in BEAT polar/retained-field agreement on real coarse horn meshes."""

from __future__ import annotations

import asyncio
from datetime import datetime
import os
from pathlib import Path

import numpy as np
import pytest

from server.app import create_app
from server.jobs.models import FieldPlaneRequest, SolveRequest
from server.mesh.builder import build_solver_mesh
from server.solver import beat, beat_field, beat_imported
from server.solver.beat_adapter.mesh import read_surface
from server.solver.beat_adapter.request import build_imported_request, build_parametric_request
from server.solver.beat_runtime import readiness
from server.solver.beat_runtime.manager import WorkerManager
from server.solver.context import SolverContext
from server.solver.field_traces_store import BEAT_FIELD_TRACE_BACKEND
from server.tests import test_imported_beat as cad
from server.tests.test_field_plane import _body, _request
from server.tests.test_field_traces_store import _job


pytestmark = [pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1 for real BEAT field qualification")]


@pytest.fixture
def workers(monkeypatch):
    pytest.importorskip("beat_engine")
    status = readiness.backend_status("cpu")
    if not status["available"]:
        pytest.skip(status["reason"])
    # Own and reap only these workers; no detached host remains after the test.
    manager = WorkerManager(mode="child")
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setattr(beat_field, "get_manager", lambda: manager)
    try:
        yield manager
    finally:
        manager.shutdown()


def _parametric_request(quadrants):
    return SolveRequest.model_validate(dict(
        design=dict(formula="OSSE", L=60, a=30, a0=10, r0=10, k=1, n=4, q=.99, s=.8,
                    mesh=dict(angular_segments=12, length_segments=4, throat_resolution=8,
                              mouth_resolution=15, quadrants=quadrants, wall_thickness=2),
                    source=dict(shape=2, velocity=1),
                    simulation=dict(sim_type="freestanding", solver_mode="full_3d")),
        options=dict(engine="beat-cpu", frequencies_hz=[500., 1200.],
                     polar_config=dict(angle_range=[-60., 60., 7],
                                       enabled_axes=["horizontal", "vertical", "diagonal"],
                                       spherical_theta_count=5, spherical_phi_count=8, field_plane=True))))


def _mesh(request):
    return asyncio.run(build_solver_mesh(request.design, request.options, lambda: None,
                                        lambda *_args: None))["msh_text"]


def _compare_finished_result(tmp_path, result, native, layout, solve_request, *, imported=False):
    artifact = result.pop("_field_traces")
    result.pop("_field_trace_unavailable_reason", None)
    result.pop("_channel_bases_npz", None)
    assert artifact.backend == BEAT_FIELD_TRACE_BACKEND
    points = np.concatenate([layout.points_m[plane] for plane in native.observation_planes])
    expected = native.pressure_complex.reshape(len(native.frequencies_hz), -1)

    async def scenario():
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        job = _job("real-beat", completed_at=datetime.now().isoformat())
        job.update(config_json=solve_request.model_dump(mode="json"), has_results=True,
                   task_metadata={"solve_path": "full-3d"})
        store.create_job(job)
        store.store_results("real-beat", result)
        store.store_field_traces("real-beat", artifact)
        service = app.state.jobs_runtime.field_plane_service
        row = store.get_job_row("real-beat")
        errors = []
        try:
            for index, frequency in enumerate(native.frequencies_hz):
                body = _body(f"polar-{index}", frequency_index=index,
                             response_id="channel:left" if imported else "channel:default")
                request = FieldPlaneRequest.model_validate(body)
                evaluation = await asyncio.to_thread(service._evaluate_sync, "real-beat", row, request, points)
                actual = evaluation.pressure
                assert np.isfinite(actual).all() and np.all(np.abs(expected[index]) > 0)
                spl_error = np.abs(20 * np.log10(np.abs(actual) / 20e-6) - native.spl_db[index].reshape(-1))
                phase_error = np.abs(np.angle(actual / expected[index], deg=True))
                assert np.all(spl_error < .5), spl_error
                assert np.all(phase_error < 3.), phase_error
                errors.append((float(frequency), float(np.max(spl_error)), float(np.max(phase_error))))
                # Compare images across both sides, including diagonal points,
                # independently of the polar arc's symmetric range.
                for axis in ((0, 1) if artifact.symmetry_plane == "yz+xz" else
                             (0,) if artifact.symmetry_plane == "yz" else ()):
                    mirrored = points.copy()
                    mirrored[:, axis] *= -1
                    image = await asyncio.to_thread(service._evaluate_sync, "real-beat", row, request, mirrored)
                    np.testing.assert_allclose(image.pressure, actual, rtol=2e-4, atol=1e-8)
                # Exercise the actual HTTP plane endpoint against this retained
                # artifact as well as evaluating the exact polar points above.
                body["plane"].update(origin_m=points[0].tolist(), width_m=.05, height_m=.05, nx=2, ny=2)
                status, _, _ = await _request(app, "/api/results/real-beat/field-plane", body)
                assert status == 200
        finally:
            await app.state.jobs_runtime.shutdown()
        return errors

    errors = asyncio.run(scenario())
    print(f"BEAT field agreement: symmetry={artifact.symmetry_plane}, imported={imported}, "
          f"triangles={len(read_surface(artifact.mesh_text).faces)}, points_per_frequency={len(points)}, "
          f"(Hz, max dB, max degrees)={errors}")


@pytest.mark.parametrize("quadrants", [1234, 14, 1])
def test_parametric_polar_arc_matches_persisted_field_at_every_point(workers, tmp_path, quadrants):
    request = _parametric_request(quadrants)
    mesh = _mesh(request)
    context = SolverContext.from_request(request, solver_mode="full_3d")
    compiled = build_parametric_request(mesh, context, backend="cpu", surface_traces=True)
    captured = []
    result = beat.solve_beat_from_msh_text(mesh, context, backend="cpu", _worker_manager=workers,
                                         _native_result_callback=lambda _channel, native: captured.append(native))
    _compare_finished_result(tmp_path, result, captured[0], compiled.layout, request)


def test_imported_rotated_translated_cad_frame_matches_persisted_field(workers, tmp_path):
    mesh = _mesh(_parametric_request(1234))
    surface = read_surface(mesh)
    rotation = np.asarray([[0., 0., 1.], [1., 0., 0.], [0., 1., 0.]])
    origin = np.asarray([.15, -.07, .04])
    points = surface.points_m @ rotation.T + origin
    lines = mesh.splitlines()
    start = lines.index("$Nodes") + 2
    for index, (node, point) in enumerate(zip(surface.node_ids, points, strict=True)):
        lines[start + index] = f"{node} " + " ".join(format(float(value), ".17g") for value in point)
    start = lines.index("$Elements") + 2
    for index in range(start, lines.index("$EndElements")):
        row = lines[index].split()
        if row[1] == "2" and row[3] == "2":
            row[3] = "101"
            lines[index] = " ".join(row)
    mesh = "\n".join(lines) + "\n"
    frame = dict(axis=rotation[:, 2].tolist(), u=rotation[:, 0].tolist(), v=rotation[:, 1].tolist(),
                 origin_m=origin.tolist(), mouth_center_m=origin.tolist(), source_center_m=origin.tolist())
    record = cad._record(frame=frame, msh_text=mesh)
    record["sources"] = record["sources"][:1]
    record["source_tags"] = {"source-a": 101}
    record["tag_map"] = {key: value for key, value in record["tag_map"].items() if key in {"1", "101"}}
    request = cad._request(drive_channels=[dict(id="left", source_ids=["source-a"])],
                           mesh=dict(rigid_size_mm=8., transition_mm=20., source_size_mm={"source-a": 3.}))
    request.options.frequencies_hz = [500., 1200.]
    request.options.polar_config = _parametric_request(1234).options.polar_config
    context = SolverContext.from_imported_request(request, quadrants=1234, source_motion="normal")
    compiled = build_imported_request(mesh, context, record, request.geometry.drive_channels,
                                      backend="cpu", surface_traces=True)
    captured = []
    result = beat_imported.solve_imported_beat_from_msh_text(
        mesh, request, record, backend="cpu", _worker_manager=workers,
        _native_result_callback=lambda _channel, native: captured.append(native))
    _compare_finished_result(tmp_path, result, captured[0], compiled.layout, request, imported=True)

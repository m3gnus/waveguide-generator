"""BEAT field dispatch and wire contracts, without Julia."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from server.app import create_app
from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.jobs.models import FieldPlaneRequest
from server.platform import temp_session
from server.solver import beat_field, field_plane
from server.solver.beat_runtime.manager import ManagedWorker
from server.solver.field_traces_store import ArtifactCorrupt, BEAT_FIELD_TRACE_BACKEND
from server.tests.test_field_plane import MESH_TEXT, _artifact, _body, _create_job, _decode, _request


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    monkeypatch.setattr(beat_field, "field_status", lambda: dict(available=True, backend="cpu"))
    state = SimpleNamespace(requests=[], arrays=[], operations=[], negotiated=[], mutate=None,
                            failed=False, incomplete=False, duplicate=False, cancelled=False,
                            block=False, entered=threading.Event(), release=threading.Event(),
                            retirements=0, start_hook=None, probe_clears=[])
    monkeypatch.setattr(beat_field.readiness, "probe_cache_clear",
                        lambda **kwargs: state.probe_clears.append(kwargs))

    class Stream:
        def __init__(self, iterator):
            self.iterator = iterator

        def __next__(self):
            return next(self.iterator)

        def close(self):
            self.iterator.close()

    class Worker:
        worker_info = {"type": "ready"}

        def ensure_started(self):
            if state.start_hook is not None:
                state.start_hook()

        def submit(self, path, *, operation, status_callback=None):
            wire = json.loads(path.read_text())
            state.requests.append(wire)
            state.operations.append(operation)
            arrays = {}
            for name, descriptor in wire["binary_arrays"].items():
                assert descriptor["offset"] == 0
                assert descriptor["order"] == "C" and descriptor["byte_order"] == "little"
                raw = (path.parent / descriptor["file"]).read_bytes()
                assert len(raw) == descriptor["nbytes"]
                arrays[name] = np.frombuffer(raw, dtype=np.dtype(descriptor["dtype"]).newbyteorder("<")).reshape(descriptor["shape"])
            state.arrays.append(arrays)

            def events():
                if state.block:
                    state.entered.set()
                    assert state.release.wait(2)
                    if state.retirements:
                        raise OSError("field worker terminated")
                if state.cancelled:
                    while not Path(wire["cancel_path"]).exists():
                        # The session's cancellation monitor owns the marker.
                        import time
                        time.sleep(.005)
                    yield dict(type="cancelled")
                    return
                if state.failed:
                    yield dict(type="failed", error="field kernel failed")
                    return
                values = np.full(len(arrays["points_m"]), arrays["boundary_pressure"][0], dtype="<c8")
                (path.parent / "field.bin").write_bytes(values.tobytes())
                descriptor = dict(file="field.bin", offset=0, nbytes=values.nbytes,
                                  dtype="complex64", shape=[len(values)], order="C", byte_order="little")
                event = dict(type="field_result", phasor_convention=wire["phasor_convention"], values_binary=descriptor)
                if state.mutate:
                    state.mutate(event)
                yield event
                if state.duplicate:
                    yield event
                if not state.incomplete:
                    yield dict(type="completed")

            return Stream(events())

        def terminate(self):
            state.retirements += 1
            state.release.set()

    def negotiate(info, request, operation):
        state.negotiated.append(operation)
        assert info == Worker.worker_info
        assert request["binary_array_schema_version"] == 1
        assert request["phasor_convention"] == SOLVER_TIME_CONVENTION

    contract = SimpleNamespace(negotiate_submission=negotiate)
    original_import = beat_field.importlib.import_module
    monkeypatch.setattr(beat_field.importlib, "import_module", lambda name:
                        contract if name == "beat_engine.beat_contract.worker" else original_import(name))
    client = ManagedWorker(Worker(), "child")
    state.client = client
    monkeypatch.setattr(beat_field, "get_manager", lambda: SimpleNamespace(get_worker=lambda backend: client))
    yield state
    client.close(detach=False)


@pytest.mark.parametrize("multi,response", [(False, "channel:default"), (True, "channel:left"),
                                            (True, "member:right"), (True, "system")])
def test_finished_beat_artifact_returns_pressure_over_http(runtime, tmp_path, multi, response):
    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "beat-field", backend=BEAT_FIELD_TRACE_BACKEND, multi=multi)
        try:
            body = _body(response_id=response)
            selected = FieldPlaneRequest.model_validate(body)
            row = store.get_job_row("beat-field")
            expected = app.state.jobs_runtime.field_plane_service._load_response_traces("beat-field", row, selected)
            status, raw, _ = await _request(app, "/api/results/beat-field/field-plane", body)
            assert status == 200
            header, pressure = _decode(raw)
            assert header["pressure_unit"] == "Pa"
            assert header["phase_convention"] == "solver_exp_plus_ikr"
            assert header["symmetry_plane"] == "yz"
            np.testing.assert_allclose(pressure, expected[4][0], rtol=1e-6)
            np.testing.assert_allclose(runtime.arrays[0]["boundary_pressure"], expected[4], rtol=1e-6)
            np.testing.assert_allclose(runtime.arrays[0]["boundary_normal_derivative"], expected[5], rtol=1e-6)
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
    assert runtime.operations == runtime.negotiated == ["bem_field"]
    wire = runtime.requests[0]
    assert wire["quadrature_order"] == 4
    assert wire["symmetry"] == "x"
    assert 2 * np.pi * wire["frequency_hz"] / wire["sound_speed_m_per_s"] == pytest.approx(18.3)
    assert not list(tmp_path.glob("wg2-beat-solve-*"))


@pytest.mark.parametrize("plane,mode", [(None, "off"), ("yz", "x"), ("yz+xz", "xy"), ("xz", "x")])
def test_image_frame_preserves_trace_order_and_rotates_points_together(runtime, tmp_path, plane, mode):
    path = tmp_path / "mesh.msh"
    # Deliberately unsorted node IDs expose accidental DOF sorting.
    path.write_text(MESH_TEXT.replace("1 0 0 0", "41 0 0 0").replace("1 2 3", "41 2 3").replace("1 3 4", "41 3 4"))
    mesh = beat_field.load_mesh(path, native_symmetry_plane=plane)
    artifact = _artifact()
    points = np.asarray([[2., 3., 4.]])
    beat_field.evaluate_exterior_from_traces(mesh, 1000., 18.3, artifact.channels[0].pressure_p1[0],
                                           artifact.channels[0].neumann_dp0[0], points, symmetry_plane=plane)
    assert runtime.requests[0]["symmetry"] == mode
    arrays = runtime.arrays[0]
    expected_points = [[3., -2., 4.]] if plane == "xz" else points
    np.testing.assert_array_equal(arrays["points_m"], expected_points)
    expected_vertices = mesh.points_m[:, [1, 0, 2]] * [1, -1, 1] if plane == "xz" else mesh.points_m
    np.testing.assert_array_equal(arrays["boundary_points_m"], expected_vertices)
    np.testing.assert_array_equal(arrays["boundary_triangles"], [[0, 1, 2], [0, 2, 3]])
    np.testing.assert_array_equal(arrays["boundary_pressure"], artifact.channels[0].pressure_p1[0])


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
def test_fields_use_a_proved_runtime_on_every_supported_host(monkeypatch, backend):
    calls = []

    def status(selected):
        calls.append(selected)
        return dict(available=selected == backend, backend=selected, reason="not provisioned")

    monkeypatch.setattr(beat_field.readiness, "backend_status", status)
    assert field_plane._field_backend_status(BEAT_FIELD_TRACE_BACKEND)["backend"] == backend
    api = field_plane._load_field_backend(BEAT_FIELD_TRACE_BACKEND)
    assert api.load_mesh is beat_field.load_mesh
    assert api.evaluate_exterior_from_traces is beat_field.evaluate_exterior_from_traces
    assert all(name in {"cpu", "metal", "cuda", "rocm"} for name in calls)


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
def test_field_operation_uses_the_available_device(runtime, monkeypatch, tmp_path, backend):
    monkeypatch.setattr(beat_field, "field_status", lambda: dict(available=True, backend=backend))
    path = tmp_path / "mesh.msh"
    path.write_text(MESH_TEXT)
    artifact = _artifact()
    beat_field.evaluate_exterior_from_traces(beat_field.load_mesh(path), 1000., 18.3,
                                           artifact.channels[0].pressure_p1[0], artifact.channels[0].neumann_dp0[0],
                                           [[2., 3., 4.]])
    assert runtime.requests[0]["bem_backend"] == backend
    assert runtime.requests[0]["phasor_convention"] == SOLVER_TIME_CONVENTION


def test_beat_unavailable_is_unsupported_with_reason_not_corrupt(monkeypatch, tmp_path):
    monkeypatch.setattr(beat_field.readiness, "backend_status", lambda backend:
                        dict(available=False, backend=backend, reason="Julia runtime not provisioned"))

    async def scenario():
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "unavailable", backend=BEAT_FIELD_TRACE_BACKEND)
        try:
            status, raw, _ = await _request(app, "/api/results/unavailable/field-plane", _body())
            assert status == 422
            assert "Julia runtime not provisioned" in json.loads(raw)["detail"]
            assert "requires backend 'beat'" in json.loads(raw)["detail"]
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["phase", "path", "shape", "truncated", "nonfinite", "failed", "incomplete", "duplicate"])
def test_invalid_field_results_do_not_return_pressure(runtime, tmp_path, failure):
    def mutate(event):
        descriptor = event["values_binary"]
        if failure == "phase":
            event["phasor_convention"] = "exp(+i omega t)"
        elif failure == "path":
            descriptor["file"] = "../field.bin"
        elif failure == "shape":
            descriptor["shape"] = [2]
        elif failure in {"truncated", "nonfinite"}:
            directory = next(tmp_path.glob("wg2-beat-solve-*"))
            (directory / "field.bin").write_bytes(b"" if failure == "truncated" else np.asarray([np.nan], dtype="<c8").tobytes())

    runtime.mutate = mutate
    runtime.failed = failure == "failed"
    runtime.incomplete = failure == "incomplete"
    runtime.duplicate = failure == "duplicate"
    path = tmp_path / "mesh.msh"
    path.write_text(MESH_TEXT)
    artifact = _artifact()
    with pytest.raises(field_plane.FieldPlaneEvaluationFailed, match="BEAT field evaluation failed"):
        beat_field.evaluate_exterior_from_traces(beat_field.load_mesh(path), 1000., 18.3,
                                               artifact.channels[0].pressure_p1[0], artifact.channels[0].neumann_dp0[0],
                                               [[2., 3., 4.]])
    assert not list(tmp_path.glob("wg2-beat-solve-*"))


def test_trace_topology_mismatch_is_corrupt(runtime, tmp_path):
    path = tmp_path / "mesh.msh"
    path.write_text(MESH_TEXT)
    with pytest.raises(ArtifactCorrupt, match="align with mesh"):
        beat_field.evaluate_exterior_from_traces(beat_field.load_mesh(path), 1000., 18.3, [1j], [1j], [[2., 3., 4.]])
    assert not runtime.requests


def test_timeout_cancels_only_the_owned_beat_field_session(runtime, tmp_path):
    runtime.cancelled = True

    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "timeout", backend=BEAT_FIELD_TRACE_BACKEND)
        service = app.state.jobs_runtime.field_plane_service
        service.timeout_seconds = .05
        try:
            status, _, _ = await _request(app, "/api/results/timeout/field-plane", _body())
            assert status == 504
            if service._background_tasks:
                await asyncio.wait_for(asyncio.gather(*service._background_tasks), timeout=2)
            assert not app.state.jobs_runtime.metal_permit.field_running
            assert not list(tmp_path.glob("wg2-beat-solve-*"))
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
    assert runtime.probe_clears == []


@pytest.mark.parametrize("stage", ["admission", "startup"])
def test_deadline_before_submission_preserves_probe_cache(runtime, monkeypatch, tmp_path, stage):
    # Force the reviewed race: only submit's synchronous checks observe the
    # deadline, before the monitor can set the session's cancellation flag.
    monkeypatch.setattr(beat_field.SolveSession, "_start_monitor", lambda self: None)
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(field_plane, "time", SimpleNamespace(monotonic=lambda: clock.now))
    owner = runtime.client.acquire(lambda: None) if stage == "admission" else None
    if stage == "admission":
        acquire = runtime.client.acquire

        def expire_then_acquire(check):
            clock.now = 2.
            return acquire(check)
        monkeypatch.setattr(runtime.client, "acquire", expire_then_acquire)
    else:
        def start():
            clock.now = 2.
        runtime.start_hook = start

    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "deadline", backend=BEAT_FIELD_TRACE_BACKEND)
        service = app.state.jobs_runtime.field_plane_service
        service.timeout_seconds = 1.
        try:
            status, _, _ = await _request(app, "/api/results/deadline/field-plane", _body())
            assert status == 504
            await asyncio.wait_for(asyncio.gather(*service._background_tasks), timeout=2)
            assert not app.state.jobs_runtime.metal_permit.field_running
        finally:
            runtime.release.set()
            await app.state.jobs_runtime.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        if owner is not None:
            owner.close()
    assert runtime.requests == runtime.probe_clears == []
    assert runtime.retirements == 0
    assert not list(tmp_path.glob("wg2-beat-solve-*"))


@pytest.mark.parametrize("expires", [False, True])
def test_superseded_field_drains_warm_worker_but_keeps_deadline(runtime, tmp_path, expires):
    runtime.block = True

    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "drag", backend=BEAT_FIELD_TRACE_BACKEND)
        service = app.state.jobs_runtime.field_plane_service
        service.timeout_seconds = .1 if expires else 2.
        first = asyncio.create_task(service.evaluate("drag", FieldPlaneRequest.model_validate(_body("first"))))
        try:
            assert await asyncio.to_thread(runtime.entered.wait, 1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert app.state.jobs_runtime.metal_permit.field_running
            if expires:
                await asyncio.wait_for(asyncio.gather(*service._background_tasks), timeout=2)
                assert runtime.retirements == 1
            else:
                newest = asyncio.create_task(service.evaluate("drag", FieldPlaneRequest.model_validate(_body("newest"))))
                while not app.state.jobs_runtime.metal_permit.field_pending:
                    await asyncio.sleep(0)
                # Outlast the old cancellation grace to prove it is drained.
                await asyncio.sleep(.35)
                assert runtime.retirements == 0
                runtime.release.set()
                evaluated = await asyncio.wait_for(newest, timeout=2)
                assert np.isfinite(evaluated.pressure).all()
                await asyncio.gather(*service._background_tasks)
                assert runtime.operations == ["bem_field", "bem_field"]
            assert runtime.probe_clears == []
            assert not app.state.jobs_runtime.metal_permit.field_running
            assert not list(tmp_path.glob("wg2-beat-solve-*"))
        finally:
            runtime.release.set()
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["failed", "nonfinite", "startup"])
def test_evaluation_failures_return_500_not_unavailable(runtime, tmp_path, failure):
    runtime.failed = failure == "failed"
    if failure == "nonfinite":
        def mutate(event):
            directory = next(tmp_path.glob("wg2-beat-solve-*"))
            (directory / "field.bin").write_bytes(np.full(event["values_binary"]["shape"], np.nan, dtype="<c8").tobytes())
        runtime.mutate = mutate
    if failure == "startup":
        def start():
            raise OSError("worker connection failed")
        runtime.start_hook = start

    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "failed", backend=BEAT_FIELD_TRACE_BACKEND)
        try:
            status, raw, _ = await _request(app, "/api/results/failed/field-plane", _body())
            assert status == 500
            detail = json.loads(raw)["detail"]
            assert "BEAT field evaluation failed" in detail
            assert "unavailable" not in detail
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_missing_contract_is_unavailable(runtime, monkeypatch, tmp_path):
    def missing(name):
        raise ImportError("BEAT worker contract missing")
    monkeypatch.setattr(beat_field.importlib, "import_module", missing)
    path = tmp_path / "mesh.msh"
    path.write_text(MESH_TEXT)
    artifact = _artifact()
    with pytest.raises(field_plane.FieldPlaneUnsupported, match="runtime is unavailable.*contract missing"):
        beat_field.evaluate_exterior_from_traces(beat_field.load_mesh(path), 1000., 18.3,
                                               artifact.channels[0].pressure_p1[0], artifact.channels[0].neumann_dp0[0],
                                               [[2., 3., 4.]])


@pytest.mark.parametrize("plane", ["yz", "xz", "xy"])
def test_ground_plane_is_explicitly_unsupported(runtime, tmp_path, plane):
    path = tmp_path / "mesh.msh"
    path.write_text(MESH_TEXT)
    artifact = _artifact()
    with pytest.raises(field_plane.FieldPlaneUnsupported, match="rigid ground plane") as failure:
        beat_field.evaluate_exterior_from_traces(beat_field.load_mesh(path), 1000., 18.3,
                                               artifact.channels[0].pressure_p1[0], artifact.channels[0].neumann_dp0[0],
                                               [[2., 3., 4.]], ground_plane=plane)
    assert failure.value.code == "unsupported_ground_plane"
    assert runtime.requests == []


@pytest.mark.parametrize("multi", [False, True])
def test_retained_ground_plane_job_cannot_silently_evaluate_free_field(runtime, monkeypatch, tmp_path, multi):
    async def scenario():
        app = create_app(data_dir=tmp_path / "data")
        store = app.state.jobs_runtime.store
        store.initialize()
        create_job = store.create_job

        def with_ground(job):
            job["config_json"]["options"] = dict(ground_plane=dict(enabled=True, axis="y", height_m=1.))
            return create_job(job)
        monkeypatch.setattr(store, "create_job", with_ground)
        _create_job(store, "ground", backend=BEAT_FIELD_TRACE_BACKEND, multi=multi)
        try:
            status, raw, _ = await _request(app, "/api/results/ground/field-plane",
                                          _body(response_id="channel:left" if multi else "channel:default"))
            assert status == 422
            payload = json.loads(raw)
            assert payload["code"] == "unsupported_ground_plane"
            assert "rigid ground plane" in payload["message"]
            assert payload["remedy"] == "Turn the ground plane off and re-solve."
            assert runtime.requests == []
        finally:
            await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())

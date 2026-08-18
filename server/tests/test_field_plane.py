from __future__ import annotations

import asyncio
import json
from pathlib import Path
import struct
import threading
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from server.app import create_app
from server.jobs.models import FieldPlaneRequest
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.mesh.artifact import mesh_text_sha256
from server.solver import field_plane
from server.solver.combine import raw_channel_weights
from server.solver.field_traces_store import (
    BEMPP_FIELD_TRACE_BACKEND,
    FieldTraceArtifact,
    FieldTraceChannel,
    METAL_FIELD_TRACE_BACKEND,
)


MESH_TEXT = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
4
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
$EndNodes
$Elements
2
1 2 2 1 1 1 2 3
2 2 2 1 1 1 3 4
$EndElements
"""

TETRAHEDRON_MESH_TEXT = """$MeshFormat
2.2 0 8
$EndMeshFormat
$PhysicalNames
2
2 1 "wall"
2 2 "source"
$EndPhysicalNames
$Nodes
4
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
$EndNodes
$Elements
4
1 2 2 2 2 1 3 2
2 2 2 1 1 1 2 4
3 2 2 1 1 1 4 3
4 2 2 1 1 2 3 4
$EndElements
"""


async def _request(
    app: Any,
    path: str,
    body: dict[str, Any],
) -> tuple[int, bytes, dict[str, str]]:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps(body).encode("utf-8"),
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"127.0.0.1:3100"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in start.get("headers", [])
    }
    return int(start["status"]), response, headers


def _body(
    request_id: str = "request-1",
    *,
    response_id: str = "channel:default",
    frequency_index: int = 0,
) -> dict[str, Any]:
    return {
        "version": 1,
        "request_id": request_id,
        "plane": {
            "origin_m": [0.0, 0.0, 0.0],
            "axis_u": [1.0, 0.0, 0.0],
            "axis_v": [0.0, 1.0, 0.0],
            "width_m": 2.0,
            "height_m": 2.0,
            "nx": 3,
            "ny": 2,
        },
        "frequency_index": frequency_index,
        "response": {"id": response_id},
    }


def _artifact(
    *,
    multi: bool = False,
    backend: str = METAL_FIELD_TRACE_BACKEND,
) -> FieldTraceArtifact:
    pressure = np.asarray([[1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j]])
    neumann = np.asarray([[9 + 10j, 11 + 12j]])
    channels = [FieldTraceChannel("default", pressure, neumann)]
    if multi:
        channels = [
            FieldTraceChannel("left", pressure, neumann),
            FieldTraceChannel("right", pressure * (2 - 1j), neumann * (2 - 1j)),
        ]
    return FieldTraceArtifact(
        mesh_text=MESH_TEXT,
        frequencies_hz=np.asarray([1000.0]),
        k_real=np.asarray([18.3]),
        k_imag=np.asarray([0.0]),
        symmetry_plane="yz",
        solve_path="full-3d",
        channels=tuple(channels),
        backend=backend,
    )


def _create_job(
    store: JobStore,
    job_id: str,
    *,
    status: str = "complete",
    traces: bool = True,
    multi: bool = False,
    solve_path: str = "full-3d",
    backend: str = METAL_FIELD_TRACE_BACKEND,
) -> None:
    now = "2026-08-18T00:00:00"
    geometry: dict[str, Any]
    if multi:
        geometry = {
            "type": "imported",
            "drive_channels": [
                {"id": "left", "source_ids": ["source-left"]},
                {"id": "right", "source_ids": ["source-right"]},
            ],
        }
    else:
        geometry = {"type": "parametric"}
    store.create_job(
        {
            "id": job_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "started_at": now if status != "queued" else None,
            "completed_at": now if status == "complete" else None,
            "progress": 1.0 if status == "complete" else 0.5,
            "stage": status,
            "stage_message": status,
            "config_json": {"geometry": geometry},
            "config_summary_json": {},
            "has_results": status == "complete",
            "task_metadata": {
                "solve_path": solve_path,
                "field_plane_available": traces,
                "unavailable_reason": (
                    None
                    if traces
                    else (
                        "unsupported_solve_mode"
                        if solve_path != "full-3d"
                        else "solve_predates_traces"
                    )
                ),
            },
        }
    )
    if status == "complete":
        results: dict[str, Any] = {"metadata": {"solve_path": solve_path}}
        if multi:
            results.update(
                {
                    "channel_order": ["left", "right", "combined"],
                    "channels": {
                        "left": {"metadata": {}},
                        "right": {"metadata": {}},
                        "combined": {
                            "metadata": {
                                "combine": {
                                    "type": "lr4_time_aligned_sum",
                                    "members": ["left", "right"],
                                    "crossovers_hz": [1000.0],
                                    "level_match": {
                                        "enabled": True,
                                        "gains_db": {"left": -1.0, "right": 2.0},
                                    },
                                    "delays_ms": {"left": 0.25, "right": 0.0},
                                }
                            }
                        },
                    },
                }
            )
        store.store_results(job_id, results)
    if traces:
        store.store_field_traces(
            job_id,
            _artifact(multi=multi, backend=backend),
        )


def _mock_field_backend(
    monkeypatch: pytest.MonkeyPatch,
    evaluator: Any,
    *,
    mesh_loader: Any | None = None,
) -> None:
    loader = mesh_loader or (lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        field_plane,
        "_load_field_backend",
        lambda _backend: field_plane._FieldBackendAPI(loader, evaluator),
    )


def _decode(payload: bytes) -> tuple[dict[str, Any], np.ndarray]:
    header_length = struct.unpack_from("<I", payload)[0]
    header_end = 4 + header_length
    header = json.loads(payload[4:header_end])
    pairs = np.frombuffer(payload[header_end:], dtype="<f4").reshape(-1, 2)
    pressure = pairs[:, 0] + 1j * pairs[:, 1]
    return header, pressure


def test_happy_path_binary_round_trip_ordering_and_mesh_lru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mesh_loads: list[Path] = []
    points_seen: list[np.ndarray] = []

    def evaluate(
        _mesh: object,
        _frequency_hz: float,
        _k_real: float,
        _pressure: np.ndarray,
        _neumann: np.ndarray,
        points: np.ndarray,
        **_kwargs: Any,
    ) -> np.ndarray:
        points_seen.append(points.copy())
        return points[:, 0] + 1j * points[:, 1]

    _mock_field_backend(
        monkeypatch,
        evaluate,
        mesh_loader=lambda path, **_kwargs: mesh_loads.append(Path(path)) or object(),
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "happy")

        for request_id in ("first", "second"):
            status, raw, headers = await _request(
                app,
                "/api/results/happy/field-plane",
                _body(request_id),
            )
            assert status == 200
            assert headers["content-type"] == "application/octet-stream"
            assert headers["cache-control"] == "no-store"
            header, pressure = _decode(raw)
            assert header == {
                "version": field_plane.FIELD_PLANE_RESPONSE_VERSION,
                "request_id": request_id,
                "job_id": "happy",
                "frequency_index": 0,
                "frequency_hz": 1000.0,
                "nx": 3,
                "ny": 2,
                "ordering": "v-major-row-major",
                "phase_convention": "solver_exp_plus_ikr",
                "pressure_unit": "Pa",
                "response_id": "channel:default",
                "geometry_sha256": mesh_text_sha256(MESH_TEXT),
                "synthesis_revision": field_plane.NO_SYNTHESIS_REVISION,
            }
            np.testing.assert_array_equal(
                pressure,
                np.asarray([-1 - 1j, -1j, 1 - 1j, -1 + 1j, 1j, 1 + 1j]),
            )
        assert len(mesh_loads) == 1
        np.testing.assert_array_equal(
            points_seen[0],
            np.asarray(
                [
                    [-1.0, -1.0, 0.0],
                    [0.0, -1.0, 0.0],
                    [1.0, -1.0, 0.0],
                    [-1.0, 1.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [1.0, 1.0, 0.0],
                ]
            ),
        )
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_artifact_backend_dispatches_matching_mesh_loader_and_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def package(backend: str) -> SimpleNamespace:
        def load(path: Path, **kwargs: Any) -> object:
            calls.append((backend, "load", dict(kwargs)))
            assert Path(path).name == "mesh.msh"
            return object()

        def evaluate(*args: Any, **kwargs: Any) -> np.ndarray:
            calls.append((backend, "evaluate", dict(kwargs)))
            return np.zeros(np.asarray(args[5]).shape[0], dtype=np.complex128)

        return SimpleNamespace(
            load_mesh=load,
            evaluate_exterior_from_traces=evaluate,
        )

    packages = {
        "hornlab_metal_bem": package(METAL_FIELD_TRACE_BACKEND),
        "hornlab_bempp_bem": package(BEMPP_FIELD_TRACE_BACKEND),
    }
    monkeypatch.setattr(
        field_plane,
        "_field_backend_status",
        lambda _backend: {"available": True, "reason": "test"},
    )
    monkeypatch.setattr(
        field_plane.importlib,
        "import_module",
        lambda name: packages[name],
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        for job_id, backend in (
            ("metal-dispatch", METAL_FIELD_TRACE_BACKEND),
            ("bempp-dispatch", BEMPP_FIELD_TRACE_BACKEND),
        ):
            _create_job(store, job_id, backend=backend)
            status, _raw, _headers = await _request(
                app,
                f"/api/results/{job_id}/field-plane",
                _body(job_id),
            )
            assert status == 200
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())

    assert [call[:2] for call in calls] == [
        (METAL_FIELD_TRACE_BACKEND, "load"),
        (METAL_FIELD_TRACE_BACKEND, "evaluate"),
        (BEMPP_FIELD_TRACE_BACKEND, "load"),
        (BEMPP_FIELD_TRACE_BACKEND, "evaluate"),
    ]
    metal_kwargs = calls[1][2]
    bempp_kwargs = calls[3][2]
    assert metal_kwargs == {"symmetry_plane": "yz", "check_open_edges": True}
    assert bempp_kwargs == {"symmetry_plane": "yz"}


def test_artifact_backend_unavailable_returns_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        field_plane,
        "_field_backend_status",
        lambda backend: {
            "available": False,
            "reason": f"{backend} runtime missing",
        },
    )
    monkeypatch.setattr(
        field_plane.importlib,
        "import_module",
        lambda _name: pytest.fail("an unavailable backend must not be imported"),
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "bempp-unavailable", backend=BEMPP_FIELD_TRACE_BACKEND)
        status, raw, _headers = await _request(
            app,
            "/api/results/bempp-unavailable/field-plane",
            _body(),
        )
        assert status == 422
        detail = json.loads(raw)["detail"]
        assert "requires backend 'bempp'" in detail
        assert "bempp runtime missing" in detail
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_degraded_assembly_backend_warns_once_not_per_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A field plane assembled on the slow backend must say so, exactly once.

    Same rule the solve path follows: falling back to numba silently is how
    someone spends a week wondering why re-evaluation is slow. But dragging the
    plane re-evaluates continuously, so warning per request would bury the log
    it is trying to write.
    """

    warning = "Falling back to the numba assembly backend because OpenCL is unusable: no device."
    monkeypatch.setattr(
        field_plane,
        "_field_backend_status",
        lambda _backend: {"available": True, "warning": warning},
    )
    monkeypatch.setattr(
        field_plane.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            load_mesh=lambda *_a, **_k: object(),
            evaluate_exterior_from_traces=lambda *_a, **_k: None,
        ),
    )
    monkeypatch.setattr(field_plane, "_WARNED_BACKENDS", set())

    with caplog.at_level("WARNING", logger=field_plane.logger.name):
        for _ in range(3):
            field_plane._load_field_backend(BEMPP_FIELD_TRACE_BACKEND)

    emitted = [r for r in caplog.records if warning in r.getMessage()]
    assert len(emitted) == 1


def test_healthy_assembly_backend_stays_quiet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        field_plane,
        "_field_backend_status",
        lambda _backend: {"available": True, "warning": None},
    )
    monkeypatch.setattr(
        field_plane.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            load_mesh=lambda *_a, **_k: object(),
            evaluate_exterior_from_traces=lambda *_a, **_k: None,
        ),
    )
    monkeypatch.setattr(field_plane, "_WARNED_BACKENDS", set())

    with caplog.at_level("WARNING", logger=field_plane.logger.name):
        field_plane._load_field_backend(BEMPP_FIELD_TRACE_BACKEND)

    assert caplog.records == []


def test_system_response_weights_traces_and_tracks_synthesis_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[np.ndarray, np.ndarray]] = []

    def evaluate(
        _mesh: object,
        _frequency_hz: float,
        _k_real: float,
        pressure: np.ndarray,
        neumann: np.ndarray,
        points: np.ndarray,
        **_kwargs: Any,
    ) -> np.ndarray:
        calls.append((pressure.copy(), neumann.copy()))
        return np.full(points.shape[0], pressure[0], dtype=np.complex128)

    _mock_field_backend(monkeypatch, evaluate)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "multi", multi=True)
        revisions: list[str] = []
        for request_id in ("initial", "identical"):
            status, raw, _headers = await _request(
                app,
                "/api/results/multi/field-plane",
                _body(request_id, response_id="system"),
            )
            assert status == 200
            header, _values = _decode(raw)
            assert header["response_id"] == "system"
            assert isinstance(header["synthesis_revision"], str)
            assert len(header["synthesis_revision"]) == 16
            revisions.append(header["synthesis_revision"])

        assert revisions[0] == revisions[1]

        results = store.get_results("multi")
        assert results is not None
        results["channels"]["combined"]["metadata"]["combine"]["level_match"][
            "gains_db"
        ]["left"] = -2.0
        store.store_results("multi", results)

        status, raw, _headers = await _request(
            app,
            "/api/results/multi/field-plane",
            _body("recombined", response_id="system"),
        )
        assert status == 200
        changed_header, _values = _decode(raw)
        assert changed_header["synthesis_revision"] != revisions[0]
        assert len(calls) == 3

        base = _artifact(multi=True)
        weights = raw_channel_weights(
            np.asarray([1000.0]),
            ["left", "right"],
            [1000.0],
            {"left": -1.0, "right": 2.0},
            {"left": 0.00025, "right": 0.0},
        )
        expected_p = sum(
            weights[channel.channel_id][0] * channel.pressure_p1[0]
            for channel in base.channels
        )
        expected_q = sum(
            weights[channel.channel_id][0] * channel.neumann_dp0[0]
            for channel in base.channels
        )
        np.testing.assert_allclose(calls[0][0], expected_p)
        np.testing.assert_allclose(calls[0][1], expected_q)
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("job_id", "status", "traces", "solve_path", "expected"),
    [
        ("missing", None, False, "full-3d", 404),
        ("incomplete", "error", False, "full-3d", 409),
        ("old", "complete", False, "full-3d", 410),
        ("axisymmetric", "complete", False, "axisymmetric-meridian", 422),
    ],
)
def test_job_and_artifact_error_mappings(
    tmp_path: Path,
    job_id: str,
    status: str | None,
    traces: bool,
    solve_path: str,
    expected: int,
) -> None:
    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        if status is not None:
            _create_job(
                store,
                job_id,
                status=status,
                traces=traces,
                solve_path=solve_path,
            )
        response_status, _raw, _headers = await _request(
            app,
            f"/api/results/{job_id}/field-plane",
            _body(),
        )
        assert response_status == expected
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "plane_update",
    [
        {"nx": 1},
        {"nx": 257},
        {"ny": 257},
        {"width_m": 0.0},
        {"height_m": 101.0},
        {"axis_u": [2.0, 0.0, 0.0]},
        {"axis_v": [1.0, 0.0, 0.0]},
        {"origin_m": [0.0, float("inf"), 0.0]},
    ],
)
def test_plane_limits_and_orthonormality_are_rejected(
    plane_update: dict[str, Any],
) -> None:
    body = _body()
    body["plane"].update(plane_update)
    with pytest.raises(ValueError):
        FieldPlaneRequest.model_validate(body)


@pytest.mark.parametrize(
    ("response_id", "frequency_index"),
    [("channel:unknown", 0), ("channel:default", 1), ("invalid", 0)],
)
def test_invalid_frequency_and_response_map_to_422(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response_id: str,
    frequency_index: int,
) -> None:
    _mock_field_backend(
        monkeypatch,
        lambda *_args, **_kwargs: np.zeros(6, dtype=np.complex128),
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "selection")
        status, _raw, _headers = await _request(
            app,
            "/api/results/selection/field-plane",
            _body(response_id=response_id, frequency_index=frequency_index),
        )
        assert status == 422
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_solve_permit_rejects_field_request_with_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_field_backend(
        monkeypatch,
        lambda *_args, **_kwargs: np.zeros(6, dtype=np.complex128),
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "busy")
        await app.state.jobs_runtime.start()
        lease = await app.state.jobs_runtime.metal_permit.acquire_solve()
        try:
            status, raw, _headers = await _request(
                app,
                "/api/results/busy/field-plane",
                _body(),
            )
        finally:
            await lease.release()
        assert status == 503
        assert json.loads(raw)["detail"]["code"] == "solve_running_or_queued"
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_latest_pending_field_request_wins_and_replaced_request_gets_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def evaluate(*args: Any, **_kwargs: Any) -> np.ndarray:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=5.0)
        points = np.asarray(args[5])
        return np.full(points.shape[0], call_number + 0j)

    _mock_field_backend(monkeypatch, evaluate)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "coalesce")
        first = asyncio.create_task(
            _request(
                app,
                "/api/results/coalesce/field-plane",
                _body("first"),
            )
        )
        assert await asyncio.to_thread(entered.wait, 2.0)
        pending = asyncio.create_task(
            _request(
                app,
                "/api/results/coalesce/field-plane",
                _body("pending"),
            )
        )
        while not app.state.jobs_runtime.metal_permit.field_pending:
            await asyncio.sleep(0)
        newest = asyncio.create_task(
            _request(
                app,
                "/api/results/coalesce/field-plane",
                _body("newest"),
            )
        )
        pending_status, pending_raw, _headers = await pending
        assert pending_status == 429
        assert json.loads(pending_raw)["detail"]["code"] == "superseded"
        release.set()
        first_result, newest_result = await asyncio.gather(first, newest)
        assert first_result[0] == 200
        assert newest_result[0] == 200
        assert calls == 2
        assert _decode(newest_result[1])[0]["request_id"] == "newest"
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_evaluation_timeout_returns_504_but_holds_permit_until_thread_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def evaluate(*args: Any, **_kwargs: Any) -> np.ndarray:
        entered.set()
        assert release.wait(timeout=5.0)
        return np.zeros(np.asarray(args[5]).shape[0], dtype=np.complex128)

    _mock_field_backend(monkeypatch, evaluate)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "timeout")
        app.state.jobs_runtime.field_plane_service.timeout_seconds = 0.01
        status, raw, _headers = await _request(
            app,
            "/api/results/timeout/field-plane",
            _body(),
        )
        # Wait rather than sample: the 10 ms timeout is meant to expire before
        # the evaluation finishes, not before the worker thread is scheduled at
        # all, and a loaded machine loses that second race. What matters is that
        # the work did start and outlived the response, not that it started
        # within the timeout.
        assert entered.wait(timeout=10.0)
        assert status == 504
        assert json.loads(raw)["detail"]["code"] == "evaluation_timeout"
        assert app.state.jobs_runtime.metal_permit.field_running is True
        release.set()
        while app.state.jobs_runtime.metal_permit.field_running:
            await asyncio.sleep(0.001)
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_real_bempp_engine_traces_round_trip_through_field_plane_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("hornlab_bempp_bem")
    pytest.importorskip("bempp_cl.api")
    from server.engines import registry
    from server.solver import bempp

    status = bempp.bempp_status()
    if not status["available"]:
        pytest.skip(str(status["reason"]))

    async def build_tetrahedron(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "msh_text": TETRAHEDRON_MESH_TEXT,
            "stats": {"vertex_count": 4, "triangle_count": 4},
            "metadata": {},
        }

    monkeypatch.setattr(bempp, "build_solver_mesh", build_tetrahedron)
    # The synthetic full tetrahedron replaces a mesher output, so it has no
    # reduced-domain cut even if request canonicalisation selects one.
    monkeypatch.setattr(bempp, "native_symmetry_plane", lambda _context: None)
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "L": 60,
                "a": 30,
                "a0": 10,
                "r0": 10,
                "mesh": {
                    "quadrants": 1234,
                    "wall_thickness": 2,
                    "max_triangles": 50000,
                },
                "source": {"shape": 2, "velocity": 1},
                "simulation": {
                    "f1": 320,
                    "f2": 321,
                    "num_frequencies": 1,
                    "sim_type": "freestanding",
                    "solver_mode": "full_3d",
                },
            },
            "options": {
                "engine": "bempp",
                "frequency_range": [320, 321],
                "num_frequencies": 1,
                "frequency_spacing": "linear",
                "stage_delay_ms": 0,
            },
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "bempp-field-plane.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [
                    registry.EngineInfo("bempp", True, "test", "editable")
                ],
                factory=lambda _name: bempp.BemppEngine(),
            ),
        )
        try:
            job_id = await runtime.submit(request)
            await runtime.wait_idle(timeout=180.0)
            job = await runtime.get_job(job_id)
            assert job["status"] == "complete", job["error_message"]
            assert job["field_plane_available"] is True

            trace_record = runtime.store.get_field_trace_record(job_id)
            assert trace_record is not None
            assert trace_record["version"] == 2
            loaded = runtime.store.load_field_traces(job_id, 0, "default")
            assert loaded[6] == BEMPP_FIELD_TRACE_BACKEND

            body = _body("real-bempp", response_id="channel:default")
            body["plane"].update(
                {
                    "origin_m": [2.0, 2.0, 2.0],
                    "width_m": 0.2,
                    "height_m": 0.2,
                    "nx": 2,
                    "ny": 2,
                }
            )
            evaluation = await runtime.evaluate_field_plane(
                job_id,
                FieldPlaneRequest.model_validate(body),
            )
            assert evaluation.frequency_hz == pytest.approx(320.0)
            assert evaluation.pressure.shape == (4,)
            assert np.all(np.isfinite(evaluation.pressure))
            assert np.any(np.abs(evaluation.pressure) > 0.0)
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import struct
import threading
from typing import Any

import numpy as np
import pytest

from server.app import create_app
from server.jobs.models import FieldPlaneRequest
from server.jobs.store import JobStore
from server.mesh.artifact import mesh_text_sha256
from server.solver import field_plane
from server.solver.combine import raw_channel_weights
from server.solver.field_traces_store import FieldTraceArtifact, FieldTraceChannel


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


def _artifact(*, multi: bool = False) -> FieldTraceArtifact:
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
    )


def _create_job(
    store: JobStore,
    job_id: str,
    *,
    status: str = "complete",
    traces: bool = True,
    multi: bool = False,
    solve_path: str = "full-3d",
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
        store.store_field_traces(job_id, _artifact(multi=multi))


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
    monkeypatch.setattr(
        field_plane,
        "load_mesh",
        lambda path, **_kwargs: mesh_loads.append(Path(path)) or object(),
    )

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

    monkeypatch.setattr(field_plane, "evaluate_exterior_from_traces", evaluate)

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
                "version": 1,
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


def test_system_response_weights_both_traces_and_evaluates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[np.ndarray, np.ndarray]] = []
    monkeypatch.setattr(field_plane, "load_mesh", lambda *_args, **_kwargs: object())

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

    monkeypatch.setattr(field_plane, "evaluate_exterior_from_traces", evaluate)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        _create_job(store, "multi", multi=True)
        status, raw, _headers = await _request(
            app,
            "/api/results/multi/field-plane",
            _body(response_id="system"),
        )
        assert status == 200
        header, _values = _decode(raw)
        assert header["response_id"] == "system"
        assert len(calls) == 1

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
    monkeypatch.setattr(field_plane, "load_mesh", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        field_plane,
        "evaluate_exterior_from_traces",
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
    monkeypatch.setattr(field_plane, "load_mesh", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        field_plane,
        "evaluate_exterior_from_traces",
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
    monkeypatch.setattr(field_plane, "load_mesh", lambda *_args, **_kwargs: object())

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

    monkeypatch.setattr(field_plane, "evaluate_exterior_from_traces", evaluate)

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
    monkeypatch.setattr(field_plane, "load_mesh", lambda *_args, **_kwargs: object())

    def evaluate(*args: Any, **_kwargs: Any) -> np.ndarray:
        entered.set()
        assert release.wait(timeout=5.0)
        return np.zeros(np.asarray(args[5]).shape[0], dtype=np.complex128)

    monkeypatch.setattr(field_plane, "evaluate_exterior_from_traces", evaluate)

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
        assert entered.is_set()
        assert status == 504
        assert json.loads(raw)["detail"]["code"] == "evaluation_timeout"
        assert app.state.jobs_runtime.metal_permit.field_running is True
        release.set()
        while app.state.jobs_runtime.metal_permit.field_running:
            await asyncio.sleep(0.001)
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())

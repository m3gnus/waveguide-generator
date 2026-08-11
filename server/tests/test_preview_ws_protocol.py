"""Direct WS core conformance tests.

The managed test sandbox cannot bind sockets, so these tests drive the
transport-independent state machine with queues. The overseer owns the live
FastAPI/WebSocket end-to-end check described in the batch handoff.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import threading
from typing import Any

import numpy as np
import pytest

from server.preview.core import (
    CLOSE_TOO_LARGE,
    CLOSE_UNSUPPORTED_VERSION,
    PreviewComputeService,
    PreviewProtocol,
    _cache_relevant_config,
    preview_options,
)
from server.protocol.frame import FrameError, decode


class FakeTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | bytes | Mapping[str, Any] | None] = asyncio.Queue()
        self.json: list[dict[str, Any]] = []
        self.binary: list[bytes] = []
        self.closes: list[int] = []
        self.changed = asyncio.Event()

    async def receive(self) -> str | bytes | Mapping[str, Any] | None:
        return await self.incoming.get()

    async def send_json(self, message: Mapping[str, Any]) -> None:
        self.json.append(dict(message))
        self.changed.set()

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(data)
        self.changed.set()

    async def close(self, code: int) -> None:
        self.closes.append(code)
        self.changed.set()


def _request(
    epoch: int,
    seq: int,
    *,
    revision: int | None = None,
    lod: str = "coarse",
    design: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "v": 1,
        "kind": "preview",
        "epoch": epoch,
        "seq": seq,
        "designRevision": seq if revision is None else revision,
        "design": dict(design or {"formula": "OSSE", "L": 120, "a": 45}),
        "lod": lod,
    }


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    async def wait_loop() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_loop(), timeout)


def _small_geometry():
    from hornlab_mesher.preview.api import PreviewGeometryV1, PreviewSurfaceV1

    return PreviewGeometryV1(
        surfaces=[
            PreviewSurfaceV1(
                role="horn.inner",
                positions=np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64),
                indices=np.asarray([0, 1, 2], dtype=np.uint32),
                normals=np.asarray([[0, 0, 1]] * 3, dtype=np.float64),
                shading="smooth",
                normal_method="analytic-parametric",
                closed_phi=False,
            )
        ],
        metadata={
            "api_version": "hornlab.preview/1",
            "actual_segment_counts": {"horn_phi": 3},
            "fidelity": {
                "horn.inner": {
                    "max_chord_error_mm": 0.2,
                    "max_normal_step_deg": 1.5,
                    "reference_density_multiplier": 4,
                }
            },
        },
    )


def test_hello_has_one_epoch_heartbeat_and_limit() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=71, heartbeat_seconds=0.01, max_frame_bytes=12345)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: len(transport.json) >= 2)
        await transport.incoming.put(None)
        await task
        hello = transport.json[0]
        assert hello == {
            "v": 1,
            "kind": "hello",
            "epoch": 71,
            "heartbeatSec": 0.01,
            "limits": {"maxFrameBytes": 12345},
        }
        assert transport.json[1]["kind"] == "ping"
        assert transport.json[1]["epoch"] == 71
        assert sum(message["kind"] == "hello" for message in transport.json) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"heartbeat_seconds": float("nan")}, "heartbeat_seconds"),
        ({"heartbeat_seconds": float("inf")}, "heartbeat_seconds"),
        ({"heartbeat_seconds": True}, "heartbeat_seconds"),
        ({"max_frame_bytes": 1.5}, "max_frame_bytes"),
        ({"max_frame_bytes": True}, "max_frame_bytes"),
        ({"epoch": 0}, "epoch"),
        ({"epoch": True}, "epoch"),
    ],
)
def test_protocol_constructor_rejects_invalid_wire_limits(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PreviewProtocol(**kwargs)


def test_app_owned_preview_service_rejects_work_after_bounded_shutdown() -> None:
    async def scenario() -> None:
        service = PreviewComputeService(max_workers=1)
        assert await service.run(lambda: b"ready") == b"ready"
        await service.shutdown()
        assert service.closed is True
        with pytest.raises(RuntimeError, match="shutting down"):
            await service.run(lambda: b"late")

    asyncio.run(scenario())


def test_preview_service_reuses_and_evicts_bounded_geometry() -> None:
    service = PreviewComputeService(max_workers=1, max_cache_entries=1, max_cache_bytes=10)
    builds = 0

    def build() -> object:
        nonlocal builds
        builds += 1
        return object()

    first = service.get_or_build("first", build, size_of=lambda _value: 4)
    assert service.get_or_build("first", build, size_of=lambda _value: 4) is first
    service.get_or_build("second", build, size_of=lambda _value: 4)
    service.get_or_build("first", build, size_of=lambda _value: 4)
    assert builds == 3


def test_protocol_reuses_injected_builder_for_identical_geometry_requests() -> None:
    async def scenario() -> None:
        builds = 0

        def builder(_config: Mapping[str, Any], _options: Any):
            nonlocal builds
            builds += 1
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=72, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(72, 1, revision=10))
        await _wait_until(lambda: len(transport.binary) == 1)
        await transport.incoming.put(_request(72, 2, revision=11))
        await _wait_until(lambda: len(transport.binary) == 2)
        await transport.incoming.put(None)
        await task

        assert builds == 1
        assert [decode(frame)[0]["seq"] for frame in transport.binary] == [1, 2]

    asyncio.run(scenario())


def test_one_pending_slot_is_latest_wins_and_reports_replacement() -> None:
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()

        def builder(config: Mapping[str, Any], _options: Any):
            if float(config["profile"]["L"]) == 100:
                started.set()
                assert release.wait(2)
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=8, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(8, 1, design={"formula": "OSSE", "L": 100}))
        assert await asyncio.to_thread(started.wait, 1)
        await transport.incoming.put(_request(8, 2, design={"formula": "OSSE", "L": 110}))
        await transport.incoming.put(_request(8, 3, design={"formula": "OSSE", "L": 120}))
        await _wait_until(lambda: any(item.get("kind") == "dropped" for item in transport.json))
        release.set()
        await _wait_until(lambda: len(transport.binary) == 2)
        await transport.incoming.put(None)
        await task

        dropped = [item for item in transport.json if item.get("kind") == "dropped"]
        assert dropped == [{"v": 1, "epoch": 8, "kind": "dropped", "seq": 2}]
        assert [decode(frame)[0]["seq"] for frame in transport.binary] == [1, 3]

    asyncio.run(scenario())


def test_a_resumed_drag_does_not_wait_for_the_fine_refinement_it_interrupted() -> None:
    """A coarse frame must not be stuck behind a fine build that outlives it.

    The client asks for fine detail 140 ms after the last edit, and fine costs
    several times what coarse does, so resuming a drag lands squarely inside
    one. With a single queue the new gesture's first frame could not start
    until the refinement finished; here the coarse frame comes back while the
    fine builder is still blocked.
    """

    async def scenario() -> None:
        fine_started = threading.Event()
        release_fine = threading.Event()

        def builder(_config: Mapping[str, Any], options: Any):
            if getattr(options, "lod", "coarse") == "fine":
                fine_started.set()
                assert release_fine.wait(5)
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=65, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(65, 1, revision=1, lod="fine"))
        assert await asyncio.to_thread(fine_started.wait, 2)
        await transport.incoming.put(_request(65, 2, revision=2, lod="coarse"))

        # The coarse frame arrives with the fine builder still held.
        await _wait_until(lambda: len(transport.binary) == 1)
        assert decode(transport.binary[0])[0]["lod"] == "coarse"
        assert not release_fine.is_set()

        release_fine.set()
        await _wait_until(lambda: len(transport.binary) == 2)
        assert decode(transport.binary[1])[0]["lod"] == "fine"
        # Neither lane superseded the other, so nothing was reported dropped.
        assert [item for item in transport.json if item.get("kind") == "dropped"] == []
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_each_lane_keeps_its_own_latest_wins_slot() -> None:
    """Holding the lanes apart must not widen the queue inside either one."""

    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()

        def builder(config: Mapping[str, Any], _options: Any):
            if float(config["profile"]["L"]) == 100:
                started.set()
                assert release.wait(5)
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=66, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(
            _request(66, 1, lod="coarse", design={"formula": "OSSE", "L": 100})
        )
        assert await asyncio.to_thread(started.wait, 2)
        for seq, length in ((2, 110), (3, 120)):
            await transport.incoming.put(
                _request(66, seq, lod="coarse", design={"formula": "OSSE", "L": length})
            )
        await _wait_until(lambda: any(item.get("kind") == "dropped" for item in transport.json))
        release.set()
        await _wait_until(lambda: len(transport.binary) == 2)
        await transport.incoming.put(None)
        await task

        dropped = [item for item in transport.json if item.get("kind") == "dropped"]
        assert dropped == [{"v": 1, "epoch": 66, "kind": "dropped", "seq": 2}]
        assert [decode(frame)[0]["seq"] for frame in transport.binary] == [1, 3]

    asyncio.run(scenario())


def test_design_validation_error_carries_seq_and_revision_paths() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=4, preview_builder=lambda *_: _small_geometry())
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(
            _request(4, 9, revision=44, design={"formula": "OSSE", "unknown": 1})
        )
        await _wait_until(lambda: len(transport.json) == 2)
        await transport.incoming.put(None)
        await task
        error = transport.json[1]
        assert error["kind"] == "error"
        assert error["code"] == "validation"
        assert error["seq"] == 9
        assert error["designRevision"] == 44
        assert any(
            path.startswith("design.") and path.endswith("unknown")
            for path in error["fields"]
        )

    asyncio.run(scenario())


def test_stale_epoch_is_exposed_and_does_not_compute_or_reply() -> None:
    async def scenario() -> None:
        calls: list[Any] = []

        def builder(*args: Any):
            calls.append(args)
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=12, preview_builder=builder)
        assert protocol.is_current_epoch(12)
        assert not protocol.is_current_epoch(11)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(11, 1))
        await asyncio.sleep(0.02)
        assert len(transport.json) == 1
        assert not transport.binary
        assert not calls
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_oversize_message_closes_4413_without_computing() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(
            epoch=2,
            max_frame_bytes=256,
            preview_builder=lambda *_: (_ for _ in ()).throw(AssertionError()),
        )
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        oversized = _request(2, 1, design={"formula": "OSSE", "extra_keys": {"x": "z" * 500}})
        await transport.incoming.put(oversized)
        await _wait_until(lambda: bool(transport.closes))
        await task
        assert transport.closes == [CLOSE_TOO_LARGE]
        assert not transport.binary

    asyncio.run(scenario())


def test_recursive_mapping_is_a_validation_error_without_killing_the_socket() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=23)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))

        recursive_design: dict[str, Any] = {"formula": "OSSE"}
        cursor = recursive_design
        for _ in range(1500):
            child: dict[str, Any] = {}
            cursor["child"] = child
            cursor = child
        await transport.incoming.put(_request(23, 1, design=recursive_design))

        await _wait_until(lambda: len(transport.json) >= 2)
        assert transport.json[-1]["kind"] == "error"
        assert transport.json[-1]["code"] == "validation"
        assert transport.json[-1]["seq"] == 1
        assert transport.json[-1]["designRevision"] == 1
        assert not transport.closes
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_preview_rejects_conflicting_expression_representations_before_build() -> None:
    """The /ws/preview validation path must not validate one L and execute another."""

    async def scenario() -> None:
        builds = 0

        def builder(_config: Mapping[str, Any], _options: Any) -> Any:
            nonlocal builds
            builds += 1
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=24, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(
            _request(
                24,
                1,
                design={"formula": "OSSE", "L": {"value": 1, "raw": "999"}},
            )
        )

        await _wait_until(lambda: len(transport.json) >= 2)
        error = transport.json[-1]
        assert error["kind"] == "error"
        assert error["code"] == "validation"
        assert error["seq"] == 1
        assert error["designRevision"] == 1
        assert builds == 0
        assert not transport.closes
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_preview_preserves_parameterized_raw_with_cached_numeric_sidecar() -> None:
    """The /ws/preview path executes raw p-formulas while retaining editor samples."""

    async def scenario() -> None:
        translated: list[Any] = []

        def builder(config: Mapping[str, Any], _options: Any) -> Any:
            translated.append(config["profile"]["a"])
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=25, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(
            _request(
                25,
                1,
                design={
                    "formula": "OSSE",
                    "a": {"value": 45, "raw": "45 + cos(p)"},
                },
            )
        )
        await _wait_until(lambda: bool(transport.binary))
        await transport.incoming.put(None)
        await task
        assert translated == ["45 + cos(p)"]

    asyncio.run(scenario())


def test_unsupported_major_closes_4400() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=3)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        message = _request(3, 1)
        message["v"] = 2
        await transport.incoming.put(message)
        await _wait_until(lambda: bool(transport.closes))
        await task
        assert transport.closes == [CLOSE_UNSUPPORTED_VERSION]

    asyncio.run(scenario())


def test_lod_names_map_to_complete_mesher_presets() -> None:
    coarse = preview_options("coarse")
    fine = preview_options("fine")
    assert coarse.lod == "coarse"
    assert fine.lod == "fine"
    for options in (coarse, fine):
        assert options.include_inner
        assert options.include_outer
        assert options.include_enclosure
        assert options.include_source_cap
        assert options.include_rear_cap
    assert coarse.include_curvature is False
    assert fine.include_curvature is True


def test_curve_kind_coalesces_through_binary_codec() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=22)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(
            {
                "v": 1,
                "kind": "curve",
                "epoch": 22,
                "seq": 5,
                "designRevision": 14,
                "curveId": "horizontal",
                "points": [[0, 1], [2.5, 3]],
            }
        )
        await _wait_until(lambda: bool(transport.binary))
        await transport.incoming.put(None)
        await task
        header, arrays = decode(transport.binary[0])
        assert header["kind"] == "curve"
        assert header["designRevision"] == 14
        assert header["curveId"] == "horizontal"
        np.testing.assert_array_equal(arrays["points"], [[0, 1], [2.5, 3]])

    asyncio.run(scenario())


def test_runtime_error_carries_revision_and_internal_message() -> None:
    async def scenario() -> None:
        def broken_builder(*_args: Any) -> Any:
            raise RuntimeError("mesher unavailable")

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=31, preview_builder=broken_builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(31, 6, revision=27))
        await _wait_until(lambda: len(transport.json) == 2)
        await transport.incoming.put(None)
        await task
        assert transport.json[1] == {
            "v": 1,
            "epoch": 31,
            "kind": "error",
            "code": "internal",
            "seq": 6,
            "designRevision": 27,
            "message": "mesher unavailable",
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(("field", "value"), [("seq", "1"), ("seq", True), ("designRevision", "2")])
def test_sequence_and_revision_fields_are_strict_integers(field: str, value: object) -> None:
    async def scenario() -> None:
        calls: list[object] = []
        transport = FakeTransport()
        protocol = PreviewProtocol(
            epoch=41,
            preview_builder=lambda *_args: calls.append(object()),
        )
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        message = _request(41, 1)
        message[field] = value
        await transport.incoming.put(message)
        await _wait_until(lambda: len(transport.json) >= 2)
        await transport.incoming.put(None)
        await task
        assert transport.json[1]["kind"] == "error"
        assert transport.json[1]["code"] == "validation"
        assert not calls

    asyncio.run(scenario())


def test_preview_applies_legacy_migrations_before_translation() -> None:
    async def scenario() -> None:
        configs: list[Mapping[str, Any]] = []

        def builder(config: Mapping[str, Any], _options: Any):
            configs.append(config)
            return _small_geometry()

        design = {
            "formula": "FREEFORM",
            "profile_h": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 50}]},
            "profile_v": {"points": [{"z": 0, "r": 10}, {"z": 100, "r": 40}]},
            "cross_sections": [{"t": 0, "shape": "circle"}, {"t": 1, "shape": "ellipse"}],
            "inflection_policy": "allow",
        }
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=42, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(42, 1, design=design))
        await _wait_until(lambda: bool(transport.binary))
        await transport.incoming.put(None)
        await task
        assert configs[0]["profile"]["inflectionPolicy"] == "warn"
        assert configs[0]["profile"]["crossSections"][0]["shape"] == "ellipse"

    asyncio.run(scenario())


def test_in_flight_native_preview_work_is_abandoned_with_a_global_concurrency_cap() -> None:
    async def scenario() -> None:
        release = threading.Event()
        lock = threading.Lock()
        started = 0

        def builder(_config: Mapping[str, Any], _options: Any):
            nonlocal started
            with lock:
                started += 1
            assert release.wait(2)
            return _small_geometry()

        service = PreviewComputeService(max_workers=4)
        transports = [FakeTransport() for _ in range(5)]
        protocols = [PreviewProtocol(epoch=50 + index, preview_builder=builder, preview_service=service) for index in range(5)]
        tasks = [
            asyncio.create_task(protocol.run(transport))
            for protocol, transport in zip(protocols, transports)
        ]
        try:
            await _wait_until(lambda: all(transport.json for transport in transports))
            for index, transport in enumerate(transports):
                await transport.incoming.put(_request(50 + index, 1))
            await _wait_until(lambda: started == 4)
            await asyncio.sleep(0.03)
            assert started == 4
            for transport in transports:
                await transport.incoming.put(None)
            await asyncio.wait_for(asyncio.gather(*tasks), 0.5)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["heartbeat", "worker"])
def test_background_send_failure_ends_blocked_receive_loop(failure: str) -> None:
    class FailingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.hello_sent = False

        async def send_json(self, message: Mapping[str, Any]) -> None:
            if not self.hello_sent:
                self.hello_sent = True
                await super().send_json(message)
                return
            raise RuntimeError("transport unavailable")

        async def send_bytes(self, data: bytes) -> None:
            raise RuntimeError("transport unavailable")

    async def scenario() -> None:
        transport = FailingTransport()
        protocol = PreviewProtocol(
            epoch=60,
            heartbeat_seconds=0.01 if failure == "heartbeat" else 10,
            preview_builder=lambda *_args: _small_geometry(),
        )
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: transport.hello_sent)
        if failure == "worker":
            await transport.incoming.put(_request(60, 1))
        await asyncio.wait_for(task, 0.5)

    asyncio.run(scenario())


def _sized_geometry(triangles: int):
    """Disjoint unit triangles, so the caller picks the encoded frame size."""

    from hornlab_mesher.preview.api import PreviewGeometryV1, PreviewSurfaceV1

    corners = np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    shift = np.arange(triangles, dtype=np.float64).repeat(3).reshape(-1, 1)
    positions = np.tile(corners, (triangles, 1)) + shift * np.asarray([[2.0, 0.0, 0.0]])
    return PreviewGeometryV1(
        surfaces=[
            PreviewSurfaceV1(
                role="horn.inner",
                positions=positions,
                indices=np.arange(3 * triangles, dtype=np.uint32),
                normals=np.tile([0.0, 0.0, 1.0], (3 * triangles, 1)),
                shading="smooth",
                normal_method="analytic-parametric",
                closed_phi=False,
            )
        ],
        metadata={"api_version": "hornlab.preview/1", "fidelity": {}},
    )


def test_oversize_frame_errors_and_leaves_the_socket_open() -> None:
    """A fine frame over budget must not wedge the viewport at coarse.

    Closing with 4413 made this unrecoverable: the client reconnects, resends
    the same design, coarse succeeds, and fine closes the socket again forever.
    """

    def builder(_config: Mapping[str, Any], options: Any) -> Any:
        return _small_geometry() if options.lod == "coarse" else _sized_geometry(2_000)

    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(
            epoch=61, max_frame_bytes=64 * 1024, preview_builder=builder
        )
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(61, 1, revision=5, lod="coarse"))
        await _wait_until(lambda: len(transport.binary) == 1)
        await transport.incoming.put(_request(61, 2, revision=5, lod="fine"))
        await _wait_until(lambda: any(m.get("kind") == "error" for m in transport.json))
        error = next(m for m in transport.json if m.get("kind") == "error")
        assert error["code"] == "too-large"
        assert error["seq"] == 2
        assert error["designRevision"] == 5
        assert str(64 * 1024) in error["message"]
        assert transport.closes == []
        # The connection must still answer the next request rather than wedge.
        await transport.incoming.put(_request(61, 3, revision=6, lod="coarse"))
        await _wait_until(lambda: len(transport.binary) == 2)
        assert transport.closes == []
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_encoder_frame_too_large_takes_the_same_open_socket_path() -> None:
    """``encode`` enforces its own 32 MiB ceiling before the protocol's."""

    def builder(_config: Mapping[str, Any], _options: Any) -> Any:
        raise FrameError("frame-too-large", "40000000 > 33554432")

    async def scenario() -> None:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=62, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        await transport.incoming.put(_request(62, 1, revision=9))
        await _wait_until(lambda: any(m.get("kind") == "error" for m in transport.json))
        error = next(m for m in transport.json if m.get("kind") == "error")
        assert error["code"] == "too-large"
        assert error["designRevision"] == 9
        assert "40000000 > 33554432" in error["message"]
        assert transport.closes == []
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def _osse_design(angular_segments: int) -> dict[str, Any]:
    return {
        "formula": "OSSE",
        "L": 120,
        "a": 45,
        "a0": 10,
        "r0": 12.7,
        "k": 1,
        "n": 4,
        "q": 0.99,
        "s": 0.8,
        "mesh": {"wall_thickness": 3, "angular_segments": angular_segments},
    }


def test_angular_segments_size_the_export_mesh_and_not_the_preview() -> None:
    """Pin the contract the 'Surface sampling' section now advertises.

    ``build_preview_geometry`` is error-bounded: it derives its own azimuthal
    sampling from the LOD's chord/normal/silhouette targets and overwrites
    ``mesh.angular_segments``. Two designs differing only in that field must
    therefore render identically, while still translating to different mesher
    configs so the export and solve paths keep honoring it.
    """

    async def scenario() -> list[bytes]:
        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=63)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        for index, angular in enumerate((40, 400)):
            await transport.incoming.put(
                _request(
                    63,
                    index + 1,
                    revision=index + 1,
                    design=_osse_design(angular),
                )
            )
            await _wait_until(lambda: len(transport.binary) == index + 1, timeout=60.0)
        await transport.incoming.put(None)
        await task
        return list(transport.binary)

    sparse, dense = asyncio.run(scenario())
    sparse_header, sparse_arrays = decode(sparse)
    dense_header, dense_arrays = decode(dense)
    assert sparse_header["surfaces"] == dense_header["surfaces"]
    assert sparse_header["fidelity"] == dense_header["fidelity"]
    assert sparse_arrays.keys() == dense_arrays.keys()
    for name, values in sparse_arrays.items():
        assert np.array_equal(values, dense_arrays[name]), name

    # Not dead, though: the same edit still reaches the export/solve mesh.
    from server.design.schema import DesignConfig
    from server.preview.translate import design_to_mesher_config

    translated = [
        design_to_mesher_config(DesignConfig.model_validate(_osse_design(angular)))
        for angular in (40, 400)
    ]
    assert translated[0]["mesh"]["angularSegments"] == 40
    assert translated[1]["mesh"]["angularSegments"] == 400


def test_export_only_sampling_fields_do_not_invalidate_the_preview_cache() -> None:
    """The other half of the contract above: identical output, one build.

    The sampling controls sit beside the solve settings, so a user adjusting
    them is usually looking straight at the viewport. Keying the geometry cache
    on a field the builder overwrites charged a full rebuild for a frame that
    was already going to come out byte-identical.
    """

    async def scenario() -> int:
        builds = 0

        def builder(_config: Mapping[str, Any], _options: Any):
            nonlocal builds
            builds += 1
            return _small_geometry()

        transport = FakeTransport()
        protocol = PreviewProtocol(epoch=64, preview_builder=builder)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: bool(transport.json))
        for index, angular in enumerate((40, 400, 40)):
            await transport.incoming.put(
                _request(64, index + 1, revision=index + 1, design=_osse_design(angular))
            )
            await _wait_until(lambda: len(transport.binary) == index + 1)
        await transport.incoming.put(None)
        await task
        return builds

    assert asyncio.run(scenario()) == 1


def test_preview_relevant_config_keeps_every_field_the_builder_reads() -> None:
    """Guard the exclusion list against growing into something load-bearing."""

    config = {
        "formula": "OSSE",
        "profile": {"a": 45.0},
        "mesh": {
            "angularSegments": 40.0,
            "lengthSegments": 20.0,
            "angular_segments": 40,
            "length_segments": 20,
            "wallThickness": 3.0,
            "quadrants": 1234,
        },
    }
    relevant = _cache_relevant_config(config)
    assert relevant["mesh"] == {"wallThickness": 3.0, "quadrants": 1234}
    assert relevant["profile"] == {"a": 45.0}
    # Never mutate the config handed to the builder.
    assert config["mesh"]["angularSegments"] == 40.0
    assert _cache_relevant_config({"formula": "OSSE"}) == {"formula": "OSSE"}

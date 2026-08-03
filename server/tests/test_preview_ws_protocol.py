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

from server.preview.core import (
    CLOSE_TOO_LARGE,
    CLOSE_UNSUPPORTED_VERSION,
    PreviewProtocol,
    preview_options,
)
from server.protocol.frame import decode


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

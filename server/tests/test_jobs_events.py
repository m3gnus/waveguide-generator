from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from server.jobs.events import CLOSE_UNSUPPORTED_VERSION, JobsProtocol
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore


class FakeTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | bytes | Mapping[str, Any] | None] = asyncio.Queue()
        self.json: list[dict[str, Any]] = []
        self.closes: list[int] = []

    async def receive(self) -> str | bytes | Mapping[str, Any] | None:
        return await self.incoming.get()

    async def send_json(self, message: Mapping[str, Any]) -> None:
        self.json.append(dict(message))

    async def close(self, code: int) -> None:
        self.closes.append(code)


def _job(job_id: str) -> dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": "queued",
        "config_json": {"design": {"formula": "OSSE"}},
        "config_summary_json": {"formula_type": "OSSE"},
        "task_metadata": {},
    }


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    async def loop() -> None:
        while not predicate():
            await asyncio.sleep(0.002)

    await asyncio.wait_for(loop(), timeout)


def test_protocol_sends_hello_then_snapshot_and_live_persisted_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_job("one"), initial_event=("queued", {}))
        runtime = JobRuntime(store)
        runtime._started = True  # isolate protocol behavior from scheduler recovery
        transport = FakeTransport()
        protocol = JobsProtocol(runtime, epoch=17, heartbeat_seconds=1)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: len(transport.json) >= 2)
        assert [message["kind"] for message in transport.json[:2]] == ["hello", "snapshot"]
        assert transport.json[1]["cursor"] == 1
        assert transport.json[1]["jobs"][0]["id"] == "one"

        _, event = store.update_job_with_event(
            "one", {"progress": 0.5}, "progress", {"progress": 0.5}
        )
        runtime.events.publish(event)
        await _wait_until(lambda: len(transport.json) >= 3)
        assert transport.json[2]["cursor"] == 2
        assert transport.json[2]["epoch"] == 17
        await transport.incoming.put(None)
        await task

    asyncio.run(scenario())


def test_resume_replays_retained_events_or_returns_fresh_snapshot(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db", event_retention=2)
    store.initialize()
    store.create_job(_job("resume"), initial_event=("queued", {}))
    for progress in (0.1, 0.2, 0.3):
        store.update_job_with_event(
            "resume", {"progress": progress}, "progress", {"progress": progress}
        )
    runtime = JobRuntime(store)
    runtime._started = True
    protocol = JobsProtocol(runtime, epoch=3)
    retained = protocol.resume_payload(2)
    assert [event["cursor"] for event in retained] == [3, 4]
    stale = protocol.resume_payload(1)
    assert stale["kind"] == "snapshot"
    assert stale["cursor"] == 4


def test_unknown_protocol_major_closes_4400(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        runtime = JobRuntime(store)
        runtime._started = True
        transport = FakeTransport()
        protocol = JobsProtocol(runtime, epoch=9, heartbeat_seconds=1)
        task = asyncio.create_task(protocol.run(transport))
        await _wait_until(lambda: len(transport.json) == 2)
        await transport.incoming.put({"v": 2, "kind": "resume", "epoch": 9, "cursor": 0})
        await task
        assert transport.closes == [CLOSE_UNSUPPORTED_VERSION]

    asyncio.run(scenario())


def test_log_event_chunks_are_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = JobStore(tmp_path / "jobs.db")
        store.initialize()
        store.create_job(_job("log"))
        runtime = JobRuntime(store)
        runtime._started = True
        await runtime._append_log("log", "x" * 10_000)
        await runtime._flush_runtime_update("log")
        replay = store.replay_events(0)
        assert replay[-1]["type"] == "log"
        assert len(replay[-1]["payload"]["chunk"]) == 2000
        assert replay[-1]["payload"]["lines"] == [replay[-1]["payload"]["chunk"]]
        assert len((store.get_job_row("log")["task_metadata"])["log_tail"][0]) == 2000

    asyncio.run(scenario())

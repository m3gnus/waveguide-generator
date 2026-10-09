from __future__ import annotations

import asyncio
from pathlib import Path

import json
import pytest
from fastapi import FastAPI, HTTPException

from server.jobs.auto_export import AutoExportMiddleware, AutoExportOwner, router_for
from server.jobs.store import JobStore
from server.tests.test_jobs_store import _job


class StoredRuntime:
    """Actual SQLite metadata merge, without starting a numerical engine."""

    def __init__(self, path: Path):
        self.store = JobStore(path)
        self.store.initialize()

    async def get_job(self, job_id):
        row = self.store.get_job_row(job_id)
        return {"id": job_id, "status": row["status"], "has_results": True, **row["task_metadata"]}

    async def patch_metadata(self, job_id, fields):
        self.store.mutate_job_metadata(job_id, fields)


def runtime_for(tmp_path):
    runtime = StoredRuntime(tmp_path / "jobs.db")
    for name in ("one", "two"):
        runtime.store.create_job(_job(name, "complete"))
    return runtime


def test_two_windows_and_restart_preserve_completed_and_blocked_formats(tmp_path):
    async def scenario():
        runtime = runtime_for(tmp_path)
        await runtime.patch_metadata(
            "one",
            {
                "exported_files": ["old.csv"],
                "auto_export_formats": {"csv": {"status": "complete", "attempted_at": "yesterday"}},
            },
        )
        owner = AutoExportOwner(runtime)
        claims = await asyncio.gather(
            owner.claim("one", ["csv", "step"]), owner.claim("one", ["csv", "step"])
        )
        assert sum(c["claimed"] for c in claims) == 1
        claim = next(c for c in claims if c["claimed"])
        assert claim["formats"] == ["step"]
        assert claims[1]["busy"]
        await owner.finish(
            claim["token"],
            {
                "files": [],
                "formats": {
                    "step": {
                        "status": "blocked",
                        "attempted_at": "now",
                        "reason": "Destination already exists",
                    }
                },
                "completed_at": None,
            },
        )
        reopened = AutoExportOwner(StoredRuntime(tmp_path / "jobs.db"))
        assert await reopened.claim("one", ["csv", "step"]) == {"claimed": False, "busy": False}
        job = await runtime.get_job("one")
        assert job["exported_files"] == ["old.csv"]
        assert job["auto_export_formats"]["csv"]["status"] == "complete"
        await reopened.retry("one")
        assert (await reopened.claim("one", ["csv", "step"]))["formats"] == ["step"]

    asyncio.run(scenario())


def test_expired_and_cancelled_owner_is_fenced_until_native_request_settles(tmp_path):
    async def scenario():
        clock = [0.0]
        owner = AutoExportOwner(runtime_for(tmp_path), clock=lambda: clock[0])
        first = await owner.claim("one", ["step"])
        reservation = await owner.enter(first["token"])
        clock[0] = 61
        assert (await owner.claim("two", ["step"]))["busy"]
        with pytest.raises(HTTPException):
            await owner.heartbeat(first["token"])
        await owner.leave(reservation)
        second = await owner.claim("two", ["step"])
        assert second["claimed"]
        with pytest.raises(HTTPException):
            await owner.enter(first["token"])
        current = await owner.enter(second["token"])
        await owner.release(second["token"])
        assert (await owner.claim("one", ["json"]))["busy"]
        await owner.leave(current)
        assert (await owner.claim("one", ["json"]))["claimed"]

    asyncio.run(scenario())


def test_backend_restart_blocks_an_ambiguous_publication_until_explicit_retry(tmp_path):
    async def scenario():
        runtime = runtime_for(tmp_path)
        first = await AutoExportOwner(runtime).claim("one", ["step"])
        assert first["claimed"]
        restarted = AutoExportOwner(StoredRuntime(tmp_path / "jobs.db"))
        assert not (await restarted.claim("one", ["step"]))["claimed"]
        await restarted.retry("one")
        assert (await restarted.claim("one", ["step"]))["claimed"]

    asyncio.run(scenario())


def test_slow_export_does_not_hold_capability_or_solve_http_requests(tmp_path):
    async def scenario():
        owner = AutoExportOwner(runtime_for(tmp_path))
        app = FastAPI()
        app.add_middleware(AutoExportMiddleware, owner=owner)
        app.include_router(router_for(owner))
        entered, release = asyncio.Event(), asyncio.Event()

        @app.post("/api/export/step")
        async def step():
            entered.set()
            await release.wait()
            return {"files": ["one.step"]}

        @app.get("/api/capabilities")
        async def capabilities():
            return {"engines": []}

        @app.post("/api/solve")
        async def solve():
            return {"job_id": "queued"}

        async def request(path, method="POST", body=None, token=None):
            delivered = False
            sent = []

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": json.dumps(body or {}).encode(),
                        "more_body": False,
                    }
                await asyncio.Event().wait()

            async def send(message):
                sent.append(message)

            headers = [(b"content-type", b"application/json")]
            if token:
                headers.append((b"x-wg-auto-export", token.encode()))
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "query_string": b"",
                    "root_path": "",
                    "headers": headers,
                    "server": ("test", 80),
                    "client": ("test", 1),
                },
                receive,
                send,
            )
            status = next(m["status"] for m in sent if m["type"] == "http.response.start")
            payload = b"".join(
                m.get("body", b"") for m in sent if m["type"] == "http.response.body"
            )
            return status, json.loads(payload)

        _, claim = await request("/api/jobs/one/auto-export/claim", body={"formats": ["step"]})
        slow = asyncio.create_task(request("/api/export/step", token=claim["token"]))
        await entered.wait()
        assert (await asyncio.wait_for(request("/api/capabilities", "GET"), 1))[0] == 200
        assert (await asyncio.wait_for(request("/api/solve"), 1))[1]["job_id"] == "queued"
        assert (await request("/api/jobs/two/auto-export/claim", body={"formats": ["step"]}))[1][
            "busy"
        ]
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        assert owner.owner.active == 1
        await owner.release(claim["token"])
        assert (await owner.claim("two", ["step"]))["busy"]
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert (await owner.claim("two", ["step"]))["claimed"]

    asyncio.run(scenario())


def test_archives_share_the_export_lane_and_finish_atomically(tmp_path):
    async def scenario():
        runtime = runtime_for(tmp_path)
        owner = AutoExportOwner(runtime)
        archive = await owner.claim("one", ["run_archive"])
        assert (await owner.claim("two", ["step"]))["busy"]
        await owner.finish(
            archive["token"],
            {
                "files": ["archived.json"],
                "formats": {"run_archive": {"status": "complete", "attempted_at": "today"}},
                "completed_at": "today",
            },
        )
        job = await runtime.get_job("one")
        assert job["archived_at"] == "today"
        assert job.get("auto_export_completed_at") is None
        assert not (await owner.claim("one", ["run_archive"]))["claimed"]
        assert (await owner.claim("one", ["step"]))["claimed"]

    asyncio.run(scenario())


def test_retry_clears_a_stale_completion_timestamp_without_losing_completed_formats(tmp_path):
    async def scenario():
        runtime = runtime_for(tmp_path)
        await runtime.patch_metadata(
            "one",
            {
                "auto_export_completed_at": "yesterday",
                "auto_export_formats": {
                    "csv": {"status": "complete", "attempted_at": "yesterday"},
                    "step": {
                        "status": "blocked",
                        "attempted_at": "yesterday",
                        "reason": "Conflict",
                    },
                },
            },
        )
        owner = AutoExportOwner(runtime)
        await owner.retry("one")
        job = await runtime.get_job("one")
        assert job["auto_export_completed_at"] is None
        assert job["auto_export_formats"]["csv"]["status"] == "complete"
        assert (await owner.claim("one", ["csv", "step"]))["formats"] == ["step"]

    asyncio.run(scenario())

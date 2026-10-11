"""Application harness for file inbox acceptance and recovery tests."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from server.app import create_app
from server.cadlink.joblane import job_shims
from server.cadlink.joblane import preparation
from server.cadlink.joblane.delivery_status import delivery_status
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT, normalize_request, request_digest
from server.tests.test_app_batch_e import TestClient

STARTUP = ("advertise_fusion_delivery_on_startup",)
REQUESTED_AT = "2026-09-17T10:00:00Z"

def _run_handlers(application, phase, names):
    handlers = application.router.on_startup if phase == "startup" else application.router.on_shutdown
    by_name = {handler.__name__: handler for handler in handlers}
    for name in names:
        result = by_name[name]()
        if asyncio.iscoroutine(result):
            asyncio.run(result)

class Wg:
    def __init__(self, application, data_dir, workspace):
        self.app, self.data_dir, self.workspace = application, data_dir, workspace
        self.client = TestClient(application)
        self._observed_jobs = set()

    @property
    def store(self):
        return self.app.state.cadlink_store

    def run_pass(self, *, running: set[str] | None = None) -> list[str]:
        """One pass of the backend delivery loop; returns what it started."""

        from server.cadlink.api import _preparation_context
        from server.cadlink.intake.returns_folder import selected_workspace_root

        spawned: list[str] = []

        def spawn(operation_id: str, coroutine) -> None:
            spawned.append(operation_id)
            coroutine.close()  # started, not run: the start is what is counted

        async def one() -> list[str]:
            ctx = _preparation_context(
                self.app.state, workspace_root=selected_workspace_root(self.app.state)
            )
            return await job_shims.run_delivery_pass(
                ctx, spawn=spawn, running=frozenset(running or ())
            )

        started = asyncio.run(one())
        # File acceptance creates its job before acknowledging. Count each job
        # seen by these intake scenarios once; the production pass itself starts
        # no second lifecycle and no operation coroutine is spawned.
        assert spawned == []
        jobs = self.app.state.jobs_runtime.store
        for row in self.store.list_operations(kind=PREPARE_AND_SOLVE):
            job_id = row.get("job_id")
            if job_id and job_id not in self._observed_jobs:
                self._observed_jobs.add(job_id)
                job = jobs.get_job_row(job_id)
                if job and job["status"] == "preparing" and row["operation_id"] not in started:
                    started.append(row["operation_id"])
        return started

    def settle(self) -> list[str]:
        from server.cadlink.api import _preparation_context
        from server.cadlink.intake.returns_folder import selected_workspace_root

        async def one() -> list[str]:
            ctx = _preparation_context(
                self.app.state, workspace_root=selected_workspace_root(self.app.state)
            )
            return await asyncio.to_thread(preparation.settle_received_snapshots, ctx)

        return asyncio.run(one())

    def ids(self) -> list[str]:
        return [str(row["operation_id"]) for row in self.store.list_operations(limit=1000)]

    def row(self, operation_id: str = "op-1") -> dict[str, Any]:
        row = self.store.get_operation(operation_id)
        assert row is not None
        return row


@contextmanager
def wg(
    tmp_path: Path, *, select: bool = True, startup: tuple[str, ...] = STARTUP, consumer: bool = True
) -> Iterator[Wg]:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    application = create_app(data_dir=data_dir, )
    # Intake scenarios count durable jobs without running a mesh or solver.
    # The jobs lane/binding and shim scenarios qualify preparation separately.
    application.state.jobs_runtime._ensure_prep_lane = lambda: None
    application.state.jobs_runtime._ensure_scheduler = lambda: None
    if select:
        application.state.cad_workspace.select(workspace)
    if consumer:
        # These tests are the consumer: they drive each delivery pass by hand
        # (``run_pass``) instead of starting the loop, so the route is told
        # one runs. ``consumer=False`` is a WG whose consumer is off.
        delivery_status(application.state).started()
    _run_handlers(application, "startup", startup)
    try:
        yield Wg(application, data_dir, workspace)
    finally:
        asyncio.run(application.state.jobs_runtime.shutdown())
        application.state.cadlink_store.close()


def _item(
    bundle_path: str,
    manifest: str,
    operation_id: str = "op-1",
    *,
    kind: str = PREPARE_AND_SOLVE,
    requested_at: str = REQUESTED_AT,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operationId": operation_id,
        "kind": kind,
        "bundlePath": bundle_path,
        "manifestSha256": manifest,
        "requestedAt": requested_at,
    }
    if kind == PREPARE_AND_SOLVE:
        body["returnId"] = "wgr_1"
    body.update(extra)
    return body


def _snapshot_digest(bundle_path: str, manifest: str) -> str:
    target, inputs = normalize_request(
        RECEIVE_SNAPSHOT, {}, {"bundle_path": bundle_path, "manifest_sha256": manifest}
    )
    return request_digest(RECEIVE_SNAPSHOT, target, inputs)

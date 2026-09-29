"""CAD solve scenarios on the production jobs backend.

The operations backend was retired at S4-F1. Mechanism-specific old scenarios
remain marked with the replacement job test until Stage 5 removes them; no
scenario executes the old lifecycle. Ledger and retention tests execute once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from server.cadlink import ingest as ingest_module
from server.cadlink.preparation import (
    PreparationContext,
    run_delivery_pass,
)
from server.cadlink.solve_command import record_outcome
from server.cadlink.store import CadLinkStore
from server.jobs.cad_intent import CadSolveIntent
from server.jobs.cad_preparation import CadPreparationHost, job_operation_view
from server.jobs.runtime import JobConflictError, JobRuntime, _CadLanePort, _ComposedJob
from server.jobs.store import JobStore

BACKENDS = ("jobs",)


def old_only(why: str, replacement: str):
    """Skip a scenario on the jobs backend, saying why and which test replaces it."""

    return pytest.mark.old_only(why=why, replacement=replacement)


backend_free = pytest.mark.backend_free


class FakeIngest:
    """``ingest_bundle`` without the mesher: it commits a real record, fenced."""

    def __init__(self, *, findings: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.findings = findings or []
        self.during: Callable[[], object] | None = None
        self.error: BaseException | None = None
        self.polar_grid_derivation: dict[str, Any] | None = None
        self.symmetry: dict[str, Any] | None = None

    def __call__(
        self, bundle_path, mesh, skipped, store, data_dir, *, prep_options, commit_guard,
        retained_copy=False, defer_viewport=False, expected_design_id=None,
        expected_instance_id=None,
    ):
        self.calls.append(
            {
                "bundle_path": str(bundle_path),
                "mesh": dict(mesh),
                "retained_copy": retained_copy,
                "defer_viewport": defer_viewport,
                "expected_design_id": expected_design_id,
                "expected_instance_id": expected_instance_id,
            }
        )
        # Stage 1 exactly as ingest_bundle reads it.
        bundle = ingest_module.read_snapshot(bundle_path, retained=retained_copy)
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error

        def record(ingest_id: str, now: str) -> str:
            payload = {
                "ingest_id": ingest_id,
                "manifest_sha256": bundle.manifest_sha256,
                "artifact_sha256": bundle.artifact_sha256,
                "findings": self.findings,
                "created_at": now,
                "document": {"return_state_hash": "sha256:" + "5" * 64},
            }
            if self.polar_grid_derivation is not None:
                payload["polar_grid_derivation"] = self.polar_grid_derivation
            if self.symmetry is not None:
                payload["symmetry"] = self.symmetry
            payload["report_sha256"] = "sha256:" + hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            return json.dumps(payload)

        row = store.allocate_ingest(
            manifest_sha256=bundle.manifest_sha256,
            artifact_sha256=bundle.artifact_sha256,
            record_builder=record,
            commit_guard=commit_guard,
        )
        return json.loads(row["record_json"])


class Refused(ValueError):
    """Stands in for the jobs system refusing a request it cannot take."""

    reason_code = "imported_engine_unsupported"


class Harness:
    """What every backend shares: the stores, the mesher stand-in and the jobs-system stand-in.

    ``_submit`` is the jobs system's seam: it records the request and what the job
    would keep of the operation, and can be made to raise (``submit_error``). A
    scenario may wrap it, and it means the same on both backends.
    """

    backend = ""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.data_dir = tmp_path / "data"
        self.workspace = tmp_path / "workspace"
        self.store = CadLinkStore(tmp_path / "cadlink.db")
        self.ingest = FakeIngest()
        self.jobs: dict[str, str] = {}
        self.submitted: list[Any] = []
        self.provenance: list[Any] = []
        self.submit_error: BaseException | None = None
        self.published: list[dict[str, Any]] = []
        self.blocked: str | None = None
        #: Further exception types the jobs system's stand-in refuses a request with.
        self.refusals: list[type[BaseException]] = []

    async def _submit(self, request, cad_provenance=None) -> str:
        self.submitted.append(request)
        self.provenance.append(cad_provenance)
        if self.submit_error is not None:
            error, self.submit_error = self.submit_error, None
            raise error
        job_id = f"job-{len(self.submitted)}"
        self.jobs[str(request.client_request_id)] = job_id
        return job_id

    def close(self) -> None:
        self.store.close()

    def row(self, operation_id: str = "cmd-1") -> dict[str, Any]:
        """The operations ledger's row: the acceptance record, whichever lifecycle solves it."""

        row = self.store.get_operation(operation_id)
        assert row is not None
        return row

    # -- the common interface ---------------------------------------------------

    def prepare(self, operation_id: str = "cmd-1", *, ingest: Any = None, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def summary(self, operation_id: str = "cmd-1") -> dict[str, Any]:
        raise NotImplementedError

    def delivery_pass(self, running: set[str] | frozenset[str] = frozenset()) -> list[str]:
        raise NotImplementedError

    def dismiss(self, operation_id: str = "cmd-1") -> None:
        """The user stops the solve, from wherever they can: on either backend."""

        raise NotImplementedError

    def restart(self) -> None:
        raise NotImplementedError

    def snapshot(self, operation_id: str = "cmd-1") -> dict[str, Any] | None:
        """What WG recorded of the operation's retained snapshot, or None."""

        row = self.store.get_operation(operation_id)
        return json.loads(row["snapshot_json"]) if row and row.get("snapshot_json") else None

    def break_provenance(self, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
        raise NotImplementedError

    def stage_log(self) -> list[str]:
        """The stages a preparation announced, in order."""

        return [item["stage"] for item in self.published if item.get("stage")]

    def bound_request(self, operation_id: str = "cmd-1") -> dict[str, Any] | None:
        """The exact request the solve is bound to, or None while it is not."""

        raise NotImplementedError

    def bound_setup_revision(self, operation_id: str = "cmd-1") -> str | None:
        """The setup revision the solve names: what it was prepared with."""

        raise NotImplementedError

    def approve(self, preparation_id: str, finding_ids: list[str], operation_id: str = "cmd-1") -> None:
        """The user reviews blocking findings of one preparation, ahead of the next press."""

        raise NotImplementedError

    def held_return_states(self) -> list[str]:
        """The captured-document states an unfinished solve still needs kept."""

        raise NotImplementedError

    def held_axis(self, operation_id: str = "cmd-1") -> str | None:
        """The solver frame axis the user's Solve showed, which the solve is held to."""

        raise NotImplementedError

    async def adismiss(self, operation_id: str = "cmd-1") -> None:
        """``dismiss`` for a seam that runs on the event loop."""

        self.dismiss(operation_id)


# -- the jobs backend -----------------------------------------------------------------


class _Loop:
    """One event loop on its own thread: the runtime lives on it, as it does in the server."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="cad-harness-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coroutine: Any, timeout: float = 120.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def call(self, function: Callable[[], Any]) -> None:
        self.loop.call_soon_threadsafe(function)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=30)
        self.loop.close()


class _Latch:
    """The update-restart latch, with the message a scenario chooses."""

    def __init__(self, harness: "JobsHarness") -> None:
        self._harness = harness
        self._listeners: list[Callable[[str, str], None]] = []

    @property
    def pending(self) -> str | None:
        return "0.0.0" if self._harness.blocked else None

    def refusal(self) -> str | None:
        return self._harness.blocked

    def remaining(self) -> float | None:
        return None

    def admit(self, start: Callable[[], Any]) -> tuple[bool, Any]:
        if self._harness.blocked:
            return False, None
        return True, start()

    def add_release_listener(self, listener: Callable[[str, str], None]) -> None:
        self._listeners.append(listener)

    def released(self) -> None:
        for listener in self._listeners:
            listener("0.0.0", "the latch came down")


class _HarnessPort(_CadLanePort):
    """The runtime's port, also refusing what the scenarios' ``Refused`` stands for."""

    harness: "JobsHarness"

    @property
    def binding_refusals(self) -> tuple[type[BaseException], ...]:
        return (*super().binding_refusals, Refused, *self.harness.refusals)


class JobsHarness(Harness):
    backend = "jobs"
    _blocked: str | None = None

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self._loop = _Loop()
        self._latch = _Latch(self)
        self._labels: dict[str, str] = {}
        self._latest: dict[str, str] = {}
        self._ingest_override: Any = None
        self._approved: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._events: list[dict[str, Any]] = []
        self._open()

    # -- the process --------------------------------------------------------------

    def _open(self) -> None:
        self.jobs_store = JobStore(self.tmp_path / "jobs.db")
        runtime = JobRuntime(
            self.jobs_store,
            cadlink_store=self.store,
            restart_approval=self._latch,  # type: ignore[arg-type]
        )
        # The stand-in for the jobs system's own checks (as ``_submit`` is for the
        # operations): a bound job is queued and stays queued, never solved here.
        runtime._compose_job = self._compose  # type: ignore[method-assign]
        runtime._ensure_scheduler = lambda: None  # type: ignore[method-assign]
        port = _HarnessPort(runtime)
        port.harness = self
        runtime._cad_port = port
        real_bind = runtime._bind_cad_job

        async def bind(job_id: str, request: Any, provenance: Mapping[str, Any]) -> str:
            outcome = await real_bind(job_id, request, provenance)
            if outcome == "bound":
                self._labels[job_id] = self._next_label
            return outcome

        runtime._bind_cad_job = bind  # type: ignore[method-assign]
        real_lane = runtime._run_cad_preparation

        async def lane(job_id: str) -> None:
            if self._dead:
                runtime._prep_running.discard(job_id)
                return
            try:
                await real_lane(job_id)
            except BaseException as crash:  # noqa: BLE001 - a scenario's stand-in for the process dying
                self.crashed = crash

        runtime._run_cad_preparation = lane  # type: ignore[method-assign]
        runtime.configure_cad_preparation(
            CadPreparationHost(
                store=self.store,
                data_dir=self.data_dir,
                workspace_root=lambda: self.workspace.resolve() if self.workspace.exists() else None,
                ingest=lambda *args, **kwargs: (self._ingest_override or self.ingest)(*args, **kwargs),
            )
        )
        self.runtime = runtime
        self._queue = self._loop.run(self._subscribe())
        self._loop.run(runtime.start())

    async def _subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        return self.runtime.events.subscribe()

    async def _compose(self, request: Any, cad_provenance: Any) -> _ComposedJob:
        label = await self._submit(request, cad_provenance=cad_provenance)
        self._next_label = label
        return _ComposedJob(
            request=request,
            config_summary={"formula_type": "cad-import"},
            script_snapshot=None,
            task_metadata={"cad": dict(cad_provenance or {})},
            mesh_stats=None,
            mesh_artifact="$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            imported=SimpleNamespace(),  # type: ignore[arg-type]
        )

    _next_label = ""
    #: Called after a job was accepted and before the ledger records it: where a
    #: scenario stops the process. The process is dead from then on: the lane
    #: does nothing more (``_dead``).
    after_job: Callable[[], None] | None = None
    _dead = False
    #: A ``BaseException`` that ended a preparation on the lane, for the scenario to see.
    crashed: BaseException | None = None

    def close(self) -> None:
        try:
            self._loop.run(self.runtime.shutdown())
        finally:
            self._loop.close()
            self.store.close()

    def restart(self) -> None:
        self._pending = self._preparing_operations()
        self._loop.run(self.runtime.shutdown())
        self.store.close()
        self.store = CadLinkStore(self.tmp_path / "cadlink.db")
        self._open()

    _pending: list[str] = []

    def _preparing_operations(self) -> list[str]:
        return sorted(
            operation_id
            for operation_id in self._latest
            if self.latest_job(operation_id)["status"] == "preparing"
        )

    def _started_since(self, before: list[str]) -> list[str]:
        """The operations that were still being prepared, and now have a queued job."""

        self._loop.run(self.runtime.wait_cad_preparations())
        return [
            operation_id for operation_id in before
            if self.latest_job(operation_id)["status"] == "queued"
        ]

    def recover(self) -> int:
        """Start-up recovery ran when the runtime started; there is nothing more to do."""

        return 0

    def recover_ledger(self) -> None:
        """The start-up sweep S4-F1 adds: a received solve gets its job, through the same key."""

        self._loop.run(self._recover_ledger())

    async def _recover_ledger(self) -> None:
        from server.cadlink.job_shims import sweep_pending_solves
        await sweep_pending_solves(self.context())
        await self.runtime.wait_cad_preparations()

    def release_latch(self) -> list[str]:
        """The update restart is called off: the lane takes up what it held, by itself."""

        before = self._preparing_operations()
        self.blocked = None
        return self._started_since(before)

    def resume_after_restart(self) -> list[str]:
        return self._started_since(self._pending)

    # -- the latch -----------------------------------------------------------------

    @property
    def blocked(self) -> str | None:
        return self._blocked

    @blocked.setter
    def blocked(self, message: str | None) -> None:
        was = self._blocked
        self._blocked = message
        if was and not message and hasattr(self, "runtime"):
            self._loop.call(self._latch.released)

    # -- the interface ---------------------------------------------------------------

    def _intent(self, operation_id: str, **kwargs: Any) -> CadSolveIntent:
        row = self.store.get_operation(operation_id)
        assert row is not None, operation_id
        inputs = json.loads(row["inputs_json"])
        approvals = (
            {
                "preparation_id": kwargs["approve_preparation_id"],
                "finding_ids": list(kwargs.get("approve_finding_ids") or ()),
            }
            if kwargs.get("approve_preparation_id")
            else None
        )
        return CadSolveIntent(
            operation_id=operation_id,
            bundle_path=inputs["bundle_path"],
            manifest_sha256=inputs["manifest_sha256"],
            return_id=inputs["return_id"],
            setup_revision_id=kwargs.get("setup_revision_id"),
            frame_axis=kwargs.get("expected_frame_axis"),
            approvals=approvals,
            submit=kwargs.get("submit", True),
        )

    def prepare(self, operation_id: str = "cmd-1", *, ingest: Any = None, **kwargs: Any) -> dict[str, Any]:
        self._ingest_override = ingest
        try:
            return self._loop.run(self._prepare(operation_id, kwargs))
        finally:
            self._ingest_override = None

    async def _prepare(self, operation_id: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        runtime = self.runtime
        if operation_id in self._approved and not kwargs.get("approve_preparation_id"):
            # Approvals the user gave ahead of this press ride with it.
            kwargs["approve_preparation_id"], kwargs["approve_finding_ids"] = self._approved[operation_id]
        known = self.jobs_store.latest_cad_job(operation_id)
        latest = known["id"] if known else self._latest.get(operation_id)
        if latest:
            self._latest[operation_id] = latest
        row = await asyncio.to_thread(self.jobs_store.get_job_row, latest) if latest else None
        if row is None:
            job_id = await runtime.accept_cad_solve(
                self._intent(operation_id, **kwargs), f"cad-solve:{operation_id}"
            )
            self._latest[operation_id] = job_id
            if self.after_job is not None:
                self._dead = True
                hook, self.after_job = self.after_job, None
                hook()
            await asyncio.to_thread(
                record_outcome, self.store, operation_id, state="accepted", job_id=job_id
            )
        elif row["status"] == "preparing" and row.get("started_at") is None:
            await runtime.prepare_cad_solve(latest, setup_revision_id=kwargs.get("setup_revision_id"),
                                            frame_axis=kwargs.get("expected_frame_axis"), submit=kwargs.get("submit", True))
        elif row["status"] == "error":
            try:
                self._latest[operation_id] = await runtime.solve_cad_again(
                    latest,
                    setup_revision_id=kwargs.get("setup_revision_id"),
                    frame_axis=kwargs.get("expected_frame_axis"),
                    approve_preparation_id=kwargs.get("approve_preparation_id"),
                    approve_finding_ids=tuple(kwargs.get("approve_finding_ids") or ()),
                    submit=kwargs.get("submit", True),
                )
            except JobConflictError:
                pass  # a return WG rejected is not solved again: the answer stays
        await runtime.wait_cad_preparations()
        return self._view(operation_id)

    def _resolve_latest(self, operation_id: str) -> str:
        """The job that stands for the operation now: the one its key made, then whatever continued it."""

        if operation_id not in self._latest:
            job_id = self.jobs_store.job_for_submission_key(f"cad-solve:{operation_id}")
            assert job_id, f"no job was accepted for {operation_id}"
            while True:
                children = [
                    row["id"]
                    for row in self.jobs_store.list_jobs(limit=500)[0]
                    if (row["config_json"] or {}).get("parent_job_id") == job_id
                ]
                if not children:
                    break
                job_id = children[0]
            self._latest[operation_id] = job_id
        return self._latest[operation_id]

    def _view(self, operation_id: str) -> dict[str, Any]:
        row = self.jobs_store.get_job_row(self._resolve_latest(operation_id))
        assert row is not None
        view = job_operation_view(row)
        view["jobId"] = self._labels.get(row["id"]) if view["jobId"] else None
        return view

    def summary(self, operation_id: str = "cmd-1") -> dict[str, Any]:
        return self._view(operation_id)

    def latest_job(self, operation_id: str = "cmd-1") -> dict[str, Any]:
        row = self.jobs_store.get_job_row(self._resolve_latest(operation_id))
        assert row is not None
        return row

    def delivery_pass(self, running: set[str] | frozenset[str] = frozenset()) -> list[str]:
        return self._loop.run(self._delivery_pass(running))

    def context(self) -> PreparationContext:
        return PreparationContext(store=self.store, data_dir=self.data_dir,
                                  workspace_root=self.workspace.resolve() if self.workspace.exists() else None,
                                  runtime=self.runtime, job_store=self.jobs_store,
                                  submission_blocked=lambda: self.blocked)

    async def _delivery_pass(self, running: set[str] | frozenset[str]) -> list[str]:
        # The crash hook lives at the durable cross-database join.
        real_accept_runtime = self.runtime.accept_cad_solve
        async def accepted(*args, **kwargs):
            job_id = await real_accept_runtime(*args, **kwargs)
            if self.after_job is not None:
                self._dead = True
                hook, self.after_job = self.after_job, None
                hook()
            return job_id
        self.runtime.accept_cad_solve = accepted
        try:
            started = await run_delivery_pass(self.context(), spawn=lambda *_args: None, running=running)
            for op in started:
                job = self.jobs_store.latest_cad_job(op)
                if job:
                    self._latest[op] = job["id"]
            await self.runtime.wait_cad_preparations()
        finally:
            self.runtime.accept_cad_solve = real_accept_runtime
        if self.crashed is not None:
            crash, self.crashed = self.crashed, None
            raise crash
        return started

    def dismiss(self, operation_id: str = "cmd-1") -> None:
        self._loop.run(self.adismiss(operation_id))

    async def adismiss(self, operation_id: str = "cmd-1") -> None:
        """Stop an active job; delete a refused one (the ledger keeps a cancelled row)."""

        latest = self._latest.get(operation_id)
        assert latest, "nothing was accepted yet: there is no job to stop"
        row = self.jobs_store.get_job_row(latest)
        assert row is not None
        if row["status"] in ("preparing", "queued", "running"):
            await self.runtime.stop(latest)
        else:
            await self.runtime.delete(latest)

    def bound_request(self, operation_id: str = "cmd-1") -> dict[str, Any] | None:
        config = self.latest_job(operation_id)["config_json"]
        return None if config.get("type") == "cad_intent" else config

    def bound_setup_revision(self, operation_id: str = "cmd-1") -> str | None:
        setup = ((self.latest_job(operation_id).get("task_metadata") or {}).get("cad") or {}).get("setup")
        return setup["revision_id"] if setup else None

    def approve(self, preparation_id: str, finding_ids: list[str], operation_id: str = "cmd-1") -> None:
        self._approved[operation_id] = (preparation_id, tuple(finding_ids))

    def held_return_states(self) -> list[str]:
        return [row["return_state_hash"] for row in self.jobs_store.unreleased_cad_return_states()]

    def held_axis(self, operation_id: str = "cmd-1") -> str | None:
        job = self.latest_job(operation_id)
        if job["config_json"].get("type") == "cad_intent":
            return job["config_json"].get("frame_axis")
        return ((job.get("task_metadata") or {}).get("cad") or {}).get("frame_axis_shown")

    def break_provenance(self, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
        from server.jobs import cad_preparation

        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise error

        monkeypatch.setattr(cad_preparation, "cad_provenance_record", broken)

    def stage_log(self) -> list[str]:
        stages: list[str] = []
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._events.append(event)
        for event in self._events:
            if event["type"] == "stage":
                stages.append(str(event["payload"].get("stage")))
            elif event["type"] == "queued":
                stages.append("submitted")
        return stages

    def snapshot(self, operation_id: str = "cmd-1") -> dict[str, Any] | None:
        cad = (self.latest_job(operation_id).get("task_metadata") or {}).get("cad") or {}
        return cad.get("snapshot")

    def state_rows(self) -> list[tuple[str, str]]:
        return [(row["id"], row["status"]) for row in self.jobs_store.list_jobs(limit=500)[0]]


def make_harness(tmp_path: Path, backend: str) -> Harness:
    assert backend == "jobs"
    return JobsHarness(tmp_path)


def backend_fixture(request: pytest.FixtureRequest, tmp_path: Path):
    """The ``harness`` fixture body: one backend per parameter, honouring the markers."""

    backend = request.param
    marker = request.node.get_closest_marker("old_only")
    if marker is not None and backend == "jobs":
        pytest.skip(f"operations mechanism ({marker.kwargs['why']}); job test: {marker.kwargs['replacement']}")
    harness = make_harness(tmp_path, backend)
    try:
        yield harness
    finally:
        harness.close()


__all__ = [
    "BACKENDS",
    "FakeIngest",
    "Harness",
    "JobsHarness",
    "Refused",
    "backend_fixture",
    "backend_free",
    "make_harness",
    "old_only",
]

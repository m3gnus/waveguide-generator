"""FIFO asyncio job scheduler and solve lifecycle.

The single-slot FIFO queue, queued/running recovery, active-delete rules, and
strong task references port v1 ``server/services/job_runtime.py:23-41,92-208,
259-285,497-574,629-686``. The dry-run solve itself is dispatched with
``asyncio.to_thread`` just like the retained in-process v1 execution model.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import inspect
import json
import logging
import math
import time
from typing import Any, Mapping
import uuid

from server.design.schema import Expr
from server.engines.registry import EngineRegistry, create_engine as get_engine
from server.jobs.legacy_design import resolve_job_design
from server.jobs.models import SolveRequest
from server.jobs.store import ALLOWED_STATUSES, JobStore
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode


logger = logging.getLogger(__name__)
MAX_LOG_LINES = 200
MAX_LOG_CHARS = 32_000
MAX_LOG_EVENT_CHARS = 2_000
CANCELLED_MESSAGE = "Simulation cancelled by user"
RUNTIME_PERSIST_INTERVAL_SECONDS = 0.15


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sweep_description(request: SolveRequest) -> str:
    """Describe the sweep for verbose logs without dumping 401 numbers."""

    explicit = request.options.frequencies_hz
    if explicit is None:
        return f"spacing={request.options.frequency_spacing}"
    head = ", ".join(f"{value:g}" for value in explicit[:6])
    suffix = ", …" if len(explicit) > 6 else ""
    return f"explicit list of {len(explicit)} points [{head}{suffix}]"


class JobNotFoundError(LookupError):
    """Requested job is absent from persistence."""


class JobConflictError(RuntimeError):
    """Requested mutation conflicts with the persisted lifecycle state."""


class JobResourceUnavailableError(RuntimeError):
    """Requested job result or artifact is not persisted."""


class EngineUnavailableError(RuntimeError):
    """The requested solve engine is known but unavailable."""


class UnknownEngineError(ValueError):
    """The request named an engine outside the registry."""


class SymmetryValidationError(ValueError):
    """The requested solve domain requires a mirror plane the geometry lacks."""


class _CancelledAtCheckpoint(RuntimeError):
    pass


@dataclass
class _PendingRuntimeUpdate:
    """Latest visible state, buffered logs, and the pinned in-flight prefix."""

    persisted_stage: str | None
    last_persisted_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stage: str | None = None
    progress: float | None = None
    message: str | None = None
    log_lines: list[str] = field(default_factory=list)
    timer_task: asyncio.Task[Any] | None = None
    expected_log_size: int | None = None
    pinned_log_lines: tuple[str, ...] | None = None
    closed: bool = False

    @property
    def pending(self) -> bool:
        return self.stage is not None or bool(self.log_lines)


class EventBroker:
    """Fan persisted events out to per-connection bounded queues."""

    def __init__(self, *, queue_size: int = 256) -> None:
        self.queue_size = max(1, queue_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: Mapping[str, Any]) -> None:
        message = dict(event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A hole forces the client reconciliation rule. Drop the oldest
                # buffered event so the subscriber can observe the cursor gap.
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


class JobRuntime:
    """One-worker FIFO runtime with a durable HTTP correctness path."""

    def __init__(
        self,
        store: JobStore,
        *,
        engine_registry: EngineRegistry | None = None,
        persistence_interval_seconds: float = RUNTIME_PERSIST_INTERVAL_SECONDS,
    ) -> None:
        self.store = store
        self.engine_registry = engine_registry or EngineRegistry(factory=get_engine)
        self.events = EventBroker()
        self._queue: deque[str] = deque()
        self._running: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._pending_updates: dict[str, _PendingRuntimeUpdate] = {}
        self._persistence_interval_seconds = max(
            0.01, float(persistence_interval_seconds)
        )
        self._started = False
        self._shutting_down = False
        self._start_lock = asyncio.Lock()

    @property
    def background_tasks(self) -> frozenset[asyncio.Task[Any]]:
        """Expose strong task refs for invariant tests and graceful shutdown."""

        return frozenset(self._background_tasks)

    @property
    def running_job_ids(self) -> frozenset[str]:
        return frozenset(self._running)

    async def start(self) -> None:
        """Initialize storage, fail running orphans, and requeue queued rows."""

        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            self._shutting_down = False
            await asyncio.to_thread(self.store.initialize)
            await asyncio.to_thread(
                self.store.prune_terminal_jobs, retention_days=30, max_terminal_jobs=1000
            )
            queued, recovery_events = await asyncio.to_thread(
                self.store.recover_on_startup, "Server restarted during execution"
            )
            self._queue.extend(row["id"] for row in queued)
            self._started = True
            for event in recovery_events:
                self.events.publish(event)
            self._ensure_scheduler()

    async def shutdown(self) -> None:
        """Stop local tasks without rewriting running rows; startup recovery owns them."""

        self._shutting_down = True
        # Stop accepting new buffered callbacks, then persist the last accepted
        # checkpoint before cancelling the scheduler. Cancelling first could
        # abandon an asyncio.to_thread file/SQLite write that is already running.
        await self._flush_all_runtime_updates(forget=True)
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._scheduler_task = None
        self._background_tasks.clear()
        # Store connections are long-lived now. Windows will not let a test's
        # temporary directory be removed, nor the migration tool replace the
        # database, while a handle is still open on it.
        await asyncio.to_thread(self.store.close)
        self._started = False

    async def submit(self, request: SolveRequest) -> str:
        await self.start()
        engine_name = request.options.engine
        known = {"auto", "dryrun", "metal", "bempp"}
        if engine_name not in known:
            raise UnknownEngineError(f"Unknown solve engine: {engine_name}")

        resolution = await asyncio.to_thread(resolve_symmetry, request.design)
        try:
            resolved_quadrants = validate_symmetry_mode(
                request.options.symmetry, resolution
            )
        except ValueError as exc:
            raise SymmetryValidationError(str(exc)) from exc
        symmetry_metadata = {
            "requested": request.options.symmetry,
            "resolved_quadrants": resolved_quadrants,
            "auto_resolution": resolution.as_dict(),
            "design_quadrants": (
                request.design.root.mesh.quadrants.text()
                if request.design.root.mesh.quadrants is not None
                else None
            ),
        }
        if engine_name == "auto":
            engine_name = await self.engine_registry.resolve(
                "auto", solver_mode=request.design.root.simulation.solver_mode
            )
            if engine_name is None:
                raise EngineUnavailableError(
                    "AUTO could not resolve a compatible solve engine from this host's "
                    "capabilities. Install/enable Metal or BEMPP; explicitly enable dry-run "
                    "with WG2_ENABLE_DRYRUN=1 for synthetic development solves."
                )
            request = request.model_copy(deep=True)
            request.options.engine = engine_name
        if await self.engine_registry.get_engine(engine_name) is None:
            reason = await self.engine_registry.unavailable_reason(engine_name)
            fallback_reason = (
                "Dry-run solves require WG2_ENABLE_DRYRUN=1."
                if engine_name == "dryrun"
                else "No capability reason was reported."
            )
            raise EngineUnavailableError(
                f"Solve engine '{engine_name}' is unavailable. "
                f"{reason or fallback_reason}"
            )

        job_id = str(uuid.uuid4())
        now = _now_iso()
        request_dump = request.model_dump(mode="json")
        summary = self._config_summary(request)
        summary["symmetry"] = symmetry_metadata
        polar_grid = request.options.polar_config.resolved_grid()
        assert request.design_snapshot is not None
        event = self.store.create_job(
            {
                "id": job_id,
                "parent_job_id": request.parent_job_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "progress": 0.0,
                "stage": "queued",
                "stage_message": "Job queued",
                "error_message": None,
                "cancellation_requested": False,
                "config_json": request_dump,
                "config_summary_json": summary,
                "has_results": False,
                "has_mesh_artifact": False,
                "mesh_stats": None,
                "label": request.label,
                "script_snapshot": request.design_snapshot.model_dump(mode="json"),
                "task_metadata": {
                    "log_tail": [],
                    "design_revision": request.design_revision,
                    "polar_grid": polar_grid,
                    "symmetry": symmetry_metadata,
                },
            },
            initial_event=("queued", {"status": "queued", "progress": 0.0}),
        )
        self._queue.append(job_id)
        if event is not None:
            self.events.publish(event)
        self._ensure_scheduler()
        return job_id

    async def stop(self, job_id: str) -> dict[str, str]:
        await self.start()
        row = self._require_job(job_id)
        status = row["status"]
        if status not in {"queued", "running"}:
            raise JobConflictError(f"Cannot stop job with status: {status}")
        if status == "queued":
            await self._flush_runtime_update(job_id, forget=True)
            self._remove_from_queue(job_id)
            event = self._transition(
                job_id,
                {
                    "status": "cancelled",
                    "progress": 0.0,
                    "stage": "cancelled",
                    "stage_message": "Simulation cancelled",
                    "error_message": CANCELLED_MESSAGE,
                    "completed_at": _now_iso(),
                    "cancellation_requested": False,
                },
                "cancelled",
                {"message": CANCELLED_MESSAGE},
            )
            self.events.publish(event)
            return {"message": f"Job {job_id} has been cancelled", "status": "cancelled"}

        # Do not let a delayed progress checkpoint overwrite the visible
        # cancelling stage after this method returns.
        await self._flush_runtime_update(job_id, forget=True)
        event = self.store.request_cancellation(
            job_id,
            {
                "stage": "cancelling",
                "stage_message": "Cancellation requested; waiting for current stage checkpoint",
                "cancellation_requested": True,
            },
            {"stage": "cancelling", "message": "Cancellation requested"},
        )
        if event is None:
            current = self._require_job(job_id)
            if current.get("status") != "running":
                raise JobConflictError(f"Cannot stop job with status: {current['status']}")
            return {
                "message": f"Cancellation already requested for job {job_id}",
                "status": "cancelling",
            }
        self.events.publish(event)
        return {
            "message": f"Cancellation requested for job {job_id}",
            "status": "cancelling",
        }

    async def get_job(self, job_id: str) -> dict[str, Any]:
        await self.start()

        def load() -> dict[str, Any]:
            return self._serialize_job(self._require_job(job_id), detailed=True)

        return await asyncio.to_thread(load)

    async def list_jobs(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        await self.start()
        statuses = self.parse_status_filter(status)

        def load() -> tuple[list[dict[str, Any]], int]:
            rows, total = self.store.list_jobs(
                statuses=statuses, limit=limit, offset=offset
            )
            return [self._serialize_job(row) for row in rows], total

        return await asyncio.to_thread(load)

    async def get_results(self, job_id: str) -> dict[str, Any]:
        return json.loads(await self.get_results_text(job_id))

    async def get_results_text(self, job_id: str) -> str:
        """The stored results JSON, read off the event loop and not re-parsed."""

        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        results = await asyncio.to_thread(self.store.get_results_text, job_id)
        if results is None:
            raise JobResourceUnavailableError("Results not available")
        return results

    async def get_mesh_artifact(self, job_id: str) -> str:
        await self.start()
        self._require_job(job_id)
        # Real MSH artifacts are multi-megabyte text blobs. Reading one through
        # SQLite on the event loop stalls both WS channels for the full copy.
        artifact = await asyncio.to_thread(self.store.get_mesh_artifact, job_id)
        if not artifact:
            raise JobResourceUnavailableError("No mesh artifact available for this job")
        return artifact

    async def get_log(self, job_id: str) -> str:
        await self.start()
        self._require_job(job_id)
        await self._flush_runtime_update(job_id)
        return await asyncio.to_thread(self.store.get_job_log, job_id)

    async def patch_metadata(self, job_id: str, fields: Mapping[str, Any]) -> None:
        await self.start()
        self._require_job(job_id)
        changed = dict(fields)
        column_fields: dict[str, Any] = {}
        if "label" in changed:
            column_fields["label"] = changed.pop("label")
        if "script_snapshot" in changed:
            value = changed.pop("script_snapshot")
            column_fields["script_snapshot_json"] = (
                json.dumps(value) if value is not None else None
            )
        if not changed and not column_fields:
            return
        changed_ok, event = self.store.mutate_job_metadata(
            job_id,
            changed,
            column_fields=column_fields,
            event_type="metadata",
            payload={"changed": dict(fields)},
        )
        if not changed_ok or event is None:
            raise JobNotFoundError(job_id)
        self.events.publish(event)

    async def delete(self, job_id: str) -> None:
        await self.start()
        row = self._require_job(job_id)
        if row["status"] in {"queued", "running"}:
            raise JobConflictError("Cannot delete active job")
        deleted, event = self.store.delete_job_with_event(job_id)
        if not deleted or event is None:
            raise JobNotFoundError(job_id)
        self._remove_from_queue(job_id)
        self._running.discard(job_id)
        self.events.publish(event)

    async def clear_failed(self) -> list[str]:
        await self.start()
        ids, events = self.store.delete_jobs_by_status_with_events(["error"])
        for job_id in ids:
            self._remove_from_queue(job_id)
            self._running.discard(job_id)
        for event in events:
            self.events.publish(event)
        return ids

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait for the queue and worker to empty (test/shutdown observation helper)."""

        async def wait_loop() -> None:
            while self._queue or self._running or (
                self._scheduler_task is not None and not self._scheduler_task.done()
            ):
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait_loop(), timeout)

    def snapshot(self) -> dict[str, Any]:
        """Return the current WS snapshot; callers must have started the runtime."""

        rows, cursor = self.store.snapshot_jobs()
        return {
            "v": 1,
            "kind": "snapshot",
            "cursor": cursor,
            "jobs": [self._serialize_job(row) for row in rows],
        }

    async def snapshot_async(self) -> dict[str, Any]:
        """Build the potentially large/cold snapshot without blocking both WS loops."""

        return await asyncio.to_thread(self.snapshot)

    def resume(self, cursor: int) -> list[dict[str, Any]] | None:
        return self.store.replay_events(cursor)

    async def resume_async(self, cursor: int) -> list[dict[str, Any]] | None:
        return await asyncio.to_thread(self.resume, cursor)

    def _ensure_scheduler(self) -> None:
        if self._shutting_down or not self._started or not self._queue:
            return
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        task = asyncio.create_task(self._drain_scheduler(), name="wg2-job-fifo")
        self._scheduler_task = task
        self._keep_task(task)

    def _keep_task(self, task: asyncio.Task[Any]) -> None:
        """Hold tasks strongly, porting v1 ``job_runtime.py:30-41``."""

        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_scheduler(self) -> None:
        try:
            while self._queue:
                job_id = self._queue.popleft()
                row = self.store.get_job_row(job_id)
                if row is None or row["status"] != "queued":
                    continue
                self._running.add(job_id)
                try:
                    await self._run_job(job_id, row)
                finally:
                    self._running.discard(job_id)
                    try:
                        _removed_ids, prune_events = await asyncio.to_thread(
                            self.store.prune_terminal_jobs_with_events,
                            retention_days=30,
                            max_terminal_jobs=1000,
                        )
                        for event in prune_events:
                            self.events.publish(event)
                    except Exception:
                        logger.exception("Post-job retention pruning failed")
        finally:
            self._scheduler_task = None
            if self._queue and not self._shutting_down:
                self._ensure_scheduler()

    async def _run_job(self, job_id: str, row: Mapping[str, Any]) -> None:
        try:
            request = SolveRequest.model_validate(row["config_json"])
            task_metadata = (
                dict(row.get("task_metadata") or {})
                if isinstance(row.get("task_metadata"), Mapping)
                else {}
            )
            symmetry_metadata = dict(task_metadata.get("symmetry") or {})
            resolved_quadrants = int(
                symmetry_metadata.get("resolved_quadrants", 1234)
            )
            request = request.model_copy(deep=True)
            request.design.root.mesh.quadrants = Expr(value=float(resolved_quadrants))
            try:
                engine = await self.engine_registry.get_engine(request.options.engine)
            except Exception:
                state = self.store.cancellation_state(job_id)
                if state is not None and state[0] != "queued":
                    return
                raise
            if engine is None:
                state = self.store.cancellation_state(job_id)
                if state is not None and state[0] != "queued":
                    return
                raise EngineUnavailableError(
                    f"Solve engine '{request.options.engine}' became unavailable"
                )
            # Batch Q extends only this established engine-call seam.  FIFO
            # scheduling/recovery remains untouched; real adapters own their
            # gmsh-worker + asyncio.to_thread orchestration, following v1
            # ``simulation_runner.py:397-427,430-527``.
            if request.options.engine != "dryrun":
                event = self.store.start_job(
                    job_id,
                    {
                        "status": "running",
                        "started_at": _now_iso(),
                        "stage": "initializing",
                        "stage_message": f"Initializing {engine.name} solver",
                        "progress": 0.05,
                    },
                    {
                        "status": "running",
                        "stage": "initializing",
                        "progress": 0.05,
                    },
                )
                if event is None:
                    return
                self.events.publish(event)
                await self._run_real_engine(
                    job_id, request, engine, symmetry_metadata=symmetry_metadata
                )
                return
            event = self.store.start_job(
                job_id,
                {
                    "status": "running",
                    "started_at": _now_iso(),
                    "stage": "mesh",
                    "stage_message": "Building dry-run mesh",
                    "progress": 0.05,
                },
                {"status": "running", "stage": "mesh", "progress": 0.05},
            )
            if event is None:
                return
            self.events.publish(event)

            design = request.design.model_dump(mode="json")
            delay = request.options.stage_delay_ms / 1000.0
            if request.options.verbose:
                await self._append_log(
                    job_id,
                    "Verbose solve options: "
                    f"engine={request.options.engine}, "
                    f"sweep={_sweep_description(request)}, "
                    f"mesh_validation={request.options.mesh_validation_mode}, "
                    f"polar={request.options.polar_config.model_dump(mode='json')}",
                )
            await self._stage(job_id, "mesh", 0.10, "Building dry-run mesh", delay)
            mesh_text, mesh_stats = await asyncio.to_thread(engine.mesh_artifact, design)
            self._check_cancelled(job_id)
            try:
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, mesh_text)
                self.store.update_job(job_id, mesh_stats_json=json.dumps(mesh_stats))
            except Exception as exc:
                # Optional artifact persistence is intentionally non-fatal, as in
                # v1 ``simulation_runner.py:451-466``.
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)

            await self._stage(job_id, "assemble", 0.30, "Assembling dry-run system", delay)
            await self._stage(job_id, "solve", 0.45, "Solving synthetic frequencies", delay)
            for progress in (0.58, 0.70, 0.82):
                self._check_cancelled(job_id)
                event = self._transition(
                    job_id,
                    {"progress": progress},
                    "progress",
                    {"stage": "solve", "progress": progress},
                )
                self.events.publish(event)
                await asyncio.sleep(delay / 3.0 if delay else 0)

            start, end, count = self._frequency_options(request)
            solve_started = time.perf_counter()
            results = await asyncio.to_thread(
                engine.solve,
                design,
                frequency_start_hz=start,
                frequency_end_hz=end,
                num_frequencies=count,
                frequency_spacing=request.options.frequency_spacing,
                frequencies_hz=request.options.frequencies_hz,
                polar_config=request.options.polar_config.model_dump(mode="json"),
                mesh_validation_mode=request.options.mesh_validation_mode,
                verbose=request.options.verbose,
            )
            result_metadata = results.setdefault("metadata", {})
            result_metadata.setdefault("solve_path", "full-3d")
            result_metadata.setdefault("axisymmetric_eligibility_reasons", [])
            result_metadata["solve_wall_time_seconds"] = (
                time.perf_counter() - solve_started
            )
            self._record_execution_metadata(job_id, result_metadata)
            results = self._with_request_metadata(
                results, request, symmetry_metadata=symmetry_metadata
            )
            self._check_cancelled(job_id)
            await self._stage(
                job_id, "postprocess", 0.90, "Postprocessing synthetic results", delay
            )
            self._check_cancelled(job_id)
            await self._append_log(job_id, "Dry-run result persistence ready")

            try:
                await self._flush_runtime_update(job_id, forget=True)
            except Exception as exc:
                logger.warning(
                    "Final runtime log flush failed for completed job %s: %s",
                    job_id,
                    exc,
                )

            completed_at = _now_iso()
            try:
                event = await asyncio.to_thread(
                    self.store.complete_job,
                    job_id,
                    results,
                    {
                        "status": "complete",
                        "stage": "complete",
                        "stage_message": "Simulation complete",
                        "progress": 1.0,
                        "completed_at": completed_at,
                        "cancellation_requested": False,
                        "error_message": None,
                    },
                    {"status": "complete", "progress": 1.0},
                )
            except Exception as exc:
                # Results persistence failure must end in error rather than leave a
                # running or falsely-complete row (v1 ``simulation_runner.py:540-553``).
                logger.error("Persistence error for job %s: %s", job_id, exc)
                await self._fail_job(
                    job_id,
                    "Results could not be saved. The simulation completed but persistence failed.",
                )
                return
            if event is None:
                self._check_cancelled(job_id)
                return
            self.events.publish(event)
        except _CancelledAtCheckpoint:
            await self._flush_runtime_update(job_id, forget=True)
            event = self._transition(
                job_id,
                {
                    "status": "cancelled",
                    "stage": "cancelled",
                    "stage_message": CANCELLED_MESSAGE,
                    "error_message": CANCELLED_MESSAGE,
                    "completed_at": _now_iso(),
                    "cancellation_requested": False,
                },
                "cancelled",
                {"message": CANCELLED_MESSAGE},
            )
            self.events.publish(event)
        except asyncio.CancelledError:
            # Preserve 'running' for the next process's orphan recovery.
            raise
        except Exception as exc:
            logger.error("Simulation error for job %s: %s", job_id, exc, exc_info=True)
            await self._fail_job(job_id, str(exc))

    async def _run_real_engine(
        self,
        job_id: str,
        request: SolveRequest,
        engine: Any,
        *,
        symmetry_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Run one real adapter while preserving Batch J's lifecycle seam.

        Native callbacks arrive from solver threads.  They are marshalled back
        onto the owning event loop before touching WS subscriber queues; stages
        map to the same overall ranges as v1
        ``server/services/simulation_runner.py:263-294``.
        """

        await self._append_log(job_id, f"Initializing {engine.name} solver")
        if request.options.verbose:
            await self._append_log(
                job_id,
                "Verbose solve options: "
                f"sweep={_sweep_description(request)}, "
                f"mesh_validation={request.options.mesh_validation_mode}, "
                f"polar={request.options.polar_config.model_dump(mode='json')}",
            )

        loop = asyncio.get_running_loop()
        stage_tasks: set[asyncio.Task[Any]] = set()
        artifact_persisted = False

        def stage_callback(stage: str, progress: float, message: str) -> None:
            def schedule() -> None:
                task = asyncio.create_task(
                    self._report_real_stage(job_id, stage, progress, message),
                    name=f"wg2-stage-{job_id}",
                )
                stage_tasks.add(task)

            loop.call_soon_threadsafe(schedule)

        async def artifact_callback(msh_text: str, mesh_stats: dict[str, Any]) -> None:
            """Persist immediately after meshing, before native solve like v1 lines 451-493."""

            nonlocal artifact_persisted
            if mesh_stats:
                self.store.update_job(job_id, mesh_stats_json=json.dumps(mesh_stats))
                # A mesh diagnosis that only reaches mesh_stats is a diagnosis
                # nobody reads. These used to be fatal -- an over-budget mesh
                # ended the solve -- so now that the solve continues, the log
                # is where the reason for a slow one has to appear.
                for warning in mesh_stats.get("warnings") or []:
                    await self._append_log(job_id, str(warning))
            try:
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, msh_text)
            except Exception as exc:
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)
            else:
                artifact_persisted = True

        try:
            run_kwargs: dict[str, Any] = {
                "cancel_cb": lambda: self._check_cancelled(job_id),
                "stage_cb": stage_callback,
            }
            if "artifact_cb" in inspect.signature(engine.run).parameters:
                run_kwargs["artifact_cb"] = artifact_callback
            solve_started = time.perf_counter()
            outcome = await engine.run(request, **run_kwargs)
        finally:
            # Drain callbacks queued by the final native frequency/result hook.
            await asyncio.sleep(0)
            if stage_tasks:
                gathered = await asyncio.gather(*tuple(stage_tasks), return_exceptions=True)
                for error in gathered:
                    if isinstance(error, BaseException) and not isinstance(
                        error, asyncio.CancelledError
                    ):
                        logger.error(
                            "Stage/progress persistence failed for job %s: %s",
                            job_id,
                            error,
                            exc_info=(type(error), error, error.__traceback__),
                        )
                stage_tasks.clear()

        self._check_cancelled(job_id)
        outcome_metadata = outcome.results.setdefault("metadata", {})
        outcome_metadata.setdefault("solve_path", "full-3d")
        outcome_metadata.setdefault("axisymmetric_eligibility_reasons", [])
        outcome_metadata["solve_wall_time_seconds"] = time.perf_counter() - solve_started
        self._record_execution_metadata(job_id, outcome_metadata)
        if outcome.msh_text and not artifact_persisted:
            try:
                if outcome.mesh_stats is not None:
                    self.store.update_job(job_id, mesh_stats_json=json.dumps(outcome.mesh_stats))
                await asyncio.to_thread(self.store.store_mesh_artifact, job_id, outcome.msh_text)
            except Exception as exc:
                # V1 makes the downloadable artifact optional
                # (``simulation_runner.py:451-466``).
                logger.warning("Mesh artifact persistence failed for job %s: %s", job_id, exc)
                self.store.update_job(job_id, has_mesh_artifact=False)

        self._check_cancelled(job_id)
        try:
            await self._flush_runtime_update(job_id, forget=True)
        except Exception as exc:
            logger.warning(
                "Final runtime log flush failed for completed job %s: %s",
                job_id,
                exc,
            )
        completed_at = _now_iso()
        try:
            event = await asyncio.to_thread(
                self.store.complete_job,
                job_id,
                self._with_request_metadata(
                    outcome.results,
                    request,
                    symmetry_metadata=symmetry_metadata,
                ),
                {
                    "status": "complete",
                    "stage": "complete",
                    "stage_message": "Simulation complete",
                    "progress": 1.0,
                    "completed_at": completed_at,
                    "cancellation_requested": False,
                    "error_message": None,
                },
                {"status": "complete", "progress": 1.0},
            )
        except Exception as exc:
            logger.error("Persistence error for job %s: %s", job_id, exc)
            await self._fail_job(
                job_id,
                "Results could not be saved. The simulation completed but persistence failed.",
            )
            return
        if event is not None:
            self.events.publish(event)
        else:
            self._check_cancelled(job_id)

    async def _report_real_stage(
        self,
        job_id: str,
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        normalized = max(0.0, min(1.0, float(progress)))
        if stage in {"mesh_prepare", "mesh_validate"}:
            public_stage = "mesh"
            overall = 0.10 + normalized * 0.20
        elif stage in {"setup", "solver_setup"}:
            public_stage = "assemble"
            overall = 0.30 + normalized * 0.05
        elif stage == "frequency_solve":
            public_stage = "solve"
            overall = 0.35 + normalized * 0.50
        elif stage in {"directivity", "finalizing"}:
            public_stage = "postprocess"
            overall = 0.85 + normalized * 0.14
        else:
            public_stage = str(stage)
            overall = normalized
        try:
            await self._stage(job_id, public_stage, overall, message, 0.0)
        except (_CancelledAtCheckpoint, JobNotFoundError):
            return

    async def _stage(
        self, job_id: str, stage: str, progress: float, message: str, delay: float
    ) -> None:
        self._check_cancelled(job_id)
        await self._queue_runtime_update(
            job_id,
            stage=stage,
            progress=progress,
            message=message,
            log_message=message,
        )
        await asyncio.sleep(delay)
        if delay:
            self._check_cancelled(job_id)

    async def _append_log(self, job_id: str, message: str) -> None:
        await self._queue_runtime_update(job_id, log_message=str(message))

    def _runtime_update_state(self, job_id: str) -> _PendingRuntimeUpdate:
        state = self._pending_updates.get(job_id)
        if state is not None:
            return state
        row = self._require_job(job_id)
        state = _PendingRuntimeUpdate(
            persisted_stage=(str(row["stage"]) if row.get("stage") is not None else None),
            last_persisted_at=time.monotonic(),
        )
        self._pending_updates[job_id] = state
        return state

    async def _queue_runtime_update(
        self,
        job_id: str,
        *,
        stage: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        log_message: str | None = None,
    ) -> None:
        if self._shutting_down:
            return
        state = self._runtime_update_state(job_id)
        async with state.lock:
            if (
                self._shutting_down
                or state.closed
                or self._pending_updates.get(job_id) is not state
            ):
                return
            stage_changed = stage is not None and stage != (
                state.stage if state.stage is not None else state.persisted_stage
            )
            if stage_changed and state.pending:
                await self._flush_runtime_update_locked(job_id, state)

            if stage is not None:
                state.stage = str(stage)
                state.progress = float(progress if progress is not None else 0.0)
                state.message = str(message or "")
            if log_message is not None:
                state.log_lines.append(str(log_message))

            elapsed = time.monotonic() - state.last_persisted_at
            if stage_changed or elapsed >= self._persistence_interval_seconds:
                await self._flush_runtime_update_locked(job_id, state)
            else:
                self._schedule_runtime_flush(
                    job_id,
                    state,
                    self._persistence_interval_seconds - elapsed,
                )

    def _schedule_runtime_flush(
        self, job_id: str, state: _PendingRuntimeUpdate, delay: float
    ) -> None:
        if state.timer_task is not None and not state.timer_task.done():
            return

        async def flush_later() -> None:
            try:
                await asyncio.sleep(max(0.0, delay))
                async with state.lock:
                    await self._flush_runtime_update_locked(job_id, state)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Buffered runtime persistence failed for job %s", job_id)
            finally:
                if state.timer_task is asyncio.current_task():
                    state.timer_task = None

        task = asyncio.create_task(flush_later(), name=f"wg2-persist-{job_id}")
        state.timer_task = task
        self._keep_task(task)

    async def _flush_runtime_update_locked(
        self, job_id: str, state: _PendingRuntimeUpdate
    ) -> None:
        timer = state.timer_task
        if timer is not None and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
        state.timer_task = None
        while not state.closed and state.pending:
            fields: dict[str, Any] = {}
            stage_payload: dict[str, Any] | None = None
            if state.stage is not None:
                fields = {
                    "stage": state.stage,
                    "stage_message": state.message or "",
                    "progress": (
                        state.progress if state.progress is not None else 0.0
                    ),
                }
                stage_payload = {
                    "stage": state.stage,
                    "message": state.message or "",
                    "progress": (
                        state.progress if state.progress is not None else 0.0
                    ),
                }

            if state.log_lines and state.pinned_log_lines is None:
                # Keep this exact prefix across failures and cancellation. A
                # later callback may extend log_lines before the retry, but it
                # must not change the bytes checked at expected_log_size.
                state.pinned_log_lines = tuple(state.log_lines)
            log_lines = state.pinned_log_lines or ()
            if log_lines and state.expected_log_size is None:
                state.expected_log_size = await asyncio.to_thread(
                    self.store.job_log_size, job_id
                )

            changed, events = await asyncio.to_thread(
                self.store.persist_runtime_update,
                job_id,
                fields,
                stage_payload=stage_payload,
                log_lines=log_lines,
                expected_log_size=state.expected_log_size,
                max_log_lines=MAX_LOG_LINES,
                max_log_chars=MAX_LOG_CHARS,
                max_log_event_chars=MAX_LOG_EVENT_CHARS,
            )
            if changed:
                if state.stage is not None:
                    state.persisted_stage = state.stage
                for event in events:
                    self.events.publish(event)

            state.stage = None
            state.progress = None
            state.message = None
            if log_lines:
                if tuple(state.log_lines[: len(log_lines)]) != log_lines:
                    raise RuntimeError("Pinned runtime log batch is no longer a prefix")
                del state.log_lines[: len(log_lines)]
            state.pinned_log_lines = None
            state.expected_log_size = None
            state.last_persisted_at = time.monotonic()

    async def _flush_runtime_update(self, job_id: str, *, forget: bool = False) -> None:
        state = self._pending_updates.get(job_id)
        if state is None:
            return
        try:
            async with state.lock:
                await self._flush_runtime_update_locked(job_id, state)
        finally:
            if forget:
                state.closed = True
                timer = state.timer_task
                if (
                    timer is not None
                    and timer is not asyncio.current_task()
                    and not timer.done()
                ):
                    timer.cancel()
                state.timer_task = None
                if self._pending_updates.get(job_id) is state:
                    self._pending_updates.pop(job_id, None)

    async def _flush_all_runtime_updates(self, *, forget: bool = False) -> None:
        for job_id in tuple(self._pending_updates):
            await self._flush_runtime_update(job_id, forget=forget)

    async def _fail_job(self, job_id: str, message: str) -> None:
        try:
            event = self._transition(
                job_id,
                {
                    "status": "error",
                    "stage": "error",
                    "stage_message": "Simulation failed",
                    "error_message": message,
                    "completed_at": _now_iso(),
                },
                "failed",
                {"message": message},
            )
        except Exception:
            logger.exception("Could not persist failure state for job %s", job_id)
        else:
            self.events.publish(event)

        try:
            await self._flush_runtime_update(job_id, forget=True)
        except Exception:
            logger.exception("Could not persist final logs for failed job %s", job_id)

    def _check_cancelled(self, job_id: str) -> None:
        # Deliberately synchronous: the solver thread calls this from inside a
        # native progress callback, where there is no event loop to await on.
        state = self.store.cancellation_state(job_id)
        if state is None:
            raise JobNotFoundError(job_id)
        status, cancellation_requested = state
        if cancellation_requested or status == "cancelled":
            raise _CancelledAtCheckpoint(CANCELLED_MESSAGE)

    def _transition(
        self,
        job_id: str,
        fields: Mapping[str, Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        changed, event = self.store.update_job_with_event(
            job_id, fields, event_type, payload
        )
        if not changed or event is None:
            raise JobNotFoundError(job_id)
        return event

    def _require_job(self, job_id: str) -> dict[str, Any]:
        row = self.store.get_job_row(job_id)
        if row is None:
            raise JobNotFoundError(job_id)
        return row

    def _remove_from_queue(self, job_id: str) -> None:
        if not self._queue:
            return
        self._queue = deque(item for item in self._queue if item != job_id)

    @staticmethod
    def parse_status_filter(raw: str | None) -> list[str] | None:
        if raw is None:
            return None
        values = list(dict.fromkeys(token.strip() for token in raw.split(",") if token.strip()))
        if not values:
            return None
        invalid = [value for value in values if value not in ALLOWED_STATUSES]
        if invalid:
            raise ValueError(
                f"status filter contains unsupported values: {', '.join(invalid)}"
            )
        return values

    @staticmethod
    def _config_summary(request: SolveRequest) -> dict[str, Any]:
        start, end, count = JobRuntime._frequency_options(request)
        return {
            "formula_type": request.design.formula,
            "frequency_range": [start, end],
            "num_frequencies": count,
            "frequency_source": (
                "explicit_list"
                if request.options.frequencies_hz is not None
                else "generated_grid"
            ),
            "engine": request.options.engine,
            "design_revision": request.design_revision,
            "polar_grid": request.options.polar_config.resolved_grid(),
        }

    @staticmethod
    def _with_request_metadata(
        results: Mapping[str, Any],
        request: SolveRequest,
        *,
        symmetry_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(results)
        metadata = dict(enriched.get("metadata") or {})
        metadata["design_revision"] = request.design_revision
        metadata["polar_grid"] = request.options.polar_config.resolved_grid()
        if symmetry_metadata is not None:
            metadata["symmetry"] = dict(symmetry_metadata)
        enriched["metadata"] = metadata
        return enriched

    def _record_execution_metadata(
        self, job_id: str, result_metadata: Mapping[str, Any]
    ) -> None:
        self.store.mutate_job_metadata(
            job_id,
            {
                "solve_path": result_metadata.get("solve_path", "full-3d"),
                "axisymmetric_eligibility_reasons": list(
                    result_metadata.get("axisymmetric_eligibility_reasons") or []
                ),
                "solve_wall_time_seconds": float(
                    result_metadata.get("solve_wall_time_seconds") or 0.0
                ),
            },
        )

    @staticmethod
    def _frequency_options(request: SolveRequest) -> tuple[float, float, int]:
        """Summarize the sweep as (start, end, count).

        An explicit list is summarized by its own endpoints and length, so job
        listings and config summaries describe the sweep that actually runs.
        """

        simulation = request.design.root.simulation

        def numeric(expr: Any, default: float) -> float:
            value = getattr(expr, "value", None) if expr is not None else None
            return float(value) if value is not None else default

        explicit = request.options.frequencies_hz
        if explicit is not None:
            return float(explicit[0]), float(explicit[-1]), len(explicit)
        if request.options.frequency_range is not None:
            start, end = request.options.frequency_range
        else:
            start = numeric(simulation.f1, 200.0)
            end = numeric(simulation.f2, 20_000.0)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("frequency bounds must be finite")
        count = request.options.num_frequencies
        if count is None:
            count = int(round(numeric(simulation.num_frequencies, 24.0)))
        count = max(1, min(401, count))
        if start <= 0 or end <= start:
            start, end = 200.0, 20_000.0
        return float(start), float(end), count

    @staticmethod
    def _serialize_job(row: Mapping[str, Any], *, detailed: bool = False) -> dict[str, Any]:
        metadata = row.get("task_metadata") if isinstance(row.get("task_metadata"), dict) else {}
        # An imported v1 job reaches the client already translated, so reopen,
        # rerun, compare and export need no legacy branch; one that cannot be
        # translated keeps its original bytes and carries the reason instead.
        design = resolve_job_design(row.get("script_snapshot"), row.get("config_json"))
        item = {
            "id": row.get("id"),
            "run_number": row.get("run_number"),
            "parent_job_id": row.get("parent_job_id"),
            "status": row.get("status"),
            "progress": float(row.get("progress", 0.0)),
            "stage": row.get("stage"),
            "stage_message": row.get("stage_message"),
            "created_at": row.get("created_at"),
            "queued_at": row.get("queued_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "config_summary": row.get("config_summary_json") or {},
            "has_results": bool(row.get("has_results")),
            "has_mesh_artifact": bool(row.get("has_mesh_artifact")),
            "label": row.get("label"),
            "error_message": row.get("error_message"),
            "cancellation_requested": bool(row.get("cancellation_requested")),
            "mesh_stats": row.get("mesh_stats"),
            "script_snapshot": design.snapshot
            if design.snapshot is not None
            else row.get("script_snapshot"),
            "design_availability": design.as_availability(),
            "design_revision": int(metadata.get("design_revision") or 0),
            "polar_grid": metadata.get("polar_grid") or {},
            "rating": metadata.get("rating"),
            "exported_files": metadata.get("exported_files") or [],
            "auto_export_completed_at": metadata.get("auto_export_completed_at"),
            "auto_export_formats": metadata.get("auto_export_formats") or {},
            "raw_results_file": metadata.get("raw_results_file"),
            "mesh_artifact_file": metadata.get("mesh_artifact_file"),
            "log_tail": metadata.get("log_tail") or [],
            "symmetry": metadata.get("symmetry") or {},
            "solve_path": metadata.get("solve_path"),
            "axisymmetric_eligibility_reasons": metadata.get(
                "axisymmetric_eligibility_reasons"
            ) or [],
            "solve_wall_time_seconds": metadata.get("solve_wall_time_seconds"),
        }
        if detailed:
            item["updated_at"] = row.get("updated_at")
            item["message"] = row.get("error_message")
        return item


__all__ = [
    "EngineUnavailableError",
    "EventBroker",
    "JobConflictError",
    "JobNotFoundError",
    "JobResourceUnavailableError",
    "JobRuntime",
    "SymmetryValidationError",
    "UnknownEngineError",
]

"""FIFO asyncio job scheduler and solve lifecycle.

The single-slot FIFO queue, queued/running recovery, active-delete rules, and
strong task references port v1 ``server/services/job_runtime.py:23-41,92-208,
259-285,497-574,629-686``. The dry-run solve itself is dispatched with
``asyncio.to_thread`` just like the retained in-process v1 execution model.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
import json
import logging
from typing import Any, Mapping
import uuid

from server.engines.registry import get_engine
from server.jobs.models import SolveRequest
from server.jobs.store import ALLOWED_STATUSES, JobStore


logger = logging.getLogger(__name__)
MAX_LOG_LINES = 200
MAX_LOG_CHARS = 32_000
MAX_LOG_EVENT_CHARS = 2_000
CANCELLED_MESSAGE = "Simulation cancelled by user"


def _now_iso() -> str:
    return datetime.now().isoformat()


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


class _CancelledAtCheckpoint(RuntimeError):
    pass


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

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.events = EventBroker()
        self._queue: deque[str] = deque()
        self._running: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._started = False
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

        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._scheduler_task = None
        self._background_tasks.clear()

    async def submit(self, request: SolveRequest) -> str:
        await self.start()
        engine_name = request.options.engine
        known = {"dryrun", "metal", "bempp", "circsym"}
        if engine_name not in known:
            raise UnknownEngineError(f"Unknown solve engine: {engine_name}")
        if get_engine(engine_name) is None:
            raise EngineUnavailableError(
                f"Solve engine '{engine_name}' is unavailable. "
                "Dry-run solves require WG2_ENABLE_DRYRUN=1."
            )

        job_id = str(uuid.uuid4())
        now = _now_iso()
        request_dump = request.model_dump(mode="json")
        summary = self._config_summary(request)
        event = self.store.create_job(
            {
                "id": job_id,
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
                "label": None,
                "task_metadata": {"log_tail": []},
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

        event = self._transition(
            job_id,
            {
                "stage": "cancelling",
                "stage_message": "Cancellation requested; waiting for current stage checkpoint",
                "cancellation_requested": True,
            },
            "stage",
            {"stage": "cancelling", "message": "Cancellation requested"},
        )
        self.events.publish(event)
        return {
            "message": f"Cancellation requested for job {job_id}",
            "status": "cancelling",
        }

    async def get_job(self, job_id: str) -> dict[str, Any]:
        await self.start()
        return self._serialize_job(self._require_job(job_id), detailed=True)

    async def list_jobs(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        await self.start()
        statuses = self.parse_status_filter(status)
        rows, total = self.store.list_jobs(statuses=statuses, limit=limit, offset=offset)
        return [self._serialize_job(row) for row in rows], total

    async def get_results(self, job_id: str) -> dict[str, Any]:
        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        results = self.store.get_results(job_id)
        if results is None:
            raise JobResourceUnavailableError("Results not available")
        return results

    async def get_mesh_artifact(self, job_id: str) -> str:
        await self.start()
        self._require_job(job_id)
        artifact = self.store.get_mesh_artifact(job_id)
        if not artifact:
            raise JobResourceUnavailableError("No mesh artifact available for this job")
        return artifact

    async def get_log(self, job_id: str) -> str:
        await self.start()
        row = self._require_job(job_id)
        tail = row.get("task_metadata", {}).get("log_tail") or []
        return "\n".join(str(line) for line in tail)

    async def patch_metadata(self, job_id: str, fields: Mapping[str, Any]) -> None:
        await self.start()
        row = self._require_job(job_id)
        changed = dict(fields)
        db_fields: dict[str, Any] = {}
        if "label" in changed:
            db_fields["label"] = changed.pop("label")
        if "script_snapshot" in changed:
            value = changed.pop("script_snapshot")
            db_fields["script_snapshot_json"] = json.dumps(value) if value is not None else None
        metadata = dict(row.get("task_metadata") or {})
        metadata.update(changed)
        if changed:
            db_fields["task_metadata_json"] = json.dumps(metadata)
        if not db_fields:
            return
        changed_ok, event = self.store.update_job_with_event(
            job_id,
            db_fields,
            "metadata",
            {"changed": dict(fields)},
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

    def resume(self, cursor: int) -> list[dict[str, Any]] | None:
        return self.store.replay_events(cursor)

    def _ensure_scheduler(self) -> None:
        if not self._started or not self._queue:
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
        finally:
            self._scheduler_task = None
            if self._queue:
                self._ensure_scheduler()

    async def _run_job(self, job_id: str, row: Mapping[str, Any]) -> None:
        try:
            request = SolveRequest.model_validate(row["config_json"])
            engine = get_engine(request.options.engine)
            if engine is None:
                raise EngineUnavailableError(f"Solve engine '{request.options.engine}' became unavailable")
            event = self._transition(
                job_id,
                {
                    "status": "running",
                    "started_at": _now_iso(),
                    "stage": "mesh",
                    "stage_message": "Building dry-run mesh",
                    "progress": 0.05,
                },
                "started",
                {"status": "running", "stage": "mesh", "progress": 0.05},
            )
            self.events.publish(event)

            design = request.design.model_dump(mode="json")
            delay = request.options.stage_delay_ms / 1000.0
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
            results = await asyncio.to_thread(
                engine.solve,
                design,
                frequency_start_hz=start,
                frequency_end_hz=end,
                num_frequencies=count,
                frequency_spacing=request.options.frequency_spacing,
            )
            self._check_cancelled(job_id)
            await self._stage(
                job_id, "postprocess", 0.90, "Postprocessing synthetic results", delay
            )
            self._check_cancelled(job_id)
            await self._append_log(job_id, "Dry-run result persistence ready")

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
                return
            self.events.publish(event)
        except _CancelledAtCheckpoint:
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

    async def _stage(
        self, job_id: str, stage: str, progress: float, message: str, delay: float
    ) -> None:
        self._check_cancelled(job_id)
        event = self._transition(
            job_id,
            {"stage": stage, "stage_message": message, "progress": progress},
            "stage",
            {"stage": stage, "message": message, "progress": progress},
        )
        self.events.publish(event)
        await self._append_log(job_id, message)
        await asyncio.sleep(delay)
        self._check_cancelled(job_id)

    async def _append_log(self, job_id: str, message: str) -> None:
        row = self._require_job(job_id)
        metadata = dict(row.get("task_metadata") or {})
        lines = [str(line) for line in metadata.get("log_tail") or []]
        line = str(message)[-MAX_LOG_EVENT_CHARS:]
        lines.append(line)
        lines = lines[-MAX_LOG_LINES:]
        while lines and sum(len(item) + 1 for item in lines) > MAX_LOG_CHARS:
            lines.pop(0)
        metadata["log_tail"] = lines
        changed, event = self.store.update_job_with_event(
            job_id,
            {"task_metadata_json": json.dumps(metadata)},
            "log",
            {"chunk": line},
        )
        if changed and event is not None:
            self.events.publish(event)

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
            return
        self.events.publish(event)

    def _check_cancelled(self, job_id: str) -> None:
        row = self.store.get_job_row(job_id)
        if row is None:
            raise JobNotFoundError(job_id)
        if row.get("cancellation_requested") or row.get("status") == "cancelled":
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
            "engine": request.options.engine,
        }

    @staticmethod
    def _frequency_options(request: SolveRequest) -> tuple[float, float, int]:
        simulation = request.design.root.simulation

        def numeric(expr: Any, default: float) -> float:
            value = getattr(expr, "value", None) if expr is not None else None
            return float(value) if value is not None else default

        if request.options.frequency_range is not None:
            start, end = request.options.frequency_range
        else:
            start = numeric(simulation.f1, 200.0)
            end = numeric(simulation.f2, 20_000.0)
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
        item = {
            "id": row.get("id"),
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
            "script_snapshot": row.get("script_snapshot"),
            "rating": metadata.get("rating"),
            "exported_files": metadata.get("exported_files") or [],
            "auto_export_completed_at": metadata.get("auto_export_completed_at"),
            "raw_results_file": metadata.get("raw_results_file"),
            "mesh_artifact_file": metadata.get("mesh_artifact_file"),
            "log_tail": metadata.get("log_tail") or [],
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
    "UnknownEngineError",
]

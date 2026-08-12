"""FIFO asyncio job scheduler and solve lifecycle.

The single-slot FIFO queue, queued/running recovery, active-delete rules, and
strong task references port v1 ``server/services/job_runtime.py:23-41,92-208,
259-285,497-574,629-686``. The dry-run solve itself is dispatched with
``asyncio.to_thread`` just like the retained in-process v1 execution model.
"""

from __future__ import annotations

import asyncio
from collections import deque
import copy
from dataclasses import dataclass, field
from datetime import datetime
import importlib.metadata
import inspect
import json
import logging
import math
import os
import time
from typing import Any, Mapping
import uuid

from server.cadlink.ingest import get_ingestion_record
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig, Expr
from server.design.textcfg import parse
from server.engines.registry import EngineRegistry, create_engine as get_engine
from server.jobs.legacy_design import resolve_job_design
from server.jobs.models import (
    ChannelCombineSpec,
    ImportedGeometrySource,
    ParametricGeometrySource,
    SolveOptions,
    SolveRequest,
)
from server.jobs.store import ALLOWED_STATUSES, JobStore
from server.platform.instance import LOCK_OPEN_FLAGS, lock_exclusive, unlock
from server.solver.imported import (
    ImportedMeshArtifactError,
    ImportedSymmetryUnsupportedError,
    global_frequency_caveat,
    imported_symmetry_from_cut_planes,
    mesh_frequency_validation,
    read_verified_import_mesh,
    requested_max_frequency_hz,
    verify_record_mesh_text,
)
from server.solver.symmetry import resolve_symmetry, validate_symmetry_mode


logger = logging.getLogger(__name__)
MAX_LOG_LINES = 200
MAX_LOG_CHARS = 32_000
MAX_LOG_EVENT_CHARS = 2_000
CANCELLED_MESSAGE = "Simulation cancelled by user"
RUNTIME_PERSIST_INTERVAL_SECONDS = 0.15


def _sort_provisional_frequencies(result: dict[str, Any]) -> None:
    """Keep accumulated chart rows ascending while deltas arrive out of order."""

    frequencies = result.get("frequencies")
    if isinstance(frequencies, list) and len(frequencies) > 1:
        order = sorted(range(len(frequencies)), key=frequencies.__getitem__)
        if order != list(range(len(frequencies))):
            count = len(frequencies)
            result["frequencies"] = [frequencies[index] for index in order]
            for block_name in ("directivity", "directivity_phase"):
                block = result.get(block_name)
                if not isinstance(block, dict):
                    continue
                for plane, rows in block.items():
                    if isinstance(rows, list) and len(rows) == count:
                        block[plane] = [rows[index] for index in order]
            for block_name in ("spl_on_axis", "impedance", "di"):
                block = result.get(block_name)
                if not isinstance(block, dict):
                    continue
                for key, values in block.items():
                    if isinstance(values, list) and len(values) == count:
                        block[key] = [values[index] for index in order]
    channels = result.get("channels")
    if isinstance(channels, dict):
        for channel in channels.values():
            if isinstance(channel, dict):
                _sort_provisional_frequencies(channel)


def _extend_provisional_results(
    current: dict[str, Any], delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Extend the runtime-owned accumulator without recopying prior rows."""

    def append_list(container: dict[str, Any], source: Mapping[str, Any], key: str) -> None:
        incoming = source.get(key)
        if not isinstance(incoming, list):
            return
        existing = container.get(key)
        if isinstance(existing, list):
            existing.extend(copy.deepcopy(incoming))
        else:
            container[key] = copy.deepcopy(incoming)

    append_list(current, delta, "frequencies")
    for block_name in ("directivity", "directivity_phase"):
        incoming_block = delta.get(block_name)
        if not isinstance(incoming_block, Mapping):
            continue
        block = current.get(block_name)
        block = block if isinstance(block, dict) else {}
        for plane, rows in incoming_block.items():
            if not isinstance(rows, list):
                continue
            existing = block.get(str(plane))
            if isinstance(existing, list):
                existing.extend(copy.deepcopy(rows))
            else:
                block[str(plane)] = copy.deepcopy(rows)
        current[block_name] = block

    for block_name in ("spl_on_axis", "impedance", "di"):
        incoming_block = delta.get(block_name)
        if not isinstance(incoming_block, Mapping):
            continue
        block = current.get(block_name)
        block = block if isinstance(block, dict) else {}
        for key in incoming_block:
            append_list(block, incoming_block, str(key))
        current[block_name] = block

    incoming_channels = delta.get("channels")
    if isinstance(incoming_channels, Mapping):
        channels = current.get("channels")
        channels = channels if isinstance(channels, dict) else {}
        for channel_id, channel_delta in incoming_channels.items():
            if not isinstance(channel_delta, Mapping):
                continue
            existing = channels.get(str(channel_id))
            channel = existing if isinstance(existing, dict) else {}
            channels[str(channel_id)] = _extend_provisional_results(
                channel, channel_delta
            )
        current["channels"] = channels

    if isinstance(delta.get("channel_order"), list):
        current["channel_order"] = copy.deepcopy(delta["channel_order"])
    if isinstance(delta.get("metadata"), Mapping):
        prior_metadata = current.get("metadata")
        current["metadata"] = {
            **(
                dict(prior_metadata)
                if isinstance(prior_metadata, Mapping)
                else {}
            ),
            **copy.deepcopy(dict(delta["metadata"])),
        }
    _sort_provisional_frequencies(current)
    return current


def merge_provisional_results(
    current: Mapping[str, Any] | None, delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one frequency-shaped result delta without mutating either input."""

    if current is None:
        return copy.deepcopy(dict(delta))
    return _extend_provisional_results(copy.deepcopy(dict(current)), delta)


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


def _stored_solve_options(config: Mapping[str, Any]) -> SolveOptions:
    """Read native v2 options or translate the solve fields persisted by v1."""

    nested = config.get("options")
    nested_options = nested if isinstance(nested, Mapping) else {}
    geometry = config.get("geometry")
    if isinstance(config.get("design"), Mapping) or isinstance(geometry, Mapping):
        current = {
            key: value
            for key, value in nested_options.items()
            if key in SolveOptions.model_fields
        }
        return SolveOptions.model_validate(current)

    legacy: dict[str, Any] = {}
    for key in (
        "frequency_range",
        "num_frequencies",
        "frequency_spacing",
        "frequencies_hz",
        "verbose",
        "mesh_validation_mode",
        "polar_config",
    ):
        value = config.get(key)
        if value is not None:
            legacy[key] = value
    backend = str(config.get("solver_backend") or config.get("device_mode") or "auto")
    normalized_backend = backend.strip().lower()
    if "metal" in normalized_backend:
        legacy["engine"] = "metal"
    elif "bem" in normalized_backend:
        legacy["engine"] = "bempp"
    elif "dry" in normalized_backend:
        legacy["engine"] = "dryrun"
    else:
        legacy["engine"] = "auto"
    return SolveOptions.model_validate(legacy)


def _replay_request(row: Mapping[str, Any]) -> SolveRequest:
    """Build the faithful request represented by a native or imported row."""

    config = row.get("config_json")
    config = config if isinstance(config, Mapping) else {}
    if isinstance(config.get("design"), Mapping) or isinstance(
        config.get("geometry"), Mapping
    ):
        return SolveRequest.model_validate(config).model_copy(deep=True)

    resolution = resolve_job_design(row.get("script_snapshot"), config)
    if not resolution.reopenable or resolution.snapshot is None:
        raise JobConflictError(
            resolution.reason or "This run has no stored design and cannot be retried"
        )
    metadata = row.get("task_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return SolveRequest.model_validate(
        {
            "design": resolution.snapshot["design"],
            "design_snapshot": resolution.snapshot,
            "options": _stored_solve_options(config).model_dump(mode="json"),
            "label": row.get("label"),
            "design_revision": int(metadata.get("design_revision") or 0),
        }
    )


class JobNotFoundError(LookupError):
    """Requested job is absent from persistence."""


class JobConflictError(RuntimeError):
    """Requested mutation conflicts with the persisted lifecycle state."""


class JobResourceUnavailableError(RuntimeError):
    """Requested job result or artifact is not persisted."""


class JobMeshDiscardedError(JobResourceUnavailableError):
    """A terminal unrated job no longer retains an instantly available mesh."""


@dataclass(frozen=True)
class MeshArtifactDownload:
    """Mesh download body plus provenance that belongs in response headers."""

    content: str
    regenerated: bool = False
    mesher_version: str | None = None


class EngineUnavailableError(RuntimeError):
    """The requested solve engine is known but unavailable."""


class UnknownEngineError(ValueError):
    """The request named an engine outside the registry."""


class SymmetryValidationError(ValueError):
    """The requested solve domain requires a mirror plane the geometry lacks."""


class ImportedSolveRefusal(ValueError):
    """A typed imported-geometry eligibility refusal at the runtime boundary."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(f"{reason_code}: {message}")


@dataclass(frozen=True)
class _ImportedSubmission:
    record: dict[str, Any]
    msh_text: str
    mesh_stats: dict[str, Any]
    symmetry_metadata: dict[str, Any]
    global_frequency_caveat: dict[str, Any] | None
    anchor_design_id: str | None
    anchor_snapshot: dict[str, Any] | None


def _imported_record_sources(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources = record.get("sources")
    if not isinstance(sources, list) or not all(
        isinstance(source, Mapping) for source in sources
    ):
        raise ImportedSolveRefusal(
            "ingest_record_incomplete",
            "the ingestion record has no authoritative source inventory; re-ingest the CAD return",
        )
    return [dict(source) for source in sources]


def _imported_symmetry_metadata(
    record: Mapping[str, Any], requested: str
) -> dict[str, Any]:
    symmetry = record.get("symmetry")
    symmetry = symmetry if isinstance(symmetry, Mapping) else {}
    cut_planes = [str(plane) for plane in symmetry.get("cut_planes") or []]
    try:
        resolved = imported_symmetry_from_cut_planes(cut_planes)
    except ImportedSymmetryUnsupportedError as exc:
        raise ImportedSolveRefusal(
            "imported_symmetry_unsupported",
            str(exc),
            details={"cut_planes": cut_planes},
        ) from exc
    if requested not in {"auto", resolved.mode}:
        raise ImportedSolveRefusal(
            "imported_symmetry_mismatch",
            f"requested symmetry {requested!r} does not match the ingestion artifact's "
            f"actual cut planes {cut_planes} ({resolved.mode})",
            details={"requested": requested, "resolved": resolved.mode, "cut_planes": cut_planes},
        )
    return {
        "requested": requested,
        "resolved": resolved.mode,
        "resolved_quadrants": resolved.quadrants,
        "native_symmetry_plane": resolved.native_plane,
        "cut_planes": cut_planes,
        "source": "cad-ingestion-cut-planes",
    }


def _validate_imported_polar_grid(
    geometry: ImportedGeometrySource,
    request: SolveRequest,
    record: Mapping[str, Any],
) -> None:
    del geometry
    derivation = record.get("polar_grid_derivation")
    derivation = derivation if isinstance(derivation, Mapping) else {}
    axes = derivation.get("axes")
    if not isinstance(axes, Mapping) or set(axes) != {
        "horizontal",
        "vertical",
        "diagonal",
    }:
        raise ImportedSolveRefusal(
            "ingest_record_incomplete",
            "ingestion polar derivation must contain horizontal, vertical, and diagonal axes; re-ingest the CAD return",
        )
    requested_start, requested_end, _count = request.options.polar_config.angle_range
    enabled_axes = set(request.options.polar_config.enabled_axes)
    for axis_name, raw_axis in axes.items():
        if not isinstance(raw_axis, Mapping):
            raise ImportedSolveRefusal(
                "ingest_record_incomplete",
                f"ingestion polar derivation axis {axis_name!r} is invalid; re-ingest the CAD return",
            )
        minimum = float(raw_axis.get("minimum_deg", -180.0))
        maximum = float(raw_axis.get("maximum_deg", 180.0))
        pinned = minimum <= -180.0 and maximum >= 180.0
        if pinned and axis_name not in enabled_axes:
            raise ImportedSolveRefusal(
                "polar_grid_narrowing",
                f"polar request disables pinned axis {axis_name!r}; ingestion requires it enabled over [-180, 180] degrees",
                details={
                    "axis": str(axis_name),
                    "required": [minimum, maximum],
                    "enabled_axes": sorted(enabled_axes),
                },
            )
        if requested_start > minimum or requested_end < maximum:
            raise ImportedSolveRefusal(
                "polar_grid_narrowing",
                f"polar request narrows pinned axis {axis_name!r}: ingestion requires "
                f"[{minimum:g}, {maximum:g}] degrees but request gives "
                f"[{requested_start:g}, {requested_end:g}]",
                details={
                    "axis": str(axis_name),
                    "required": [minimum, maximum],
                    "requested": [requested_start, requested_end],
                },
            )


def _validate_imported_frequency(
    request: SolveRequest,
    record: Mapping[str, Any],
    active_source_ids: set[str],
) -> dict[str, Any] | None:
    validation = mesh_frequency_validation(record)
    requested_max = requested_max_frequency_hz(request)
    per_source = validation.get("per_source")
    per_source = per_source if isinstance(per_source, Mapping) else {}
    exceeded: dict[str, float] = {}
    for source_id in sorted(active_source_ids):
        item = per_source.get(source_id)
        if not isinstance(item, Mapping):
            raise ImportedSolveRefusal(
                "ingest_record_incomplete",
                f"ingestion record has no frequency-validity result for active source {source_id!r}",
            )
        limit = float(item.get("effective_max_valid_frequency_hz", 0.0))
        if requested_max > limit:
            exceeded[source_id] = limit
    if exceeded:
        detail = ", ".join(
            f"{source_id} (limit {limit:g} Hz)"
            for source_id, limit in exceeded.items()
        )
        raise ImportedSolveRefusal(
            "source_frequency_limit",
            f"requested maximum {requested_max:g} Hz exceeds active source limits: {detail}",
            details={"requested_max_frequency_hz": requested_max, "sources": exceeded},
        )
    return global_frequency_caveat(request, record)


class _CancelledAtCheckpoint(RuntimeError):
    pass


class _RuntimeOwnershipLock:
    """Exclude a second scheduler from recovering or executing the same DB."""

    def __init__(self, store: JobStore) -> None:
        self.path = store.db_path.with_suffix(store.db_path.suffix + ".runtime.lock")
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, LOCK_OPEN_FLAGS, 0o600)
        try:
            lock_exclusive(descriptor)
        except BlockingIOError:
            os.close(descriptor)
            raise JobConflictError(
                "Another job runtime already owns this Waveguide Generator database"
            ) from None
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            unlock(descriptor)
        finally:
            os.close(descriptor)


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
    """Fan durable lifecycle events and ephemeral result deltas to subscribers."""

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
        cadlink_store: CadLinkStore | None = None,
        persistence_interval_seconds: float = RUNTIME_PERSIST_INTERVAL_SECONDS,
    ) -> None:
        self.store = store
        self.cadlink_store = cadlink_store
        self.engine_registry = engine_registry or EngineRegistry(factory=get_engine)
        self.events = EventBroker()
        self._queue: deque[str] = deque()
        self._running: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._pending_updates: dict[str, _PendingRuntimeUpdate] = {}
        # Live rows are process-local by design. The final result remains the
        # sole durable/canonical artifact committed by ``complete_job``.
        self._partial_results: dict[str, dict[str, Any]] = {}
        self._persistence_interval_seconds = max(
            0.01, float(persistence_interval_seconds)
        )
        self._started = False
        self._shutting_down = False
        self._start_lock = asyncio.Lock()
        self._ownership = _RuntimeOwnershipLock(store)

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
            await asyncio.to_thread(self._ownership.acquire)
            try:
                await asyncio.to_thread(self.store.initialize)
                queued, recovery_events = await asyncio.to_thread(
                    self.store.recover_on_startup, "Server restarted during execution"
                )
                await asyncio.to_thread(
                    self.store.prune_terminal_jobs,
                    retention_days=30,
                    max_terminal_jobs=1000,
                )
            except BaseException:
                await asyncio.to_thread(self._ownership.release)
                raise
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
        await asyncio.to_thread(self._ownership.release)
        self._partial_results.clear()
        self._started = False

    async def submit(self, request: SolveRequest) -> str:
        await self.start()
        imported: _ImportedSubmission | None = None
        if isinstance(request.geometry, ImportedGeometrySource):
            imported = await self._prepare_imported_submission(request)
        engine_name = request.options.engine
        known = {"auto", "dryrun", "metal", "bempp"}
        if isinstance(request.geometry, ImportedGeometrySource) and engine_name == "circsym":
            raise ImportedSolveRefusal(
                "imported_circsym_unsupported",
                "imported geometry supports Metal full 3-D solves only; CircSym is unavailable",
            )
        if engine_name not in known:
            raise UnknownEngineError(f"Unknown solve engine: {engine_name}")

        if imported is not None:
            symmetry_metadata = imported.symmetry_metadata
            if engine_name in {"bempp", "dryrun"}:
                raise ImportedSolveRefusal(
                    "imported_engine_unsupported",
                    f"imported geometry supports Metal only; engine {engine_name!r} is unavailable",
                    details={"engine": engine_name},
                )
            if engine_name == "auto":
                engine_name = "metal"
                request = request.model_copy(deep=True)
                request.options.engine = engine_name
        else:
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
            assert isinstance(request.geometry, ParametricGeometrySource)
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
        if isinstance(request.geometry, ParametricGeometrySource):
            assert request.design_snapshot is not None
            script_snapshot = request.design_snapshot.model_dump(mode="json")
        else:
            assert imported is not None
            script_snapshot = imported.anchor_snapshot
        task_metadata: dict[str, Any] = {
            "log_tail": [],
            "design_revision": request.design_revision,
            "polar_grid": polar_grid,
            "symmetry": symmetry_metadata,
        }
        if imported is not None:
            task_metadata["imported_geometry"] = {
                "ingest_id": request.geometry.ingest_id,
                "anchor_design_id": imported.anchor_design_id,
                "global_frequency_caveat": imported.global_frequency_caveat,
            }
        job_record = {
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
            "has_mesh_artifact": imported is not None,
            "mesh_stats": imported.mesh_stats if imported is not None else None,
            "label": request.label,
            "script_snapshot": script_snapshot,
            "task_metadata": task_metadata,
        }
        initial_event = ("queued", {"status": "queued", "progress": 0.0})
        if imported is None:
            event = self.store.create_job(job_record, initial_event=initial_event)
        else:
            event = await asyncio.to_thread(
                self.store.create_job,
                job_record,
                initial_event=initial_event,
                mesh_artifact=imported.msh_text,
            )
        self._queue.append(job_id)
        if event is not None:
            self.events.publish(event)
        self._ensure_scheduler()
        return job_id

    async def _prepare_imported_submission(
        self, request: SolveRequest
    ) -> _ImportedSubmission:
        geometry = request.geometry
        assert isinstance(geometry, ImportedGeometrySource)
        if self.cadlink_store is None:
            raise ImportedSolveRefusal(
                "ingest_registry_unavailable",
                "the CAD ingestion registry is unavailable to this job runtime",
            )
        record = await asyncio.to_thread(
            get_ingestion_record, self.cadlink_store, geometry.ingest_id
        )
        if record is None:
            raise ImportedSolveRefusal(
                "ingest_not_found",
                f"ingestion record {geometry.ingest_id!r} does not exist",
                details={"ingest_id": geometry.ingest_id},
            )
        mismatches = {
            field: {"request": getattr(geometry, field), "record": record.get(field)}
            for field in ("manifest_sha256", "artifact_sha256")
            if getattr(geometry, field) != record.get(field)
        }
        if mismatches:
            raise ImportedSolveRefusal(
                "ingest_sha_mismatch",
                "request hashes do not match the immutable ingestion record",
                details=mismatches,
            )

        sources = _imported_record_sources(record)
        source_by_id = {str(source.get("id")): source for source in sources}
        requested_skips = set(geometry.skipped_source_ids)
        unknown_skips = requested_skips - set(source_by_id)
        required_skips = sorted(
            source_id
            for source_id in requested_skips
            if bool(source_by_id.get(source_id, {}).get("required"))
        )
        if unknown_skips:
            raise ImportedSolveRefusal(
                "unknown_skipped_source",
                f"skipped_source_ids names unknown record sources {sorted(unknown_skips)}",
            )
        if required_skips:
            raise ImportedSolveRefusal(
                "required_source_skipped",
                f"required sources cannot be skipped: {required_skips}",
                details={"source_ids": required_skips},
            )
        recorded_skips = set(record.get("skipped_source_ids") or [])
        if recorded_skips != requested_skips:
            raise ImportedSolveRefusal(
                "skipped_sources_mismatch",
                "request skipped_source_ids must match the sources excluded by ingestion",
                details={
                    "request": sorted(requested_skips),
                    "record": sorted(recorded_skips),
                },
            )
        active_sources = set(source_by_id) - requested_skips
        driven_sources = {
            source_id
            for channel in geometry.drive_channels
            for source_id in channel.source_ids
        }
        if driven_sources != active_sources:
            raise ImportedSolveRefusal(
                "drive_source_coverage",
                "drive channels must cover every non-skipped ingestion source exactly once",
                details={
                    "missing": sorted(active_sources - driven_sources),
                    "extra": sorted(driven_sources - active_sources),
                },
            )
        recorded_sizes = record.get("mesh_sizes")
        if not isinstance(recorded_sizes, Mapping):
            raise ImportedSolveRefusal(
                "ingest_record_incomplete",
                "the ingestion record has no authoritative mesh sizes; re-ingest the CAD return",
            )
        if geometry.mesh.model_dump(mode="json") != dict(recorded_sizes):
            raise ImportedSolveRefusal(
                "mesh_sizes_mismatch",
                "request mesh sizes do not match the sizes used to create the ingestion artifact",
            )

        report_sha = str(record.get("report_sha256") or "")
        acknowledged = set(geometry.acknowledged_findings)
        blocking_ids = [
            str(finding.get("id"))
            for finding in record.get("findings") or []
            if isinstance(finding, Mapping) and bool(finding.get("blocking"))
        ]
        missing_findings = [
            finding_id
            for finding_id in blocking_ids
            if f"{report_sha}:{finding_id}" not in acknowledged
        ]
        if missing_findings:
            raise ImportedSolveRefusal(
                "unacknowledged_findings",
                f"blocking ingestion findings require current report acknowledgements: {missing_findings}",
                details={
                    "report_sha256": report_sha,
                    "missing_finding_ids": missing_findings,
                },
            )

        acoustic_domain = str(
            record.get("acoustic_domain") or record.get("sim_type") or "free-space"
        ).strip().lower()
        if bool(record.get("infinite_baffle")) or acoustic_domain not in {
            "free-space",
            "free_space",
            "freestanding",
        }:
            raise ImportedSolveRefusal(
                "imported_infinite_baffle_unsupported",
                "imported geometry supports free-space solves only; infinite baffle is unavailable",
            )
        fem_volumes = (
            (record.get("evidence") or {}).get("fem_air_volumes")
            if isinstance(record.get("evidence"), Mapping)
            else []
        ) or []
        required_fem = [
            volume
            for volume in fem_volumes
            if not isinstance(volume, Mapping) or volume.get("required", True)
        ]
        if required_fem and not geometry.exterior_only:
            raise ImportedSolveRefusal(
                "fem_required",
                "the CAD return declares required FEM air volumes; set exterior_only=true "
                "to explicitly exclude them from this Phase 2 solve",
            )

        symmetry_metadata = _imported_symmetry_metadata(
            record, request.options.symmetry
        )
        _validate_imported_polar_grid(geometry, request, record)
        polar = request.options.polar_config
        if "diagonal" in polar.enabled_axes and not math.isclose(
            polar.inclination, 45.0
        ):
            raise ImportedSolveRefusal(
                "imported_diagonal_inclination_unsupported",
                "Phase 2 imported solves support diagonal observation only at the default 45-degree inclination",
                details={"inclination": polar.inclination},
            )
        caveat = _validate_imported_frequency(request, record, active_sources)
        try:
            msh_text = await asyncio.to_thread(read_verified_import_mesh, record)
        except ImportedMeshArtifactError as exc:
            raise ImportedSolveRefusal(
                "ingest_mesh_unavailable",
                str(exc),
            ) from exc
        mesh_record = record.get("mesh")
        mesh_stats = (
            dict(mesh_record.get("stats") or {})
            if isinstance(mesh_record, Mapping)
            else {}
        )
        anchor = record.get("anchor")
        anchor = anchor if isinstance(anchor, Mapping) else {}
        record_anchor_design_id = (
            str(anchor["design_id"]) if anchor.get("design_id") else None
        )
        anchor_design_id: str | None = None
        anchor_snapshot: dict[str, Any] | None = None
        if record_anchor_design_id is not None:
            design_row = await asyncio.to_thread(
                self.cadlink_store.get_design, record_anchor_design_id
            )
            if design_row is not None:
                anchor_design_id = record_anchor_design_id
                try:
                    parsed = await asyncio.to_thread(
                        parse, str(design_row["snapshot_text"])
                    )
                    anchor_snapshot = {
                        "version": 1,
                        "design": parsed.design.model_dump(mode="json"),
                    }
                except Exception:
                    logger.warning(
                        "Could not parse anchor design snapshot %s for imported job",
                        record_anchor_design_id,
                    )
        return _ImportedSubmission(
            record=dict(record),
            msh_text=msh_text,
            mesh_stats=mesh_stats,
            symmetry_metadata=symmetry_metadata,
            global_frequency_caveat=caveat,
            anchor_design_id=anchor_design_id,
            anchor_snapshot=anchor_snapshot,
        )

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

    async def retry(self, job_id: str) -> str:
        """Submit a faithful replay from the target's persisted request config."""

        await self.start()
        row = self._require_job(job_id)
        request = _replay_request(row)
        request.parent_job_id = job_id
        return await self.submit(request)

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

    async def recombine_results(
        self, job_id: str, spec: ChannelCombineSpec
    ) -> dict[str, Any]:
        """Recompute the combined channel from stored bases and persist it."""

        from server.solver.recombine import recombine_stored_results

        await self.start()
        row = self._require_job(job_id)
        if row["status"] != "complete":
            raise JobConflictError(f"Job not complete. Current status: {row['status']}")
        results_text = await asyncio.to_thread(self.store.get_results_text, job_id)
        if results_text is None:
            raise JobResourceUnavailableError("Results not available")
        bases = await asyncio.to_thread(self.store.get_channel_bases, job_id)
        if bases is None:
            raise JobResourceUnavailableError(
                "This job has no stored channel bases; re-solve to enable "
                "crossover changes without a new solve"
            )
        request = _replay_request(row)
        results = json.loads(results_text)
        updated = await asyncio.to_thread(
            recombine_stored_results, results, bases, spec, request
        )
        await asyncio.to_thread(self.store.store_results, job_id, updated)
        return updated

    async def get_partial_results(self, job_id: str) -> dict[str, Any]:
        """Return the current process-local provisional result snapshot."""

        await self.start()
        self._require_job(job_id)
        item = self._partial_results.get(job_id)
        if item is None:
            raise JobResourceUnavailableError("Partial results not available")
        return copy.deepcopy(item)

    def partial_result_messages(self) -> list[dict[str, Any]]:
        """Full live snapshots sent once when a jobs socket connects."""

        return [
            {
                "v": 1,
                "kind": "partialResult",
                "jobId": job_id,
                "revision": int(item["revision"]),
                "snapshot": True,
                "result": copy.deepcopy(item["result"]),
            }
            for job_id, item in self._partial_results.items()
        ]

    async def get_mesh_artifact_download(self, job_id: str) -> MeshArtifactDownload:
        await self.start()
        row = self._require_job(job_id)
        # Real MSH artifacts are multi-megabyte text blobs. Reading one through
        # SQLite on the event loop stalls both WS channels for the full copy.
        artifact = await asyncio.to_thread(self.store.get_mesh_artifact, job_id)
        if artifact:
            return MeshArtifactDownload(content=artifact)

        metadata = row.get("task_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if not metadata.get("mesh_discarded_at"):
            raise JobResourceUnavailableError("No mesh artifact is available for this job")

        config = row.get("config_json")
        config = config if isinstance(config, Mapping) else {}
        geometry = config.get("geometry")
        if isinstance(geometry, Mapping) and geometry.get("type") == "imported":
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "this job was solved from imported geometry, so its stored parametric "
                "anchor is not the solve mesh."
            )

        resolution = resolve_job_design(row.get("script_snapshot"), config)
        if not resolution.reopenable or resolution.snapshot is None:
            reason = resolution.reason or "the job has no stored design"
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                f"{reason}"
            )

        snapshot = resolution.snapshot
        raw_design = snapshot.get("design", snapshot)
        try:
            design = DesignConfig.model_validate(raw_design)
        except Exception as exc:
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "the stored design no longer resolves in the current server "
                f"({exc})."
            ) from exc

        try:
            from server.mesh.builder import build_solver_mesh

            # Regeneration deliberately uses the server's currently pinned mesher,
            # which may differ from the version that produced the solve. Force a
            # fresh build for every download and never put it back in the job store:
            # retention would only discard it again.
            regenerated = await build_solver_mesh(
                design,
                _stored_solve_options(config),
                force_rebuild=True,
            )
            content = regenerated.get("msh_text")
            if not isinstance(content, str) or not content:
                raise RuntimeError("the current mesher returned no MSH payload")
        except Exception as exc:
            raise JobMeshDiscardedError(
                "Mesh artifact was discarded by retention and cannot be regenerated: "
                "the stored design no longer resolves with the current mesher "
                f"({exc})."
            ) from exc

        try:
            mesher_version = importlib.metadata.version("hornlab-waveguide-mesher")
        except importlib.metadata.PackageNotFoundError:  # pragma: no cover - build failed first
            mesher_version = None
        return MeshArtifactDownload(
            content=content,
            regenerated=True,
            mesher_version=mesher_version,
        )

    async def get_mesh_artifact(self, job_id: str) -> str:
        return (await self.get_mesh_artifact_download(job_id)).content

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
        self._partial_results.pop(job_id, None)
        self.events.publish(event)

    async def clear_failed(self) -> list[str]:
        await self.start()
        ids, events = self.store.delete_jobs_by_status_with_events(["error"])
        for job_id in ids:
            self._remove_from_queue(job_id)
            self._running.discard(job_id)
            self._partial_results.pop(job_id, None)
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
            imported_record: dict[str, Any] | None = None
            if isinstance(request.geometry, ParametricGeometrySource):
                request.design.root.mesh.quadrants = Expr(value=float(resolved_quadrants))
            else:
                if self.cadlink_store is None:
                    raise ImportedSolveRefusal(
                        "ingest_registry_unavailable",
                        "the CAD ingestion registry is unavailable during execution",
                    )
                imported_record = await asyncio.to_thread(
                    get_ingestion_record,
                    self.cadlink_store,
                    request.geometry.ingest_id,
                )
                if imported_record is None:
                    raise ImportedSolveRefusal(
                        "ingest_not_found",
                        f"ingestion record {request.geometry.ingest_id!r} disappeared before execution",
                    )
                job_msh_text = await asyncio.to_thread(
                    self.store.get_mesh_artifact, job_id
                )
                job_artifact_present = job_msh_text is not None
                try:
                    if job_msh_text is not None:
                        verify_record_mesh_text(imported_record, job_msh_text)
                    else:
                        job_msh_text = await asyncio.to_thread(
                            read_verified_import_mesh, imported_record
                        )
                except ImportedMeshArtifactError as exc:
                    raise ImportedSolveRefusal(
                        "ingest_mesh_unavailable",
                        str(exc),
                    ) from exc
                imported_record = {
                    **imported_record,
                    "_execution_msh_text": job_msh_text,
                    "_execution_mesh_source": (
                        "job-artifact" if job_artifact_present else "imports-cache"
                    ),
                }
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
                    job_id,
                    request,
                    engine,
                    symmetry_metadata=symmetry_metadata,
                    imported_record=imported_record,
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
            self._partial_results.pop(job_id, None)
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
        imported_record: Mapping[str, Any] | None = None,
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

        def result_callback(index: int, result: dict[str, Any]) -> None:
            # Native callbacks run in the solver worker. Provisional state and
            # subscriber queues remain owned by the asyncio thread.
            loop.call_soon_threadsafe(
                self._accept_partial_result,
                job_id,
                int(index),
                result,
            )

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
            if "result_cb" in inspect.signature(engine.run).parameters:
                run_kwargs["result_cb"] = result_callback
            if (
                imported_record is not None
                and "imported_record" in inspect.signature(engine.run).parameters
            ):
                run_kwargs["imported_record"] = imported_record
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
        channel_bases = getattr(outcome, "channel_bases", None)
        if channel_bases is not None:
            try:
                await asyncio.to_thread(
                    self.store.store_channel_bases, job_id, channel_bases
                )
            except Exception as exc:
                # Recombination degrades to "re-solve to change the crossover";
                # the solve itself must not fail over an optional artifact.
                logger.warning(
                    "Channel-bases persistence failed for job %s: %s", job_id, exc
                )

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
        self._partial_results.pop(job_id, None)

    def _accept_partial_result(
        self, job_id: str, index: int, result: Mapping[str, Any]
    ) -> None:
        """Merge and fan out a solver-thread frequency callback."""

        if job_id not in self._running or index < 0:
            return
        revision = index + 1
        previous = self._partial_results.get(job_id)
        previous_revision = int(previous["revision"]) if previous is not None else 0
        if revision <= previous_revision:
            return
        merged = (
            _extend_provisional_results(previous["result"], result)
            if previous is not None
            else copy.deepcopy(dict(result))
        )
        self._partial_results[job_id] = {"revision": revision, "result": merged}
        self.events.publish(
            {
                "v": 1,
                "kind": "partialResult",
                "jobId": job_id,
                "revision": revision,
                "snapshot": False,
                "result": copy.deepcopy(dict(result)),
            }
        )

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
        self._partial_results.pop(job_id, None)
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
        if isinstance(request.geometry, ParametricGeometrySource):
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
        return {
            "formula_type": "cad-import",
            "geometry_type": "imported",
            "frequency_range": [start, end],
            "num_frequencies": count,
            "frequency_source": (
                "explicit_list"
                if request.options.frequencies_hz is not None
                else "generated_grid"
            ),
            "engine": request.options.engine,
            "design_revision": 0,
            "ingest_id": request.geometry.ingest_id,
            "drive_channel_ids": [
                channel.id for channel in request.geometry.drive_channels
            ],
            "combine": (
                {
                    "id": request.geometry.combine.id,
                    "members": list(request.geometry.combine.members),
                    "crossovers_hz": list(request.geometry.combine.crossovers_hz),
                }
                if request.geometry.combine is not None
                else None
            ),
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
        if isinstance(request.geometry, ParametricGeometrySource):
            metadata["design_revision"] = request.design_revision
        else:
            metadata["geometry_type"] = "imported"
            metadata["ingest_id"] = request.geometry.ingest_id
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

        simulation = (
            request.design.root.simulation
            if isinstance(request.geometry, ParametricGeometrySource)
            else None
        )

        def numeric(expr: Any, default: float) -> float:
            value = getattr(expr, "value", None) if expr is not None else None
            return float(value) if value is not None else default

        explicit = request.options.frequencies_hz
        if explicit is not None:
            return float(explicit[0]), float(explicit[-1]), len(explicit)
        if request.options.frequency_range is not None:
            start, end = request.options.frequency_range
        else:
            if isinstance(request.geometry, ImportedGeometrySource):
                raise ValueError("imported geometry requires an explicit frequency range")
            assert simulation is not None
            start = numeric(simulation.f1, 200.0)
            end = numeric(simulation.f2, 20_000.0)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("frequency bounds must be finite")
        count = request.options.num_frequencies
        if count is None:
            if isinstance(request.geometry, ImportedGeometrySource):
                raise ValueError("imported geometry requires an explicit frequency count")
            assert simulation is not None
            count = int(round(numeric(simulation.num_frequencies, 24.0)))
        count = max(1, min(401, count))
        if start <= 0 or end <= start:
            start, end = 200.0, 20_000.0
        return float(start), float(end), count

    @staticmethod
    def _serialize_job(row: Mapping[str, Any], *, detailed: bool = False) -> dict[str, Any]:
        metadata = row.get("task_metadata") if isinstance(row.get("task_metadata"), dict) else {}
        stored_config = row.get("config_json") or {}
        # An imported v1 job reaches the client already translated, so reopen,
        # rerun, compare and export need no legacy branch; one that cannot be
        # translated keeps its original bytes and carries the reason instead.
        geometry = stored_config.get("geometry")
        imported = isinstance(geometry, Mapping) and geometry.get("type") == "imported"
        design = resolve_job_design(row.get("script_snapshot"), row.get("config_json"))
        if imported:
            imported_metadata = metadata.get("imported_geometry")
            imported_metadata = (
                imported_metadata if isinstance(imported_metadata, Mapping) else {}
            )
            anchor_design_id = imported_metadata.get("anchor_design_id")
            anchor_note = (
                f" The ingestion anchor is registered design {anchor_design_id}."
                if anchor_design_id
                else ""
            )
            design_availability = {
                "reopenable": False,
                "source": "cad-import",
                "reason_code": "imported_geometry",
                "reason": (
                    "This job was solved from an immutable CAD-return ingestion and cannot "
                    "be reopened as a parametric design."
                    + anchor_note
                ),
                "note": None,
            }
        else:
            design_availability = design.as_availability()
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
            "solve_options": _stored_solve_options(stored_config).model_dump(mode="json"),
            "has_results": bool(row.get("has_results")),
            "has_mesh_artifact": bool(row.get("has_mesh_artifact")),
            "label": row.get("label"),
            "error_message": row.get("error_message"),
            "cancellation_requested": bool(row.get("cancellation_requested")),
            "mesh_stats": row.get("mesh_stats"),
            "script_snapshot": design.snapshot
            if design.snapshot is not None
            else row.get("script_snapshot"),
            "design_availability": design_availability,
            "design_revision": int(metadata.get("design_revision") or 0),
            "polar_grid": metadata.get("polar_grid") or {},
            "rating": metadata.get("rating"),
            "exported_files": metadata.get("exported_files") or [],
            "auto_export_completed_at": metadata.get("auto_export_completed_at"),
            "auto_export_formats": metadata.get("auto_export_formats") or {},
            "raw_results_file": metadata.get("raw_results_file"),
            "mesh_artifact_file": metadata.get("mesh_artifact_file"),
            "results_discarded_at": metadata.get("results_discarded_at"),
            "mesh_discarded_at": metadata.get("mesh_discarded_at"),
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
    "ImportedSolveRefusal",
    "JobConflictError",
    "JobMeshDiscardedError",
    "JobNotFoundError",
    "JobResourceUnavailableError",
    "JobRuntime",
    "MeshArtifactDownload",
    "SymmetryValidationError",
    "UnknownEngineError",
    "merge_provisional_results",
]

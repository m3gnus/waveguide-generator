"""Post-solve exterior-field plane evaluation from retained surface traces."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import importlib
import json
import logging
import math
import os
from pathlib import Path
import struct
import tempfile
from typing import Any

import numpy as np
from numpy.typing import NDArray

from server.jobs.models import FieldPlaneRequest, FieldPlaneSpec
from server.jobs.store import JobStore
from server.mesh.artifact import mesh_text_sha256

from .combine import raw_channel_weights
from .field_traces_store import (
    ArtifactCorrupt,
    ArtifactMissing,
    BEMPP_FIELD_TRACE_BACKEND,
    FieldTraceBackend,
    METAL_FIELD_TRACE_BACKEND,
)
from .metal_permit import MetalLease, MetalPermit


logger = logging.getLogger(__name__)

FIELD_PLANE_ORDERING = "v-major-row-major"
FIELD_PLANE_RESPONSE_VERSION = 2
FIELD_PLANE_TIMEOUT_ENV = "WG2_FIELD_PLANE_TIMEOUT_SECONDS"
DEFAULT_FIELD_PLANE_TIMEOUT_SECONDS = 120.0
NO_SYNTHESIS_REVISION = hashlib.sha256(
    b"field-plane-synthesis:none"
).hexdigest()[:16]

#: Backends whose degraded-assembly warning has already been logged. Dragging
#: the plane re-evaluates continuously, so this must not warn per request.
_WARNED_BACKENDS: set[str] = set()


class FieldPlaneJobNotFound(LookupError):
    """The requested job does not exist."""


class FieldPlaneJobIncomplete(RuntimeError):
    """The requested job has not completed."""


class FieldPlaneUnsupported(ValueError):
    """The job's solve path cannot provide exterior field planes."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        remedy: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remedy = remedy


class FieldPlaneInvalidSelection(ValueError):
    """The frequency or response selector is not available in the artifact."""


class FieldPlaneTimedOut(TimeoutError):
    """The configured field evaluation deadline elapsed."""


@dataclass(frozen=True, slots=True)
class FieldPlaneEvaluation:
    frequency_hz: float
    pressure: NDArray[np.complex64]
    geometry_sha256: str
    synthesis_revision: str
    symmetry_plane: str | None


@dataclass(frozen=True, slots=True)
class _FieldBackendAPI:
    load_mesh: Callable[..., Any]
    evaluate_exterior_from_traces: Callable[..., Any]


def _field_backend_status(backend: FieldTraceBackend) -> Mapping[str, Any]:
    """Probe only the backend required by a loaded trace artifact."""

    if backend == METAL_FIELD_TRACE_BACKEND:
        from .metal import metal_status

        return metal_status()
    if backend == BEMPP_FIELD_TRACE_BACKEND:
        from .bempp import bempp_status

        return bempp_status()
    raise ArtifactCorrupt(f"field-trace backend {backend!r} is unsupported")


def _load_field_backend(backend: FieldTraceBackend) -> _FieldBackendAPI:
    status = _field_backend_status(backend)
    if not bool(status.get("available")):
        reason = str(status.get("reason") or "runtime capability probe failed")
        raise FieldPlaneUnsupported(
            f"Field-trace artifact requires backend {backend!r}, but that "
            f"backend is unavailable: {reason}"
        )

    warning = status.get("warning")
    if warning and backend not in _WARNED_BACKENDS:
        # Same rule the solve path follows: a degraded assembly backend is
        # never silent. Field evaluation re-runs on every drag, so say it once
        # per process rather than once per request.
        _WARNED_BACKENDS.add(backend)
        logger.warning("%s", warning)

    package_name = (
        "hornlab_metal_bem"
        if backend == METAL_FIELD_TRACE_BACKEND
        else "hornlab_bempp_bem"
    )
    try:
        package = importlib.import_module(package_name)
        mesh_loader = getattr(package, "load_mesh")
        evaluator = getattr(package, "evaluate_exterior_from_traces")
    except (ImportError, OSError, AttributeError) as exc:
        raise FieldPlaneUnsupported(
            f"Field-trace artifact requires backend {backend!r}, but installed "
            f"{package_name} does not provide trace field evaluation"
        ) from exc
    return _FieldBackendAPI(mesh_loader, evaluator)


def field_plane_timeout_seconds(
    environ: Mapping[str, str] | None = None,
) -> float:
    """Resolve the positive finite field evaluation timeout."""

    values = os.environ if environ is None else environ
    raw = str(values.get(FIELD_PLANE_TIMEOUT_ENV, "")).strip()
    if not raw:
        return DEFAULT_FIELD_PLANE_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(f"{FIELD_PLANE_TIMEOUT_ENV} must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError(f"{FIELD_PLANE_TIMEOUT_ENV} must be finite and positive")
    return timeout


def field_plane_points(plane: FieldPlaneSpec) -> NDArray[np.float64]:
    """Build centred points in v-major rows, with u varying fastest.

    Flattened index ``j * nx + i`` is located at::

        origin + u[i] * axis_u + v[j] * axis_v

    where u spans ``[-width/2, +width/2]`` and v spans
    ``[-height/2, +height/2]`` including both edges.
    """

    origin = np.asarray(plane.origin_m, dtype=np.float64)
    axis_u = np.asarray(plane.axis_u, dtype=np.float64)
    axis_v = np.asarray(plane.axis_v, dtype=np.float64)
    u = np.linspace(-plane.width_m / 2.0, plane.width_m / 2.0, plane.nx)
    v = np.linspace(-plane.height_m / 2.0, plane.height_m / 2.0, plane.ny)
    points = (
        origin[None, None, :]
        + u[None, :, None] * axis_u[None, None, :]
        + v[:, None, None] * axis_v[None, None, :]
    )
    return np.ascontiguousarray(points.reshape(plane.nx * plane.ny, 3))


def encode_field_plane_response(
    job_id: str,
    request: FieldPlaneRequest,
    evaluation: FieldPlaneEvaluation,
) -> bytes:
    """Encode the versioned JSON header plus interleaved complex32 payload."""

    header = {
        "version": FIELD_PLANE_RESPONSE_VERSION,
        "request_id": request.request_id,
        "job_id": job_id,
        "frequency_index": request.frequency_index,
        "frequency_hz": evaluation.frequency_hz,
        "nx": request.plane.nx,
        "ny": request.plane.ny,
        "ordering": FIELD_PLANE_ORDERING,
        "phase_convention": "solver_exp_plus_ikr",
        "pressure_unit": "Pa",
        "response_id": request.response.id,
        "geometry_sha256": evaluation.geometry_sha256,
        "synthesis_revision": evaluation.synthesis_revision,
        "symmetry_plane": evaluation.symmetry_plane,
    }
    header_bytes = json.dumps(
        header,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    pressure = np.asarray(evaluation.pressure, dtype=np.complex64).reshape(-1)
    pairs = np.empty((pressure.size, 2), dtype="<f4")
    pairs[:, 0] = pressure.real
    pairs[:, 1] = pressure.imag
    return struct.pack("<I", len(header_bytes)) + header_bytes + pairs.tobytes(order="C")


class FieldPlaneService:
    """Load retained traces, synthesize a response, and evaluate one plane."""

    def __init__(
        self,
        store: JobStore,
        permit: MetalPermit,
        *,
        solve_queued: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        mesh_cache_entries: int = 3,
    ) -> None:
        if isinstance(mesh_cache_entries, bool) or not 2 <= mesh_cache_entries <= 3:
            raise ValueError("mesh_cache_entries must be 2 or 3")
        self.store = store
        self.permit = permit
        self._solve_queued = solve_queued or (lambda: False)
        self.timeout_seconds = (
            field_plane_timeout_seconds()
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds must be finite and positive")
        self._mesh_cache_entries = mesh_cache_entries
        self._mesh_cache: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def evaluate(
        self, job_id: str, request: FieldPlaneRequest
    ) -> FieldPlaneEvaluation:
        row = await asyncio.to_thread(self.store.get_job_row, job_id)
        if row is None:
            raise FieldPlaneJobNotFound(job_id)
        if row.get("status") != "complete":
            raise FieldPlaneJobIncomplete(
                f"Job not complete. Current status: {row.get('status')}"
            )
        metadata = row.get("task_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        solve_path = metadata.get("solve_path")
        unavailable_reason = metadata.get("unavailable_reason")
        unsupported_reasons = {
            "unsupported_solve_mode",
            "unsupported_axisymmetric_formulation",
            "unsupported_coupled_infinite_baffle",
        }
        if unavailable_reason == "unsupported_axisymmetric_formulation":
            raise FieldPlaneUnsupported(
                "Axisymmetric meridian solves do not retain exterior field traces.",
                code="unsupported_axisymmetric_formulation",
                remedy=(
                    "Set Solver mode to Full 3D and re-solve with Metal or BEMPP. "
                    "For a coupled infinite-baffle design, also change Simulation "
                    "type to Free-standing."
                ),
            )
        if unavailable_reason == "unsupported_coupled_infinite_baffle":
            raise FieldPlaneUnsupported(
                "Coupled infinite-baffle solves do not retain exterior field traces.",
                code="unsupported_coupled_infinite_baffle",
                remedy=(
                    "Set Simulation type to Free-standing and re-solve with Metal "
                    "or BEMPP."
                ),
            )
        if (
            solve_path not in {None, "full-3d"}
            or unavailable_reason in unsupported_reasons
        ):
            raise FieldPlaneUnsupported(
                "Field planes require a completed full-3D solve"
            )
        if metadata.get("field_plane_available") is not True:
            reason = str(unavailable_reason or "solve_predates_traces")
            raise ArtifactMissing(
                f"field traces are unavailable for job {job_id}: {reason}"
            )

        points = field_plane_points(request.plane)
        lease = await self.permit.acquire_field(
            request.request_id,
            solve_queued=bool(self._solve_queued()),
        )
        work = asyncio.create_task(
            asyncio.to_thread(
                self._evaluate_sync,
                job_id,
                row,
                request,
                points,
            ),
            name=f"wg2-field-plane-{request.request_id}",
        )
        try:
            evaluation = await asyncio.wait_for(
                asyncio.shield(work),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self._release_when_finished(work, lease)
            raise FieldPlaneTimedOut(
                f"Field evaluation exceeded {self.timeout_seconds:g} seconds"
            ) from exc
        except asyncio.CancelledError:
            self._release_when_finished(work, lease)
            raise
        except BaseException:
            await lease.release()
            raise
        await lease.release()
        return evaluation

    def _release_when_finished(
        self, work: asyncio.Task[FieldPlaneEvaluation], lease: MetalLease
    ) -> None:
        """Keep the lease until uncancellable ``to_thread`` work really exits."""

        async def finish() -> None:
            try:
                await asyncio.gather(work, return_exceptions=True)
            finally:
                await lease.release()

        task = asyncio.create_task(finish(), name="wg2-field-plane-permit-release")
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _evaluate_sync(
        self,
        job_id: str,
        row: Mapping[str, Any],
        request: FieldPlaneRequest,
        points: NDArray[np.float64],
    ) -> FieldPlaneEvaluation:
        (
            mesh_text,
            frequency_hz,
            k_real,
            symmetry_plane,
            pressure,
            neumann,
            backend,
            synthesis_revision,
        ) = self._load_response_traces(job_id, row, request)
        backend_api = _load_field_backend(backend)
        geometry_sha256 = mesh_text_sha256(mesh_text)
        mesh = self._cached_mesh(
            backend,
            backend_api,
            job_id,
            geometry_sha256,
            mesh_text,
            symmetry_plane,
        )
        evaluation_kwargs: dict[str, Any] = {"symmetry_plane": symmetry_plane}
        if backend == METAL_FIELD_TRACE_BACKEND:
            evaluation_kwargs["check_open_edges"] = True
        evaluated = backend_api.evaluate_exterior_from_traces(
            mesh,
            frequency_hz,
            k_real,
            pressure,
            neumann,
            points,
            **evaluation_kwargs,
        )
        values = np.asarray(evaluated)
        if values.shape != (points.shape[0],):
            raise RuntimeError(
                "field evaluator returned an unexpected pressure grid shape"
            )
        return FieldPlaneEvaluation(
            frequency_hz=float(frequency_hz),
            pressure=np.ascontiguousarray(values, dtype=np.complex64),
            geometry_sha256=geometry_sha256,
            synthesis_revision=synthesis_revision,
            symmetry_plane=symmetry_plane,
        )

    def _load_response_traces(
        self,
        job_id: str,
        row: Mapping[str, Any],
        request: FieldPlaneRequest,
    ) -> tuple[
        str,
        float,
        float,
        str | None,
        NDArray[Any],
        NDArray[Any],
        FieldTraceBackend,
        str,
    ]:
        response_id = request.response.id
        if response_id.startswith("channel:"):
            channel_id = response_id.removeprefix("channel:")
            return (
                *self._load_channel(job_id, request.frequency_index, channel_id),
                NO_SYNTHESIS_REVISION,
            )

        raw_channels = self._raw_channel_ids(row)
        if len(raw_channels) == 1:
            return (
                *self._load_channel(
                    job_id,
                    request.frequency_index,
                    raw_channels[0],
                ),
                NO_SYNTHESIS_REVISION,
            )
        combine = self._current_combine(job_id)
        members, crossovers_hz, gains_db, delays_s = self._combine_parameters(combine)
        synthesis_revision = self._synthesis_revision(
            members,
            crossovers_hz,
            gains_db,
            delays_s,
        )
        unknown = [member for member in members if member not in raw_channels]
        if unknown:
            raise ArtifactCorrupt(
                f"system synthesis references unknown retained channels: {unknown}"
            )

        loaded = [
            self._load_channel(job_id, request.frequency_index, member)
            for member in members
        ]
        first = loaded[0]
        mesh_text, frequency_hz, k_real, symmetry_plane = first[:4]
        backend = first[6]
        for channel in loaded[1:]:
            if (
                channel[0] != mesh_text
                or channel[1] != frequency_hz
                or channel[2] != k_real
                or channel[3] != symmetry_plane
                or channel[6] != backend
            ):
                raise ArtifactCorrupt(
                    "retained system channels do not share geometry and frequency metadata"
                )
        weights = raw_channel_weights(
            np.asarray([frequency_hz], dtype=np.float64),
            members,
            crossovers_hz,
            gains_db,
            delays_s,
        )
        pressure = np.zeros_like(np.asarray(first[4]), dtype=np.complex128)
        neumann = np.zeros_like(np.asarray(first[5]), dtype=np.complex128)
        for member, channel in zip(members, loaded, strict=True):
            weight = complex(weights[member][0])
            pressure += weight * np.asarray(channel[4], dtype=np.complex128)
            neumann += weight * np.asarray(channel[5], dtype=np.complex128)
        return (
            mesh_text,
            frequency_hz,
            k_real,
            symmetry_plane,
            pressure,
            neumann,
            backend,
            synthesis_revision,
        )

    def _load_channel(
        self, job_id: str, frequency_index: int, channel_id: str
    ) -> tuple[
        str,
        float,
        float,
        str | None,
        NDArray[Any],
        NDArray[Any],
        FieldTraceBackend,
    ]:
        try:
            loaded = self.store.load_field_traces(
                job_id,
                frequency_index,
                channel_id,
            )
        except IndexError as exc:
            raise FieldPlaneInvalidSelection(str(exc)) from exc
        except KeyError as exc:
            raise FieldPlaneInvalidSelection(str(exc)) from exc
        return loaded  # type: ignore[return-value]

    @staticmethod
    def _raw_channel_ids(row: Mapping[str, Any]) -> list[str]:
        config = row.get("config_json")
        config = config if isinstance(config, Mapping) else {}
        geometry = config.get("geometry")
        if not isinstance(geometry, Mapping) or geometry.get("type") != "imported":
            return ["default"]
        raw_channels = geometry.get("drive_channels")
        if not isinstance(raw_channels, list):
            raise ArtifactCorrupt("stored imported job has no drive-channel definition")
        channel_ids = [
            str(channel.get("id"))
            for channel in raw_channels
            if isinstance(channel, Mapping) and channel.get("id")
        ]
        if len(channel_ids) != len(raw_channels) or len(set(channel_ids)) != len(
            channel_ids
        ):
            raise ArtifactCorrupt("stored imported drive-channel ids are invalid")
        return channel_ids

    def _current_combine(self, job_id: str) -> Mapping[str, Any]:
        results = self.store.get_results(job_id)
        if not isinstance(results, Mapping):
            raise ArtifactCorrupt("completed job has no stored result metadata")
        channels = results.get("channels")
        channels = channels if isinstance(channels, Mapping) else {}
        order = results.get("channel_order")
        ordered = [str(value) for value in order] if isinstance(order, list) else []
        candidates = [*reversed(ordered), *reversed([str(key) for key in channels])]
        seen: set[str] = set()
        for channel_id in candidates:
            if channel_id in seen:
                continue
            seen.add(channel_id)
            channel = channels.get(channel_id)
            metadata = channel.get("metadata") if isinstance(channel, Mapping) else None
            combine = metadata.get("combine") if isinstance(metadata, Mapping) else None
            if isinstance(combine, Mapping):
                return combine
        raise FieldPlaneInvalidSelection(
            "response 'system' requires stored multi-channel combine metadata"
        )

    @staticmethod
    def _combine_parameters(
        combine: Mapping[str, Any],
    ) -> tuple[list[str], list[float], dict[str, float], dict[str, float]]:
        try:
            members_raw = combine["members"]
            crossovers_raw = combine["crossovers_hz"]
            level_match = combine["level_match"]
            gains_raw = level_match["gains_db"]
            delays_raw = combine["delays_ms"]
            if (
                not isinstance(members_raw, list)
                or not isinstance(crossovers_raw, list)
                or not isinstance(gains_raw, Mapping)
                or not isinstance(delays_raw, Mapping)
            ):
                raise TypeError
            members = [str(value) for value in members_raw]
            crossovers = [float(value) for value in crossovers_raw]
            gains = {name: float(gains_raw[name]) for name in members}
            delays = {name: float(delays_raw[name]) / 1000.0 for name in members}
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactCorrupt("stored system synthesis metadata is invalid") from exc
        numeric = [*crossovers, *gains.values(), *delays.values()]
        if (
            len(members) < 2
            or len(set(members)) != len(members)
            or len(crossovers) != len(members) - 1
            or any(not name for name in members)
            or any(not math.isfinite(value) for value in numeric)
            or any(value <= 0.0 for value in crossovers)
            or any(
                later <= earlier
                for earlier, later in zip(crossovers, crossovers[1:])
            )
        ):
            raise ArtifactCorrupt("stored system synthesis metadata is invalid")
        return members, crossovers, gains, delays

    @staticmethod
    def _synthesis_revision(
        members: list[str],
        crossovers_hz: list[float],
        gains_db: Mapping[str, float],
        delays_s: Mapping[str, float],
    ) -> str:
        def canonical_float(value: float) -> float:
            return 0.0 if value == 0.0 else value

        canonical = {
            "members": members,
            "crossovers_hz": [canonical_float(value) for value in crossovers_hz],
            "gains_db": [
                [name, canonical_float(gains_db[name])] for name in members
            ],
            "delays_s": [
                [name, canonical_float(delays_s[name])] for name in members
            ],
        }
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(b"field-plane-synthesis:combine:" + encoded)
        return digest.hexdigest()[:16]

    def _cached_mesh(
        self,
        backend: FieldTraceBackend,
        backend_api: _FieldBackendAPI,
        job_id: str,
        geometry_sha256: str,
        mesh_text: str,
        symmetry_plane: str | None,
    ) -> Any:
        key = (backend, job_id, geometry_sha256)
        cached = self._mesh_cache.pop(key, None)
        if cached is not None:
            self._mesh_cache[key] = cached
            return cached
        with tempfile.TemporaryDirectory(prefix="wg2-field-plane-") as directory:
            mesh_path = Path(directory) / "mesh.msh"
            mesh_path.write_text(mesh_text, encoding="utf-8")
            mesh = backend_api.load_mesh(
                mesh_path,
                native_symmetry_plane=symmetry_plane,
            )
        self._mesh_cache[key] = mesh
        while len(self._mesh_cache) > self._mesh_cache_entries:
            self._mesh_cache.popitem(last=False)
        return mesh


__all__ = [
    "DEFAULT_FIELD_PLANE_TIMEOUT_SECONDS",
    "FIELD_PLANE_ORDERING",
    "FIELD_PLANE_RESPONSE_VERSION",
    "FIELD_PLANE_TIMEOUT_ENV",
    "FieldPlaneEvaluation",
    "FieldPlaneInvalidSelection",
    "FieldPlaneJobIncomplete",
    "FieldPlaneJobNotFound",
    "FieldPlaneService",
    "FieldPlaneTimedOut",
    "FieldPlaneUnsupported",
    "NO_SYNTHESIS_REVISION",
    "encode_field_plane_response",
    "field_plane_points",
    "field_plane_timeout_seconds",
]

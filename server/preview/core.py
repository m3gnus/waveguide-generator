"""Transport-independent implementation of WS-PROTOCOL v1 preview state."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from itertools import count
import json
import math
import threading
import time
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from server.design.migrate import apply_migrations
from server.design.schema import DesignConfig
from server.protocol.frame import DEFAULT_MAX_FRAME_BYTES, FrameError, encode
from server.preview.translate import design_to_mesher_config


PROTOCOL_VERSION = 1
HEARTBEAT_SECONDS = 15
CLOSE_UNSUPPORTED_VERSION = 4400
CLOSE_ORIGIN_REJECTED = 4401
CLOSE_HEARTBEAT_TIMEOUT = 4408
CLOSE_TOO_LARGE = 4413
CLOSE_RESTARTING = 1012

_EPOCHS = count(1)
_INJECTED_BUILDER_NAMESPACES = count(1)


class PreviewComputeService:
    """Application-owned executor with an explicit bounded shutdown policy."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_cache_entries: int = 16,
        max_cache_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="wg-preview"
        )
        self._closed = False
        self._max_cache_entries = max(0, max_cache_entries)
        self._max_cache_bytes = max(0, max_cache_bytes)
        self._cache_bytes = 0
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    async def run(self, function: Callable[..., bytes], *args: Any) -> bytes:
        if self._closed:
            raise RuntimeError("preview service is shutting down")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, function, *args)

    def get_or_build(
        self,
        key: str,
        builder: Callable[[], Any],
        *,
        size_of: Callable[[Any], int],
    ) -> Any:
        """Reuse immutable geometry for exact undo/redo and repeated requests."""

        with self._cache_lock:
            cached = self._cache.pop(key, None)
            if cached is not None:
                self._cache[key] = cached
                return cached[0]
        value = builder()
        size = max(0, int(size_of(value)))
        if (
            self._max_cache_entries == 0
            or self._max_cache_bytes == 0
            or size > self._max_cache_bytes
        ):
            return value
        with self._cache_lock:
            existing = self._cache.pop(key, None)
            if existing is not None:
                self._cache_bytes -= existing[1]
            self._cache[key] = (value, size)
            self._cache_bytes += size
            while (
                len(self._cache) > self._max_cache_entries
                or self._cache_bytes > self._max_cache_bytes
            ):
                _evicted_key, (_evicted_value, evicted_size) = self._cache.popitem(
                    last=False
                )
                self._cache_bytes -= evicted_size
        return value

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._cache_lock:
            self._cache.clear()
            self._cache_bytes = 0
        # Queued work is cancelled and running native work is boundedly
        # abandoned; ThreadPoolExecutor workers finish in the background.
        self._executor.shutdown(wait=False, cancel_futures=True)


class PreviewTransport(Protocol):
    """Minimal transport surface used by the protocol engine and test fakes."""

    async def receive(self) -> str | bytes | Mapping[str, Any] | None: ...

    async def send_json(self, message: Mapping[str, Any]) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int) -> None: ...


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    v: Literal[1]
    kind: str
    epoch: int = Field(ge=1, strict=True)
    seq: int = Field(ge=0, strict=True)
    design_revision: int = Field(alias="designRevision", ge=0, strict=True)


class _PreviewMessage(_Message):
    kind: Literal["preview"]
    design: dict[str, Any]
    lod: Literal["coarse", "fine"]
    #: Whether the viewport is showing the curvature heatmap. Absent from a
    #: client that predates the flag, which then never asks for the sections --
    #: the same answer as any client sitting in one of the other seven display
    #: modes, and the one the viewport already renders a placeholder for.
    curvature: bool = False


class _CurveMessage(_Message):
    kind: Literal["curve"]
    curve_id: str = Field(alias="curveId", min_length=1)
    points: list[list[float]]

    @field_validator("points")
    @classmethod
    def _finite_2d_points(cls, points: list[list[float]]) -> list[list[float]]:
        if any(len(point) != 2 for point in points):
            raise ValueError("every curve point must contain exactly two coordinates")
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValueError("curve points must be finite")
        return points


class _Request:
    def __init__(
        self,
        *,
        kind: Literal["preview", "curve"],
        seq: int,
        design_revision: int,
        lod: str,
        design: DesignConfig | None = None,
        curve_id: str | None = None,
        points: list[list[float]] | None = None,
        curvature: bool = False,
    ) -> None:
        self.kind = kind
        self.seq = seq
        self.design_revision = design_revision
        self.lod = lod
        self.design = design
        self.curve_id = curve_id
        self.points = points
        self.curvature = curvature


def preview_options(lod: Literal["coarse", "fine"], *, curvature: bool = True):
    """Map protocol LOD names onto the mesher's stable preview presets.

    The preview is error-bounded, not segment-driven: ``build_preview_geometry``
    takes chord-error, normal-step, and silhouette targets and *derives* its own
    azimuthal and axial sampling, overwriting ``mesh.angular_segments`` and
    ``mesh.length_segments`` in the config it is handed. Those design fields
    therefore size the exported and solved mesh only, which is what the
    "Surface sampling" section now says.

    Do not forward them as ``PreviewOptionsV1`` overrides to make the old
    "live preview tessellation" label true. ``min_silhouette_segments`` is a
    fidelity floor, not an ATH segment count: the design default of 40 sits
    below both presets' floors (64 coarse, 128 fine), so wiring it would let a
    solve-mesh field silently move the viewport off the LOD contract that
    ``lod`` is supposed to pin. The WS test named
    ``..._size_the_export_mesh_and_not_the_preview`` fails if this changes.

    ``curvature`` is the viewport's answer to "am I showing the curvature
    heatmap". Analytic curvature is evaluated on the dense canonical master --
    two full passes over a 385x512 grid on a fine freestanding R-OSSE -- and
    then decimated to the ~97x256 the frame actually carries, which costs 84 ms
    of a 256 ms build and 432 KiB of a 3.07 MiB frame. Seven of the eight
    display modes never read a curvature section, so only the eighth pays.
    """

    from hornlab_mesher.preview.api import PreviewOptionsV1

    return PreviewOptionsV1(
        lod=lod,
        include_inner=True,
        include_outer=True,
        include_enclosure=True,
        include_source_cap=True,
        include_rear_cap=True,
        include_curvature=lod == "fine" and curvature,
    )


#: Mesh fields the preview builder overwrites before it samples anything.
#: ``hornlab_mesher.preview.api._lod_config`` assigns ``angular_segments`` and
#: ``length_segments`` from the LOD preset and drops the camel-case aliases the
#: translation emits, which is the same fact ``preview_options`` documents at
#: length: these size the exported and solved mesh, never the viewport.
_PREVIEW_OVERWRITTEN_MESH_FIELDS = frozenset(
    {"angular_segments", "angularSegments", "length_segments", "lengthSegments"}
)


def _cache_relevant_config(config: Mapping[str, Any]) -> Any:
    """Drop config the builder discards, so it cannot invalidate the cache.

    Two designs that differ only in "Surface sampling" produce byte-identical
    frames, and those controls sit next to the solve settings a user adjusts
    while looking at the viewport. Keying on them charged a full rebuild --
    tens to hundreds of milliseconds -- for a change the preview never sees.
    """

    mesh = config.get("mesh")
    if not isinstance(mesh, Mapping):
        return config
    if not _PREVIEW_OVERWRITTEN_MESH_FIELDS.intersection(mesh):
        return config
    relevant = dict(config)
    relevant["mesh"] = {
        name: value
        for name, value in mesh.items()
        if name not in _PREVIEW_OVERWRITTEN_MESH_FIELDS
    }
    return relevant


def _geometry_nbytes(geometry: Any) -> int:
    total = 0
    for surface in geometry.surfaces:
        for field in (
            "positions",
            "indices",
            "normals",
            "curvature_mean",
            "curvature_principal",
        ):
            values = getattr(surface, field, None)
            if isinstance(values, np.ndarray):
                total += values.nbytes
    return total


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def _fidelity_header(metadata: Mapping[str, Any]) -> dict[str, Any]:
    per_surface = metadata.get("fidelity")
    if not isinstance(per_surface, Mapping):
        per_surface = {}
    chord_values: list[float] = []
    normal_values: list[float] = []
    requested_chord_values: list[float] = []
    requested_normal_values: list[float] = []
    requested_silhouette_values: list[int] = []
    silhouette_values: list[int] = []
    unmeasured_intervals = 0
    chord_measurement_complete = True
    cap_limited = False
    for measured in per_surface.values():
        if not isinstance(measured, Mapping):
            continue
        chord = measured.get(
            "max_chord_error_mm_achieved", measured.get("max_chord_error_mm")
        )
        normal = measured.get(
            "max_normal_step_deg_achieved", measured.get("max_normal_step_deg")
        )
        if isinstance(chord, (int, float)) and math.isfinite(float(chord)):
            chord_values.append(max(0.0, float(chord)))
        measurement_complete = measured.get("measurement_complete")
        if measurement_complete is False or not (
            isinstance(chord, (int, float)) and math.isfinite(float(chord))
        ):
            chord_measurement_complete = False
        raw_unmeasured = measured.get(
            "unmeasured_interval_count", measured.get("unmeasured_intervals", 0)
        )
        if isinstance(raw_unmeasured, int) and not isinstance(raw_unmeasured, bool):
            unmeasured_intervals += max(0, raw_unmeasured)
        if isinstance(normal, (int, float)) and math.isfinite(float(normal)):
            normal_values.append(max(0.0, float(normal)))
        requested_chord = measured.get("max_chord_error_mm_requested")
        requested_normal = measured.get("max_normal_step_deg_requested")
        requested_silhouette = measured.get("min_silhouette_segments_requested")
        if isinstance(requested_chord, (int, float)) and math.isfinite(
            float(requested_chord)
        ):
            requested_chord_values.append(max(0.0, float(requested_chord)))
        if isinstance(requested_normal, (int, float)) and math.isfinite(
            float(requested_normal)
        ):
            requested_normal_values.append(max(0.0, float(requested_normal)))
        if isinstance(requested_silhouette, int) and not isinstance(
            requested_silhouette, bool
        ):
            requested_silhouette_values.append(max(0, requested_silhouette))
        achieved_silhouette = measured.get("silhouette_segments_achieved")
        if isinstance(achieved_silhouette, int) and not isinstance(
            achieved_silhouette, bool
        ):
            silhouette_values.append(max(0, achieved_silhouette))
        cap_limited = cap_limited or measured.get("vertex_cap_limited") is True
    if not per_surface:
        chord_measurement_complete = True
    achieved_chord = (
        max(chord_values, default=0.0) if chord_measurement_complete else None
    )
    achieved_normal = max(normal_values, default=0.0)
    silhouette = min(silhouette_values, default=0)
    requested_chord = max(
        requested_chord_values,
        default=achieved_chord if achieved_chord is not None else 0.0,
    )
    requested_normal = max(requested_normal_values, default=achieved_normal)
    requested_silhouette = max(requested_silhouette_values, default=silhouette)
    missed = (
        not chord_measurement_complete
        or (achieved_chord is not None and achieved_chord > requested_chord)
        or achieved_normal > requested_normal
        or silhouette < requested_silhouette
    )
    return {
        "maxChordErrorMmRequested": requested_chord,
        "maxNormalStepDegRequested": requested_normal,
        "minSilhouetteSegmentsRequested": requested_silhouette,
        "maxChordErrorMmAchieved": achieved_chord,
        "chordMeasurementComplete": chord_measurement_complete,
        "unmeasuredChordIntervals": unmeasured_intervals,
        "maxNormalStepDegAchieved": achieved_normal,
        "minSilhouetteSegmentsAchieved": silhouette,
        "vertexCapLimited": cap_limited or missed,
        "surfaces": _json_safe(per_surface),
    }


def encode_preview_geometry(
    geometry: Any,
    *,
    epoch: int,
    seq: int,
    design_revision: int,
    lod: str,
    eval_ms: float,
) -> bytes:
    """Convert ``PreviewGeometryV1`` f64 arrays at the FRAME-SPEC boundary."""

    arrays: dict[str, np.ndarray] = {}
    surfaces: list[dict[str, Any]] = []
    for index, surface in enumerate(geometry.surfaces):
        prefix = f"surface{index}"
        position_name = f"{prefix}.positions"
        index_name = f"{prefix}.indices"
        normal_name = f"{prefix}.normals"
        positions = np.ascontiguousarray(surface.positions, dtype="<f4")
        indices = np.ascontiguousarray(surface.indices, dtype="<u4").reshape(-1)
        normals = np.ascontiguousarray(surface.normals, dtype="<f4")
        if not np.isfinite(positions).all() or not np.isfinite(normals).all():
            raise ValueError(f"{surface.role}: geometry contains non-finite values")
        arrays[position_name] = positions
        arrays[index_name] = indices
        arrays[normal_name] = normals
        declaration = {
            "role": surface.role,
            "positions": position_name,
            "indices": index_name,
            "normals": normal_name,
            "shading": surface.shading,
            "normalMethod": surface.normal_method,
            "closedPhi": bool(surface.closed_phi),
        }
        # FRAME-SPEC §5 optional curvature sections. Absent until the mesher
        # started producing them, which is why the viewport's curvature heatmap
        # could never populate; a surface that still has none simply omits them
        # and the client says so honestly.
        for field, section in (
            ("curvature_mean", "curvatureMean"),
            ("curvature_principal", "curvaturePrincipal"),
        ):
            values = getattr(surface, field, None)
            if values is None:
                continue
            array = np.ascontiguousarray(values, dtype="<f4").reshape(-1)
            if len(array) != len(positions):
                raise ValueError(
                    f"{surface.role}: {section} is not row-aligned with positions"
                )
            if not np.isfinite(array).all():
                raise ValueError(f"{surface.role}: {section} contains non-finite values")
            name = f"{prefix}.{section}"
            arrays[name] = array
            declaration[section] = name
        surfaces.append(declaration)
    metadata = _json_safe(geometry.metadata)
    return encode(
        {
            "kind": "preview",
            "epoch": epoch,
            "seq": seq,
            "designRevision": design_revision,
            "lod": lod,
            "evalMs": float(eval_ms),
            "surfaces": surfaces,
            "fidelity": _fidelity_header(metadata),
            "previewMetadata": metadata,
        },
        arrays,
    )


def _validation_fields(error: ValidationError, *, prefix: str | None = None) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        location = [str(part) for part in item.get("loc", ())]
        if prefix is not None:
            location.insert(0, prefix)
        path = ".".join(location) or prefix or "message"
        fields[path] = str(item.get("msg", "Invalid value"))
    return fields


def _compute_value_error_fields(request: _Request, error: ValueError) -> dict[str, str]:
    """Attribute mesher failures only when their diagnostic is unambiguous.

    FREEFORM's convexity guard deliberately distinguishes a rectangle-morph
    failure from a defect in the cross-section schedule. Only the former is
    repaired by changing Morph Corner Radius, so keep every other mesher
    ``ValueError`` at the global design/points target.
    """

    message = str(error)
    if request.kind == "preview" and message.startswith(
        "FREEFORM morph to the rectangle target produces a non-convex outline"
    ):
        return {"morph.corner_radius": message}
    return {"design" if request.kind == "preview" else "points": message}


class PreviewProtocol:
    """One-connection state machine, latest-wins per lane (see ``_lane``)."""

    def __init__(
        self,
        *,
        epoch: int | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        preview_builder: Callable[[Mapping[str, Any], Any], Any] | None = None,
        preview_service: PreviewComputeService | None = None,
    ) -> None:
        if (
            isinstance(heartbeat_seconds, bool)
            or not isinstance(heartbeat_seconds, (int, float))
            or not math.isfinite(heartbeat_seconds)
            or heartbeat_seconds <= 0
        ):
            raise ValueError("heartbeat_seconds must be a finite positive number")
        if (
            isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or max_frame_bytes <= 0
        ):
            raise ValueError("max_frame_bytes must be a positive integer")
        resolved_epoch = next(_EPOCHS) if epoch is None else epoch
        if (
            isinstance(resolved_epoch, bool)
            or not isinstance(resolved_epoch, int)
            or resolved_epoch < 1
        ):
            raise ValueError("epoch must be a positive integer")
        self.epoch = resolved_epoch
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.max_frame_bytes = max_frame_bytes
        self._preview_builder = preview_builder
        self._preview_builder_namespace = (
            "default"
            if preview_builder is None
            else f"injected:{next(_INJECTED_BUILDER_NAMESPACES)}"
        )
        self._preview_service = preview_service or PreviewComputeService()
        self._owns_preview_service = preview_service is None
        self._transport: PreviewTransport | None = None
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[str, _Request] = {}
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._background_failure = asyncio.Event()

    def _supervise(self, task: asyncio.Task[Any]) -> None:
        """Wake the receive loop when a background transport operation fails."""

        if task.cancelled():
            return
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            return
        if exception is not None:
            self._background_failure.set()

    def is_current_epoch(self, epoch: object) -> bool:
        """Return whether an incoming or decoded response belongs to this socket."""

        return isinstance(epoch, int) and not isinstance(epoch, bool) and epoch == self.epoch

    async def close_restarting(self) -> None:
        """Close an active connection with WS-PROTOCOL's restart code."""

        await self._close(CLOSE_RESTARTING)

    async def run(self, transport: PreviewTransport) -> None:
        self._transport = transport
        await transport.send_json(
            {
                "v": PROTOCOL_VERSION,
                "kind": "hello",
                "epoch": self.epoch,
                "heartbeatSec": self.heartbeat_seconds,
                "limits": {"maxFrameBytes": self.max_frame_bytes},
            }
        )
        heartbeat = asyncio.create_task(self._heartbeat())
        heartbeat.add_done_callback(self._supervise)
        background_failure = asyncio.create_task(self._background_failure.wait())
        try:
            while not self._closed:
                receive = asyncio.create_task(transport.receive())
                done, _ = await asyncio.wait(
                    {receive, background_failure},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if background_failure in done:
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                    break
                message = receive.result()
                if message is None:
                    break
                if not await self._receive_message(message):
                    break
        except asyncio.CancelledError:
            if not self._closed:
                self._closed = True
                await transport.close(CLOSE_RESTARTING)
            raise
        finally:
            self._closed = True
            heartbeat.cancel()
            background_failure.cancel()
            workers = tuple(self._workers.values())
            for worker in workers:
                worker.cancel()
            await asyncio.gather(
                heartbeat,
                background_failure,
                *workers,
                return_exceptions=True,
            )
            if self._owns_preview_service:
                await self._preview_service.shutdown()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._send_json({"kind": "ping"})

    async def _receive_message(self, raw: str | bytes | Mapping[str, Any]) -> bool:
        try:
            if isinstance(raw, Mapping):
                wire = json.dumps(raw, allow_nan=False, separators=(",", ":")).encode("utf-8")
                value: Any = dict(raw)
            else:
                wire = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                if len(wire) > self.max_frame_bytes:
                    await self._close(CLOSE_TOO_LARGE)
                    return False
                value = json.loads(wire)
        except (
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ) as exc:
            await self._error(None, None, "validation", fields={"message": str(exc)})
            return True
        if len(wire) > self.max_frame_bytes:
            await self._close(CLOSE_TOO_LARGE)
            return False
        if not isinstance(value, dict):
            await self._error(None, None, "validation", fields={"message": "must be an object"})
            return True
        version = value.get("v")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != PROTOCOL_VERSION
        ):
            await self._close(CLOSE_UNSUPPORTED_VERSION)
            return False
        incoming_epoch = value.get("epoch")
        if not isinstance(incoming_epoch, int) or isinstance(incoming_epoch, bool):
            await self._error(
                seq=None,
                revision=None,
                code="validation",
                fields={"epoch": "must be an integer"},
            )
            return True
        if not self.is_current_epoch(incoming_epoch):
            # A receive queued before reconnect is stale by definition. It must
            # not consume the sole pending slot or produce a response.
            return True

        kind = value.get("kind")
        seq = (
            value.get("seq")
            if isinstance(value.get("seq"), int) and not isinstance(value.get("seq"), bool)
            else None
        )
        revision = (
            value.get("designRevision")
            if isinstance(value.get("designRevision"), int)
            and not isinstance(value.get("designRevision"), bool)
            else None
        )
        try:
            if kind == "preview":
                parsed = _PreviewMessage.model_validate(value)
                try:
                    migrated, _applications = apply_migrations(parsed.design)
                    design = DesignConfig.model_validate(migrated)
                except (ValidationError, RecursionError, ValueError) as exc:
                    fields = (
                        _validation_fields(exc, prefix="design")
                        if isinstance(exc, ValidationError)
                        else {"design": str(exc)}
                    )
                    await self._error(
                        parsed.seq,
                        parsed.design_revision,
                        "validation",
                        fields=fields,
                    )
                    return True
                request = _Request(
                    kind="preview",
                    seq=parsed.seq,
                    design_revision=parsed.design_revision,
                    lod=parsed.lod,
                    design=design,
                    curvature=parsed.curvature,
                )
            elif kind == "curve":
                parsed_curve = _CurveMessage.model_validate(value)
                request = _Request(
                    kind="curve",
                    seq=parsed_curve.seq,
                    design_revision=parsed_curve.design_revision,
                    lod="coarse",
                    curve_id=parsed_curve.curve_id,
                    points=parsed_curve.points,
                )
            else:
                raise ValueError(f"unsupported message kind {kind!r}")
        except ValidationError as exc:
            await self._error(seq, revision, "validation", fields=_validation_fields(exc))
            return True
        except ValueError as exc:
            await self._error(seq, revision, "validation", fields={"kind": str(exc)})
            return True
        await self._enqueue(request)
        return True

    @staticmethod
    def _lane(request: _Request) -> str:
        """Name the queue a request waits in: interactive, or refinement.

        Latest-wins with a single queue means the *kinds* of request supersede
        each other, and they must not. A fine refinement is requested 140 ms
        after the last edit and takes several times as long as a coarse frame,
        so a user who resumes dragging used to wait out the whole of it before
        the first frame of the new gesture could even start -- the one moment
        in the session where latency is most visible. Held apart, the drag
        keeps its own cadence and the refinement finishes underneath it.

        Curve echoes are pure re-encoding and never block anything; they share
        the interactive lane because that is where their sender is.
        """

        return "fine" if request.lod == "fine" else "coarse"

    async def _enqueue(self, request: _Request) -> None:
        lane = self._lane(request)
        if lane not in self._workers:
            worker = asyncio.create_task(self._work(lane, request))
            self._workers[lane] = worker
            worker.add_done_callback(self._supervise)
            return
        superseded = self._pending.get(lane)
        if superseded is not None:
            await self._send_json({"kind": "dropped", "seq": superseded.seq})
        self._pending[lane] = request

    async def _work(self, lane: str, request: _Request) -> None:
        current: _Request | None = request
        while current is not None and not self._closed:
            try:
                frame = await self._preview_service.run(self._compute_frame, current)
            except FrameError as exc:
                if exc.rule == "frame-too-large":
                    await self._frame_too_large(current, exc.detail)
                else:
                    await self._error(
                        current.seq,
                        current.design_revision,
                        "validation",
                        fields={
                            "design" if current.kind == "preview" else "points": str(exc)
                        },
                    )
            except ValueError as exc:
                await self._error(
                    current.seq,
                    current.design_revision,
                    "validation",
                    fields=_compute_value_error_fields(current, exc),
                )
            except Exception as exc:  # pragma: no cover - exact failures are dependency-specific
                await self._error(
                    current.seq,
                    current.design_revision,
                    "internal",
                    message=str(exc),
                )
            else:
                if len(frame) > self.max_frame_bytes:
                    await self._frame_too_large(
                        current, f"{len(frame)} > {self.max_frame_bytes}"
                    )
                else:
                    # Transport failures must escape the compute-error handlers
                    # so task supervision can terminate a blocked receive().
                    await self._send_bytes(frame)
            current = self._pending.pop(lane, None)
        self._workers.pop(lane, None)

    def _compute_frame(self, request: _Request) -> bytes:
        began = time.perf_counter()
        if request.kind == "curve":
            points = np.ascontiguousarray(request.points, dtype="<f4")
            elapsed = (time.perf_counter() - began) * 1000.0
            return encode(
                {
                    "kind": "curve",
                    "epoch": self.epoch,
                    "seq": request.seq,
                    "designRevision": request.design_revision,
                    "lod": request.lod,
                    "evalMs": elapsed,
                    "curveId": request.curve_id,
                },
                {"points": points},
            )
        assert request.design is not None
        config = design_to_mesher_config(request.design)
        builder = self._preview_builder
        if builder is None:
            from hornlab_mesher.preview.api import build_preview_geometry

            builder = build_preview_geometry
        cache_key = json.dumps(
            {
                # Test/application-injected builders use the production cache
                # path but have an unambiguous per-protocol namespace.
                "builder": self._preview_builder_namespace,
                "lod": request.lod,
                # Two frames for the same design differ in whether they carry
                # curvature sections, so they cannot share a cache entry. Only
                # fine frames ever have them, so coarse keeps one entry either
                # way and a drag never splits its cache.
                "curvature": request.curvature and request.lod == "fine",
                "config": _cache_relevant_config(config),
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        geometry = self._preview_service.get_or_build(
            cache_key,
            lambda: builder(
                config, preview_options(request.lod, curvature=request.curvature)
            ),
            size_of=_geometry_nbytes,
        )
        elapsed = (time.perf_counter() - began) * 1000.0
        return encode_preview_geometry(
            geometry,
            epoch=self.epoch,
            seq=request.seq,
            design_revision=request.design_revision,
            lod=request.lod,
            eval_ms=elapsed,
        )

    async def _frame_too_large(self, request: _Request, detail: str) -> None:
        """Report an over-budget frame without dropping the connection.

        Closing with 4413 wedges the viewport: the client reconnects, resends
        the same design, and the fine LOD closes the socket again forever while
        coarse keeps succeeding. The size ceiling is a property of one request,
        not of the connection, so it is reported like any other per-request
        failure and the socket stays up on the last frame that fit.
        """

        await self._error(
            request.seq,
            request.design_revision,
            "too-large",
            message=(
                f"preview frame exceeds the {self.max_frame_bytes}-byte limit "
                f"({detail}); reduce the geometry detail or stay at a coarser view"
            ),
        )

    async def _error(
        self,
        seq: int | None,
        revision: int | None,
        code: Literal["validation", "internal", "too-large"],
        *,
        fields: Mapping[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        response: dict[str, Any] = {"kind": "error", "code": code}
        if seq is not None:
            response["seq"] = seq
        if revision is not None:
            response["designRevision"] = revision
        if fields is not None:
            response["fields"] = dict(fields)
        if message is not None:
            response["message"] = message
        await self._send_json(response)

    async def _send_json(self, fields: Mapping[str, Any]) -> None:
        if self._closed or self._transport is None:
            return
        async with self._send_lock:
            if not self._closed and self._transport is not None:
                await self._transport.send_json(
                    {"v": PROTOCOL_VERSION, "epoch": self.epoch, **dict(fields)}
                )

    async def _send_bytes(self, data: bytes) -> None:
        if self._closed or self._transport is None:
            return
        async with self._send_lock:
            if not self._closed and self._transport is not None:
                await self._transport.send_bytes(data)

    async def _close(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        if self._transport is not None:
            async with self._send_lock:
                await self._transport.close(code)


# Name the role explicitly for users/tests that think in terms of an engine.
PreviewProtocolEngine = PreviewProtocol


__all__ = [
    "CLOSE_HEARTBEAT_TIMEOUT",
    "CLOSE_ORIGIN_REJECTED",
    "CLOSE_RESTARTING",
    "CLOSE_TOO_LARGE",
    "CLOSE_UNSUPPORTED_VERSION",
    "HEARTBEAT_SECONDS",
    "PROTOCOL_VERSION",
    "PreviewProtocol",
    "PreviewProtocolEngine",
    "PreviewComputeService",
    "PreviewTransport",
    "encode_preview_geometry",
    "preview_options",
]

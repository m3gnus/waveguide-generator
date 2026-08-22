"""REST correctness path and FastAPI adapter for solve jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.websockets import WebSocketDisconnect, WebSocketState

from server.jobs.events import CLOSE_ORIGIN_REJECTED, JobsProtocol
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.jobs.models import (
    CadIdentityProvenance,
    ChannelCombineSpec,
    ClearFailedResponse,
    DeleteResponse,
    FieldPlaneRequest,
    JobListResponse,
    JobMetadataPatch,
    JobStatusResponse,
    MetadataResponse,
    SolveAccepted,
    SolveRequest,
    StopResponse,
)
from server.solver.errors import RecombineError
from server.solver.field_plane import (
    FieldPlaneInvalidSelection,
    FieldPlaneJobIncomplete,
    FieldPlaneJobNotFound,
    FieldPlaneTimedOut,
    FieldPlaneUnsupported,
    encode_field_plane_response,
)
from server.solver.field_traces_store import ArtifactCorrupt, ArtifactMissing
from server.solver.metal_permit import (
    FieldEvaluationBusy,
    FieldEvaluationSuperseded,
)
from server.jobs.runtime import (
    EngineUnavailableError,
    ImportedSolveRefusal,
    JobConflictError,
    JobMeshDiscardedError,
    JobNotFoundError,
    JobResourceUnavailableError,
    JobRuntime,
    SymmetryValidationError,
    UnknownEngineError,
    resolve_submission,
)
from server.jobs.store import JobStore
from server.engines.registry import EngineRegistry
from server.platform.origin import websocket_request_allowed


class ExtensibleResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ResultProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1]
    wg_version: str
    dependency_shas: dict[str, str]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_identity: Literal["execution"]
    execution_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_solve_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_engine: str
    cad_identity: CadIdentityProvenance | None = None


class ParametricResultEnvelope(ExtensibleResultModel):
    result_kind: Literal["parametric"]
    result_contract_version: Literal[1]
    client_request_id: str | None
    client_metadata: dict[str, JsonValue]
    provenance: ResultProvenance
    metadata: dict[str, Any]


class MultiChannelResultEnvelope(ExtensibleResultModel):
    result_kind: Literal["multi_channel"]
    result_contract_version: Literal[2]
    client_request_id: str | None
    client_metadata: dict[str, JsonValue]
    provenance: ResultProvenance
    metadata: dict[str, Any]
    channels: dict[str, dict[str, Any]]
    channel_order: list[str]


ResultEnvelope = Annotated[
    ParametricResultEnvelope | MultiChannelResultEnvelope,
    Field(discriminator="result_kind"),
]


class RadiationImpedanceAperture(BaseModel):
    name: str
    area_m2: float
    tag: int


class RadiationImpedanceMatrix(BaseModel):
    real: list[list[list[float]]]
    imaginary: list[list[list[float]]]


class RadiationImpedanceTermination(BaseModel):
    aperture_names: list[str]
    real: list[list[float]]
    imaginary: list[list[float]]


class RadiationImpedancePresentation(BaseModel):
    """Plot/export view of the lossless NPZ, always in engineering convention."""

    schema_version: Literal[1]
    quantity: Literal["average_aperture_pressure_per_volume_velocity"]
    units: Literal["Pa*s/m^3"]
    phase_time_convention: Literal["engineering_exp_plus_jwt"]
    frequencies_hz: list[float]
    apertures: list[RadiationImpedanceAperture]
    engineering_matrix: RadiationImpedanceMatrix
    in_phase_termination: RadiationImpedanceTermination


class SolvePlanResponse(BaseModel):
    """The request-specific engine/formulation selected by the runtime."""

    engine: str
    formulation: Literal["axisymmetric", "full-3d"]
    reason: str
    eligibility_reasons: list[str]


class FieldPlaneUnavailableResponse(BaseModel):
    """Backward-compatible, versioned remedy for an unsupported field plane."""

    detail: str
    error_contract_version: Literal[1] = 1
    code: Literal[
        "unsupported_axisymmetric_formulation",
        "unsupported_coupled_infinite_baffle",
    ]
    message: str
    remedy: str


class FieldPlaneStringErrorResponse(BaseModel):
    detail: str


class FieldPlaneValidationErrorResponse(BaseModel):
    detail: list[dict[str, Any]]


FieldPlane422Response = (
    FieldPlaneUnavailableResponse
    | FieldPlaneStringErrorResponse
    | FieldPlaneValidationErrorResponse
)



class _FastAPIJobsTransport:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def receive(self) -> str | bytes | None:
        try:
            message = await self.websocket.receive()
        except WebSocketDisconnect:
            return None
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("text") is not None:
            return message["text"]
        return message.get("bytes")

    async def send_json(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)

    async def close(self, code: int) -> None:
        if self.websocket.application_state != WebSocketState.DISCONNECTED:
            await self.websocket.close(code=code)


def _error_response(
    status_code: int,
    *,
    code: str,
    stage: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    client_request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(
            code=code,
            stage=stage,
            message=message,
            retryable=retryable,
            details=details,
            client_request_id=client_request_id,
        ),
    )


def _recover_client_request_id(body: Any) -> str | None:
    """Recover only an identifier that independently satisfies its wire contract."""

    if not isinstance(body, dict):
        return None
    value = body.get("client_request_id")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        return None
    return normalized


class _JobsContractRoute(APIRoute):
    """Use the public error envelope for solve submission and planning."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        route_handler = super().get_route_handler()
        if self.path not in {"/api/solve", "/api/solve/plan"}:
            return route_handler

        async def solve_contract_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                return _error_response(
                    422,
                    code="invalid_request",
                    stage="input",
                    message="Solve request body is invalid",
                    details={"validation_errors": jsonable_encoder(exc.errors())},
                    client_request_id=_recover_client_request_id(exc.body),
                )

        return solve_contract_handler


def create_jobs_router(
    runtime: JobRuntime, *, extra_ws_origins: Collection[str] = ()
) -> APIRouter:
    """Build bound routes with v1 error mappings and response shapes.

    Route coverage follows v1 ``server/api/routes_simulation.py:69-297``.
    """

    router = APIRouter(route_class=_JobsContractRoute)
    router.add_event_handler("startup", runtime.start)
    router.add_event_handler("shutdown", runtime.shutdown)

    @router.post(
        "/api/solve/plan",
        response_model=SolvePlanResponse,
        responses={
            422: {"model": ErrorEnvelope, "description": "Solve request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
        },
    )
    async def plan_solve(body: SolveRequest) -> SolvePlanResponse | JSONResponse:
        """Resolve the submitted design without allocating or persisting a job."""

        try:
            resolved = await resolve_submission(body, runtime.engine_registry)
        except UnknownEngineError as exc:
            return _error_response(
                422,
                code="unknown_engine",
                stage="planning",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except ImportedSolveRefusal as exc:
            return _error_response(
                422,
                code=exc.reason_code,
                stage="planning",
                message=str(exc),
                details=exc.details,
                client_request_id=body.client_request_id,
            )
        except (SymmetryValidationError, ValueError) as exc:
            return _error_response(
                422,
                code="invalid_solve_plan",
                stage="planning",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except EngineUnavailableError as exc:
            return _error_response(
                503,
                code="engine_unavailable",
                stage="planning",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        plan = resolved.symmetry_metadata["solver_plan"]
        return SolvePlanResponse(
            engine=resolved.engine_name,
            formulation=plan["formulation"],
            reason=plan["reason"],
            eligibility_reasons=[str(reason) for reason in plan["eligibility_reasons"]],
        )

    @router.post(
        "/api/solve",
        response_model=SolveAccepted,
        response_model_exclude_none=True,
        responses={
            422: {"model": ErrorEnvelope, "description": "Solve request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
        },
    )
    async def submit_solve(body: SolveRequest) -> SolveAccepted | JSONResponse:
        try:
            job_id = await runtime.submit(body)
        except UnknownEngineError as exc:
            return _error_response(
                422,
                code="unknown_engine",
                stage="submission",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except SymmetryValidationError as exc:
            return _error_response(
                422,
                code="invalid_symmetry",
                stage="submission",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except ImportedSolveRefusal as exc:
            return _error_response(
                422,
                code=exc.reason_code,
                stage="submission",
                message=str(exc),
                details=exc.details,
                client_request_id=body.client_request_id,
            )
        except EngineUnavailableError as exc:
            return _error_response(
                503,
                code="engine_unavailable",
                stage="submission",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        return SolveAccepted(
            job_id=job_id,
            client_request_id=body.client_request_id,
        )

    @router.post("/api/stop/{job_id}", response_model=StopResponse)
    async def stop_job(job_id: str) -> StopResponse:
        try:
            return StopResponse.model_validate(await runtime.stop(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/jobs/{job_id}/retry", response_model=SolveAccepted)
    async def retry_job(job_id: str) -> SolveAccepted:
        try:
            return SolveAccepted(job_id=await runtime.retry(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except (
            UnknownEngineError,
            SymmetryValidationError,
            ImportedSolveRefusal,
            ValueError,
        ) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EngineUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/api/status/{job_id}", response_model=JobStatusResponse)
    async def job_status(job_id: str) -> JobStatusResponse:
        try:
            return JobStatusResponse.model_validate(await runtime.get_job(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @router.get(
        "/api/results/{job_id}",
        response_model=ResultEnvelope,
        responses={
            200: {
                "description": "Versioned solver result",
                "headers": {
                    "ETag": {
                        "description": "SHA-256 identity of the exact stored bytes",
                        "schema": {"type": "string"},
                    },
                    "X-WG-Results-SHA256": {
                        "description": "Hex SHA-256 of the exact stored bytes",
                        "schema": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                },
            }
        },
    )
    async def job_results(job_id: str) -> Response:
        # Returned as a pre-encoded body on purpose. A dict return value makes
        # FastAPI validate the response against the annotation and then
        # re-serialise it, which for a multi-megabyte sweep is two extra full
        # walks of the data on the event loop before json.dumps does a third.
        # The database already holds exactly the bytes the client wants.
        try:
            content, digest = await runtime.get_results_payload(job_id)
            return Response(
                content=content,
                media_type="application/json",
                headers={
                    "ETag": f'"sha256:{digest}"',
                    "X-WG-Results-SHA256": digest,
                },
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/jobs/{job_id}/archive-snapshot", response_model=None)
    async def job_archive_snapshot(job_id: str) -> Response:
        """Return one retention-consistent set of permanent-archive inputs."""

        try:
            snapshot = await runtime.get_archive_snapshot(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=json.dumps(snapshot, allow_nan=False),
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-WG-Archive-Snapshot-Version": "1",
            },
        )

    @router.post("/api/results/{job_id}/combine", response_model=None)
    async def recombine_job_results(job_id: str, body: ChannelCombineSpec) -> Response:
        """Recompute the combined channel from stored bases without re-solving."""

        try:
            updated = await runtime.recombine_results(job_id, body)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RecombineError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=json.dumps(updated, allow_nan=False),
            media_type="application/json",
        )

    @router.post(
        "/api/results/{job_id}/field-plane",
        response_model=None,
        responses={
            422: {
                "model": FieldPlane422Response,
                "description": (
                    "Invalid field-plane selection, unsupported solve with an "
                    "actionable remedy, or invalid request body"
                ),
            }
        },
    )
    async def field_plane(job_id: str, body: FieldPlaneRequest) -> Response:
        """Evaluate one exterior complex-pressure grid from retained traces."""

        try:
            evaluated = await runtime.evaluate_field_plane(job_id, body)
        except FieldPlaneJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except FieldPlaneJobIncomplete as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ArtifactMissing as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except ArtifactCorrupt as exc:
            raise HTTPException(
                status_code=410,
                detail=f"Field-trace artifact is corrupt: {exc}",
            ) from exc
        except FieldPlaneUnsupported as exc:
            if exc.code in {
                "unsupported_axisymmetric_formulation",
                "unsupported_coupled_infinite_baffle",
            } and exc.remedy is not None:
                return JSONResponse(
                    status_code=422,
                    content=FieldPlaneUnavailableResponse(
                        detail=str(exc),
                        code=exc.code,
                        message=str(exc),
                        remedy=exc.remedy,
                    ).model_dump(mode="json"),
                )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FieldPlaneInvalidSelection as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FieldEvaluationSuperseded as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "superseded",
                    "message": str(exc),
                    "replacement_request_id": exc.replacement_request_id,
                },
            ) from exc
        except FieldEvaluationBusy as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "solve_running_or_queued",
                    "message": str(exc),
                },
            ) from exc
        except FieldPlaneTimedOut as exc:
            raise HTTPException(
                status_code=504,
                detail={"code": "evaluation_timeout", "message": str(exc)},
            ) from exc
        return Response(
            content=encode_field_plane_response(job_id, body, evaluated),
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/partial-results/{job_id}", response_model=None)
    async def partial_job_results(job_id: str) -> Response:
        """Process-local correctness path for a dropped live-result delta."""

        try:
            partial = await runtime.get_partial_results(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=json.dumps(partial, allow_nan=False),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/mesh-artifact/{job_id}", response_class=PlainTextResponse)
    async def mesh_artifact(job_id: str) -> PlainTextResponse:
        try:
            artifact = await runtime.get_mesh_artifact_download(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobMeshDiscardedError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        headers: dict[str, str] = {}
        if artifact.regenerated:
            headers["X-WG2-Mesh-Regenerated"] = "1"
            if artifact.mesher_version is not None:
                headers["X-WG2-Mesher-Version"] = artifact.mesher_version
        return PlainTextResponse(
            content=artifact.content,
            media_type="text/plain",
            headers=headers,
        )

    @router.get("/api/radiation-impedance/{job_id}", response_model=None)
    async def radiation_impedance_artifact(job_id: str) -> Response:
        try:
            content = await runtime.get_radiation_impedance(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    'attachment; filename="port_exit_radiation_impedance_matrix.npz"'
                )
            },
        )

    @router.get("/api/pressure-basis/{job_id}", response_model=None)
    async def pressure_basis_artifact(
        job_id: str, channel_id: str | None = Query(default=None)
    ) -> Response:
        try:
            artifact = await runtime.get_pressure_basis(job_id, channel_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact.channel_id).strip("-.")
        stem = stem or "channel"
        return Response(
            content=artifact.content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{stem}_pressure_basis.npz"'
                )
            },
        )

    @router.get(
        "/api/radiation-impedance/{job_id}/presentation",
        response_model=RadiationImpedancePresentation,
    )
    async def radiation_impedance_presentation(
        job_id: str,
    ) -> RadiationImpedancePresentation:
        try:
            presentation = await runtime.get_radiation_impedance_presentation(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except JobResourceUnavailableError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RadiationImpedancePresentation.model_validate(presentation)

    @router.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
    async def job_log(job_id: str) -> PlainTextResponse:
        try:
            content = await runtime.get_log(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return PlainTextResponse(content=content, media_type="text/plain")

    @router.get("/api/jobs", response_model=JobListResponse)
    async def list_jobs(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> JobListResponse:
        try:
            items, total = await runtime.list_jobs(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JobListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    @router.patch("/api/jobs/{job_id}/metadata", response_model=MetadataResponse)
    async def patch_job_metadata(
        job_id: str, body: JobMetadataPatch
    ) -> MetadataResponse:
        try:
            fields = {
                name: getattr(body, name) for name in body.model_fields_set
            }
            await runtime.patch_metadata(job_id, fields)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return MetadataResponse(status="ok")

    @router.delete("/api/jobs/clear-failed", response_model=ClearFailedResponse)
    async def clear_failed() -> ClearFailedResponse:
        deleted_ids = await runtime.clear_failed()
        return ClearFailedResponse(
            deleted=bool(deleted_ids),
            deleted_count=len(deleted_ids),
            deleted_ids=deleted_ids,
        )

    @router.delete("/api/jobs/{job_id}", response_model=DeleteResponse)
    async def delete_job(job_id: str) -> DeleteResponse:
        try:
            await runtime.delete(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return DeleteResponse(deleted=True, job_id=job_id)

    @router.websocket("/ws/jobs")
    async def jobs_websocket(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        server = websocket.scope.get("server")
        bound_port = server[1] if isinstance(server, (list, tuple)) and len(server) > 1 else None
        if not websocket_request_allowed(
            origin=origin,
            host=websocket.headers.get("host"),
            scheme=websocket.scope.get("scheme", "ws"),
            bound_port=bound_port,
            extra_origins=extra_ws_origins,
        ):
            await websocket.close(code=CLOSE_ORIGIN_REJECTED)
            return
        await websocket.accept()
        protocol = JobsProtocol(runtime)
        try:
            await protocol.run(_FastAPIJobsTransport(websocket))
        except asyncio.CancelledError:
            await protocol.close_restarting()
            raise
        except WebSocketDisconnect:
            return

    return router


def mount_jobs(
    application: FastAPI,
    engine_registry: EngineRegistry | None = None,
    *,
    extra_ws_origins: Collection[str] = (),
) -> JobRuntime:
    """Attach one data-dir-bound runtime before the frontend catch-all mount."""

    data_dir = Path(application.state.data_dir)
    runtime = JobRuntime(
        JobStore.for_data_dir(data_dir),
        engine_registry=engine_registry,
        cadlink_store=application.state.cadlink_store,
    )
    application.state.jobs_runtime = runtime
    application.include_router(
        create_jobs_router(runtime, extra_ws_origins=extra_ws_origins)
    )
    return runtime


__all__ = ["create_jobs_router", "mount_jobs"]

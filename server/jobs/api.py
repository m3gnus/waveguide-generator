"""REST correctness path and FastAPI adapter for solve jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
import json
from pathlib import Path
import re
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from starlette.websockets import WebSocketDisconnect, WebSocketState

from server.jobs.fem_field import FemFieldCorrupt, FemFieldInvalidSelection
from server.platform.feature_flags import fem_regions_enabled
from server.jobs.events import CLOSE_ORIGIN_REJECTED, JobsProtocol
from server.integration.contracts import ErrorEnvelope, error_envelope
from server.jobs.models import (
    CadApprovalsRequest,
    CadSolveAgainRequest,
    ChannelCombineSpec,
    ClearFailedResponse,
    DeleteResponse,
    FieldPlaneRequest,
    FieldPlaneSpec,
    ImportedGeometrySource,
    JobListResponse,
    PolarConfig,
    JobMetadataPatch,
    JobStatusResponse,
    MetadataResponse,
    SolveAccepted,
    SolveRequest,
    StopResponse,
)
from server.jobs.runtime.engine_policy import SolveBlocker
from server.jobs.plans import SolvePlanResponse, solve_plan_response
from server.jobs.result_contracts import ResultEnvelope
from server.jobs.retention import settings_auto_cleanup_policy
from server.settings.store import SettingsStore
from server.solver.errors import RecombineError
from server.solver.field_plane import (
    FieldPlaneEvaluationFailed,
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
    JobProtectionError,
    JobResourceUnavailableError,
    JobRuntime,
    RemovedSolverError,
    SymmetryValidationError,
    UnknownEngineError,
)
from server.jobs.store import JobStore, SubmissionConflictError
from server.cadlink.operations import TERMINAL_STATES
from server.mesh.prepared_gc import PreparedMeshCollector
from server.engines.registry import EngineRegistry
from server.platform.origin import websocket_request_allowed
from server.updates.restart import UPDATE_RESTART_PENDING, RestartApproval


def _json_tokens(value: Any) -> Iterator[str]:
    """Yield valid JSON without encoding one unbounded string or array at once."""

    if isinstance(value, str):
        yield '"'
        for offset in range(0, len(value), 64 * 1024):
            encoded = json.dumps(value[offset : offset + 64 * 1024], allow_nan=False)
            yield encoded[1:-1]
        yield '"'
    elif isinstance(value, Mapping):
        yield "{"
        for index, (key, member) in enumerate(value.items()):
            if index:
                yield ","
            yield json.dumps(str(key), allow_nan=False)
            yield ":"
            yield from _json_tokens(member)
        yield "}"
    elif isinstance(value, (list, tuple)):
        yield "["
        for index, member in enumerate(value):
            if index:
                yield ","
            yield from _json_tokens(member)
        yield "]"
    else:
        yield json.dumps(value, allow_nan=False, separators=(",", ":"))


def _json_chunks(value: Any) -> Iterator[bytes]:
    """Coalesce tokens into bounded chunks for StreamingResponse's threadpool."""

    buffered: list[str] = []
    buffered_characters = 0
    for token in _json_tokens(value):
        buffered.append(token)
        buffered_characters += len(token)
        if buffered_characters >= 256 * 1024:
            yield "".join(buffered).encode("utf-8")
            buffered.clear()
            buffered_characters = 0
    if buffered:
        yield "".join(buffered).encode("utf-8")


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


class FemFieldSurface(BaseModel):
    name: str
    triangles: str


class FemFieldPresentation(BaseModel):
    schema_: Literal["wg-fem-field-v1"] = Field(alias="schema")
    region_id: str
    frequency_hz: float
    points_mm: str
    tetrahedra: str
    surfaces: list[FemFieldSurface]
    pressure_real: str
    pressure_imag: str
    frequencies_hz: list[float]
    channel_ids: list[str]
    channel_id: str
    phasor_convention: Literal["exp(-i omega t)"]
    drive_normalization: Literal["unit_acceleration_m_per_s2", "unit_acceleration", "voltage_rms"]
    transducer_reference_voltage_v: float | None = None
    density_kg_per_m3: float | None = None
    plane_unavailable_reason: str | None = None
    plane_scopes: list[Literal["interior", "exterior", "combined"]] = Field(default_factory=lambda: ["interior"])


class FemPlaneRequest(BaseModel):
    plane: FieldPlaneSpec
    frequency: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    channel: str | None = Field(default=None, min_length=1)
    scope: Literal["interior", "exterior", "combined"] = "combined"
    quantity: Literal["pressure", "velocity_normal"] = "pressure"


class FemPlanePresentation(BaseModel):
    frequency_hz: float
    channel_id: str
    scope: Literal["interior", "exterior", "combined"]
    quantity: Literal["pressure", "velocity_normal"]
    unit: Literal["Pa", "m/s"]
    phasor_convention: Literal["exp(-i omega t)"]
    nx: int
    ny: int
    points_mm: str
    pressure_real: str
    pressure_imag: str
    valid_mask: str
    region_class: str
    crosses_body: str
    mask_reason: str = Field(description="Base64 uint8 per point: 0 valid, 1 solid or outside selected domain, 2 too close to a surface for a reliable velocity")


class JobRequestsResponse(BaseModel):
    """The same request stages used by the CLI's artifact bundle."""

    effective_request: SolveRequest
    execution_request: SolveRequest


class ImportedEngineVerdictResponse(BaseModel):
    """Whether one engine can solve one ingested CAD return here, and why not."""

    name: str
    label: str
    solves: bool
    stage: str | None = None
    code: str | None = None
    reason: str | None = None


class ImportedSolvePlanResponse(BaseModel):
    """Every engine's verdict on one imported request, and where it resolves.

    ``engine`` is what submitting this request would run on; it is null, with
    ``code`` and ``reason``, when the request would be refused or no capable
    engine is available.
    """

    readiness: SolveBlocker | None = None
    ingest_id: str
    effective_polar_config: PolarConfig
    requested: str
    engine: str | None
    code: str | None = None
    reason: str
    domain: str | None = None
    #: The record's sealed domain decision, summarised as the job records it
    #: (``server/cadlink/interpretation/domain_decision.py``); null for an earlier build's record.
    domain_decision: dict[str, Any] | None = None
    engines: list[ImportedEngineVerdictResponse]


class FieldPlaneUnavailableResponse(BaseModel):
    """Backward-compatible, versioned remedy for an unsupported field plane."""

    detail: str
    error_contract_version: Literal[1] = 1
    code: Literal[
        "unsupported_axisymmetric_formulation",
        "unsupported_coupled_infinite_baffle",
        "unsupported_per_band_mesh_ladder",
        "unsupported_ground_plane",
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
        if self.path not in {"/api/solve", "/api/solve/plan", "/api/solve/imported-plan"}:
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
    runtime: JobRuntime,
    *,
    extra_ws_origins: Collection[str] = (),
    restart_approval: RestartApproval | None = None,
    after_delete: Callable[[], None] | None = None,
) -> APIRouter:
    """Build bound routes with v1 error mappings and response shapes.

    ``after_delete`` is told when jobs were deleted (it schedules prepared-mesh
    collection; it never waits for it).

    Route coverage follows v1 ``server/api/routes_simulation.py:69-297``.

    ``restart_approval`` is the server's restart-approved latch
    (``docs/reference/UPDATE-TRANSACTION-CONTRACT.md`` §4.2). While it is set,
    routes that start work (Solve, Retry and CAD Solve again), and both
    solve plans, refuse with HTTP 409. An exact CAD intake replay can still
    recover its original job.
    Read routes and approvals on an existing preparation stay open.
    """

    router = APIRouter(route_class=_JobsContractRoute)
    retirement_hold = getattr(restart_approval, "retirement_hold", None)

    def restart_refusal(client_request_id: str | None = None) -> JSONResponse | None:
        message = restart_approval.refusal() if restart_approval is not None else None
        if message is None and retirement_hold is not None:
            message = retirement_hold.refusal()
        if message is None:
            return None
        return _error_response(
            409,
            code=UPDATE_RESTART_PENDING,
            stage="submission",
            message=message,
            retryable=True,
            client_request_id=client_request_id,
        )
    router.add_event_handler("startup", runtime.start)
    router.add_event_handler("shutdown", runtime.shutdown)

    @router.post(
        "/api/solve/imported-plan",
        response_model=ImportedSolvePlanResponse,
        responses={
            409: {"model": ErrorEnvelope, "description": "A restart or retirement hold is pending"},
            422: {"model": ErrorEnvelope, "description": "Imported request refused"},
        },
    )
    async def plan_imported_solve(
        body: SolveRequest,
    ) -> ImportedSolvePlanResponse | JSONResponse:
        """Every engine's verdict on one ingested CAD return, without a job.

        The solver selector reads this for imported geometry: the same
        per-engine capability ``POST /api/solve`` resolves with, keyed by the
        request's ``ingest_id``. A request the ingestion record itself refuses
        (unknown ingest, mismatched hashes, an unsupported cut set) answers 422.
        """

        refused = restart_refusal(body.client_request_id)
        if refused is not None:
            return refused
        try:
            plan = await runtime.plan_imported(body)
        except ImportedSolveRefusal as exc:
            return _error_response(
                422,
                code=exc.reason_code,
                stage="planning",
                message=str(exc),
                details=exc.details,
                client_request_id=body.client_request_id,
            )
        except ValueError as exc:
            return _error_response(
                422,
                code="invalid_solve_plan",
                stage="planning",
                message=str(exc),
                details=getattr(exc, "details", None),
                client_request_id=body.client_request_id,
            )
        return ImportedSolvePlanResponse.model_validate(plan)

    @router.post(
        "/api/solve/plan",
        response_model=SolvePlanResponse,
        responses={
            409: {"model": ErrorEnvelope, "description": "A restart or retirement hold is pending"},
            422: {"model": ErrorEnvelope, "description": "Solve request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
        },
    )
    async def plan_solve(body: SolveRequest) -> SolvePlanResponse | JSONResponse:
        """Resolve the submitted design without allocating or persisting a job."""

        refused = restart_refusal(body.client_request_id)
        if refused is not None:
            return refused
        try:
            validation = await runtime.validate_submission(body, preview=True)
            resolved = validation.resolved
            if resolved is None:
                raise ValueError("Use the imported plan endpoint for imported geometry")
            plan = resolved.symmetry_metadata["solver_plan"]
            if plan.get("refusal") and not plan.get("readiness"):
                raise SymmetryValidationError(plan["refusal"])
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
        except RemovedSolverError as exc:
            return _error_response(
                422,
                code=exc.code,
                stage="planning",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except (SymmetryValidationError, ValueError) as exc:
            return _error_response(
                422,
                code="invalid_solve_plan",
                stage="planning",
                message=str(exc),
                details=getattr(exc, "details", None),
                client_request_id=body.client_request_id,
            )
        except EngineUnavailableError as exc:
            return _error_response(
                503,
                code="engine_unavailable",
                stage="planning",
                message=str(exc),
                details=exc.details,
                client_request_id=body.client_request_id,
            )
        return solve_plan_response(resolved)

    @router.post(
        "/api/solve",
        response_model=SolveAccepted,
        response_model_exclude_none=True,
        responses={
            409: {
                "model": ErrorEnvelope,
                "description": "Submission key conflict, or an update restart is pending",
            },
            422: {"model": ErrorEnvelope, "description": "Solve request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
        },
    )
    async def submit_solve(body: SolveRequest) -> SolveAccepted | JSONResponse:
        refused = restart_refusal(body.client_request_id)
        if refused is not None:
            return refused
        try:
            if isinstance(body.geometry, ImportedGeometrySource):
                # Imported geometry is its model's solve: the same setup record
                # and in-flight rule as a Solve of the model. Imported here:
                # it reaches the CAD lanes, which import this package.
                from server.jobs.model_solve import submit_imported_request

                job_id = await submit_imported_request(runtime, body)
            else:
                job_id = await runtime.submit(body)
        except UnknownEngineError as exc:
            return _error_response(
                422,
                code="unknown_engine",
                stage="submission",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        except RemovedSolverError as exc:
            return _error_response(
                422,
                code=exc.code,
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
                details=getattr(exc, "details", None),
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
                details=exc.details,
                client_request_id=body.client_request_id,
            )
        except SubmissionConflictError as exc:
            return _error_response(
                409,
                code="submission_key_conflict",
                stage="submission",
                message=str(exc),
                client_request_id=body.client_request_id,
            )
        return SolveAccepted(
            job_id=job_id,
            client_request_id=body.client_request_id,
        )

    async def check_cad_setup(revision_id: str | None) -> None:
        if revision_id and await asyncio.to_thread(runtime.cadlink_store.get_setup_revision, revision_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown setup revision {revision_id}")

    @router.post(
        "/api/jobs/{job_id}/solve-again", response_model=SolveAccepted,
        responses={409: {"model": ErrorEnvelope, "description": "Restart pending or job cannot be continued"}},
    )
    async def solve_again(job_id: str, body: CadSolveAgainRequest) -> SolveAccepted | JSONResponse:
        """Capture a waiting first press, or continue a refused CAD preparation."""

        try:
            replay = await asyncio.to_thread(runtime.recover_cad_solve_press, job_id, **body.press())
            if replay is not None:
                return SolveAccepted(job_id=replay)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        refused = restart_refusal()
        if refused is not None:
            return refused
        await check_cad_setup(body.setup_revision_id)
        try:
            job = await runtime.get_job(job_id)
            if job.get("cad_intent") is None:
                raise JobConflictError("This solve already has a solve request; retry it instead")
            return SolveAccepted(job_id=await runtime.prepare_cad_solve(job_id, **body.press()))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/jobs/{job_id}/approvals", response_model=JobStatusResponse)
    async def cad_approvals(job_id: str, body: CadApprovalsRequest) -> JobStatusResponse:
        """Record blocking findings on this refused job's exact preparation."""

        try:
            await runtime.get_job(job_id)
            await asyncio.to_thread(runtime.store.approve_cad_preparation, job_id, body.preparation_id, body.finding_ids)
            return JobStatusResponse.model_validate(await runtime.get_job(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/api/jobs/{job_id}/dismiss", response_model=DeleteResponse,
        responses={409: {"description": "Not a refused CAD solve, or still being prepared"}},
    )
    async def dismiss_cad_job(job_id: str) -> DeleteResponse:
        """Dismiss a refused CAD solve with its refused ancestors, as one change.

        Deleting the jobs one by one could leave a parent a reconnect revives.
        """

        try:
            job = await runtime.get_job(job_id)
            intent = job.get("cad_intent")
            if intent is None or not intent.get("operation_id"):
                raise JobConflictError("Only a CAD solve that was never bound can be dismissed")
            await runtime.dismiss_cad_solve(str(intent["operation_id"]))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if after_delete is not None:
            after_delete()
        return DeleteResponse(deleted=True, job_id=job_id)

    @router.post("/api/stop/{job_id}", response_model=StopResponse)
    async def stop_job(job_id: str) -> StopResponse:
        try:
            return StopResponse.model_validate(await runtime.stop(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/jobs/{job_id}/retry",
        response_model=SolveAccepted,
        responses={
            422: {"model": ErrorEnvelope, "description": "Solve request refused"},
            503: {"model": ErrorEnvelope, "description": "Engine unavailable"},
            409: {
                "model": ErrorEnvelope,
                "description": (
                    "An update restart is pending (error envelope), or the job "
                    "has no solve request to replay (`detail`), for example one "
                    "still being prepared"
                ),
            },
        },
    )
    async def retry_job(job_id: str) -> SolveAccepted | JSONResponse:
        refused = restart_refusal()
        if refused is not None:
            return refused
        try:
            return SolveAccepted(job_id=await runtime.retry(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            # A job with no solve request to replay (one still being prepared, or a
            # run with no stored design): the message says which.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (
            RemovedSolverError,
            UnknownEngineError,
            SymmetryValidationError,
            ImportedSolveRefusal,
            ValueError,
            EngineUnavailableError,
        ) as exc:
            from server.jobs.model_solve import refusal_from

            refusal = refusal_from(exc)
            return _error_response(
                refusal.status, code=refusal.code, stage="retry",
                message=str(refusal), details=refusal.details,
            )

    @router.get("/api/status/{job_id}", response_model=JobStatusResponse)
    async def job_status(job_id: str) -> JobStatusResponse:
        try:
            return JobStatusResponse.model_validate(await runtime.get_job(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @router.get("/api/jobs/{job_id}/requests", response_model=JobRequestsResponse)
    async def job_requests(job_id: str) -> Response:
        try:
            effective = await runtime.get_effective_request(job_id)
            execution = await runtime.get_execution_request(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            content={
                "effective_request": effective.model_dump(mode="json"),
                "execution_request": execution.model_dump(mode="json"),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get(
        "/api/results/{job_id}",
        response_model=ResultEnvelope,
        responses={
            200: {
                "description": "Versioned solver result",
                "headers": {
                    "ETag": {
                        "description": (
                            "SHA-256 identity of the served bytes: the stored "
                            "bytes, or an archived record with read-time "
                            "power-qualification flags added"
                        ),
                        "schema": {"type": "string"},
                    },
                    "X-WG-Results-SHA256": {
                        "description": "Hex SHA-256 of the served bytes",
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
        return StreamingResponse(
            _json_chunks(snapshot),
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
            500: {
                "model": FieldPlaneStringErrorResponse,
                "description": "Field evaluation failed to produce a valid pressure result",
            },
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
            # Any refusal that names both a code and a remedy gets the full
            # envelope. This used to be a list of the two codes that did, which
            # meant a third one silently degraded to a bare detail string --
            # caught by adding the mesh-ladder refusal, whose remedy reached
            # nobody until the list grew.
            if exc.code is not None and exc.remedy is not None:
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
        except FieldPlaneEvaluationFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
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

    @router.get("/api/results/{job_id}/fem-field", response_model=FemFieldPresentation)
    async def fem_field(
        job_id: str,
        frequency: float | None = Query(default=None, gt=0, allow_inf_nan=False),
        channel: str | None = Query(default=None, min_length=1),
    ) -> Response:
        """Selected FEM basis pressure, little-endian float32/uint32 base64.

        Frequency is in Hz; omitted selectors use the first retained entry.
        Channels are raw Pa per unit acceleration, without system synthesis.
        The view contains every FEM region, with rebased topology indices.
        """
        if not fem_regions_enabled():
            raise HTTPException(status_code=404, detail="FEM fields are turned off")
        row = await asyncio.to_thread(runtime.store.get_job_row, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if row["status"] != "complete":
            raise HTTPException(status_code=404, detail="No completed FEM field for this job")
        try:
            payload = await asyncio.to_thread(runtime.store.load_fem_field, job_id, frequency, channel)
        except FemFieldCorrupt as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except FemFieldInvalidSelection as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload is None:
            raise HTTPException(status_code=404, detail="No retained FEM field for this job")
        return StreamingResponse(_json_chunks(payload), media_type="application/json",
                                 headers={"Cache-Control": "no-store"})

    @router.post("/api/results/{job_id}/fem-field/plane", response_model=FemPlanePresentation)
    async def fem_field_plane(job_id: str, selection: FemPlaneRequest) -> Response:
        """Raw channel plane; masked samples are zero with valid_mask=0.

        P1 interior normal velocity is exact per tetrahedron. Exterior normal
        velocity uses centred differences of the official BEAT potential.
        """
        from server.jobs.coupled_field import evaluate_coupled_plane_async

        if not fem_regions_enabled():
            raise HTTPException(status_code=404, detail="FEM fields are turned off")
        row = await asyncio.to_thread(runtime.store.get_job_row, job_id)
        if row is None or row["status"] != "complete":
            raise HTTPException(status_code=404, detail="No completed FEM field for this job")
        try:
            payload = await evaluate_coupled_plane_async(runtime.store, job_id, selection, runtime.field_plane_service)
        except (FieldEvaluationBusy, FieldEvaluationSuperseded) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FemFieldInvalidSelection as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FieldPlaneUnsupported as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FieldPlaneTimedOut as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except (ArtifactCorrupt, FemFieldCorrupt) as exc:
            raise HTTPException(status_code=410, detail=f"Field-trace artifact is corrupt: {exc}") from exc
        except FieldPlaneEvaluationFailed as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if payload is None:
            raise HTTPException(status_code=404, detail="No retained FEM field for this job")
        return StreamingResponse(_json_chunks(payload), media_type="application/json",
                                 headers={"Cache-Control": "no-store"})

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
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except OSError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return MetadataResponse(status="ok")

    @router.delete("/api/jobs/clear-failed", response_model=ClearFailedResponse)
    async def clear_failed() -> ClearFailedResponse:
        deleted_ids = await runtime.clear_failed()
        if deleted_ids and after_delete is not None:
            after_delete()
        return ClearFailedResponse(
            deleted=bool(deleted_ids),
            deleted_count=len(deleted_ids),
            deleted_ids=deleted_ids,
        )

    @router.delete("/api/jobs/{job_id}", response_model=DeleteResponse)
    async def delete_job(
        job_id: str,
        skip_model_protection: bool = Query(
            False,
            description="Delete without protecting the Fusion model, only after explicit user confirmation. Leaves all files on disk untouched.",
        ),
    ) -> DeleteResponse:
        try:
            await runtime.delete(job_id, skip_model_protection=skip_model_protection)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobProtectionError as exc:
            raise HTTPException(status_code=409, detail={
                "code": "model_protection_failed", "message": str(exc),
            }) from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not skip_model_protection and after_delete is not None:
            after_delete()
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
    restart_approval: RestartApproval | None = None,
    settings: SettingsStore | None = None,
) -> JobRuntime:
    """Attach one data-dir-bound runtime before the frontend catch-all mount.

    ``settings`` is the store the settings routes write. The runtime reads the
    automatic result cleanup preference from it; without one, results are kept.
    """

    data_dir = Path(application.state.data_dir)
    runtime = JobRuntime(
        JobStore.for_data_dir(data_dir),
        engine_registry=engine_registry,
        cadlink_store=application.state.cadlink_store,
        # The same latch the refusing routes read: while it is set no queued
        # job starts either, and a shutdown ends running jobs as ended by the
        # update restart (contract §4.3).
        restart_approval=restart_approval,
        results_auto_cleanup=(
            settings_auto_cleanup_policy(settings) if settings is not None else None
        ),
    )
    from server.jobs.auto_export import AutoExportMiddleware, AutoExportOwner, router_for

    export_owner = AutoExportOwner(runtime)
    application.add_middleware(AutoExportMiddleware, owner=export_owner)
    application.include_router(router_for(export_owner))
    application.state.jobs_runtime = runtime
    # Prepared meshes nothing uses are collected off the request path: once
    # after startup and after jobs are deleted (CAD-OPERATIONS.md, "Retention").
    collector = PreparedMeshCollector(
        application.state.cadlink_store,
        runtime.store,
        data_dir,
        terminal_states=TERMINAL_STATES,
        restart_pending=runtime._restart_pending,
    )
    application.state.prepared_mesh_collector = collector
    application.router.add_event_handler("startup", collector.startup)
    application.router.add_event_handler("shutdown", collector.shutdown)
    application.include_router(
        create_jobs_router(
            runtime,
            extra_ws_origins=extra_ws_origins,
            restart_approval=restart_approval,
            after_delete=collector.request,
        )
    )
    return runtime


__all__ = ["create_jobs_router", "mount_jobs"]
